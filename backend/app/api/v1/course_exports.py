"""Class-scoped export tasks and browser-native downloads for teachers."""

import hashlib
import hmac
import re
import time
from datetime import date, datetime, timezone
from typing import Literal

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo.errors import DuplicateKeyError

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.datetime_utils import utc_isoformat
from app.core.security import content_disposition_header
from app.core.schemas.learning_design import StageId
from app.repositories.course import Course
from app.repositories.export_job import TEACHER_COURSE_EXPORT, ExportJob
from app.repositories.project import Project
from app.repositories.user import User
from app.services.teacher_course_exports import export_path, is_downloadable, queue_teacher_export

router = APIRouter(prefix="/courses", tags=["course-exports"])
DOWNLOAD_COOKIE = "aiscl_teacher_export"
DOWNLOAD_TTL_SECONDS = 90
TOKEN_PATTERN = re.compile(r"([0-9]{1,12})\.([a-f0-9]{64})")


class TeacherExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["participation", "artifacts", "research"]
    project_ids: list[PydanticObjectId] | None = Field(default=None, min_length=1, max_length=100)
    from_date: date | None = Field(default=None, ge=date(1900, 1, 1), le=date(9998, 12, 31))
    through_date: date | None = Field(default=None, ge=date(1900, 1, 1), le=date(9998, 12, 31))
    stage_id: StageId | None = None
    include_files: bool = False

    @model_validator(mode="after")
    def validate_selection(self) -> "TeacherExportRequest":
        if self.from_date and self.through_date and self.from_date > self.through_date:
            raise ValueError("开始日期不能晚于结束日期。")
        if self.stage_id and self.profile != "research":
            raise ValueError("仅研究过程包支持阶段筛选。")
        if self.include_files and self.profile != "artifacts":
            raise ValueError("仅协作成果包可包含附件原件。")
        return self


async def _owned_course(course_id: PydanticObjectId, teacher: User) -> Course:
    if teacher.role != "teacher" or not teacher.is_active or teacher.is_banned:
        raise HTTPException(status_code=403, detail="仅任课教师可导出班级数据。")
    course = await Course.get(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="找不到班级。")
    if course.teacher_id != str(teacher.id):
        raise HTTPException(status_code=403, detail="仅任课教师可导出班级数据。")
    return course


def _job_filter(course_id: PydanticObjectId, teacher: User) -> dict:
    return {"course_id": str(course_id), "requested_by": str(teacher.id), "job_type": TEACHER_COURSE_EXPORT}


