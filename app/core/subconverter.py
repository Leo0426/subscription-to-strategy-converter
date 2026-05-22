from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from app.core.fetcher import FetchError, _ensure_resolved_host_is_public, _validate_url
from app.models.subconverter import SubconverterOptions


class SubconverterError(ValueError):
    pass


DEFAULT_SUBCONVERTER_BASE_URL = "http://127.0.0.1:25500"
DEFAULT_SUBCONVERTER_CONFIG_BASE_URL = "http://host.docker.internal:8000"
_PROJECT_DIR = Path(__file__).resolve().parents[2]
_COMMUNITY_DIR = (_PROJECT_DIR / "community_templates").resolve()

SUBCONVERTER_TARGETS = (
    {"id": "clash", "label": "Clash"},
    {"id": "clashr", "label": "ClashR"},
    {"id": "surge", "label": "Surge"},
    {"id": "quan", "label": "Quantumult"},
    {"id": "quanx", "label": "Quantumult X"},
    {"id": "loon", "label": "Loon"},
    {"id": "surfboard", "label": "Surfboard"},
    {"id": "v2ray", "label": "V2Ray"},
    {"id": "ss", "label": "Shadowsocks"},
    {"id": "sssub", "label": "SS Android"},
    {"id": "ssd", "label": "SSD"},
    {"id": "ssr", "label": "SSR"},
    {"id": "trojan", "label": "Trojan"},
    {"id": "mellow", "label": "Mellow"},
    {"id": "mixed", "label": "Mixed"},
)
SUBCONVERTER_TARGET_IDS = frozenset(item["id"] for item in SUBCONVERTER_TARGETS)


def subconverter_base_url() -> str:
    return os.getenv("SUBCONVERTER_BASE_URL", DEFAULT_SUBCONVERTER_BASE_URL).rstrip("/")


def subconverter_config_base_url() -> str:
    return os.getenv("SUBCONVERTER_CONFIG_BASE_URL", DEFAULT_SUBCONVERTER_CONFIG_BASE_URL).rstrip("/")


async def _validate_remote_subscription_url(url: str) -> None:
    try:
        _validate_url(url)
        parsed = urlparse(url)
        await _ensure_resolved_host_is_public(parsed.hostname)  # type: ignore[arg-type]
    except FetchError as exc:
        raise SubconverterError(str(exc)) from exc


def _resolve_config_value(value: str) -> str:
    config = value.strip()
    if not config:
        raise SubconverterError("subconverter config cannot be empty")

    if config.startswith("community:"):
        return f"{subconverter_config_base_url()}/community/templates/raw?id={quote(config, safe='')}"

    parsed = urlparse(config)
    if parsed.scheme in {"http", "https"}:
        return config
    if parsed.scheme:
        raise SubconverterError("subconverter config must be an http(s) URL or a community_templates path")

    candidate = Path(config)
    if not candidate.is_absolute():
        candidate = _PROJECT_DIR / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(_COMMUNITY_DIR):
        raise SubconverterError("local subconverter config must be under community_templates")
    if not candidate.exists():
        raise SubconverterError(f"subconverter config not found: {value}")
    if candidate.suffix.lower() not in {".ini", ".conf", ".yaml", ".yml"}:
        raise SubconverterError("subconverter config must be .ini, .conf, .yaml, or .yml")
    relative = candidate.relative_to(_COMMUNITY_DIR).as_posix()
    template_id = f"community:{relative}"
    return f"{subconverter_config_base_url()}/community/templates/raw?id={quote(template_id, safe='')}"


def _build_subconverter_params(
    url: str,
    options: SubconverterOptions | None,
    target: str = "clash",
) -> dict[str, str]:
    if target not in SUBCONVERTER_TARGET_IDS:
        raise SubconverterError(f"unsupported subconverter target: {target}")
    params = {
        "target": target,
        "url": url,
    }
    if options is None:
        return params

    option_params = options.query_params()
    if "config" in option_params:
        option_params["config"] = _resolve_config_value(option_params["config"])
    params.update(option_params)
    return params


async def convert_subscription(
    url: str,
    target: str,
    options: SubconverterOptions | None = None,
) -> str:
    """Convert any subconverter-supported subscription URL to a target format."""
    await _validate_remote_subscription_url(url)

    endpoint = f"{subconverter_base_url()}/sub"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                endpoint,
                params=_build_subconverter_params(url, options, target),
            )
    except httpx.HTTPError as exc:
        raise SubconverterError(f"subconverter request failed: {exc}") from exc

    if response.status_code < 200 or response.status_code >= 300:
        detail = response.text.strip()
        if detail:
            detail = f": {detail[:300]}"
        raise SubconverterError(f"subconverter returned HTTP {response.status_code}{detail}")

    content = response.text
    if not content.strip():
        raise SubconverterError("subconverter returned empty content")

    return content


async def convert_subscription_to_clash(url: str, options: SubconverterOptions | None = None) -> str:
    """Convert any subconverter-supported subscription URL to Clash YAML."""
    return await convert_subscription(url, "clash", options)
