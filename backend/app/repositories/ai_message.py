"""AI message model."""

from datetime import datetime
from typing import List, Optional

from beanie import Document
from pydantic import Field


def assistant_reply_content(content: str, *, attachment_assisted: bool) -> dict:
    if not attachment_assisted:
        return {"content": content}
    return {
        "content": "[个人附件相关回复，仅对会话所有者可见]",
        "private_attachment_reply": content,
    }


class Citation(Document):
    """Citation schema."""

    resource_id: str
    page: Optional[int] = None


class AIMessage(Document):
    """AI message document model."""

    conversation_id: str = Field(..., index=True)
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str
    attachments: List[dict] = Field(default_factory=list)
    private_attachment_reply: Optional[str] = None
    citations: List[dict] = Field(default_factory=list)  # List of Citation-like dicts
    metadata: Optional[dict] = None
    feedback: Optional[dict] = None  # {rating: int, comment: str}
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def dialogue_content(self) -> str:
        """Only the owner's dialogue may use the full attachment-assisted reply."""
        return self.content if self.private_attachment_reply is None else self.private_attachment_reply

    class Settings:
        """Beanie settings."""

        name = "ai_messages"
        indexes = [
            [("conversation_id", 1)],
            [("created_at", 1)],
            [("conversation_id", 1), ("created_at", 1)],
        ]
