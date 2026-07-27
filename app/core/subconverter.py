"""Optional protocol-compatibility adapter backed by subconverter.

Subflow owns policy semantics.  This module only asks a separately operated
subconverter instance to normalize an otherwise unsupported subscription into
a node-only Clash document.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from app.core.fetcher import FetchError, _validate_url


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
        _validate_url(url)
    except FetchError as exc:
        raise SubconverterError(str(exc)) from exc

    endpoint = f"{_validated_base_url()}/sub"
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.get(
                endpoint,
                params={"target": "clash", "url": url, "list": "true"},
            )
    except httpx.HTTPError as exc:
        raise SubconverterError(f"subconverter request failed: {exc}") from exc

    if not 200 <= response.status_code < 300:
        detail = response.text.strip()[:240]
        suffix = f": {detail}" if detail else ""
        raise SubconverterError(
            f"subconverter returned HTTP {response.status_code}{suffix}"
        )

    if not response.text.strip():
        raise SubconverterError("subconverter returned empty content")
    return response.text
