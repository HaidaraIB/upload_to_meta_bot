from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from Config import Config
from meta.errors import MetaPublishUserError
from meta.ig_video_preflight import _mp4_moov_before_mdat

logger = logging.getLogger(__name__)

# Instagram publishing works reliably with H.264 + AAC in MP4; other codecs often pass Facebook but fail IG processing.
_IG_VIDEO_CODECS = frozenset({"h264", "avc", "avc1"})
_IG_AUDIO_CODECS = frozenset({"aac"})
# 8-bit 4:2:0 — what libx264 + yuv420p produces; IG often rejects 10-bit / 4:2:2 / 4:4:4 even if codec_name is h264.
_IG_SAFE_PIX_FMT = frozenset({"yuv420p", "yuvj420p"})
# Cap maxrate derived from source probe so high CRF does not exceed practical IG size limits.
# Deliberately generous: this is a safety ceiling for pathological sources, not a quality
# target. CRF drives the actual quality, and clamping below the source rate would visibly
# degrade high-motion footage.
_MAX_VIDEO_BITRATE_BPS = 50_000_000

# Reels specs Meta enforces at ingest but the codec-level probe never measured.
_IG_REELS_MIN_DURATION_SEC = 3.0
_IG_REELS_MAX_DURATION_SEC = 15 * 60.0
_IG_MIN_FPS = 23.0
_IG_MAX_FPS = 60.0
_IG_AUDIO_SAMPLE_RATE = 48000
_IG_MAX_AUDIO_CHANNELS = 2
_IG_MIN_ASPECT_RATIO = 0.01
_IG_MAX_ASPECT_RATIO = 10.0
# ffprobe reports the whole ISO-BMFF family under one comma-joined format_name.
_IG_MP4_FORMAT_NAMES = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})
# r_frame_rate and avg_frame_rate diverging by more than this means variable frame rate.
_VFR_TOLERANCE_FPS = 0.35


def _ffprobe_bin() -> str:
    fb = Path(getattr(Config, "FFMPEG_BIN", "ffmpeg"))
    name = fb.name.lower()
    if name == "ffmpeg.exe":
        return str(fb.with_name("ffprobe.exe"))
    if name == "ffmpeg":
        return str(fb.with_name("ffprobe"))
    return "ffprobe"


def ffprobe_available() -> bool:
    probe = _ffprobe_bin()
    pp = Path(probe)
    exe = str(pp.resolve()) if pp.is_file() else shutil.which(probe)
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _h264_stream_needs_reencode_for_ig(
    *,
    codec_name: str,
    pix_fmt: str | None,
    profile: str | None,
) -> bool:
    """H.264/AVC that still breaks IG processing (10-bit, chroma subsampling, High 10 profile)."""
    c = (codec_name or "").lower().strip()
    if c not in _IG_VIDEO_CODECS:
        return True

    pix = (pix_fmt or "").lower().strip()
    prof = (profile or "").lower().strip()

    if "10" in prof or "high 10" in prof or "high10" in prof:
        logger.info(
            "Instagram prep: H.264 profile %r is not IG-safe; re-encoding to 8-bit yuv420p.",
            profile,
        )
        return True

    if pix:
        if pix in _IG_SAFE_PIX_FMT:
            return False
        if "10" in pix or "12" in pix or "422" in pix or "444" in pix:
            logger.info(
                "Instagram prep: pixel format %r often fails IG processing; re-encoding to yuv420p.",
                pix_fmt,
            )
            return True
        logger.info(
            "Instagram prep: pixel format %r is not yuv420p; re-encoding for IG compatibility.",
            pix_fmt,
        )
        return True

    return False


@dataclass(frozen=True)
class StreamCompatibility:
    """Per-stream IG compatibility and full Reels-spec measurements from ffprobe."""

    video_needs_reencode: bool
    audio_needs_reencode: bool
    has_audio: bool
    source_video_bitrate: int | None = None
    width: int | None = None
    height: int | None = None
    duration_sec: float | None = None
    r_frame_rate: float | None = None
    r_frame_rate_text: str | None = None
    avg_frame_rate: float | None = None
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    video_stream_count: int = 0
    audio_stream_count: int = 0
    other_stream_count: int = 0
    format_name: str | None = None


# Instagram-friendly max box: 1080 on the short side, 1920 on the long side.
_IG_MAX_SHORT_SIDE = 1080
_IG_MAX_LONG_SIDE = 1920
# Used when dimensions are unknown: guarantees even dimensions for yuv420p without resizing.
_EVEN_DIMS_VF = "scale=trunc(iw/2)*2:trunc(ih/2)*2"


