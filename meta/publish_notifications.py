import copy
import logging
import re
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from urllib.parse import urlparse

from telegram.constants import ParseMode

from Config import Config
from common.lang_dicts import BUTTONS, TEXTS
import models

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
    v = re.sub(r"(Bearer\s+)[^\s]+", r"\1[REDACTED]", v, flags=re.IGNORECASE)
    # Redact "access_token=..."
    v = re.sub(
        r"(access_token\s*=\s*)[^\s&]+",
        r"\1[REDACTED]",
        v,
        flags=re.IGNORECASE,
    )
    # Redact "token: <...>" and similar
    v = re.sub(
        r"(token\s*[:=]\s*)[^\s,;\]\"']+",
        r"\1[REDACTED]",
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


def _lang_from_payload(payload: dict[str, Any]) -> models.Language:
    raw = payload.get("lang")
    if isinstance(raw, models.Language):
        return raw
    if isinstance(raw, str) and "ENGLISH" in raw.upper():
        return models.Language.ENGLISH
    return models.Language.ARABIC


def _clip_telegram_caption(html: str, max_len: int = 1024) -> str:
    if len(html) <= max_len:
        return html
    return html[: max_len - 20] + "\n…(truncated)"


def _post_type_label(lang: models.Language, post_type: str | None) -> str:
    if not post_type:
        return "—"
    key = f"post_type_{post_type}"
    if key in BUTTONS[lang]:
        return BUTTONS[lang][key]
    return str(post_type)


def _selected_platforms_csv(lang: models.Language, platforms: list[Any] | None) -> str:
    pls = set(platforms or [])
    labels: list[str] = []
    for p in ("instagram", "facebook"):
        if p in pls:
            labels.append(BUTTONS[lang][f"platform_{p}"])
    return ", ".join(labels) if labels else "—"


def _append_platform_breakdown_lines(
    lines: list[str],
    *,
    payload: dict[str, Any],
    lang: models.Language,
    t: dict[str, str],
) -> None:
    raw = payload.get("_publish_platform_results")
    if not raw or not isinstance(raw, dict):
        return
    lines.append(f"<b>{escape(t['publish_report_platform_breakdown'])}</b>")
    for key in ("instagram", "facebook"):
        if key not in raw:
            continue
        entry = raw[key]
        if not isinstance(entry, dict):
            continue
        name = escape(BUTTONS[lang][f"platform_{key}"])
        oc = entry.get("outcome")
        if oc == "not_selected":
            line = f"• {name}: {escape(t['publish_report_platform_not_selected'])}"
        elif oc == "success":
            line = f"• {name}: {escape(t['publish_report_platform_ok'])}"
        elif oc == "failed":
            err = _sanitize_text(str(entry.get("error") or ""), max_len=220)
            line = (
                f"• {name}: {escape(t['publish_report_platform_failed'])} — {escape(err)}"
            )
        elif oc == "not_attempted":
            reason = entry.get("reason")
            if reason == "previous_platform_failed":
                detail = t["publish_report_platform_not_attempted_previous"]
            else:
                detail = t["publish_report_platform_not_attempted_pre"]
            line = f"• {name}: {escape(detail)}"
        elif oc == "pending":
            line = f"• {name}: …"
        else:
            line = f"• {name}: {escape(str(oc))}"
        lines.append(line)


def _is_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def build_publish_report_html(
    *,
    status: str,
    meta_post_id: int | None,
    payload: dict[str, Any],
    meta_response: str | None,
    last_error: str | None,
    report_at_utc: datetime | None = None,
) -> str:
    """
    Short localized HTML report for the publish-results channel.
    meta_post_id / meta_response are accepted for call-site compatibility but omitted from output.
    """
    _ = meta_post_id, meta_response

    lang = _lang_from_payload(payload)
    t = TEXTS[lang]

    when = report_at_utc or datetime.now(timezone.utc)

    head_key_by_status = {
        "published": "publish_report_head_published",
        "scheduled": "publish_report_head_scheduled",
        "failed": "publish_report_head_failed",
    }
    headline_key = head_key_by_status.get(status, "publish_report_head_failed")

    page_name = payload.get("page_name")
    page_display = page_name if page_name is not None else "—"

    admin_id = payload.get("admin_id")
    admin_display = "—"
    with models.session_scope() as s:
        settings = models.GeneralSettings.get_or_create(s)
        offset_hours = settings.meta_timezone_offset_hours
        if admin_id is not None:
            row = s.get(models.User, int(admin_id))
            if row is not None:
                admin_display = f"@{row.username}" if row.username else row.name
            else:
                admin_display = str(admin_id)

    report_local = when + timedelta(hours=offset_hours)
    dt_str = report_local.strftime("%Y-%m-%d %H:%M")

    lines: list[str] = [
        f"<b>{escape(t[headline_key])}</b>",
        f"<b>{escape(t['publish_report_page'])}</b>: {escape(str(page_display))}",
        f"<b>{escape(t['publish_report_datetime'])}</b>: {escape(dt_str)}",
        f"<b>{escape(t['publish_report_admin'])}</b>: {escape(admin_display)}",
    ]

    pt_label = _post_type_label(lang, payload.get("post_type"))
    lines.append(
        f"<b>{escape(t['publish_report_post_type'])}</b>: {escape(pt_label)}"
    )
    plat_csv = _selected_platforms_csv(lang, payload.get("platforms"))
    lines.append(
        f"<b>{escape(t['publish_report_selected_platforms'])}</b>: {escape(plat_csv)}"
    )
    _append_platform_breakdown_lines(lines, payload=payload, lang=lang, t=t)

    drive_archive_status = payload.get("_drive_archive_status")
    if drive_archive_status:
        drive_key = f"publish_report_drive_archive_{drive_archive_status}"
        drive_text = t.get(drive_key, str(drive_archive_status))
        if drive_archive_status == "failed" and payload.get("_drive_archive_error"):
            err = _sanitize_text(str(payload.get("_drive_archive_error")), max_len=200)
            drive_text = f"{drive_text}: {err}"
        lines.append(
            f"<b>{escape(t['publish_report_drive_archive'])}</b>: {escape(str(drive_text))}"
        )

    if status != "published" and last_error:
        err = _sanitize_text(_truncate_string(last_error, 300), max_len=300)
        lines.append(f"<b>{escape(t['publish_report_error'])}</b>: {escape(err)}")

    return "\n".join(lines)


# Backwards-compatible name for callers/tests expecting "text" (now HTML).
def build_publish_report_text(**kwargs: Any) -> str:
    return build_publish_report_html(**kwargs)


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
        html = build_publish_report_html(
            status=status,
            meta_post_id=meta_post_id,
            payload=payload,
            meta_response=meta_response,
            last_error=last_error,
        )
        caption = _clip_telegram_caption(html)

        media_file_id = payload.get("media_file_id")
        media_type = (payload.get("media_type") or "").strip().lower()
        image_url = payload.get("instagram_image_url")

        sent_with_media = False
        if media_file_id and media_type == "photo":
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=media_file_id,
                    caption=caption,
                )
                sent_with_media = True
            except Exception:
                logger.exception(
                    "Failed to send publish report photo to channel; falling back to text"
                )
        elif media_file_id and media_type == "video":
            try:
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=media_file_id,
                    caption=caption,
                )
                sent_with_media = True
            except Exception:
                logger.exception(
                    "Failed to send publish report video to channel; falling back to text"
                )
        elif (
            not media_file_id and isinstance(image_url, str) and _is_http_url(image_url)
        ):
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=caption,
                )
                sent_with_media = True
            except Exception:
                logger.exception(
                    "Failed to send publish report image URL to channel; falling back to text"
                )

        if not sent_with_media:
            # Text-only posts or media send failure: full message allows > caption limit.
            await context.bot.send_message(
                chat_id=chat_id,
                text=html,
            )
    except Exception:
        # Never break the main publishing flow due to notifications.
        logger.exception("Failed to send publish report to channel")
