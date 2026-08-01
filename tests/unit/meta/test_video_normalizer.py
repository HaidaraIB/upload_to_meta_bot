"""Unit tests for Instagram video auto-normalization."""

from __future__ import annotations

import logging
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


def _ok_compat(*, has_audio: bool = False) -> vn.StreamCompatibility:
    return vn.StreamCompatibility(
        video_needs_reencode=False,
        audio_needs_reencode=False,
        has_audio=has_audio,
    )


def test_faststart_input_not_changed(monkeypatch: pytest.MonkeyPatch):
    # Synthetic bytes are not a real MP4; real ffprobe may return inconclusive (None).
    # Isolate from .env (e.g. IG_VIDEO_STRICT_PROBE) and force a clean "compatible" probe.
    monkeypatch.setattr(vn.Config, "IG_VIDEO_STRICT_PROBE", False)
    monkeypatch.setattr(
        vn,
        "_probe_stream_compatibility",
        lambda _path: _ok_compat(),
    )
    result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.changed is False
    assert result.method == "none"


def test_slow_mp4_fixed_by_copy_faststart(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK", True)
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        vn,
        "_probe_stream_compatibility",
        lambda _path: _ok_compat(),
    )

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
    monkeypatch.setattr(
        vn,
        "_probe_stream_compatibility",
        lambda _path: _ok_compat(),
    )

    with pytest.raises(MetaPublishUserError) as cm:
        vn.normalize_instagram_video_bytes(_slow_mp4())
    assert cm.value.message_key == "meta_err_ig_video_prepare_failed"
    assert cm.value.format_kwargs.get("detail") == "ffmpeg_unavailable"


def test_h264_yuv420p10_needs_reencode():
    assert (
        vn._h264_stream_needs_reencode_for_ig(
            codec_name="h264",
            pix_fmt="yuv420p10le",
            profile="High",
        )
        is True
    )


def test_h264_yuv420p_ok():
    assert (
        vn._h264_stream_needs_reencode_for_ig(
            codec_name="h264",
            pix_fmt="yuv420p",
            profile="High",
        )
        is False
    )


def test_incompatible_codec_triggers_reencode(monkeypatch: pytest.MonkeyPatch):
    """When ffprobe says non-H264/non-AAC, we re-encode even if MP4 layout is already fast-start."""
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", False)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: True)
    monkeypatch.setattr(
        vn,
        "_probe_stream_compatibility",
        lambda _path: vn.StreamCompatibility(
            video_needs_reencode=True,
            audio_needs_reencode=True,
            has_audio=True,
        ),
    )
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd: list[str]):
        captured_cmds.append(cmd)
        out_path = Path(cmd[-1])
        out_path.write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)

    result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.changed is True
    assert result.method == "reencode_faststart"
    assert captured_cmds
    cmd = captured_cmds[-1]
    assert "-crf" in cmd
    assert "18" in cmd or str(vn.Config.IG_VIDEO_CRF) in cmd
    assert "yuv420p" in cmd
    assert "high" in cmd
    assert "4.1" in cmd
    assert "+faststart" in cmd


def test_video_only_incompatible_uses_audio_copy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: True)
    monkeypatch.setattr(
        vn,
        "_probe_stream_compatibility",
        lambda _path: vn.StreamCompatibility(
            video_needs_reencode=True,
            audio_needs_reencode=False,
            has_audio=True,
        ),
    )
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd: list[str]):
        captured_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)

    result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.method == "video_reencode_faststart"
    cmd = captured_cmds[-1]
    assert "-c:v" in cmd and "libx264" in cmd
    assert "-c:a" in cmd and "copy" in cmd


def test_audio_only_incompatible_uses_video_copy(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: True)
    monkeypatch.setattr(
        vn,
        "_probe_stream_compatibility",
        lambda _path: vn.StreamCompatibility(
            video_needs_reencode=False,
            audio_needs_reencode=True,
            has_audio=True,
        ),
    )
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd: list[str]):
        captured_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)

    result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.method == "audio_reencode_faststart"
    cmd = captured_cmds[-1]
    assert "-c:v" in cmd and "copy" in cmd
    assert "-c:a" in cmd and "aac" in cmd


def test_reencode_uses_config_crf(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_CRF", 16)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_ENCODE_PRESET", "slow")
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUDIO_BITRATE", "256k")
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", True)
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd: list[str]):
        captured_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)

    vn.normalize_instagram_video_bytes(_fast_mp4())
    cmd = captured_cmds[-1]
    crf_idx = cmd.index("-crf")
    assert cmd[crf_idx + 1] == "16"
    preset_idx = cmd.index("-preset")
    assert cmd[preset_idx + 1] == "slow"
    ba_idx = cmd.index("-b:a")
    assert cmd[ba_idx + 1] == "256k"


