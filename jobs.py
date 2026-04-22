from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from telegram.ext import Application, ContextTypes
import logging
import time
from Config import Config
from meta.errors import format_meta_publish_failure
from meta.publishers import publish_to_meta, publish_firestore_to_meta
from meta.publish_notifications import send_publish_report
from google_drive.archive import (
    persist_drive_upload_status,
    upload_video_to_linked_drive_folder,
)
import models

try:
    from google.cloud import firestore
except Exception:  # pragma: no cover - optional dependency in some installs.
    firestore = None


logger = logging.getLogger(__name__)
_poll_in_progress = False


def _lang_from_code(code: str | None):
    if not code:
        return models.Language.ARABIC
    c = code.strip().lower()
    if c.startswith("en"):
        return models.Language.ENGLISH
    return models.Language.ARABIC


def _to_utc_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _get_fire_schedule_time(payload: dict[str, Any]) -> datetime | None:
    for key in ("scheduledAt", "scheduled_at", "publishAt", "publish_at"):
        dt = _to_utc_dt(payload.get(key))
        if dt is not None:
            return dt
    return None


def _build_publish_payload_from_firestore(
    doc_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    media_url = (data.get("mediaUrl") or data.get("media_url") or "").strip()
    media_type = (data.get("mediaType") or data.get("media_type") or "").strip() or None
    post_type = (
        (data.get("postType") or data.get("post_type") or "feed").strip().lower()
    )
    platforms_raw = data.get("platforms") or []
    caption = (data.get("caption") or "").strip()
    scheduled_utc_dt = _get_fire_schedule_time(data)
    status = str(data.get("status") or "").strip().lower()
    schedule_mode = "now" if status in ("queued", "queued_now") else "schedule"
    payload: dict[str, Any] = {
        "source": "firestore",
        "firestore_doc_id": doc_id,
        "page_id": str(data.get("pageId") or data.get("page_id") or "").strip(),
        "page_access_token": str(
            data.get("pageAccessToken") or data.get("page_access_token") or ""
        ).strip(),
        "instagram_user_id": str(
            data.get("instagramUserId") or data.get("instagram_user_id") or ""
        ).strip(),
        "post_type": post_type if post_type in ("feed", "story", "reel") else "feed",
        "media_type": media_type,
        "caption": caption,
        "platforms": platforms_raw,
        "schedule_mode": schedule_mode,
        "scheduler_backend": "bot",
        "scheduled_utc_dt": scheduled_utc_dt,
        "scheduled_utc": scheduled_utc_dt.isoformat() if scheduled_utc_dt else None,
        "scheduled_local_text": str(data.get("scheduledLocalText") or ""),
        # publishers._download_telegram_file now supports URL fallback for bytes.
        "media_url": media_url or None,
        "media_file_id": None,
        "lang": _lang_from_code(str(data.get("lang") or data.get("language") or "ar")),
        "admin_id": data.get("createdBy") or data.get("created_by"),
        "page_name": data.get("pageName") or data.get("page_name"),
        "instagram_user_name": data.get("instagramUserName") or data.get("instagram_user_name"),
    }
    if media_url and media_type == "photo":
        # Avoid unnecessary upload-to-supabase branch for IG photo.
        payload["instagram_image_url"] = media_url
    return payload


def _normalize_admin_id_for_sql(payload: dict[str, Any]) -> int:
    """MetaPost.admin_id is mandatory FK to users.user_id in bot DB."""
    raw = payload.get("admin_id")
    try:
        candidate = int(raw)
    except (TypeError, ValueError):
        candidate = int(Config.OWNER_ID)

    with models.session_scope() as s:
        user = s.get(models.User, candidate)
        if user is not None:
            return candidate
    return int(Config.OWNER_ID)


def _platforms_csv(platforms_raw: Any) -> str:
    if platforms_raw is None:
        return ""
    if isinstance(platforms_raw, str):
        return platforms_raw
    if isinstance(platforms_raw, (list, tuple, set)):
        vals: list[str] = []
        for p in platforms_raw:
            t = str(p).strip().lower()
            if t:
                vals.append(t)
        return ",".join(vals)
    return str(platforms_raw)


def _create_local_meta_post_row(payload: dict[str, Any]) -> int | None:
    """
    Mirror Firestore scheduled jobs into the bot MetaPost table.
    Keeps lifecycle/status handling aligned with existing bot principles.
    """
    try:
        admin_id = _normalize_admin_id_for_sql(payload)
        with models.session_scope() as s:
            row = models.MetaPost(
                admin_id=admin_id,
                page_id=int(payload.get("page_id"))
                if str(payload.get("page_id") or "").isdigit()
                else None,
                page_name=str(payload.get("page_name") or "") or None,
                instagram_user_id=str(payload.get("instagram_user_id") or "") or None,
                instagram_user_name=str(payload.get("instagram_user_name") or "") or None,
                post_type=str(payload.get("post_type") or "feed"),
                media_type=str(payload.get("media_type") or "") or None,
                media_file_id=None,
                instagram_image_url=str(payload.get("instagram_image_url") or "") or None,
                caption=str(payload.get("caption") or ""),
                platforms=_platforms_csv(payload.get("platforms")),
                schedule_mode=str(payload.get("schedule_mode") or "schedule"),
                scheduled_utc_iso=str(payload.get("scheduled_utc") or "") or None,
                scheduled_local_text=f"firestore:{payload.get('firestore_doc_id')}",
                status="publishing",
                meta_response=None,
                last_error=None,
            )
            s.add(row)
            s.flush()
            return int(row.id)
    except Exception:
        logger.exception(
            "Failed to create local MetaPost mirror row: firestore_doc_id=%s",
            payload.get("firestore_doc_id"),
        )
        return None


def _is_due_for_publish(data: dict[str, Any], now_utc: datetime) -> bool:
    status = str(data.get("status") or "").strip().lower()
    if status in ("queued", "queued_now"):
        return True
    if status != "scheduled":
        return False
    scheduled = _get_fire_schedule_time(data)
    if scheduled is None:
        return False
    return scheduled <= now_utc


def _claim_due_doc(doc_ref, now_utc: datetime) -> dict[str, Any] | None:
    if firestore is None:
        return None

    transaction = doc_ref._client.transaction()

    @firestore.transactional
    def _tx_claim(tx):
        snap = doc_ref.get(transaction=tx)
        if not snap.exists:
            return None
        payload = snap.to_dict() or {}
        if not _is_due_for_publish(payload, now_utc):
            return None
        tx.update(
            doc_ref,
            {
                "status": "publishing",
                "publishStartedAt": firestore.SERVER_TIMESTAMP,
                "lastError": None,
            },
        )
        payload["status"] = "publishing"
        return payload

    return _tx_claim(transaction)


async def poll_firestore_scheduled_meta_posts(context: ContextTypes.DEFAULT_TYPE):
    global _poll_in_progress

    if firestore is None:
        logger.warning(
            "Firestore polling enabled but google-cloud-firestore is unavailable. "
            "Install dependency and restart."
        )
        return
    if _poll_in_progress:
        logger.warning(
            "Firestore poll tick skipped because previous run is still active."
        )
        return

    project_id = getattr(Config, "FIRESTORE_PROJECT_ID", None)
    collection_name = getattr(Config, "FIRESTORE_META_POSTS_COLLECTION", "meta_posts")
    batch_size = int(getattr(Config, "FIRESTORE_POLL_BATCH_SIZE", 20))
    client = firestore.Client(project=project_id) if project_id else firestore.Client()
    now_utc = datetime.now(timezone.utc)

    _poll_in_progress = True
    started_at = time.monotonic()
    processed = 0
    published = 0
    failed = 0
    try:
        docs = (
            client.collection(collection_name)
            .where("status", "in", ["scheduled", "queued", "queued_now"])
            .limit(batch_size)
            .stream()
        )

        for doc in docs:
            local_meta_post_id: int | None = None
            try:
                claimed_data = _claim_due_doc(doc.reference, now_utc)
                if not claimed_data:
                    continue
                processed += 1
                payload = _build_publish_payload_from_firestore(doc.id, claimed_data)
                local_meta_post_id = _create_local_meta_post_row(payload)
                result_text = await publish_firestore_to_meta(payload=payload)

                update_data = {
                    "status": "published",
                    "metaResponse": result_text,
                    "lastError": None,
                    "publishedAt": firestore.SERVER_TIMESTAMP,
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                }
                doc.reference.update(update_data)
                if local_meta_post_id:
                    with models.session_scope() as s:
                        row = s.get(models.MetaPost, local_meta_post_id)
                        if row:
                            row.status = "published"
                            row.meta_response = result_text
                            row.last_error = None
                published += 1
            except Exception as e:
                failed += 1
                lang = _lang_from_code(
                    str(
                        (claimed_data or {}).get("lang")
                        or (claimed_data or {}).get("language")
                        or "ar"
                    )
                )
                fail_text = format_meta_publish_failure(e, lang)
                doc.reference.update(
                    {
                        "status": "failed",
                        "lastError": f"{fail_text}\n\nraw_error: {str(e)}",
                        "updatedAt": firestore.SERVER_TIMESTAMP,
                    }
                )
                if local_meta_post_id:
                    with models.session_scope() as s:
                        row = s.get(models.MetaPost, local_meta_post_id)
                        if row:
                            row.status = "failed"
                            row.meta_response = None
                            row.last_error = str(e)
                logger.exception(
                    "Firestore scheduled publish failed: doc_id=%s error=%s", doc.id, e
                )
    except Exception as e:
        logger.exception("Firestore poll loop failed: %s", e)
    finally:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        if processed > 0 or failed > 0:
            logger.info(
                "Firestore poll done: processed=%s published=%s failed=%s elapsed_ms=%s",
                processed,
                published,
                failed,
                elapsed_ms,
            )
        _poll_in_progress = False


def register_firestore_polling_job(app: Application) -> None:
    if not getattr(Config, "FIRESTORE_POLLING_ENABLED", False):
        return
    interval = int(getattr(Config, "FIRESTORE_POLL_INTERVAL_SECONDS", 60))
    app.job_queue.run_repeating(
        callback=poll_firestore_scheduled_meta_posts,
        interval=interval,
        first=10,
        name="firestore_poll_scheduled_meta_posts",
        job_kwargs={
            "id": "firestore_poll_scheduled_meta_posts",
            "misfire_grace_time": None,
            "replace_existing": True,
        },
    )
    logger.info(
        "Registered Firestore scheduler poll job: every %ss collection=%s project=%s",
        interval,
        getattr(Config, "FIRESTORE_META_POSTS_COLLECTION", "meta_posts"),
        getattr(Config, "FIRESTORE_PROJECT_ID", None) or "<adc-default>",
    )


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
