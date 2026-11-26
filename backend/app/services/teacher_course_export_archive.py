"""Collect explicitly group-visible data for the teacher export jobs."""

import csv
import io
import json
import re
import shutil
import tempfile
import zipfile
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from bson import ObjectId
from bs4 import BeautifulSoup

from app.core.datetime_utils import CHINA_TZ, ensure_aware_utc, utc_isoformat
from app.core.db.mongodb import mongodb
from app.core.schemas.learning_design import DEFAULT_STAGE_NAMES
from app.repositories.chat_log import ChatLog
from app.repositories.collaboration_snapshot import CollaborationSnapshot
from app.repositories.course import Course
from app.repositories.document import Document, DocumentVersion
from app.repositories.inquiry_snapshot import InquirySnapshot
from app.repositories.project import Project
from app.repositories.research_event import ResearchEvent
from app.repositories.resource import Resource
from app.repositories.task import Task
from app.repositories.task_submission_artifact import TaskSubmissionArtifact
from app.repositories.user import User
from app.repositories.wiki_item import WikiItem
from app.services.agents.multi_agent_stage_matrix import STAGE_ALIASES
from app.services.research_config_service import ResearchConfigService


STAGES = {
    **ResearchConfigService.LEGACY_STAGE_ID_MAP,
    **STAGE_ALIASES,
    "问题建构": "problem_construction",
    "summary": "application_solution",
    "reflection": "application_solution",
}
PRIVATE_FLAGS = ("teacher_help_request", "teacher_private_reply", "private")
SUBMITTED = ("submitted", "late_submitted", "auto_submitted")
COUNTS = {
    "discussion": "public_messages",
    "document_versions": "document_saves",
    "knowledge": "knowledge_items",
    "submissions": "task_submissions",
    "resources": "library_uploads",
}
GROUP_TABLES = {
    "participation": ("members", *COUNTS, "group_counts", "student_counts"),
    "artifacts": (
        "documents",
        "document_versions",
        "knowledge",
        "snapshots",
        "submissions",
        "submission_files",
        "resources",
    ),
    "research": (
        "members",
        "discussion",
        "documents",
        "knowledge",
        "snapshots",
        "submissions",
        "event_sequence",
        "stage_summary",
        "document_update_operations",
    ),
}
SCHEMAS = {
    "groups": "case_id project_id group_code group_name group_condition ai_scaffold_mode process_scaffold_mode template_key",
    "members": "case_id actor_id role display_name",
    "discussion": "case_id object_id actor_id actor_type occurred_at stage_id stage_label_zh experiment_version_id message_type event_type content",
    "documents": "case_id object_id actor_id occurred_at title text html reference_only",
    "document_versions": "case_id object_id document_id actor_id occurred_at version_number",
    "knowledge": "case_id object_id actor_id occurred_at stage_id stage_label_zh item_type title content",
    "snapshots": "case_id object_id actor_id occurred_at snapshot_type snapshot_version",
    "submissions": "case_id object_id actor_id occurred_at title release_id status note linked_object_ids reference_only",
    "submission_files": "case_id object_id task_id actor_id occurred_at filename mime_type size artifact_type archive_path",
    "resources": "case_id object_id actor_id occurred_at filename mime_type size archive_path",
    "event_sequence": "case_id sequence_index event_id event_source event_domain event_type actor_id actor_type occurred_at stage_id stage_label_zh experiment_version_id object_id",
    "document_update_operations": "case_id operation_id object_id actor_id occurred_at operation_type payload_size",
    "stage_summary": "case_id stage_id stage_label_zh events public_messages unique_student_actors",
    "group_counts": "case_id student_count " + " ".join(COUNTS.values()),
    "student_counts": "case_id actor_id display_name " + " ".join(COUNTS.values()),
    "file_errors": "case_id object_id filename reason",
}

