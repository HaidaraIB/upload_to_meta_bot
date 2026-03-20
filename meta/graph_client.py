from typing import Any
import logging
import aiohttp
from Config import Config
from meta.errors import (
    MetaPublishUserError,
    graph_error_detail,
    graph_error_message_key,
)

logger = logging.getLogger(__name__)


async def _graph_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    data: Any = None,
    json: Any = None,
    *,
    access_token: str | None = None,
):
    url = f"https://graph.facebook.com/{Config.META_GRAPH_VERSION}{path}"
    if params is None:
        params = {}
    if "access_token" not in params:
        params["access_token"] = access_token or Config.META_ACCESS_TOKEN

    # Never log access tokens; keep logs useful by logging keys only.
    log_params = {k: ("<redacted>" if k == "access_token" else v) for k, v in params.items()}
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "Graph request start: method=%s path=%s params_keys=%s",
            method,
            path,
            list(log_params.keys()),
        )

    async with session.request(
        method, url, params=params, data=data, json=json
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status >= 400:
            detail = graph_error_detail(body)
            logger.warning(
                "Graph request failed: method=%s path=%s status=%s detail=%s",
                method,
                path,
                resp.status,
                detail,
            )
            raise MetaPublishUserError(
                graph_error_message_key(detail),
                status=resp.status,
                detail=detail,
            )
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Graph request ok: method=%s path=%s status=%s",
                method,
                path,
                resp.status,
            )
        return body


async def list_business_assets() -> list[dict[str, Any]]:
    """
    logger.info("Listing business assets (/me/accounts).")
    Returns a list of Pages the token can access and (if available) their connected Instagram Business accounts.

    Expected returned dict keys:
      - page_id, page_name, page_access_token, instagram_user_id (optional),
        instagram_user_name (optional), label (optional)
    """

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _graph_request(
            session,
            "GET",
            "/me/accounts",
            params={
                "fields": "id,name,access_token,instagram_business_account{id,username}",
            },
        )

    pages = data.get("data", []) if isinstance(data, dict) else []
    assets: list[dict[str, Any]] = []
    for p in pages:
        ig = p.get("instagram_business_account") or {}
        ig_user_id = ig.get("id")
        ig_username = ig.get("username")
        label = p["name"]
        if ig_username:
            label = f"{p['name']} / IG: {ig_username}"
        assets.append(
            {
                "page_id": p["id"],
                "page_name": p["name"],
                "page_access_token": p.get("access_token"),
                "instagram_user_id": ig_user_id,
                "instagram_user_name": ig_username,
                "label": label,
            }
        )
    logger.info("Business assets found: %s", len(assets))
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("Business assets labels: %s", [a.get("label") for a in assets])
    return assets
