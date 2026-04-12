"""Eligibility for delegating schedule time to Meta (scheduled_publish_time)."""

import pytest

from meta.scheduling_backends import (
    _is_meta_native_supported,
    meta_native_scheduling_supported,
)


@pytest.mark.parametrize(
    "post_type,platforms,expected",
    [
        ("feed", ["instagram"], True),
        ("feed", ["facebook"], True),
        ("feed", ["instagram", "facebook"], True),
        ("feed", "both", True),
        ("reel", ["instagram"], False),
        ("reel", ["facebook"], False),
        ("reel", ["instagram", "facebook"], False),
        ("reel", "both", False),
        ("story", ["instagram"], False),
        ("story", ["facebook"], False),
        ("story", ["instagram", "facebook"], False),
        ("story", "both", False),
        ("carousel", ["instagram"], False),
    ],
)
def test_is_meta_native_supported(post_type, platforms, expected):
    payload = {"post_type": post_type, "platforms": platforms}
    assert _is_meta_native_supported(payload) is expected


def test_meta_native_scheduling_supported_is_alias():
    assert meta_native_scheduling_supported is _is_meta_native_supported
