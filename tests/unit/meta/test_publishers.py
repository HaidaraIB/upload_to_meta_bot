"""Unit tests for all Meta publishing paths in meta.publishers."""

from __future__ import annotations

import re
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

import meta.publishers as publishers_module
from meta.errors import MetaPublishUserError
from meta.publishers import publish_to_meta


# --- shared helpers ---


@contextmanager
def mock_ig_rupload_http():
    with aioresponses() as m:
        m.post(
            re.compile(r"https://rupload\.facebook\.com/ig-api-upload/.*"),
            status=200,
            body=b"{}",
        )
        yield m


def _ig_video_payload(**overrides):
    base = {
        "platforms": ["instagram"],
        "page_id": "p1",
        "post_type": "reel",
        "caption": "c",
        "media_type": "video",
        "media_file_id": "tg_vid",
        "instagram_user_id": "17841400",
    }
    base.update(overrides)
    return base


def _fb_only_payload(**overrides):
    base = {
        "platforms": ["facebook"],
        "page_id": "page99",
        "post_type": "feed",
        "caption": "",
        "media_type": None,
    }
    base.update(overrides)
    return base


def make_download(b: bytes):
    async def _fn(_ctx, _fid):
        return b

    return _fn


# --- publish_to_meta entry validation ---


@pytest.mark.parametrize(
    "payload,key",
    [
        (
            {"platforms": [], "page_id": "1", "post_type": "feed"},
            "meta_err_no_platforms",
        ),
        (
            {
                "platforms": ["instagram"],
                "page_id": "1",
                "post_type": "reel",
                "media_type": "photo",
                "instagram_user_id": "ig1",
            },
            "meta_err_reel_requires_video",
        ),
        (
            {
                "platforms": ["facebook"],
                "page_id": "1",
                "post_type": "story",
                "media_type": None,
            },
            "meta_err_story_requires_media",
        ),
        (
            {
                "platforms": ["instagram"],
                "page_id": "1",
                "post_type": "feed",
                "media_type": None,
            },
            "meta_err_instagram_no_text_only",
        ),
        (
            {
                "platforms": ["instagram"],
                "page_id": "1",
                "post_type": "feed",
                "media_type": "video",
                "instagram_user_id": "",
            },
            "meta_err_missing_ig_user_id",
        ),
        (
            {
                "platforms": ["facebook"],
                "page_id": "1",
                "post_type": "feed",
                "media_type": None,
                "caption": "",
            },
            "meta_err_fb_text_requires_caption",
        ),
        (
            {
                "platforms": ["instagram"],
                "page_id": "1",
                "post_type": "feed",
                "media_type": "photo",
                "instagram_user_id": "ig1",
            },
            "meta_err_ig_missing_image_url",
        ),
        (
            _fb_only_payload(post_type="feed", media_type="audio"),
            "meta_err_fb_unsupported_feed_media",
        ),
        (
            _fb_only_payload(post_type="story", media_type="audio", caption="x"),
            "meta_err_fb_unsupported_story_media",
        ),
        (
            _fb_only_payload(post_type="unknown_type", media_type="video"),
            "meta_err_unsupported_post_type",
        ),
    ],
)
@pytest.mark.asyncio
async def test_publish_to_meta_validation_errors(mock_context, payload, key: str):
    with pytest.raises(MetaPublishUserError) as cm:
        await publish_to_meta(payload, mock_context)
    assert cm.value.message_key == key


# --- Instagram video (reel / story / feed video) ---


