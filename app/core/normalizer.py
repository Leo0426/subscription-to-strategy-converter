from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
import re
from typing import Any

from app.ir import ProxyNode


def _clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _freeze(value: Any) -> Any:
    """Return a hashable, deterministic representation of connection data."""
    if is_dataclass(value):
        value_type = type(value)
        return (
            "dataclass",
            f"{value_type.__module__}.{value_type.__qualname__}",
            _freeze(asdict(value)),
        )
    if isinstance(value, dict):
        items = [(_freeze(key), _freeze(item)) for key, item in value.items()]
        return ("dict", tuple(sorted(items, key=repr)))
    if isinstance(value, list):
        return ("list", tuple(_freeze(item) for item in value))
    if isinstance(value, tuple):
        return ("tuple", tuple(_freeze(item) for item in value))
    if isinstance(value, set):
        return ("set", tuple(sorted((_freeze(item) for item in value), key=repr)))
    value_type = type(value)
    type_name = f"{value_type.__module__}.{value_type.__qualname__}"
    try:
        hash(value)
    except TypeError:
        return ("repr", type_name, repr(value))
    return ("scalar", type_name, value)


def _connection_key(node: ProxyNode) -> tuple[Any, ...]:
    """Identify a connection without using its display name.

    A server can intentionally host multiple credentials, SNI virtual hosts,
    transports, or protocol variants on the same port.  Collapsing those by
    endpoint alone removes valid subscription entries.
    """
    return (
        node.protocol,
        node.server,
        node.port,
        _freeze(node.tls),
        _freeze(node.transport),
        _freeze(node.extra),
    )


def normalize_nodes(nodes: list[ProxyNode]) -> list[ProxyNode]:
    """Deduplicate identical connections and resolve display-name conflicts."""
    normalized: list[ProxyNode] = []
    seen_keys: set[tuple[Any, ...]] = set()
    used_names: dict[str, int] = {}

    for index, node in enumerate(nodes, start=1):
        dedup_key = _connection_key(node)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        base_name = _clean_name(node.name) or f"node-{index}"
        count = used_names.get(base_name, 0) + 1
        used_names[base_name] = count

        named = replace(node, name=base_name if count == 1 else f"{base_name}-{count}")
        normalized.append(named)

    return normalized
