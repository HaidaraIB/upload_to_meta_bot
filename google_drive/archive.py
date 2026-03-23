from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Any

import models
from google_drive import google_drive_service

logger = logging.getLogger(__name__)


async def upload_video_to_linked_drive_folder(
    payload: dict[str, Any], video_bytes: bytes
) -> dict[str, Any] | None:
    """
    Upload video bytes to the active Drive folder linked to payload page_id.
    Returns uploaded metadata, or None if no linked folder exists.
    """
    page_id = str(payload.get("page_id") or "").strip()
    if not page_id:
        return None

    with models.session_scope() as s:
        drive_folder = (
            s.query(models.DriveFolder)
            .filter(
                models.DriveFolder.page_id == page_id,
                models.DriveFolder.is_active.is_(True),
            )
            .order_by(models.DriveFolder.id.desc())
            .first()
        )

    if not drive_folder:
        logger.info("Drive upload skipped: no linked folder for page_id=%s", page_id)
        return None

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(video_bytes)
            temp_path = Path(tmp.name)

        upload_result = await google_drive_service.upload_file_async(
            file_path=temp_path,
            folder_id=drive_folder.folder_id,
        )
        upload_result.setdefault("folder_id", drive_folder.folder_id)
        logger.info(
            "Drive upload success: page_id=%s folder_id=%s file_id=%s",
            page_id,
            drive_folder.folder_id,
            upload_result.get("id"),
        )
        upload_result.setdefault("name", f"meta_video_{page_id}_{int(time.time())}.mp4")
        return upload_result
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                logger.debug("Could not delete temp video file: %s", temp_path)


def persist_drive_upload_status(meta_post_id: int, payload: dict[str, Any]) -> None:
    """
    Upsert drive upload status record linked to a MetaPost.
    """
    status = payload.get("_drive_archive_status")
    if not status:
        # If no explicit status was set, keep a deterministic default.
        status = "skipped_not_video"

    with models.session_scope() as s:
        row = (
            s.query(models.DriveUpload)
            .filter(models.DriveUpload.meta_post_id == meta_post_id)
            .first()
        )
        if not row:
            row = models.DriveUpload(meta_post_id=meta_post_id)
            s.add(row)

        row.admin_id = payload.get("admin_id")
        row.page_id = str(payload.get("page_id")) if payload.get("page_id") else None
        row.page_name = payload.get("page_name")
        row.drive_folder_id = payload.get("_drive_archive_folder_id")
        row.drive_file_id = payload.get("_drive_archive_file_id")
        row.status = status
        row.error_detail = payload.get("_drive_archive_error")