# Only public research streams. Dialogue comes from ChatLog so it can be linked
# to real, visibility-checked messages instead of duplicate or private events.
EVENT_TYPES = {
    "stage_transition": (
        "learning_stage_enter",
        "learning_stage_transition",
        "group_leader_stage_change",
    ),
    "inquiry_structure": (
        "node_add",
        "node_content_commit",
        "node_type_update",
        "node_delete",
        "edge_add",
        "edge_relation_toggle",
        "edge_delete",
        "evidence_source_bind",
        "evidence_source_open",
    ),
    "shared_record": (
        "shared_record_content_commit",
        "shared_record_save",
        "shared_record_create",
        "shared_record_annotation_create",
        "shared_record_annotation_edit",
        "shared_record_annotation_reply",
        "shared_record_annotation_resolve",
        "shared_record_annotation_delete",
        "course_task_submit",
        "course_task_auto_submit",
    ),
    "wiki": ("wiki_item_created", "wiki_item_updated", "wiki_item_quoted"),
}
DOCUMENT_SCOPE = {"scope": {"$in": ["shared", None]}}
LIBRARY_SCOPE = {"source_type": {"$in": ["library", None]}}


def _object_ids(values):
    return sorted({ObjectId(value) for value in values if ObjectId.is_valid(value)})


async def _find(model, query, fields):
    return await _projected(model.get_motor_collection(), query, fields)


async def _projected(collection, query, fields):
    return await collection.find(query, {field: 1 for field in fields.split()}).to_list(
        None
    )


def _window(from_date, through_date):
    if from_date and through_date and from_date > through_date:
        raise ValueError("Invalid export date range")
    bounds = {}
    if from_date:
        bounds["$gte"] = datetime.combine(from_date, time.min, CHINA_TZ).astimezone(
            timezone.utc
        )
    if through_date and through_date != date.max:
        bounds["$lt"] = datetime.combine(
            through_date + timedelta(days=1), time.min, CHINA_TZ
        ).astimezone(timezone.utc)
    return bounds


def _dated(field, bounds):
    return {field: bounds} if bounds else {}


def _stage(value):
    return STAGES.get(value) if isinstance(value, str) else None


def _staged(field, stage_id):
    return (
        {
            field: {
                "$in": [alias for alias, stage in STAGES.items() if stage == stage_id]
            }
        }
        if stage_id
        else {}
    )


def _stage_fields(stage_id):
    return {"stage_id": stage_id, "stage_label_zh": DEFAULT_STAGE_NAMES.get(stage_id)}


def _public(prefix):
    return {
        **{f"{prefix}.{flag}": {"$ne": True} for flag in PRIVATE_FLAGS},
        f"{prefix}.visibility": {"$nin": ["private", "personal"]},
        f"{prefix}.scope": {"$nin": ["private", "personal"]},
    }


def _stable_id(course_id, kind, value):
    return f"{kind}-{uuid5(NAMESPACE_URL, f'aiscl:{course_id}:{kind}:{value}').hex}"


def _id(kind, value):
    return f"{kind}:{value}"


def _record(case, actor, kind, item, user_field, time_field):
    return {
        "case_id": case,
        "object_id": _id(kind, item["_id"]),
        "actor_id": actor(item.get(user_field)),
        "occurred_at": item.get(time_field),
    }


async def _discussion(scope, case, actor, actor_type, bounds, stage_id):
    rows = await _find(
        ChatLog,
        {
            **scope,
            **_public("metadata"),
            **_dated("created_at", bounds),
            **_staged("metadata.stage_id", stage_id),
            "metadata.public_reply": {"$ne": False},
            # Group chat and private tutors share the chat_attachment resource type.
            "message_type": {"$in": ["text", "ai"]},
        },
        "user_id created_at message_type content metadata.stage_id metadata.experiment_version_id",
    )
    discussion = []
    for item in rows:
        sender = item.get("user_id") or ""
        metadata = item.get("metadata") or {}
        auto_prompt = sender.startswith("auto_prompt:")
        is_ai = auto_prompt or sender == "ai_assistant" or item["message_type"] == "ai"
        discussion.append(
            {
                **_record(case, actor, "chat", item, "user_id", "created_at"),
                **_stage_fields(_stage(metadata.get("stage_id"))),
                "experiment_version_id": metadata.get("experiment_version_id"),
                "actor_type": "ai_assistant" if is_ai else actor_type(sender),
                "event_type": "auto_group_prompt_send"
                if auto_prompt
                else ("public_ai_message" if is_ai else "public_group_message"),
                "message_type": item["message_type"],
                "content": item.get("content"),
            }
        )
    return discussion


