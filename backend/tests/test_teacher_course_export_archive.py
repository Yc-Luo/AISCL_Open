"""Builder tests with an isolated Mongo query/projection and storage boundary."""

import csv
import io
import json
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId

from app.services import teacher_course_export_archive as builder


DAY = date(2026, 6, 4)
START = datetime(2026, 6, 3, 16, tzinfo=timezone.utc)
END = START + timedelta(days=1)


def _value(row, key):
    for piece in key.split("."):
        row = row.get(piece) if isinstance(row, dict) else None
    return row


def _matches(row, query):
    for field, wanted in query.items():
        if field == "$or":
            if not any(_matches(row, branch) for branch in wanted):
                return False
            continue
        actual = _value(row, field)
        if not isinstance(wanted, dict):
            if actual != wanted:
                return False
            continue
        for op, value in wanted.items():
            if op == "$in" and actual not in value:
                return False
            if op == "$nin" and actual in value:
                return False
            if op == "$ne" and type(actual) is type(value) and actual == value:
                return False
            if op == "$gte" and (actual is None or actual < value):
                return False
            if op == "$lt" and (actual is None or actual >= value):
                return False
            assert op in {"$in", "$nin", "$ne", "$gte", "$lt"}
    return True


def _project(row, fields):
    result = {"_id": row["_id"]}
    for field in fields:
        key, _, child = field.partition(".")
        if key not in row:
            continue
        value = row[key]
        if not child:
            result[key] = value
        elif isinstance(value, list):
            result[key] = [{child: item[child]} for item in value if child in item]
        elif isinstance(value, dict) and child in value:
            result.setdefault(key, {})[child] = value[child]
    return result


@pytest.fixture
def store(monkeypatch):
    records = defaultdict(list)
    calls = []

    def collection(model):
        def find(query, fields):
            calls.append((model, query, fields))

            async def to_list(length):
                return [
                    _project(row, fields)
                    for row in records[model]
                    if _matches(row, query)
                ]

            return SimpleNamespace(to_list=to_list)

        return SimpleNamespace(find=find)

    for model in (
        builder.ChatLog,
        builder.CollaborationSnapshot,
        builder.Document,
        builder.DocumentVersion,
        builder.InquirySnapshot,
        builder.Project,
        builder.ResearchEvent,
        builder.Resource,
        builder.Task,
        builder.TaskSubmissionArtifact,
        builder.User,
        builder.WikiItem,
    ):
        monkeypatch.setattr(
            model, "get_motor_collection", lambda model=model: collection(model)
        )
    monkeypatch.setattr(
        builder.mongodb,
        "get_database",
        lambda: {
            "document_update_stream": collection("document_update_stream"),
        },
    )

    def add(model, **fields):
        row = {"_id": ObjectId(), **fields}
        records[model].append(row)
        return str(row["_id"])

    return SimpleNamespace(add=add, records=records, calls=calls)


@pytest.fixture
def classroom(store):
    student = store.add(
        builder.User, username="'=name", role="student", email="private@example.org"
    )
    idle = store.add(builder.User, username="Idle", role="student")
    course = SimpleNamespace(
        id=ObjectId(),
        name="Class",
        teacher_id=str(ObjectId()),
        students=[student, idle],
    )
    project = store.add(
        builder.Project,
        name="  =group",
        course_id=str(course.id),
        owner_id=student,
        members=[{"user_id": idle, "private": "no"}],
        experiment_version={
            "group_condition": "configured",
            "prompts": "private prompt",
        },
    )
    return SimpleNamespace(course=course, project=project, student=student, idle=idle)


async def collect(classroom, profile, **filters):
    params = dict(
        project_ids=[classroom.project],
        from_date=None,
        through_date=None,
        stage_id=None,
    )
    return await builder.collect_teacher_export(
        classroom.course, profile=profile, **(params | filters)
    )


def _tables(data):
    return data["tables"][data["groups"][0]["case_id"]]


def _csv(archive, path):
    return list(
        csv.DictReader(io.StringIO(archive.read(path).decode("utf-8-sig"), newline=""))
    )


