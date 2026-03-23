from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

import models
from common.lang_dicts import BUTTONS, TEXTS
from meta.publish_notifications import send_publish_report
from meta.publishers import preflight_publish_payload, publish_to_meta

logger = logging.getLogger(__name__)


def _is_meta_native_supported(payload: dict) -> bool:
    post_type = payload.get("post_type")
    # Keep test mode safe: only feed scheduling is delegated to Meta.
    return post_type == "feed"


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


async def schedule_with_meta_backend(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    payload: dict,
    lang,
    meta_post_id: int,
) -> str:
    await preflight_publish_payload(payload=payload, context=context)

    if not _is_meta_native_supported(payload):
        payload["scheduler_backend"] = "bot"
        await schedule_with_bot_backend(
            context,
            payload=payload,
            lang=lang,
            meta_post_id=meta_post_id,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
        )
        return TEXTS[lang]["meta_upload_scheduled_success_backend"].format(
            time=payload.get("scheduled_utc"),
            backend=BUTTONS[lang]["schedule_backend_bot"],
        )

    payload["scheduler_backend"] = "meta"
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
            status="published",
            meta_post_id=meta_post_id,
            payload=payload,
            meta_response=result_text,
        )
        return TEXTS[lang]["meta_upload_scheduled_success_backend"].format(
            time=payload.get("scheduled_utc"),
            backend=BUTTONS[lang]["schedule_backend_meta"],
        )
    except Exception as e:
        logger.exception("Meta-native scheduling failed: %s", e)
        await update.callback_query.edit_message_text(
            TEXTS[lang]["meta_upload_schedule_fallback_to_bot"],
        )
        payload["scheduler_backend"] = "bot"
        await schedule_with_bot_backend(
            context,
            payload=payload,
            lang=lang,
            meta_post_id=meta_post_id,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
        )
        return TEXTS[lang]["meta_upload_scheduled_success_backend"].format(
            time=payload.get("scheduled_utc"),
            backend=BUTTONS[lang]["schedule_backend_bot"],
        )
