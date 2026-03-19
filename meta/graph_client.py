from typing import Any
import aiohttp
from Config import Config


async def _graph_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    data: Any = None,
    json: Any = None,
):
    url = f"https://graph.facebook.com/{Config.META_GRAPH_VERSION}{path}"
    if params is None:
        params = {}
    if "access_token" not in params:
        params["access_token"] = Config.META_ACCESS_TOKEN

    async with session.request(
        method, url, params=params, data=data, json=json
    ) as resp:
        body = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(f"Graph API {resp.status} {body}")
        return body


async def list_business_assets() -> list[dict[str, Any]]:
    """
    Returns a list of Pages the token can access and (if available) their connected Instagram Business accounts.

    Expected returned dict keys:
      - page_id, page_name, instagram_user_id (optional), instagram_user_name (optional), label (optional)
    """

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        data = await _graph_request(
            session,
            "GET",
            "/me/accounts",
            params={
                "fields": "id,name,instagram_business_account{id,username}",
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
                "instagram_user_id": ig_user_id,
                "instagram_user_name": ig_username,
                "label": label,
            }
        )
    return assets
