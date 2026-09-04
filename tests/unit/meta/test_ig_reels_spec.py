"""
Reels-spec enforcement in meta.video_normalizer.

These cover the properties Meta checks at ingest but that the codec-level probe
never measured — the gap that let files through to an opaque 400
ProcessingFailedError from rupload.facebook.com.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import meta.video_normalizer as vn
from meta.errors import MetaPublishUserError


def _box(kind: bytes, payload: bytes = b"") -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + kind + payload


def _fast_mp4() -> bytes:
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isom") + _box(b"moov") + _box(b"mdat", b"x")


def _compat(**overrides) -> vn.StreamCompatibility:
    """A source that satisfies every Reels rule, unless overridden."""
    base = dict(
        video_needs_reencode=False,
        audio_needs_reencode=False,
        has_audio=True,
        width=1080,
        height=1920,
        duration_sec=12.0,
        r_frame_rate=30.0,
        r_frame_rate_text="30/1",
        avg_frame_rate=30.0,
        audio_sample_rate=48000,
        audio_channels=2,
        video_stream_count=1,
        audio_stream_count=1,
        other_stream_count=0,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
    )
    base.update(overrides)
    return vn.StreamCompatibility(**base)


# --- violation detection ---


def test_conforming_source_has_no_violations():
    assert vn._reels_spec_violations(_compat()) == []


@pytest.mark.parametrize(
    "overrides,expected",
    [
        # Nominal and measured rates diverge: screen recordings, Telegram transcodes.
        ({"r_frame_rate": 30.0, "avg_frame_rate": 24.0}, "vfr"),
        ({"r_frame_rate": 120.0, "avg_frame_rate": 120.0}, "fps_out_of_range"),
        ({"r_frame_rate": 12.0, "avg_frame_rate": 12.0}, "fps_out_of_range"),
        ({"width": 1079, "height": 1920}, "odd_dimensions"),
        ({"audio_sample_rate": 44100}, "audio_sample_rate"),
        ({"audio_sample_rate": 8000}, "audio_sample_rate"),
        ({"audio_channels": 6}, "audio_channels"),
        ({"video_stream_count": 2}, "extra_streams"),
        ({"other_stream_count": 1}, "extra_streams"),
        ({"format_name": "matroska,webm"}, "container"),
    ],
)
def test_detects_reels_violation(overrides, expected):
    assert expected in vn._reels_spec_violations(_compat(**overrides))


def test_silent_source_is_not_faulted_for_audio_rate():
    compat = _compat(has_audio=False, audio_sample_rate=None, audio_channels=None)
    assert vn._reels_spec_violations(compat) == []


# --- hard limits (no re-encode can repair these) ---


def test_video_under_three_seconds_is_rejected():
    with pytest.raises(MetaPublishUserError) as cm:
        vn.check_reels_hard_limits(_compat(duration_sec=2.0))
    assert cm.value.message_key == "meta_err_ig_video_too_short"


def test_video_over_fifteen_minutes_is_rejected():
    with pytest.raises(MetaPublishUserError) as cm:
        vn.check_reels_hard_limits(_compat(duration_sec=16 * 60))
    assert cm.value.message_key == "meta_err_ig_video_too_long"


def test_extreme_aspect_ratio_is_rejected():
    with pytest.raises(MetaPublishUserError) as cm:
        vn.check_reels_hard_limits(_compat(width=1920, height=100))
    assert cm.value.message_key == "meta_err_ig_video_aspect_ratio"


def test_hard_limits_accept_normal_reel():
    vn.check_reels_hard_limits(_compat())


def test_hard_limits_skip_when_probe_unavailable():
    vn.check_reels_hard_limits(None)


# --- frame rate selection ---


def test_conforming_fractional_fps_is_preserved():
    """29.97 is valid for Reels; forcing 30 would resample every frame for nothing."""
    compat = _compat(r_frame_rate=29.97, r_frame_rate_text="30000/1001", avg_frame_rate=29.97)
    assert vn._target_fps(compat) == "30000/1001"


def test_out_of_range_constant_fps_falls_back_to_target():
    """120 fps is outside Meta's 23-60 range and has no in-range rate to preserve."""
    assert vn._target_fps(_compat(r_frame_rate=120.0, avg_frame_rate=120.0)) == "30"


def test_vfr_keeps_its_own_measured_rate():
    """
    Pinning VFR to the configured 30 would resample a 24 fps source for no reason.
    The measured average is in range, so it becomes the CFR target.
    """
    assert vn._target_fps(_compat(r_frame_rate=30.0, avg_frame_rate=24.0)) == "24"


def test_vfr_60fps_phone_clip_is_not_halved():
    """The common case: phone 60 fps capture is VFR, and must not come out at 30."""
    assert vn._target_fps(_compat(r_frame_rate=600.0, avg_frame_rate=59.94)) == "60"


def test_vfr_above_range_is_clamped_to_max():
    """120 fps slow-mo clamps to 60, the fastest rate Meta accepts."""
    assert vn._target_fps(_compat(r_frame_rate=600.0, avg_frame_rate=120.0)) == "60"


