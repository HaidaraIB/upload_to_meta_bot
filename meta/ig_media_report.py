"""
Post-mortem description of the exact bytes handed to Meta.

Meta answers a rejected reel upload with nothing but
``{"debug_info":{"retriable":false,"type":"ProcessingFailedError","message":"Request processing failed"}}``,
which names no property and no stream. Without a probe of the payload we actually sent,
a failure is undiagnosable. Everything here is best-effort: a diagnostic path must never
raise over the error it is describing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Probing happens on an already-failed request, so keep it short.
_PROBE_TIMEOUT_SEC = 60

_FORMAT_FIELDS = ("format_name", "format_long_name", "duration", "size", "bit_rate")
_VIDEO_FIELDS = (
    "codec_name",
    "profile",
    "level",
    "pix_fmt",
    "width",
    "height",
    "coded_width",
    "coded_height",
    "sample_aspect_ratio",
    "display_aspect_ratio",
    "r_frame_rate",
    "avg_frame_rate",
    "time_base",
    "start_time",
    "duration",
    "nb_frames",
    "bit_rate",
    "color_space",
    "color_transfer",
    "color_primaries",
)
_AUDIO_FIELDS = (
    "codec_name",
    "profile",
    "sample_rate",
    "channels",
    "channel_layout",
    "start_time",
    "duration",
    "bit_rate",
)


def _pick(stream: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: stream[f] for f in fields if stream.get(f) is not None}


def describe_video_bytes(video_bytes: bytes) -> dict[str, Any]:
    """
    ffprobe the given bytes and return a compact, JSON-safe description.

    Always returns a dict. On any failure the dict carries a ``probe_error`` key
    instead of raising — callers are already handling a publish failure.
    """
    from meta.video_normalizer import _resolve_ffprobe_exe

    report: dict[str, Any] = {
        "size_bytes": len(video_bytes),
        "sha256": hashlib.sha256(video_bytes).hexdigest()[:16],
        "ftyp": _ftyp_brand(video_bytes),
    }

    ffprobe = _resolve_ffprobe_exe()
    if not ffprobe:
        report["probe_error"] = "ffprobe_unavailable"
        return report

    try:
        with tempfile.TemporaryDirectory(prefix="ig-media-report-") as tmpdir:
            path = Path(tmpdir) / "payload.mp4"
            path.write_bytes(video_bytes)
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
                timeout=_PROBE_TIMEOUT_SEC,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        report["probe_error"] = f"{type(exc).__name__}: {exc}"
        return report

    if proc.returncode != 0 or not (proc.stdout or "").strip():
        report["probe_error"] = (proc.stderr or f"exit={proc.returncode}").strip()[:300]
        return report

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        report["probe_error"] = f"probe_json_invalid: {exc}"
        return report

    report["format"] = _pick(parsed.get("format") or {}, _FORMAT_FIELDS)

    streams = parsed.get("streams") or []
    report["nb_streams"] = len(streams)
    report["video_streams"] = [
        _pick(s, _VIDEO_FIELDS) for s in streams if s.get("codec_type") == "video"
    ]
    report["audio_streams"] = [
        _pick(s, _AUDIO_FIELDS) for s in streams if s.get("codec_type") == "audio"
    ]
    other = [
        s.get("codec_type")
        for s in streams
        if s.get("codec_type") not in ("video", "audio")
    ]
    if other:
        report["other_stream_types"] = other

    return report


def _ftyp_brand(video_bytes: bytes) -> str | None:
    """Major brand from the MP4 ftyp box, or None when this is not an ISO-BMFF file."""
    if len(video_bytes) < 12 or video_bytes[4:8] != b"ftyp":
        return None
    return video_bytes[8:12].decode("ascii", errors="replace")


def summarize_report(report: dict[str, Any]) -> str:
    """One-line human summary for Telegram / log prefixes."""
    if "probe_error" in report:
        return (
            f"size={report.get('size_bytes')}B sha={report.get('sha256')} "
            f"probe_error={report['probe_error']}"
        )

    parts: list[str] = [
        f"size={report.get('size_bytes')}B",
        f"sha={report.get('sha256')}",
    ]
    fmt = report.get("format") or {}
    if fmt.get("format_name"):
        parts.append(f"container={fmt['format_name']}")
    if fmt.get("duration"):
        parts.append(f"duration={fmt['duration']}s")

    vstreams = report.get("video_streams") or []
    if vstreams:
        v = vstreams[0]
        parts.append(
            f"video={v.get('codec_name')}/{v.get('profile')}@L{v.get('level')} "
            f"{v.get('width')}x{v.get('height')} {v.get('pix_fmt')} "
            f"r={v.get('r_frame_rate')} avg={v.get('avg_frame_rate')}"
        )
    else:
        parts.append("video=NONE")

    astreams = report.get("audio_streams") or []
    if astreams:
        a = astreams[0]
        parts.append(
            f"audio={a.get('codec_name')} {a.get('sample_rate')}Hz "
            f"{a.get('channels')}ch"
        )
    else:
        parts.append("audio=NONE")

    if len(vstreams) > 1 or len(astreams) > 1 or report.get("other_stream_types"):
        parts.append(f"nb_streams={report.get('nb_streams')}")

    return " ".join(str(p) for p in parts)
