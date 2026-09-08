"""Paired Shadowrocket node subscription and native Leo policy configuration.

Use the client's Clash-compatible node import, as Sub-Store does, so modern
transport fields need not be guessed into the unrelated native [Proxy] syntax.
"""
from __future__ import annotations

from typing import Any

from app.core.parsers.clash import ir_to_clash_dict
from app.core.platforms.ini import IniDialect, NoSupportedNodesError, build_ini_config
from app.core.platforms.surge import _rule_to_surge_line
from app.core.renderer import render_yaml
from app.ir import ProxyNode


_RULE_TYPES = frozenset({
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
    "IP-CIDR", "IP-CIDR6", "GEOIP", "DEST-PORT", "DST-PORT",
    "RULE-SET", "FINAL",
})
_PROTOCOLS = frozenset({
    "ss", "ssr", "vmess", "vless", "trojan", "hysteria", "hysteria2",
    "tuic", "anytls", "http", "socks5",
})


def _compatible_nodes(nodes: list[ProxyNode]) -> tuple[list[ProxyNode], list[dict]]:
    accepted, warnings = [], []
    for node in nodes:
        if node.protocol not in _PROTOCOLS or any(char in node.name for char in ",=\r\n"):
            warnings.append({
                "code": "unsupported_protocol", "value": node.protocol,
                "suggestion": "Shadowrocket 输出暂不支持此协议或包含 INI 分隔符的节点名称，已跳过",
            })
        else:
            accepted.append(node)
    if not accepted:
        raise NoSupportedNodesError("Shadowrocket: no supported proxy nodes remain after compatibility checks")
    return accepted, warnings


def build_shadowrocket_subscription(nodes: list[ProxyNode]) -> tuple[str, list[dict]]:
    accepted, warnings = _compatible_nodes(nodes)
    proxies = []
    for node in accepted:
        proxy = ir_to_clash_dict(node)
        if node.protocol in {"vmess", "vless"} and proxy.get("sni"):
            proxy["servername"] = proxy.pop("sni")
        if node.protocol in {"trojan", "hysteria", "hysteria2", "tuic", "anytls"}:
            proxy.pop("tls", None)
        if node.protocol == "tuic" and not proxy.get("token"):
            proxy.setdefault("version", 5)
        proxies.append(proxy)
    return render_yaml({"proxies": proxies}), warnings


def _group_line(group: dict[str, Any], node_names: list[str], group_names: set[str]) -> str:
    kind = str(group.get("type") or "select")
    if kind not in {"select", "url-test", "fallback", "load-balance"}:
        kind = "select"
    members = group.get("proxies") or node_names or ["REJECT"]
    parts = [kind, *members]
    if kind != "select":
        parts.extend([
            f"interval={int(group.get('interval') or 600)}",
            f"timeout={max(1, int(group.get('timeout') or 5000) // 1000)}",
            f"url={group.get('url') or 'https://cp.cloudflare.com/generate_204'}",
        ])
        if kind == "url-test":
            parts.append(f"tolerance={int(group.get('tolerance', 100))}")
    return f"{group['name']} = {', '.join(parts)}"


def _rule_line(rule: str, providers: dict[str, Any]) -> str | None:
    kind = rule.split(",", 1)[0].upper()
    if kind in {"DST-PORT", "DEST-PORT"}:
        line = _rule_to_surge_line(rule.replace("DEST-PORT,", "DST-PORT,", 1), providers)
        return line.replace("DEST-PORT,", "DST-PORT,", 1) if line else None
    if kind not in _RULE_TYPES and kind != "MATCH":
        return None
    # The emitted classical rules and audited text URLs are shared by both
    # clients; node syntax and group health-check syntax are kept separate.
    return _rule_to_surge_line(rule, providers)


def build_shadowrocket_config(
    nodes: list[ProxyNode], proxy_groups: list[Any], rules: list[Any],
    rule_providers: dict[str, Any],
) -> tuple[str, list[dict]]:
    accepted, warnings = _compatible_nodes(nodes)
    config, policy_warnings = build_ini_config(
        accepted, proxy_groups, rules, rule_providers,
        excluded_node_names={node.name for node in nodes} - {node.name for node in accepted},
        dialect=IniDialect(
            name="Shadowrocket", node=None, group=_group_line,
            rule=_rule_line, rule_types=_RULE_TYPES,
            general="\n".join([
                "[General]", "bypass-system = true", "ipv6 = false",
                "dns-server = 223.5.5.5, 119.29.29.29",
                "skip-proxy = 127.0.0.1, 192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, localhost, *.local",
            ]),
            host=lambda _: None, require_nodes=True,
        ),
    )
    return config, warnings + policy_warnings