async def _documents(tables, scope, case, actor, bounds, profile):
    query = {**scope, **DOCUMENT_SCOPE}
    if profile == "participation" or bounds:
        shared_ids = [str(item["_id"]) for item in await _find(Document, query, "_id")]
    else:
        shared_ids = []

    if profile != "participation":
        rows = await _find(
            Document,
            {**query, **_dated("updated_at", bounds)},
            "title content last_modified_by updated_at",
        )
        for item in rows:
            html = item.get("content") or ""
            tables["documents"].append(
                {
                    **_record(
                        case, actor, "document", item, "last_modified_by", "updated_at"
                    ),
                    "title": item.get("title"),
                    "html": html,
                    "text": BeautifulSoup(html, "html.parser").get_text(
                        " ", strip=True
                    ),
                    "reference_only": False,
                }
            )
        if not bounds:
            shared_ids = [str(item["_id"]) for item in rows]

    if profile != "research" and shared_ids:
        versions = await _find(
            DocumentVersion,
            {"document_id": {"$in": shared_ids}, **_dated("created_at", bounds)},
            "document_id version_number created_by created_at",
        )
        exported = {row["object_id"] for row in tables.get("documents", ())}
        for version in versions:
            document_id = _id("document", version["document_id"])
            tables["document_versions"].append(
                {
                    **_record(
                        case, actor, "version", version, "created_by", "created_at"
                    ),
                    "document_id": document_id,
                    "version_number": version.get("version_number"),
                }
            )
            if profile == "artifacts" and document_id not in exported:
                tables["documents"].append(
                    {"case_id": case, "object_id": document_id, "reference_only": True}
                )
                exported.add(document_id)
    return shared_ids


async def _knowledge(scope, case, actor, bounds, stage_id, shared_ids, include_content):
    project_id = scope["project_id"]
    rows = await _find(
        WikiItem,
        {
            **scope,
            **_dated("updated_at", bounds),
            **_staged("stage_id", stage_id),
            "visibility": {"$in": ["project", "group", None]},
            "group_id": {"$in": [None, project_id, f"project:{project_id}"]},
            "$or": [
                {"source_type": {"$in": ["manual", "teacher_brief", "inquiry", None]}},
                {"source_type": "document", "source_id": {"$in": shared_ids}},
            ],
        },
        "created_by updated_at stage_id item_type title"
        + (" content" if include_content else ""),
    )
    return [
        {
            **_record(case, actor, "wiki", item, "created_by", "updated_at"),
            **_stage_fields(_stage(item.get("stage_id"))),
            **{field: item.get(field) for field in ("item_type", "title", "content")},
        }
        for item in rows
    ]


async def _snapshots(scope, case, actor, bounds):
    rows = []
    for model, kind, time_field, fields in (
        (
            InquirySnapshot,
            "inquiry",
            "created_at",
            "created_by snapshot_type snapshot_version",
        ),
        (CollaborationSnapshot, "whiteboard", "updated_at", "snapshot_data.type"),
    ):
        query = {**scope, **_dated(time_field, bounds)}
        if model is CollaborationSnapshot:
            query["snapshot_data.type"] = {"$in": ["whiteboard", "inquiry", None]}
        for item in await _find(model, query, f"{fields} {time_field}"):
            rows.append(
                {
                    **_record(case, actor, kind, item, "created_by", time_field),
                    "snapshot_type": item.get("snapshot_type")
                    or (item.get("snapshot_data") or {}).get("type")
                    or kind,
                    "snapshot_version": item.get("snapshot_version"),
                }
            )
    return rows


