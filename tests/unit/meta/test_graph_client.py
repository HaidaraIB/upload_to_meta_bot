"""Unit tests for Graph API client (mocked HTTP)."""

from __future__ import annotations

import re

from aioresponses import aioresponses
import pytest

from meta.errors import MetaPublishUserError, graph_error_message_key
from meta.graph_client import list_business_assets


@pytest.mark.asyncio
async def test_list_business_assets_maps_pages_and_instagram(
    patch_meta_config, meta_graph_version
):
    with aioresponses() as m:
        m.get(
            re.compile(
                rf"https://graph\.facebook\.com/{meta_graph_version}/me/accounts\?.*"
            ),
            payload={
                "data": [
                    {
                        "id": "page_1",
                        "name": "My Page",
                        "access_token": "page_token_1",
                        "instagram_business_account": {
                            "id": "17841400",
                            "username": "mybrand",
                        },
                    },
                    {
                        "id": "page_2",
                        "name": "No IG",
                        "access_token": "page_token_2",
                    },
                ]
            },
        )
        assets = await list_business_assets()

    assert len(assets) == 2
    assert assets[0]["page_id"] == "page_1"
    assert assets[0]["page_access_token"] == "page_token_1"
    assert assets[0]["instagram_user_id"] == "17841400"
    assert assets[0]["instagram_user_name"] == "mybrand"
    assert "IG: mybrand" in assets[0]["label"]
    assert assets[1]["instagram_user_id"] is None
    assert assets[1]["page_access_token"] == "page_token_2"
    assert assets[1]["label"] == "No IG"


@pytest.mark.asyncio
async def test_list_business_assets_empty_data(patch_meta_config, meta_graph_version):
    with aioresponses() as m:
        m.get(
            re.compile(
                rf"https://graph\.facebook\.com/{meta_graph_version}/me/accounts\?.*"
            ),
            payload={"data": []},
        )
        assets = await list_business_assets()
    assert assets == []


def test_graph_error_message_key_pages_manage_posts():
    msg = (
        "(#200) The permission(s) pages_manage_posts are not available. "
        "It could because either they are deprecated or need to be approved by App Review."
    )
    assert graph_error_message_key(msg) == "meta_err_pages_manage_posts"


def test_graph_error_message_key_default_for_other_errors():
    assert graph_error_message_key("Invalid OAuth access token.") == "meta_err_graph"


@pytest.mark.asyncio
async def test_list_business_assets_graph_error_raises(
    patch_meta_config, meta_graph_version
):
    with aioresponses() as m:
        m.get(
            re.compile(
                rf"https://graph\.facebook\.com/{meta_graph_version}/me/accounts\?.*"
            ),
            status=400,
            payload={"error": {"message": "Invalid OAuth access token."}},
        )
        with pytest.raises(MetaPublishUserError) as cm:
            await list_business_assets()
        assert cm.value.message_key == "meta_err_graph"
        assert cm.value.format_kwargs["status"] == 400


@pytest.mark.asyncio
async def test_list_business_assets_graph_403_pages_manage_posts_uses_dedicated_key(
    patch_meta_config, meta_graph_version
):
    body = {
        "error": {
            "message": (
                "(#200) The permission(s) pages_manage_posts are not available. "
                "need to be approved by App Review."
            ),
            "type": "OAuthException",
            "code": 200,
        }
    }
    with aioresponses() as m:
        m.get(
            re.compile(
                rf"https://graph\.facebook\.com/{meta_graph_version}/me/accounts\?.*"
            ),
            status=403,
            payload=body,
        )
        with pytest.raises(MetaPublishUserError) as cm:
            await list_business_assets()
    assert cm.value.message_key == "meta_err_pages_manage_posts"
