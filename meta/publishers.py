from __future__ import annotations

from io import BytesIO
from typing import Any
import logging
import time
import uuid
from datetime import datetime, timezone
import aiohttp
import models
from telegram.error import TelegramError
from Config import Config
from common.lang_dicts import TEXTS
from meta.errors import MetaPublishUserError, graph_error_detail
from meta.graph_client import _graph_request
from meta.ig_video_preflight import instagram_video_binary_preflight
from meta.supabase_storage import upload_bytes_public_url
from meta.video_normalizer import normalize_instagram_video_bytes

logger = logging.getLogger(__name__)


def _platform_error_text(exc: BaseException, max_len: int = 280) -> str:
    s = str(exc)
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def _init_publish_platform_results(payload: dict[str, Any]) -> None:
    """Track per-platform outcome for publish channel reports (instagram + facebook)."""
    platforms = set(payload.get("platforms") or [])
    payload["_publish_platform_results"] = {}
    for key in ("instagram", "facebook"):
        if key in platforms:
            payload["_publish_platform_results"][key] = {"outcome": "pending"}
        else:
            payload["_publish_platform_results"][key] = {"outcome": "not_selected"}


def _mark_pending_not_attempted_pre_publish(payload: dict[str, Any]) -> None:
    r = payload.get("_publish_platform_results")
    if not r:
        return
    for key in ("instagram", "facebook"):
        entry = r.get(key)
        if isinstance(entry, dict) and entry.get("outcome") == "pending":
            r[key] = {
                "outcome": "not_attempted",
                "reason": "pre_publish_failed",
            }


def _max_telegram_media_bytes() -> int:
    return int(getattr(Config, "TELEGRAM_MEDIA_MAX_BYTES", 200 * 1024 * 1024))


def _prepare_instagram_video_bytes(video_bytes: bytes, post_type: str) -> bytes:
    """
    Normalize known MP4 layout issues for Instagram, then re-run strict preflight.
    """
    prepared = normalize_instagram_video_bytes(video_bytes)
    if prepared.changed:
        logger.info("Instagram video normalized before publish: method=%s", prepared.method)
    instagram_video_binary_preflight(prepared.video_bytes, post_type)
    return prepared.video_bytes


def _meta_schedule_unix(payload: dict[str, Any]) -> int | None:
    if payload.get("scheduler_backend") != "meta":
        return None
    scheduled_dt = payload.get("scheduled_utc_dt")
    if not isinstance(scheduled_dt, datetime):
        return None
    return int(scheduled_dt.astimezone(timezone.utc).timestamp())


async def _edit_publish_progress_message(
    context,
    payload: dict[str, Any],
    text_key: str,
) -> None:
    """Same chat/message as the initial «publishing now» edit (dual-platform flow)."""
    spec = payload.get("publish_progress_edit")
    if not spec or context is None:
        return
    chat_id = spec.get("chat_id")
    message_id = spec.get("message_id")
    if chat_id is None or message_id is None:
        return
    lang = payload.get("lang", models.Language.ARABIC)
    text = TEXTS[lang][text_key]
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
        )
    except TelegramError as e:
        logger.debug("Publish progress message edit skipped: %s", e)


async def _notify_telethon_download_queue(context, payload: dict[str, Any] | None) -> None:
    """Tell the admin we're waiting on the shared Telethon download lock."""
    if context is None or not payload:
        return
    admin_id = payload.get("admin_id")
    if admin_id is None:
        job = getattr(context, "job", None)
        if job is not None:
            admin_id = getattr(job, "user_id", None)
    if admin_id is None:
        return
    lang = payload.get("lang", models.Language.ARABIC)
    text = TEXTS[lang]["meta_telethon_download_queue"]
    try:
        await context.bot.send_message(chat_id=int(admin_id), text=text)
    except Exception as e:
        logger.debug("Telethon queue notice not sent: %s", e)


