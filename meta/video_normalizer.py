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


def _probe_streams_incompatible_with_instagram(path: Path) -> bool | None:
    """
    True if video is not H.264 or an audio stream exists and is not AAC.
    False if compatible. None if probe failed (caller may treat as not incompatible).
    """
    ffprobe_raw = _ffprobe_bin()
    pp = Path(ffprobe_raw)
    ffprobe = str(pp.resolve()) if pp.is_file() else shutil.which(ffprobe_raw)
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
                "stream=codec_name,pix_fmt,profile",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        a = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
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

    if vcodec not in _IG_VIDEO_CODECS:
        logger.info(
            "Instagram prep: video codec %r is not H.264; will re-encode for compatibility.",
            vcodec,
        )
        return True

    if _h264_stream_needs_reencode_for_ig(
        codec_name=vcodec,
        pix_fmt=vstream.get("pix_fmt"),
        profile=vstream.get("profile"),
    ):
        return True

    if a.returncode != 0:
        return False

    aline = (a.stdout or "").strip().lower()
    if not aline:
        return False

    acodec = aline.split("\n")[0].strip()
    if acodec not in _IG_AUDIO_CODECS:
        logger.info(
            "Instagram prep: audio codec %r is not AAC; will re-encode for compatibility.",
            acodec,
        )
        return True

    return False


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
        return VideoNormalizeResult(video_bytes=video_bytes, changed=False, method="none")

    with tempfile.TemporaryDirectory(prefix="ig-video-normalize-") as tmpdir:
        in_path = Path(tmpdir) / "input.mp4"
        out_copy_path = Path(tmpdir) / "output-copy-faststart.mp4"
        out_reencode_path = Path(tmpdir) / "output-reencode-faststart.mp4"

        in_path.write_bytes(video_bytes)

        incompatible = False
        if force:
            incompatible = True
        elif want_probe:
            pr = _probe_streams_incompatible_with_instagram(in_path)
            incompatible = pr is True

        if not force and not incompatible and not layout_bad:
            return VideoNormalizeResult(video_bytes=video_bytes, changed=False, method="none")

        if not getattr(Config, "IG_VIDEO_AUTOFIX_ENABLED", True):
            raise MetaPublishUserError("meta_err_ig_video_prepare_failed")

        if not ffmpeg_available():
            logger.warning("Instagram auto-fix skipped: ffmpeg unavailable.")
            raise MetaPublishUserError("meta_err_ig_video_prepare_failed")

        ffmpeg_bin = getattr(Config, "FFMPEG_BIN", "ffmpeg")

        try_copy = layout_bad and not incompatible and not force

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
                logger.warning(
                    "Instagram auto-fix remux failed and re-encode fallback disabled."
                )
                raise MetaPublishUserError("meta_err_ig_video_prepare_failed")

        reencode_cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(in_path),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out_reencode_path),
        ]
        reencode_proc = _run_ffmpeg(reencode_cmd)
        if reencode_proc.returncode == 0 and out_reencode_path.exists():
            out_bytes = out_reencode_path.read_bytes()
            if _mp4_moov_before_mdat(out_bytes) is not False:
                logger.info("Instagram video auto-fix succeeded via re-encode faststart.")
                return VideoNormalizeResult(
                    video_bytes=out_bytes, changed=True, method="reencode_faststart"
                )

        logger.warning("Instagram auto-fix failed (copy + re-encode).")
        raise MetaPublishUserError("meta_err_ig_video_prepare_failed")


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