def _needs_ig_dimension_downscale(width: int | None, height: int | None) -> bool:
    """True when frame exceeds the 1080x1920 / 1920x1080 IG-safe box."""
    if width is None or height is None or width <= 0 or height <= 0:
        return False
    longer = max(width, height)
    shorter = min(width, height)
    return longer > _IG_MAX_LONG_SIDE or shorter > _IG_MAX_SHORT_SIDE


def _ig_downscale_vf(
    width: int,
    height: int,
    *,
    long_side: int = _IG_MAX_LONG_SIDE,
    short_side: int = _IG_MAX_SHORT_SIDE,
) -> str:
    """
    Scale down to fit long_side x short_side, keeping AR.
    No pad/letterbox — pixels are only reduced when over the limit.
    `force_divisible_by=2` also guarantees the even dimensions yuv420p requires.
    """
    if width >= height:
        return (
            f"scale={long_side}:{short_side}:"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
    return (
        f"scale={short_side}:{long_side}:"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def _has_odd_dimensions(width: int | None, height: int | None) -> bool:
    if not width or not height:
        return False
    return width % 2 != 0 or height % 2 != 0


def _ig_scale_vf(
    width: int | None,
    height: int | None,
    *,
    long_side: int = _IG_MAX_LONG_SIDE,
    short_side: int = _IG_MAX_SHORT_SIDE,
) -> str | None:
    """
    Scale filter for a re-encode, or None when the frame already conforms.

    Downscales when the frame exceeds the box, rounds to even dimensions when they
    are odd (libx264 with -pix_fmt yuv420p rejects odd dimensions), and stays out of
    the way otherwise. Dimensions unknown means no probe, so round defensively.
    """
    if width is None or height is None or width <= 0 or height <= 0:
        return _EVEN_DIMS_VF
    if max(width, height) > long_side or min(width, height) > short_side:
        return _ig_downscale_vf(width, height, long_side=long_side, short_side=short_side)
    if _has_odd_dimensions(width, height):
        return _EVEN_DIMS_VF
    return None


def _parse_fraction(raw: str | None) -> float | None:
    """Parse ffprobe rate fields such as '30000/1001'. Returns None when undefined ('0/0')."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        if "/" in text:
            num_s, den_s = text.split("/", 1)
            num = float(num_s)
            den = float(den_s)
            if den == 0:
                return None
            return num / den
        return float(text)
    except (TypeError, ValueError):
        return None


def _aspect_ratio(width: int | None, height: int | None) -> float | None:
    if not width or not height or width <= 0 or height <= 0:
        return None
    return width / height


def _is_vfr(compat: StreamCompatibility) -> bool:
    """
    Variable frame rate: ffprobe's nominal rate (r_frame_rate) and the measured
    average diverge. Meta's ingest rejects VFR reels.
    """
    r = compat.r_frame_rate
    avg = compat.avg_frame_rate
    if r is None or avg is None or r <= 0 or avg <= 0:
        return False
    return abs(r - avg) > _VFR_TOLERANCE_FPS


# Violations repaired by re-encoding the video stream.
_VIDEO_VIOLATIONS = frozenset({"vfr", "fps_out_of_range", "odd_dimensions"})
# Violations repaired by re-encoding the audio stream.
_AUDIO_VIOLATIONS = frozenset({"audio_sample_rate", "audio_channels"})
# "extra_streams" and "container" are repaired by the -map / mux alone, so they
# need a remux but not necessarily a re-encode of either stream.


def _reels_spec_violations(compat: StreamCompatibility) -> list[str]:
    """
    Reels-spec properties that ffmpeg can repair. A non-empty list forces at least a remux.

    Deliberately excludes codec/pix_fmt/downscale checks, which the caller already
    tracks via video_needs_reencode / audio_needs_reencode / the downscale check.
    """
    violations: list[str] = []

    if _is_vfr(compat):
        violations.append("vfr")

    fps = compat.avg_frame_rate or compat.r_frame_rate
    if fps is not None and fps > 0 and not (_IG_MIN_FPS <= fps <= _IG_MAX_FPS):
        violations.append("fps_out_of_range")

    if _has_odd_dimensions(compat.width, compat.height):
        violations.append("odd_dimensions")

    if compat.has_audio:
        if (
            compat.audio_sample_rate is not None
            and compat.audio_sample_rate != _IG_AUDIO_SAMPLE_RATE
        ):
            violations.append("audio_sample_rate")
        if (
            compat.audio_channels is not None
            and compat.audio_channels > _IG_MAX_AUDIO_CHANNELS
        ):
            violations.append("audio_channels")

    if (
        compat.video_stream_count > 1
        or compat.audio_stream_count > 1
        or compat.other_stream_count > 0
    ):
        violations.append("extra_streams")

    if compat.format_name:
        names = {n.strip().lower() for n in compat.format_name.split(",")}
        if not (names & _IG_MP4_FORMAT_NAMES):
            violations.append("container")

    return violations


def check_reels_hard_limits(compat: StreamCompatibility | None) -> None:
    """
    Raise for Reels violations no re-encode can repair, before spending CPU on ffmpeg
    and before Meta answers with an opaque ProcessingFailedError.
    """
    if compat is None:
        return

    duration = compat.duration_sec
    if duration is not None and duration > 0:
        if duration < _IG_REELS_MIN_DURATION_SEC:
            raise MetaPublishUserError(
                "meta_err_ig_video_too_short",
                min_seconds=int(_IG_REELS_MIN_DURATION_SEC),
                actual=round(duration, 1),
            )
        if duration > _IG_REELS_MAX_DURATION_SEC:
            raise MetaPublishUserError(
                "meta_err_ig_video_too_long",
                max_minutes=int(_IG_REELS_MAX_DURATION_SEC // 60),
                actual=round(duration / 60, 1),
            )

    ratio = _aspect_ratio(compat.width, compat.height)
    if ratio is not None and not (
        _IG_MIN_ASPECT_RATIO <= ratio <= _IG_MAX_ASPECT_RATIO
    ):
        raise MetaPublishUserError(
            "meta_err_ig_video_aspect_ratio",
            width=compat.width,
            height=compat.height,
        )


def _resolve_ffprobe_exe() -> str | None:
    ffprobe_raw = _ffprobe_bin()
    pp = Path(ffprobe_raw)
    ffprobe = str(pp.resolve()) if pp.is_file() else shutil.which(ffprobe_raw)
    return ffprobe


def _as_int(raw: object) -> int | None:
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _as_float(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _probe_stream_compatibility(path: Path) -> StreamCompatibility | None:
    """
    Probe container, video and audio in a single ffprobe call.
    Returns None if the video stream could not be read.
    """
    ffprobe = _resolve_ffprobe_exe()
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ffprobe failed: %s", exc)
        return None

    if proc.returncode != 0 or not (proc.stdout or "").strip():
        return None

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None

    streams = parsed.get("streams") or []
    if not isinstance(streams, list):
        return None

    vstreams = [s for s in streams if s.get("codec_type") == "video"]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    other_count = len(streams) - len(vstreams) - len(astreams)

    # A still image carried as a video stream is cover art, not the reel.
    real_vstreams = [s for s in vstreams if s.get("disposition", {}).get("attached_pic") != 1]
    if real_vstreams:
        vstream = real_vstreams[0]
    elif vstreams:
        vstream = vstreams[0]
    else:
        return None

    vcodec = (vstream.get("codec_name") or "").lower().strip()
    if not vcodec:
        return None

    video_needs = False
    if vcodec not in _IG_VIDEO_CODECS:
        logger.info(
            "Instagram prep: video codec %r is not H.264; will re-encode for compatibility.",
            vcodec,
        )
        video_needs = True
    elif _h264_stream_needs_reencode_for_ig(
        codec_name=vcodec,
        pix_fmt=vstream.get("pix_fmt"),
        profile=vstream.get("profile"),
    ):
        video_needs = True

    width = _as_int(vstream.get("width"))
    height = _as_int(vstream.get("height"))
    if width is None or height is None:
        width = None
        height = None

    fmt = parsed.get("format") or {}
    # Container duration is authoritative; fall back to the video stream's own.
    duration = _as_float(fmt.get("duration")) or _as_float(vstream.get("duration"))

    r_rate_text = (vstream.get("r_frame_rate") or "").strip() or None

    has_audio = False
    audio_needs = False
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    if astreams:
        has_audio = True
        astream = astreams[0]
        acodec = (astream.get("codec_name") or "").lower().strip()
        if acodec and acodec not in _IG_AUDIO_CODECS:
            logger.info(
                "Instagram prep: audio codec %r is not AAC; will re-encode for compatibility.",
                acodec,
            )
            audio_needs = True
        audio_sample_rate = _as_int(astream.get("sample_rate"))
        audio_channels = _as_int(astream.get("channels"))

    return StreamCompatibility(
        video_needs_reencode=video_needs,
        audio_needs_reencode=audio_needs,
        has_audio=has_audio,
        # Streams remuxed by some tools carry no per-stream bit_rate. The container
        # rate (video + audio, so slightly generous) keeps maxrate anchored to the
        # real source instead of leaving the encode unbounded.
        source_video_bitrate=(
            _as_int(vstream.get("bit_rate")) or _as_int(fmt.get("bit_rate"))
        ),
        width=width,
        height=height,
        duration_sec=duration,
        r_frame_rate=_parse_fraction(r_rate_text),
        r_frame_rate_text=r_rate_text,
        avg_frame_rate=_parse_fraction(vstream.get("avg_frame_rate")),
        audio_sample_rate=audio_sample_rate,
        audio_channels=audio_channels,
        video_stream_count=len(vstreams),
        audio_stream_count=len(astreams),
        other_stream_count=max(0, other_count),
        format_name=(fmt.get("format_name") or "").strip() or None,
    )


def _probe_streams_incompatible_with_instagram(path: Path) -> bool | None:
    """
    True if video is not H.264 or an audio stream exists and is not AAC.
    False if compatible. None if probe failed (caller may treat as not incompatible).
    """
    compat = _probe_stream_compatibility(path)
    if compat is None:
        return None
    if compat.video_needs_reencode or compat.audio_needs_reencode:
        return True
    return False


def _warn_ig_codec_check_skipped(reason: str) -> None:
    logger.warning(
        "Instagram video prep: %s. "
        "Codec compatibility was not verified; HEVC/H.265 and other non-H.264 MP4s "
        "often reach Meta then fail with ProcessingFailedError. "
        "Install ffprobe next to ffmpeg on the publish host, or set IG_VIDEO_FORCE_REENCODE=true.",
        reason,
    )


@dataclass(frozen=True)
class VideoNormalizeResult:
    video_bytes: bytes
    changed: bool
    method: str
    # Reels-spec violations found in the source, for logging when Meta still rejects it.
    violations: tuple[str, ...] = ()


def ffmpeg_available() -> bool:
    ffmpeg_bin = getattr(Config, "FFMPEG_BIN", "ffmpeg")
    fb = Path(ffmpeg_bin)
    if not fb.is_file() and shutil.which(ffmpeg_bin) is None:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@lru_cache(maxsize=4)
def _ffmpeg_major_version(ffmpeg_bin: str) -> int | None:
    """Major version of the ffmpeg build, or None when it cannot be determined."""
    try:
        proc = subprocess.run(
            [ffmpeg_bin, "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # "ffmpeg version 6.1.1 ..." / "ffmpeg version n7.0 ..." / "ffmpeg version 2024-... git"
    match = re.search(r"ffmpeg version n?(\d+)\.", proc.stdout or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _cfr_args(ffmpeg_bin: str, target_fps: str) -> list[str]:
    """
    Force constant frame rate. `-r` alone already produces CFR for the mp4 muxer;
    `-fps_mode` (ffmpeg >= 5.0) makes the intent explicit. `-vsync` is its removed
    predecessor, so it is only used on older builds.
    """
    args = ["-r", target_fps]
    major = _ffmpeg_major_version(ffmpeg_bin)
    if major is None:
        return args
    args.extend(["-fps_mode", "cfr"] if major >= 5 else ["-vsync", "cfr"])
    return args


def _target_fps(compat: StreamCompatibility | None) -> str:
    """
    Frame rate for the output, chosen to lose as little smoothness as possible.

    A constant in-range source keeps its exact rate verbatim (including fractional
    ones such as 30000/1001). A variable-rate source is pinned to its own measured
    average rather than the configured target, so a 60 fps phone clip — which is
    almost always VFR — comes out 60 fps instead of being halved to 30.
    """
    fallback = str(getattr(Config, "IG_REELS_TARGET_FPS", 30))
    if compat is None:
        return fallback

    if _is_vfr(compat):
        measured = compat.avg_frame_rate
        if measured is None or measured <= 0:
            return fallback
        # Round to a whole rate: CFR output at the source's own average.
        rounded = round(measured)
        if _IG_MIN_FPS <= rounded <= _IG_MAX_FPS:
            return str(rounded)
        # Out of Meta's range (timelapse, 120 fps slow-mo): clamp to the nearest bound.
        return str(int(_IG_MAX_FPS) if rounded > _IG_MAX_FPS else fallback)

    fps = compat.avg_frame_rate or compat.r_frame_rate
    if fps is None or not (_IG_MIN_FPS <= fps <= _IG_MAX_FPS):
        return fallback
    return compat.r_frame_rate_text or fallback


def _video_encode_args(
    source_video_bitrate: int | None,
    *,
    crf: int | None = None,
    preset: str | None = None,
) -> list[str]:
    """
    libx264 settings for IG.

    No explicit -level: 1080p60 exceeds level 4.1's macroblock rate, and pinning it
    makes x264 emit a stream whose header disagrees with its content. Letting x264
    pick guarantees a conformant level.
    """
    use_crf = getattr(Config, "IG_VIDEO_CRF", 18) if crf is None else crf
    use_preset = (
        getattr(Config, "IG_VIDEO_ENCODE_PRESET", "medium") if preset is None else preset
    )
    args = [
        "-c:v",
        "libx264",
        "-crf",
        str(use_crf),
        "-preset",
        str(use_preset),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
    ]
    # Only bound the rate when the source rate is known; an unknown-bitrate source
    # gets pure CRF, which is what preserves quality on high-motion footage.
    if source_video_bitrate and source_video_bitrate > 0:
        maxrate = min(int(source_video_bitrate * 1.2), _MAX_VIDEO_BITRATE_BPS)
        bufsize = max(maxrate * 2, maxrate + 1)
        args.extend(["-maxrate", str(maxrate), "-bufsize", str(bufsize)])
    return args


def _reencode_method_name(*, reencode_video: bool, reencode_audio: bool) -> str:
    if reencode_video and reencode_audio:
        return "reencode_faststart"
    if reencode_video:
        return "video_reencode_faststart"
    if reencode_audio:
        return "audio_reencode_faststart"
    return "reencode_faststart"


def _build_reencode_cmd(
    ffmpeg_bin: str,
    in_path: Path,
    out_path: Path,
    *,
    reencode_video: bool,
    reencode_audio: bool,
    has_audio: bool,
    source_video_bitrate: int | None,
    crf: int | None = None,
    preset: str | None = None,
    scale_vf: str | None = None,
    target_fps: str | None = None,
) -> list[str]:
    """
    Build ffmpeg argv for IG-safe output.

    Every property Meta checks at ingest is pinned here, not inherited from the source:
    one video + at most one audio track (-map, so cover art and timecode tracks are
    dropped), constant frame rate inside 23-60 fps, 48 kHz stereo AAC, even dimensions
    inside the 1080x1920 box, and moov ahead of mdat.

    When the source has no audio, mux silent AAC (anullsrc) instead of -an —
    Instagram REELS often reject video-only MP4s with ProcessingFailedError.
    """
    audio_bitrate = getattr(Config, "IG_VIDEO_AUDIO_BITRATE", "192k")

    def _append_video_args(cmd: list[str]) -> None:
        if reencode_video:
            if scale_vf:
                cmd.extend(["-vf", scale_vf])
            cmd.extend(_video_encode_args(source_video_bitrate, crf=crf, preset=preset))
            if target_fps:
                cmd.extend(_cfr_args(ffmpeg_bin, target_fps))
        else:
            cmd.extend(["-c:v", "copy"])

    def _append_audio_args(cmd: list[str], *, force_encode: bool) -> None:
        if force_encode or reencode_audio:
            cmd.extend(
                [
                    "-c:a",
                    "aac",
                    "-b:a",
                    str(audio_bitrate),
                    "-ar",
                    str(_IG_AUDIO_SAMPLE_RATE),
                    "-ac",
                    str(_IG_MAX_AUDIO_CHANNELS),
                ]
            )
        else:
            cmd.extend(["-c:a", "copy"])

    # Strip edit lists / non-zero start offsets that survive a plain re-encode.
    tail = [
        "-video_track_timescale",
        "30000",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        str(out_path),
    ]

    if has_audio:
        cmd: list[str] = [ffmpeg_bin, "-y", "-i", str(in_path)]
        # Explicit maps: default stream selection would also carry cover art and data tracks.
        cmd.extend(["-map", "0:v:0", "-map", "0:a:0"])
        _append_video_args(cmd)
        _append_audio_args(cmd, force_encode=False)
        cmd.extend(tail)
        return cmd

    # No audio stream: generate silent AAC timed to the video via -shortest.
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(in_path),
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout=stereo:sample_rate={_IG_AUDIO_SAMPLE_RATE}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    _append_video_args(cmd)
    _append_audio_args(cmd, force_encode=True)
    cmd.append("-shortest")
    cmd.extend(tail)
    return cmd


def normalize_instagram_video_bytes(
    video_bytes: bytes, *, safe_mode: bool = False
) -> VideoNormalizeResult:
    """
    Ensure MP4 bytes satisfy Instagram expectations:
    - If IG_VIDEO_REENCODE_IF_INCOMPATIBLE: ffprobe; re-encode when video is not H.264,
      H.264 is 10-bit / non-yuv420p, or an audio stream exists and is not AAC.
    - Re-encode when the source violates any Reels spec ffmpeg can repair: variable or
      out-of-range frame rate, audio not 48 kHz stereo, extra streams, non-MP4 container.
    - Raise for violations no re-encode can repair (duration, aspect ratio).
    - If frame exceeds 1080x1920 / 1920x1080: downscale. Every re-encode also rounds
      dimensions to even values.
    - If mdat appears before moov: remux with -c copy +faststart when codecs already match.
    - IG_VIDEO_FORCE_REENCODE: always re-encode to the same IG-safe profile.
    - safe_mode: last-resort profile (smaller, faster, forced 30 fps) for the retry after
      Meta rejects the first payload.
    """
    layout_bad = _mp4_moov_before_mdat(video_bytes) is False
    force = bool(getattr(Config, "IG_VIDEO_FORCE_REENCODE", False) or safe_mode)
    reencode_if_inc = getattr(Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    can_probe = ffprobe_available()
    want_probe = reencode_if_inc and can_probe

    if not force and not layout_bad and not can_probe:
        if reencode_if_inc:
            _warn_ig_codec_check_skipped(
                "ffprobe unavailable while IG_VIDEO_REENCODE_IF_INCOMPATIBLE is enabled"
            )
        return VideoNormalizeResult(video_bytes=video_bytes, changed=False, method="none")

    long_side = _IG_MAX_LONG_SIDE
    short_side = _IG_MAX_SHORT_SIDE
    crf_override: int | None = None
    preset_override: str | None = None
    if safe_mode:
        long_side = int(getattr(Config, "IG_REELS_SAFE_MODE_LONG_SIDE", 1280))
        short_side = max(2, int(long_side * _IG_MAX_SHORT_SIDE / _IG_MAX_LONG_SIDE) // 2 * 2)
        crf_override = int(getattr(Config, "IG_REELS_SAFE_MODE_CRF", 23))
        preset_override = "veryfast"

    with tempfile.TemporaryDirectory(prefix="ig-video-normalize-") as tmpdir:
        in_path = Path(tmpdir) / "input.mp4"
        out_copy_path = Path(tmpdir) / "output-copy-faststart.mp4"
        out_reencode_path = Path(tmpdir) / "output-reencode-faststart.mp4"

        in_path.write_bytes(video_bytes)

        compat: StreamCompatibility | None = None
        probe_inconclusive = False
        violations: list[str] = []

        if can_probe:
            compat = _probe_stream_compatibility(in_path)
            probe_inconclusive = compat is None

        # Fail fast on what no re-encode can repair, before spending CPU.
        check_reels_hard_limits(compat)

        if compat is not None:
            violations = _reels_spec_violations(compat)
            found = set(violations)
            video_needs = bool(
                (want_probe and compat.video_needs_reencode)
                or _needs_ig_dimension_downscale(compat.width, compat.height)
                or (found & _VIDEO_VIOLATIONS)
            )
            audio_needs = bool(
                (want_probe and compat.audio_needs_reencode)
                or (found & _AUDIO_VIOLATIONS)
            )
            has_audio = compat.has_audio
            source_bitrate = compat.source_video_bitrate
            width = compat.width
            height = compat.height
            if violations:
                logger.info(
                    "Instagram prep: source violates Reels spec (%s); repairing.",
                    ", ".join(violations),
                )
        else:
            # No probe: assume nothing. has_audio=False so a silent source still gets
            # silent AAC muxed — a video-only MP4 is a known ProcessingFailedError cause.
            video_needs = False
            audio_needs = False
            has_audio = False
            source_bitrate = None
            width = None
            height = None

        incompatible = force or video_needs or audio_needs
        # Extra tracks and a non-MP4 container are fixed by the mux and -map alone.
        needs_remux = layout_bad or bool(
            set(violations) & {"extra_streams", "container"}
        )

        if not incompatible and not needs_remux:
            if probe_inconclusive:
                logger.warning(
                    "Instagram video prep: ffprobe did not determine stream compatibility "
                    "(probe failed or unreadable file). Meta may reject the upload "
                    "(ProcessingFailedError). Fix ffprobe, set IG_VIDEO_FORCE_REENCODE=true, "
                    "or enable IG_VIDEO_STRICT_PROBE=true to fail here instead of at Meta."
                )
                if getattr(Config, "IG_VIDEO_STRICT_PROBE", False):
                    raise MetaPublishUserError("meta_err_ig_video_probe_ambiguous")
            return VideoNormalizeResult(video_bytes=video_bytes, changed=False, method="none")

        # Downscales when oversized, rounds odd frames to even, otherwise None.
        scale_vf = _ig_scale_vf(width, height, long_side=long_side, short_side=short_side)
        target_fps = (
            str(getattr(Config, "IG_REELS_TARGET_FPS", 30))
            if safe_mode
            else _target_fps(compat)
        )
        if _needs_ig_dimension_downscale(width, height):
            logger.info(
                "Instagram prep: frame %sx%s exceeds the %sx%s box; downscaling via %s.",
                width,
                height,
                short_side,
                long_side,
                scale_vf,
            )

        if not getattr(Config, "IG_VIDEO_AUTOFIX_ENABLED", True):
            raise MetaPublishUserError(
                "meta_err_ig_video_prepare_failed",
                detail="autofix_disabled",
            )

        if not ffmpeg_available():
            logger.warning("Instagram auto-fix skipped: ffmpeg unavailable.")
            raise MetaPublishUserError(
                "meta_err_ig_video_prepare_failed",
                detail="ffmpeg_unavailable",
            )

        ffmpeg_bin = getattr(Config, "FFMPEG_BIN", "ffmpeg")

        # Remux-only is valid when layout / container / extra tracks are the sole
        # problem; any codec or timing mismatch needs a real encode.
        try_copy = needs_remux and not video_needs and not audio_needs and not force
        copy_proc: subprocess.CompletedProcess[str] | None = None
        reencode_proc: subprocess.CompletedProcess[str] | None = None

        if try_copy:
            copy_cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                str(in_path),
                # Drops cover art, timecode and data tracks that IG ingest rejects.
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out_copy_path),
            ]
            copy_proc = _run_ffmpeg(copy_cmd)
            if copy_proc.returncode == 0 and out_copy_path.exists():
                out_bytes = out_copy_path.read_bytes()
                if _mp4_moov_before_mdat(out_bytes) is not False:
                    logger.info("Instagram video auto-fix succeeded via remux copy faststart.")
                    return VideoNormalizeResult(
                        video_bytes=out_bytes,
                        changed=True,
                        method="copy_faststart",
                        violations=tuple(violations),
                    )

            if not getattr(Config, "IG_VIDEO_AUTOFIX_REENCODE_FALLBACK", True):
                snippet = _ffmpeg_stderr_snippet(copy_proc)
                logger.warning(
                    "Instagram auto-fix remux failed and re-encode fallback disabled. stderr=%s",
                    snippet,
                )
                raise MetaPublishUserError(
                    "meta_err_ig_video_prepare_failed",
                    detail=_prepare_fail_detail("remux_failed_no_fallback", snippet),
                )

        # Scaling and CFR enforcement both require a video encode; when only the
        # audio is at fault the video stream is still copied verbatim.
        reencode_video = bool(force or video_needs)
        # When muxing silent AAC, treat as audio re-encode for method naming.
        reencode_audio = force or audio_needs or not has_audio
        method = _reencode_method_name(
            reencode_video=reencode_video, reencode_audio=reencode_audio
        )
        if safe_mode:
            method = f"{method}_safe_mode"
        elif _needs_ig_dimension_downscale(width, height):
            method = f"{method}_downscale"

        def _try_reencode(
            out_path: Path,
            *,
            crf: int | None = None,
            preset: str | None = None,
        ) -> tuple[bytes | None, subprocess.CompletedProcess[str]]:
            cmd = _build_reencode_cmd(
                ffmpeg_bin,
                in_path,
                out_path,
                reencode_video=reencode_video,
                reencode_audio=force or audio_needs,
                has_audio=has_audio,
                source_video_bitrate=source_bitrate,
                crf=crf if crf is not None else crf_override,
                preset=preset if preset is not None else preset_override,
                scale_vf=scale_vf,
                target_fps=target_fps,
            )
            proc = _run_ffmpeg(cmd)
            if proc.returncode == 0 and out_path.exists():
                out_bytes = out_path.read_bytes()
                if _mp4_moov_before_mdat(out_bytes) is not False:
                    return out_bytes, proc
            return None, proc

        out_bytes, reencode_proc = _try_reencode(out_reencode_path)
        if out_bytes is not None:
            logger.info("Instagram video auto-fix succeeded via %s.", method)
            return VideoNormalizeResult(
                video_bytes=out_bytes,
                changed=True,
                method=method,
                violations=tuple(violations),
            )

        # One faster retry after timeout/failure (common on small VPS).
        base_crf = crf_override if crf_override is not None else int(
            getattr(Config, "IG_VIDEO_CRF", 18)
        )
        fast_crf = min(51, base_crf + 4)
        out_retry_path = Path(tmpdir) / "output-reencode-fast-retry.mp4"
        logger.warning(
            "Instagram auto-fix re-encode failed (snippet=%s); retrying with "
            "preset=veryfast crf=%s.",
            _ffmpeg_stderr_snippet(reencode_proc),
            fast_crf,
        )
        out_bytes, retry_proc = _try_reencode(
            out_retry_path, crf=fast_crf, preset="veryfast"
        )
        if out_bytes is not None:
            fast_method = f"{method}_fast_retry"
            logger.info(
                "Instagram video auto-fix succeeded via %s.", fast_method
            )
            return VideoNormalizeResult(
                video_bytes=out_bytes,
                changed=True,
                method=fast_method,
                violations=tuple(violations),
            )

        snippet = _ffmpeg_stderr_snippet(retry_proc or reencode_proc or copy_proc)
        logger.warning(
            "Instagram auto-fix failed (copy + re-encode + fast retry). stderr=%s",
            snippet,
        )
        raise MetaPublishUserError(
            "meta_err_ig_video_prepare_failed",
            detail=_prepare_fail_detail("remux_and_reencode_failed", snippet),
        )


def _sanitize_ffmpeg_detail_text(raw: str) -> str:
    """Normalize ffmpeg/exception text for user-facing detail (no argv dumps)."""
    text = (raw or "").strip()
    if not text:
        return ""
    # TimeoutExpired / CalledProcessError str() starts with Command '[...]'
    if text.startswith("Command ") or text.startswith("Command["):
        lower = text.lower()
        if "timed out" in lower:
            # Keep only the timeout message if present after the argv list.
            idx = lower.rfind("timed out")
            if idx >= 0:
                text = text[idx:]
            else:
                text = "timed out"
        elif "returned non-zero" in lower:
            idx = lower.rfind("returned non-zero")
            text = text[idx:] if idx >= 0 else "non-zero exit"
        else:
            text = "ffmpeg_command_failed"
    return (
        text.replace("{", "(")
        .replace("}", ")")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _ffmpeg_stderr_snippet(
    proc: subprocess.CompletedProcess[str] | None, max_len: int = 240
) -> str:
    if proc is None:
        return ""
    raw = (proc.stderr or proc.stdout or "").strip()
    if not raw:
        return f"exit={proc.returncode}"

    # Our timeout marker is already reason-first — keep from the start.
    if raw.startswith("ffmpeg_timeout_") or raw.startswith("ffmpeg_exec_error:"):
        safe = _sanitize_ffmpeg_detail_text(raw)
        return safe[:max_len] if len(safe) > max_len else safe

    safe = _sanitize_ffmpeg_detail_text(raw)
    if not safe:
        return f"exit={proc.returncode}"
    # ffmpeg puts the actionable error at the end of stderr.
    if len(safe) > max_len:
        return safe[-max_len:]
    return safe


def _prepare_fail_detail(reason: str, snippet: str) -> str:
    snippet = (snippet or "").strip()
    if not snippet:
        return reason
    return f"{reason}: {snippet}"


def _ffmpeg_timeout_seconds() -> int:
    try:
        return max(30, int(getattr(Config, "IG_VIDEO_FFMPEG_TIMEOUT", 600)))
    except (TypeError, ValueError):
        return 600


def _run_ffmpeg(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    timeout = _ffmpeg_timeout_seconds()
    logger.debug(
        "Running ffmpeg command for Instagram auto-fix: %s (timeout=%ss)",
        cmd[:4],
        timeout,
    )
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = ""
        if exc.stderr:
            if isinstance(exc.stderr, bytes):
                partial = exc.stderr.decode("utf-8", errors="replace")
            else:
                partial = str(exc.stderr)
        partial = _sanitize_ffmpeg_detail_text(partial)
        msg = f"ffmpeg_timeout_{timeout}s"
        if partial:
            # Prefer tail of partial stderr.
            tail = partial[-200:] if len(partial) > 200 else partial
            msg = f"{msg}; {tail}"
        logger.warning("ffmpeg timed out after %ss for Instagram auto-fix.", timeout)
        return subprocess.CompletedProcess(
            args=cmd, returncode=124, stdout="", stderr=msg
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffmpeg execution failure: %s", exc)
        msg = f"ffmpeg_exec_error: {type(exc).__name__}: {_sanitize_ffmpeg_detail_text(str(exc))}"
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout="", stderr=msg
        )