def test_warns_when_ffprobe_unavailable(caplog: pytest.LogCaptureFixture, monkeypatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: False)
    with caplog.at_level(logging.WARNING):
        result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.method == "none"
    assert "ffprobe unavailable" in caplog.text


def test_strict_probe_raises_when_probe_inconclusive(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_STRICT_PROBE", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: True)
    monkeypatch.setattr(vn, "_probe_stream_compatibility", lambda _path: None)
    with pytest.raises(MetaPublishUserError) as cm:
        vn.normalize_instagram_video_bytes(_fast_mp4())
    assert cm.value.message_key == "meta_err_ig_video_probe_ambiguous"


def test_probe_inconclusive_logs_without_strict(caplog, monkeypatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_STRICT_PROBE", False)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: True)
    monkeypatch.setattr(vn, "_probe_stream_compatibility", lambda _path: None)
    with caplog.at_level(logging.WARNING):
        result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.method == "none"
    assert "ffprobe did not determine" in caplog.text


def test_ffmpeg_stderr_snippet_uses_tail():
    marker = "UNIQUE_ERR_TAIL_XYZ"
    long_err = ("HEAD_ONLY_" * 40) + marker
    proc = subprocess.CompletedProcess(
        args=["ffmpeg"], returncode=1, stdout="", stderr=long_err
    )
    snippet = vn._ffmpeg_stderr_snippet(proc, max_len=240)
    assert marker in snippet
    assert len(snippet) <= 240
    assert snippet == long_err[-240:]
    assert not snippet.startswith("HEAD_ONLY_")


def test_ffmpeg_stderr_snippet_strips_command_prefix_keeps_timeout():
    raw = (
        "Command '['/usr/bin/ffmpeg', '-y', '-i', '/tmp/in.mp4', '-c:v', 'libx264', "
        "'-crf', '18', '-preset', 'medium', '-pix_fmt', 'yuv420p']' timed out after 300 seconds"
    )
    proc = subprocess.CompletedProcess(
        args=["ffmpeg"], returncode=1, stdout="", stderr=raw
    )
    snippet = vn._ffmpeg_stderr_snippet(proc)
    assert "Command [" not in snippet
    assert "timed out" in snippet.lower()


def test_run_ffmpeg_timeout_detail_has_ffmpeg_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FFMPEG_TIMEOUT", 30)

    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd=["/usr/bin/ffmpeg", "-y"], timeout=30)

    monkeypatch.setattr(subprocess, "run", boom)
    proc = vn._run_ffmpeg(["/usr/bin/ffmpeg", "-y", "-i", "in.mp4", "out.mp4"])
    assert proc.returncode == 124
    assert proc.stderr.startswith("ffmpeg_timeout_30s")
    assert "Command [" not in proc.stderr


def test_no_audio_muxes_silent_aac(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: True)
    monkeypatch.setattr(
        vn,
        "_probe_stream_compatibility",
        lambda _path: vn.StreamCompatibility(
            video_needs_reencode=True,
            audio_needs_reencode=False,
            has_audio=False,
        ),
    )
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd: list[str]):
        captured_cmds.append(cmd)
        Path(cmd[-1]).write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)

    result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.changed is True
    cmd = captured_cmds[0]
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in cmd
    assert "-an" not in cmd
    assert "aac" in cmd
    assert "-shortest" in cmd


def test_reencode_retries_with_veryfast_on_failure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_CRF", 18)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_ENCODE_PRESET", "medium")
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", True)
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    captured_cmds: list[list[str]] = []

    def fake_run(cmd: list[str]):
        captured_cmds.append(list(cmd))
        if len(captured_cmds) == 1:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=124,
                stdout="",
                stderr="ffmpeg_timeout_600s",
            )
        Path(cmd[-1]).write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)

    result = vn.normalize_instagram_video_bytes(_fast_mp4())
    assert result.changed is True
    assert result.method.endswith("_fast_retry")
    assert len(captured_cmds) == 2
    assert captured_cmds[0][captured_cmds[0].index("-preset") + 1] == "medium"
    assert captured_cmds[0][captured_cmds[0].index("-crf") + 1] == "18"
    assert captured_cmds[1][captured_cmds[1].index("-preset") + 1] == "veryfast"
    assert captured_cmds[1][captured_cmds[1].index("-crf") + 1] == "22"


def test_prepare_failed_timeout_detail_not_command_dump(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", True)
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    def always_timeout(cmd: list[str]):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout="",
            stderr="ffmpeg_timeout_600s",
        )

    monkeypatch.setattr(vn, "_run_ffmpeg", always_timeout)

    with pytest.raises(MetaPublishUserError) as cm:
        vn.normalize_instagram_video_bytes(_fast_mp4())
    detail = cm.value.format_kwargs.get("detail") or ""
    assert cm.value.message_key == "meta_err_ig_video_prepare_failed"
    assert "ffmpeg_timeout" in detail
    assert "Command [" not in detail
