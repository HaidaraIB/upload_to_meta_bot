from telegram.ext import ContextTypes
import logging
import time
from meta.errors import format_meta_publish_failure
from meta.publishers import publish_to_meta
from meta.publish_notifications import send_publish_report
from google_drive.archive import (
    persist_drive_upload_status,
    upload_video_to_linked_drive_folder,
)
import models

logger = logging.getLogger(__name__)


async def schedule_publish_to_meta(context: ContextTypes.DEFAULT_TYPE):
    meta_post_id = context.job.data.get("meta_post_id")
    lang = context.job.data.get("lang") or models.Language.ARABIC
    job_payload = context.job.data.get("payload") or {}

    page_id = job_payload.get("page_id")
    post_type = job_payload.get("post_type")
    platforms = job_payload.get("platforms") or []
    media_type = job_payload.get("media_type")
    caption_len = len(job_payload.get("caption") or "")

    started_at = time.monotonic()
    logger.info(
        "Scheduled publish start: meta_post_id=%s page_id=%s post_type=%s platforms=%s media_type=%s caption_len=%s lang=%s",
        meta_post_id,
        page_id,
        post_type,
        platforms,
        media_type,
        caption_len,
        getattr(lang, "value", lang),
    )

    try:
        result_text = await publish_to_meta(
            payload=context.job.data["payload"], context=context
        )
        video_bytes = context.job.data["payload"].pop("_post_publish_video_bytes", None)
        if video_bytes:
            try:
                drive_result = await upload_video_to_linked_drive_folder(
                    context.job.data["payload"], video_bytes
                )
                if drive_result:
                    context.job.data["payload"]["_drive_archive_status"] = "success"
                    context.job.data["payload"]["_drive_archive_file_id"] = (
                        drive_result.get("id")
                    )
                    context.job.data["payload"]["_drive_archive_folder_id"] = (
                        drive_result.get("folder_id")
                    )
                    logger.info(
                        "Drive archival success (scheduled): meta_post_id=%s file_id=%s",
                        meta_post_id,
                        drive_result.get("id"),
                    )
                else:
                    context.job.data["payload"][
                        "_drive_archive_status"
                    ] = "skipped_no_link"
                    logger.info(
                        "Drive archival skipped (scheduled): meta_post_id=%s no linked folder",
                        meta_post_id,
                    )
            except Exception:
                context.job.data["payload"]["_drive_archive_status"] = "failed"
                context.job.data["payload"][
                    "_drive_archive_error"
                ] = "drive_upload_failed"
                logger.exception(
                    "Drive archival failed after scheduled publish: meta_post_id=%s",
                    meta_post_id,
                )

        if meta_post_id:
            with models.session_scope() as s:
                meta_post = s.get(models.MetaPost, meta_post_id)
                if meta_post:
                    meta_post.status = "published"
                    meta_post.meta_response = result_text
                    meta_post.last_error = None
        if meta_post_id:
            persist_drive_upload_status(
                meta_post_id=meta_post_id, payload=context.job.data["payload"]
            )

        await context.bot.send_message(
            chat_id=context.job.user_id,
            text=result_text,
        )

        await send_publish_report(
            context,
            status="published",
            meta_post_id=meta_post_id,
            payload=context.job.data["payload"],
            meta_response=result_text,
        )

        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.info(
            "Scheduled publish success: meta_post_id=%s duration_ms=%s",
            meta_post_id,
            duration_ms,
        )
    except Exception as e:
        duration_ms = int((time.monotonic() - started_at) * 1000)
        logger.exception(
            "Scheduled publish failed: meta_post_id=%s duration_ms=%s error=%s",
            meta_post_id,
            duration_ms,
            e,
        )
        context.job.data["payload"].setdefault(
            "_drive_archive_status", "not_attempted_meta_failed"
        )
        if meta_post_id:
            with models.session_scope() as s:
                meta_post = s.get(models.MetaPost, meta_post_id)
                if meta_post:
                    meta_post.status = "failed"
                    meta_post.meta_response = None
                    meta_post.last_error = str(e)
            persist_drive_upload_status(
                meta_post_id=meta_post_id, payload=context.job.data["payload"]
            )

        failure_text = format_meta_publish_failure(e, lang)

        await send_publish_report(
            context,
            status="failed",
            meta_post_id=meta_post_id,
            payload=context.job.data["payload"],
            last_error=f"{failure_text}\n\nraw_error: {str(e)}",
        )

        await context.bot.send_message(
            chat_id=context.job.user_id,
            text=failure_text,
        )