async def _owned_job(course_id: PydanticObjectId, job_id: PydanticObjectId, teacher: User) -> ExportJob:
    await _owned_course(course_id, teacher)
    job = await ExportJob.find_one({**_job_filter(course_id, teacher), "_id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="找不到该导出任务。")
    return job


def _job_response(job: ExportJob) -> dict:
    expired = job.status == "completed" and not is_downloadable(job)
    return {
        "id": str(job.id),
        "profile": job.export_profile,
        "status": "expired" if expired else job.status,
        "course_id": job.course_id,
        "project_ids": job.project_ids,
        "from_date": job.from_date,
        "through_date": job.through_date,
        "stage_id": job.stage_id,
        "include_files": job.include_files,
        "progress": job.progress,
        "message": "下载文件已过期或已被清理，请重新生成。" if expired else job.message,
        "filename": job.filename,
        "file_size": job.file_size,
        "missing_files": job.missing_files,
        "created_at": utc_isoformat(job.created_at),
    }


def _download_mac(job: ExportJob, expires: int) -> str:
    message = f"aiscl-teacher-download:v1:{job.id}:{job.course_id}:{job.requested_by}:{expires}"
    return hmac.new(settings.SECRET_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()


def _download_path(course_id: PydanticObjectId, job_id: PydanticObjectId) -> str:
    return f"/api/v1/courses/{course_id}/export-jobs/{job_id}/download"


@router.get("/{course_id}/export-groups")
async def get_export_groups(course_id: PydanticObjectId, current_user: User = Depends(get_current_user)) -> dict:
    await _owned_course(course_id, current_user)
    groups = await Project.find({"course_id": str(course_id), "is_template": {"$ne": True}}).sort("name").to_list()
    return {"groups": [{"id": str(group.id), "name": group.name, "is_archived": group.is_archived} for group in groups]}


@router.get("/{course_id}/export-jobs")
async def list_export_jobs(course_id: PydanticObjectId, current_user: User = Depends(get_current_user)) -> dict:
    await _owned_course(course_id, current_user)
    jobs = await ExportJob.find(_job_filter(course_id, current_user)).sort("-created_at", "-_id").limit(10).to_list()
    return {"jobs": [_job_response(job) for job in jobs]}


@router.post("/{course_id}/export-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_export_job(
    course_id: PydanticObjectId, selection: TeacherExportRequest, current_user: User = Depends(get_current_user),
) -> dict:
    course = await _owned_course(course_id, current_user)
    query = {"course_id": str(course_id), "is_template": {"$ne": True}}
    selected_ids = set(selection.project_ids or [])
    if selected_ids:
        query["_id"] = {"$in": list(selected_ids)}
    groups = await Project.find(query).sort("name").to_list()
    if selected_ids and selected_ids != {group.id for group in groups}:
        raise HTTPException(status_code=422, detail="选择的小组有不属于该班级或已不存在的记录。")

    now = datetime.now(timezone.utc)
    now = now.replace(microsecond=now.microsecond // 1000 * 1000)
    job = ExportJob(
        job_type=TEACHER_COURSE_EXPORT,
        course_id=str(course_id),
        course_name=course.name,
        requested_by=str(current_user.id),
        export_profile=selection.profile,
        project_ids=[str(group.id) for group in groups],
        from_date=selection.from_date.isoformat() if selection.from_date else None,
        through_date=selection.through_date.isoformat() if selection.through_date else None,
        stage_id=selection.stage_id,
        include_files=selection.include_files,
        message="导出任务已创建，等待后台生成。",
        created_at=now,
        updated_at=now,
    )
    try:
        await job.insert()
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="已有导出任务正在进行，请等待完成后再生成。") from exc
    queue_teacher_export(job)
    return _job_response(job)


@router.get("/{course_id}/export-jobs/{job_id}")
async def get_export_job(
    course_id: PydanticObjectId, job_id: PydanticObjectId, current_user: User = Depends(get_current_user),
) -> dict:
    return _job_response(await _owned_job(course_id, job_id, current_user))


@router.post("/{course_id}/export-jobs/{job_id}/download-ticket")
async def issue_download_ticket(
    course_id: PydanticObjectId, job_id: PydanticObjectId, request: Request, response: Response,
    current_user: User = Depends(get_current_user),
) -> dict:
    job = await _owned_job(course_id, job_id, current_user)
    if not is_downloadable(job):
        raise HTTPException(status_code=409, detail="下载文件尚未就绪或已过期。")
    expires = int(time.time()) + DOWNLOAD_TTL_SECONDS
    path = _download_path(course_id, job_id)
    response.set_cookie(
        DOWNLOAD_COOKIE, f"{expires}.{_download_mac(job, expires)}", max_age=DOWNLOAD_TTL_SECONDS,
        path=path, httponly=True, samesite="strict",
        secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"download_url": path, "expires_at": utc_isoformat(datetime.fromtimestamp(expires, timezone.utc))}


@router.get("/{course_id}/export-jobs/{job_id}/download")
async def download_export(course_id: PydanticObjectId, job_id: PydanticObjectId, request: Request) -> FileResponse:
    ticket = TOKEN_PATTERN.fullmatch(request.cookies.get(DOWNLOAD_COOKIE) or "")
    if not ticket or int(ticket[1]) <= int(time.time()):
        raise HTTPException(status_code=403, detail="下载凭证无效或已过期，请返回班级重新下载。")
    job = await ExportJob.find_one({"_id": job_id, "course_id": str(course_id), "job_type": TEACHER_COURSE_EXPORT})
    if not job or not hmac.compare_digest(ticket[2], _download_mac(job, int(ticket[1]))):
        raise HTTPException(status_code=403, detail="下载凭证无效或已过期，请返回班级重新下载。")
    teacher = await User.get(job.requested_by)
    if not teacher:
        raise HTTPException(status_code=403, detail="账号不可用。")
    await _owned_course(course_id, teacher)
    if not is_downloadable(job):
        raise HTTPException(status_code=409, detail="下载文件尚未就绪或已过期。")
    return FileResponse(
        export_path(job), media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition_header(job.filename or "AISCL-export.zip"),
            "Cache-Control": "private, no-store",
        },
    )
