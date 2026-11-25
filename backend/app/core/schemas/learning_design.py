"""Validated course learning design and its server-owned revision."""

from typing import Annotated, Literal, Self, get_args
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.services.agents.multi_agent_roles import SUBAGENT_LABELS

BuiltinRoleId = Literal[
    "evidence_researcher", "viewpoint_challenger", "feedback_prompter", "problem_progressor"
]
StageId = Literal[
    "problem_construction", "meaning_exploration", "explanation_integration", "application_solution"
]
DEFAULT_STAGE_NAMES: dict[StageId, str] = {
    "problem_construction": "问题构建",
    "meaning_exploration": "意义探索",
    "explanation_integration": "解释整合",
    "application_solution": "应用解决",
}
BUILTIN_ROLE_NAMES = frozenset(label.casefold() for label in SUBAGENT_LABELS.values())
MAX_CUSTOM_ROLES = 6


class RoleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: BuiltinRoleId
    enabled: StrictBool
    prompt: str = Field(max_length=2500)


class CustomRoleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^custom_[0-9a-f]{16}$")
    enabled: StrictBool
    prompt: str = Field(max_length=2500)
    name: str = Field(min_length=1, max_length=40)
    routing_anchor: BuiltinRoleId

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value):
        if isinstance(value, str):
            if value and value.splitlines() != [value]:
                raise ValueError("Custom role name must be a single line")
            return value.strip()
        return value

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Custom role prompt must not be blank")
        return value


class StageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StageId
    name: str = Field(max_length=48)
    activity_guidance: str = Field(max_length=1600)


ModelId = Annotated[str, StringConstraints(strict=True, pattern=r"^\S(?:.*\S)?$")]


class ModelAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_model_id: ModelId | None = None
    role_model_ids: dict[str, ModelId] = Field(default_factory=dict)


class LearningDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[RoleItem | CustomRoleItem] = Field(max_length=len(get_args(BuiltinRoleId)) + MAX_CUSTOM_ROLES)
    orchestration_mode: Literal["adaptive", "single", "parallel", "debate", "pipeline"]
    stages: list[StageItem] = Field(max_length=4)
    model_assignments: ModelAssignments | None = None

    @model_validator(mode="after")
    def validate_items(self) -> Self:
        role_ids = {role.id for role in self.roles}
        if len(role_ids) != len(self.roles):
            raise ValueError("Role IDs must be unique")
        # Built-ins omitted from the request are materialized in the snapshot.
        if self.model_assignments:
            known_roles = role_ids.union(get_args(BuiltinRoleId))
            if self.model_assignments.role_model_ids.keys() - known_roles:
                raise ValueError("Model overrides must refer to built-in roles or custom roles in this design")
        if len({stage.id for stage in self.stages}) != len(self.stages):
            raise ValueError("Stage IDs must be unique")
        custom_names = [role.name.casefold() for role in self.roles if isinstance(role, CustomRoleItem)]
        if len(custom_names) > MAX_CUSTOM_ROLES:
            raise ValueError("At most six custom roles are allowed")
        unique_custom_names = set(custom_names)
        if len(unique_custom_names) != len(custom_names) or unique_custom_names & BUILTIN_ROLE_NAMES:
            raise ValueError("Custom role names must be unique and distinct from built-in role labels")
        builtin_count = len(self.roles) - len(custom_names)
        if builtin_count == len(get_args(BuiltinRoleId)) and not any(role.enabled for role in self.roles):
            raise ValueError("At least one role must be enabled")
        return self


class DesignSnapshot(LearningDesign):
    revision: str

    @classmethod
    def from_design(cls, design: LearningDesign) -> Self:
        roles = {role.id: role for role in design.roles if isinstance(role, RoleItem)}
        stages = {stage.id: stage for stage in design.stages}
        return cls(
            revision=uuid4().hex,
            orchestration_mode=design.orchestration_mode,
            model_assignments=design.model_assignments.model_copy(deep=True) if design.model_assignments else None,
            roles=[
                *(
                    roles[role_id] if role_id in roles else RoleItem(id=role_id, enabled=True, prompt="")
                    for role_id in get_args(BuiltinRoleId)
                ),
                *(role for role in design.roles if isinstance(role, CustomRoleItem)),
            ],
            stages=[
                StageItem(
                    id=stage_id,
                    name=(stages[stage_id].name.strip() if stage_id in stages else "") or default_name,
                    activity_guidance=stages[stage_id].activity_guidance if stage_id in stages else "",
                )
                for stage_id, default_name in DEFAULT_STAGE_NAMES.items()
            ],
        )

    def has_stage_overrides(self) -> bool:
        return any(
            stage.name != DEFAULT_STAGE_NAMES[stage.id] or stage.activity_guidance.strip()
            for stage in self.stages
        )


class LearningDesignResponse(BaseModel):
    design: DesignSnapshot | None


class LearningDesignModel(BaseModel):
    id: str
    name: str


class LearningDesignModelsResponse(BaseModel):
    models: list[LearningDesignModel]


class ApplyLearningDesignResponse(BaseModel):
    project_id: str
    revision: str
