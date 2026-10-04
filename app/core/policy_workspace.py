from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, is_dataclass
from typing import Any

from app.core.parsers.clash import clash_to_ir, ir_to_clash_dict
from app.core.provider_egress import apply_provider_egress
from app.ir import PolicyRule, PolicyWorkspace, ProxyGroup, ProxyNode, RuleProvider, TLSConfig, TransportConfig


POLICY_SECTIONS = {"proxies", "proxy-groups", "rules", "rule-providers"}
_LOGICAL_RULE_TYPES = {"AND", "OR", "NOT", "SUB-RULE"}
_REGEX_RULE_TYPES = {"DOMAIN-REGEX", "PROCESS-NAME-REGEX", "PROCESS-PATH-REGEX"}


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _rule_parts(rule: Any) -> tuple[str, str, str, list[str]]:
    if isinstance(rule, str):
        parts = [part.strip() for part in _rule_segments(rule)]
        rule_type = parts[0].upper() if parts else ""
        if rule_type in {"MATCH", "FINAL"}:
            match = ""
            target = parts[1] if len(parts) > 1 else ""
            options = parts[2:]
        else:
            match = parts[1] if len(parts) > 1 else ""
            target = parts[2] if len(parts) > 2 else ""
            options = parts[3:]
        return rule_type, match, target, options

    if isinstance(rule, dict):
        provider = str(rule.get("rule-set") or rule.get("provider") or "")
        rule_type = str(rule.get("type") or rule.get("rule") or "").upper()
        match = str(rule.get("match") or rule.get("value") or rule.get("domain") or rule.get("ip") or provider or "")
        target = str(rule.get("proxy") or rule.get("policy") or rule.get("target") or "")
        options_raw = rule.get("options") or []
        options = [str(item) for item in options_raw] if isinstance(options_raw, list) else []
        return rule_type, match, target, options

    return type(rule).__name__.upper(), "", "", []


def _rule_segments(rule: str) -> list[str]:
    kind, separator, remainder = rule.partition(",")
    rule_type = kind.strip().upper()
    if separator and rule_type in _REGEX_RULE_TYPES:
        # Mihomo regex rules have no options: only the last comma separates
        # the target; regex punctuation belongs to the payload.
        match, target_separator, target = remainder.rpartition(",")
        return [kind, match, target] if target_separator else [kind, remainder]
    return _split_rule(rule, quoted=rule_type not in _LOGICAL_RULE_TYPES, nested=rule_type != "URL-REGEX")


def _split_rule(rule: str, *, quoted: bool = True, nested: bool = True) -> list[str]:
    """Split top-level rule fields without splitting nested logical expressions."""
    parts: list[str] = []
    start = 0
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(rule):
        if escaped:
            escaped = False
        elif char == "\\" and quote:
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif quoted and char in {'"', "'"}:
            quote = char
        elif nested and char == "(":
            depth += 1
        elif nested and char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(rule[start:index])
            start = index + 1
    parts.append(rule[start:])
    return parts


def parse_policy_rule(rule: Any, index: int) -> PolicyRule:
    rule_type, match, target, options = _rule_parts(rule)
    provider = match if rule_type == "RULE-SET" else ""
    return PolicyRule(
        id=f"rule:{index}",
        index=index,
        type=rule_type,
        match=match,
        target=target,
        provider=provider,
        options=options,
        raw=_jsonable(rule),
    )


def _logical_clauses(payload: str) -> list[str]:
    """Return outer parenthesized clauses using Mihomo's logical grammar."""
    clauses = []
    depth = 0
    start = 0
    for index, char in enumerate(payload):
        if char == "(":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return []
            if depth == 0:
                clauses.append(payload[start:index].strip())
    return [] if depth else clauses


def rule_expression_matches(rule: PolicyRule) -> Iterator[tuple[str, str]]:
    """Walk effective logical leaves without treating regex text as clauses."""
    pending = [(rule.type.upper(), rule.match or rule.provider)]
    while pending:
        kind, payload = pending.pop()
        if kind in _LOGICAL_RULE_TYPES:
            children = []
            for body in _logical_clauses(payload):
                if body.startswith("("):
                    children.append((kind, body))
                else:
                    child_kind, separator, child_payload = body.partition(",")
                    if separator:
                        child_kind = child_kind.strip().upper()
                        if child_kind not in _LOGICAL_RULE_TYPES | _REGEX_RULE_TYPES:
                            child_payload = _split_rule(child_payload)[0]
                        children.append((child_kind, child_payload.strip()))
            pending.extend(reversed(children))
        else:
            yield kind, payload


def rule_provider_references(rule: PolicyRule) -> tuple[str, ...]:
    """Read effective RULE-SET clauses, never regex payloads or stale raw text."""
    return tuple(dict.fromkeys(match for kind, match in rule_expression_matches(rule)
                               if kind == "RULE-SET" and match))


