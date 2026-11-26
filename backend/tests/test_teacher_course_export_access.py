"""Teacher exports use a database-backed course scope and a private download ticket."""

import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from beanie import init_beanie
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_archive", [False, True])
async def test_teacher_archive_worker_completes_or_cleans_partial_file(monkeypatch, tmp_path, fail_archive):
    from app.services import teacher_course_export_archive as archive
    from app.services import teacher_course_exports as exports

    monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)
    course = SimpleNamespace(id="class-id", teacher_id="teacher-id", name="教学测试班")
    teacher = SimpleNamespace(role="teacher", is_active=True, is_banned=False)
    monkeypatch.setattr(exports.Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(exports.User, "get", AsyncMock(return_value=teacher))
    job = SimpleNamespace(
        id="job-id", course_id=course.id, requested_by=course.teacher_id,
        project_ids=["group-id"], export_profile="artifacts", from_date=None,
        through_date=None, stage_id=None, include_files=True, status="queued", progress=0,
    )
    saved = []

    async def save():
        saved.append((job.status, job.progress))

    job.save = save
    collect = AsyncMock(return_value={"collected": True})
    monkeypatch.setattr(archive, "collect_teacher_export", collect)

    def write(path, selected_course, data, *, profile, include_files):
        assert selected_course is course and data == {"collected": True}
        assert profile == "artifacts" and include_files is True
        with zipfile.ZipFile(path, "w") as result:
            result.writestr("test.csv", "case_id\ncase-1")
        if fail_archive:
            raise OSError("private-system-path")
        return 1

    monkeypatch.setattr(archive, "write_teacher_export", write)
    await exports._run_teacher_export(job)

    collect.assert_awaited_once_with(
        course, project_ids=["group-id"], profile="artifacts", from_date=None, through_date=None, stage_id=None,
    )
    assert saved[:2] == [("running", 10), ("running", 55)]
    assert not exports.export_path(job).with_suffix(".tmp").exists()
    if fail_archive:
        assert job.status == "failed" and not exports.export_path(job).exists()
        assert "private-system-path" not in job.message
    else:
        assert saved[-1] == ("completed", 100) and job.missing_files == 1
        assert "教学测试班" in job.filename
        with zipfile.ZipFile(exports.export_path(job)) as result:
            assert result.read("test.csv") == b"case_id\ncase-1"


@pytest.mark.asyncio
async def test_teacher_exports_check_course_scope_again_at_download(monkeypatch, tmp_path):
    uri = os.environ.get("COURSE_TEST_MONGODB_URI") or os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set CHAT_TEST_MONGODB_URI to run teacher export integration tests")

    from app.api.v1 import admin_data, course_exports
    from app.repositories.course import Course
    from app.repositories.export_job import ExportJob
    from app.repositories.project import Project
    from app.repositories.user import User
    from app.services import teacher_course_exports as exports

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"teacher_course_exports_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[Course, ExportJob, Project, User])
        actors = {}
        for username, role in [("owner", "teacher"), ("peer", "teacher"), ("student", "student"), ("admin", "admin")]:
            actors[username] = await User(
                username=username, role=role, email=f"{username}@example.test", password_hash="unused",
            ).insert()

        async def create_course(name, teacher):
            return await Course(name=name, teacher_id=str(actors[teacher].id), semester="2026 秋季", invite_code=teacher).insert()

        own = await create_course("Own course", "owner")
        other = await create_course("Other course", "peer")
        second_own = await create_course("Second own course", "owner")
        active = await Project(name="Active group", course_id=str(own.id), owner_id=own.teacher_id).insert()
        archived = await Project(name="Archived group", course_id=str(own.id), owner_id=own.teacher_id, is_archived=True).insert()
        foreign = await Project(name="Foreign group", course_id=str(other.id), owner_id=other.teacher_id).insert()
        template = await Project(name="Template group", course_id=str(own.id), owner_id=own.teacher_id, is_template=True).insert()

        async def current_actor(x_actor: str = Header("owner")):
            return await User.get(actors[x_actor].id)

        monkeypatch.setattr(exports, "EXPORT_DIR", tmp_path)
        launch = Mock()
        monkeypatch.setattr(course_exports, "queue_teacher_export", launch)
        app = FastAPI()
        app.include_router(course_exports.router, prefix="/api/v1")
        app.include_router(admin_data.router, prefix="/api/v1")
        app.dependency_overrides[course_exports.get_current_user] = current_actor
        own_path = f"/api/v1/courses/{own.id}"
        job_path = f"{own_path}/export-jobs"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://aiscl.test") as api:
            groups = await api.get(f"{own_path}/export-groups")
            assert groups.status_code == 200
            assert groups.json() == {"groups": [
                {"id": str(active.id), "name": active.name, "is_archived": False},
                {"id": str(archived.id), "name": archived.name, "is_archived": True},
            ]}
            for actor in ("peer", "student", "admin"):
                headers = {"X-Actor": actor}
                assert (await api.get(job_path, headers=headers)).status_code == 403
                assert (await api.post(job_path, json={"profile": "research"}, headers=headers)).status_code == 403
            for invalid in (
                {"project_ids": [str(active.id), str(foreign.id)]}, {"project_ids": [str(template.id)]},
                {"project_ids": []}, {"from_date": "2026-12-01", "through_date": "2026-11-01"},
                {"include_files": True}, {"include_private_ai": True},
            ):
                assert (await api.post(job_path, json={"profile": "research", **invalid})).status_code == 422
            launch.assert_not_called()

            created = await api.post(job_path, json={
                "profile": "research", "project_ids": [str(archived.id)],
                "stage_id": "meaning_exploration", "from_date": "2026-11-20", "through_date": "2026-11-25",
            })
            assert created.status_code == 202, created.text
            submitted = created.json()
            assert submitted["project_ids"] == [str(archived.id)] and submitted["status"] == "queued"
            assert submitted["stage_id"] == "meaning_exploration" and submitted["from_date"] == "2026-11-20"
            assert (await api.get(job_path)).json()["jobs"] == [submitted]
            assert "file_path" not in submitted and "requested_by" not in submitted
            launch.assert_called_once()

            job_url = f"{job_path}/{submitted['id']}"
            ticket_url = f"{job_url}/download-ticket"
            assert (await api.get(f"/api/v1/courses/{second_own.id}/export-jobs/{submitted['id']}")).status_code == 404
            assert (await api.post(job_path, json={"profile": "artifacts"})).status_code == 409
            assert (await api.post(ticket_url)).status_code == 409
            assert (await api.get(f"/api/v1/admin/data/export-jobs/{submitted['id']}",
                                  headers={"X-Actor": "admin"})).status_code == 404

            job = await ExportJob.get(submitted["id"])
            archive_path = exports.export_path(job)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("data.csv", "group_id\nexample")
            job.status = "completed"
            job.filename = "class-export.zip"
            job.file_size = archive_path.stat().st_size
            await job.save()

            no_cookie_url = f"{job_url}/download"
            assert (await api.get(no_cookie_url)).status_code == 403
            assert (await api.post(ticket_url, headers={"X-Actor": "peer"})).status_code == 403
            ticket = await api.post(ticket_url)
            assert ticket.status_code == 200, ticket.text
            assert ticket.json()["download_url"] == no_cookie_url
            assert "HttpOnly" in ticket.headers["set-cookie"] and "Secure" in ticket.headers["set-cookie"]
            assert "SameSite=strict" in ticket.headers["set-cookie"]
            download = await api.get(no_cookie_url)
            assert download.status_code == 200 and download.content == Path(archive_path).read_bytes()
            assert int(download.headers["content-length"]) == job.file_size
            assert "attachment" in download.headers["content-disposition"]

            own.teacher_id = str(actors["peer"].id)
            await own.save()
            assert (await api.get(no_cookie_url)).status_code == 403
            assert (await api.get(job_url)).status_code == 403
            assert (await api.get(job_url, headers={"X-Actor": "peer"})).status_code == 404
            assert (await api.post(ticket_url, headers={"X-Actor": "peer"})).status_code == 404

            own.teacher_id = str(actors["owner"].id)
            await own.save()
            archive_path.unlink()
            assert (await api.get(job_url)).json()["status"] == "expired"
            assert (await api.get(no_cookie_url)).status_code == 409

            second_job = await api.post(job_path, json={"profile": "participation"})
            assert second_job.status_code == 202, second_job.text
            assert second_job.json()["project_ids"] == [str(active.id), str(archived.id)]
            await exports.recover_interrupted_teacher_exports()
            recovered = await api.get(f"{job_path}/{second_job.json()['id']}")
            assert recovered.json()["status"] == "failed"
            assert "重启" in recovered.json()["message"]
    finally:
        await mongo.drop_database(db.name)
        mongo.close()
