from __future__ import annotations

import re

from app.ir import ProxyNode


def _clean_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_nodes(nodes: list[ProxyNode]) -> list[ProxyNode]:
    """Deduplicate by (protocol, server, port) and resolve name conflicts."""
    normalized: list[ProxyNode] = []
    seen_keys: set[tuple[str, str, int]] = set()
    used_names: dict[str, int] = {}

    for index, node in enumerate(nodes, start=1):
        dedup_key = (node.protocol, node.server, node.port)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        base_name = _clean_name(node.name) or f"node-{index}"
        count = used_names.get(base_name, 0) + 1
        used_names[base_name] = count

        from dataclasses import replace
        named = replace(node, name=base_name if count == 1 else f"{base_name}-{count}")
        normalized.append(named)

    return normalized
