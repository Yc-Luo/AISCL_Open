"""Course learning design validation, persistence, and public projection."""

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

from app.core.schemas.learning_design import DesignSnapshot, LearningDesign
from app.repositories.course import Course
from app.repositories.project import Project


ROLE_IDS = [
    "evidence_researcher", "viewpoint_challenger", "feedback_prompter", "problem_progressor",
]
STAGE_IDS = [
    "problem_construction", "meaning_exploration", "explanation_integration", "application_solution",
]
ROLES = [
    {"id": ROLE_IDS[0], "enabled": False, "prompt": "private teacher prompt"},
    {"id": ROLE_IDS[2], "enabled": True, "prompt": "$teacher-note"},
]
STAGE = {"id": STAGE_IDS[1], "name": "", "activity_guidance": "$Compare two sources."}
DESIGN = {"roles": ROLES, "orchestration_mode": "parallel", "stages": [STAGE]}
DISABLED_BUILTINS = [{"id": role_id, "enabled": False, "prompt": ""} for role_id in ROLE_IDS]
AI_SETTINGS = {
    "ai_scaffold_mode": "single_agent",
    "process_scaffold_mode": "off",
    "collaboration_memory_enabled": False,
    "auto_prompt_enabled": False,
}


def custom_role(index=0, **overrides):
    return {
        "id": f"custom_{index:016x}",
        "enabled": True,
        "prompt": "  $Private custom role instructions.  ",
        "name": f"Source Reviewer {index}",
        "routing_anchor": ROLE_IDS[0],
        **overrides,
    }


