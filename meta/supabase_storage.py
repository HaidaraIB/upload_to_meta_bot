from __future__ import annotations

import aiohttp
import logging
from typing import Any
from meta.errors import MetaPublishUserError
from Config import Config

logger = logging.getLogger(__name__)


async def upload_bytes_public_url(
    *,
    session: aiohttp.ClientSession,
    bucket: str,
    object_path: str,
    content_type: str,
    file_bytes: bytes,
) -> str:
    """
    Uploads bytes to Supabase Storage and returns a public URL.

    Requires:
      - Config.SUPABASE_URL
      - Config.SUPABASE_SERVICE_ROLE_KEY
      - A PUBLIC bucket in Supabase Storage (or access compatible with public URL).
    """
    if not Config.SUPABASE_URL or not Config.SUPABASE_SERVICE_ROLE_KEY:
        logger.error(
            "Supabase not configured: SUPABASE_URL=%s SUPABASE_SERVICE_ROLE_KEY=%s",
            bool(Config.SUPABASE_URL),
            bool(Config.SUPABASE_SERVICE_ROLE_KEY),
        )
        raise MetaPublishUserError("meta_err_supabase_missing_config")
    if not bucket:
        logger.error("Supabase bucket missing.")
        raise MetaPublishUserError("meta_err_supabase_missing_config")

    # REST endpoint:
    # POST /storage/v1/object/{bucket}/{object}
    # Uploads raw bytes; bucket must allow public access for the returned URL to work.
    url = f"{Config.SUPABASE_URL}/storage/v1/object/{bucket}/{object_path.lstrip('/')}"
    headers: dict[str, Any] = {
        "Authorization": f"Bearer {Config.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }

    safe_object_path = object_path[-120:] if len(object_path) > 120 else object_path
    logger.info(
        "Supabase upload start: bucket=%s object_path_tail=%s bytes=%s content_type=%s",
        bucket,
        safe_object_path,
        len(file_bytes),
        content_type,
    )
    async with session.post(url, headers=headers, data=file_bytes) as resp:
        if resp.status >= 400:
            body = await resp.text()
            logger.warning(
                "Supabase upload failed: bucket=%s status=%s detail=%s",
                bucket,
                resp.status,
                (body or "")[:250],
            )
            raise MetaPublishUserError(
                "meta_err_supabase_upload_failed",
                status=resp.status,
                detail=(body or "")[:400],
            )

    public_url = (
        f"{Config.SUPABASE_URL}/storage/v1/object/public/{bucket}/"
        f"{object_path.lstrip('/')}"
    )
    logger.info(
        "Supabase upload ok: bucket=%s object_path_tail=%s public_url_tail=%s",
        bucket,
        safe_object_path,
        public_url[-80:] if len(public_url) > 80 else public_url,
    )
    return public_url

