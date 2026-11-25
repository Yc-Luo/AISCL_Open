"""Student dashboard shows only scoped, saved activity and non-evaluative advice."""

from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from uuid import uuid4

from beanie import init_beanie
from bson import ObjectId
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest

from app.core.datetime_utils import utc_isoformat
from app.services.student_learning_overview import _suggestions


def test_suggests_opening_the_actual_shared_document_and_nothing_for_empty_groups():
    activity = {"messagesSent": 0, "openIssues": 0, "evidenceEntries": 0, "recentDocuments": []}
    assert _suggestions(None, activity, False) == {"personal": None, "group": None}
    activity["recentDocuments"] = [{"id": "saved-document", "title": "Evidence notes"}]
    suggestion = _suggestions(None, activity, False)["group"]
    assert suggestion["target"] == {"kind": "document", "documentId": "saved-document"}
    assert "Evidence notes" in suggestion["basis"]
    assert _suggestions(None, activity, True) == {"personal": None, "group": None}


@pytest.mark.asyncio
async def test_overview_uses_auth_identity_and_real_scoped_records(monkeypatch):
    uri = os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set CHAT_TEST_MONGODB_URI to run MongoDB integration tests")

    from app.api.v1 import analytics
    from app.services import student_learning_overview as overview

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"learning_overview_test_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[analytics.Project])
        monkeypatch.setattr(overview.mongodb, "get_database", lambda: db)
        student, peer, outsider, teacher = [str(ObjectId()) for _ in range(4)]
        await db.users.insert_many([
            {"_id": ObjectId(user_id), "role": "teacher" if user_id == teacher else "student"}
            for user_id in (student, peer, outsider, teacher)
        ])
        project = await analytics.Project(
            name="My group", owner_id=teacher, course_id="same-course",
            members=[{"user_id": member, "role": "editor"} for member in (student, peer)],
        ).insert()
        project_id = str(project.id)
        other_group = str(ObjectId())
        now = datetime.now(timezone.utc).replace(microsecond=0)
        recent = now - timedelta(hours=1)
        older = now - timedelta(days=10)

        def chat(user_id, *, group=project_id, when=recent, kind="text", mentions=None, **metadata):
            return {"project_id": group, "user_id": user_id, "created_at": when, "message_type": kind,
                    "metadata": metadata, "mentions": mentions or [], "content": "never return message text"}

        await db.chat_logs.insert_many([
            chat(student, mentions=[student]), chat(peer, kind="file", mentions=[student, peer]),
            chat(peer, when=older), chat(teacher, mentions=[student]), chat(outsider, mentions=[student]),
            chat(peer, group=other_group, mentions=[student]), chat("ai_assistant", kind="ai"),
            chat(peer, teacher_help_request=True, mentions=[student]),
            chat(peer, teacher_private_reply=True, mentions=[student]),
            chat(student, when=now + timedelta(days=1)),
        ])

        def document(title, **overrides):
            return {"_id": ObjectId(), "project_id": project_id, "scope": "shared",
                    "title": title, "updated_at": recent, **overrides}

        shared = document("Current shared draft")
        private = document("Private draft", scope="personal")
        foreign = document("Other group draft", project_id=other_group)
        archived = document("Archived draft", is_archived=True)
        old_shared = document("Earlier shared draft", updated_at=older)
        await db.documents.insert_many([shared, private, foreign, archived, old_shared])
        await db.document_versions.insert_many([
            {"document_id": str(row["_id"]), "created_by": student, "created_at": recent}
            for row in (shared, private, foreign, archived)
        ] + [{"document_id": str(shared["_id"]), "created_by": peer, "created_at": recent}])

        def wiki(kind, user_id=student, **overrides):
            return {"project_id": project_id, "item_type": kind, "created_by": user_id,
                    "created_at": recent, "updated_at": recent, "source_type": "manual", **overrides}

        await db.wiki_items.insert_many([
            wiki("evidence"), wiki("controversy", peer, confidence_level="working"),
            wiki("controversy", peer, confidence_level="verified"),
            {**wiki("evidence"), "source_type": "system"},
            {**wiki("evidence"), "project_id": other_group},
        ])

        app = FastAPI()
        app.include_router(analytics.router)
        viewer = SimpleNamespace(id=student, role="student", class_id="same-course")
        app.dependency_overrides[analytics.get_current_user] = lambda: viewer
        url = f"/analytics/projects/{project_id}/student-process-dashboard"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(url, params={"days": 7, "user_id": peer})
            assert response.status_code == 200, response.text
            data = response.json()
            assert set(data) == {"updatedAt", "periodDays", "personal", "group", "suggestions"}
            assert data["updatedAt"].endswith("Z") and data["periodDays"] == 7
            assert data["personal"] == {
                "messagesSent": 1, "mentionsReceived": 1, "sharedDocumentSaves": 1, "knowledgeEntriesAdded": 1,
            }
            assert data["group"] == {
                "messagesSent": 2, "activeDiscussants": 2, "sharedDocumentsUpdated": 1,
                "evidenceEntries": 1, "openIssues": 1, "recentDocuments": [
                    {"id": str(shared["_id"]), "title": shared["title"], "updatedAt": utc_isoformat(recent)},
                ],
            }
            assert data["suggestions"]["personal"]["target"] == {"kind": "chat"}
            assert data["suggestions"]["group"]["target"] == {"kind": "wiki"}
            assert "never return message text" not in response.text

            viewer.id = peer
            peer_data = (await client.get(url, params={"days": 7})).json()
            assert peer_data["group"] == data["group"]
            assert peer_data["personal"]["knowledgeEntriesAdded"] == 2
            longer = (await client.get(url, params={"days": 30})).json()
            assert longer["personal"]["messagesSent"] == 2 and longer["group"]["sharedDocumentsUpdated"] == 2
            assert (await client.get(url, params={"days": 14})).status_code == 422

            viewer.id = outsider
            assert (await client.get(url)).status_code == 403
            viewer.id, viewer.role = teacher, "teacher"
            assert (await client.get(url)).json()["personal"] is None

            await db.projects.update_one({"_id": project.id}, {"$set": {"is_archived": True}})
            viewer.id, viewer.role = student, "student"
            archived_data = (await client.get(url)).json()
            assert archived_data["group"] == data["group"]
            assert archived_data["suggestions"] == {"personal": None, "group": None}
    finally:
        await mongo.drop_database(db.name)
        mongo.close()