def make_app(user):
    app = FastAPI()
    app.include_router(courses.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.dependency_overrides[courses.get_current_user] = lambda: user
    return app


@pytest.fixture
def api(monkeypatch):
    course = Course.model_construct(id=PydanticObjectId(), teacher_id="teacher")
    user = SimpleNamespace(id="teacher", role="teacher")

    async def load_course(course_id):
        return course if course_id == course.id else None

    monkeypatch.setattr(Course, "get", load_course)
    save = Mock(side_effect=AssertionError("Invalid requests must not write"))
    monkeypatch.setattr(Course, "find_one", save)
    apply = AsyncMock(side_effect=AssertionError("Invalid requests must not write"))
    monkeypatch.setattr(courses.project_service, "apply_course_learning_design", apply)
    with TestClient(make_app(user)) as client:
        yield client, user, f"/api/v1/courses/{course.id}/learning-design"


@pytest.mark.parametrize(
    "body",
    [
        *[{k: v for k, v in DESIGN.items() if k != missing} for missing in DESIGN],
        {**DESIGN, "revision": "injected"},
        {**DESIGN, "ai_scaffold_mode": "multi_agent"},
        {**DESIGN, "orchestration_mode": "invalid"},
        {**DESIGN, "roles": [ROLES[0]] * 2},
        {**DESIGN, "roles": [ROLES[0]] * 5},
        {**DESIGN, "roles": [{**ROLES[0], "id": role_id} for role_id in ROLE_IDS]},
        {**DESIGN, "roles": [{**ROLES[0], "id": "cognitive_support"}]},
        {**DESIGN, "roles": [{**ROLES[0], "enabled": "false"}]},
        {**DESIGN, "roles": [{**ROLES[0], "prompt": "x" * 2501}]},
        {**DESIGN, "roles": [{**ROLES[0], "system_prompt": "injected"}]},
        {**DESIGN, "roles": [{"id": ROLE_IDS[0], "enabled": True}]},
        {**DESIGN, "stages": [STAGE] * 2},
        {**DESIGN, "stages": [STAGE] * 5},
        {**DESIGN, "stages": [{**STAGE, "id": "inquiry"}]},
        {**DESIGN, "stages": [{**STAGE, "name": "x" * 49}]},
        {**DESIGN, "stages": [{**STAGE, "activity_guidance": "x" * 1601}]},
        {**DESIGN, "stages": [{**STAGE, "stage_control_mode": "hard_constraint"}]},
        {**DESIGN, "stages": [{"id": STAGE_IDS[0], "name": ""}]},
    ],
)
def test_invalid_design_rejected(api, body):
    client, _, url = api
    assert client.put(url, json=body).status_code == 422


@pytest.mark.parametrize(
    "roles",
    [
        [custom_role(), custom_role(1, name=" SOURCE REVIEWER 0 ", enabled=False)],
        [custom_role(), custom_role(name="Different name")],
        *[[custom_role(name=f" {label} ")] for label in ("资料研究员", "观点挑战者", "反馈追问者", "问题推进者")],
        [custom_role(routing_anchor="cognitive_support")],
        [custom_role(routing_anchor=custom_role(1)["id"])],
        [custom_role(id="custom_000000000000000F")],
        [custom_role(id="custom_000000000000000")],
        [custom_role(id="custom_0000000000000000\n")],
        [custom_role(name=" ")],
        [custom_role(name="x" * 41)],
        [custom_role(name="Source\nReviewer")],
        [custom_role(name="Reviewer\u2028")],
        [custom_role(prompt=" \t\n")],
        [custom_role(prompt="x" * 2501)],
        [custom_role(enabled="true")],
        [custom_role(system_prompt="injected")],
        [{k: v for k, v in custom_role().items() if k != "routing_anchor"}],
        [{**ROLES[0], "name": "Renamed built-in", "routing_anchor": ROLE_IDS[0]}],
        [custom_role(i) for i in range(7)],
        [*DISABLED_BUILTINS, *(custom_role(i) for i in range(7))],
        [*DISABLED_BUILTINS, custom_role(enabled=False)],
    ],
)
def test_invalid_custom_roles_rejected(api, roles):
    client, _, url = api
    assert client.put(url, json={**DESIGN, "roles": roles}).status_code == 422


def test_snapshot_normalizes_builtins_and_preserves_custom_priority():
    first = custom_role(8, name=f"  {'n' * 40}  ", prompt="p" * 2500, enabled=False)
    submitted = [first, *reversed(DISABLED_BUILTINS[1:]), *(custom_role(i) for i in range(5))]
    snapshot = DesignSnapshot.from_design(LearningDesign.model_validate({**DESIGN, "roles": submitted}))
    assert snapshot.model_dump()["roles"] == [
        {**DISABLED_BUILTINS[0], "enabled": True},
        *DISABLED_BUILTINS[1:],
        {**first, "name": "n" * 40},
        *(custom_role(i) for i in range(5)),
    ]


def test_public_experiment_version_only_projects_scaffold_role_families():
    active = custom_role(2)
    roles = [*DISABLED_BUILTINS, active, custom_role(1), custom_role(3, enabled=False, routing_anchor=ROLE_IDS[1])]
    design = LearningDesign.model_validate({**DESIGN, "roles": roles})
    snapshot = DesignSnapshot.from_design(design).model_dump()
    version = {
        "project_id": "group",
        "ai_scaffold_mode": "multi_agent",
        "enabled_scaffold_roles": ["feedback_prompting", active["id"]],
    }
    public = projects._public_experiment_version({**version, "learning_design": snapshot})
    assert public.enabled_scaffold_roles == ["cognitive_support"]
    assert public.learning_design_revision == snapshot["revision"]
    assert public.stage_guidance[1].activity_guidance == STAGE["activity_guidance"]
    assert "learning_design" not in public.model_dump()
    for private in (active["id"], active["name"], active["prompt"].strip()):
        assert private not in public.model_dump_json()


def test_public_experiment_version_preserves_legacy_roles_without_design():
    roles = ["feedback_prompting", "legacy_role"]
    public = projects._public_experiment_version({"project_id": "group", "enabled_scaffold_roles": roles})
    assert public.enabled_scaffold_roles == roles


@pytest.mark.parametrize("role,user_id", [("teacher", "other"), ("student", "teacher")])
def test_course_design_requires_owner_teacher_or_admin(api, role, user_id):
    client, user, url = api
    user.role, user.id = role, user_id
    assert client.get(url).status_code == 403
    assert client.put(url, json=DESIGN).status_code == 403
    assert client.post(f"{url}/projects/{PydanticObjectId()}").status_code == 403


@pytest.fixture
async def mongo_db():
    uri = os.environ.get("COURSE_TEST_MONGODB_URI") or os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set COURSE_TEST_MONGODB_URI to run learning design integration tests")
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"course_design_test_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[Course, Project])
        yield db
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


async def test_project_listing_intersects_course_filter_with_membership_and_pagination(mongo_db):
    first, second, foreign = [
        await Course(
            name=f"Course {number}", teacher_id=owner, semester="2026-Fall", invite_code=f"FILTER{number}"
        ).insert()
        for number, owner in enumerate(("teacher", "teacher", "other"))
    ]
    visible = await Project(name="Member group", owner_id="student", course_id=str(first.id)).insert()
    archived = await Project(
        name="Archived group", owner_id="peer", course_id=str(first.id), is_archived=True
    ).insert()
    sibling = await Project(name="Sibling group", owner_id="student", course_id=str(second.id)).insert()
    await Project(name="External group", owner_id="other", course_id=str(foreign.id)).insert()
    user = SimpleNamespace(id="teacher", role="teacher")
    url = "/api/v1/projects"

    async with AsyncClient(transport=ASGITransport(app=make_app(user)), base_url="http://test") as client:
        async def list_groups(**params):
            response = await client.get(url, params=params)
            assert response.status_code == 200, response.text
            result = response.json()
            return {group["id"] for group in result["projects"]}, result["total"]

        assert await list_groups() == ({str(visible.id), str(archived.id), str(sibling.id)}, 3)
        assert await list_groups(course_id=str(first.id), limit=1, skip=1) == ({str(archived.id)}, 2)
        assert await list_groups(course_id=str(first.id), archived=False) == ({str(visible.id)}, 1)
        assert await list_groups(course_id=str(foreign.id)) == (set(), 0)

        user.role, user.id = "student", "student"
        assert await list_groups(course_id=str(first.id)) == ({str(visible.id)}, 1)
        user.id = "unassigned"
        assert await list_groups(course_id=str(first.id)) == (set(), 0)


async def test_snapshot_lifecycle_and_public_projection(mongo_db):
    course = await Course(
        name="Inquiry", teacher_id="teacher", semester="2026-Fall", invite_code="TEST01",
    ).insert()
    stale_course = await Course.get(course.id)
    other = await Course(
        name="Other course", teacher_id="teacher", semester="2026-Fall", invite_code="TEST02",
    ).insert()
    previous = {
        "ai_scaffold_mode": "multi_agent",
        "process_scaffold_mode": "on",
        "enabled_scaffold_roles": ["cognitive_support"],
        "auto_prompt_enabled": True,
        "stage_sequence": [STAGE_IDS[0], STAGE_IDS[2]],
        "current_stage": STAGE_IDS[2],
        "group_condition": "legacy",
    }

    async def create_existing(**overrides):
        fields = {"course_id": str(course.id), "experiment_version": previous, **overrides}
        return await Project(name="Existing group", owner_id="student", **fields).insert()

    target = await create_existing()
    peer = await create_existing()
    archived = await create_existing(is_archived=True)
    template = await create_existing(is_template=True)
    outside = await create_existing(course_id=str(other.id))
    user = SimpleNamespace(id="teacher", role="teacher")
    root = f"/api/v1/courses/{course.id}"
    url = f"{root}/learning-design"
    project_url = f"/api/v1/projects/{target.id}"

    async with AsyncClient(transport=ASGITransport(app=make_app(user)), base_url="http://test") as client:
        assert (await client.get(url)).json() == {"design": None}
        assert (await client.post(f"{url}/projects/{target.id}")).status_code == 409

        saved = await client.put(url, json=DESIGN)
        assert saved.status_code == 200, saved.text
        snapshot = saved.json()["design"]
        assert [role["id"] for role in snapshot["roles"]] == ROLE_IDS
        assert snapshot["roles"][0] == ROLES[0]
        assert snapshot["roles"][1] == {"id": ROLE_IDS[1], "enabled": True, "prompt": ""}
        assert [stage["id"] for stage in snapshot["stages"]] == STAGE_IDS
        assert snapshot["stages"][1] == {**STAGE, "name": "意义探索"}
        assert snapshot["stages"][0]["activity_guidance"] == ""
        stale_course.description = "Ordinary course edit"
        await stale_course.save_changes()
        assert (await client.get(url)).json() == saved.json()
        assert "learning_design" not in (await client.get(root)).json()

        legacy = (await client.get(f"{project_url}/experiment-version")).json()
        assert legacy["enabled_scaffold_roles"] == previous["enabled_scaffold_roles"]
        assert legacy["stage_guidance"] == [] and legacy["learning_design_revision"] is None
        assert (await Project.get(target.id)).experiment_version == previous

        created = await client.post("/api/v1/projects", json={"name": "New group", "course_id": str(course.id)})
        assert created.status_code == 201, created.text
        created_version = (await Project.get(created.json()["id"])).experiment_version
        assert created_version["learning_design"] == snapshot
        assert created_version["stage_sequence"] == STAGE_IDS
        assert created_version["current_stage"] == STAGE_IDS[0]
        assert created_version["ai_scaffold_mode"] == "single_agent"
        opted_out = await client.post("/api/v1/projects", json={
            "name": "Opted-out group", "course_id": str(course.id), "inherit_course_template": False,
        })
        assert opted_out.status_code == 201, opted_out.text
        assert (await Project.get(opted_out.json()["id"])).experiment_version is None

        assert (await client.put(f"{root}/ai-settings", json=AI_SETTINGS)).status_code == 200
        user.role, user.id = "admin", "admin"
        assert (await client.get(url)).json() == saved.json()
        applied = await client.post(f"{url}/projects/{target.id}")
        assert applied.status_code == 200, applied.text
        assert applied.json() == {"project_id": str(target.id), "revision": snapshot["revision"]}
        target_version = (await Project.get(target.id)).experiment_version
        assert target_version.pop("learning_design") == snapshot
        assert target_version.pop("updated_at")
        assert target_version == previous
        for group in (archived, template, outside):
            assert (await client.post(f"{url}/projects/{group.id}")).status_code == 404
        for group in (peer, archived, template, outside):
            assert (await Project.get(group.id)).experiment_version == previous

        user.role, user.id = "student", "student"
        visible = await client.get(f"{project_url}/experiment-version")
        assert visible.status_code == 200, visible.text
        assert visible.json()["enabled_scaffold_roles"] == [
            "viewpoint_challenge", "feedback_prompting", "problem_progression",
        ]
        assert visible.json()["stage_guidance"] == snapshot["stages"]
        assert visible.json()["learning_design_revision"] == snapshot["revision"]
        for response in (visible, await client.get(project_url)):
            assert "learning_design" not in response.json()
            assert "private teacher prompt" not in response.text
            assert "$teacher-note" not in response.text

        user.role, user.id = "admin", "admin"
        version_update = await client.put(f"{project_url}/experiment-version", json={"current_stage": STAGE_IDS[2]})
        assert version_update.status_code == 200, version_update.text
        assert version_update.json() == visible.json() | {"updated_at": version_update.json()["updated_at"]}
        replacement = await client.put(url, json={"roles": [], "orchestration_mode": "adaptive", "stages": []})
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["design"]["revision"] != snapshot["revision"]
        assert (await Project.get(target.id)).experiment_version["learning_design"] == snapshot
        assert (await client.get(f"{root}/ai-settings")).json() == {"settings": AI_SETTINGS}

        other_design = await client.put(
            f"/api/v1/courses/{other.id}/learning-design",
            json={"roles": [], "orchestration_mode": "adaptive", "stages": []},
        )
        assert other_design.status_code == 200, other_design.text
        defaults_only = await client.post(
            "/api/v1/projects", json={"name": "Defaults-only group", "course_id": str(other.id)},
        )
        assert defaults_only.status_code == 201, defaults_only.text
        default_version = (await Project.get(defaults_only.json()["id"])).experiment_version
        assert default_version["learning_design"] == other_design.json()["design"]
        assert default_version["stage_sequence"] == [] and default_version["current_stage"] is None


async def test_custom_role_changes_require_explicit_reapply_to_each_group(mongo_db):
    course = await Course(
        name="Inquiry", teacher_id="teacher", semester="2026-Fall", invite_code="TEST01",
    ).insert()
    groups = [
        await Project(
            name=name, owner_id="teacher", course_id=str(course.id),
            experiment_version={"ai_scaffold_mode": "multi_agent"},
        ).insert()
        for name in ("Legacy group", "Target group", "Peer group")
    ]
    legacy, target, peer = groups
    app = make_app(SimpleNamespace(id="teacher", role="teacher"))
    url = f"/api/v1/courses/{course.id}/learning-design"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def save(roles):
            response = await client.put(url, json={**DESIGN, "roles": roles})
            assert response.status_code == 200, response.text
            assert (await client.get(url)).json() == response.json()
            return response.json()["design"]

        async def apply(group, snapshot):
            response = await client.post(f"{url}/projects/{group.id}")
            assert response.status_code == 200, response.text
            assert response.json() == {"project_id": str(group.id), "revision": snapshot["revision"]}

        builtin_snapshot = await save(ROLES)
        await apply(legacy, builtin_snapshot)
        custom_roles = [custom_role(3), custom_role(1), custom_role(2, routing_anchor=ROLE_IDS[1])]
        original = await save([*DISABLED_BUILTINS, *custom_roles])
        assert original["roles"] == [*DISABLED_BUILTINS, *custom_roles]
        await apply(target, original)
        await apply(peer, original)

        edited = {**custom_roles[0], "name": "Edited reviewer", "prompt": "New instructions"}
        stopped = {**custom_roles[1], "enabled": False}
        added = custom_role(4, routing_anchor=ROLE_IDS[3])
        replacement = await save([*DISABLED_BUILTINS, edited, stopped, added])
        assert replacement["revision"] != original["revision"]
        assert replacement["roles"] == [*DISABLED_BUILTINS, edited, stopped, added]
        assert (await Project.get(legacy.id)).experiment_version["learning_design"] == builtin_snapshot
        for group in (target, peer):
            assert (await Project.get(group.id)).experiment_version["learning_design"] == original

        public_url = f"/api/v1/projects/{target.id}/experiment-version"
        visible = await client.get(public_url)
        assert visible.status_code == 200, visible.text
        assert visible.json()["enabled_scaffold_roles"] == ["cognitive_support", "viewpoint_challenge"]
        assert visible.json()["learning_design_revision"] == original["revision"]
        await apply(target, replacement)
        visible = await client.get(public_url)
        assert visible.status_code == 200, visible.text
        assert visible.json()["enabled_scaffold_roles"] == ["cognitive_support", "problem_progression"]
        assert visible.json()["learning_design_revision"] == replacement["revision"]
        assert (await Project.get(target.id)).experiment_version["learning_design"] == replacement
        assert (await Project.get(peer.id)).experiment_version["learning_design"] == original


@pytest.mark.parametrize(
    "previous,sequence,current",
    [
        (None, STAGE_IDS, STAGE_IDS[0]),
        ({"stage_sequence": [], "current_stage": None}, STAGE_IDS, STAGE_IDS[0]),
        ({"current_stage": STAGE_IDS[2]}, STAGE_IDS, STAGE_IDS[2]),
        ({"stage_sequence": [STAGE_IDS[2]]}, [STAGE_IDS[2]], None),
    ],
)
async def test_apply_initializes_only_missing_stage_progress(mongo_db, previous, sequence, current):
    course = await Course(
        name="Inquiry", teacher_id="teacher", semester="2026-Fall", invite_code="TEST01",
    ).insert()
    group = await Project(
        name="Existing group", owner_id="teacher", course_id=str(course.id), experiment_version=previous,
    ).insert()
    app = make_app(SimpleNamespace(id="teacher", role="teacher"))
    url = f"/api/v1/courses/{course.id}/learning-design"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.put(url, json={**DESIGN, "stages": []})).status_code == 200
        assert (await client.post(f"{url}/projects/{group.id}")).status_code == 200
        version = (await Project.get(group.id)).experiment_version
        assert version.get("stage_sequence") == (previous or {}).get("stage_sequence")
        assert version.get("current_stage") == (previous or {}).get("current_stage")

        assert (await client.put(url, json=DESIGN)).status_code == 200
        assert (await client.post(f"{url}/projects/{group.id}")).status_code == 200
        version = (await Project.get(group.id)).experiment_version
        assert version["stage_sequence"] == sequence
        assert version.get("current_stage") == current
