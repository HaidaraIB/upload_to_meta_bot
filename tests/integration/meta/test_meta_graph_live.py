"""
Optional live checks against Meta (no publishing).

Requires:
  META_RUN_LIVE_TESTS=1
  META_ACCESS_TOKEN in environment (.env or shell)
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def load_dotenv_once():
    load_dotenv()


@pytest.mark.asyncio
async def test_list_business_assets_live():
    if not os.getenv("META_ACCESS_TOKEN"):
        pytest.skip("META_ACCESS_TOKEN not set")

    from meta.graph_client import list_business_assets

    assets = await list_business_assets()
    assert isinstance(assets, list)
    for a in assets:
        assert "page_id" in a and "page_name" in a
