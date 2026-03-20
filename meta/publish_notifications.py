import copy
import json
import logging
import re
from typing import Any

from Config import Config

logger = logging.getLogger(__name__)


_SENSITIVE_KEY_SUBSTRINGS = (
    "access_token",
    "page_access_token",
    "bot_token",
    "authorization",
    "bearer",
    "secret",
    "token",
)


def _sanitize_text(value: str, *, max_len: int = 2000) -> str:
    """Redact some common token-like patterns and truncate."""
    if value is None:
        return ""
    v = str(value)
    # Redact "Bearer <token>"
    v = re.sub(r"(Bearer\\s+)[^\\s]+", r"\\1[REDACTED]", v, flags=re.IGNORECASE)
    # Redact "access_token=..."
    v = re.sub(
        r"(access_token\\s*=?\\s*)[^\\s&]+",
        r"\\1[REDACTED]",
        v,
        flags=re.IGNORECASE,
    )
    # Redact "token: <...>" and similar
    v = re.sub(
        r"(token\\s*[:=]\\s*)[^\\s,;\\]\"']+",
        r"\\1[REDACTED]",
        v,
        flags=re.IGNORECASE,
    )

    if len(v) > max_len:
        return v[:max_len] + "...(truncated)"
    return v


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Returns a sanitized copy of payload by redacting sensitive keys.

    Note:
    - This is key-based redaction (based on key substrings), not value-based.
    """
    def _walk(obj: Any) -> Any:
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            for k, v in obj.items():
                k_str = str(k)
                k_lower = k_str.lower()
                if any(substr in k_lower for substr in _SENSITIVE_KEY_SUBSTRINGS):
                    out[k_str] = "[REDACTED]"
                else:
                    out[k_str] = _walk(v)
            return out
        if isinstance(obj, list):
            return [_walk(x) for x in obj]
        return obj

    return _walk(copy.deepcopy(payload))


def _truncate_string(value: Any, max_len: int) -> str:
    if value is None:
        return ""
    s = str(value)
    if len(s) <= max_len:
        return s
    return s[:max_len] + "...(truncated)"


def build_publish_report_text(
    *,
    status: str,
    meta_post_id: int | None,
    payload: dict[str, Any],
    meta_response: str | None,
    last_error: str | None,
) -> str:
    lang = payload.get("lang")
    admin_id = payload.get("admin_id")
    page_id = payload.get("page_id")
    page_name = payload.get("page_name")
    instagram_user_name = payload.get("instagram_user_name")
    post_type = payload.get("post_type")
    media_type = payload.get("media_type")
    platforms = payload.get("platforms")
    schedule_mode = payload.get("schedule_mode")
    scheduled_utc_raw = payload.get("scheduled_utc_raw")
    caption = payload.get("caption")

    # Keep caption + meta_response short enough for Telegram.
    caption_s = _truncate_string(caption, 800)
    meta_response_s = _truncate_string(meta_response, 1200) if meta_response else ""
    last_error_s = _truncate_string(last_error, 1200) if last_error else ""

    sanitized = sanitize_payload(payload)
    # payload قد يحتوي على كائنات غير قابلة للـJSON (مثل datetime).
    # default=str يجعل الإرسال يعمل بدون فشل.
    sanitized_json = json.dumps(
        sanitized, ensure_ascii=False, indent=2, default=str
    )
    # Telegram limit is 4096 chars. Keep a hard cap to avoid failures.
    sanitized_json = _truncate_string(sanitized_json, 1500)

    lines: list[str] = []
    lines.append("نتيجة نشر Meta")
    lines.append(f"status: {status}")
    if meta_post_id is not None:
        lines.append(f"meta_post_id: {meta_post_id}")
    if admin_id is not None:
        lines.append(f"admin_id: {admin_id}")

    lines.append("---")
    lines.append(f"page_id: {page_id}")
    lines.append(f"page_name: {page_name}")
    lines.append(f"instagram_user_name: {instagram_user_name}")
    lines.append(f"post_type: {post_type}")
    lines.append(f"media_type: {media_type}")
    lines.append(f"platforms: {platforms}")
    lines.append(f"caption: {caption_s}")

    if schedule_mode:
        lines.append(f"schedule_mode: {schedule_mode}")
    if scheduled_utc_raw:
        lines.append(f"scheduled_utc_raw: {scheduled_utc_raw}")

    lines.append("---")
    if status == "published":
        lines.append(f"meta_response: {_sanitize_text(meta_response_s, max_len=1200)}")
    else:
        lines.append(f"last_error: {_sanitize_text(last_error_s, max_len=1200)}")

    if lang is not None:
        lines.append(f"lang: {lang}")

    lines.append("---")
    lines.append("payload_sanitized_json:")
    lines.append(sanitized_json)

    return "\n".join(lines)


async def send_publish_report(
    context,
    *,
    status: str,
    meta_post_id: int | None,
    payload: dict[str, Any],
    meta_response: str | None = None,
    last_error: str | None = None,
) -> None:
    """
    Sends a publishing report to `Config.PUBLISH_RESULTS_CHANNEL` if configured.
    """
    chat_id = getattr(Config, "PUBLISH_RESULTS_CHANNEL", None)
    if not chat_id:
        return

    try:
        text = build_publish_report_text(
            status=status,
            meta_post_id=meta_post_id,
            payload=payload,
            meta_response=meta_response,
            last_error=last_error,
        )
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        # Never break the main publishing flow due to notifications.
        logger.exception("Failed to send publish report to channel")

