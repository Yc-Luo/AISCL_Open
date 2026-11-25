"""Revoke live project subscriptions after a membership change is saved."""

import asyncio

from app.services.room_mapping_service import (
    room_access_lock,
    room_belongs_to_project,
    validate_room_access,
)
from app.websocket import socketio_server, yjs_server


async def revoke_project_room_access(project_id: str, user_id: str) -> None:
    """Evict this user's local sessions only where project access was lost."""
    async with room_access_lock(user_id):
        subscribed = (
            socketio_server.get_user_rooms(user_id)
            | yjs_server.get_user_rooms(user_id)
        )
        denied = {
            room_id
            for room_id in subscribed
            if await room_belongs_to_project(room_id, project_id)
            and not await validate_room_access(room_id, user_id)
        }
        if denied:
            await asyncio.gather(
                socketio_server.revoke_rooms(user_id, denied),
                yjs_server.revoke_rooms(user_id, denied),
            )