async def test_participation_rechecks_snapshot_course_and_public_visibility(
    store, classroom, tmp_path
):
    cross_course = store.add(builder.Project, course_id=str(ObjectId()))
    template = store.add(
        builder.Project, course_id=str(classroom.course.id), is_template=True
    )
    later = store.add(builder.Project, course_id=str(classroom.course.id))
    empty = await collect(classroom, "participation", project_ids=[])
    assert empty["groups"] == [] and store.calls == []

    for project in [cross_course, template, later]:
        store.add(
            builder.ChatLog,
            project_id=project,
            created_at=START,
            message_type="text",
            content="outside scope",
        )
    for content, metadata, created_at, kind in (
        (" \t=first", {"stage_id": "inquiry"}, START, "text"),
        ("+last", {}, END - timedelta(milliseconds=1), "text"),
        ("next day", {}, END, "text"),
        ("previous day", {}, START - timedelta(milliseconds=1), "text"),
        ("help request", {"teacher_help_request": True}, START, "text"),
        ("private reply", {"teacher_private_reply": True}, START, "text"),
        ("private ai", {"visibility": "private"}, START, "ai"),
        (
            "private tutor attachment",
            {"file_info": {"filename": "private tutor.pdf"}},
            START,
            "file",
        ),
    ):
        store.add(
            builder.ChatLog,
            project_id=classroom.project,
            user_id=classroom.student,
            created_at=created_at,
            message_type=kind,
            content=content,
            metadata=metadata,
        )
    data = await collect(
        classroom,
        "participation",
        project_ids=[cross_course, template, classroom.project],
        from_date=DAY,
        through_date=DAY,
        stage_id="application_solution",
    )
    assert [row["project_id"] for row in data["groups"]] == [classroom.project]
    assert data["filters"]["stage_id"] is None
    table = _tables(data)
    assert [row["content"] for row in table["discussion"]] == [" \t=first", "+last"]
    assert table["group_counts"][0]["public_messages"] == 2
    assert {
        row["display_name"]: row["public_messages"] for row in table["student_counts"]
    } == {"'=name": 2, "Idle": 0}
    assert not (
        {builder.ResearchEvent, builder.TaskSubmissionArtifact}
        & {call[0] for call in store.calls}
    )
    chat_projection = next(
        fields for model, _, fields in store.calls if model is builder.ChatLog
    )
    assert set(chat_projection) == {
        "user_id",
        "created_at",
        "message_type",
        "content",
        "metadata.stage_id",
        "metadata.experiment_version_id",
    }

    path = tmp_path / "participation.zip"
    assert (
        builder.write_teacher_export(
            str(path),
            classroom.course,
            data,
            profile="participation",
            include_files=True,
        )
        == 0
    )
    with zipfile.ZipFile(path) as archive:
        groups = _csv(archive, "groups.csv")
        case = groups[0]["case_id"]
        assert groups[0]["group_name"] == "'  =group"
        assert case == data["groups"][0]["case_id"]
        assert [
            row["content"] for row in _csv(archive, f"groups/{case}/discussion.csv")
        ] == ["' \t=first", "'+last"]
        assert any(
            row["display_name"] == "''=name"
            for row in _csv(archive, f"groups/{case}/student_counts.csv")
        )
        assert (
            "remove exactly one initial apostrophe"
            in json.loads(archive.read("manifest.json"))["csv_policy"]
        )


