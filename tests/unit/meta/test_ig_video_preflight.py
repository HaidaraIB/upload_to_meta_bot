"""Unit tests for Instagram video binary preflight (MP4 layout + size caps)."""

from __future__ import annotations

import pytest

from meta.errors import MetaPublishUserError
from meta.ig_video_preflight import (
    _mp4_moov_before_mdat,
    instagram_video_binary_preflight,
)


def _box(fourcc: bytes, payload: bytes = b"") -> bytes:
    size = 8 + len(payload)
    return size.to_bytes(4, "big") + fourcc + payload


def test_mp4_moov_before_mdat_good():
    data = _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"moov") + _box(b"mdat", b"x")
    assert _mp4_moov_before_mdat(data) is True


def test_mp4_mdat_before_moov_bad():
    data = _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"mdat", b"x") + _box(b"moov")
    assert _mp4_moov_before_mdat(data) is False


def test_non_mp4_returns_none():
    assert _mp4_moov_before_mdat(b"not a video file" * 4) is None


def test_story_video_over_100mb_raises():
    big = b"\x00" * (100 * 1024 * 1024 + 1)
    with pytest.raises(MetaPublishUserError) as cm:
        instagram_video_binary_preflight(big, "story")
    assert cm.value.message_key == "meta_err_ig_video_file_too_large"


def test_reel_under_limit_slow_mp4_raises_faststart():
    data = _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"mdat", b"x") + _box(b"moov")
    with pytest.raises(MetaPublishUserError) as cm:
        instagram_video_binary_preflight(data, "reel")
    assert cm.value.message_key == "meta_err_ig_mp4_requires_faststart"


def test_reel_faststart_order_passes():
    data = _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"moov") + _box(b"mdat", b"x")
    instagram_video_binary_preflight(data, "reel")