async def _download_via_telethon(
    context,
    payload: dict[str, Any] | None,
    tg_chat_id: int,
    tg_message_id: int,
    max_bytes: int,
) -> bytes:
    """Download via MTProto (Telethon); supports files far above Bot API getFile ~20MB cap."""
    from TeleClientSingleton import TeleClientSingleton

    lock = TeleClientSingleton.download_lock
    if lock.locked():
        await _notify_telethon_download_queue(context, payload)

    async with lock:
        logger.debug(
            "Telethon download started (serialized): chat_id=%s message_id=%s",
            tg_chat_id,
            tg_message_id,
        )
        client = await TeleClientSingleton.get_client()
        msg = await client.get_messages(tg_chat_id, ids=tg_message_id)
        if isinstance(msg, list):
            msg = msg[0] if msg else None
        if msg is None:
            raise RuntimeError("telegram_message_not_found")

        doc = getattr(msg, "document", None)
        if doc is not None:
            sz = getattr(doc, "size", None)
            if sz is not None and sz > max_bytes:
                raise MetaPublishUserError(
                    "meta_err_telegram_media_too_large",
                    max_mb=max_bytes // (1024 * 1024),
                )

        buf = BytesIO()
        await client.download_media(msg, file=buf)
        data = buf.getvalue()
        if len(data) > max_bytes:
            raise MetaPublishUserError(
                "meta_err_telegram_media_too_large",
                max_mb=max_bytes // (1024 * 1024),
            )
        logger.debug(
            "Telethon download finished: chat_id=%s message_id=%s bytes=%s",
            tg_chat_id,
            tg_message_id,
            len(data),
        )
        return data


async def _download_telegram_file(context, payload: dict[str, Any]) -> bytes:
    media_file_id = payload.get("media_file_id")
    max_bytes = _max_telegram_media_bytes()
    chat_id = payload.get("source_chat_id")
    message_id = payload.get("source_message_id")

    if chat_id is not None and message_id is not None:
        try:
            data = await _download_via_telethon(
                context, payload, int(chat_id), int(message_id), max_bytes
            )
            logger.debug(
                "Telethon download ok: chat_id=%s message_id=%s bytes=%s",
                chat_id,
                message_id,
                len(data),
            )
            return data
        except MetaPublishUserError:
            raise
        except Exception as e:
            logger.warning(
                "Telethon download failed (chat_id=%s message_id=%s): %s; trying Bot API",
                chat_id,
                message_id,
                e,
            )

    if not media_file_id:
        raise MetaPublishUserError(
            "meta_err_telegram_download",
            detail="missing media_file_id",
        )

    logger.debug("Downloading Telegram file via Bot API: file_id=%s", media_file_id)
    tg_file = await context.bot.get_file(media_file_id)
    data = await tg_file.download_as_bytearray()
    b = bytes(data)
    if len(b) > max_bytes:
        raise MetaPublishUserError(
            "meta_err_telegram_media_too_large",
            max_mb=max_bytes // (1024 * 1024),
        )
    logger.debug(
        "Downloaded Telegram file via Bot API: file_id=%s bytes=%s", media_file_id, len(b)
    )
    return b


async def _upload_to_rupload(
    session: aiohttp.ClientSession,
    upload_url: str,
    file_bytes: bytes,
    *,
    access_token: str | None = None,
) -> None:
    # Avoid logging full URL (can include query params). Log only host and size.
    from urllib.parse import urlparse

    host = urlparse(upload_url).netloc or upload_url
    logger.debug(
        "Uploading to rupload: host=%s bytes=%s", host, len(file_bytes)
    )
    # Some Facebook upload endpoints (rupload) may require OAuth auth header.
    headers = {
        "Authorization": f"OAuth {access_token or Config.META_ACCESS_TOKEN}",
        "offset": "0",
        "file_size": str(len(file_bytes)),
        "Content-Type": "application/octet-stream",
    }
    async with session.post(upload_url, headers=headers, data=file_bytes) as resp:
        if resp.status >= 400:
            body = await resp.text()
            logger.warning(
                "rupload upload failed: host=%s status=%s detail=%s",
                host,
                resp.status,
                (body or "")[:250],
            )
            raise MetaPublishUserError(
                "meta_err_upload",
                status=resp.status,
                detail=(body or "")[:400],
            )


