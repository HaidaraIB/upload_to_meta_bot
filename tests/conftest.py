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


@pytest.fixture(autouse=True)
def _ig_video_strict_probe_off_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """IG_VIDEO_STRICT_PROBE in .env breaks synthetic video bytes used in publisher tests."""
    monkeypatch.setattr(config_module.Config, "IG_VIDEO_STRICT_PROBE", False)


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
