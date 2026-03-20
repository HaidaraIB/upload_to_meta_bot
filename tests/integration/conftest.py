"""
Live Meta tests are opt-in so `pytest` never calls the network by default.

Run:
  set META_RUN_LIVE_TESTS=1
  pytest tests/integration -m integration
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items) -> None:
    enabled = os.getenv("META_RUN_LIVE_TESTS", "").lower() in ("1", "true", "yes")
    if enabled:
        return
    skip = pytest.mark.skip(
        reason="Live Meta tests disabled; set META_RUN_LIVE_TESTS=1 and META_ACCESS_TOKEN"
    )
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip)
