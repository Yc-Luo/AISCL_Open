"""Resource model."""

from datetime import datetime
from typing import Optional

from beanie import Document
from pydantic import Field, model_validator


class Resource(Document):
    """Resource document model."""

    project_id: Optional[str] = Field(default=None, index=True)
    course_id: Optional[str] = Field(default=None, index=True)
    scope: str = Field(default="project", pattern="^(project|course)$", index=True)
    filename: str
    file_key: str  # S3 Object Key: projects/{project_id}/files/{file_id} or courses/{course_id}/files/{file_id}
    url: str  # CDN URL
    size: int = Field(..., ge=0)  # File size in bytes
    mime_type: str
    source_type: str = Field(default="library", index=True)
    uploaded_by: str = Field(..., index=True)
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    parse_status: str = Field(
        default="pending",
        pattern="^(pending|parsing|embedding|indexing|indexed|failed|unsupported|no_text|disabled|unverified)$",
        index=True,
    )
    parse_provider: Optional[str] = Field(default=None, index=True)
    parse_task_id: Optional[str] = None
    parse_error: Optional[str] = None
    parsed_markdown_key: Optional[str] = None
    parsed_content_key: Optional[str] = None
    parsed_zip_key: Optional[str] = None
    parsed_at: Optional[datetime] = None
    indexed_at: Optional[datetime] = None

    @model_validator(mode="after")
    def mark_legacy_index_unverified(self) -> "Resource":
        # Older uploads recorded extraction as indexed without a Qdrant receipt.
        if self.parse_status == "indexed" and self.indexed_at is None:
            self.parse_status = "unverified"
        return self

    async def set_rag_status(self, status: str, error: Optional[str] = None) -> None:
        self.parse_status = status
        self.parse_error = error
        self.indexed_at = datetime.utcnow() if status == "indexed" else None
        await self.save()

    class Settings:
        """Beanie settings."""

        name = "resources"
        indexes = [
            [("project_id", 1)],
            [("course_id", 1)],
            [("scope", 1)],
            [("project_id", 1), ("source_type", 1)],
            [("course_id", 1), ("source_type", 1)],
            [("uploaded_by", 1)],
            [("uploaded_at", 1)],
            [("parse_status", 1)],
            [("parse_provider", 1)],
        ]