@pytest.mark.parametrize(
    "post_type,expected_ig_media_type",
    [
        ("reel", "REELS"),
        ("story", "STORIES"),
        ("feed", "REELS"),
    ],
)
@pytest.mark.asyncio
async def test_publish_instagram_video_resumable_paths(
    mock_context,
    publishers_texts,
    patch_meta_config,
    post_type,
    expected_ig_media_type,
):
    video = b"v"
    with mock_ig_rupload_http():
        with (
            patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
            patch.object(
                publishers_module,
                "_download_telegram_file",
                side_effect=make_download(video),
            ),
        ):
            graph.side_effect = [{"id": "cre1"}, {"id": "pub1"}]
            await publish_to_meta(
                _ig_video_payload(post_type=post_type),
                mock_context,
            )
    assert graph.await_count == 2
    assert graph.await_args_list[0].kwargs["params"]["media_type"] == expected_ig_media_type
    assert graph.await_args_list[0].kwargs["params"]["upload_type"] == "resumable"


@pytest.mark.asyncio
async def test_publish_instagram_resumable_rupload_http_error(
    mock_context, publishers_texts, patch_meta_config
):
    video = b"v"
    with aioresponses() as m:
        m.post(
            re.compile(r"https://rupload\.facebook\.com/ig-api-upload/.*"),
            status=400,
            body=b'{"debug_info":{"message":"bad"}}',
        )
        with (
            patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
            patch.object(
                publishers_module,
                "_download_telegram_file",
                side_effect=make_download(video),
            ),
        ):
            graph.side_effect = [{"id": "cre1"}]
            with pytest.raises(MetaPublishUserError) as cm:
                await publish_to_meta(_ig_video_payload(), mock_context)
            assert cm.value.message_key == "meta_err_ig_resumable_upload"
            assert cm.value.format_kwargs["status"] == 400


@pytest.mark.asyncio
async def test_publish_instagram_video_missing_downloaded_bytes_raises(
    mock_context, publishers_texts, patch_meta_config
):
    async def none_bytes(_c, _f):
        return None

    with mock_ig_rupload_http():
        with (
            patch.object(publishers_module, "_graph_request", new_callable=AsyncMock),
            patch.object(publishers_module, "_download_telegram_file", side_effect=none_bytes),
        ):
            with pytest.raises(MetaPublishUserError) as cm:
                await publish_to_meta(_ig_video_payload(), mock_context)
            assert cm.value.message_key == "meta_err_ig_missing_video_bytes"


@pytest.mark.asyncio
async def test_publish_instagram_requires_video_for_video_branch(mock_context):
    """Story + instagram with photo must go through photo branch."""
    with pytest.raises(MetaPublishUserError) as cm:
        await publish_to_meta(
            {
                "platforms": ["instagram"],
                "page_id": "1",
                "post_type": "story",
                "media_type": "photo",
                "instagram_user_id": "ig1",
            },
            mock_context,
        )
    assert cm.value.message_key == "meta_err_ig_missing_image_url"


@pytest.mark.asyncio
async def test_publish_instagram_photo_requires_photo_media_type(mock_context):
    with pytest.raises(MetaPublishUserError) as cm:
        await publish_to_meta(
            {
                "platforms": ["instagram"],
                "page_id": "1",
                "post_type": "feed",
                "media_type": "document",
                "instagram_user_id": "ig1",
            },
            mock_context,
        )
    assert cm.value.message_key == "meta_err_ig_requires_photo"


# --- Instagram photo (image_url + media_publish) ---


@pytest.mark.asyncio
async def test_publish_instagram_feed_photo_uses_image_url(
    mock_context, publishers_texts, patch_meta_config
):
    """post_type feed + photo uses IMAGE + image_url (not resumable video)."""
    url = "https://cdn.example.com/p.jpg"
    with patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph:
        graph.side_effect = [{"id": "ic1"}, {"id": "ip1"}]
        await publish_to_meta(
            {
                "platforms": ["instagram"],
                "page_id": "p1",
                "post_type": "feed",
                "caption": "cap",
                "media_type": "photo",
                "instagram_user_id": "17841400",
                "instagram_image_url": url,
            },
            mock_context,
        )
    assert graph.await_count == 2
    c0 = graph.await_args_list[0]
    assert c0.args[2] == "/17841400/media"
    assert c0.kwargs["params"]["media_type"] == "IMAGE"
    assert c0.kwargs["params"]["image_url"] == url
    c1 = graph.await_args_list[1]
    assert c1.args[2] == "/17841400/media_publish"
    assert c1.kwargs["params"]["creation_id"] == "ic1"