def config_to_workspace(config: dict[str, Any], nodes: list[ProxyNode] | None = None, target: str = "mihomo") -> PolicyWorkspace:
    proxies = nodes
    if proxies is None:
        proxies = [
            clash_to_ir(proxy)
            for proxy in config.get("proxies", [])
            if isinstance(proxy, dict)
        ]

    groups = [
        ProxyGroup(
            name=str(group.get("name") or ""),
            type=str(group.get("type") or "select"),
            members=[str(item) for item in group.get("proxies", []) if item is not None],
            raw=_jsonable(group),
        )
        for group in config.get("proxy-groups", [])
        if isinstance(group, dict)
    ]

    providers = [
        RuleProvider(
            name=str(name),
            type=str(provider.get("type") or "") if isinstance(provider, dict) else "",
            behavior=str(provider.get("behavior") or "") if isinstance(provider, dict) else "",
            format=str(provider.get("format") or "") if isinstance(provider, dict) else "",
            url=str(provider.get("url") or "") if isinstance(provider, dict) else "",
            raw=_jsonable(provider if isinstance(provider, dict) else {}),
        )
        for name, provider in (config.get("rule-providers") or {}).items()
    ] if isinstance(config.get("rule-providers"), dict) else []

    rules = [
        parse_policy_rule(rule, index)
        for index, rule in enumerate(config.get("rules", []) if isinstance(config.get("rules"), list) else [])
    ]

    settings = {
        key: _jsonable(value)
        for key, value in config.items()
        if key not in POLICY_SECTIONS
    }

    return PolicyWorkspace(
        target=target,
        proxies=proxies,
        proxy_groups=groups,
        rules=rules,
        rule_providers=providers,
        settings=settings,
    )


def workspace_to_dict(workspace: PolicyWorkspace) -> dict[str, Any]:
    return _jsonable(workspace)


def workspace_from_dict(data: dict[str, Any]) -> PolicyWorkspace:
    proxies = [
        _proxy_from_workspace_dict(proxy)
        for proxy in data.get("proxies", [])
        if isinstance(proxy, dict)
    ]

    # Preserve full proxy fields when clients post Mihomo-shaped proxies.
    if data.get("proxies") and any("type" in item for item in data.get("proxies", []) if isinstance(item, dict)):
        proxies = [clash_to_ir(item) for item in data.get("proxies", []) if isinstance(item, dict)]

    groups = [
        ProxyGroup(
            name=str(group.get("name") or ""),
            type=str(group.get("type") or "select"),
            members=[str(item) for item in group.get("members", group.get("proxies", [])) if item is not None],
            raw=dict(group.get("raw") or group),
        )
        for group in data.get("proxy_groups", data.get("proxy-groups", []))
        if isinstance(group, dict)
    ]

    rules = [_rule_from_workspace_dict(rule, index) for index, rule in enumerate(data.get("rules", []))]

    providers = [
        RuleProvider(
            name=str(provider.get("name") or name),
            type=str(provider.get("type") or ""),
            behavior=str(provider.get("behavior") or ""),
            format=str(provider.get("format") or ""),
            url=str(provider.get("url") or ""),
            raw=dict(provider.get("raw") or provider),
        )
        for name, provider in _iter_provider_items(data.get("rule_providers", data.get("rule-providers", [])))
    ]

    return PolicyWorkspace(
        target=str(data.get("target") or "mihomo"),
        proxies=proxies,
        proxy_groups=groups,
        rules=rules,
        rule_providers=providers,
        settings=dict(data.get("settings") or {}),
    )


def _rule_from_workspace_dict(value: Any, index: int) -> PolicyRule:
    if not isinstance(value, dict) or "raw" not in value:
        return parse_policy_rule(value, index)
    rule = PolicyRule(
        id=str(value.get("id") or f"rule:{index}"),
        index=int(value.get("index", index)),
        type=str(value.get("type") or "").upper(),
        match=str(value.get("match") or ""),
        target=str(value.get("target") or ""),
        provider=str(value.get("provider") or ""),
        options=[str(item) for item in value.get("options", [])],
        raw=value.get("raw"),
    )
    if rule.type == "RULE-SET":
        _sync_rule_provider(rule)
    return rule


def _sync_rule_provider(rule: PolicyRule) -> None:
    """Keep RULE-SET's provider alias and match field in agreement."""
    if not rule.provider:
        rule.provider = rule.match
    elif not rule.match:
        rule.match = rule.provider
    elif rule.match != rule.provider:
        _, raw_match, _, _ = _rule_parts(rule.raw)
        if rule.match == raw_match:
            rule.match = rule.provider
        elif rule.provider == raw_match:
            rule.provider = rule.match
        else:
            raise ValueError("RULE-SET rule match and provider disagree")