def test_missing_probe_defaults_to_target_fps():
    assert vn._target_fps(None) == "30"


# --- generated ffmpeg command ---


def _capture_cmd(monkeypatch: pytest.MonkeyPatch, compat, *, safe_mode: bool = False):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", False)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: True)
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(vn, "_probe_stream_compatibility", lambda _p: compat)

    captured: list[list[str]] = []

    def fake_run(cmd: list[str]):
        captured.append(cmd)
        Path(cmd[-1]).write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)
    result = vn.normalize_instagram_video_bytes(_fast_mp4(), safe_mode=safe_mode)
    return result, captured[0]


def test_vfr_source_is_forced_to_constant_frame_rate(monkeypatch: pytest.MonkeyPatch):
    result, cmd = _capture_cmd(
        monkeypatch, _compat(r_frame_rate=30.0, avg_frame_rate=24.0)
    )
    assert result.changed is True
    assert "vfr" in result.violations
    # Pinned to the source's own measured rate, not the configured target.
    assert "-r" in cmd and "24" in cmd
    # ffmpeg >= 5 uses -fps_mode; -vsync is its removed predecessor.
    assert "-fps_mode" in cmd or "-vsync" in cmd


def test_wrong_sample_rate_is_resampled_to_48k(monkeypatch: pytest.MonkeyPatch):
    result, cmd = _capture_cmd(monkeypatch, _compat(audio_sample_rate=44100))
    assert "audio_sample_rate" in result.violations
    assert "-ar" in cmd and "48000" in cmd


def test_surround_audio_is_downmixed_to_stereo(monkeypatch: pytest.MonkeyPatch):
    result, cmd = _capture_cmd(monkeypatch, _compat(audio_channels=6))
    assert "audio_channels" in result.violations
    assert "-ac" in cmd and "2" in cmd


def test_extra_streams_are_dropped_by_explicit_maps(monkeypatch: pytest.MonkeyPatch):
    """Cover art and timecode tracks must not reach Meta."""
    _result, cmd = _capture_cmd(monkeypatch, _compat(video_stream_count=2))
    assert "-map" in cmd
    assert "0:v:0" in cmd


def test_odd_dimensions_are_rounded_even(monkeypatch: pytest.MonkeyPatch):
    result, cmd = _capture_cmd(monkeypatch, _compat(width=1079, height=1919))
    assert "odd_dimensions" in result.violations
    assert "-vf" in cmd
    assert any("trunc(iw/2)*2" in a for a in cmd)


def test_conforming_source_gets_no_scale_filter(monkeypatch: pytest.MonkeyPatch):
    """A frame already inside the box and even must not be resampled."""
    _result, cmd = _capture_cmd(
        monkeypatch, _compat(video_needs_reencode=True, width=1080, height=1920)
    )
    assert "-vf" not in cmd


def test_no_level_is_pinned(monkeypatch: pytest.MonkeyPatch):
    """1080p60 exceeds level 4.1; x264 must pick a conformant level itself."""
    _result, cmd = _capture_cmd(monkeypatch, _compat(video_needs_reencode=True))
    assert "-level" not in cmd


def test_faststart_and_timescale_are_always_set(monkeypatch: pytest.MonkeyPatch):
    _result, cmd = _capture_cmd(monkeypatch, _compat(video_needs_reencode=True))
    assert "+faststart" in cmd
    assert "-video_track_timescale" in cmd
    assert "-avoid_negative_ts" in cmd


def test_safe_mode_uses_smaller_faster_profile(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(vn.Config, "IG_REELS_SAFE_MODE_LONG_SIDE", 1280)
    monkeypatch.setattr(vn.Config, "IG_REELS_SAFE_MODE_CRF", 23)
    result, cmd = _capture_cmd(monkeypatch, _compat(), safe_mode=True)
    assert "safe_mode" in result.method
    assert "veryfast" in cmd
    assert "23" in cmd
    assert "-r" in cmd and "30" in cmd
    # 1080x1920 exceeds the safe-mode 720x1280 box, so it must be scaled down.
    assert any("1280" in a for a in cmd if a.startswith("scale="))


def test_silent_source_gets_48k_silent_audio(monkeypatch: pytest.MonkeyPatch):
    _result, cmd = _capture_cmd(
        monkeypatch,
        _compat(has_audio=False, audio_sample_rate=None, audio_channels=None,
                video_needs_reencode=True),
    )
    assert "anullsrc=channel_layout=stereo:sample_rate=48000" in cmd


def test_force_reencode_without_probe_still_muxes_silent_audio(
    monkeypatch: pytest.MonkeyPatch,
):
    """
    With no ffprobe, assuming the source has audio produced a video-only MP4 for
    silent sources — a known ProcessingFailedError cause.
    """
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", True)
    monkeypatch.setattr(vn, "ffprobe_available", lambda: False)
    monkeypatch.setattr(vn, "ffmpeg_available", lambda: True)

    captured: list[list[str]] = []

    def fake_run(cmd: list[str]):
        captured.append(cmd)
        Path(cmd[-1]).write_bytes(_fast_mp4())
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vn, "_run_ffmpeg", fake_run)
    vn.normalize_instagram_video_bytes(_fast_mp4())
    assert any("anullsrc" in a for a in captured[0])