async def _ig_create_container(
    session: aiohttp.ClientSession,
    ig_user_id: str,
    media_type: str | None,
    caption: str | None,
    upload_type: str | None = None,
    image_url: str | None = None,
    *,
    schedule_unix: int | None = None,
    publish_now: bool = True,
    access_token: str | None = None,
) -> str:
    logger.debug(
        "IG create container: ig_user_id=%s media_type=%s upload_type=%s has_image_url=%s",
        ig_user_id,
        media_type,
        upload_type,
        bool(image_url),
    )
    params: dict[str, Any] = {}
    if media_type:
        params["media_type"] = media_type
    if upload_type:
        params["upload_type"] = upload_type
    if caption:
        params["caption"] = caption
    if image_url:
        params["image_url"] = image_url
    if not publish_now:
        params["published"] = "false"
    if schedule_unix is not None:
        params["scheduled_publish_time"] = str(schedule_unix)

    body = await _graph_request(
        session,
        "POST",
        f"/{ig_user_id}/media",
        params=params,
        access_token=access_token,
    )
    creation_id = body.get("id")
    if not creation_id:
        raise MetaPublishUserError(
            "meta_err_ig_container",
            detail=graph_error_detail(body),
        )
    return str(creation_id)


async def _ig_upload_and_publish_video_resumable(
    session: aiohttp.ClientSession,
    ig_user_id: str,
    creation_id: str,
    video_bytes: bytes,
    *,
    schedule_unix: int | None = None,
    publish_now: bool = True,
    access_token: str | None = None,
) -> dict[str, Any]:
    token = access_token or Config.META_ACCESS_TOKEN
    upload_url = f"{Config.RUUPLOAD_BASE}/ig-api-upload/{Config.META_GRAPH_VERSION}/{creation_id}"
    logger.debug(
        "IG resumable upload: ig_user_id=%s creation_id=%s bytes=%s",
        ig_user_id,
        creation_id,
        len(video_bytes),
    )
    headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(len(video_bytes)),
        "Content-Type": "application/octet-stream",
    }
    async with session.post(upload_url, headers=headers, data=video_bytes) as resp:
        if resp.status >= 400:
            body = await resp.text()
            raise MetaPublishUserError(
                "meta_err_ig_resumable_upload",
                status=resp.status,
                detail=(body or "")[:400],
            )
        logger.info(
            "IG rupload ok: creation_id=%s status=%s bytes=%s",
            creation_id,
            resp.status,
            len(video_bytes),
        )

    body = await _graph_request(
        session,
        "POST",
        f"/{ig_user_id}/media_publish",
        params={
            "creation_id": creation_id,
            "published": "true" if publish_now else "false",
            **(
                {"scheduled_publish_time": str(schedule_unix)}
                if schedule_unix is not None
                else {}
            ),
        },
        access_token=access_token,
    )
    return body


def _validate_publish_payload_rules(payload: dict[str, Any]) -> None:
    platforms: list[str] = payload.get("platforms") or []
    post_type = payload["post_type"]
    media_type = payload.get("media_type")
    caption = payload.get("caption") or ""
    ig_user_id = str(payload.get("instagram_user_id") or "")

    if not platforms:
        raise MetaPublishUserError("meta_err_no_platforms")
    if post_type == "reel" and media_type != "video":
        raise MetaPublishUserError("meta_err_reel_requires_video")
    if post_type == "story" and not media_type:
        raise MetaPublishUserError("meta_err_story_requires_media")
    if post_type == "feed" and not media_type and "instagram" in platforms:
        raise MetaPublishUserError("meta_err_instagram_no_text_only")
    if "instagram" in platforms and not ig_user_id:
        raise MetaPublishUserError("meta_err_missing_ig_user_id")
    if (
        "facebook" in platforms
        and post_type == "feed"
        and not media_type
        and not caption
    ):
        raise MetaPublishUserError("meta_err_fb_text_requires_caption")


