"""Private personal AI uploads, not group resources."""

from datetime import datetime, timezone

from beanie import Document
from pydantic import Field

class AIAttachment(Document):
    project_id: str
    owner_id: str
    file_key: str
    filename: str
    size: int
    mime_type: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "ai_attachments"

    def summary(self) -> dict:
        return {"id": str(self.id), "filename": self.filename, "size": self.size, "mime_type": self.mime_type}
