import json
import logging
from typing import Dict, Optional, Any

from socketio import AsyncServer, ASGIApp
from socketio.exceptions import ConnectionRefusedError

from app.core.config import settings
from app.services.auth_service import verify_token
from app.services.room_mapping_service import room_access_lock, validate_room_access
from app.websocket.handlers.collaboration_handler import unload_resources

# Create Socket.IO server
sio = AsyncServer(
    cors_allowed_origins=settings.CORS_ORIGINS,
    async_mode="asgi",
    ping_timeout=60,
    ping_interval=25,
    compression=True,
)

logger = logging.getLogger(__name__)

MAX_BATCH_OPERATIONS = 100
MAX_OPERATION_PAYLOAD_CHARS = 250_000
MAX_BATCH_PAYLOAD_CHARS = 1_000_000

# Store user connections: {user_id: [sid1, sid2, ...]}
user_connections: Dict[str, list] = {}
# Store room members: {room_id: {user_id: {sid, ...}}}
room_members: Dict[str, Dict[str, set[str]]] = {}


def get_user_rooms(user_id: str) -> set[str]:
    return {
        room_id
        for sid in user_connections.get(user_id, ())
        for room_id in sio.rooms(sid)
        if room_id != sid
    }


async def _remove_room_session(user_id: str, sid: str, room_id: str) -> None:
    await sio.leave_room(sid, room_id)
    members = room_members.get(room_id)
    sessions = members.get(user_id) if members is not None else None
    if not sessions or sid not in sessions:
        return

    sessions.remove(sid)
    if sessions:
        return

    del members[user_id]
    if not members:
        del room_members[room_id]
        await unload_resources(room_id)
    else:
        await sio.emit("user_left", {"roomId": room_id, "user_id": user_id}, room=room_id)


async def revoke_rooms(user_id: str, room_ids: set[str]) -> None:
    for sid in tuple(user_connections.get(user_id, ())):
        for room_id in room_ids.intersection(sio.rooms(sid)):
            await _remove_room_session(user_id, sid, room_id)
            await sio.emit("room_left", {"room": room_id}, room=sid)


def normalize_room_id(room_id: str, module: Optional[str] = None) -> str:
    """Normalize client room IDs before permission checks and broadcasts."""
    if room_id.startswith(("project:", "doc:", "wb:", "inquiry:")):
        return room_id
    if module == "inquiry":
        return f"inquiry:{room_id}"
    if module == "document":
        return f"doc:{room_id}"
    if module in {"whiteboard", "collaboration"}:
        # Socket.IO collaboration handlers route whiteboard/project operations through
        # project-scoped rooms. The wb: prefix is reserved for Yjs websocket rooms.
        return f"project:{room_id}"
    return f"project:{room_id}"


async def ensure_room_access(user_id: str, room_id: str) -> bool:
    """Validate Socket.IO room access and keep denial logging in one place."""
    has_access = await validate_room_access(room_id, user_id)
    if not has_access:
        logger.warning("Socket room access denied: user_id=%s room_id=%s", user_id, room_id)
    return has_access


async def authenticate_socket(auth: Optional[dict]) -> Optional[str]:
    """Authenticate socket connection using JWT token."""
    if not auth or "token" not in auth:
        return None

    token = auth["token"]
    payload = verify_token(token, token_type="access")
    if not payload:
        return None

    user_id = payload.get("sub")
    return user_id


