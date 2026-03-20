import os
from dotenv import load_dotenv

load_dotenv()


class Config:
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

    # Supabase Storage (for auto-providing Instagram image_url)
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET")
    SUPABASE_PUBLISHABLE_KEY = os.getenv("SUPABASE_PUBLISHABLE_KEY")
    SUPABASE_DB_PASSWORD = os.getenv("SUPABASE_DB_PASSWORD")
