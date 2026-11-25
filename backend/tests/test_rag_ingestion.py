"""Resource ingestion tests with no live parser, storage, or vector providers."""

import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import BackgroundTasks

from app.api.v1 import storage
from app.core.config import settings
from app.repositories.resource import Resource
from app.services.document_parse_service import document_parse_service
from app.services.embedding_service import EmbeddingService, EmbeddingRuntimeConfig, embedding_service
from app.services.rag_service import rag_service


@pytest.fixture
def resource(monkeypatch):
    monkeypatch.setattr(Resource, "get_motor_collection", classmethod(lambda cls: None))
    return Resource(
        project_id="project-1",
        filename="report.pdf",
        file_key="projects/project-1/files/report",
        url="https://storage.invalid/report",
        size=3,
        mime_type="application/pdf",
        uploaded_by="user-1",
    )


@pytest.fixture
def ingestion(monkeypatch, resource):
    snapshots = []

    async def save(self):
        snapshots.append(self.model_dump())

    monkeypatch.setattr(Resource, "get", AsyncMock(return_value=resource))
    monkeypatch.setattr(Resource, "save", save)
    monkeypatch.setattr(settings, "RAG_VECTOR_ENABLED", True)
    monkeypatch.setattr(settings, "QDRANT_URL", "https://qdrant.invalid")
    monkeypatch.setattr(embedding_service, "is_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(embedding_service, "embed_texts", AsyncMock(return_value=[[0.1, 0.2]]))
    monkeypatch.setattr(document_parse_service, "get_mineru_config", AsyncMock(return_value=SimpleNamespace(enabled=True)))
    upload = Mock()
    monkeypatch.setattr(storage.storage_service, "upload_file_bytes", upload)
    return snapshots, upload


@pytest.fixture
def mock_http(monkeypatch):
    client = httpx.AsyncClient

    def install(handler):
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
            transport=httpx.MockTransport(handler), **kwargs
        ))

    install(lambda request: pytest.fail(f"Unexpected HTTP request: {request.url}"))
    return install


@pytest.mark.parametrize("http_status, qdrant_status", [(200, "completed"), (200, "acknowledged"), (503, None)])
async def test_mineru_persists_artifacts_before_qdrant_confirmation(
    monkeypatch, resource, ingestion, mock_http, http_status, qdrant_status
):
    snapshots, upload = ingestion
    parser = AsyncMock(return_value=("Body text\n", b"zip", b"[]"))
    monkeypatch.setattr(document_parse_service, "_parse_with_mineru", parser)

    async def embed(chunks):
        assert chunks == ["Body text"]
        assert snapshots[-1]["parse_status"] == "embedding"
        assert snapshots[-1]["parsed_at"] is not None
        assert upload.call_count == 3
        return [[0.1, 0.2]]

    monkeypatch.setattr(embedding_service, "embed_texts", embed)

    def qdrant(request):
        assert snapshots[-1]["parse_status"] == "indexing"
        assert snapshots[-1]["indexed_at"] is None
        if request.method == "GET":
            return httpx.Response(200, json={"result": {"config": {"params": {"vectors": {"size": 2}}}}})
        assert request.method == "PUT" and request.url.path.endswith("/points")
        assert request.url.params["wait"] == "true"
        points = json.loads(request.content)["points"]
        assert len(points) == 1 and points[0]["payload"]["content"] == "Body text"
        return httpx.Response(http_status, json={"status": "ok", "result": {"status": qdrant_status}})

    mock_http(qdrant)
    succeeded = qdrant_status == "completed"
    assert await document_parse_service.process_resource("id", fallback_text="local text") is succeeded
    assert [item["parse_status"] for item in snapshots] == [
        "parsing", "embedding", "indexing", "indexed" if succeeded else "failed"
    ]
    assert (resource.indexed_at is not None) is succeeded
    assert resource.parse_error == (None if succeeded else "Vector indexing failed")
    assert resource.parsed_zip_key.endswith("mineru_result.zip")
    assert resource.parsed_markdown_key.endswith("full.md")
    assert resource.parsed_content_key.endswith("content_list.json")
    upload.assert_any_call(resource.parsed_markdown_key, b"Body text\n", "text/markdown")
    assert resource.file_key == "projects/project-1/files/report"


@pytest.mark.parametrize("markdown", [None, "", "![](images/figure.png)"])
async def test_mineru_without_text_keeps_returned_artifacts(monkeypatch, resource, ingestion, mock_http, markdown):
    archive_data = io.BytesIO()
    with zipfile.ZipFile(archive_data, "w") as archive:
        archive.writestr("nested/content_list.json", "[]")
        if markdown is not None:
            archive.writestr("nested/full.md", markdown)
    zip_bytes = archive_data.getvalue()
    text, content = document_parse_service._extract_mineru_zip(zip_bytes)
    monkeypatch.setattr(document_parse_service, "_parse_with_mineru", AsyncMock(return_value=(text, zip_bytes, content)))

    assert not await document_parse_service.process_resource("id")
    assert resource.parse_status == "no_text"
    assert resource.parsed_at is not None
    assert (resource.parsed_markdown_key is not None) is (markdown is not None)
    ingestion[1].assert_any_call(resource.parsed_zip_key, zip_bytes, "application/zip")
    ingestion[1].assert_any_call(resource.parsed_content_key, b"[]", "application/json")
    embedding_service.embed_texts.assert_not_awaited()


