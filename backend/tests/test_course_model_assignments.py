"""Course model catalog, live authorization, and snapshot persistence."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from beanie import PydanticObjectId, init_beanie
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from minio import Minio
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import ValidationError

with patch.object(Minio, "bucket_exists", return_value=True):
    from app.api.v1 import courses

from app.core.schemas.learning_design import DesignSnapshot, LearningDesign
from app.repositories.course import Course
from app.repositories.project import Project
from app.repositories.system_config import SystemConfig
from app.repositories.teacher_model_connection import TeacherModelConnection
from app.repositories.user import User
from app.services import course_model_service as service
from app.services.teacher_model_service import encrypt_teacher_api_key


BUILTIN = "evidence_researcher"
CUSTOM = "custom_0000000000000001"
DESIGN = {
    "roles": [{
        "id": CUSTOM,
        "enabled": True,
        "name": "Reviewer",
        "prompt": "Review the evidence.",
        "routing_anchor": BUILTIN,
    }],
    "orchestration_mode": "adaptive",
    "stages": [],
}
PRIVATE = "teacher:000000000000000000000001"
OTHER_PRIVATE = "teacher:000000000000000000000002"
ADMIN_PRIVATE = "teacher:000000000000000000000003"
UNAVAILABLE_SHARED = ("no-key", "no-endpoint", "unsupported", "research-only", "follow_system_default")


@pytest.mark.parametrize("assignments", [
    {"default_model_id": " "},
    {"default_model_id": " shared "},
    {"default_model_id": 7},
    {"role_model_ids": {BUILTIN: None}},
    {"role_model_ids": {"custom_0000000000000002": "shared"}},
    {"role_model_ids": {"answer_synthesizer": "shared"}},
    {"default_model_id": "shared", "api_key": "not-part-of-a-design"},
    {"default_model_id": "shared", "base_url": "https://example.invalid/v1"},
])
def test_assignment_schema_rejects_invalid_ids_roles_and_config(assignments):
    with pytest.raises(ValidationError):
        LearningDesign.model_validate({**DESIGN, "model_assignments": assignments})


def test_snapshot_keeps_roles_and_copies_assignments():
    assignments = {"default_model_id": "shared", "role_model_ids": {BUILTIN: "other", CUSTOM: PRIVATE}}
    design = LearningDesign.model_validate({**DESIGN, "model_assignments": assignments})
    snapshot = DesignSnapshot.from_design(design)
    design.model_assignments.role_model_ids.clear()
    assert snapshot.model_dump()["model_assignments"] == assignments
    assert snapshot.model_dump()["roles"][-1] == DESIGN["roles"][0]
    assert DesignSnapshot.from_design(LearningDesign.model_validate(DESIGN)).model_assignments is None


async def test_runtime_without_effective_bindings_never_queries_course(monkeypatch):
    load = AsyncMock(side_effect=AssertionError("No binding should not query a course"))
    monkeypatch.setattr(Course, "get", load)
    project = SimpleNamespace(course_id=str(PydanticObjectId()), experiment_version=None)
    assert await service.resolve_project_model_assignments(project) == {}
    project.experiment_version = {"learning_design": {}}
    assert await service.resolve_project_model_assignments(project) == {}
    for raw in (None, {}, {"default_model_id": None, "role_model_ids": {}}):
        project.experiment_version["learning_design"]["model_assignments"] = raw
        assert await service.resolve_project_model_assignments(project) == {}
    project.experiment_version["learning_design"]["model_assignments"] = {"role_model_ids": {BUILTIN: PRIVATE}}
    assert await service.resolve_project_model_assignments(project, include_roles=False) == {}
    load.assert_not_awaited()


@pytest.mark.parametrize("course_id", [None, "invalid-id", str(PydanticObjectId())])
async def test_runtime_missing_authoritative_course_fails_closed(monkeypatch, course_id):
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=None))
    project = SimpleNamespace(course_id=course_id, experiment_version={
        "source_course_id": str(PydanticObjectId()),
        "learning_design": {"model_assignments": {"default_model_id": "shared"}},
    })
    with pytest.raises(service.CourseModelUnavailable):
        await service.resolve_project_model_assignments(project)


@pytest.fixture
async def mongo_db():
    uri = os.environ.get("COURSE_TEST_MONGODB_URI") or os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set COURSE_TEST_MONGODB_URI to run course model integration tests")
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"course_models_test_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[Course, Project, SystemConfig, TeacherModelConnection, User])
        yield db
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


@pytest.fixture
async def setup(mongo_db):
    async def create_user(role, **extra):
        return await User(email=f"{uuid4().hex}@example.invalid", password_hash="unused", role=role, **extra).insert()

    owner = await create_user("teacher", config_permissions={"allowed_model_ids": ["shared", *UNAVAILABLE_SHARED]})
    other = await create_user("teacher")
    admin = await create_user("admin")
    course = await Course(name="Inquiry", teacher_id=str(owner.id), semester="2026-Fall", invite_code="COURSE").insert()
    group = await Project(name="Inquiry Group", course_id=str(course.id), owner_id=str(other.id)).insert()
    definition = {
        "api_key": "dedicated-secret",
        "base_url": "https://api.openai.com/v1", "provider": "openai_compatible",
    }
    dedicated = await SystemConfig(key="user_custom_models", updated_by=str(admin.id), value=json.dumps([
        {**definition, "id": "shared", "name": "Shared"},
        {**definition, "id": "restricted", "name": "Restricted"},
        {**definition, "id": "no-key", "api_key": ""},
        {**definition, "id": "no-endpoint", "base_url": ""},
        {**definition, "id": "unsupported", "provider": "ollama"},
        {**definition, "id": "follow_system_default"},
    ])).insert()
    await SystemConfig(key="research_model_pool", updated_by=str(admin.id), value=json.dumps([
        {**definition, "id": "research-only"},
    ])).insert()
    for connection_id, account, name in (
        (PRIVATE, owner, "My connection"),
        (OTHER_PRIVATE, other, "Other connection"),
        (ADMIN_PRIVATE, admin, "Admin connection"),
    ):
        await TeacherModelConnection(
            id=PydanticObjectId(connection_id.removeprefix("teacher:")), owner_id=str(account.id),
            name=name, provider="openai", model="test-model", base_url="https://api.openai.com/v1",
            encrypted_api_key=encrypt_teacher_api_key(str(account.id), "private-secret"),
        ).insert()

    context = SimpleNamespace(
        user=owner, owner=owner, other=other, admin=admin, course=course, group=group,
        dedicated=dedicated,
        url=f"/courses/{course.id}/learning-design",
    )
    app = FastAPI()
    app.include_router(courses.router)
    app.dependency_overrides[courses.get_current_user] = lambda: context.user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        context.client = client
        yield context


async def test_catalog_and_put_always_use_course_owner_authority(setup):
    client, url = setup.client, setup.url
    visible = {"models": [{"id": "shared", "name": "Shared"}, {"id": PRIVATE, "name": "My connection"}]}
    assert (await client.get(f"{url}/models")).json() == visible
    setup.user = setup.other
    assert (await client.get(f"{url}/models")).status_code == 403
    setup.user = SimpleNamespace(id=setup.owner.id, role="student")
    assert (await client.get(f"{url}/models")).status_code == 403

    setup.user = setup.admin
    assert (await client.get(f"{url}/models")).json() == visible
    for model_id in ("restricted", *UNAVAILABLE_SHARED, OTHER_PRIVATE, ADMIN_PRIVATE):
        response = await client.put(url, json={**DESIGN, "model_assignments": {"default_model_id": model_id}})
        assert response.status_code == 422
    assert (await Course.get(setup.course.id)).learning_design is None


async def test_admin_owned_course_allows_eligible_shared_models_and_only_its_own_private_models(setup):
    client = setup.client
    setup.user = setup.admin
    course = await Course(
        name="Admin course", teacher_id=str(setup.admin.id), semester="2026-Fall", invite_code="ADMIN",
    ).insert()
    group = await Project(name="Admin group", course_id=str(course.id), owner_id=str(setup.other.id)).insert()
    url = f"/courses/{course.id}/learning-design"
    response = await client.get(f"{url}/models")
    assert response.json() == {"models": [
        {"id": "shared", "name": "Shared"}, {"id": "restricted", "name": "Restricted"},
        {"id": ADMIN_PRIVATE, "name": "Admin connection"},
    ]}
    rejected = await client.put(url, json={**DESIGN, "model_assignments": {"default_model_id": PRIVATE}})
    assert rejected.status_code == 422

    assignments = {"default_model_id": ADMIN_PRIVATE, "role_model_ids": {BUILTIN: "restricted"}}
    response = await client.put(url, json={**DESIGN, "model_assignments": assignments})
    assert response.status_code == 200, response.text
    assert (await client.post(f"{url}/projects/{group.id}")).status_code == 200
    assert await service.resolve_project_model_assignments(await Project.get(group.id)) == {
        **assignments, "course_owner_id": str(setup.admin.id),
    }


@pytest.mark.parametrize("permissions", [None, {}, {"allowed_model_ids": []}, {"allowed_model_ids": "shared"}])
async def test_missing_empty_or_malformed_grants_do_not_grant_shared_models(setup, permissions):
    await User.find_one({"_id": setup.owner.id}).set({"config_permissions": permissions})
    response = await setup.client.get(f"{setup.url}/models")
    assert response.json() == {"models": [{"id": PRIVATE, "name": "My connection"}]}


@pytest.mark.parametrize("change", [{"is_active": False}, {"is_banned": True}, {"role": "student"}])
async def test_unavailable_owner_has_no_catalog_even_for_admin(setup, change):
    await User.find_one({"_id": setup.owner.id}).set(change)
    setup.user = setup.admin
    assert (await setup.client.get(f"{setup.url}/models")).json() == {"models": []}
    response = await setup.client.put(setup.url, json={**DESIGN, "model_assignments": {"default_model_id": PRIVATE}})
    assert response.status_code == 422


async def test_saved_snapshot_is_id_only_and_revalidates_revoked_bindings(setup):
    client, url, group = setup.client, setup.url, setup.group
    apply_url = f"{url}/projects/{group.id}"
    assignments = {"default_model_id": "shared", "role_model_ids": {CUSTOM: PRIVATE}}
    response = await client.put(url, json={**DESIGN, "model_assignments": assignments})
    assert response.status_code == 200, response.text
    snapshot = response.json()["design"]
    assert snapshot["model_assignments"] == assignments
    assert snapshot["roles"][-1] == DESIGN["roles"][0]
    assert (await client.get(url)).json() == {"design": snapshot}
    assert (await Course.get_motor_collection().find_one({"_id": setup.course.id}))["learning_design"] == snapshot
    assert (await client.post(apply_url)).json() == {"project_id": str(group.id), "revision": snapshot["revision"]}
    assert (await Project.get_motor_collection().find_one({"_id": group.id}))["experiment_version"]["learning_design"] == snapshot

    group = await Project.get(group.id)
    assert await service.resolve_project_model_assignments(group) == {**assignments, "course_owner_id": str(setup.owner.id)}

    await TeacherModelConnection.find_one({"owner_id": str(setup.owner.id)}).delete()
    assert (await client.post(apply_url)).status_code == 409
    assert (await Project.get(group.id)).experiment_version == group.experiment_version
    with pytest.raises(service.CourseModelUnavailable):
        await service.resolve_project_model_assignments(group)
    assert await service.resolve_project_model_assignments(group, include_roles=False) == {
        "default_model_id": "shared", "role_model_ids": {}, "course_owner_id": str(setup.owner.id),
    }

    await User.find_one({"_id": setup.owner.id}).set({"config_permissions": {"allowed_model_ids": []}})
    with pytest.raises(service.CourseModelUnavailable):
        await service.resolve_project_model_assignments(group, include_roles=False)
    replacement = await client.put(url, json={**DESIGN, "model_assignments": None})
    assert replacement.status_code == 200
    assert replacement.json()["design"]["model_assignments"] is None
    assert (await Project.get(group.id)).experiment_version["learning_design"] == snapshot


async def test_runtime_rechecks_current_owner_and_dedicated_pool(setup):
    client, url, group = setup.client, setup.url, setup.group
    response = await client.put(url, json={**DESIGN, "model_assignments": {"default_model_id": "shared"}})
    assert response.status_code == 200
    assert (await client.post(f"{url}/projects/{group.id}")).status_code == 200
    group = await Project.get(group.id)

    await Course.find_one({"_id": setup.course.id}).set({"teacher_id": str(setup.other.id)})
    with pytest.raises(service.CourseModelUnavailable):
        await service.resolve_project_model_assignments(group)
    await User.find_one({"_id": setup.other.id}).set({"config_permissions": {"allowed_model_ids": ["shared"]}})
    assert (await service.resolve_project_model_assignments(group))["course_owner_id"] == str(setup.other.id)
    await setup.dedicated.set({"value": "[]"})
    with pytest.raises(service.CourseModelUnavailable):
        await service.resolve_project_model_assignments(group)
