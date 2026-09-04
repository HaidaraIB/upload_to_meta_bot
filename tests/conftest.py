"""
Shared fixtures for Meta (and future) tests.

Unit tests (default, no network):
  pytest
  pytest tests/unit

Live Meta API (reads .env for META_ACCESS_TOKEN):
  pytest tests/integration
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import Config as config_module


# Documented defaults, pinned so unit tests never depend on the developer's .env.
# A host with IG_VIDEO_FORCE_REENCODE=true would otherwise send every synthetic
# test fixture through real ffmpeg. Tests that exercise a setting patch it themselves.
_IG_VIDEO_TEST_DEFAULTS = {
    "IG_VIDEO_STRICT_PROBE": False,
    "IG_VIDEO_FORCE_REENCODE": False,
    "IG_VIDEO_REENCODE_IF_INCOMPATIBLE": True,
    "IG_VIDEO_AUTOFIX_ENABLED": True,
    "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK": True,
    "IG_VIDEO_CRF": 18,
    "IG_VIDEO_ENCODE_PRESET": "medium",
    "IG_VIDEO_AUDIO_BITRATE": "192k",
    "IG_REELS_TARGET_FPS": 30,
    "IG_REELS_SAFE_MODE_RETRY": True,
    "IG_REELS_SAFE_MODE_CRF": 23,
    "IG_REELS_SAFE_MODE_LONG_SIDE": 1280,
}


@pytest.fixture(autouse=True)
def _ig_video_config_defaults_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the IG video pipeline from real .env values during unit tests."""
    for name, value in _IG_VIDEO_TEST_DEFAULTS.items():
        monkeypatch.setattr(config_module.Config, name, value)


@pytest.fixture
def mock_context():
    """Minimal Telegram context for publish_to_meta (no real bot)."""
    ctx = AsyncMock()
    ctx.bot = AsyncMock()
    return ctx


@pytest.fixture
def publishers_texts():
    """Stub TEXTS so tests do not import models (avoids DB stack in conftest)."""

    _ok = {
        "meta_upload_publish_ok_instagram": "IG OK",
        "meta_upload_publish_ok_facebook": "FB OK",
        "meta_upload_publish_ok_facebook_reel": "FB OK",
        "meta_upload_publish_ok_facebook_story": "FB OK",
    }

    class _StubTexts(dict):
        def __getitem__(self, _lang):
            return _ok

    with patch("meta.publishers.TEXTS", _StubTexts()):
        yield


@pytest.fixture
def meta_access_token():
    return "unit-test-access-token"


@pytest.fixture
def meta_graph_version():
    return "v25.0"


@pytest.fixture
def patch_meta_config(meta_access_token, meta_graph_version):
    """Isolate Config from real .env during unit tests (single patch on Config module)."""
    with (
        patch("Config.Config.META_ACCESS_TOKEN", meta_access_token),
        patch("Config.Config.META_GRAPH_VERSION", meta_graph_version),
    ):
        yield


@pytest.fixture(autouse=True)
def _no_errors_channel_during_tests():
    """
    Upload-failure diagnostics push to ERRORS_CHANNEL via a real Bot. ERRORS_CHANNEL
    is populated from .env, so without this every failure-path test would hit Telegram.
    """
    with patch("Config.Config.ERRORS_CHANNEL", None):
        yield
