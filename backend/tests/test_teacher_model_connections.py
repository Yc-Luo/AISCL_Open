"""Regression coverage for credential ownership, redaction, and explicit model selection."""

import os
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from beanie import init_beanie
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from minio import Minio
from motor.motor_asyncio import AsyncIOMotorClient

with patch.object(Minio, "bucket_exists", return_value=True):
    from app.api.v1 import ai, teacher_model_connections as routes

from app.core import llm_config
from app.core.error_handlers import setup_error_handlers
from app.core.security import setup_rate_limiting
from app.core.schemas.ai import AIChatRequest
from app.repositories.teacher_model_connection import TeacherModelConnection
from app.services.agents import agent_service as agent_module
from app.services.teacher_model_service import load_teacher_model_definition


FAKE_KEY = "test-credential-never-published"


@pytest.fixture
async def connection_api():
    uri = os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set CHAT_TEST_MONGODB_URI to run private model storage tests")
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"teacher_model_test_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[TeacherModelConnection])
        app = FastAPI()
        setup_error_handlers(app)
        setup_rate_limiting(app)
        app.include_router(routes.router, prefix="/api/v1")
        owner = SimpleNamespace(id="teacher-a", role="teacher", is_active=True, is_banned=False)
        app.dependency_overrides[routes.get_current_user] = lambda: owner
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, owner, db
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


@pytest.mark.asyncio
async def test_private_connection_lifecycle_and_redaction(connection_api, caplog):
    client, owner, db = connection_api
    url = "/api/v1/teacher-model-connections"
    data = {"name": "Private teacher model", "provider": "deepseek", "model": "test-model", "api_key": FAKE_KEY}
    result = await client.post(url, json=data)
    assert result.status_code == 201, result.text
    saved = result.json()
    assert saved["base_url"] == "https://api.deepseek.com"
    assert saved["has_api_key"] is True
    assert FAKE_KEY not in result.text
    raw = await db.teacher_model_connections.find_one({"owner_id": owner.id})
    assert raw["encrypted_api_key"] and FAKE_KEY not in str(raw)
    item = f"{url}/{saved['id']}"
    assert (await load_teacher_model_definition(owner.id, saved["id"]))["api_key"] == FAKE_KEY

    owner.id = "teacher-b"
    assert (await client.get(url)).json() == {"connections": []}
    assert (await client.put(item, json=data)).status_code == 404
    assert (await client.delete(item)).status_code == 404
    assert await load_teacher_model_definition(owner.id, saved["id"]) is None
    owner.role = "student"
    assert (await client.get(url)).status_code == 403
    owner.role, owner.id = "teacher", "teacher-a"

    invalid = await client.post(url, json={**data, "provider": "invalid"})
    assert invalid.status_code == 422 and FAKE_KEY not in invalid.text
    assert FAKE_KEY not in caplog.text
    unsafe = await client.post(url, json={**data, "provider": "custom", "base_url": "https://127.0.0.1/v1"})
    assert unsafe.status_code == 422
    assert (await client.put(item, json={**data, "model": "different-model"})).status_code == 409
    updated = await client.put(item, json={**data, "name": "Renamed", "api_key": "rotated-fake-key"})
    assert updated.status_code == 200 and updated.json()["name"] == "Renamed"
    assert (await load_teacher_model_definition(owner.id, saved["id"]))["api_key"] == "rotated-fake-key"

    @asynccontextmanager
    async def offline_model(**options):
        assert options["owner_id"] == owner.id and options["model_id"] == saved["id"]
        yield object()

    with patch.object(routes, "course_dialogue_llm", offline_model), patch.object(
        routes, "guarded_ainvoke", AsyncMock(side_effect=ValueError("rotated-fake-key")),
    ):
        test = await client.post(f"{item}/test")
    assert test.status_code == 200 and test.json()["success"] is False
    assert "rotated-fake-key" not in test.text and "rotated-fake-key" not in caplog.text

    assert (await client.delete(item)).status_code == 204
    assert await load_teacher_model_definition(owner.id, saved["id"]) is None


