import io
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from beanie import init_beanie
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from minio import Minio
from motor.motor_asyncio import AsyncIOMotorClient

with patch.object(Minio, "bucket_exists", return_value=True):
    from app.api.v1 import ai

from app.repositories.ai_attachment import AIAttachment
from app.repositories.ai_conversation import AIConversation
from app.repositories.ai_message import AIMessage
from app.repositories.document import Document
from app.repositories.project import Project
from app.repositories.resource import Resource
from app.services.ai_service import AIService


@pytest.fixture
async def assistant_db():
    uri = os.getenv("COURSE_TEST_MONGODB_URI") or os.getenv("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Personal AI integration tests require test MongoDB")
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    database = mongo[f"personal_ai_test_{uuid4().hex}"]
    try:
        await init_beanie(database=database, document_models=[AIAttachment, AIConversation, AIMessage, Document, Project, Resource])
        yield database
    finally:
        await mongo.drop_database(database.name)
        mongo.close()


async def test_tutor_uses_current_activity_and_keeps_conversations_and_uploads_private(assistant_db, monkeypatch):
    group = await Project(
        name="Physics inquiry", owner_id="teacher",
        members=[{"user_id": "learner"}, {"user_id": "peer"}],
        experiment_version={"ai_scaffold_mode": "single_agent", "current_stage": "meaning_exploration"},
    ).insert()
    other = await Project(name="Other group", owner_id="learner").insert()
    own_doc = await Document(project_id=str(group.id), title="Owned notes", scope="personal",
                             owner_id="learner", last_modified_by="learner", content="NOT_AUTO_READ").insert()
    peer_doc = await Document(project_id=str(group.id), title="PEER_PRIVATE_TITLE", scope="personal",
                              owner_id="peer", last_modified_by="peer").insert()
    actor = SimpleNamespace(id="learner", role="student")
    app = FastAPI()
    app.include_router(ai.router, prefix="/api/v1")
    app.dependency_overrides[ai.get_current_user] = lambda: actor
    inputs = []
    stored = {}
    secret = b"PRIVATE_FIXTURE_EVIDENCE"

    class ObjectResponse(io.BytesIO):
        def stream(self, size):
            while chunk := self.read(size):
                yield chunk

        def release_conn(self):
            pass

    def upload(key, stream, size, mime_type, *, private_ai):
        assert private_ai and mime_type == "text/plain"
        stored[key] = stream.read(size)

    @asynccontextmanager
    async def model(*args):
        yield None

    async def fake_stream(llm, messages):
        text = "\n".join(str(message.content) for message in messages)
        inputs.append(text)
        yield "回复见 " + secret.decode()

    monkeypatch.setattr(ai.storage_service, "upload_file_object", upload)
    monkeypatch.setattr(ai.storage_service, "open_private_ai_object",
                        lambda key, *, length=0: ObjectResponse(stored[key][:length] if length else stored[key]))
    monkeypatch.setattr(ai.ai_service, "get_role", AsyncMock(return_value=None))
    monkeypatch.setattr(ai.ai_service, "get_default_role", AsyncMock(return_value=None))
    monkeypatch.setattr(ai, "_course_models_for_request", AsyncMock(return_value={}))
    monkeypatch.setattr(ai.group_memory_service, "get_project_task_context", AsyncMock(return_value="课题说明"))
    monkeypatch.setattr(AIService, "_dialogue_model", staticmethod(model))
    monkeypatch.setattr("app.services.ai_service.guarded_astream", fake_stream)

    base = "/api/v1/ai"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        attachment = await client.post(f"{base}/attachments", data={"project_id": str(group.id)},
                                       files={"file": ("note.txt", secret, "text/plain")})
        assert attachment.status_code == 201, attachment.text
        file_info = attachment.json()
        file_url = f"{base}/attachments/{file_info['id']}"
        assert (await client.get(file_url)).content == secret
        assert await Resource.find_all().count() == 0
        bad = await client.post(f"{base}/attachments", data={"project_id": str(group.id)},
                                files={"file": ("unsupported.html", b"<p>file</p>", "text/html")})
        assert bad.status_code == 415

        payload = {
            "project_id": str(group.id), "message": "当前活动？", "role_id": "default-tutor",
            "context_source": "floating_ai", "active_tab": "document",
            "selected_document_id": str(own_doc.id), "attachment_ids": [file_info["id"]],
        }
        assert (await client.post(f"{base}/chat/stream", json={**payload, "project_id": str(other.id)})).status_code == 404
        first = await client.post(f"{base}/chat/stream", json=payload)
        assert first.status_code == 200 and "event: done" in first.text, first.text
        metadata = json.loads(first.text.split("event: meta\ndata: ", 1)[1].split("\n\n", 1)[0])
        conversation_id = metadata["conversation_id"]
        assert "Owned notes" in inputs[0] and "Physics inquiry" in inputs[0]
        assert "meaning_exploration" in inputs[0] and secret.decode() in inputs[0]
        assert "NOT_AUTO_READ" not in inputs[0]
        second = await client.post(f"{base}/chat/stream", json={
            **payload, "conversation_id": conversation_id, "message": "下一步是什么？",
            "selected_document_id": str(peer_doc.id), "attachment_ids": [],
        })
        assert "event: done" in second.text
        assert "PEER_PRIVATE_TITLE" not in inputs[1] and "回复见" in inputs[1]

        list_url = f"{base}/conversations/{group.id}"
        messages_url = f"{base}/conversations/{conversation_id}/messages"
        history = (await client.get(list_url)).json()
        assert history["total"] == 1 and "当前活动" in history["conversations"][0]["title"]
        saved = (await client.get(messages_url)).json()["messages"]
        assert len(saved) == 4 and saved[0]["attachments"] == [file_info]
        assert "回复见" in saved[-1]["content"] and saved[-1]["private_context"]
        rows = await AIMessage.find({"conversation_id": conversation_id, "role": "assistant"}).to_list()
        assert all(secret.decode() not in row.content for row in rows)

        for role, user_id in [("student", "peer"), ("teacher", "teacher")]:
            actor.role, actor.id = role, user_id
            assert (await client.get(list_url)).json()["total"] == 0
            assert (await client.get(messages_url)).status_code == 404
            assert (await client.get(file_url)).status_code == 404
            assert (await client.delete(f"{base}/conversations/{conversation_id}")).status_code == 404

        actor.role, actor.id = "student", "learner"
        group.members = [{"user_id": "peer"}]
        await group.save()
        assert (await client.get(messages_url)).status_code == 403
        assert (await client.get(file_url)).status_code == 403
