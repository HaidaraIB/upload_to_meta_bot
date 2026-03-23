from __future__ import annotations

from dataclasses import dataclass
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from Config import Config
from meta.errors import MetaPublishUserError
from meta.ig_video_preflight import _mp4_moov_before_mdat

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VideoNormalizeResult:
    video_bytes: bytes
    changed: bool
    method: str


def ffmpeg_available() -> bool:
    ffmpeg_bin = getattr(Config, "FFMPEG_BIN", "ffmpeg")
    if shutil.which(ffmpeg_bin) is None:
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
    Ensure MP4 bytes satisfy Instagram faststart expectations.
    - If already fine/unknown: return unchanged.
    - If mdat appears before moov: attempt ffmpeg copy faststart.
    - If copy fails and fallback enabled: try compatible re-encode + faststart.
    """
    order = _mp4_moov_before_mdat(video_bytes)
    if order is not False:
        return VideoNormalizeResult(video_bytes=video_bytes, changed=False, method="none")

    if not getattr(Config, "IG_VIDEO_AUTOFIX_ENABLED", True):
        raise MetaPublishUserError("meta_err_ig_video_prepare_failed")

    if not ffmpeg_available():
        logger.warning("Instagram auto-fix skipped: ffmpeg unavailable.")
        raise MetaPublishUserError("meta_err_ig_video_prepare_failed")

    ffmpeg_bin = getattr(Config, "FFMPEG_BIN", "ffmpeg")
    with tempfile.TemporaryDirectory(prefix="ig-video-normalize-") as tmpdir:
        in_path = Path(tmpdir) / "input.mp4"
        out_copy_path = Path(tmpdir) / "output-copy-faststart.mp4"
        out_reencode_path = Path(tmpdir) / "output-reencode-faststart.mp4"

        in_path.write_bytes(video_bytes)

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
