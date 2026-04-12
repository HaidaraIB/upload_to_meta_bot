from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

import models
from common.common import format_datetime
from common.keyboards import build_admin_keyboard
from common.lang_dicts import BUTTONS, TEXTS
from google_drive.archive import persist_drive_upload_status
from meta.errors import format_meta_publish_failure
from meta.publish_notifications import send_publish_report
from meta.publishers import preflight_publish_payload, publish_to_meta

logger = logging.getLogger(__name__)


def _is_meta_native_supported(payload: dict) -> bool:
    """Meta-side ``scheduled_publish_time`` is enabled only for feed posts (IG + FB)."""
    return payload.get("post_type") == "feed"


# Public alias for handlers (same rules as _is_meta_native_supported).
meta_native_scheduling_supported = _is_meta_native_supported


async def schedule_with_bot_backend(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    payload: dict,
    lang,
    meta_post_id: int,
    user_id: int,
    chat_id: int,
) -> None:
    await preflight_publish_payload(payload=payload, context=context)

    from jobs import schedule_publish_to_meta

    scheduled_dt = payload["scheduled_utc_dt"]
    context.job_queue.run_once(
        callback=schedule_publish_to_meta,
        name="schedule_publish_to_meta",
        when=scheduled_dt,
        data={
            "payload": payload,
            "lang": lang,
            "meta_post_id": meta_post_id,
        },
        user_id=user_id,
        chat_id=chat_id,
        job_kwargs={"misfire_grace_time": None},
    )

    await send_publish_report(
        context,
        status="scheduled",
        meta_post_id=meta_post_id,
        payload=payload,
        meta_response=None,
    )


async def schedule_with_meta_backend(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    payload: dict,
    lang,
    meta_post_id: int,
) -> str | None:
    if not _is_meta_native_supported(payload):
        msg = TEXTS[lang]["meta_upload_meta_schedule_feed_only"]
        with models.session_scope() as s:
            meta_post = s.get(models.MetaPost, meta_post_id)
            if meta_post:
                meta_post.status = "failed"
                meta_post.meta_response = None
                meta_post.last_error = "meta_schedule_feed_only"
        payload.setdefault("_drive_archive_status", "not_attempted_meta_failed")
        persist_drive_upload_status(meta_post_id=meta_post_id, payload=payload)
        await send_publish_report(
            context,
            status="failed",
            meta_post_id=meta_post_id,
            payload=payload,
            last_error=msg,
        )
        await update.callback_query.edit_message_text(
            text=msg,
            reply_markup=build_admin_keyboard(
                lang, user_id=update.effective_user.id
            ),
        )
        return None

    payload["scheduler_backend"] = "meta"
    await preflight_publish_payload(payload=payload, context=context)
    try:
        result_text = await publish_to_meta(payload=payload, context=context)
        with models.session_scope() as s:
            meta_post = s.get(models.MetaPost, meta_post_id)
            if meta_post:
                meta_post.status = "scheduled"
                meta_post.meta_response = result_text
                meta_post.last_error = None

        await send_publish_report(
            context,
            status="scheduled",
            meta_post_id=meta_post_id,
            payload=payload,
            meta_response=result_text,
        )
        return TEXTS[lang]["meta_upload_scheduled_success_backend"].format(
            time=format_datetime(payload["scheduled_utc_dt"]),
            backend=BUTTONS[lang]["schedule_backend_meta"],
        )
    except Exception as e:
        logger.exception("Meta-native scheduling failed: %s", e)
        failure_text = format_meta_publish_failure(e, lang)
        payload.setdefault("_drive_archive_status", "not_attempted_meta_failed")

        with models.session_scope() as s:
            meta_post = s.get(models.MetaPost, meta_post_id)
            if meta_post:
                meta_post.status = "failed"
                meta_post.meta_response = None
                meta_post.last_error = str(e)
        persist_drive_upload_status(meta_post_id=meta_post_id, payload=payload)

        await send_publish_report(
            context,
            status="failed",
            meta_post_id=meta_post_id,
            payload=payload,
            last_error=f"{failure_text}\n\nraw_error: {str(e)}",
        )

        await update.callback_query.edit_message_text(
            text=failure_text,
            reply_markup=build_admin_keyboard(
                lang, user_id=update.effective_user.id
            ),
        )
        return None
