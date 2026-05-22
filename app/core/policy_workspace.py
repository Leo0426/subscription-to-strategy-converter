from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from app.core.parsers.clash import clash_to_ir, ir_to_clash_dict
from app.ir import PolicyRule, PolicyWorkspace, ProxyGroup, ProxyNode, RuleProvider, TLSConfig, TransportConfig


POLICY_SECTIONS = {"proxies", "proxy-groups", "rules", "rule-providers"}


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
        parts = [part.strip() for part in rule.split(",")]
        rule_type = parts[0].upper() if parts else ""
        match = parts[1] if len(parts) > 1 else ""
        options: list[str] = []
        target = ""
        if len(parts) >= 4 and parts[-1].lower() == "no-resolve":
            target = parts[-2]
            options = parts[3:]
        elif len(parts) > 2:
            target = parts[-1]
            options = parts[3:]
        elif len(parts) == 2 and rule_type == "MATCH":
            target = parts[1]
            match = ""
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

    rules = [
        PolicyRule(
            id=str(rule.get("id") or f"rule:{index}"),
            index=int(rule.get("index", index)),
            type=str(rule.get("type") or "").upper(),
            match=str(rule.get("match") or ""),
            target=str(rule.get("target") or ""),
            provider=str(rule.get("provider") or ""),
            options=[str(item) for item in rule.get("options", [])],
            raw=rule.get("raw"),
        )
        if isinstance(rule, dict) and "raw" in rule
        else parse_policy_rule(rule, index)
        for index, rule in enumerate(data.get("rules", []))
    ]

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
    config["rules"] = [rule.raw for rule in workspace.rules]
    return config