@pytest.mark.parametrize("vectors", [[], [[0.1]], [[0.1], []], [[0.1], [0.2], [0.3]]])
async def test_rag_rejects_incomplete_or_excess_embedding_batches(resource, ingestion, mock_http, vectors):
    embedding_service.embed_texts.return_value = vectors
    assert not await rag_service.process_resource("id", "one two", chunk_size=4, overlap=0)
    assert [item["parse_status"] for item in ingestion[0]] == ["embedding", "failed"]
    assert resource.parse_error == "Embedding did not return one vector per chunk"


@pytest.mark.parametrize("provider, method", [
    ("minimax", "_embed_with_minimax"), ("openai", "_embed_with_openai_compatible")
])
async def test_embedding_service_checks_provider_vector_count(monkeypatch, mock_http, provider, method):
    cfg = EmbeddingRuntimeConfig(provider=provider, api_key="test", base_url="", model="", embedding_type="")
    monkeypatch.setattr(settings, "RAG_VECTOR_ENABLED", True)
    monkeypatch.setattr(EmbeddingService, "_resolve_config", AsyncMock(return_value=cfg))
    monkeypatch.setattr(EmbeddingService, method, AsyncMock(return_value=[[0.1]]))
    assert await EmbeddingService.embed_texts(["one", "two"]) == []


async def test_embedding_failure_is_not_a_mineru_failure(monkeypatch, resource, ingestion, mock_http):
    monkeypatch.setattr(document_parse_service, "_parse_with_mineru", AsyncMock(return_value=("Body text", b"zip", None)))
    embedding_service.embed_texts.side_effect = RuntimeError("provider unavailable")
    assert not await document_parse_service.process_resource("id", fallback_text="Local text")
    embedding_service.embed_texts.assert_awaited_once_with(["Body text"])
    assert resource.parse_status == "failed"
    assert resource.parse_error == "Embedding failed"
    assert resource.parse_provider == "mineru"
    assert resource.parsed_zip_key.endswith("mineru_result.zip")
    assert resource.indexed_at is None


async def test_disabled_vectors_do_not_call_embedder(monkeypatch, resource, ingestion, mock_http):
    monkeypatch.setattr(settings, "RAG_VECTOR_ENABLED", False)
    assert not await rag_service.process_resource("id", "Body text")
    assert resource.parse_status == "disabled"
    embedding_service.embed_texts.assert_not_awaited()


async def test_mineru_failure_uses_real_local_text(monkeypatch, resource, ingestion, mock_http):
    monkeypatch.setattr(document_parse_service, "_parse_with_mineru", AsyncMock(side_effect=ValueError("parse failed")))
    mock_http(lambda request: httpx.Response(200, json={"status": "ok", "result": {"status": "completed"}}))
    assert await document_parse_service.process_resource("id", fallback_text="Actual local text")
    embedding_service.embed_texts.assert_awaited_once_with(["Actual local text"])
    assert resource.parse_status == "indexed"
    assert resource.parse_provider == "local"


async def test_upload_task_does_not_index_metadata_only(monkeypatch, resource, ingestion, mock_http):
    monkeypatch.setattr(storage.Course, "get", AsyncMock(return_value=object()))
    monkeypatch.setattr(storage, "ensure_course_manage_access", AsyncMock())
    monkeypatch.setattr(storage, "_ensure_storage_quota_available", AsyncMock())
    monkeypatch.setattr(storage.storage_service, "get_file_size", Mock(return_value=3))
    monkeypatch.setattr(storage.storage_service, "get_file_bytes", Mock(return_value=b" \n "))
    monkeypatch.setattr(storage.storage_service, "generate_presigned_get_url", Mock(return_value=resource.url))

    async def insert(self):
        Resource.get.return_value = self

    monkeypatch.setattr(Resource, "insert", insert)
    tasks = BackgroundTasks()
    response = await storage._create_resource_from_uploaded_object(
        storage.CreateResourceRequest(
            filename="blank.txt", file_key="courses/course-1/files/blank", mime_type="text/plain",
            size=3, scope="course", course_id="course-1",
        ),
        tasks,
        SimpleNamespace(id="user-1"),
    )
    assert response["parse_status"] == "pending"
    await tasks()
    assert storage._serialize_resource(Resource.get.return_value)["parse_status"] == "no_text"
    embedding_service.embed_texts.assert_not_awaited()


def test_old_indexed_record_reads_as_unverified(resource):
    legacy_row = resource.model_dump()
    legacy_row.update(parse_status="indexed", parsed_zip_key="old-artifacts.zip", parsed_at=resource.uploaded_at)
    del legacy_row["indexed_at"]
    legacy = Resource.model_validate(legacy_row)
    assert storage._serialize_resource(legacy)["parse_status"] == "unverified"
    assert legacy.parsed_zip_key == "old-artifacts.zip"
    verified = Resource.model_validate({**legacy_row, "indexed_at": resource.uploaded_at})
    assert storage._serialize_resource(verified)["parse_status"] == "indexed"
