"""Private teacher model credentials and safe catalog entries."""

import base64
import hashlib
from typing import Any

from beanie import PydanticObjectId
from bson.errors import InvalidId
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.repositories.teacher_model_connection import TeacherModelConnection

TEACHER_MODEL_PREFIX = "teacher:"
PRESET_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


def _secret_cipher(owner_id: str) -> Fernet:
    material = f"aiscl:teacher-model:v1:{owner_id}:{settings.SECRET_KEY}".encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_teacher_api_key(owner_id: str, value: str) -> str:
    return _secret_cipher(owner_id).encrypt(value.encode()).decode()


def teacher_model_id(connection: TeacherModelConnection) -> str:
    return f"{TEACHER_MODEL_PREFIX}{connection.id}"


async def get_teacher_connection(owner_id: str, model_id: str) -> TeacherModelConnection | None:
    if not model_id.startswith(TEACHER_MODEL_PREFIX):
        return None
    try:
        object_id = PydanticObjectId(model_id[len(TEACHER_MODEL_PREFIX):])
    except (InvalidId, TypeError, ValueError):
        return None
    return await TeacherModelConnection.find_one({"_id": object_id, "owner_id": owner_id})


async def list_teacher_model_choices(owner_id: str) -> list[dict[str, str]]:
    rows = await TeacherModelConnection.find({"owner_id": owner_id}).sort("name", "_id").to_list()
    return [{"id": teacher_model_id(row), "name": row.name} for row in rows]


async def load_teacher_model_definition(owner_id: str, model_id: str) -> dict[str, Any] | None:
    row = await get_teacher_connection(owner_id, model_id)
    if row is None:
        return None
    try:
        api_key = _secret_cipher(owner_id).decrypt(row.encrypted_api_key.encode()).decode()
    except InvalidToken:
        return None
    return {
        "id": row.model,
        "name": row.name,
        "provider": row.provider,
        "base_url": row.base_url,
        "api_key": api_key,
    }
