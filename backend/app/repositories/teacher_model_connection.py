"""Private, reusable model connections owned by individual teachers."""

from datetime import datetime, timezone

from beanie import Document
from pydantic import Field


class TeacherModelConnection(Document):
    owner_id: str = Field(index=True)
    name: str
    provider: str
    model: str
    base_url: str
    encrypted_api_key: str = Field(repr=False)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "teacher_model_connections"
