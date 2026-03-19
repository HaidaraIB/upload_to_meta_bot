from typing import Any
import aiohttp
import models
from Config import Config
from common.lang_dicts import TEXTS
from meta.graph_client import _graph_request


async def _download_telegram_file(context, file_id: str) -> bytes:
    tg_file = await context.bot.get_file(file_id)
    data = await tg_file.download_as_bytearray()
    return bytes(data)


async def _upload_to_rupload(
    session: aiohttp.ClientSession, upload_url: str, file_bytes: bytes
) -> None:
    headers = {
        "offset": "0",
        "file_size": str(len(file_bytes)),
    }
    async with session.post(upload_url, headers=headers, data=file_bytes) as resp:
        if resp.status >= 400:
            body = await resp.text()
            raise RuntimeError(f"Upload failed {resp.status}: {body}")


async def _ig_create_container(
    session: aiohttp.ClientSession,
    ig_user_id: str,
    media_type: str,
    caption: str | None,
    upload_type: str | None = None,
    image_url: str | None = None,
) -> str:
    params: dict[str, Any] = {"media_type": media_type}
    if upload_type:
        params["upload_type"] = upload_type
    if caption:
        params["caption"] = caption
    if image_url:
        params["image_url"] = image_url

    body = await _graph_request(
        session,
        "POST",
        f"/{ig_user_id}/media",
        params=params,
    )
    creation_id = body.get("id")
    if not creation_id:
        raise RuntimeError(f"IG container creation failed: {body}")
    return str(creation_id)


async def _ig_upload_and_publish_video_resumable(
    session: aiohttp.ClientSession,
    ig_user_id: str,
    creation_id: str,
    video_bytes: bytes,
) -> dict[str, Any]:
    upload_url = f"{Config.RUUPLOAD_BASE}/ig-api-upload/{Config.META_GRAPH_VERSION}/{creation_id}"
    headers = {
        "Authorization": f"OAuth {Config.META_ACCESS_TOKEN}",
        "offset": "0",
        "file_size": str(len(video_bytes)),
    }
    async with session.post(upload_url, headers=headers, data=video_bytes) as resp:
        if resp.status >= 400:
            body = await resp.text()
            raise RuntimeError(f"IG resumable upload failed {resp.status}: {body}")

    body = await _graph_request(
        session,
        "POST",
        f"/{ig_user_id}/media_publish",
        params={"creation_id": creation_id},
    )
    return body


async def publish_to_meta(payload: dict[str, Any], context) -> str:
    """
    Returns an Arabic/English message text for the admin.
    """
    platforms: list[str] = payload.get("platforms") or []
    page_id = str(payload["page_id"])
    post_type = payload["post_type"]  # reel|story|regular
    caption = payload.get("caption") or ""
    media_type = payload.get("media_type")
    media_file_id = payload.get("media_file_id")
    ig_user_id = str(payload.get("instagram_user_id") or "")

    if not platforms:
        raise ValueError("No platforms specified.")

    if post_type == "reel":
        if media_type != "video":
            raise ValueError("Reel requires a video media.")
    if post_type == "story":
        if not media_type:
            raise ValueError("Story requires media.")
    if post_type == "regular":
        if not media_type and "instagram" in platforms:
            raise ValueError("Instagram requires media (no text-only).")

    # Determine what we need to download from Telegram.
    need_video_bytes = media_type == "video" and (
        ("instagram" in platforms and post_type in ("reel", "story", "regular"))
        or ("facebook" in platforms and post_type in ("reel", "story", "regular"))
    )
    need_photo_bytes = media_type == "photo" and (
        "facebook" in platforms and post_type in ("regular", "story")
    )

    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        video_bytes = None
        photo_bytes = None

        if need_video_bytes:
            video_bytes = await _download_telegram_file(context, media_file_id)
        if need_photo_bytes:
            photo_bytes = await _download_telegram_file(context, media_file_id)

        results: list[str] = []

        if "instagram" in platforms:
            results.append(
                await _publish_instagram(
                    session,
                    ig_user_id,
                    post_type,
                    caption,
                    media_type,
                    video_bytes,
                    payload,
                )
            )

        if "facebook" in platforms:
            results.append(
                await _publish_facebook(
                    session,
                    page_id,
                    post_type,
                    caption,
                    media_type,
                    video_bytes,
                    photo_bytes,
                    payload,
                )
            )

    if len(results) == 1:
        return results[0]
    joiner = "\n\n"
    return joiner.join([r for r in results if r])