async def test_research_stage_order_alignment_and_reserved_group_ai(
    store, classroom, tmp_path
):
    scope = {"project_id": classroom.project}
    document = store.add(
        builder.Document,
        **scope,
        scope="shared",
        title="Public",
        content="<p>public</p>",
        updated_at=END,
    )
    personal = store.add(
        builder.Document,
        **scope,
        scope="personal",
        title="private document",
        updated_at=START,
    )
    for doc_id in [document, personal]:
        store.add(
            builder.ResearchEvent,
            **scope,
            stage_id="inquiry",
            event_time=START,
            experiment_version_id="persisted version",
            event_domain="shared_record",
            event_type="shared_record_content_commit",
            actor_type="student",
            user_id=classroom.student,
            payload={"document_id": doc_id, "text": "opaque event text"},
        )
    store.add(
        builder.ResearchEvent,
        **scope,
        stage_id="inquiry",
        event_time=START,
        event_domain="dialogue",
        event_type="teacher_private_reply",
        actor_type="teacher",
        payload={"text": "private support"},
    )
    student_event = store.add(
        builder.ChatLog,
        **scope,
        message_type="text",
        content="Student",
        user_id=classroom.student,
        created_at=START,
        metadata={"stage_id": "meaning_exploration"},
    )
    assistant_event = store.add(
        builder.ChatLog,
        **scope,
        message_type="text",
        content="Assistant",
        user_id="ai_assistant",
        created_at=START,
        metadata={"stage_id": "inquiry", "experiment_version_id": "persisted version"},
    )
    auto_event = store.add(
        builder.ChatLog,
        **scope,
        message_type="ai",
        content="Auto prompt",
        user_id="auto_prompt:role",
        created_at=START,
        metadata={"stage_id": "inquiry"},
    )
    store.add(
        builder.ChatLog,
        **scope,
        message_type="text",
        content="Other stage",
        user_id=classroom.student,
        created_at=START,
        metadata={"stage_id": "revision"},
    )

    data = await collect(
        classroom,
        "research",
        from_date=DAY,
        through_date=DAY,
        stage_id="meaning_exploration",
    )
    table = _tables(data)
    events = table["event_sequence"]
    assert len(events) == 4
    assert [row["event_id"] for row in events] == sorted(
        row["event_id"] for row in events
    )
    assert [row["sequence_index"] for row in events] == [1, 2, 3, 4]
    assert {row["stage_id"] for row in events} == {"meaning_exploration"}
    by_object = {row["object_id"]: row for row in events}
    assert by_object[f"chat:{student_event}"]["event_type"] == "public_group_message"
    assert by_object[f"chat:{assistant_event}"]["event_type"] == "public_ai_message"
    assert by_object[f"chat:{auto_event}"]["event_type"] == "auto_group_prompt_send"
    assert (
        by_object[f"chat:{assistant_event}"]["actor_type"]
        == by_object[f"chat:{auto_event}"]["actor_type"]
        == "ai_assistant"
    )
    assert (
        by_object[f"document:{document}"]["experiment_version_id"]
        == "persisted version"
    )
    assert [row["object_id"] for row in table["documents"]] == [f"document:{document}"]
    assert table["documents"][0]["reference_only"] is True
    assert table["document_update_operations"] == []
    assert not any(model == "document_update_stream" for model, _, _ in store.calls)
    assert "private document" not in str(data) and "opaque event text" not in str(data)
    assert all(
        "payload" not in fields and "payload.text" not in fields
        for _, _, fields in store.calls
    )

    path = tmp_path / "research.zip"
    builder.write_teacher_export(
        str(path), classroom.course, data, profile="research", include_files=False
    )
    with zipfile.ZipFile(path) as archive:
        case = _csv(archive, "groups.csv")[0]["case_id"]
        assert _csv(archive, "groups.csv")[0]["group_condition"] == "configured"
        assert _csv(archive, f"groups/{case}/document_update_operations.csv") == []
        assert {
            row["event_id"]
            for row in _csv(archive, f"groups/{case}/event_sequence.csv")
        } == {row["event_id"] for row in events}
    participation = await collect(classroom, "participation")
    assert participation["groups"][0]["case_id"] == data["groups"][0]["case_id"]


async def test_research_document_protocol_updates_are_public_metadata_only(
    store, classroom, tmp_path
):
    public = store.add(
        builder.Document, project_id=classroom.project, scope="shared", updated_at=END
    )
    personal = store.add(
        builder.Document,
        project_id=classroom.project,
        scope="personal",
        updated_at=START,
    )
    for doc, timestamp, kind in [
        (public, START, "update"),
        (public, START, "sync-step-2"),
        (public, END, "update"),
        (public, START, "awareness"),
        (personal, START, "update"),
    ]:
        store.add(
            "document_update_stream",
            document_id=doc,
            timestamp=timestamp,
            operation_type=kind,
            user_id=classroom.student,
            payload_size=24,
            update="secret raw bytes",
            client_id="private device",
        )
    data = await collect(classroom, "research", from_date=DAY, through_date=DAY)
    table = _tables(data)
    ops = table["document_update_operations"]
    assert [(row["object_id"], row["operation_type"]) for row in ops] == [
        (f"document:{public}", "update"),
        (f"document:{public}", "sync-step-2"),
    ]
    assert len({row["operation_id"] for row in ops}) == 2
    assert table["event_sequence"] == []
    assert len(table["stage_summary"]) == 4
    assert table["documents"][0]["object_id"] == ops[0]["object_id"]
    query, projection = next(
        (query, fields)
        for model, query, fields in store.calls
        if model == "document_update_stream"
    )
    assert query["document_id"] == {"$in": [public]}
    assert "update" not in projection and "client_id" not in projection
    path = tmp_path / "ops.zip"
    builder.write_teacher_export(
        str(path), classroom.course, data, profile="research", include_files=False
    )
    with zipfile.ZipFile(path) as archive:
        case = data["groups"][0]["case_id"]
        assert len(_csv(archive, f"groups/{case}/document_update_operations.csv")) == 2
        assert (
            "not text, keystrokes, or semantic"
            in json.loads(archive.read("manifest.json"))["document_update_operations"]
        )