async def _submissions(data, tables, scope, case, actor, bounds, course_id, profile):
    query = {
        **scope,
        "source_type": "course_task_release",
        "submission_status": {"$in": list(SUBMITTED)},
    }
    fields = "title course_task_release_id submission_status submitted_at submitted_by"
    if profile == "artifacts":
        fields += " submission_note artifact_document_id artifact_document_ids artifact_wiki_item_ids artifact_inquiry_snapshot_id artifact_snapshot_id submission_artifact_ids"
    tasks = await _find(Task, {**query, **_dated("submitted_at", bounds)}, fields)
    by_id = {str(item["_id"]): item for item in tasks}
    if profile == "artifacts":
        files = await _find(
            TaskSubmissionArtifact,
            {
                **scope,
                "course_id": {"$in": [course_id, None]},
                **_dated("uploaded_at", bounds),
            },
            "task_id filename file_key mime_type size artifact_type uploaded_at uploaded_by",
        )
        missing_parents = _object_ids(
            item["task_id"] for item in files if item["task_id"] not in by_id
        )
        if missing_parents:
            parents = await _find(
                Task,
                {**query, "_id": {"$in": missing_parents}},
                "submission_artifact_ids",
            )
            by_id.update({str(item["_id"]): item for item in parents})
        for item in files:
            parent = by_id.get(item["task_id"])
            if not parent or str(item["_id"]) not in (
                parent.get("submission_artifact_ids") or []
            ):
                continue
            prefix = f"projects/{scope['project_id']}/task-artifacts/{item['task_id']}/"
            row = _file_row(data, case, actor, "submission_file", item, prefix)
            row.update(
                task_id=_id("task", item["task_id"]),
                artifact_type=item.get("artifact_type"),
            )
            tables["submission_files"].append(row)

    targets = {
        row["object_id"]
        for name in ("documents", "knowledge", "snapshots", "submission_files")
        for row in tables.get(name, ())
    }
    for item in tasks:
        refs = [
            *(
                _id("document", value)
                for value in [
                    item.get("artifact_document_id"),
                    *(item.get("artifact_document_ids") or []),
                ]
                if value
            ),
            *(_id("wiki", value) for value in item.get("artifact_wiki_item_ids") or []),
            *(
                _id("submission_file", value)
                for value in item.get("submission_artifact_ids") or []
            ),
        ]
        if snapshot := item.get("artifact_inquiry_snapshot_id") or item.get(
            "artifact_snapshot_id"
        ):
            refs.extend((_id("inquiry", snapshot), _id("whiteboard", snapshot)))
        tables["submissions"].append(
            {
                **_record(case, actor, "task", item, "submitted_by", "submitted_at"),
                "title": item.get("title"),
                "release_id": item.get("course_task_release_id"),
                "status": item.get("submission_status"),
                "note": item.get("submission_note"),
                "linked_object_ids": sorted(set(refs) & targets),
                "reference_only": False,
            }
        )
    task_ids = {row["object_id"] for row in tables["submissions"]}
    for task_id in sorted(
        {row["task_id"] for row in tables.get("submission_files", ())} - task_ids
    ):
        tables["submissions"].append(
            {"case_id": case, "object_id": task_id, "reference_only": True}
        )


def _file_row(data, case, actor, kind, item, prefix=None):
    row = {
        **_record(case, actor, kind, item, "uploaded_by", "uploaded_at"),
        **{field: item.get(field) for field in ("filename", "mime_type", "size")},
    }
    if prefix:
        key = item.get("file_key") or ""
        # Never follow a pointer into another group or to a different file namespace.
        suffix = key.removeprefix(prefix)
        valid = key.startswith(prefix) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", suffix))
        data["originals"].setdefault(case, []).append((row, key if valid else None))
    return row


async def _resources(data, scope, case, actor, bounds, prefix=None):
    rows = await _find(
        Resource,
        {**scope, **LIBRARY_SCOPE, **_dated("uploaded_at", bounds)},
        "uploaded_by uploaded_at filename mime_type size"
        + (" file_key" if prefix else ""),
    )
    return [_file_row(data, case, actor, "resource", item, prefix) for item in rows]


