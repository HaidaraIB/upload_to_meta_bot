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


def _video_encode_args(
    source_video_bitrate: int | None,
    *,
    crf: int | None = None,
    preset: str | None = None,
) -> list[str]:
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
    crf: int | None = None,
    preset: str | None = None,
) -> list[str]:
    """
    Build ffmpeg argv for IG-safe output.
    When the source has no audio, mux silent AAC (anullsrc) instead of -an —
    Instagram REELS often reject video-only MP4s with ProcessingFailedError.
    """
    audio_bitrate = getattr(Config, "IG_VIDEO_AUDIO_BITRATE", "192k")

    if has_audio:
        cmd: list[str] = [ffmpeg_bin, "-y", "-i", str(in_path)]
        if reencode_video:
            cmd.extend(
                _video_encode_args(
                    source_video_bitrate, crf=crf, preset=preset
                )
            )
        else:
            cmd.extend(["-c:v", "copy"])
        if reencode_audio:
            cmd.extend(["-c:a", "aac", "-b:a", str(audio_bitrate)])
        else:
            cmd.extend(["-c:a", "copy"])
        cmd.extend(["-movflags", "+faststart", str(out_path)])
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
        "anullsrc=channel_layout=stereo:sample_rate=44100",
    ]
    if reencode_video:
        cmd.extend(
            _video_encode_args(source_video_bitrate, crf=crf, preset=preset)
        )
    else:
        cmd.extend(["-c:v", "copy"])
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            str(audio_bitrate),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )
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
        # When muxing silent AAC, treat as audio re-encode for method naming.
        reencode_audio = force or audio_needs or not has_audio
        method = _reencode_method_name(
            reencode_video=reencode_video, reencode_audio=reencode_audio
        )

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
                crf=crf,
                preset=preset,
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
                video_bytes=out_bytes, changed=True, method=method
            )

        # One faster retry after timeout/failure (common on small VPS).
        base_crf = int(getattr(Config, "IG_VIDEO_CRF", 18))
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
                video_bytes=out_bytes, changed=True, method=fast_method
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