@pytest.mark.asyncio
async def test_publish_instagram_story_photo_hits_video_branch_first(
    mock_context, publishers_texts, patch_meta_config
):
    """Story with photo goes through Instagram photo branch (STORIES + image_url)."""
    url = "https://x/y.jpg"
    with patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph:
        graph.side_effect = [{"id": "cre1"}, {"success": True}]
        msg = await publish_to_meta(
            {
                "platforms": ["instagram"],
                "page_id": "p1",
                "post_type": "story",
                "caption": "",
                "media_type": "photo",
                "instagram_user_id": "17841400",
                "instagram_image_url": url,
            },
            mock_context,
        )

    assert msg == "IG OK"
    assert graph.await_count == 2
    c0 = graph.await_args_list[0]
    assert c0.args[2] == "/17841400/media"
    assert c0.kwargs["params"]["media_type"] == "STORIES"
    assert c0.kwargs["params"]["image_url"] == url
    c1 = graph.await_args_list[1]
    assert c1.args[2] == "/17841400/media_publish"
    assert c1.kwargs["params"]["creation_id"] == "cre1"


@pytest.mark.asyncio
async def test_publish_instagram_container_creation_fails_runtime(
    mock_context, publishers_texts, patch_meta_config
):
    with patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph:
        graph.return_value = {"no": "id"}
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                {
                    "platforms": ["instagram"],
                    "page_id": "p1",
                    "post_type": "feed",
                    "media_type": "photo",
                    "instagram_user_id": "17841400",
                    "instagram_image_url": "https://x/y.jpg",
                },
                mock_context,
            )
        assert cm.value.message_key == "meta_err_ig_container"


# --- Facebook feed ---


@pytest.mark.asyncio
async def test_publish_facebook_text_post_calls_feed(
    mock_context, publishers_texts, patch_meta_config
):
    with patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph:
        graph.return_value = {"id": "post_1"}
        msg = await publish_to_meta(
            {
                "platforms": ["facebook"],
                "page_id": "123",
                "post_type": "feed",
                "caption": "Hello world",
                "media_type": None,
            },
            mock_context,
        )
    graph.assert_awaited_once()
    ca = graph.await_args
    assert ca.args[1] == "POST"
    assert ca.args[2] == "/123/feed"
    assert ca.kwargs["params"]["message"] == "Hello world"
    assert ca.kwargs["params"]["published"] == "true"
    assert msg == "FB OK"


@pytest.mark.asyncio
async def test_publish_facebook_feed_photo_formdata(
    mock_context, publishers_texts, patch_meta_config
):
    photo = b"\xff\xd8\xff"
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(photo),
        ),
    ):
        graph.return_value = {"id": "ph1"}
        await publish_to_meta(
            _fb_only_payload(
                post_type="feed",
                media_type="photo",
                caption="pic cap",
                media_file_id="tg_p",
            ),
            mock_context,
        )
    graph.assert_awaited_once()
    ca = graph.await_args
    assert ca.args[2] == "/page99/photos"
    assert isinstance(ca.kwargs["data"], aiohttp.FormData)


@pytest.mark.asyncio
async def test_publish_facebook_feed_video_formdata(
    mock_context, publishers_texts, patch_meta_config
):
    vid = b"mp4data"
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(vid),
        ),
    ):
        graph.return_value = {"id": "v1"}
        await publish_to_meta(
            _fb_only_payload(
                post_type="feed",
                media_type="video",
                caption="vid cap",
                media_file_id="tg_v",
            ),
            mock_context,
        )
    ca = graph.await_args
    assert ca.args[2] == "/page99/videos"
    assert isinstance(ca.kwargs["data"], aiohttp.FormData)


