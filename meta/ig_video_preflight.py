"""
Instagram Graph API video constraints (see ig-user /media specs).

Reels / feed video containers use REELS specs (300MB max, moov before mdat).
Story video has a 100MB limit.
"""

from __future__ import annotations

from typing import Iterator

from meta.errors import MetaPublishUserError

# From Meta "Reel Specifications" / "Story Video Specifications" (ig-user media).
_IG_REEL_VIDEO_MAX_BYTES = 300 * 1024 * 1024
_IG_STORY_VIDEO_MAX_BYTES = 100 * 1024 * 1024


def _iter_mp4_top_level_boxes(data: bytes) -> Iterator[tuple[bytes, int, int]]:
    """Yield (fourcc, start_offset, total_box_size) for each top-level ISO BMFF box."""
    pos = 0
    n = len(data)
    while pos + 8 <= n:
        size_hi = int.from_bytes(data[pos : pos + 4], "big")
        typ = data[pos + 4 : pos + 8]
        header = 8
        if size_hi == 0:
            box_size = n - pos
        elif size_hi == 1:
            if pos + 16 > n:
                return
            box_size = int.from_bytes(data[pos + 8 : pos + 16], "big")
            header = 16
        else:
            box_size = size_hi
        if box_size < header or pos + box_size > n:
            return
        yield typ, pos, box_size
        pos += box_size


def _mp4_moov_before_mdat(video_bytes: bytes) -> bool | None:
    """
    Meta requires MP4 with moov at the front (no huge mdat before moov / "fast start").
    Returns True if moov appears before first top-level mdat, False if mdat is first, None if unknown.
    """
    if len(video_bytes) < 12 or video_bytes[4:8] != b"ftyp":
        return None
    saw_moov = False
    for typ, _start, _size in _iter_mp4_top_level_boxes(video_bytes):
        if typ == b"mdat":
            return saw_moov
        if typ == b"moov":
            saw_moov = True
    return None


def instagram_video_binary_preflight(video_bytes: bytes, post_type: str) -> None:
    """Raise MetaPublishUserError if bytes clearly violate Instagram video rules."""
    if post_type == "story":
        max_b = _IG_STORY_VIDEO_MAX_BYTES
    elif post_type in ("reel", "feed"):
        max_b = _IG_REEL_VIDEO_MAX_BYTES
    else:
        return

    if len(video_bytes) > max_b:
        raise MetaPublishUserError(
            "meta_err_ig_video_file_too_large",
            max_mb=max_b // (1024 * 1024),
        )

    order = _mp4_moov_before_mdat(video_bytes)
    if order is False:
        raise MetaPublishUserError("meta_err_ig_mp4_requires_faststart")