@pytest.mark.asyncio
async def test_model_client_acquires_only_the_named_private_connection():
    private = {"id": "teacher-provider-model", "api_key": FAKE_KEY, "provider": "custom",
               "base_url": "https://models.mydomain.com/v1"}

    @asynccontextmanager
    async def checked_transport(base_url, *, timeout):
        assert base_url == private["base_url"]
        yield "guarded-client"

    with patch("app.services.teacher_model_service.load_teacher_model_definition", new=AsyncMock(return_value=private)) as load, patch(
        "app.core.teacher_model_transport.create_teacher_async_http_client", new=checked_transport,
    ), patch.object(llm_config, "_CompatibleChatOpenAI", return_value=object()) as model, patch.object(
        llm_config, "get_llm", new=AsyncMock(),
    ) as platform:
        async with llm_config.course_dialogue_llm(model_id="teacher:known", owner_id="owner", temperature=0.7):
            pass
        load.assert_awaited_once_with("owner", "teacher:known")
        assert model.call_args.kwargs["openai_api_key"] == FAKE_KEY
        assert model.call_args.kwargs["model"] == private["id"]
        assert model.call_args.kwargs["http_async_client"] == "guarded-client"
        load.return_value = None
        with pytest.raises(llm_config.DedicatedModelUnavailable):
            async with llm_config.course_dialogue_llm(model_id="teacher:other", owner_id="owner", temperature=0.7):
                raise AssertionError("Do not substitute platform credentials for an unavailable private connection")
        platform.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,url,field", [
    ("openai", "https://api.openai.com/v1", "max_completion_tokens"),
    ("deepseek", "https://api.deepseek.com", "max_tokens"),
    ("siliconflow", "https://api.siliconflow.cn/v1", "max_tokens"),
])
async def test_real_sdk_uses_correct_endpoint_key_and_token_field(provider, url, field):
    def reply(request):
        assert str(request.url) == f"{url}/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        assert body[field] == 32
        if provider == "openai":
            assert "temperature" not in body
        return Response(200, json={
            "id": "mock-chat", "object": "chat.completion", "created": 0, "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
        })

    def client(base_url, *, timeout):
        return AsyncClient(base_url=base_url, timeout=timeout, transport=MockTransport(reply))

    connection = {"id": "test-model", "api_key": FAKE_KEY, "provider": provider, "base_url": url}
    with patch("app.services.teacher_model_service.load_teacher_model_definition", new=AsyncMock(return_value=connection)), patch(
        "app.core.teacher_model_transport.create_teacher_async_http_client", new=client,
    ):
        async with llm_config.course_dialogue_llm(
            model_id="teacher:test", owner_id="teacher", temperature=0.25, max_tokens=32,
        ) as model:
            assert (await model.ainvoke("Hi")).content == "OK"


@pytest.mark.asyncio
async def test_role_override_default_and_platform_mode_order():
    context = {"course_models": {
        "course_owner_id": "owner-a", "default_model_id": "teacher:base",
        "role_model_ids": {"custom_source": "teacher:review"},
    }}
    acquired = []

    @asynccontextmanager
    async def course_model(**options):
        acquired.append((options["owner_id"], options["model_id"]))
        yield object()

    service = agent_module.AgentService()
    with patch.object(agent_module, "course_dialogue_llm", course_model), patch.object(
        agent_module, "resolve_role_model_id", AsyncMock(return_value="legacy"),
    ), patch.object(agent_module, "get_llm", AsyncMock()) as legacy:
        async with service._reply_model(context=context, agent_name="custom_source", model_role="evidence_researcher", temperature=0.7):
            pass
        async with service._reply_model(context=context, model_role="answer_synthesizer", temperature=0.25):
            pass
        legacy.assert_not_awaited()
        async with service._reply_model(context={}, model_role="answer_synthesizer", temperature=0.25):
            pass
        legacy.assert_awaited_once_with(temperature=0.25, model_id="legacy")
    assert acquired == [("owner-a", "teacher:review"), ("owner-a", "teacher:base")]


@pytest.mark.asyncio
async def test_direct_student_reply_uses_class_access_not_student_model_access():
    project = SimpleNamespace(id="group", experiment_version={"ai_scaffold_mode": "single_agent"})
    assignments = {"default_model_id": "teacher:base", "course_owner_id": "owning-teacher"}
    with patch.object(ai.Project, "get", AsyncMock(return_value=project)), patch.object(
        ai, "ensure_project_access", AsyncMock(),
    ), patch.object(
        ai, "resolve_project_model_assignments", AsyncMock(return_value=assignments),
    ) as resolve, patch.object(
        ai, "_build_direct_ai_context", AsyncMock(return_value=[]),
    ), patch.object(
        ai.ai_service, "raw_chat", AsyncMock(return_value={"conversation_id": "chat", "message": "answer"}),
    ) as chat:
        await ai.chat.__wrapped__(AIChatRequest(project_id="group", message="Hi"), None, SimpleNamespace(id="student"))
    resolve.assert_awaited_once_with(project, include_roles=False)
    assert chat.await_args.kwargs["model_id"] == "teacher:base"
    assert chat.await_args.kwargs["course_model_owner_id"] == "owning-teacher"
