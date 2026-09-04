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

    _ig_strict_probe_raw = os.getenv("IG_VIDEO_STRICT_PROBE", "false").lower()
    IG_VIDEO_STRICT_PROBE = _ig_strict_probe_raw in ("1", "true", "yes", "on")

    # Instagram re-encode quality (only when transcoding is required for IG compatibility).
    _ig_video_crf_raw = os.getenv("IG_VIDEO_CRF", "18")
    try:
        IG_VIDEO_CRF = max(0, min(51, int(_ig_video_crf_raw)))
    except ValueError:
        IG_VIDEO_CRF = 18

    IG_VIDEO_ENCODE_PRESET = os.getenv("IG_VIDEO_ENCODE_PRESET", "medium").strip() or "medium"

    _ig_audio_bitrate_raw = os.getenv("IG_VIDEO_AUDIO_BITRATE", "192k").strip()
    IG_VIDEO_AUDIO_BITRATE = _ig_audio_bitrate_raw or "192k"

    # Per ffmpeg invocation timeout (re-encode can be slow on small VPS).
    _ig_ffmpeg_timeout_raw = os.getenv("IG_VIDEO_FFMPEG_TIMEOUT", "600")
    try:
        IG_VIDEO_FFMPEG_TIMEOUT = max(30, int(_ig_ffmpeg_timeout_raw))
    except ValueError:
        IG_VIDEO_FFMPEG_TIMEOUT = 600

    # Frame rate forced onto reels whose source is VFR or outside Meta's 23-60 fps range.
    _ig_reels_fps_raw = os.getenv("IG_REELS_TARGET_FPS", "30")
    try:
        IG_REELS_TARGET_FPS = max(23, min(60, int(_ig_reels_fps_raw)))
    except ValueError:
        IG_REELS_TARGET_FPS = 30

    # Second upload attempt after Meta rejects the first payload with ProcessingFailedError.
    _ig_safe_retry_raw = os.getenv("IG_REELS_SAFE_MODE_RETRY", "true").lower()
    IG_REELS_SAFE_MODE_RETRY = _ig_safe_retry_raw in ("1", "true", "yes", "on")

    _ig_safe_crf_raw = os.getenv("IG_REELS_SAFE_MODE_CRF", "23")
    try:
        IG_REELS_SAFE_MODE_CRF = max(0, min(51, int(_ig_safe_crf_raw)))
    except ValueError:
        IG_REELS_SAFE_MODE_CRF = 23

    # Long-side cap used by the safe-mode retry encode (short side scales to keep AR).
    _ig_safe_long_side_raw = os.getenv("IG_REELS_SAFE_MODE_LONG_SIDE", "1280")
    try:
        IG_REELS_SAFE_MODE_LONG_SIDE = max(480, min(1920, int(_ig_safe_long_side_raw)))
    except ValueError:
        IG_REELS_SAFE_MODE_LONG_SIDE = 1280

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