def _reference_documents(tables, case, references):
    existing = {row["object_id"] for row in tables["documents"]}
    for ref in sorted(references - existing):
        tables["documents"].append(
            {"case_id": case, "object_id": ref, "reference_only": True}
        )


async def _document_updates(tables, case, actor, bounds, shared_ids):
    if not shared_ids:
        return
    rows = await _projected(
        mongodb.get_database()["document_update_stream"],
        {
            "document_id": {"$in": shared_ids},
            "operation_type": {"$in": ["update", "sync-step-2"]},
            **_dated("timestamp", bounds),
        },
        "document_id user_id timestamp operation_type payload_size",
    )
    operations = [
        {
            "case_id": case,
            "operation_id": _id("document_update", item["_id"]),
            "object_id": _id("document", item["document_id"]),
            "actor_id": actor(item.get("user_id")),
            "occurred_at": item["timestamp"],
            "operation_type": item["operation_type"],
            "payload_size": item.get("payload_size"),
        }
        for item in rows
    ]
    tables["document_update_operations"] = operations
    _reference_documents(tables, case, {row["object_id"] for row in operations})


async def _research(tables, project_id, case, actor, bounds, stage_id, shared_ids):
    objects = {
        row["object_id"]
        for table in ("documents", "knowledge", "submissions")
        for row in tables[table]
    }
    objects.update(_id("document", value) for value in shared_ids)
    # For the streams that can touch personal documents require a known public
    # target at query time. The only projected payload fields are these IDs.
    branches = []
    for domain, kinds in EVENT_TYPES.items():
        if domain in {"shared_record", "wiki"}:
            for field, kind, event_types in (
                (
                    "document_id",
                    "document",
                    [
                        name
                        for name in kinds
                        if name.startswith("shared_record_")
                        or name == "wiki_item_quoted"
                    ],
                ),
                (
                    "task_id",
                    "task",
                    [name for name in kinds if name.startswith("course_task_")],
                ),
                (
                    "wiki_item_id",
                    "wiki",
                    [
                        name
                        for name in kinds
                        if name in {"wiki_item_created", "wiki_item_updated"}
                    ],
                ),
            ):
                ids = sorted(
                    value.split(":", 1)[1]
                    for value in objects
                    if value.startswith(kind + ":")
                )
                if ids and event_types:
                    branches.append(
                        {
                            "event_domain": domain,
                            "event_type": {"$in": event_types},
                            f"payload.{field}": {"$in": ids},
                        }
                    )
        else:
            branches.append(
                {"event_domain": domain, "event_type": {"$in": list(kinds)}}
            )
    rows = await _find(
        ResearchEvent,
        {
            "project_id": project_id,
            "group_id": {"$in": [None, project_id, f"project:{project_id}"]},
            **_public("payload"),
            **_dated("event_time", bounds),
            **_staged("stage_id", stage_id),
            "$or": branches,
        },
        "event_time event_type event_domain actor_type user_id stage_id experiment_version_id payload.document_id payload.wiki_item_id payload.task_id",
    )
    events = []
    for item in rows:
        event_type = item["event_type"]
        object_id = None
        for prefix, field, matches in (
            (
                "document",
                "document_id",
                event_type.startswith("shared_record_")
                or event_type == "wiki_item_quoted",
            ),
            ("task", "task_id", event_type.startswith("course_task_")),
            (
                "wiki",
                "wiki_item_id",
                event_type in {"wiki_item_created", "wiki_item_updated"},
            ),
        ):
            if matches:
                candidate = (item.get("payload") or {}).get(field)
                if isinstance(candidate, str) and _id(prefix, candidate) in objects:
                    object_id = _id(prefix, candidate)
                break
        events.append(
            {
                "case_id": case,
                "event_id": _id("research", item["_id"]),
                "event_source": "research_event",
                "event_domain": item["event_domain"],
                "event_type": event_type,
                "object_id": object_id,
                "actor_type": item["actor_type"],
                "actor_id": actor(item.get("user_id")),
                "experiment_version_id": item.get("experiment_version_id"),
                "occurred_at": item["event_time"],
                **_stage_fields(_stage(item.get("stage_id"))),
            }
        )
    for row in tables["discussion"]:
        events.append(
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"content", "message_type"}
                },
                "event_id": row["object_id"],
                "event_source": "chat_log",
                "event_domain": "dialogue",
            }
        )
    _reference_documents(
        tables,
        case,
        {
            row["object_id"]
            for row in events
            if (row["object_id"] or "").startswith("document:")
        },
    )
    events.sort(
        key=lambda row: (utc_isoformat(row["occurred_at"]) or "", row["event_id"])
    )
    for index, event in enumerate(events, 1):
        event["sequence_index"] = index
    tables["event_sequence"] = events
    stages = [stage_id] if stage_id else list(DEFAULT_STAGE_NAMES)
    if not stage_id and any(row["stage_id"] is None for row in events):
        stages.append(None)
    for stage in stages:
        selected = [row for row in events if row["stage_id"] == stage]
        tables["stage_summary"].append(
            {
                "case_id": case,
                **_stage_fields(stage),
                "events": len(selected),
                "public_messages": sum(
                    row["event_source"] == "chat_log" for row in selected
                ),
                "unique_student_actors": len(
                    {
                        row["actor_id"]
                        for row in selected
                        if row["actor_id"] and row["actor_type"] == "student"
                    }
                ),
            }
        )


