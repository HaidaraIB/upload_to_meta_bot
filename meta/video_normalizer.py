from __future__ import annotations

from dataclasses import dataclass
import json
import logging
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
_MAX_VIDEO_BITRATE_BPS = 50_000_000


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
    """Per-stream IG compatibility from ffprobe."""

    video_needs_reencode: bool
    audio_needs_reencode: bool
    has_audio: bool
    source_video_bitrate: int | None = None


def _resolve_ffprobe_exe() -> str | None:
    ffprobe_raw = _ffprobe_bin()
    pp = Path(ffprobe_raw)
    ffprobe = str(pp.resolve()) if pp.is_file() else shutil.which(ffprobe_raw)
    return ffprobe


def _probe_stream_compatibility(path: Path) -> StreamCompatibility | None:
    """
    Probe video and audio streams separately.
    Returns None if the video stream could not be read.
    """
    ffprobe = _resolve_ffprobe_exe()
    if not ffprobe:
        return None
    try:
        vjson = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,pix_fmt,profile,bit_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        ajson = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
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

    if vjson.returncode != 0 or not (vjson.stdout or "").strip():
        return None

    try:
        parsed = json.loads(vjson.stdout)
        streams = parsed.get("streams") or []
        vstream = streams[0] if streams else {}
    except (json.JSONDecodeError, IndexError, TypeError):
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

    source_bitrate: int | None = None
    br_raw = vstream.get("bit_rate")
    if br_raw is not None:
        try:
            source_bitrate = int(br_raw)
            if source_bitrate <= 0:
                source_bitrate = None
        except (TypeError, ValueError):
            source_bitrate = None

    has_audio = False
    audio_needs = False
    if ajson.returncode == 0 and (ajson.stdout or "").strip():
        try:
            aparsed = json.loads(ajson.stdout)
            astreams = aparsed.get("streams") or []
            if astreams:
                has_audio = True
                acodec = (astreams[0].get("codec_name") or "").lower().strip()
                if acodec and acodec not in _IG_AUDIO_CODECS:
                    logger.info(
                        "Instagram prep: audio codec %r is not AAC; will re-encode for compatibility.",
                        acodec,
                    )
                    audio_needs = True
        except (json.JSONDecodeError, IndexError, TypeError):
            pass

    return StreamCompatibility(
        video_needs_reencode=video_needs,
        audio_needs_reencode=audio_needs,
        has_audio=has_audio,
        source_video_bitrate=source_bitrate,
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


def _video_encode_args(source_video_bitrate: int | None) -> list[str]:
    crf = getattr(Config, "IG_VIDEO_CRF", 18)
    preset = getattr(Config, "IG_VIDEO_ENCODE_PRESET", "medium")
    args = [
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.1",
    ]
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
) -> list[str]:
    audio_bitrate = getattr(Config, "IG_VIDEO_AUDIO_BITRATE", "192k")
    cmd: list[str] = [ffmpeg_bin, "-y", "-i", str(in_path)]

    if reencode_video:
        cmd.extend(_video_encode_args(source_video_bitrate))
    else:
        cmd.extend(["-c:v", "copy"])

    if has_audio:
        if reencode_audio:
            cmd.extend(["-c:a", "aac", "-b:a", str(audio_bitrate)])
        else:
            cmd.extend(["-c:a", "copy"])
    else:
        cmd.append("-an")

    cmd.extend(["-movflags", "+faststart", str(out_path)])
    return cmd


