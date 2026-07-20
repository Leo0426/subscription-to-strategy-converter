from __future__ import annotations

from app.core.fetcher import FetchError, fetch_subscription
from app.core.normalizer import normalize_nodes
from app.core.parsers.clash import clash_to_ir, ir_to_clash_dict
from app.core.parsers.surge import SurgeParseError, looks_like_surge_config, parse_surge_nodes
from app.core.parser import ParseError, parse_clash_yaml_full
from app.ir import ProxyNode


class SubscriptionError(ValueError):
    pass


async def load_subscription(url: str) -> tuple[list[ProxyNode], dict]:
    """Fetch a subscription URL, then parse Clash YAML or Surge config.

    Returns (normalized_nodes, raw_config_dict) where raw_config_dict is the
    full parsed YAML (used by the preview tree).
    """
    try:
        content = await fetch_subscription(url)
    except FetchError as exc:
        raise SubscriptionError(str(exc)) from exc

    if not content.strip():
        raise SubscriptionError("subscription content is empty")

    try:
        raw_proxies, raw_config = parse_clash_yaml_full(content)
    except ParseError as exc:
        if not looks_like_surge_config(content):
            raise SubscriptionError(f"subscription returned unexpected content: {exc}") from exc
        try:
            ir_nodes = parse_surge_nodes(content)
        except SurgeParseError as surge_exc:
            raise SubscriptionError(f"subscription returned invalid Surge config: {surge_exc}") from surge_exc
        if not ir_nodes:
            raise SubscriptionError("Surge subscription contains no supported proxy nodes") from exc
        normalized = normalize_nodes(ir_nodes)
        return normalized, {
            "source-format": "surge",
            "proxies": [ir_to_clash_dict(node) for node in normalized],
        }

    ir_nodes = [clash_to_ir(p) for p in raw_proxies]
    return normalize_nodes(ir_nodes), raw_config