async def preflight_publish_payload(payload: dict[str, Any], context) -> None:
    """
    Validate payload before scheduling without creating/publishing any post.
    Raises MetaPublishUserError on predictable failures.
    """
    _validate_publish_payload_rules(payload)

    platforms: list[str] = payload.get("platforms") or []
    page_id = str(payload["page_id"])
    post_type = payload["post_type"]
    media_type = payload.get("media_type")
    media_file_id = payload.get("media_file_id")
    ig_user_id = str(payload.get("instagram_user_id") or "")
    supabase_configured = all(
        [
            getattr(Config, "SUPABASE_URL", None),
            getattr(Config, "SUPABASE_SERVICE_ROLE_KEY", None),
            getattr(Config, "SUPABASE_STORAGE_BUCKET", None),
        ]
    )
    graph_token = payload.get("page_access_token") or Config.META_ACCESS_TOKEN

    need_video_bytes = media_type == "video" and (
        ("instagram" in platforms and post_type in ("reel", "story", "feed"))
        or ("facebook" in platforms and post_type in ("reel", "story", "feed"))
    )
    need_instagram_photo_bytes = (
        media_type == "photo"
        and post_type in ("feed", "story")
        and "instagram" in platforms
        and not payload.get("instagram_image_url")
        and bool(media_file_id)
        and supabase_configured
    )
    need_photo_bytes = media_type == "photo" and (
        ("facebook" in platforms and post_type in ("feed", "story"))
        or need_instagram_photo_bytes
    )

    timeout = aiohttp.ClientTimeout(
        total=getattr(Config, "META_HTTP_TIMEOUT_TOTAL", 600),
        sock_read=getattr(Config, "META_HTTP_TIMEOUT_TOTAL", 600),
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Safe remote checks: verify token/page access and IG visibility.
        await _graph_request(
            session,
            "GET",
            f"/{page_id}",
            params={"fields": "id"},
            access_token=graph_token,
        )
        if "instagram" in platforms:
            await _graph_request(
                session,
                "GET",
                f"/{ig_user_id}",
                params={"fields": "id"},
                access_token=graph_token,
            )

        video_bytes = None
        photo_bytes = None
        if need_video_bytes:
            video_bytes = await _download_telegram_file(context, payload)
        if need_photo_bytes:
            photo_bytes = await _download_telegram_file(context, payload)

        if (
            "instagram" in platforms
            and media_type == "video"
            and post_type in ("reel", "story", "feed")
        ):
            if video_bytes is None:
                raise MetaPublishUserError("meta_err_ig_missing_video_bytes")
            video_bytes = _prepare_instagram_video_bytes(video_bytes, post_type)

        if "instagram" in platforms and media_type == "photo":
            if not payload.get("instagram_image_url") and not (
                need_instagram_photo_bytes and photo_bytes is not None
            ):
                raise MetaPublishUserError("meta_err_ig_missing_image_url")


async def publish_to_meta(payload: dict[str, Any], context) -> str:
    """
    Returns an Arabic/English message text for the admin.
    """
    platforms: list[str] = payload.get("platforms") or []
    page_id = str(payload["page_id"])
    post_type = payload["post_type"]  # reel|story|feed (feed post)
    caption = payload.get("caption") or ""
    media_type = payload.get("media_type")
    media_file_id = payload.get("media_file_id")
    ig_user_id = str(payload.get("instagram_user_id") or "")
    admin_id = payload.get("admin_id")
    supabase_configured = all(
        [
            getattr(Config, "SUPABASE_URL", None),
            getattr(Config, "SUPABASE_SERVICE_ROLE_KEY", None),
            getattr(Config, "SUPABASE_STORAGE_BUCKET", None),
        ]
    )

    _validate_publish_payload_rules(payload)
    _init_publish_platform_results(payload)
    schedule_unix = _meta_schedule_unix(payload)
    publish_now = schedule_unix is None
    graph_token = payload.get("page_access_token") or Config.META_ACCESS_TOKEN

    # Determine what we need to download from Telegram.
    need_video_bytes = media_type == "video" and (
        ("instagram" in platforms and post_type in ("reel", "story", "feed"))
        or ("facebook" in platforms and post_type in ("reel", "story", "feed"))
    )
    need_instagram_photo_bytes = (
        media_type == "photo"
        and post_type in ("feed", "story")
        and "instagram" in platforms
        and not payload.get("instagram_image_url")
        and bool(media_file_id)
        and supabase_configured
    )
    need_photo_bytes = media_type == "photo" and (
        ("facebook" in platforms and post_type in ("feed", "story"))
        or need_instagram_photo_bytes
    )
    logger.info(
        "publish_to_meta start: admin_id=%s page_id=%s post_type=%s platforms=%s media_type=%s caption_len=%s",
        admin_id,
        page_id,
        post_type,
        platforms,
        media_type,
        len(caption) if caption else 0,
    )
    logger.debug(
        "publish_to_meta download plan: need_video=%s need_photo=%s has_media_file_id=%s",
        need_video_bytes,
        need_photo_bytes,
        bool(media_file_id),
    )

    timeout = aiohttp.ClientTimeout(
        total=getattr(Config, "META_HTTP_TIMEOUT_TOTAL", 600),
        sock_read=getattr(Config, "META_HTTP_TIMEOUT_TOTAL", 600),
    )
    platforms_set = set(platforms)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        video_bytes = None
        photo_bytes = None

        try:
            if need_video_bytes:
                video_bytes = await _download_telegram_file(context, payload)
            if need_photo_bytes:
                photo_bytes = await _download_telegram_file(context, payload)
        except Exception:
            _mark_pending_not_attempted_pre_publish(payload)
            raise

        results: list[str] = []

        if need_instagram_photo_bytes and photo_bytes is not None:
            try:
                # Upload to Supabase so Instagram can fetch it via a public URL.
                object_path = (
                    f"ig_uploads/{admin_id or 'unknown'}/"
                    f"{int(time.time())}_{uuid.uuid4().hex}.jpg"
                )
                logger.info("Uploading IG photo bytes to Supabase: path=%s", object_path)
                payload["instagram_image_url"] = await upload_bytes_public_url(
                    session=session,
                    bucket=Config.SUPABASE_STORAGE_BUCKET,
                    object_path=object_path,
                    content_type="image/jpeg",
                    file_bytes=photo_bytes,
                )
                logger.info("Supabase image_url ready for Instagram.")
            except Exception:
                _mark_pending_not_attempted_pre_publish(payload)
                raise

        if "instagram" in platforms:
            logger.info(
                "Publishing on Instagram: post_type=%s media_type=%s", post_type, media_type
            )
            try:
                ig_text = await _publish_instagram(
                    session,
                    ig_user_id,
                    post_type,
                    caption,
                    media_type,
                    video_bytes,
                    payload,
                    schedule_unix=schedule_unix,
                    publish_now=publish_now,
                    access_token=graph_token,
                )
                results.append(ig_text)
                payload["_publish_platform_results"]["instagram"] = {"outcome": "success"}
            except Exception as e:
                payload["_publish_platform_results"]["instagram"] = {
                    "outcome": "failed",
                    "error": _platform_error_text(e),
                }
                if (
                    "facebook" in platforms_set
                    and payload["_publish_platform_results"]
                    .get("facebook", {})
                    .get("outcome")
                    == "pending"
                ):
                    payload["_publish_platform_results"]["facebook"] = {
                        "outcome": "not_attempted",
                        "reason": "previous_platform_failed",
                    }
                raise
            if "facebook" in platforms:
                await _edit_publish_progress_message(
                    context,
                    payload,
                    "meta_upload_publish_progress_fb_after_ig",
                )

        if "facebook" in platforms:
            logger.info(
                "Publishing on Facebook: post_type=%s media_type=%s", post_type, media_type
            )
            try:
                fb_text = await _publish_facebook(
                    session,
                    page_id,
                    post_type,
                    caption,
                    media_type,
                    video_bytes,
                    photo_bytes,
                    payload,
                    schedule_unix=schedule_unix,
                    publish_now=publish_now,
                    access_token=graph_token,
                )
                results.append(fb_text)
                payload["_publish_platform_results"]["facebook"] = {"outcome": "success"}
            except Exception as e:
                payload["_publish_platform_results"]["facebook"] = {
                    "outcome": "failed",
                    "error": _platform_error_text(e),
                }
                raise

    if len(results) == 1:
        result_text = results[0]
    else:
        joiner = "\n\n"
        result_text = joiner.join([r for r in results if r])

    # Expose video bytes for post-publish orchestrators (e.g., Drive archival).
    # Caller is expected to pop this key after use.
    if media_type == "video" and video_bytes is not None:
        payload["_post_publish_video_bytes"] = video_bytes
    return result_text


async def _publish_instagram(
    session: aiohttp.ClientSession,
    ig_user_id: str,
    post_type: str,
    caption: str,
    media_type: str | None,
    video_bytes: bytes | None,
    payload: dict[str, Any],
    *,
    schedule_unix: int | None = None,
    publish_now: bool = True,
    access_token: str | None = None,
) -> str:
    if not ig_user_id:
        raise MetaPublishUserError("meta_err_missing_ig_user_id")

    lang = payload.get("lang", models.Language.ARABIC)
    platforms_caption = caption if caption else ""

    media_type_map = {
        "reel": "REELS",
        "story": "STORIES",
        # For Instagram resumable uploads, "VIDEO" can return Invalid parameter.
        # Use "REELS" for feed video as well.
        "feed": "REELS",
    }

    # Video-based Instagram publishing.
    # Reels always require video, and Stories/Feed use video when `media_type == "video"`.
    # If `post_type == "story"` with `media_type == "photo"`, we must go through the photo branch.
    if media_type == "video" and post_type in ("reel", "story", "feed"):
        logger.info(
            "Instagram video branch: post_type=%s ig_media_type=%s",
            post_type,
            media_type_map.get(post_type),
        )
        if media_type != "video":
            raise MetaPublishUserError("meta_err_ig_requires_video")
        if video_bytes is None:
            raise MetaPublishUserError("meta_err_ig_missing_video_bytes")

        video_bytes = _prepare_instagram_video_bytes(video_bytes, post_type)

        ig_media_type = media_type_map[post_type]
        creation_id = await _ig_create_container(
            session=session,
            ig_user_id=ig_user_id,
            media_type=ig_media_type,
            caption=platforms_caption if platforms_caption else None,
            upload_type="resumable",
            schedule_unix=schedule_unix,
            publish_now=publish_now,
            access_token=access_token,
        )
        await _ig_upload_and_publish_video_resumable(
            session=session,
            ig_user_id=ig_user_id,
            creation_id=creation_id,
            video_bytes=video_bytes,
            schedule_unix=schedule_unix,
            publish_now=publish_now,
            access_token=access_token,
        )
        return TEXTS[lang]["meta_upload_publish_ok_instagram"]

    # Photo branch
    if media_type != "photo":
        raise MetaPublishUserError("meta_err_ig_requires_photo")

    image_url = payload.get("instagram_image_url")
    if not image_url:
        raise MetaPublishUserError("meta_err_ig_missing_image_url")

    if post_type == "story":
        ig_media_type: str | None = "STORIES"
    else:
        # Feed photo uses image_url without forcing media_type=IMAGE.
        # Some Graph API responses reject IMAGE with:
        # "Only photo or video can be accepted as media type."
        ig_media_type = None
    logger.info(
        "Instagram photo branch: post_type=%s ig_media_type=%s",
        post_type,
        ig_media_type,
    )

    creation_id = await _ig_create_container(
        session=session,
        ig_user_id=ig_user_id,
        media_type=ig_media_type,
        caption=platforms_caption if platforms_caption else None,
        image_url=image_url,
        schedule_unix=schedule_unix,
        publish_now=publish_now,
        access_token=access_token,
    )
    await _graph_request(
        session,
        "POST",
        f"/{ig_user_id}/media_publish",
        params={
            "creation_id": creation_id,
            "published": "true" if publish_now else "false",
            **(
                {"scheduled_publish_time": str(schedule_unix)}
                if schedule_unix is not None
                else {}
            ),
        },
        access_token=access_token,
    )
    return TEXTS[lang]["meta_upload_publish_ok_instagram"]


async def _publish_facebook(
    session: aiohttp.ClientSession,
    page_id: str,
    post_type: str,
    caption: str,
    media_type: str | None,
    video_bytes: bytes | None,
    photo_bytes: bytes | None,
    payload: dict[str, Any],
    *,
    schedule_unix: int | None = None,
    publish_now: bool = True,
    access_token: str | None = None,
) -> str:
    lang = payload.get("lang", models.Language.ARABIC)

    if post_type == "feed":
        if not media_type:
            logger.info("Facebook feed text-only branch.")
            if not caption:
                raise MetaPublishUserError("meta_err_fb_text_requires_caption")
            await _graph_request(
                session,
                "POST",
                f"/{page_id}/feed",
                params={
                    "message": caption,
                    "published": "true" if publish_now else "false",
                    **(
                        {"scheduled_publish_time": str(schedule_unix)}
                        if schedule_unix is not None
                        else {}
                    ),
                },
                access_token=access_token,
            )
            return TEXTS[lang]["meta_upload_publish_ok_facebook"]

        if media_type == "photo":
            logger.info("Facebook feed photo branch.")
            if photo_bytes is None:
                raise MetaPublishUserError("meta_err_fb_missing_photo_bytes")

            form = aiohttp.FormData()
            form.add_field(
                "source", photo_bytes, filename="photo.jpg", content_type="image/jpeg"
            )
            if caption:
                form.add_field("message", caption)
            form.add_field("published", "true" if publish_now else "false")
            if schedule_unix is not None:
                form.add_field("scheduled_publish_time", str(schedule_unix))
            await _graph_request(
                session,
                "POST",
                f"/{page_id}/photos",
                data=form,
                access_token=access_token,
            )
            return TEXTS[lang]["meta_upload_publish_ok_facebook"]

        if media_type == "video":
            logger.info("Facebook feed video branch.")
            if video_bytes is None:
                raise MetaPublishUserError("meta_err_fb_missing_video_bytes")
            form = aiohttp.FormData()
            form.add_field(
                "source", video_bytes, filename="video.mp4", content_type="video/mp4"
            )
            if caption:
                form.add_field("description", caption)
            form.add_field("published", "true" if publish_now else "false")
            if schedule_unix is not None:
                form.add_field("scheduled_publish_time", str(schedule_unix))
            # Note: depending on your setup, /videos might require the Video API flow.
            await _graph_request(
                session,
                "POST",
                f"/{page_id}/videos",
                data=form,
                access_token=access_token,
            )
            return TEXTS[lang]["meta_upload_publish_ok_facebook"]

        raise MetaPublishUserError("meta_err_fb_unsupported_feed_media")

    if post_type == "reel":
        logger.info("Facebook reel branch.")
        if media_type != "video" or video_bytes is None:
            raise MetaPublishUserError("meta_err_fb_reel_requires_video")

        # Video Reels flow: START -> upload -> FINISH
        start_body = await _graph_request(
            session,
            "POST",
            f"/{page_id}/video_reels",
            params={
                "upload_phase": "START",
                "video_state": "PUBLISHED",
                "description": caption or "",
            },
            access_token=access_token,
        )
        video_id = start_body.get("video_id")
        upload_url = start_body.get("upload_url")
        if not video_id or not upload_url:
            raise MetaPublishUserError(
                "meta_err_fb_reel_init",
                detail=graph_error_detail(start_body),
            )

        await _upload_to_rupload(
            session, upload_url, video_bytes, access_token=access_token
        )

        await _graph_request(
            session,
            "POST",
            f"/{page_id}/video_reels",
            params={
                "upload_phase": "FINISH",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": caption or "",
            },
            access_token=access_token,
        )
        return TEXTS[lang]["meta_upload_publish_ok_facebook_reel"]

    if post_type == "story":
        logger.info("Facebook story branch.")
        if not media_type:
            raise MetaPublishUserError("meta_err_fb_story_requires_media")

        if media_type == "video":
            logger.info("Facebook story video branch.")
            if video_bytes is None:
                raise MetaPublishUserError("meta_err_fb_story_missing_video_bytes")
            init = await _graph_request(
                session,
                "POST",
                f"/{page_id}/video_stories",
                params={"upload_phase": "start", "description": caption or ""},
                access_token=access_token,
            )
            video_id = init.get("video_id")
            upload_url = init.get("upload_url")
            if not video_id or not upload_url:
                raise MetaPublishUserError(
                    "meta_err_fb_story_init",
                    detail=graph_error_detail(init),
                )
            await _upload_to_rupload(
                session, upload_url, video_bytes, access_token=access_token
            )
            await _graph_request(
                session,
                "POST",
                f"/{page_id}/video_stories",
                params={"upload_phase": "finish", "video_id": video_id},
                access_token=access_token,
            )
            return TEXTS[lang]["meta_upload_publish_ok_facebook_story"]

        if media_type == "photo":
            logger.info("Facebook story photo branch.")
            if photo_bytes is None:
                raise MetaPublishUserError("meta_err_fb_story_missing_photo_bytes")

            # Upload photo (unpublished) to /photos, include message so story inherits it.
            form = aiohttp.FormData()
            form.add_field(
                "source", photo_bytes, filename="story.jpg", content_type="image/jpeg"
            )
            form.add_field("published", "false")
            form.add_field("temporary", "true")
            if caption:
                form.add_field("message", caption)

            upload = await _graph_request(
                session,
                "POST",
                f"/{page_id}/photos",
                data=form,
                access_token=access_token,
            )
            photo_id = upload.get("id")
            if not photo_id:
                raise MetaPublishUserError(
                    "meta_err_fb_story_photo",
                    detail=graph_error_detail(upload),
                )

            await _graph_request(
                session,
                "POST",
                f"/{page_id}/photo_stories",
                params={"photo_id": photo_id},
                access_token=access_token,
            )
            return TEXTS[lang]["meta_upload_publish_ok_facebook_story"]

        raise MetaPublishUserError("meta_err_fb_unsupported_story_media")

    raise MetaPublishUserError(
        "meta_err_unsupported_post_type", post_type=post_type
    )
