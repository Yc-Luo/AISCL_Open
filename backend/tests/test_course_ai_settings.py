"""Regression tests for course AI settings and group creation."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from beanie import PydanticObjectId, init_beanie
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from minio import Minio
from motor.motor_asyncio import AsyncIOMotorClient

with patch.object(Minio, "bucket_exists", return_value=True):
    from app.api.v1 import courses, projects

from app.core.schemas.course import CourseAISettings
from app.repositories.course import Course
from app.repositories.project import Project
from app.services.project_service import ProjectService


MULTI_SETTINGS = {
    "ai_scaffold_mode": "multi_agent",
    "process_scaffold_mode": "on",
    "collaboration_memory_enabled": True,
    "auto_prompt_enabled": True,
}
SINGLE_SETTINGS = {
    "ai_scaffold_mode": "single_agent",
    "process_scaffold_mode": "off",
    "collaboration_memory_enabled": False,
    "auto_prompt_enabled": False,
}
STAGES = [
    "problem_construction",
    "meaning_exploration",
    "explanation_integration",
    "application_solution",
]


@pytest.fixture
def api(monkeypatch):
    course = Course.model_construct(
        id=PydanticObjectId(),
        name="Inquiry",
        teacher_id="teacher",
        semester="2026-Fall",
        invite_code="TEST01",
        students=["student"],
        experiment_template_key="legacy",
        experiment_template_snapshot={
            "mode": "research",
            "version_name": "legacy",
            "group_condition": "legacy_condition",
            "stage_sequence": ["inquiry"],
        },
    )
    user = SimpleNamespace(id="teacher", role="teacher", class_id=None)
    groups = {}

    async def get_course(course_id):
        return course if str(course_id) == str(course.id) else None

    async def set_course_fields(fields):
        course.ai_settings = CourseAISettings.model_validate(fields["ai_settings"])
        return SimpleNamespace(matched_count=1)

    async def insert_project(project):
        project.id = PydanticObjectId()
        groups[str(project.id)] = project

    set_fields = AsyncMock(side_effect=set_course_fields)
    monkeypatch.setattr(Course, "get", get_course)
    monkeypatch.setattr(
        Course, "find_one", Mock(return_value=SimpleNamespace(set=set_fields))
    )
    monkeypatch.setattr(Project, "get_motor_collection", Mock())
    monkeypatch.setattr(Project, "insert", insert_project)
    monkeypatch.setattr(Project, "save", AsyncMock())
    monkeypatch.setattr(Project, "get", AsyncMock(side_effect=groups.get))
    monkeypatch.setattr(
        Project,
        "find",
        Mock(return_value=SimpleNamespace(to_list=AsyncMock(return_value=[]))),
    )
    monkeypatch.setattr(
        projects, "generate_unique_group_code", AsyncMock(return_value="GROUP1")
    )

    app = FastAPI()
    app.include_router(courses.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.dependency_overrides[courses.get_current_user] = lambda: user
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            course=course,
            user=user,
            url=f"/api/v1/courses/{course.id}/ai-settings",
            set_fields=set_fields,
            groups=groups,
        )


@pytest.mark.parametrize("role,user_id", [("teacher", "teacher"), ("admin", "admin")])
def test_manager_can_read_and_save_independent_settings(api, role, user_id):
    api.user.role, api.user.id = role, user_id
    initial = api.client.get(api.url)
    assert initial.status_code == 200
    assert initial.json() == {"settings": None}

    updated = api.client.put(api.url, json=MULTI_SETTINGS)
    assert updated.status_code == 200
    assert (
        updated.json() == api.client.get(api.url).json() == {"settings": MULTI_SETTINGS}
    )
    api.set_fields.assert_awaited_once()
    assert set(api.set_fields.await_args.args[0]) == {"ai_settings", "updated_at"}
    assert api.course.experiment_template_key == "legacy"
    assert (
        api.course.experiment_template_snapshot["group_condition"] == "legacy_condition"
    )


@pytest.mark.parametrize(
    "role,user_id",
    [("teacher", "other"), ("student", "student"), ("student", "teacher")],
)
def test_other_users_cannot_read_or_change_settings(api, role, user_id):
    api.user.role, api.user.id = role, user_id
    assert api.client.get(api.url).status_code == 403
    assert api.client.put(api.url, json=MULTI_SETTINGS).status_code == 403
    api.set_fields.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {**SINGLE_SETTINGS, "collaboration_memory_enabled": True},
        {**SINGLE_SETTINGS, "process_scaffold_mode": "on", "auto_prompt_enabled": True},
        {**MULTI_SETTINGS, "process_scaffold_mode": "off"},
        {**MULTI_SETTINGS, "ai_scaffold_mode": "multi-ai"},
        {**MULTI_SETTINGS, "process_scaffold_mode": "enabled"},
        {**MULTI_SETTINGS, "auto_prompt_enabled": "true"},
        {**MULTI_SETTINGS, "collaboration_memory_enabled": 1},
        {**MULTI_SETTINGS, "group_condition": "injected"},
        *[
            {key: value for key, value in MULTI_SETTINGS.items() if key != missing}
            for missing in MULTI_SETTINGS
        ],
    ],
)
def test_invalid_settings_are_not_persisted(api, body):
    assert api.client.put(api.url, json=body).status_code == 422
    api.set_fields.assert_not_awaited()


def create_group(api, **overrides):
    response = api.client.post(
        "/api/v1/projects",
        json={"name": "Inquiry group", "course_id": str(api.course.id), **overrides},
    )
    assert response.status_code == 201, response.text
    return api.groups[response.json()["id"]]


def test_settings_override_legacy_only_for_new_groups_that_inherit(api):
    legacy = create_group(api)
    legacy_version = legacy.experiment_version.copy()
    assert legacy.inherited_template_key == "legacy"
    assert legacy_version["group_condition"] == "legacy_condition"
    assert legacy_version["stage_sequence"] == ["meaning_exploration"]

    assert api.client.put(api.url, json=MULTI_SETTINGS).status_code == 200
    assert legacy.experiment_version == legacy_version

    opted_out = create_group(api, inherit_course_template=False)
    assert opted_out.experiment_version is None
    assert opted_out.inherited_template_key is None

    api.user.role, api.user.id = "student", "student"
    inherited = create_group(api)  # Uses the student request's default inheritance.
    version = inherited.experiment_version
    assert inherited.leader_id == api.user.id
    assert inherited.inherited_template_key is None
    assert {key: version[key] for key in MULTI_SETTINGS} == MULTI_SETTINGS
    assert version["mode"] == "default"
    assert version["template_key"] is None
    assert version["group_condition"] is None
    assert version["source_course_id"] == str(api.course.id)
    assert version["stage_sequence"] == STAGES
    assert version["current_stage"] == STAGES[0]
    assert version["enabled_scaffold_layers"] == [
        "multi_agent_scaffold",
        "process_scaffold",
    ]


def test_process_off_keeps_stage_progression(api):
    assert api.client.put(api.url, json=SINGLE_SETTINGS).status_code == 200
    group = create_group(api)
    assert group.experiment_version["stage_sequence"] == STAGES
    assert group.experiment_version["current_stage"] == STAGES[0]
    assert group.experiment_version["enabled_scaffold_layers"] == []
    assert group.experiment_version["enabled_scaffold_roles"] == []


@pytest.mark.asyncio
async def test_stale_course_edit_preserves_ai_settings():
    uri = os.environ.get("COURSE_TEST_MONGODB_URI") or os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip(
            "Set COURSE_TEST_MONGODB_URI to run course settings integration tests"
        )

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"course_ai_test_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[Course])
        course = await Course(
            name="Inquiry",
            teacher_id="teacher",
            semester="2026-Fall",
            invite_code="TEST01",
            experiment_template_key="legacy",
        ).insert()
        stale = await Course.get(course.id)

        app = FastAPI()
        app.include_router(courses.router, prefix="/api/v1")
        app.dependency_overrides[courses.get_current_user] = lambda: SimpleNamespace(
            id="teacher", role="teacher"
        )
        url = f"/api/v1/courses/{course.id}/ai-settings"
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(url, json=MULTI_SETTINGS)
            assert response.status_code == 200, response.text

            stale.description = "Edited from a pre-settings snapshot"
            await stale.save_changes()

            assert (await client.get(url)).json() == {"settings": MULTI_SETTINGS}
            saved = await Course.get(course.id)
            assert saved.description == stale.description
            assert saved.experiment_template_key == "legacy"
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


@pytest.mark.asyncio
async def test_stale_group_stage_update_preserves_ai_settings():
    uri = os.environ.get("COURSE_TEST_MONGODB_URI") or os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set COURSE_TEST_MONGODB_URI to run group configuration integration tests")

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"group_ai_test_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[Project])
        group = await Project(name="Inquiry group", owner_id="teacher").insert()
        group_stage_copy = await Project.get(group.id)
        assert group_stage_copy.experiment_version is None

        ai_override = {**MULTI_SETTINGS, "version_name": "$teacher-plan"}
        await ProjectService.update_experiment_version(group, ai_override)
        saved = await ProjectService.update_experiment_version(
            group_stage_copy, {"current_stage": "meaning_exploration"},
        )
        assert saved["current_stage"] == "meaning_exploration"
        assert {key: saved[key] for key in ai_override} == ai_override
        assert saved["group_condition"] is None
    finally:
        await mongo.drop_database(db.name)
        mongo.close()