@pytest.mark.asyncio
async def test_publish_facebook_feed_missing_photo_bytes(
    mock_context, publishers_texts, patch_meta_config
):
    async def none_photo(_c, _f):
        return None

    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock),
        patch.object(publishers_module, "_download_telegram_file", side_effect=none_photo),
    ):
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                _fb_only_payload(
                    post_type="feed",
                    media_type="photo",
                    media_file_id="x",
                ),
                mock_context,
            )
        assert cm.value.message_key == "meta_err_fb_missing_photo_bytes"


@pytest.mark.asyncio
async def test_publish_facebook_feed_missing_video_bytes(
    mock_context, publishers_texts, patch_meta_config
):
    async def none_vid(_c, _f):
        return None

    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock),
        patch.object(publishers_module, "_download_telegram_file", side_effect=none_vid),
    ):
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                _fb_only_payload(
                    post_type="feed",
                    media_type="video",
                    media_file_id="x",
                ),
                mock_context,
            )
        assert cm.value.message_key == "meta_err_fb_missing_video_bytes"


# --- Facebook reel ---


@pytest.mark.asyncio
async def test_publish_facebook_reel_start_upload_finish(
    mock_context, publishers_texts, patch_meta_config
):
    video = b"reel-bytes"
    start = {
        "video_id": "vid_fb",
        "upload_url": "https://rupload.facebook.com/video-upload/xyz",
    }
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(video),
        ),
        patch.object(
            publishers_module, "_upload_to_rupload", new_callable=AsyncMock
        ) as up,
    ):
        graph.side_effect = [start, {"success": True}]
        msg = await publish_to_meta(
            _fb_only_payload(
                post_type="reel",
                media_type="video",
                media_file_id="tg_r",
            ),
            mock_context,
        )
    assert graph.await_count == 2
    assert graph.await_args_list[0].args[2] == "/page99/video_reels"
    assert graph.await_args_list[0].kwargs["params"]["upload_phase"] == "START"
    assert graph.await_args_list[1].kwargs["params"]["upload_phase"] == "FINISH"
    up.assert_awaited_once()
    assert msg == "FB OK"


@pytest.mark.asyncio
async def test_publish_facebook_reel_start_missing_ids_runtime(
    mock_context, publishers_texts, patch_meta_config
):
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(b"v"),
        ),
    ):
        graph.return_value = {}
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                _fb_only_payload(
                    post_type="reel",
                    media_type="video",
                    media_file_id="tg",
                ),
                mock_context,
            )
        assert cm.value.message_key == "meta_err_fb_reel_init"


@pytest.mark.asyncio
async def test_publish_facebook_reel_requires_video_media(mock_context):
    with pytest.raises(MetaPublishUserError) as cm:
        await publish_to_meta(
            _fb_only_payload(post_type="reel", media_type="photo"),
            mock_context,
        )
    assert cm.value.message_key == "meta_err_reel_requires_video"


@pytest.mark.asyncio
async def test_publish_facebook_reel_missing_video_bytes_after_download(
    mock_context, publishers_texts, patch_meta_config
):
    async def none_vid(_c, _f):
        return None

    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(publishers_module, "_download_telegram_file", side_effect=none_vid),
    ):
        graph.return_value = {
            "video_id": "v",
            "upload_url": "https://rupload.facebook.com/x",
        }
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                _fb_only_payload(
                    post_type="reel",
                    media_type="video",
                    media_file_id="tg",
                ),
                mock_context,
            )
        assert cm.value.message_key == "meta_err_fb_reel_requires_video"


@pytest.mark.asyncio
async def test_publish_facebook_reel_upload_to_rupload_fails(
    mock_context, publishers_texts, patch_meta_config
):
    async def boom(*_a, **_k):
        raise RuntimeError("Upload failed 500: oops")

    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(b"v"),
        ),
        patch.object(publishers_module, "_upload_to_rupload", side_effect=boom),
    ):
        graph.return_value = {
            "video_id": "vid_fb",
            "upload_url": "https://rupload.facebook.com/u",
        }
        with pytest.raises(RuntimeError, match="Upload failed 500"):
            await publish_to_meta(
                _fb_only_payload(
                    post_type="reel",
                    media_type="video",
                    media_file_id="tg",
                ),
                mock_context,
            )