async def test_artifact_links_and_missing_originals_without_partial_entries(
    store, classroom, tmp_path, monkeypatch
):
    from app.services.storage_service import storage_service

    scope = {"project_id": classroom.project}
    doc = store.add(
        builder.Document,
        **scope,
        scope="shared",
        content="<p>Shared HTML</p>",
        title="Shared",
        updated_at=START,
    )
    private = store.add(
        builder.Document,
        **scope,
        scope="personal",
        content="personal data",
        updated_at=START,
    )
    store.add(
        builder.DocumentVersion,
        document_id=doc,
        created_at=START,
        version_number=4,
        content_state=b"not exported",
    )
    snapshot = store.add(
        builder.InquirySnapshot, **scope, created_at=START, data=b"not exported"
    )
    task = store.add(
        builder.Task,
        **scope,
        source_type="course_task_release",
        submission_status="submitted",
        title="Task",
        submitted_at=START,
        artifact_document_ids=[doc, private],
        artifact_inquiry_snapshot_id=snapshot,
        review_comment="private review",
    )
    older_task = store.add(
        builder.Task,
        **scope,
        source_type="course_task_release",
        submission_status="submitted",
        title="Previous title",
        submitted_at=START - timedelta(days=1),
        submission_note="previous note",
    )
    key_base = f"projects/{classroom.project}/task-artifacts"
    for parent, filename in [(task, "../../good.pdf"), (older_task, "unavailable.pdf")]:
        artifact = store.add(
            builder.TaskSubmissionArtifact,
            **scope,
            task_id=parent,
            filename=filename,
            size=4,
            file_key=f"{key_base}/{parent}/original",
            uploaded_at=START,
        )
        task_row = next(
            row for row in store.records[builder.Task] if str(row["_id"]) == parent
        )
        task_row["submission_artifact_ids"] = [artifact]
    store.add(
        builder.TaskSubmissionArtifact,
        **scope,
        task_id=task,
        filename="unlinked.pdf",
        size=4,
        uploaded_at=START,
    )
    store.add(
        builder.Resource,
        **scope,
        source_type="chat_attachment",
        filename="private tutor origin",
        uploaded_at=START,
    )
    store.add(
        builder.Resource,
        **scope,
        source_type="library",
        filename="malformed.pdf",
        uploaded_at=START,
        size=4,
        file_key=f"projects/{ObjectId()}/files/secret",
    )

    def write_file(key, target):
        if key == f"{key_base}/{task}/original":
            return target.write(b"good")
        assert key == f"{key_base}/{older_task}/original"
        target.write(b"part")
        raise OSError("provider endpoint and credential")

    monkeypatch.setattr(storage_service, "write_file_to", write_file)
    data = await collect(classroom, "artifacts", from_date=DAY, through_date=DAY)
    assert all(
        value not in str(data)
        for value in (
            private,
            "private review",
            "unlinked.pdf",
            "private tutor origin",
            "previous note",
        )
    )
    path = tmp_path / "artifacts.zip"
    assert (
        builder.write_teacher_export(
            str(path), classroom.course, data, profile="artifacts", include_files=True
        )
        == 2
    )
    with zipfile.ZipFile(path) as archive:
        case = data["groups"][0]["case_id"]
        folder = f"groups/{case}"
        files = _csv(archive, f"{folder}/submission_files.csv")
        original = next(
            row["archive_path"] for row in files if row["filename"].endswith("good.pdf")
        )
        assert archive.read(original) == b"good"
        assert ".." not in original.split("/")
        assert len([name for name in archive.namelist() if "/originals/" in name]) == 1
        errors = _csv(archive, "file_errors.csv")
        assert {row["filename"] for row in errors} == {
            "unavailable.pdf",
            "malformed.pdf",
        }
        assert all(row["reason"] == "unavailable" for row in errors)
        assert b"credential" not in archive.read("file_errors.csv")
        submissions = {
            row["object_id"]: row for row in _csv(archive, f"{folder}/submissions.csv")
        }
        assert submissions[f"task:{older_task}"]["reference_only"] == "True"
        assert {f"document:{doc}", f"inquiry:{snapshot}"} <= set(
            json.loads(submissions[f"task:{task}"]["linked_object_ids"])
        )
        document = _csv(archive, f"{folder}/documents.csv")[0]
        assert (document["text"], document["html"]) == (
            "Shared HTML",
            "<p>Shared HTML</p>",
        )
        assert (
            _csv(archive, f"{folder}/document_versions.csv")[0]["document_id"]
            == document["object_id"]
        )
        assert json.loads(archive.read("manifest.json"))["missing_files"] == 2


@pytest.mark.parametrize(
    "value",
    [
        "=1+2",
        "+cmd",
        "-1",
        "@cmd",
        "\tplain",
        "\rplain",
        "\n=1",
        "   =1",
        "'literal",
        "\uff1dcmd",
    ],
)
def test_formula_guard_round_trips(value):
    assert builder._csv_cell(value) == "'" + value
    assert builder._csv_cell("case-22fd") == "case-22fd"
    assert builder._csv_cell(-1) == -1
