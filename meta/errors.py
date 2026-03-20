from __future__ import annotations

import logging
from typing import Any


class MetaPublishUserError(Exception):
    """Localized user-facing publish/Graph error; use message_key with TEXTS[lang]."""

    def __init__(self, message_key: str, **format_kwargs: Any):
        self.message_key = message_key
        self.format_kwargs = format_kwargs
        super().__init__(message_key)

    def __str__(self) -> str:
        return f"{self.message_key}|{self.format_kwargs}"


def graph_error_detail(body: Any, max_len: int = 400) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if msg is not None:
                return str(msg)[:max_len]
        return str(body)[:max_len]
    if body is None:
        return ""
    return str(body)[:max_len]


def graph_error_message_key(detail: str) -> str:
    """Map known Graph error text to a localized message key for admins."""
    if not detail:
        return "meta_err_graph"
    dl = detail.lower()
    if "pages_manage_posts" in dl and (
        "not available" in dl or "app review" in dl or "deprecated" in dl
    ):
        logging.getLogger(__name__).info(
            "Graph error classified as pages_manage_posts missing/invalid."
        )
        return "meta_err_pages_manage_posts"
    return "meta_err_graph"


def format_meta_publish_failure(exc: BaseException, lang) -> str:
    from common.lang_dicts import TEXTS

    if isinstance(exc, MetaPublishUserError):
        return TEXTS[lang][exc.message_key].format(**exc.format_kwargs)
    return TEXTS[lang]["meta_upload_publish_failed_unexpected"].format(err=str(exc))