async def _publish_instagram(
    session: aiohttp.ClientSession,
    ig_user_id: str,
    post_type: str,
    caption: str,
    media_type: str | None,
    video_bytes: bytes | None,
    payload: dict[str, Any],
) -> str:
    if not ig_user_id:
        raise ValueError("Missing Instagram user id.")

    lang = payload.get("lang", models.Language.ARABIC)
    platforms_caption = caption if caption else ""

    media_type_map = {
        "reel": "REELS",
        "story": "STORIES",
        "regular": "VIDEO",  # regular video feed
    }

    if post_type in ("reel", "story") or (
        post_type == "regular" and media_type == "video"
    ):
        if media_type != "video":
            raise ValueError("Instagram requires video bytes for this post type.")
        if video_bytes is None:
            raise ValueError("Missing downloaded video bytes for Instagram.")

        ig_media_type = media_type_map[post_type]
        creation_id = await _ig_create_container(
            session=session,
            ig_user_id=ig_user_id,
            media_type=ig_media_type,
            caption=platforms_caption if platforms_caption else None,
            upload_type="resumable",
        )
        await _ig_upload_and_publish_video_resumable(
            session=session,
            ig_user_id=ig_user_id,
            creation_id=creation_id,
            video_bytes=video_bytes,
        )
        return TEXTS[lang].get(
            "meta_upload_publish_ok_instagram", "Instagram published successfully ✅"
        )

    # Photo branch
    if media_type != "photo":
        raise ValueError("Instagram requires a photo for photo-based posts.")

    image_url = payload.get("instagram_image_url")
    if not image_url:
        raise ValueError("Missing instagram_image_url for photo publishing.")

    if post_type == "story":
        ig_media_type = "STORIES"
    else:
        ig_media_type = "IMAGE"

    creation_id = await _ig_create_container(
        session=session,
        ig_user_id=ig_user_id,
        media_type=ig_media_type,
        caption=platforms_caption if platforms_caption else None,
        image_url=image_url,
    )
    await _graph_request(
        session,
        "POST",
        f"/{ig_user_id}/media_publish",
        params={"creation_id": creation_id},
    )
    return TEXTS[lang].get(
        "meta_upload_publish_ok_instagram", "Instagram published successfully ✅"
    )


async def _publish_facebook(
    session: aiohttp.ClientSession,
    page_id: str,
    post_type: str,
    caption: str,
    media_type: str | None,
    video_bytes: bytes | None,
    photo_bytes: bytes | None,
    payload: dict[str, Any],
) -> str:
    lang = payload.get("lang", models.Language.ARABIC)

    if post_type == "regular":
        if not media_type:
            if not caption:
                raise ValueError("Facebook text-only post requires caption/text.")
            await _graph_request(
                session,
                "POST",
                f"/{page_id}/feed",
                params={"message": caption, "published": "true"},
            )
            return TEXTS[lang].get(
                "meta_upload_publish_ok_facebook", "Facebook published successfully ✅"
            )

        if media_type == "photo":
            if photo_bytes is None:
                raise ValueError("Missing photo bytes for Facebook.")

            form = aiohttp.FormData()
            form.add_field(
                "source", photo_bytes, filename="photo.jpg", content_type="image/jpeg"
            )
            if caption:
                form.add_field("message", caption)
            form.add_field("published", "true")
            await _graph_request(
                session,
                "POST",
                f"/{page_id}/photos",
                data=form,
            )
            return TEXTS[lang].get(
                "meta_upload_publish_ok_facebook", "Facebook published successfully ✅"
            )

        if media_type == "video":
            if video_bytes is None:
                raise ValueError("Missing video bytes for Facebook.")
            form = aiohttp.FormData()
            form.add_field(
                "source", video_bytes, filename="video.mp4", content_type="video/mp4"
            )
            if caption:
                form.add_field("description", caption)
            form.add_field("published", "true")
            # Note: depending on your setup, /videos might require the Video API flow.
            await _graph_request(
                session,
                "POST",
                f"/{page_id}/videos",
                data=form,
            )
            return TEXTS[lang].get(
                "meta_upload_publish_ok_facebook", "Facebook published successfully ✅"
            )

        raise ValueError("Unsupported media_type for Facebook regular.")

    if post_type == "reel":
        if media_type != "video" or video_bytes is None:
            raise ValueError("Reel requires video for Facebook.")

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
        )
        video_id = start_body.get("video_id")
        upload_url = start_body.get("upload_url")
        if not video_id or not upload_url:
            raise RuntimeError(f"Facebook reel init failed: {start_body}")

        await _upload_to_rupload(session, upload_url, video_bytes)

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
        )
        return TEXTS[lang].get(
            "meta_upload_publish_ok_facebook", "Facebook reel published successfully ✅"
        )

    if post_type == "story":
        if not media_type:
            raise ValueError("Story requires media for Facebook.")

        if media_type == "video":
            if video_bytes is None:
                raise ValueError("Missing video bytes for Facebook story.")
            init = await _graph_request(
                session,
                "POST",
                f"/{page_id}/video_stories",
                params={"upload_phase": "start", "description": caption or ""},
            )
            video_id = init.get("video_id")
            upload_url = init.get("upload_url")
            if not video_id or not upload_url:
                raise RuntimeError(f"Facebook story init failed: {init}")
            await _upload_to_rupload(session, upload_url, video_bytes)
            await _graph_request(
                session,
                "POST",
                f"/{page_id}/video_stories",
                params={"upload_phase": "finish", "video_id": video_id},
            )
            return TEXTS[lang].get(
                "meta_upload_publish_ok_facebook",
                "Facebook story published successfully ✅",
            )

        if media_type == "photo":
            if photo_bytes is None:
                raise ValueError("Missing photo bytes for Facebook story.")

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
            )
            photo_id = upload.get("id")
            if not photo_id:
                raise RuntimeError(f"Facebook story photo upload failed: {upload}")

            await _graph_request(
                session,
                "POST",
                f"/{page_id}/photo_stories",
                params={"photo_id": photo_id},
            )
            return TEXTS[lang].get(
                "meta_upload_publish_ok_facebook",
                "Facebook story published successfully ✅",
            )

        raise ValueError("Unsupported media_type for Facebook story.")

    raise ValueError(f"Unsupported post type: {post_type}")