def normalize_instagram_video_bytes(video_bytes: bytes) -> VideoNormalizeResult:
    """
    Ensure MP4 bytes satisfy Instagram expectations:
    - If IG_VIDEO_REENCODE_IF_INCOMPATIBLE: ffprobe; re-encode when video is not H.264,
      H.264 is 10-bit / non-yuv420p, or an audio stream exists and is not AAC.
    - If mdat appears before moov: remux with -c copy +faststart when codecs already match.
    - Re-encode with libx264/AAC + faststart when codecs mismatch or remux is insufficient.
    - IG_VIDEO_FORCE_REENCODE: always re-encode to the same IG-safe profile.
    """
    layout_bad = _mp4_moov_before_mdat(video_bytes) is False
    force = getattr(Config, "IG_VIDEO_FORCE_REENCODE", False)
    reencode_if_inc = getattr(Config, "IG_VIDEO_REENCODE_IF_INCOMPATIBLE", True)
    want_probe = reencode_if_inc and ffprobe_available()

    if not force and not layout_bad and not want_probe:
        if reencode_if_inc and not ffprobe_available():
            _warn_ig_codec_check_skipped(
                "ffprobe unavailable while IG_VIDEO_REENCODE_IF_INCOMPATIBLE is enabled"
            )
        return VideoNormalizeResult(video_bytes=video_bytes, changed=False, method="none")

    with tempfile.TemporaryDirectory(prefix="ig-video-normalize-") as tmpdir:
        in_path = Path(tmpdir) / "input.mp4"
        out_copy_path = Path(tmpdir) / "output-copy-faststart.mp4"
        out_reencode_path = Path(tmpdir) / "output-reencode-faststart.mp4"

        in_path.write_bytes(video_bytes)

        compat: StreamCompatibility | None = None
        probe_inconclusive = False
        if force:
            video_needs = True
            audio_needs = True
            has_audio = True
            source_bitrate = None
        elif want_probe:
            compat = _probe_stream_compatibility(in_path)
            if compat is None:
                probe_inconclusive = True
                video_needs = False
                audio_needs = False
                has_audio = False
                source_bitrate = None
            else:
                video_needs = compat.video_needs_reencode
                audio_needs = compat.audio_needs_reencode
                has_audio = compat.has_audio
                source_bitrate = compat.source_video_bitrate
        else:
            video_needs = False
            audio_needs = False
            has_audio = False
            source_bitrate = None

        incompatible = force or video_needs or audio_needs

        if not force and not incompatible and not layout_bad:
            if want_probe and probe_inconclusive:
                logger.warning(
                    "Instagram video prep: ffprobe did not determine stream compatibility "
                    "(probe failed or unreadable file). Meta may reject the upload "
                    "(ProcessingFailedError). Fix ffprobe, set IG_VIDEO_FORCE_REENCODE=true, "
                    "or enable IG_VIDEO_STRICT_PROBE=true to fail here instead of at Meta."
                )
                if getattr(Config, "IG_VIDEO_STRICT_PROBE", False):
                    raise MetaPublishUserError("meta_err_ig_video_probe_ambiguous")
            return VideoNormalizeResult(video_bytes=video_bytes, changed=False, method="none")

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

        try_copy = layout_bad and not video_needs and not audio_needs and not force
        copy_proc: subprocess.CompletedProcess[str] | None = None
        reencode_proc: subprocess.CompletedProcess[str] | None = None

        if try_copy:
            copy_cmd = [
                ffmpeg_bin,
                "-y",
                "-i",
                str(in_path),
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
                        video_bytes=out_bytes, changed=True, method="copy_faststart"
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

        reencode_video = force or video_needs
        reencode_audio = force or audio_needs
        method = _reencode_method_name(
            reencode_video=reencode_video, reencode_audio=reencode_audio
        )
        reencode_cmd = _build_reencode_cmd(
            ffmpeg_bin,
            in_path,
            out_reencode_path,
            reencode_video=reencode_video,
            reencode_audio=reencode_audio,
            has_audio=has_audio,
            source_video_bitrate=source_bitrate,
        )
        reencode_proc = _run_ffmpeg(reencode_cmd)
        if reencode_proc.returncode == 0 and out_reencode_path.exists():
            out_bytes = out_reencode_path.read_bytes()
            if _mp4_moov_before_mdat(out_bytes) is not False:
                logger.info(
                    "Instagram video auto-fix succeeded via %s.", method
                )
                return VideoNormalizeResult(
                    video_bytes=out_bytes, changed=True, method=method
                )

        snippet = _ffmpeg_stderr_snippet(reencode_proc or copy_proc)
        logger.warning(
            "Instagram auto-fix failed (copy + re-encode). stderr=%s",
            snippet,
        )
        raise MetaPublishUserError(
            "meta_err_ig_video_prepare_failed",
            detail=_prepare_fail_detail("remux_and_reencode_failed", snippet),
        )


def _ffmpeg_stderr_snippet(
    proc: subprocess.CompletedProcess[str] | None, max_len: int = 240
) -> str:
    if proc is None:
        return ""
    raw = (proc.stderr or proc.stdout or "").strip()
    if not raw:
        return f"exit={proc.returncode}"
    # Avoid breaking str.format in localized TEXTS.
    safe = raw.replace("{", "(").replace("}", ")").replace("\r", " ").replace("\n", " ")
    return safe[:max_len]


def _prepare_fail_detail(reason: str, snippet: str) -> str:
    snippet = (snippet or "").strip()
    if not snippet:
        return reason
    return f"{reason}: {snippet}"


def _run_ffmpeg(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    logger.debug("Running ffmpeg command for Instagram auto-fix: %s", cmd[:4])
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffmpeg execution failure: %s", exc)
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr=str(exc))