def _participation(tables, case):
    counts = {
        field: Counter(row["actor_id"] for row in tables[table])
        for table, field in COUNTS.items()
    }
    members = [row for row in tables["members"] if row["role"] == "student"]
    tables["student_counts"] = [
        {
            **member,
            **{field: tally[member["actor_id"]] for field, tally in counts.items()},
        }
        for member in members
    ]
    tables["group_counts"] = [
        {
            "case_id": case,
            "student_count": len(members),
            **{field: len(tables[table]) for table, field in COUNTS.items()},
        }
    ]


async def collect_teacher_export(
    course: Course,
    *,
    project_ids: list[str],
    profile: str,
    from_date: date | None,
    through_date: date | None,
    stage_id: str | None,
) -> dict:
    """Read the creation-time group allowlist again, intersected with this course."""
    if profile not in GROUP_TABLES:
        raise ValueError("Unknown teacher export profile")
    selected_stage = _stage(stage_id) if profile == "research" else None
    if profile == "research" and stage_id is not None and selected_stage is None:
        raise ValueError("Unknown research stage")
    bounds = _window(from_date, through_date)
    course_id = str(course.id)
    data = {
        "course_id": course_id,
        "profile": profile,
        "groups": [],
        "tables": {},
        "originals": {},
        "course_resources": [],
        "filters": {
            "from_date": from_date,
            "through_date": through_date,
            "stage_id": selected_stage,
        },
    }
    if not (allowed_ids := _object_ids(project_ids)):
        return data

    conditions = (
        "group_condition",
        "ai_scaffold_mode",
        "process_scaffold_mode",
        "template_key",
    )
    projects = await _find(
        Project,
        {
            "_id": {"$in": allowed_ids},
            "course_id": course_id,
            "is_template": {"$ne": True},
        },
        "name group_code owner_id members.user_id"
        + (
            " " + " ".join(f"experiment_version.{field}" for field in conditions)
            if profile == "research"
            else ""
        ),
    )
    if not projects:
        return data

    students = set(course.students or [])

    def actor(user_id):
        if isinstance(user_id, str) and user_id:
            return _stable_id(course_id, "actor", user_id)
        return None

    def actor_type(user_id):
        if user_id in students:
            return "student"
        return "teacher" if user_id == course.teacher_id else "other"

    def member_ids(project):
        return {
            m["user_id"] for m in project.get("members", []) if m.get("user_id")
        } | {project.get("owner_id")}

    project_members = set().union(*(member_ids(project) for project in projects))
    names = {}
    if profile == "participation":
        roster = _object_ids(project_members)
        if roster:
            users = await _find(User, {"_id": {"$in": roster}}, "username role")
            for user in users:
                user_id = str(user["_id"])
                if user.get("role") == "student":
                    students.add(user_id)
                    names[user_id] = user.get("username")
                elif user.get("role"):
                    students.discard(user_id)

    for project in sorted(projects, key=lambda item: str(item["_id"])):
        project_id = str(project["_id"])
        case = _stable_id(course_id, "case", project_id)
        scope = {"project_id": project_id}
        tables = {table: [] for table in GROUP_TABLES[profile]}
        data["tables"][case] = tables
        data["groups"].append(
            {
                "case_id": case,
                "project_id": project_id,
                "group_code": project.get("group_code"),
                "group_name": project.get("name"),
                **{
                    field: (project.get("experiment_version") or {}).get(field)
                    for field in conditions
                },
            }
        )
        if "members" in tables:
            tables["members"] = [
                {
                    "case_id": case,
                    "actor_id": actor(user_id),
                    "role": "student",
                    "display_name": names.get(user_id),
                }
                for user_id in sorted(students & member_ids(project))
            ]
        if "discussion" in tables:
            tables["discussion"] = await _discussion(
                scope, case, actor, actor_type, bounds, selected_stage
            )
        shared_ids = await _documents(tables, scope, case, actor, bounds, profile)
        tables["knowledge"] = await _knowledge(
            scope,
            case,
            actor,
            bounds,
            selected_stage,
            shared_ids,
            profile != "participation",
        )
        if "snapshots" in tables:
            tables["snapshots"] = await _snapshots(scope, case, actor, bounds)
        await _submissions(data, tables, scope, case, actor, bounds, course_id, profile)
        if "resources" in tables:
            prefix = f"projects/{project_id}/files/" if profile == "artifacts" else None
            tables["resources"] = await _resources(
                data,
                {**scope, "scope": {"$in": ["project", None]}, "course_id": None},
                case,
                actor,
                bounds,
                prefix,
            )
        if profile == "research":
            await _research(
                tables, project_id, case, actor, bounds, selected_stage, shared_ids
            )
            if not selected_stage:
                await _document_updates(tables, case, actor, bounds, shared_ids)
        elif profile == "participation":
            _participation(tables, case)
    if profile == "artifacts":
        data["course_resources"] = await _resources(
            data,
            {"course_id": course_id, "project_id": None, "scope": "course"},
            None,
            actor,
            bounds,
            f"courses/{course_id}/files/",
        )
    return data


