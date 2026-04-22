import logging
import os
from dotenv import load_dotenv

load_dotenv()

_LOG_LEVEL_NAME = os.getenv("LOG_LEVEL", "DEBUG").upper()
LOG_LEVEL = getattr(logging, _LOG_LEVEL_NAME, logging.DEBUG)


class Config:
    LOG_LEVEL = LOG_LEVEL

    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    OWNER_ID = int(os.getenv("OWNER_ID"))
    _errors_channel_raw = os.getenv("ERRORS_CHANNEL")
    try:
        ERRORS_CHANNEL = (
            int(_errors_channel_raw) if _errors_channel_raw else None
        )
    except ValueError:
        ERRORS_CHANNEL = None

    # Optional channel to receive meta publishing reports (chat_id).
    # If not set (or invalid), publishing results will not be forwarded to a channel.
    _publish_results_channel_raw = os.getenv("PUBLISH_RESULTS_CHANNEL")
    try:
        PUBLISH_RESULTS_CHANNEL = (
            int(_publish_results_channel_raw)
            if _publish_results_channel_raw
            else None
        )
    except ValueError:
        PUBLISH_RESULTS_CHANNEL = None

    DB_PATH = os.getenv("DB_PATH")
    DB_POOL_SIZE = 20
    DB_MAX_OVERFLOW = 10

    # Meta Graph API (Facebook + Instagram)
    META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v25.0")
    META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
    RUUPLOAD_BASE = os.getenv("RUUPLOAD_BASE", "https://rupload.facebook.com")
    _meta_http_timeout_raw = os.getenv("META_HTTP_TIMEOUT_TOTAL", "600")
    try:
        META_HTTP_TIMEOUT_TOTAL = max(60, int(_meta_http_timeout_raw))
    except ValueError:
        META_HTTP_TIMEOUT_TOTAL = 600

    # Max Telegram media size to load into memory for Meta publish (Telethon path).
    _max_mb_raw = os.getenv("TELEGRAM_MEDIA_MAX_MB", "200")
    try:
        TELEGRAM_MEDIA_MAX_BYTES = max(1, int(_max_mb_raw)) * 1024 * 1024
    except ValueError:
        TELEGRAM_MEDIA_MAX_BYTES = 200 * 1024 * 1024

    FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")

    _ig_video_autofix_enabled_raw = os.getenv("IG_VIDEO_AUTOFIX_ENABLED", "true").lower()
    IG_VIDEO_AUTOFIX_ENABLED = _ig_video_autofix_enabled_raw in ("1", "true", "yes", "on")

    _ig_video_autofix_reencode_raw = os.getenv(
        "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK", "true"
    ).lower()
    IG_VIDEO_AUTOFIX_REENCODE_FALLBACK = _ig_video_autofix_reencode_raw in (
        "1",
        "true",
        "yes",
        "on",
    )

    _ig_reencode_incompatible_raw = os.getenv(
        "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", "true"
    ).lower()
    IG_VIDEO_REENCODE_IF_INCOMPATIBLE = _ig_reencode_incompatible_raw in (
        "1",
        "true",
        "yes",
        "on",
    )

    _ig_force_reencode_raw = os.getenv("IG_VIDEO_FORCE_REENCODE", "false").lower()
    IG_VIDEO_FORCE_REENCODE = _ig_force_reencode_raw in ("1", "true", "yes", "on")

    # Supabase Storage (for auto-providing Instagram image_url)
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")
    SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY")
    SUPABASE_DB_PASSWORD = os.getenv("SUPABASE_DB_PASSWORD")

    # Optional Firestore poller (used as external scheduler worker for Point app).
    _fs_poll_enabled_raw = os.getenv("FIRESTORE_POLLING_ENABLED", "false").lower()
    FIRESTORE_POLLING_ENABLED = _fs_poll_enabled_raw in ("1", "true", "yes", "on")
    FIRESTORE_PROJECT_ID = os.getenv("FIRESTORE_PROJECT_ID")
    FIRESTORE_META_POSTS_COLLECTION = os.getenv("FIRESTORE_META_POSTS_COLLECTION", "meta_posts")
    _fs_poll_interval_raw = os.getenv("FIRESTORE_POLL_INTERVAL_SECONDS", "60")
    try:
        FIRESTORE_POLL_INTERVAL_SECONDS = max(15, int(_fs_poll_interval_raw))
    except ValueError:
        FIRESTORE_POLL_INTERVAL_SECONDS = 60
    _fs_poll_batch_raw = os.getenv("FIRESTORE_POLL_BATCH_SIZE", "20")
    try:
        FIRESTORE_POLL_BATCH_SIZE = max(1, min(200, int(_fs_poll_batch_raw)))
    except ValueError:
        FIRESTORE_POLL_BATCH_SIZE = 20