def _payload_size_chars(payload: Any) -> int:
    """Estimate JSON payload size before dispatching WebSocket operations."""
    try:
        return len(json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        return len(str(payload))


@sio.event
async def connect(sid, environ, auth):
    """Handle client connection with authentication."""
    user_id = await authenticate_socket(auth)
    if not user_id:
        raise ConnectionRefusedError("Authentication failed")

    # Store connection
    if user_id not in user_connections:
        user_connections[user_id] = []
    user_connections[user_id].append(sid)

    # Store user info in session
    await sio.save_session(sid, {"user_id": user_id})
    print(f"Client connected: {sid}, user_id: {user_id}")


@sio.event
async def disconnect(sid, *args):
    """Handle client disconnection."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")

    if user_id:
        # Remove from user connections
        if user_id in user_connections:
            if sid in user_connections[user_id]:
                user_connections[user_id].remove(sid)
            if not user_connections[user_id]:
                del user_connections[user_id]

        for room_id in sio.rooms(sid):
            if room_id != sid:
                await _remove_room_session(user_id, sid, room_id)

    logger.info(f"Client disconnected: {sid}, user_id: {user_id}")


@sio.event
async def join_room(sid, data):
    """Join a project room."""
    logger.info(f"[join_room] sid={sid}, data={data}")
    
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    if not user_id:
        logger.warning(f"[join_room] No user_id in session for {sid}")
        return

    room_id = data.get("room_id") or data.get("room") or data.get("roomId")
    if not room_id:
        logger.warning(f"[join_room] No room_id in data for {sid}")
        return

    # Extract module to help formatting
    module = data.get("module")

    room_id = normalize_room_id(room_id, module)
    async with room_access_lock(user_id):
        if not await ensure_room_access(user_id, room_id):
            await sio.emit(
                "room_join_error",
                {"room": room_id, "message": "No permission to join this room"},
                room=sid,
            )
            return
        if sid not in user_connections.get(user_id, ()):
            return

        await sio.enter_room(sid, room_id)
        room_members.setdefault(room_id, {}).setdefault(user_id, set()).add(sid)

        from app.repositories.user import User

        user = await User.get(user_id)
        if user and room_id in sio.rooms(sid):
            await sio.emit(
                "user_joined",
                {
                    "user_id": user_id,
                    "username": user.username,
                    "avatar_url": user.avatar_url,
                    "role": user.role,
                    "roomId": room_id,
                },
                room=room_id,
                skip_sid=sid,
            )

        if room_id in sio.rooms(sid):
            await sio.emit("room_joined", {"room": room_id}, room=sid)
            if module in ["whiteboard", "collaboration", "document", "inquiry"]:
                from app.websocket.handlers.collaboration_handler import sync_yjs_state
                await sync_yjs_state(sio, sid, room_id, module)


@sio.event
async def leave_room(sid, data):
    """Leave a project room."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    if not user_id:
        return

    room_id = data.get("room_id") or data.get("room") or data.get("roomId")
    if not room_id:
        return

    room_id = normalize_room_id(room_id, data.get("module"))
    await _remove_room_session(user_id, sid, room_id)
    await sio.emit("room_left", {"room": room_id}, room=sid)


@sio.event
async def operation(sid, data):
    """Handle unified sync operations."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    if not user_id:
        return

    if _payload_size_chars(data) > MAX_OPERATION_PAYLOAD_CHARS:
        logger.warning("Operation payload too large from user %s", user_id)
        return

    room_id = data.get("room_id") or data.get("roomId")
    if not room_id:
        logger.warning("Operation from user %s has no room id: %s", user_id, data)
        return

    normalized_room_id = normalize_room_id(room_id, data.get("module"))
    if not await ensure_room_access(user_id, normalized_room_id):
        return

    data["roomId"] = normalized_room_id
    data["room_id"] = normalized_room_id

    # Dispatch to appropriate handler
    from app.websocket.operation_dispatcher import dispatch_operation
    await dispatch_operation(sio, sid, data, user_id)


@sio.on("batch-operations")
async def batch_operations(sid, data):
    """Handle batch of operations for reliable sync."""
    session = await sio.get_session(sid)
    user_id = session.get("user_id")
    if not user_id:
        return {"status": "error", "message": "Unauthorized"}

    operations = data.get("operations", [])
    if not operations:
        return {"status": "success", "count": 0}
    if not isinstance(operations, list):
        return {"status": "error", "message": "Invalid operations payload"}
    if len(operations) > MAX_BATCH_OPERATIONS:
        logger.warning("Batch operation count exceeded by user %s: %s", user_id, len(operations))
        return {
            "status": "error",
            "message": f"Too many operations in one batch; max is {MAX_BATCH_OPERATIONS}",
        }
    if _payload_size_chars(data) > MAX_BATCH_PAYLOAD_CHARS:
        logger.warning("Batch payload too large from user %s", user_id)
        return {"status": "error", "message": "Batch payload too large"}

    from app.websocket.operation_dispatcher import dispatch_operation

    success_count = 0
    denied_count = 0
    for op in operations:
        try:
            if not isinstance(op, dict):
                denied_count += 1
                continue
            if _payload_size_chars(op) > MAX_OPERATION_PAYLOAD_CHARS:
                denied_count += 1
                logger.warning("Single operation payload too large from user %s", user_id)
                continue
            room_id = op.get("room_id") or op.get("roomId")
            if not room_id:
                continue
            normalized_room_id = normalize_room_id(room_id, op.get("module"))
            if not await ensure_room_access(user_id, normalized_room_id):
                denied_count += 1
                continue
            op["roomId"] = normalized_room_id
            op["room_id"] = normalized_room_id
            await dispatch_operation(sio, sid, op, user_id)
            success_count += 1
        except Exception as e:
            denied_count += 1
            logger.exception("Error processing operation in batch from user %s: %s", user_id, e)

    # Return ACK
    return {"status": "success", "processed": success_count, "denied": denied_count}


@sio.on("ping")
async def handle_ping(sid):
    """Handle custom application-level heartbeat."""
    await sio.emit("pong", room=sid)



# Create ASGI app
socketio_app = ASGIApp(sio, socketio_path="/")
