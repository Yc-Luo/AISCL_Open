"""Authorize course design models against the current course owner's catalog."""

from beanie import PydanticObjectId
from bson.errors import InvalidId
from pydantic import ValidationError

from app.core.schemas.learning_design import LearningDesignModel, ModelAssignments
from app.repositories.course import Course
from app.repositories.project import Project
from app.repositories.user import User


class CourseModelUnavailable(ValueError):
    def __init__(self, message: str = "A model assigned in the learning design is unavailable to this course") -> None:
        super().__init__(message)


def _document_id(value: str | None) -> PydanticObjectId | None:
    if not value:
        return None
    try:
        return PydanticObjectId(value)
    except (InvalidId, TypeError):
        return None


async def _course_model_choices(course: Course) -> dict[str, str]:
    owner_id = _document_id(course.teacher_id)
    owner = await User.get(owner_id) if owner_id is not None else None
    if not owner or not owner.is_active or owner.is_banned or owner.role not in {"teacher", "admin"}:
        return {}

    from app.core.llm_config import list_dedicated_chat_models
    from app.services.teacher_model_service import list_teacher_model_choices

    dedicated = await list_dedicated_chat_models()
    permissions = owner.config_permissions
    grants = permissions.get("allowed_model_ids") if isinstance(permissions, dict) else None
    allowed = {model_id for model_id in grants if isinstance(model_id, str)} if isinstance(grants, list) else set()
    choices = {
        model_id: model["name"] for model_id, model in dedicated.items()
        if owner.role == "admin" or model_id in allowed
    }
    for model in await list_teacher_model_choices(str(owner_id)):
        model_id = model["id"]
        if model_id in dedicated:
            # Never bind an ID that could point to two different connections.
            choices.pop(model_id, None)
        else:
            choices[model_id] = model["name"]
    return choices


async def list_course_model_choices(course: Course) -> list[LearningDesignModel]:
    choices = await _course_model_choices(course)
    return [LearningDesignModel(id=model_id, name=name) for model_id, name in choices.items()]


async def validate_course_model_assignments(course: Course, assignments: ModelAssignments | None) -> None:
    if assignments is None:
        return
    requested = set(assignments.role_model_ids.values())
    if assignments.default_model_id is not None:
        requested.add(assignments.default_model_id)
    if requested and requested - (await _course_model_choices(course)).keys():
        raise CourseModelUnavailable()


async def resolve_project_model_assignments(project: Project, *, include_roles: bool = True) -> dict:
    """Return snapshot bindings only after checking live course ownership and permissions."""
    snapshot = (project.experiment_version or {}).get("learning_design")
    raw = snapshot.get("model_assignments") if isinstance(snapshot, dict) else None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CourseModelUnavailable()
    if not include_roles:
        raw = {"default_model_id": raw.get("default_model_id")}
    try:
        assignments = ModelAssignments.model_validate(raw)
    except ValidationError:
        raise CourseModelUnavailable() from None
    if assignments.default_model_id is None and not assignments.role_model_ids:
        return {}

    course_id = _document_id(project.course_id)
    course = await Course.get(course_id) if course_id is not None else None
    if course is None:
        raise CourseModelUnavailable()
    await validate_course_model_assignments(course, assignments)
    return {**assignments.model_dump(), "course_owner_id": course.teacher_id}
