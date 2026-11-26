"""Background archive jobs for the teacher console (one deployed backend worker)."""

import asyncio
import logging
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

from app.core.datetime_utils import CHINA_TZ, ensure_aware_utc
from app.repositories.course import Course
from app.repositories.export_job import ACTIVE_EXPORT_STATES, TEACHER_COURSE_EXPORT, ExportJob
from app.repositories.user import User

logger = logging.getLogger(__name__)
EXPORT_DIR = Path(tempfile.gettempdir()) / "aiscl_teacher_exports"
EXPORT_LIFETIME = timedelta(days=7)
_jobs: set[asyncio.Task] = set()
_archive_slot = asyncio.Semaphore(1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def export_path(job: ExportJob) -> Path:
    return EXPORT_DIR / f"{job.id}.zip"


def is_downloadable(job: ExportJob) -> bool:
    return (
        job.status == "completed"
        and ensure_aware_utc(job.created_at) + EXPORT_LIFETIME > _now()
        and export_path(job).is_file()
    )


def _cleanup_old_archives() -> None:
    if not EXPORT_DIR.is_dir():
        return
    cutoff = time.time() - EXPORT_LIFETIME.total_seconds()
    try:
        for path in EXPORT_DIR.iterdir():
            if path.suffix in {".zip", ".tmp"} and path.is_file() and path.stat().st_mtime < cutoff:
                _remove_archive(path)
    except OSError:
        logger.warning("Unable to scan expired teacher exports", exc_info=True)


def _remove_archive(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Unable to remove teacher export archive %s", path.name, exc_info=True)


async def recover_interrupted_teacher_exports() -> None:
    # Existing Compose uses one backend worker; a restart loses its in-process queue.
    now = _now()
    await ExportJob.get_motor_collection().update_many(
        {"job_type": TEACHER_COURSE_EXPORT, "status": {"$in": ACTIVE_EXPORT_STATES}},
        {"$set": {"status": "failed", "message": "导出因服务重启中断，请重新生成。",
                  "updated_at": now, "completed_at": now}},
    )
    await run_in_threadpool(_cleanup_old_archives)


def queue_teacher_export(job: ExportJob) -> None:
    task = asyncio.create_task(_run_teacher_export(job))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)


async def stop_teacher_exports() -> None:
    tasks = tuple(_jobs)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _run_teacher_export(job: ExportJob) -> None:
    final_path = export_path(job)
    temp_path = final_path.with_suffix(".tmp")
    try:
        from app.services.teacher_course_export_archive import collect_teacher_export, write_teacher_export

        async with _archive_slot:
            course = await Course.get(job.course_id)
            teacher = await User.get(job.requested_by)
            if (not course or course.teacher_id != job.requested_by or not teacher
                    or teacher.role != "teacher" or not teacher.is_active or teacher.is_banned):
                raise ValueError("Course export authorization is no longer valid")

            job.status, job.progress, job.message = "running", 10, "正在读取选定班级的数据。"
            job.started_at = job.updated_at = _now()
            await job.save()
            data = await collect_teacher_export(
                course,
                project_ids=job.project_ids,
                profile=job.export_profile,
                from_date=date.fromisoformat(job.from_date) if job.from_date else None,
                through_date=date.fromisoformat(job.through_date) if job.through_date else None,
                stage_id=job.stage_id,
            )
            job.progress, job.message, job.updated_at = 55, "正在打包数据和附件。", _now()
            await job.save()
            await run_in_threadpool(_cleanup_old_archives)
            EXPORT_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            missing = await run_in_threadpool(
                write_teacher_export, str(temp_path), course, data,
                profile=job.export_profile, include_files=job.include_files,
            )
            os.replace(temp_path, final_path)
            job.status, job.progress = "completed", 100
            job.missing_files = missing
            job.message = "导出完成，可以下载。" if not missing else "导出完成，部分附件未能打包，请查看包内清单。"
            course_name = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in course.name)[:60].strip(". ")
            job.filename = f"AISCL-{job.export_profile}-{course_name or course.id}-{_now().astimezone(CHINA_TZ):%Y%m%d-%H%M%S}.zip"
            job.file_size = final_path.stat().st_size
            job.completed_at = job.updated_at = _now()
            await job.save()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Teacher course export failed: job=%s", job.id)
        _remove_archive(final_path)
        job.status, job.message = "failed", "导出失败，请重新生成；重复失败请联系平台管理员。"
        job.completed_at = job.updated_at = _now()
        try:
            await job.save()
        except Exception:
            logger.exception("Unable to persist failure for teacher export: job=%s", job.id)
    finally:
        _remove_archive(temp_path)
