from __future__ import annotations

from app.core.normalizer import normalize_nodes
from app.core.parsers.clash import clash_to_ir
from app.core.parser import ParseError, parse_clash_yaml_full
from app.core.subconverter import SubconverterError, convert_subscription_to_clash
from app.ir import ProxyNode
from app.models.subconverter import SubconverterOptions


class SubscriptionError(ValueError):
    pass


async def load_subscription(url: str, subconverter: SubconverterOptions | None = None) -> tuple[list[ProxyNode], dict]:
    """Convert a subscription URL with subconverter, then parse Clash YAML.

    Returns (normalized_nodes, raw_config_dict) where raw_config_dict is the
    full parsed YAML from subconverter (used by the preview tree).
    """
    try:
        content = await convert_subscription_to_clash(url, subconverter)
    except SubconverterError as exc:
        raise SubscriptionError(str(exc)) from exc

    if not content.strip():
        raise SubscriptionError("subscription content is empty")

    try:
        raw_proxies, raw_config = parse_clash_yaml_full(content)
    except ParseError as exc:
        raise SubscriptionError(f"subconverter returned unexpected content: {exc}") from exc

    ir_nodes = [clash_to_ir(p) for p in raw_proxies]
    return normalize_nodes(ir_nodes), raw_config