def _csv_cell(value):
    if isinstance(value, datetime):
        return utc_isoformat(value)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        if value.startswith("'") or re.match(
            r"^[\s\ufeff]*[=+\-@\t\r\n\uff1d\uff0b\uff0d\uff20]", value
        ):
            return "'" + value
    return value


def _write_csv(archive, path, table, rows):
    columns = SCHEMAS[table].split()
    with archive.open(path, "w", force_zip64=True) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            writer.writerows(
                [_csv_cell(row.get(column)) for column in columns] for row in rows
            )


def _ordered(rows):
    def key(row):
        timestamp = row.get("occurred_at")
        return (
            ensure_aware_utc(timestamp).isoformat() if timestamp else "",
            row.get("operation_id") or row.get("object_id", row.get("actor_id", "")),
        )

    return sorted(rows, key=key)


def _write_original(archive, path, key, size):
    if key is None:
        return "unavailable"
    from app.services.storage_service import storage_service

    # Stage one object on disk so a failed download cannot publish a partial ZIP entry.
    with tempfile.TemporaryFile() as source:
        try:
            written = storage_service.write_file_to(key, source)
        except Exception:
            return "unavailable"
        if written != size:
            return "size_mismatch"
        source.seek(0)
        with archive.open(path, "w", force_zip64=True) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
    return None


