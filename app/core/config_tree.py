from __future__ import annotations

from collections import defaultdict
from typing import Any


BUILTIN_TARGETS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "GLOBAL"}


def _tree_node(label: str, kind: str, meta: str = "", children: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "kind": kind,
        "meta": meta,
        "children": children or [],
    }


def _rule_parts(rule: Any) -> list[str]:
    if isinstance(rule, str):
        return [part.strip() for part in rule.split(",")]
    if isinstance(rule, dict):
        payload = rule.get("payload") or rule.get("rule")
        if isinstance(payload, str):
            return [part.strip() for part in payload.split(",")]
        target = rule.get("proxy") or rule.get("policy") or rule.get("target") or ""
        rule_type = rule.get("type") or rule.get("rule-type") or "RULE"
        value = rule.get("value") or rule.get("domain") or rule.get("ip") or rule.get("name") or ""
        return [str(rule_type), str(value), str(target)]
    return [str(rule)]


def _rule_target(rule: Any) -> str:
    if isinstance(rule, dict):
        target = rule.get("proxy") or rule.get("policy") or rule.get("target")
        return str(target) if target else ""

    parts = _rule_parts(rule)
    if len(parts) < 2:
        return ""
    if parts[-1].lower() == "no-resolve" and len(parts) >= 3:
        return parts[-2]
    return parts[-1]


def _rule_label(rule: Any) -> str:
    parts = _rule_parts(rule)
    if not parts:
        return str(rule)
    if len(parts) == 1:
        return parts[0]
    rule_type = parts[0]
    value = parts[1] if len(parts) > 1 else ""
    target = _rule_target(rule)
    if value:
        return f"{rule_type} · {value} -> {target}"
    return f"{rule_type} -> {target}"


def _member_kind(member: str, proxy_names: set[str], group_names: set[str]) -> str:
    if member in proxy_names:
        return "node"
    if member in group_names:
        return "group"
    if member in BUILTIN_TARGETS:
        return "builtin"
    return "missing"


def _proxy_node(proxy: dict[str, Any]) -> dict[str, Any]:
    label = str(proxy.get("name") or "未命名节点")
    meta_parts = [
        str(proxy.get("type") or "unknown"),
        str(proxy.get("server") or ""),
        str(proxy.get("port") or ""),
    ]
    meta = " · ".join(part for part in meta_parts if part)
    return _tree_node(label, "node", meta)


def _group_node(group: dict[str, Any], proxy_names: set[str], group_names: set[str]) -> dict[str, Any]:
    name = str(group.get("name") or "未命名策略组")
    group_type = str(group.get("type") or "select")
    members = [str(member) for member in group.get("proxies", []) if member is not None]
    children = [
        _tree_node(member, _member_kind(member, proxy_names, group_names))
        for member in members
    ]
    return _tree_node(name, "group", f"{group_type} · {len(members)} 个成员", children)


def _rules_by_target(rules: list[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for rule in rules:
        grouped[_rule_target(rule) or "未指定"].append(rule)

    nodes = []
    for target, target_rules in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        children = [_tree_node(_rule_label(rule), "rule") for rule in target_rules]
        nodes.append(_tree_node(target, "rule-target", f"{len(target_rules)} 条规则", children))
    return nodes


def _provider_node(name: str, provider: Any) -> dict[str, Any]:
    if not isinstance(provider, dict):
        return _tree_node(name, "provider")
    meta_parts = [
        str(provider.get("behavior") or ""),
        str(provider.get("format") or ""),
        str(provider.get("type") or ""),
    ]
    meta = " / ".join(part for part in meta_parts if part)
    url = provider.get("url")
    children = [_tree_node(str(url), "url")] if url else []
    return _tree_node(name, "provider", meta, children)


def build_config_tree(config: dict[str, Any]) -> dict[str, Any]:
    proxies = [proxy for proxy in config.get("proxies", []) if isinstance(proxy, dict)]
    groups = [group for group in config.get("proxy-groups", []) if isinstance(group, dict)]
    rules = config.get("rules", [])
    rule_providers = config.get("rule-providers", {})

    if not isinstance(rules, list):
        rules = []
    if not isinstance(rule_providers, dict):
        rule_providers = {}

    proxy_names = {str(proxy.get("name")) for proxy in proxies if proxy.get("name")}
    group_names = {str(group.get("name")) for group in groups if group.get("name")}

    node_type_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for proxy in proxies:
        node_type_groups[str(proxy.get("type") or "unknown")].append(_proxy_node(proxy))

    proxy_children = [
        _tree_node(proxy_type, "node-type", f"{len(children)} 个节点", children)
        for proxy_type, children in sorted(node_type_groups.items())
    ]
    group_children = [_group_node(group, proxy_names, group_names) for group in groups]
    provider_children = [
        _provider_node(str(name), provider)
        for name, provider in sorted(rule_providers.items(), key=lambda item: str(item[0]))
    ]

    root_children = [
        _tree_node("代理节点", "section", f"{len(proxies)} 个", proxy_children),
        _tree_node("策略组", "section", f"{len(groups)} 个", group_children),
        _tree_node("规则", "section", f"{len(rules)} 条", _rules_by_target(rules)),
        _tree_node("Rule Providers", "section", f"{len(provider_children)} 个", provider_children),
    ]
    return _tree_node("Mihomo 配置", "root", "最终导入结构", root_children)
