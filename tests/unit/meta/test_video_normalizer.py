"""Unit tests for Instagram video auto-normalization."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from meta.errors import MetaPublishUserError
import meta.video_normalizer as vn


def _box(fourcc: bytes, payload: bytes = b"") -> bytes:
    size = 8 + len(payload)
    return size.to_bytes(4, "big") + fourcc + payload


def _slow_mp4() -> bytes:
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"mdat", b"x") + _box(b"moov")


def _fast_mp4() -> bytes:
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"moov") + _box(b"mdat", b"x")


def test_faststart_input_not_changed():
    result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.changed is False
    assert result.method == "none"


def test_slow_mp4_fixed_by_copy_faststart(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK", True)
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    def fake_run(cmd: list[str]):
        out_path = Path(cmd[-1])
        out_path.write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)

    result = vn.normalize_instagram_video_bytes(_slow_mp4())
    assert result.changed is True
    assert result.method == "copy_faststart"
    assert vn._mp4_moov_before_mdat(result.video_bytes) is True


def test_slow_mp4_fix_fails_returns_user_friendly_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK", True)
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: False)

    with pytest.raises(MetaPublishUserError) as cm:
        vn.normalize_instagram_video_bytes(_slow_mp4())
    assert cm.value.message_key == "meta_err_ig_video_prepare_failed"


def test_incompatible_codec_triggers_reencode(monkeypatch: pytest.MonkeyPatch):
    """When ffprobe says non-H264/non-AAC, we re-encode even if MP4 layout is already fast-start."""
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", False)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: True)
    monkeypatch.setattr(
        vn,
        "_probe_streams_incompatible_with_instagram",
        lambda _path: True,
    )
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    def fake_run(cmd: list[str]):
        out_path = Path(cmd[-1])
        out_path.write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)

    result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.changed is True
    assert result.method == "reencode_faststart"
