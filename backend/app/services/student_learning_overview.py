"""Read-only learning overview built from saved objects, not research scores."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from beanie import PydanticObjectId

from app.core.datetime_utils import utc_isoformat
from app.core.db.mongodb import mongodb
from app.repositories.project import Project


def _suggestions(personal: dict | None, group: dict, archived: bool) -> dict:
    personal_tip = group_tip = None
    if not archived:
        if personal and personal["mentionsReceived"]:
            personal_tip = {
                "basis": f"这段时间有 {personal['mentionsReceived']} 条小组消息提到了你。",
                "action": "回看与你有关的讨论，看看有没有可以补充的材料或想法。",
                "target": {"kind": "chat"},
            }
        elif personal and group["messagesSent"] and not personal["messagesSent"]:
            personal_tip = {
                "basis": "小组近期有讨论，暂未记录到你的群聊发言。",
                "action": "选一条你能补充的讨论，说说你的理解或还想问的问题。",
                "target": {"kind": "chat"},
            }

        if group["openIssues"]:
            group_tip = {
                "basis": f"知识沉淀中有 {group['openIssues']} 条近期更新、仍待核验的分歧记录。",
                "action": "挑一条分歧，比对各方用到的材料，记下还需要核验的地方。",
                "target": {"kind": "wiki"},
            }
        elif group["messagesSent"] and not group["evidenceEntries"]:
            group_tip = {
                "basis": "近期有小组讨论，知识沉淀中暂未见近期新增或更新的证据条目。",
                "action": "回顾一次讨论，把支持或挑战某个观点的材料连同出处记下来。",
                "target": {"kind": "chat"},
            }
        elif group["recentDocuments"]:
            document = group["recentDocuments"][0]
            group_tip = {
                "basis": f"共享文档「{document['title']}」近期有变动。",
                "action": "一起回看这份文档，标出已达成的共识和仍需补充的内容。",
                "target": {"kind": "document", "documentId": document["id"]},
            }
    return {"personal": personal_tip, "group": group_tip}


async def build_student_learning_overview(
    project: Project, *, student_id: str | None, days: int,
) -> dict[str, Any]:
    """Summarize the accessible group and only the signed-in student's activity."""
    db = mongodb.get_database()
    now = datetime.now(timezone.utc)
    window = {"$gte": now - timedelta(days=days), "$lte": now}
    project_id = str(project.id)

    member_ids = {member.get("user_id") for member in project.members}
    member_ids.add(project.owner_id)
    member_object_ids = [PydanticObjectId(value) for value in member_ids
                         if isinstance(value, str) and PydanticObjectId.is_valid(value)]
    student_members = await db["users"].find(
        {"_id": {"$in": member_object_ids}, "role": "student"},
        {"_id": 1},
    ).to_list(length=None)
    chat_query = {
        "project_id": project_id,
        "created_at": window,
        "user_id": {"$in": [str(row["_id"]) for row in student_members]},
        "message_type": {"$in": ["text", "file"]},
        "metadata.teacher_help_request": {"$ne": True},
        "metadata.teacher_private_reply": {"$ne": True},
    }
    shared_documents = {"project_id": project_id, "scope": {"$ne": "personal"}, "is_archived": {"$ne": True}}
    updated_documents = {**shared_documents, "updated_at": window}
    wiki_items = {"project_id": project_id, "source_type": {"$ne": "system"}}

    async def personal_counts() -> tuple[int, int]:
        if not student_id:
            return 0, 0
        # Only snapshots attached to a currently visible shared document count.
        versions = db["document_versions"].aggregate([
            {"$match": {"created_by": student_id, "created_at": window}},
            {"$project": {"doc_id": {"$convert": {"input": "$document_id", "to": "objectId", "onError": None}}}},
            {"$lookup": {"from": "documents", "localField": "doc_id", "foreignField": "_id", "as": "document"}},
            {"$match": {"document": {"$elemMatch": shared_documents}}},
            {"$count": "total"},
        ]).to_list(length=1)
        saves, entries = await asyncio.gather(versions, db["wiki_items"].count_documents({
            **wiki_items, "created_by": student_id, "created_at": window,
        }))
        return (saves[0]["total"] if saves else 0), entries

    mentions_expression = 0 if not student_id else {"$cond": [
        {"$in": [student_id, {"$cond": [{"$isArray": "$mentions"}, "$mentions", []]}]}, 1, 0,
    ]}
    chat, latest_docs, doc_count, evidence_count, issue_count, personal_records = await asyncio.gather(
        db["chat_logs"].aggregate([
            {"$match": chat_query},
            {"$group": {"_id": "$user_id", "total": {"$sum": 1}, "mentions": {"$sum": mentions_expression}}},
        ]).to_list(length=None),
        db["documents"].find(updated_documents, {"title": 1, "updated_at": 1})
        .sort([("updated_at", -1), ("_id", -1)]).limit(5).to_list(length=5),
        db["documents"].count_documents(updated_documents),
        db["wiki_items"].count_documents({**wiki_items, "updated_at": window, "item_type": "evidence"}),
        db["wiki_items"].count_documents({
            **wiki_items, "updated_at": window, "item_type": "controversy",
            "confidence_level": {"$in": ["unverified", "working"]},
        }),
        personal_counts(),
    )
    personal = None if not student_id else {
        "messagesSent": next((row["total"] for row in chat if row["_id"] == student_id), 0),
        "mentionsReceived": sum(row["mentions"] for row in chat if row["_id"] != student_id),
        "sharedDocumentSaves": personal_records[0],
        "knowledgeEntriesAdded": personal_records[1],
    }
    group = {
        "messagesSent": sum(row["total"] for row in chat),
        "activeDiscussants": len(chat),
        "sharedDocumentsUpdated": doc_count,
        "evidenceEntries": evidence_count,
        "openIssues": issue_count,
        "recentDocuments": [
            {"id": str(row["_id"]), "title": row["title"], "updatedAt": utc_isoformat(row["updated_at"])}
            for row in latest_docs
        ],
    }
    return {
        "updatedAt": utc_isoformat(now),
        "periodDays": days,
        "personal": personal,
        "group": group,
        "suggestions": _suggestions(personal, group, project.is_archived),
    }
