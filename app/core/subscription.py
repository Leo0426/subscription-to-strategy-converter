from __future__ import annotations

from copy import deepcopy
import asyncio
import hashlib
import os

from app.core.inflight import SingleFlight, BusyError
from app.core.network import fetch_timeout

from app.core.fetcher import FetchError, fetch_subscription
from app.core.normalizer import normalize_nodes, normalize_nodes_with_source_names
from app.core.parsers.clash import AnyTLSOptionError, clash_to_ir, ir_to_clash_dict
from app.core.parsers.surge import SurgeParseError, looks_like_surge_config, parse_surge_nodes
from app.core.parsers.shadowrocket import ShadowrocketParseError, parse_shadowrocket_source
from app.core.parser import ParseError, parse_clash_yaml_full
from app.core.subconverter import (
    SubconverterError,
    convert_subscription_to_clash,
    is_subconverter_configured,
)
from app.ir import ProxyNode


class SubscriptionError(ValueError):
    pass


class SubscriptionUnavailableError(SubscriptionError):
    """External source failure eligible for a generation-matched stale artifact."""


_loads = SingleFlight()


def _clash_nodes(raw_proxies: list[dict]) -> list[ProxyNode]:
    try:
        return normalize_nodes([clash_to_ir(proxy) for proxy in raw_proxies])
    except AnyTLSOptionError as exc:
        raise SubscriptionError(str(exc)) from exc


async def load_subscription(url: str, *, target: str = "mihomo") -> tuple[list[ProxyNode], dict]:
    target = "shadowrocket" if target in {"shadowrocket", "shadowrocket-config"} else "mihomo"
    key = hashlib.sha256((url + '\0' + os.getenv('SUBFLOW_SUBSCRIPTION_USER_AGENT', '')
                          + '\0' + os.getenv('SUBFLOW_SUBCONVERTER_URL', '')
                          + '\0' + target + '\0' + os.getenv('SUBFLOW_SHADOWROCKET_USER_AGENT', '')).encode()).digest()
    try:
        async with asyncio.timeout(fetch_timeout()):
            result = await _loads.run(key, lambda: _load_subscription(url, target=target))
    except BusyError as exc:
        raise SubscriptionUnavailableError(str(exc)) from exc
    except TimeoutError as exc:
        raise SubscriptionUnavailableError('subscription refresh deadline exceeded') from exc
    # Each target owns its nodes; compilers cannot mutate peers.
    return deepcopy(result)


def _shadowrocket_source(content: str) -> tuple[list[ProxyNode], dict]:
    """Keep the received native text; parse only an inventory for policy names."""
    profile = None
    try:
        proxies, _ = parse_clash_yaml_full(content)
    except ParseError:
        try:
            original, profile = parse_shadowrocket_source(content)
        except ShadowrocketParseError as exc:
            raise SubscriptionError(str(exc)) from exc
        source_format = "ini" if profile is not None else "uris"
    else:
        try:
            original = [clash_to_ir(proxy) for proxy in proxies]
        except (ValueError, TypeError, AttributeError, OverflowError):
            raise SubscriptionError("Shadowrocket 原生 YAML 节点字段无效") from None
        source_format = "yaml"
    pairs = normalize_nodes_with_source_names(original)
    names = {node.name: name for node, name in pairs}
    if (not pairs or len(names) != len(pairs)
            or len({node.name for node in original}) != len(original)
            or any(any(c in name for c in ",=\r\n") for name in names.values())):
        raise SubscriptionError("Shadowrocket 原生节点名称为空、冲突或无法用于策略引用")
    return [node for node, _ in pairs], {
        "source-format": "shadowrocket", "_shadowrocket_source": content,
        "_shadowrocket_format": source_format, "_shadowrocket_names": names,
        "_shadowrocket_profile": profile,
    }


async def _load_subscription(url: str, *, target: str = "mihomo") -> tuple[list[ProxyNode], dict]:
    """Negotiate the source family and retain its native connectivity envelope.

    Shadowrocket inventory is used only for policy; its native text is published
    unchanged. Other inputs use Clash YAML or the existing Surge parser.
    """
    try:
        content = await fetch_subscription(url, target=target) if target == "shadowrocket" else await fetch_subscription(url)
    except FetchError as exc:
        raise SubscriptionUnavailableError(str(exc)) from exc

    if not content.strip():
        raise SubscriptionError("subscription content is empty")
    if target == "shadowrocket":
        return _shadowrocket_source(content)

    try:
        raw_proxies, raw_config = parse_clash_yaml_full(content)
    except ParseError as clash_exc:
        if not looks_like_surge_config(content):
            if not is_subconverter_configured():
                raise SubscriptionError(
                    "subscription returned unexpected content: expected Clash YAML or Surge "
                    "config. For universal subscriptions that force Base64 or URI output, "
                    "use the provider's Clash/Mihomo link or configure "
                    "SUBFLOW_SUBCONVERTER_URL for subscription compatibility"
                ) from clash_exc
            try:
                converted = await convert_subscription_to_clash(url)
                raw_proxies, raw_config = parse_clash_yaml_full(converted)
            except SubconverterError as adapter_exc:
                raise SubscriptionUnavailableError(f'subscription compatibility conversion failed: {adapter_exc}') from adapter_exc
            except ParseError as adapter_exc:
                raise SubscriptionError(
                    f"subscription compatibility conversion failed: {adapter_exc}"
                ) from adapter_exc
            raw_config = dict(raw_config)
            raw_config.pop("_surge_source", None)
            raw_config["source-format"] = "subconverter"
            return _clash_nodes(raw_proxies), raw_config
        try:
            ir_nodes = parse_surge_nodes(content)
        except SurgeParseError as surge_exc:
            raise SubscriptionError(f"subscription returned invalid Surge config: {surge_exc}") from surge_exc
        if not ir_nodes:
            raise SubscriptionError("Surge subscription contains no supported proxy nodes") from clash_exc
        normalized = normalize_nodes(ir_nodes)
        return normalized, {
            "source-format": "surge",
            "_surge_source": content,
            "proxies": [ir_to_clash_dict(node) for node in normalized],
        }

    # Native source metadata is created only by our Surge parser, never by YAML.
    for key in ("_surge_source", "_shadowrocket_source", "_shadowrocket_format", "_shadowrocket_names", "_shadowrocket_profile"):
        raw_config.pop(key, None)
    raw_config.pop("source-format", None)
    return _clash_nodes(raw_proxies), raw_config