def _proxy_from_workspace_dict(proxy: dict[str, Any]) -> ProxyNode:
    tls = proxy.get("tls") if isinstance(proxy.get("tls"), dict) else {}
    transport = proxy.get("transport") if isinstance(proxy.get("transport"), dict) else {}
    return ProxyNode(
        name=str(proxy.get("name") or ""),
        protocol=str(proxy.get("protocol") or proxy.get("type") or ""),
        server=str(proxy.get("server") or ""),
        port=int(proxy.get("port") or 0),
        tls=TLSConfig(
            enabled=bool(tls.get("enabled", False)),
            sni=str(tls.get("sni") or ""),
            insecure=bool(tls.get("insecure", False)),
            alpn=[str(item) for item in tls.get("alpn", [])] if isinstance(tls.get("alpn"), list) else [],
            fingerprint=str(tls.get("fingerprint") or ""),
            reality=dict(tls.get("reality") or {}),
        ),
        transport=TransportConfig(
            type=str(transport.get("type") or ""),
            path=str(transport.get("path") or ""),
            host=str(transport.get("host") or ""),
            headers={str(key): str(value) for key, value in dict(transport.get("headers") or {}).items()},
            service_name=str(transport.get("service_name") or ""),
        ),
        extra=dict(proxy.get("extra") or {}),
    )


def _iter_provider_items(value: Any) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        return [(str(name), provider) for name, provider in value.items() if isinstance(provider, dict)]
    if isinstance(value, list):
        return [(str(item.get("name") or index), item) for index, item in enumerate(value) if isinstance(item, dict)]
    return []


def workspace_to_mihomo_config(workspace: PolicyWorkspace) -> dict[str, Any]:
    config = dict(workspace.settings)
    config["proxies"] = [ir_to_clash_dict(proxy) for proxy in workspace.proxies]
    config["proxy-groups"] = [
        {**group.raw, "name": group.name, "type": group.type, "proxies": list(group.members)}
        for group in workspace.proxy_groups
    ]
    config["rule-providers"] = {
        provider.name: dict(provider.raw)
        for provider in workspace.rule_providers
    }
    config["rules"] = [_compiled_rule(rule) for rule in workspace.rules]
    return config


def _compiled_rule(rule: PolicyRule) -> Any:
    """Keep source syntax unless edited structured fields need materializing."""
    raw = rule.raw
    if isinstance(raw, dict):
        raw_type, raw_match, raw_target, raw_options = _rule_parts(raw)
        changes = _rule_changes(rule, raw_type, raw_match, raw_target, raw_options)
        if not changes:
            return raw
        compiled = dict(raw)
        if "type" in changes:
            key = _first_rule_key(raw, ("type", "rule"), "type")
            compiled[key] = rule.type
        if "match" in changes:
            key = _first_rule_key(raw, ("match", "value", "domain", "ip", "rule-set", "provider"), "match")
            compiled[key] = rule.match
        if "target" in changes:
            key = _first_rule_key(raw, ("proxy", "policy", "target"), "target")
            compiled[key] = rule.target
        if "options" in changes:
            compiled["options"] = list(rule.options)
        return compiled

    if raw is None:
        return _render_rule_fields(rule)
    if not isinstance(raw, str):
        return raw
    raw_type, raw_match, raw_target, raw_options = _rule_parts(raw)
    changes = _rule_changes(rule, raw_type, raw_match, raw_target, raw_options)
    if not changes:
        return raw
    if (raw_type in {"MATCH", "FINAL"}) != (rule.type in {"MATCH", "FINAL"}):
        return _render_rule_fields(rule)

    parts = _rule_segments(raw)
    target_index = 1 if raw_type in {"MATCH", "FINAL"} else 2
    while len(parts) <= target_index:
        parts.append("")
    for field, index, value in (("type", 0, rule.type), ("match", 1, rule.match), ("target", target_index, rule.target)):
        if field in changes and not (field == "match" and target_index == 1):
            parts[index] = _replace_rule_segment(parts[index], value)
    if "options" in changes:
        parts = parts[:target_index + 1] + list(rule.options)
    return ",".join(parts)


def _rule_changes(rule: PolicyRule, raw_type: str, raw_match: str, raw_target: str, raw_options: list[str]) -> set[str]:
    changes: set[str] = set()
    if rule.type != raw_type:
        changes.add("type")
    if rule.match != raw_match:
        changes.add("match")
    if rule.target != raw_target:
        changes.add("target")
    if rule.options != raw_options:
        changes.add("options")
    return changes


def _first_rule_key(raw: dict[str, Any], keys: tuple[str, ...], fallback: str) -> str:
    for key in keys:
        if raw.get(key):
            return key
    for key in keys:
        if key in raw:
            return key
    return fallback


def _replace_rule_segment(segment: str, value: str) -> str:
    leading = segment[:len(segment) - len(segment.lstrip())]
    trailing = segment[len(segment.rstrip()):]
    return f"{leading}{value}{trailing}"


def _render_rule_fields(rule: PolicyRule) -> str:
    if rule.type in {"MATCH", "FINAL"}:
        return ",".join((rule.type, rule.target, *rule.options))
    return ",".join((rule.type, rule.match, rule.target, *rule.options))


def compile_mihomo_config(config: dict[str, Any], nodes: list[ProxyNode]) -> dict[str, Any]:
    workspace = config_to_workspace(config, nodes, target="mihomo")
    compiled = workspace_to_mihomo_config(workspace)
    apply_provider_egress(
        compiled["rule-providers"],
        [group.name for group in workspace.proxy_groups],
    )
    return compiled