def write_teacher_export(
    path: str, course: Course, data: dict, *, profile: str, include_files: bool
) -> int:
    """Write the selected export, returning the number of unavailable originals."""
    if profile != data["profile"] or str(course.id) != data["course_id"]:
        raise ValueError(
            "Teacher export data does not match the requested course/profile"
        )
    include_files = bool(include_files and profile == "artifacts")
    errors = []
    paths = {}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if include_files:
            for case, entries in data["originals"].items():
                folder = f"groups/{case}" if case else "course"
                for row, key in sorted(
                    entries, key=lambda entry: entry[0]["object_id"]
                ):
                    filename = (
                        re.sub(r"[^\w. -]", "_", row.get("filename") or "")[:60].strip(
                            " ."
                        )
                        or "file"
                    )
                    object_id = row["object_id"]
                    target = f"{folder}/originals/{object_id.replace(':', '/')}/file-{filename}"
                    reason = _write_original(archive, target, key, row.get("size"))
                    if reason:
                        errors.append({**row, "reason": reason})
                    else:
                        paths[object_id] = target
        _write_csv(archive, "groups.csv", "groups", data["groups"])
        for case, tables in data["tables"].items():
            for table, rows in tables.items():
                if table not in {"event_sequence", "stage_summary"}:
                    rows = _ordered(rows)
                if table in {"resources", "submission_files"}:
                    rows = (
                        {**row, "archive_path": paths.get(row["object_id"])}
                        for row in rows
                    )
                _write_csv(archive, f"groups/{case}/{table}.csv", table, rows)
        if profile == "artifacts":
            rows = (
                {**row, "archive_path": paths.get(row["object_id"])}
                for row in _ordered(data["course_resources"])
            )
            _write_csv(archive, "course/resources.csv", "resources", rows)
            _write_csv(archive, "file_errors.csv", "file_errors", errors)
        manifest = {
            "schema_version": 1,
            "profile": profile,
            "course_id": data["course_id"],
            "course_name": course.name,
            "timezone": "Asia/Shanghai",
            "timestamps": "UTC ISO 8601",
            "filters": data["filters"],
            "include_files": include_files,
            "missing_files": len(errors),
            "group_count": len(data["groups"]),
            "stages": DEFAULT_STAGE_NAMES,
            "table_schemas": {
                table: SCHEMAS[table].split()
                for table in (
                    "groups",
                    *GROUP_TABLES[profile],
                    *(("file_errors",) if profile == "artifacts" else ()),
                )
            },
            "group_table_path": "groups/{case_id}/{table}.csv",
            "joins": "case_id joins groups; actor_id is stable per course; object_id is type-prefixed. "
            "Join event_sequence.object_id to an object table within the same case. "
            "document_versions.document_id and submission_files.task_id reference object_id.",
            "date_policy": "Inclusive Shanghai calendar dates. occurred_at is chat/event/version/snapshot creation "
            "(collaboration snapshots: update), resource/file upload, task submission, or current document/wiki update. "
            "Reference-only parent rows have no out-of-window contents. Research stage filters tagged "
            "events, discussion and knowledge; untagged artifact tables supply date-scoped context.",
            "csv_policy": "UTF-8 BOM. Text beginning with an apostrophe or a spreadsheet formula prefix "
            "(including after leading whitespace) has one apostrophe prepended. "
            "To reverse after parsing CSV, remove exactly one initial apostrophe from text cells that have one. "
            "Stable IDs are not changed. List cells contain JSON arrays.",
            "coverage": "Public group records only. No chat attachments or private AI/support records. "
            "Research includes allowlisted group events and public text/AI chat; blank stage means unassigned. "
            "Document versions are saved checkpoints, not realtime operations. Actor IDs are pseudonyms, "
            "not anonymization of public contributions. Counts cover exported records, not all platform activity.",
        }
        if profile == "research":
            manifest["document_update_operations"] = (
                "Persisted Yjs protocol updates (update and sync-step-2), not text, keystrokes, or semantic "
                "actions. Rows are scoped to currently shared documents and the timestamp window. "
                "operation_id is stable; object_id joins documents within the case. payload_size is the "
                "stored encoded update length. No update bytes, save-event substitutes, or stage inference. "
                "The table is empty with a stage filter and separate from event_sequence."
            )
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, default=date.isoformat),
        )
    return len(errors)
