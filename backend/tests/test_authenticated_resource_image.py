"""The same stored image URL is protected in history, documents and chat previews."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_image_route_authorizes_before_opening_object(monkeypatch):
    from app.api.v1 import storage

    project = SimpleNamespace(owner_id="teacher", members=[{"user_id": "student"}], course_id="course")
    course = SimpleNamespace(teacher_id="teacher", students=["student", "classmate"])
    resource = SimpleNamespace(scope="project", project_id="first-group", course_id=None,
                               mime_type="image/png", file_key="test.png", filename="photo.png")
    monkeypatch.setattr(storage.Project, "get", AsyncMock(return_value=project))
    monkeypatch.setattr(storage.Course, "get", AsyncMock(return_value=course))
    info = AsyncMock(return_value=resource)
    monkeypatch.setattr(storage.Resource, "get", info)
    png = b"\x89PNG\r\n\x1a\nimage"
    read = Mock(return_value=png)
    monkeypatch.setattr(storage.storage_service, "get_file_prefix", read)
    object_ = Mock()
    object_.stream.return_value = iter([png])
    get_object = Mock(return_value=object_)
    monkeypatch.setattr(storage.storage_service, "client", SimpleNamespace(get_object=get_object))
    app = FastAPI()
    app.include_router(storage.router)
    path = "/storage/resources/0123456789abcdef01234567/view"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).status_code in (401, 403)
        info.assert_not_awaited()

        viewer = SimpleNamespace(id="classmate", role="student")
        app.dependency_overrides[storage.get_current_user] = lambda: viewer
        assert (await client.get(path)).status_code == 403
        read.assert_not_called()
        get_object.assert_not_called()

        viewer.id = "student"
        response = await client.get(path)
        assert response.status_code == 200 and response.content == png
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        object_.close.assert_called_once()
        object_.release_conn.assert_called_once()

        project.members.clear()
        assert (await client.get(path)).status_code == 403
        get_object.assert_called_once()

        resource.scope, resource.course_id = "course", "course"
        resource.mime_type = "application/pdf"
        assert (await client.get(path)).status_code == 415
        viewer.id = "outsider"
        assert (await client.get(path)).status_code == 403