# --- Facebook story (video / photo) ---


@pytest.mark.asyncio
async def test_publish_facebook_story_video_flow(
    mock_context, publishers_texts, patch_meta_config
):
    video = b"story-vid"
    init = {"video_id": "sv1", "upload_url": "https://rupload.facebook.com/story/1"}
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(video),
        ),
        patch.object(publishers_module, "_upload_to_rupload", new_callable=AsyncMock),
    ):
        graph.side_effect = [init, {"ok": True}]
        await publish_to_meta(
            _fb_only_payload(
                post_type="story",
                media_type="video",
                media_file_id="tg_sv",
            ),
            mock_context,
        )
    assert graph.await_count == 2
    assert graph.await_args_list[0].args[2] == "/page99/video_stories"
    assert graph.await_args_list[0].kwargs["params"]["upload_phase"] == "start"
    assert graph.await_args_list[1].kwargs["params"]["upload_phase"] == "finish"


@pytest.mark.asyncio
async def test_publish_facebook_story_video_missing_bytes(
    mock_context, publishers_texts, patch_meta_config
):
    async def none_v(_c, _f):
        return None

    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock),
        patch.object(publishers_module, "_download_telegram_file", side_effect=none_v),
    ):
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                _fb_only_payload(
                    post_type="story",
                    media_type="video",
                    media_file_id="tg",
                ),
                mock_context,
            )
        assert cm.value.message_key == "meta_err_fb_story_missing_video_bytes"


@pytest.mark.asyncio
async def test_publish_facebook_story_video_init_fails(
    mock_context, publishers_texts, patch_meta_config
):
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(b"v"),
        ),
    ):
        graph.return_value = {}
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                _fb_only_payload(
                    post_type="story",
                    media_type="video",
                    media_file_id="tg",
                ),
                mock_context,
            )
        assert cm.value.message_key == "meta_err_fb_story_init"


@pytest.mark.asyncio
async def test_publish_facebook_story_video_rupload_fails(
    mock_context, publishers_texts, patch_meta_config
):
    async def boom(*_a, **_k):
        raise RuntimeError("Upload failed 502: x")

    init = {"video_id": "s1", "upload_url": "https://rupload.facebook.com/s/1"}
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(b"v"),
        ),
        patch.object(publishers_module, "_upload_to_rupload", side_effect=boom),
    ):
        graph.return_value = init
        with pytest.raises(RuntimeError, match="Upload failed 502"):
            await publish_to_meta(
                _fb_only_payload(
                    post_type="story",
                    media_type="video",
                    media_file_id="tg",
                ),
                mock_context,
            )


@pytest.mark.asyncio
async def test_publish_facebook_story_photo_flow(
    mock_context, publishers_texts, patch_meta_config
):
    photo = b"jpg-bytes"
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(photo),
        ),
    ):
        graph.side_effect = [{"id": "photo_st1"}, {"id": "story1"}]
        await publish_to_meta(
            _fb_only_payload(
                post_type="story",
                media_type="photo",
                caption="s cap",
                media_file_id="tg_sp",
            ),
            mock_context,
        )
    assert graph.await_count == 2
    assert graph.await_args_list[0].args[2] == "/page99/photos"
    assert graph.await_args_list[1].args[2] == "/page99/photo_stories"
    assert graph.await_args_list[1].kwargs["params"]["photo_id"] == "photo_st1"


@pytest.mark.asyncio
async def test_publish_facebook_story_photo_missing_bytes(
    mock_context, publishers_texts, patch_meta_config
):
    async def none_p(_c, _f):
        return None

    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock),
        patch.object(publishers_module, "_download_telegram_file", side_effect=none_p),
    ):
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                _fb_only_payload(
                    post_type="story",
                    media_type="photo",
                    media_file_id="tg",
                ),
                mock_context,
            )
        assert cm.value.message_key == "meta_err_fb_story_missing_photo_bytes"


