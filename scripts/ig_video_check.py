"""
Diagnose why Instagram rejected (or would reject) a video, without publishing.

Meta answers a failed reel upload with only
``{"debug_info":{"retriable":false,"type":"ProcessingFailedError", ...}}``, naming
no property. This runs the same preparation the publisher runs and prints the
verdict at every stage, so a failure has a concrete cause.

Usage:
    python -m scripts.ig_video_check <path-or-url> [--post-type reel] [--safe-mode]

The argument accepts a local file or the `mediaUrl` from a failed `meta_posts` doc.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meta.errors import MetaPublishUserError  # noqa: E402
from meta.ig_media_report import describe_video_bytes, summarize_report  # noqa: E402
from meta.ig_video_preflight import instagram_video_binary_preflight  # noqa: E402
from meta.video_normalizer import (  # noqa: E402
    _probe_stream_compatibility,
    _reels_spec_violations,
    _target_fps,
    check_reels_hard_limits,
    ffmpeg_available,
    ffprobe_available,
    normalize_instagram_video_bytes,
)


def _load(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        print(f"Downloading {source} ...")
        req = Request(source, headers={"User-Agent": "ig-video-check/1.0"})
        with urlopen(req, timeout=300) as resp:
            return resp.read()
    return Path(source).read_bytes()


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _probe_source(video_bytes: bytes) -> None:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ig-check-") as tmp:
        path = Path(tmp) / "input.mp4"
        path.write_bytes(video_bytes)
        compat = _probe_stream_compatibility(path)

    if compat is None:
        print("  ffprobe could not read this file (probe inconclusive).")
        print("  Every codec / dimension / Reels-spec check is skipped for such files.")
        return

    print(f"  container      : {compat.format_name}")
    print(f"  dimensions     : {compat.width}x{compat.height}")
    print(f"  duration       : {compat.duration_sec}s")
    print(f"  r_frame_rate   : {compat.r_frame_rate}")
    print(f"  avg_frame_rate : {compat.avg_frame_rate}")
    print(f"  audio          : {compat.audio_sample_rate}Hz {compat.audio_channels}ch "
          f"(present={compat.has_audio})")
    print(f"  streams        : video={compat.video_stream_count} "
          f"audio={compat.audio_stream_count} other={compat.other_stream_count}")
    print(f"  needs reencode : video={compat.video_needs_reencode} "
          f"audio={compat.audio_needs_reencode}")

    violations = _reels_spec_violations(compat)
    print(f"\n  Reels violations : {', '.join(violations) if violations else 'none'}")
    print(f"  target fps       : {_target_fps(compat)}")

    try:
        check_reels_hard_limits(compat)
        print("  hard limits      : ok")
    except MetaPublishUserError as exc:
        print(f"  hard limits      : REJECTED -> {exc.message_key} {exc.format_kwargs}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="local path or media URL")
    parser.add_argument("--post-type", default="reel", choices=["reel", "story", "feed"])
    parser.add_argument("--safe-mode", action="store_true", help="use the retry profile")
    parser.add_argument("--out", help="write the prepared video here")
    args = parser.parse_args()

    _section("HOST")
    print(f"  ffmpeg_available  : {ffmpeg_available()}")
    print(f"  ffprobe_available : {ffprobe_available()}")
    if not ffprobe_available():
        print("  WARNING: without ffprobe nothing below can be verified, and the "
              "publisher would upload this file unchecked.")

    video_bytes = _load(args.source)

    _section("SOURCE")
    print(f"  {summarize_report(describe_video_bytes(video_bytes))}")
    _probe_source(video_bytes)

    _section("PREPARATION")
    try:
        result = normalize_instagram_video_bytes(video_bytes, safe_mode=args.safe_mode)
    except MetaPublishUserError as exc:
        print(f"  REJECTED before upload -> {exc.message_key} {exc.format_kwargs}")
        print("\n  This is the reason Meta would have failed the publish.")
        return 1

    print(f"  method     : {result.method}")
    print(f"  changed    : {result.changed}")
    print(f"  violations : {', '.join(result.violations) or 'none'}")
    print(f"  size       : {len(video_bytes)} -> {len(result.video_bytes)} bytes")

    _section("PREPARED PAYLOAD (what Meta would receive)")
    report = describe_video_bytes(result.video_bytes)
    print(f"  {summarize_report(report)}")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))

    _section("PREFLIGHT")
    try:
        instagram_video_binary_preflight(result.video_bytes, args.post_type)
        print("  ok — this payload satisfies every check the publisher performs.")
    except MetaPublishUserError as exc:
        print(f"  REJECTED -> {exc.message_key} {exc.format_kwargs}")
        return 1

    if args.out:
        Path(args.out).write_bytes(result.video_bytes)
        print(f"\nPrepared video written to {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
