"""Course schemas."""

from datetime import datetime
from typing import List, Literal, Optional, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class CourseAISettings(BaseModel):
    """Teacher-selected defaults for newly created course groups."""

    model_config = ConfigDict(extra="forbid")

    ai_scaffold_mode: Literal["single_agent", "multi_agent"]
    process_scaffold_mode: Literal["on", "off"]
    collaboration_memory_enabled: StrictBool
    auto_prompt_enabled: StrictBool

    @model_validator(mode="after")
    def validate_mode_dependencies(self) -> Self:
        if self.ai_scaffold_mode == "single_agent" and (
            self.collaboration_memory_enabled or self.auto_prompt_enabled
        ):
            raise ValueError("Collaboration memory and auto prompt require multi_agent mode")
        if self.auto_prompt_enabled and self.process_scaffold_mode == "off":
            raise ValueError("Auto prompt requires process scaffolding to be on")
        return self


class CourseAISettingsResponse(BaseModel):
    settings: Optional[CourseAISettings]


class CourseResponse(BaseModel):
    """Course response schema."""

    id: str
    name: str
    teacher_id: str
    semester: str
    invite_code: str
    students: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    experiment_template_key: Optional[str] = None
    experiment_template_label: Optional[str] = None
    experiment_template_release_id: Optional[str] = None
    experiment_template_release_note: Optional[str] = None
    experiment_template_source: Optional[str] = None
    experiment_template_bound_at: Optional[datetime] = None
    initial_task_document_title: Optional[str] = None
    initial_task_document_content: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        """Pydantic config."""

        from_attributes = True


class CourseCreateRequest(BaseModel):
    """Course create request schema."""

    name: str = Field(..., min_length=1, max_length=100)
    semester: str
    description: Optional[str] = None
    experiment_template_key: Optional[str] = None
    initial_task_document_title: Optional[str] = None
    initial_task_document_content: Optional[str] = None


class CourseUpdateRequest(BaseModel):
    """Course update request schema."""

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    semester: Optional[str] = Field(None, min_length=1)
    description: Optional[str] = None
    experiment_template_key: Optional[str] = None
    initial_task_document_title: Optional[str] = None
    initial_task_document_content: Optional[str] = None


class CourseJoinRequest(BaseModel):
    """Course join request schema."""

    invite_code: str = Field(..., min_length=6, max_length=6)


class CourseStudentImportItem(BaseModel):
    """One student row for teacher-side batch import."""

    username: str = Field(..., min_length=1, max_length=50)
    email: str = Field(..., min_length=3, max_length=254)
    password: Optional[str] = Field(None, min_length=6)


class CourseStudentImportRequest(BaseModel):
    """Batch student import request."""

    students: List[CourseStudentImportItem] = Field(..., min_length=1, max_length=500)
    default_password: str = Field(default="Password123!", min_length=6)


class CourseStudentImportRowResult(BaseModel):
    """Result for one imported student row."""

    row: int
    username: str
    email: str
    status: str
    message: str
    user_id: Optional[str] = None


class CourseStudentImportResponse(BaseModel):
    """Batch student import response."""

    created_count: int
    linked_count: int
    skipped_count: int
    failed_count: int
    results: List[CourseStudentImportRowResult]


class CourseListResponse(BaseModel):
    """Course list response schema."""

    courses: List[CourseResponse]
