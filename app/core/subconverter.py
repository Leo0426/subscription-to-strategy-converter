"""Optional protocol-compatibility adapter backed by subconverter.

Subflow owns policy semantics.  This module only asks a separately operated
subconverter instance to normalize an otherwise unsupported subscription into
a node-only Clash document.
"""
from __future__ import annotations

import os
import asyncio
from urllib.parse import urlparse

import httpx

from app.core.fetcher import FetchError, _ensure_resolved_host_is_public, _validate_url, request_text
from app.core.network import fetch_timeout


class SubconverterError(ValueError):
    pass


def subconverter_base_url() -> str:
    return os.environ.get("SUBFLOW_SUBCONVERTER_URL", "").strip().rstrip("/")


def is_subconverter_configured() -> bool:
    return bool(subconverter_base_url())


def _validated_base_url() -> str:
    base_url = subconverter_base_url()
    if not base_url:
        raise SubconverterError("subconverter compatibility adapter is not configured")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SubconverterError("SUBFLOW_SUBCONVERTER_URL must be an http(s) URL")
    return base_url


async def convert_subscription_to_clash(url: str) -> str:
    """Normalize one public subscription URL to a node-only Clash document."""
    try:
        async with asyncio.timeout(fetch_timeout()):
            _validate_url(url)
            await _ensure_resolved_host_is_public(urlparse(url).hostname)
            content = await request_text(
                f"{_validated_base_url()}/sub", params={"target": "clash", "url": url, "list": "true"},
                public=False, redirects=False,
            )
    except TimeoutError as exc:
        raise SubconverterError("subconverter refresh deadline exceeded") from exc
    except FetchError as exc:
        raise SubconverterError(str(exc)) from exc
    if not content.strip():
        raise SubconverterError("subconverter returned empty content")
    return content