@pytest.mark.asyncio
async def test_publish_facebook_story_photo_upload_no_id_runtime(
    mock_context, publishers_texts, patch_meta_config
):
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(b"p"),
        ),
    ):
        graph.side_effect = [{"oops": True}]
        with pytest.raises(MetaPublishUserError) as cm:
            await publish_to_meta(
                _fb_only_payload(
                    post_type="story",
                    media_type="photo",
                    media_file_id="tg",
                ),
                mock_context,
            )
        assert cm.value.message_key == "meta_err_fb_story_photo"


# --- Dual platform ---


@pytest.mark.asyncio
async def test_publish_dual_feed_video_both_platforms(
    mock_context, publishers_texts, patch_meta_config
):
    """Same post_type/media_type: IG feed video (resumable) + FB /videos."""
    video = b"v"
    with mock_ig_rupload_http():
        with patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph:
            graph.side_effect = [
                {"id": "ig_c"},
                {"id": "ig_p"},
                {"id": "fb_v"},
            ]
            with patch.object(
                publishers_module,
                "_download_telegram_file",
                side_effect=make_download(video),
            ):
                msg = await publish_to_meta(
                    {
                        "platforms": ["instagram", "facebook"],
                        "page_id": "page99",
                        "post_type": "feed",
                        "caption": "both",
                        "media_type": "video",
                        "media_file_id": "tg",
                        "instagram_user_id": "17841400",
                    },
                    mock_context,
                )
    assert graph.await_count == 3
    assert msg == "IG OK\n\nFB OK"
    assert graph.await_args_list[2].args[2] == "/page99/videos"


@pytest.mark.asyncio
async def test_publish_dual_instagram_photo_and_facebook_photo(
    mock_context, publishers_texts, patch_meta_config
):
    photo = b"ph"
    with (
        patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
        patch.object(
            publishers_module,
            "_download_telegram_file",
            side_effect=make_download(photo),
        ),
    ):
        graph.side_effect = [
            {"id": "ig_m"},
            {"id": "ig_pub"},
            {"id": "fb_ph"},
        ]
        msg = await publish_to_meta(
            {
                "platforms": ["instagram", "facebook"],
                "page_id": "page99",
                "post_type": "feed",
                "caption": "dual",
                "media_type": "photo",
                "media_file_id": "tg",
                "instagram_user_id": "17841400",
                "instagram_image_url": "https://x/i.jpg",
            },
            mock_context,
        )
    assert graph.await_count == 3
    assert "IG OK" in msg and "FB OK" in msg


@pytest.mark.asyncio
async def test_publish_dual_both_reels_ig_rupload_and_fb_upload(
    mock_context, publishers_texts, patch_meta_config
):
    video = b"vv"
    fb_start = {"video_id": "fv", "upload_url": "https://rupload.facebook.com/vr/1"}
    with mock_ig_rupload_http():
        with (
            patch.object(publishers_module, "_graph_request", new_callable=AsyncMock) as graph,
            patch.object(
                publishers_module,
                "_download_telegram_file",
                side_effect=make_download(video),
            ),
            patch.object(
                publishers_module, "_upload_to_rupload", new_callable=AsyncMock
            ) as fb_up,
        ):
            graph.side_effect = [
                {"id": "igc"},
                {"id": "igp"},
                fb_start,
                {"done": True},
            ]
            await publish_to_meta(
                {
                    "platforms": ["instagram", "facebook"],
                    "page_id": "page99",
                    "post_type": "reel",
                    "caption": "rr",
                    "media_type": "video",
                    "media_file_id": "tg",
                    "instagram_user_id": "17841400",
                },
                mock_context,
            )
    assert graph.await_count == 4
    fb_up.assert_awaited_once()