# --- end-to-end against real ffmpeg ---

_HAS_FFMPEG = vn.ffmpeg_available() and vn.ffprobe_available()
requires_ffmpeg = pytest.mark.skipif(
    not _HAS_FFMPEG, reason="ffmpeg/ffprobe not available on this host"
)


def _make_source(path: Path, *, fps: int, sample_rate: int, duration: int = 4) -> bytes:
    subprocess.run(
        [
            vn.Config.FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate={fps}:duration={duration}",
            "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate={sample_rate}:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", str(sample_rate),
            "-movflags", "+faststart",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=180,
    )
    return path.read_bytes()


def _probe(video_bytes: bytes, tmp_path: Path) -> dict:
    out = tmp_path / "probe-target.mp4"
    out.write_bytes(video_bytes)
    proc = subprocess.run(
        [
            vn._resolve_ffprobe_exe(), "-v", "error",
            "-show_format", "-show_streams", "-of", "json", str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(proc.stdout)


@requires_ffmpeg
def test_real_nonconforming_source_becomes_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    The whole point of the fix: a source Meta would reject (120 fps, 44.1 kHz)
    must come out of the normalizer satisfying every Reels property.
    """
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", False)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_ENCODE_PRESET", "ultrafast")

    source = _make_source(tmp_path / "src.mp4", fps=120, sample_rate=44100)
    result = vn.normalize_instagram_video_bytes(source)

    assert result.changed is True
    assert "fps_out_of_range" in result.violations
    assert "audio_sample_rate" in result.violations

    parsed = _probe(result.video_bytes, tmp_path)
    streams = parsed["streams"]
    video = [s for s in streams if s["codec_type"] == "video"]
    audio = [s for s in streams if s["codec_type"] == "audio"]

    assert len(video) == 1 and len(audio) == 1, "exactly one video and one audio track"
    assert video[0]["codec_name"] == "h264"
    assert video[0]["pix_fmt"] == "yuv420p"

    # Constant frame rate, inside Meta's 23-60 range.
    r_fps = vn._parse_fraction(video[0]["r_frame_rate"])
    avg_fps = vn._parse_fraction(video[0]["avg_frame_rate"])
    assert abs(r_fps - avg_fps) <= vn._VFR_TOLERANCE_FPS
    assert vn._IG_MIN_FPS <= avg_fps <= vn._IG_MAX_FPS

    assert int(audio[0]["sample_rate"]) == 48000
    assert int(audio[0]["channels"]) <= 2

    assert video[0]["width"] % 2 == 0 and video[0]["height"] % 2 == 0
    assert vn._mp4_moov_before_mdat(result.video_bytes) is not False


@requires_ffmpeg
def test_real_conforming_source_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A file that already meets the spec must not be re-encoded."""
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", False)

    source = _make_source(tmp_path / "ok.mp4", fps=30, sample_rate=48000)
    result = vn.normalize_instagram_video_bytes(source)

    assert result.violations == ()
    assert result.changed is False
    assert result.method == "none"


@requires_ffmpeg
def test_real_short_video_is_rejected_before_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", False)

    source = _make_source(tmp_path / "short.mp4", fps=30, sample_rate=48000, duration=2)
    with pytest.raises(MetaPublishUserError) as cm:
        vn.normalize_instagram_video_bytes(source)
    assert cm.value.message_key == "meta_err_ig_video_too_short"


@requires_ffmpeg
def test_remuxed_file_still_reports_its_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Cover art needs only a remux, not a re-encode. The violation must still reach the
    result — it is what a later Meta rejection would be diagnosed from.
    """
    monkeypatch.setattr(vn.Config, "IG_VIDEO_AUTOFIX_ENABLED", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    monkeypatch.setattr(vn.Config, "IG_VIDEO_FORCE_REENCODE", False)

    plain = _make_source(tmp_path / "plain.mp4", fps=30, sample_rate=48000)
    cover = tmp_path / "cover.png"
    subprocess.run(
        [vn.Config.FFMPEG_BIN, "-y", "-f", "lavfi", "-i",
         "color=c=red:size=320x320:duration=1", "-frames:v", "1", str(cover)],
        capture_output=True, check=True, timeout=120,
    )
    with_cover = tmp_path / "cover_art.mp4"
    subprocess.run(
        [vn.Config.FFMPEG_BIN, "-y", "-i", str(tmp_path / "plain.mp4"), "-i", str(cover),
         "-map", "0:v", "-map", "0:a", "-map", "1", "-c", "copy", "-c:v:1", "mjpeg",
         "-disposition:v:1", "attached_pic", "-movflags", "+faststart", str(with_cover)],
        capture_output=True, check=True, timeout=180,
    )

    result = vn.normalize_instagram_video_bytes(with_cover.read_bytes())

    assert "extra_streams" in result.violations
    assert result.changed is True

    parsed = _probe(result.video_bytes, tmp_path)
    assert len(parsed["streams"]) == 2, "cover art must be dropped"
    assert len(plain) > 0
