"""Unit tests for Meta publish channel notifications."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import models
from meta.publish_notifications import (
    build_publish_report_html,
    send_publish_report,
)


def _payload(**kwargs):
    base = {
        "lang": models.Language.ARABIC,
        "page_name": "Test Page",
        "admin_id": 123,
    }
    base.update(kwargs)
    return base


def test_build_publish_report_html_arabic_published():
    html = build_publish_report_html(
        status="published",
        meta_post_id=1,
        payload=_payload(),
        meta_response="ignored",
        last_error=None,
        report_at_utc=datetime(2026, 3, 21, 12, 0, tzinfo=timezone.utc),
    )
    assert "نتيجة نشر Meta" in html
    assert "الحالة" in html
    assert "منشور" in html
    assert "البيج" in html
    assert "Test Page" in html
    assert "التاريخ والوقت" in html
    assert "2026-03-21 15:00" in html
    assert "الادمن" in html
    assert "123" in html
    assert "السبب" not in html


def test_build_publish_report_html_escapes_html_in_page_name():
    html = build_publish_report_html(
        status="published",
        meta_post_id=None,
        payload=_payload(page_name='<script>alert(1)</script>'),
        meta_response=None,
        last_error=None,
        report_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_build_publish_report_html_failed_includes_error():
    html = build_publish_report_html(
        status="failed",
        meta_post_id=5,
        payload=_payload(lang=models.Language.ENGLISH),
        meta_response=None,
        last_error="Something went wrong",
        report_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    assert "Failed" in html
    assert "Reason" in html
    assert "Something went wrong" in html


def test_build_publish_report_lang_string_english():
    html = build_publish_report_html(
        status="published",
        meta_post_id=None,
        payload=_payload(lang="Language.ENGLISH"),
        meta_response=None,
        last_error=None,
        report_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    assert "Meta publish result" in html
    assert "Published" in html


@pytest.mark.asyncio
@patch("meta.publish_notifications.Config")
async def test_send_publish_report_calls_send_video_with_caption(mock_config):
    channel_id = -1001234567890
    mock_config.PUBLISH_RESULTS_CHANNEL = channel_id

    context = MagicMock()
    context.bot.send_video = AsyncMock()
    context.bot.send_photo = AsyncMock()
    context.bot.send_message = AsyncMock()

    payload = _payload(
        media_type="video",
        media_file_id="file_id_xyz",
    )

    await send_publish_report(
        context,
        status="published",
        meta_post_id=1,
        payload=payload,
        meta_response="ok",
    )

    context.bot.send_video.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()
    call_kw = context.bot.send_video.await_args.kwargs
    assert call_kw["chat_id"] == channel_id
    assert call_kw["video"] == "file_id_xyz"
    assert "منشور" in call_kw["caption"]


@pytest.mark.asyncio
@patch("meta.publish_notifications.Config")
async def test_send_publish_report_fallback_to_message_on_video_error(mock_config):
    mock_config.PUBLISH_RESULTS_CHANNEL = -1001

    context = MagicMock()

    async def boom(**kwargs):
        raise RuntimeError("telegram error")

    context.bot.send_video = AsyncMock(side_effect=boom)
    context.bot.send_photo = AsyncMock()
    context.bot.send_message = AsyncMock()

    await send_publish_report(
        context,
        status="published",
        meta_post_id=None,
        payload=_payload(media_type="video", media_file_id="fid"),
        meta_response="ok",
    )

    context.bot.send_message.assert_awaited_once()
