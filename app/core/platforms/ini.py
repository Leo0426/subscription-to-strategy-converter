"""Shared assembly for INI policy artifacts; dialects own client syntax."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.ir import ProxyNode

_BUILTIN_TARGETS = frozenset({"DIRECT", "REJECT", "REJECT-DROP"})


class NoSupportedNodesError(ValueError):
    pass


@dataclass
class UnsupportedRuleTypeError(Exception):
    """Raised when a RULE-SET URL uses a format the target cannot process."""
    code: str
    field: str
    value: str
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "field": self.field,
            "value": self.value,
            "suggestion": self.suggestion,
        }


@dataclass
class UnsupportedProtocolError(Exception):
    """Raised when a ProxyNode uses a protocol the target cannot emit."""
    code: str
    value: str       # the protocol name
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "value": self.value,
            "suggestion": self.suggestion,
        }


@dataclass(frozen=True)
class IniDialect:
    name: str
    node: Callable[[ProxyNode], str] | None
    group: Callable[[dict[str, Any], list[str], set[str]], str]
    rule: Callable[[str, dict[str, Any]], str | None]
    rule_types: frozenset[str]
    general: str
    host: Callable[[list[ProxyNode]], str | None]
    require_nodes: bool = False


def close_group_members(
    proxy_groups: list[Any],
    compiled_node_names: list[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Remove groups emptied by target-specific protocol filtering.

    A group that originally named nodes or dynamically selected nodes must not
    become ``DIRECT`` merely because the client skipped every matching protocol.
    Removing it and closing parent references preserves the template's own
    fallback chain instead.
    """
    groups: list[dict[str, Any]] = []
    prunable_names: set[str] = set()
    removed_names: set[str] = set()
    for raw_group in proxy_groups:
        if not isinstance(raw_group, dict) or not raw_group.get("name"):
            continue
        group = dict(raw_group)
        if isinstance(raw_group.get("proxies"), list):
            group["proxies"] = list(raw_group["proxies"])
        name = str(group["name"])
        if group.get("proxies"):
            prunable_names.add(name)
        elif (
            group.get("include-all")
            or group.get("use")
            or group.get("filter")
            or group.get("exclude-filter")
        ):
            members = list(compiled_node_names)
            include_expression = str(group.get("filter") or "").strip()
            exclude_expression = str(group.get("exclude-filter") or "").strip()
            if include_expression:
                include = re.compile(include_expression)
                members = [member for member in members if include.search(member)]
            if exclude_expression:
                exclude = re.compile(exclude_expression)
                members = [member for member in members if not exclude.search(member)]
            group["proxies"] = members
            prunable_names.add(name)
        groups.append(group)

    while True:
        group_names = {str(group["name"]) for group in groups}
        valid_members = set(compiled_node_names) | group_names | _BUILTIN_TARGETS
        newly_empty: set[str] = set()
        for group in groups:
            members = group.get("proxies")
            if not isinstance(members, list):
                continue
            group["proxies"] = [
                member for member in members if str(member) in valid_members
            ]
            name = str(group["name"])
            if name in prunable_names and not group["proxies"]:
                newly_empty.add(name)

        if not newly_empty:
            return groups, removed_names
        removed_names.update(newly_empty)
        groups = [
            group for group in groups if str(group["name"]) not in newly_empty
        ]


def redirect_unavailable_target(line: str, unavailable_targets: set[str]) -> str:
    if not unavailable_targets:
        return line
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 2:
        return line
    target_index = -2 if parts[-1].lower() == "no-resolve" else -1
    if parts[target_index] in unavailable_targets:
        parts[target_index] = "REJECT"
    return ",".join(parts)


def build_ini_config(
    nodes: list[ProxyNode],
    proxy_groups: list[Any],
    rules: list[Any],
    rule_providers: dict[str, Any],
    *,
    dialect: IniDialect,
    excluded_node_names: set[str] | None = None,
) -> tuple[str, list[dict]]:
    warnings: list[dict] = []
    proxy_lines: list[str] = []
    compiled_nodes: list[ProxyNode] = []
    compiled_node_names: list[str] = []
    for node in nodes:
        try:
            if dialect.node is not None:
                proxy_lines.append(dialect.node(node))
            compiled_nodes.append(node)
            compiled_node_names.append(node.name)
        except UnsupportedProtocolError as exc:
            warnings.append(exc.to_dict())

    if dialect.require_nodes and not compiled_nodes:
        raise NoSupportedNodesError(f"{dialect.name}: no supported proxy nodes remain after compatibility checks")

    closed_groups, removed_group_names = close_group_members(
        proxy_groups,
        compiled_node_names,
    )
    group_names = {str(group["name"]) for group in closed_groups}
    unavailable_targets = (removed_group_names | {node.name for node in nodes} | (excluded_node_names or set())) - (
        set(compiled_node_names) | group_names | _BUILTIN_TARGETS
    )
    group_lines = [
        dialect.group(group, compiled_node_names, group_names)
        for group in closed_groups
    ]
    if removed_group_names:
        warnings.append(
            {
                "code": "unavailable_proxy_groups",
                "count": len(removed_group_names),
                "groups": sorted(removed_group_names),
                "suggestion": (
                    f"{dialect.name} 不支持这些组的全部节点，已删除空组及父级引用；"
                    "直接命中这些组的规则已改为 REJECT"
                ),
            }
        )

    providers = rule_providers if isinstance(rule_providers, dict) else {}
    rule_lines: list[str] = []
    unsupported_rule_set_urls: list[str] = []
    unsupported_rule_types: list[str] = []
    has_final = False
    for rule in (rules if isinstance(rules, list) else []):
        if not isinstance(rule, str):
            continue
        try:
            line = dialect.rule(rule, providers)
        except UnsupportedRuleTypeError as exc:
            unsupported_rule_set_urls.append(exc.value)
            continue
        if line is None:
            rule_type = rule.split(",", 1)[0].strip().upper()
            if rule_type and rule_type not in dialect.rule_types and rule_type != "MATCH":
                unsupported_rule_types.append(rule_type)
            continue
        line = redirect_unavailable_target(line, unavailable_targets)
        if line.startswith("FINAL,"):
            has_final = True
        rule_lines.append(line)

    if not has_final:
        rule_lines.append("FINAL,DIRECT")
    if unsupported_rule_set_urls:
        unique_urls = list(dict.fromkeys(unsupported_rule_set_urls))
        warnings.append(
            {
                "code": "unsupported_rule_sets",
                "count": len(unique_urls),
                "examples": unique_urls[:5],
                "suggestion": f"{dialect.name} 不支持这些规则源（MRS / Clash payload YAML / domain·ipcidr 裸列表），已跳过对应规则",
            }
        )
    if unsupported_rule_types:
        unique_types = list(dict.fromkeys(unsupported_rule_types))
        warnings.append(
            {
                "code": "unsupported_rule_types",
                "count": len(unique_types),
                "types": unique_types,
                "suggestion": f"{dialect.name} 不支持这些 Mihomo 规则类型，已跳过对应规则",
            }
        )

    sections: list[str] = [dialect.general]
    host_section = dialect.host(compiled_nodes)
    if host_section:
        sections.extend(["", host_section])
    if dialect.node is not None:
        sections.extend(["", "[Proxy]", *proxy_lines])
    sections.extend([
        "",
        "[Proxy Group]",
        *group_lines,
        "",
        "[Rule]",
        *rule_lines,
    ])
    return "\n".join(sections) + "\n", warnings
