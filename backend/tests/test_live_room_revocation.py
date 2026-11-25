"""Membership changes revoke local live subscriptions, not whole user sessions."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from beanie import init_beanie
from fastapi import HTTPException, WebSocket
from minio import Minio
from motor.motor_asyncio import AsyncIOMotorClient
from socketio import AsyncServer

with patch.object(Minio, "bucket_exists", return_value=True):
    from app.api.v1 import courses, projects
    from app.websocket import socketio_server, yjs_server

from app.repositories.course import Course
from app.repositories.document import Document
from app.repositories.project import Project
from app.repositories.user import User


OLD_ROOMS = ("project:old", "inquiry:old", "wb:old", "doc:shared")
KEPT_ROOMS = ("project:other", "doc:other", "doc:personal")


def member(user_id, role="editor"):
    return {"user_id": user_id, "role": role, "joined_at": None}


@pytest.fixture
def records(monkeypatch):
    teacher = SimpleNamespace(id="teacher", role="teacher")
    student = SimpleNamespace(id="student", role="student", class_id="course", save=AsyncMock())
    people = {"teacher": teacher, "student": student, "peer": SimpleNamespace(id="peer", role="student")}
    for person in people.values():
        person.username = person.id
        person.avatar_url = None
    old = Project.model_construct(
        id="old", name="Old group", owner_id="teacher", course_id="course",
        leader_id="student", members=[member("student"), member("peer")],
    )
    other = Project.model_construct(
        id="other", name="Other group", owner_id="teacher", course_id="other-course",
        members=[member("student")],
    )
    groups = {"old": old, "other": other}
    course = Course.model_construct(id="course", teacher_id="teacher", students=["student", "peer"])
    documents = {
        "shared": SimpleNamespace(project_id="old", scope="shared"),
        "other": SimpleNamespace(project_id="other", scope="shared"),
        "personal": SimpleNamespace(project_id="old", scope="personal", owner_id=None, last_modified_by="student"),
        "peer-personal": SimpleNamespace(project_id="old", scope="personal", owner_id="peer"),
    }

    async def save(project):
        groups[project.id] = project.model_copy(deep=True)

    async def get_project(project_id):
        group = groups.get(project_id)
        return group.model_copy(deep=True) if group else None

    def find_projects(query):
        course_id = query["course_id"]
        user_id = query["$or"][0]["owner_id"]
        assert query["$or"][1] == {"members.user_id": user_id}
        return SimpleNamespace(to_list=AsyncMock(return_value=[
            group.model_copy(deep=True) for group in groups.values()
            if group.course_id == course_id and (
                group.owner_id == user_id or any(m["user_id"] == user_id for m in group.members)
            )
        ]))

    monkeypatch.setattr(Project, "get", get_project)
    monkeypatch.setattr(Project, "save", save)
    monkeypatch.setattr(Project, "find", Mock(side_effect=find_projects))
    monkeypatch.setattr(User, "get", AsyncMock(side_effect=people.get))
    monkeypatch.setattr(Document, "get", AsyncMock(side_effect=documents.get))
    monkeypatch.setattr(Course, "get", AsyncMock(return_value=course))
    monkeypatch.setattr(Course, "save_changes", AsyncMock())
    return SimpleNamespace(groups=groups, course=course, teacher=teacher, student=student)


@pytest.fixture
def socket_io(monkeypatch):
    sio = AsyncServer(async_mode="asgi")
    sio.on("disconnect", socketio_server.disconnect)
    packets = AsyncMock()
    sessions = {}

    async def connect(eio_sid, user_id, rooms):
        sid = await sio.manager.connect(eio_sid, "/")
        await socketio_server.connect(sid, {}, {"token": user_id})
        for room in rooms:
            await socketio_server.join_room(sid, {"room_id": room})
            assert room in sio.rooms(sid)
        return sid

    monkeypatch.setattr(socketio_server, "sio", sio)
    monkeypatch.setattr(socketio_server, "user_connections", {})
    monkeypatch.setattr(socketio_server, "room_members", {})
    monkeypatch.setattr(socketio_server, "verify_token", lambda token, **_: {"sub": token})
    monkeypatch.setattr(sio, "get_session", AsyncMock(side_effect=sessions.get))
    monkeypatch.setattr(sio, "save_session", AsyncMock(side_effect=sessions.__setitem__))
    monkeypatch.setattr(sio, "_send_eio_packet", packets)
    monkeypatch.setattr(sio, "_send_packet", AsyncMock())
    return SimpleNamespace(sio=sio, connect=connect, packets=packets)


@pytest.fixture
async def yjs(monkeypatch):
    monkeypatch.setattr(yjs_server, "rooms", {})
    monkeypatch.setattr(yjs_server, "room_connection_counts", {})
    monkeypatch.setattr(yjs_server, "room_observers", {})
    monkeypatch.setattr(yjs_server, "rooms_lock", asyncio.Lock())
    monkeypatch.setattr(yjs_server, "user_rooms", {})
    monkeypatch.setattr(yjs_server, "verify_token", lambda token, **_: {"sub": token})
    monkeypatch.setattr(yjs_server, "load_room_data", AsyncMock())
    monkeypatch.setattr(yjs_server, "setup_persistence", Mock())
    monkeypatch.setattr(yjs_server.collaboration_service, "save_snapshot", AsyncMock())
    sessions = []

    async def connect(user_id, room_id, *, allowed=True):
        incoming, outgoing = asyncio.Queue(), asyncio.Queue()
        incoming.put_nowait({"type": "websocket.connect"})
        websocket = WebSocket({"type": "websocket"}, incoming.get, outgoing.put)
        task = asyncio.create_task(yjs_server.websocket_endpoint(websocket, room_id, user_id))
        sessions.append((task, incoming))

        async def receive():
            return await asyncio.wait_for(outgoing.get(), 2)

        message = await receive()
        if allowed:
            assert message["type"] == "websocket.accept"
            assert (await receive())["type"] == "websocket.send"  # Yjs initial sync.
        else:
            assert message == {"type": "websocket.close", "code": 1008, "reason": ""}
        return SimpleNamespace(task=task, incoming=incoming, outgoing=outgoing, receive=receive)

    yield connect

    for task, incoming in sessions:
        if not task.done():
            incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
    await asyncio.wait_for(asyncio.gather(*(task for task, _ in sessions)), 3)


@pytest.mark.parametrize("remove_from_course", [False, True], ids=["project", "course"])
async def test_revokes_all_sessions_without_evicting_peers_or_other_rooms(
    records, socket_io, yjs, remove_from_course
):
    if remove_from_course:
        # Course removal must also revoke a student who owns their old group.
        records.groups["old"].owner_id = "student"

    sio = socket_io.sio
    tabs = [
        await socket_io.connect(f"tab-{i}", "student", OLD_ROOMS + KEPT_ROOMS)
        for i in range(2)
    ]
    peer_sid = await socket_io.connect("peer", "peer", OLD_ROOMS)
    removed = [
        await yjs("student", room_id) for room_id in OLD_ROOMS for _ in range(2)
    ]
    kept = [await yjs("student", room_id) for room_id in KEPT_ROOMS]
    peer_sessions = [await yjs("peer", room_id) for room_id in OLD_ROOMS]

    if remove_from_course:
        await courses.remove_student_from_course("course", "student", records.teacher)
        assert records.course.students == ["peer"]
        assert records.student.class_id is None
        assert records.groups["old"].owner_id == "teacher"
    else:
        await projects.remove_project_member("old", "student", records.teacher)

    assert records.groups["old"].members == [member("peer")]
    assert records.groups["old"].leader_id == "peer"
    assert records.groups["other"].members == [member("student")]
    for sid in tabs:
        assert set(sio.rooms(sid)) == {sid, *KEPT_ROOMS}
    assert set(sio.rooms(peer_sid)) == {peer_sid, *OLD_ROOMS}
    for room_id in OLD_ROOMS:
        assert socketio_server.room_members[room_id] == {"peer": {peer_sid}}
        socket_io.packets.reset_mock()
        await sio.emit("operation", {"roomId": room_id}, room=room_id)
        assert [call.args[0] for call in socket_io.packets.await_args_list] == ["peer"]
        await socketio_server.join_room(tabs[0], {"room_id": room_id})
        assert room_id not in sio.rooms(tabs[0])
        await yjs("student", room_id, allowed=False)
    for session in removed:
        assert (await session.receive())["code"] == 1008
        await asyncio.wait_for(session.task, 2)
    assert yjs_server.get_user_rooms("student") == set(KEPT_ROOMS)
    assert all(not session.task.done() for session in kept + peer_sessions)
    assert all(yjs_server.room_connection_counts[room_id] == 1 for room_id in OLD_ROOMS)

    # Remaining clients in an old YRoom can still exchange protocol traffic.
    awareness = b"\x01\x01\x00"
    peer_sessions[-1].incoming.put_nowait({"type": "websocket.receive", "bytes": awareness})
    assert (await peer_sessions[-1].receive())["bytes"] == awareness


@pytest.mark.parametrize("remains_member", [False, True], ids=["owner-only", "retained-editor"])
async def test_ownership_transfer_only_revokes_access_the_previous_owner_lost(
    records, socket_io, yjs, remains_member
):
    project = records.groups["old"]
    project.owner_id = "student"
    project.members = [member("peer")]
    if remains_member:
        project.members.append(member("student", "owner"))
    sid = await socket_io.connect("former-owner", "student", ("project:old", "doc:peer-personal", "doc:personal"))
    foreign_personal = await yjs("student", "doc:peer-personal")
    own_personal = await yjs("student", "doc:personal")

    with pytest.raises(HTTPException) as error:
        await projects.remove_project_member("old", "student", records.teacher)
    assert error.value.status_code == 409

    await projects.transfer_ownership("old", "peer", records.student)

    assert ("project:old" in socket_io.sio.rooms(sid)) is remains_member
    assert "doc:peer-personal" not in socket_io.sio.rooms(sid)
    assert "doc:personal" in socket_io.sio.rooms(sid)
    assert (await foreign_personal.receive())["code"] == 1008
    assert not own_personal.task.done()


@pytest.mark.parametrize("disconnect", [False, True], ids=["leave", "disconnect"])
async def test_one_tab_leaving_keeps_other_tab_in_presence_index(records, socket_io, disconnect):
    first = await socket_io.connect("first", "student", ("project:old",))
    second = await socket_io.connect("second", "student", ("project:old",))
    if disconnect:
        await socket_io.sio.disconnect(first)
    else:
        await socketio_server.leave_room(first, {"room_id": "project:old"})
    assert socketio_server.room_members["project:old"] == {"student": {second}}

    await projects.remove_project_member("old", "student", records.teacher)
    assert socket_io.sio.rooms(second) == [second]
    assert "project:old" not in socketio_server.room_members


async def test_admission_started_before_removal_cannot_outlive_revocation(
    records, socket_io, monkeypatch
):
    sid = await socket_io.connect("racing-tab", "student", ())
    access_checked, finish_join, removal_saved = (asyncio.Event() for _ in range(3))
    ensure_access = socketio_server.ensure_room_access
    save_project = Project.save

    async def delayed_check(*args):
        result = await ensure_access(*args)
        access_checked.set()
        await finish_join.wait()
        return result

    async def save(project):
        await save_project(project)
        removal_saved.set()

    monkeypatch.setattr(socketio_server, "ensure_room_access", delayed_check)
    monkeypatch.setattr(Project, "save", save)
    join = asyncio.create_task(socketio_server.join_room(sid, {"room_id": "project:old"}))
    try:
        await asyncio.wait_for(access_checked.wait(), 2)
        removal = asyncio.create_task(projects.remove_project_member("old", "student", records.teacher))
        await asyncio.wait_for(removal_saved.wait(), 2)
        assert not removal.done()
    finally:
        finish_join.set()
        await asyncio.wait_for(join, 2)
    await asyncio.wait_for(removal, 2)
    assert socket_io.sio.rooms(sid) == [sid]


async def test_course_removal_persists_old_group_revocation(socket_io, yjs):
    uri = os.environ.get("COURSE_TEST_MONGODB_URI") or os.environ.get("CHAT_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("Set COURSE_TEST_MONGODB_URI to run course removal integration test")

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
    db = mongo[f"course_removal_test_{uuid4().hex}"]
    try:
        await init_beanie(database=db, document_models=[Course, Project, User, Document])
        student = await User(
            username="Student", email="student@example.org", role="student", password_hash="unused",
        ).insert()
        student_id = str(student.id)
        course = await Course(
            name="Old course", teacher_id="teacher", semester="2026-Fall",
            invite_code="TEST01", students=[student_id],
        ).insert()
        student.class_id = str(course.id)
        await student.save()
        group = await Project(
            name="Old group", owner_id=student_id, leader_id=student_id, course_id=str(course.id),
            members=[member(student_id, "owner"), member("peer")],
        ).insert()
        room_id = f"project:{group.id}"
        sid = await socket_io.connect("tab", student_id, (room_id,))
        websocket = await yjs(student_id, f"wb:{group.id}")

        await courses.remove_student_from_course(
            str(course.id), student_id, SimpleNamespace(id="teacher", role="teacher"),
        )

        saved = await Project.get(group.id)
        assert (saved.owner_id, saved.leader_id, saved.members) == ("teacher", "peer", [member("peer")])
        assert (await Course.get(course.id)).students == []
        assert (await User.get(student.id)).class_id is None
        assert socket_io.sio.rooms(sid) == [sid]
        assert (await websocket.receive())["code"] == 1008
        await asyncio.wait_for(websocket.task, 2)
        await yjs(student_id, f"wb:{group.id}", allowed=False)
    finally:
        await mongo.drop_database(db.name)
        mongo.close()
