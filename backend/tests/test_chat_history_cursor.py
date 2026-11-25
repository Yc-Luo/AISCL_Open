"""Chat history contract tests against an isolated MongoDB test database."""

from datetime import datetime, timedelta
import os
from unittest.mock import AsyncMock
from uuid import uuid4

from beanie import init_beanie
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest


@pytest.mark.asyncio
async def test_chat_cursor_survives_ties_and_new_inserts(monkeypatch):
    uri = os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set CHAT_TEST_MONGODB_URI to run chat history integration tests")

    from app.api.v1 import chat

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"chat_cursor_test_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[chat.ChatLog])
        project = AsyncMock(return_value=object())
        permission = AsyncMock(return_value=True)
        monkeypatch.setattr(chat.Project, "get", project)
        monkeypatch.setattr(chat, "check_project_member_permission", permission)
        app = FastAPI()
        app.include_router(chat.router)
        app.dependency_overrides[chat.get_current_user] = lambda: object()
        time = datetime(2026, 1, 1)

        async def insert(number, *, created_at=time, project_id="group", **fields):
            message = chat.ChatLog(
                id=ObjectId(f"{number:024x}"), project_id=project_id,
                user_id="system", content=str(number), created_at=created_at, **fields,
            )
            await message.insert()
            return str(message.id)

        ids = [await insert(i) for i in range(1, 7)]
        await insert(7, metadata={"teacher_help_request": True})
        await insert(8, metadata={"teacher_private_reply": True})
        await insert(9, project_id="other")
        await insert(10, message_type="file")
        url = "/chat/projects/group/messages"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            filters = {"limit": 2, "message_type": "text"}
            response = await client.get(url, params={**filters, "skip": 2})
            assert response.status_code == 200, response.text
            legacy = response.json()
            assert set(legacy) == {"messages", "total"}
            assert [m["id"] for m in legacy["messages"]] == ids[2:4][::-1]
            assert legacy["total"] == 6

            first = (await client.get(url, params={**filters, "cursor": ""})).json()
            assert [m["id"] for m in first["messages"]] == ids[-2:][::-1]
            await insert(11, created_at=time + timedelta(seconds=1))
            await insert(12)
            cursor = first["next_cursor"]
            for expected in (ids[2:4][::-1], ids[:2][::-1]):
                response = await client.get(url, params={**filters, "cursor": cursor})
                assert response.status_code == 200
                page = response.json()
                assert [m["id"] for m in page["messages"]] == expected
                assert page["total"] == 8
                cursor = page["next_cursor"]
            assert cursor is None
            assert (await client.get(url, params={"cursor": "bad"})).status_code == 422
            assert (await client.get(url, params={"cursor": "", "skip": 1})).status_code == 422
            permission.return_value = False
            assert (await client.get(url, params={"cursor": ""})).status_code == 403
    finally:
        await mongo.drop_database(db.name)
        mongo.close()
