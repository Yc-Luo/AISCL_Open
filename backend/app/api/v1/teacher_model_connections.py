"""Teacher-owned model connection API. Never return or log the stored key."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.v1.auth import get_current_user
from app.core.llm_config import course_dialogue_llm
from app.core.llm_runtime import guarded_ainvoke
from app.core.security import limiter
from app.core.teacher_model_transport import normalize_teacher_endpoint
from app.repositories.teacher_model_connection import TeacherModelConnection
from app.repositories.user import User
from app.services.teacher_model_service import (
    PRESET_BASE_URLS,
    encrypt_teacher_api_key,
    get_teacher_connection,
    teacher_model_id,
)

router = APIRouter(prefix="/teacher-model-connections", tags=["teacher-model-connections"])
MAX_CONNECTIONS = 20


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    provider: Literal["openai", "deepseek", "siliconflow", "custom"]
    model: str = Field(min_length=1, max_length=180)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: SecretStr | None = Field(default=None, max_length=512)


class ConnectionView(BaseModel):
    id: str
    name: str
    provider: str
    model: str
    base_url: str
    has_api_key: bool


def _view(row: TeacherModelConnection) -> ConnectionView:
    return ConnectionView(
        id=teacher_model_id(row), name=row.name, provider=row.provider, model=row.model,
        base_url=row.base_url, has_api_key=bool(row.encrypted_api_key),
    )


async def _connection_owner(user: User = Depends(get_current_user)) -> str:
    if user.role not in {"teacher", "admin"} or not user.is_active or user.is_banned:
        raise HTTPException(status_code=403, detail="只有启用的教师或管理员账号可以管理自己的模型连接。")
    return str(user.id)


async def _owned_connection(connection_id: str, owner_id: str) -> TeacherModelConnection:
    row = await get_teacher_connection(owner_id, connection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="未找到此模型连接。")
    return row


def _connection_changes(payload: ConnectionInput, owner_id: str, current: TeacherModelConnection | None = None) -> dict:
    try:
        base_url = normalize_teacher_endpoint(PRESET_BASE_URLS.get(payload.provider) or payload.base_url or "")
    except ValueError:
        raise HTTPException(status_code=422, detail="请填写公网 HTTPS 接口基础地址（不要包含 /chat/completions）。") from None

    key = payload.api_key.get_secret_value().strip() if payload.api_key is not None else ""
    if current is not None and (current.provider, current.model, current.base_url) != (payload.provider, payload.model, base_url):
        raise HTTPException(status_code=409, detail="更换模型或地址时请新建连接，再将新连接应用到需要的小组。")
    if not key and current is None:
        raise HTTPException(status_code=422, detail="新增连接时请填写 API 密钥。")
    if key and (not key.isascii() or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in key)):
        raise HTTPException(status_code=422, detail="API 密钥不能包含空白或控制字符。")

    changes = {"name": payload.name, "provider": payload.provider, "model": payload.model, "base_url": base_url}
    if key:
        changes["encrypted_api_key"] = encrypt_teacher_api_key(owner_id, key)
    return changes


@router.get("")
async def list_connections(owner_id: str = Depends(_connection_owner)) -> dict[str, list[ConnectionView]]:
    rows = await TeacherModelConnection.find({"owner_id": owner_id}).sort("name", "_id").to_list()
    return {"connections": [_view(row) for row in rows]}


@router.post("", response_model=ConnectionView, status_code=status.HTTP_201_CREATED)
async def create_connection(payload: ConnectionInput, owner_id: str = Depends(_connection_owner)) -> ConnectionView:
    if await TeacherModelConnection.find({"owner_id": owner_id}).count() >= MAX_CONNECTIONS:
        raise HTTPException(status_code=409, detail="每个账号最多可保存 20 条模型连接。")
    row = TeacherModelConnection(owner_id=owner_id, **_connection_changes(payload, owner_id))
    await row.insert()
    return _view(row)


@router.put("/{connection_id}", response_model=ConnectionView)
async def update_connection(
    connection_id: str, payload: ConnectionInput, owner_id: str = Depends(_connection_owner)
) -> ConnectionView:
    row = await _owned_connection(connection_id, owner_id)
    changes = _connection_changes(payload, owner_id, row)
    await row.set({**changes, "updated_at": datetime.now(timezone.utc)})
    return _view(row)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: str, owner_id: str = Depends(_connection_owner)):
    row = await _owned_connection(connection_id, owner_id)
    await row.delete()


@router.post("/{connection_id}/test")
@limiter.limit("5/minute")
async def test_connection(request: Request, connection_id: str, owner_id: str = Depends(_connection_owner)) -> dict:
    await _owned_connection(connection_id, owner_id)
    try:
        async with course_dialogue_llm(
            model_id=connection_id, owner_id=owner_id, temperature=0, max_tokens=32, timeout=12,
        ) as model:
            await guarded_ainvoke(model, "Please reply with OK.")
        return {"success": True, "message": "模型连接测试通过。"}
    except Exception:
        return {"success": False, "message": "连接测试失败，请检查服务地址、模型 ID、API 密钥与服务商用量。"}
