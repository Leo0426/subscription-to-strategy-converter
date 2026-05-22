"""Sing-box config compiler.

Takes ProxyNode IR + Clash-format strategy (proxy-groups, rules, rule-providers)
and produces a complete Sing-box JSON config dict.

Flow:
  ProxyNode list  →  outbound[]   (IR → Sing-box outbound format)
  proxy-groups    →  outbound[]   (selector / urltest / loadbalance)
  rules           →  route.rules  (Clash rule syntax → Sing-box rule syntax)
  rule-providers  →  route.rule_set[]
"""
from __future__ import annotations

from typing import Any

from app.ir import ProxyNode


# ── Clash → Sing-box protocol name mapping ─────────────────────────────────

_PROTO_MAP = {
    "ss": "shadowsocks",
    "vmess": "vmess",
    "vless": "vless",
    "trojan": "trojan",
    "hysteria2": "hysteria2",
    "tuic": "tuic",
    "socks5": "socks",
    "http": "http",
}

# Clash builtin targets → Sing-box builtin outbound tags
_BUILTIN_MAP = {
    "DIRECT": "direct",
    "REJECT": "block",
    "REJECT-DROP": "block",
    "PASS": "direct",
    "GLOBAL": "direct",
}

# Clash rule types → Sing-box route rule fields
_RULE_FIELD_MAP = {
    "DOMAIN": "domain",
    "DOMAIN-SUFFIX": "domain_suffix",
    "DOMAIN-KEYWORD": "domain_keyword",
    "DOMAIN-REGEX": "domain_regex",
    "IP-CIDR": "ip_cidr",
    "IP-CIDR6": "ip_cidr",
    "SRC-IP-CIDR": "source_ip_cidr",
    "GEOIP": "geoip",
    "GEOSITE": "geosite",
    "RULE-SET": "rule_set",
    "PROCESS-NAME": "process_name",
    "PROCESS-PATH": "process_path",
    "PORT": "port",
    "DEST-PORT": "port",
    "SRC-PORT": "source_port",
    "NETWORK": "network",
}


def _resolve(tag: str) -> str:
    return _BUILTIN_MAP.get(tag, tag)


# ── IR → Sing-box outbound ─────────────────────────────────────────────────


def _ir_to_outbound(node: ProxyNode) -> dict[str, Any]:
    sb_type = _PROTO_MAP.get(node.protocol, node.protocol)
    d: dict[str, Any] = {
        "type": sb_type,
        "tag": node.name,
        "server": node.server,
        "server_port": node.port,
    }

    # TLS
    if node.tls.enabled:
        tls: dict[str, Any] = {"enabled": True}
        if node.tls.sni:
            tls["server_name"] = node.tls.sni
        if node.tls.insecure:
            tls["insecure"] = True
        if node.tls.alpn:
            tls["alpn"] = node.tls.alpn
        if node.tls.fingerprint:
            tls["utls"] = {"enabled": True, "fingerprint": node.tls.fingerprint}
        if node.tls.reality:
            tls["reality"] = {
                "enabled": True,
                "public_key": node.tls.reality.get("public_key", ""),
                "short_id": node.tls.reality.get("short_id", ""),
            }
        d["tls"] = tls

    # Transport
    t = node.transport
    if t.type:
        transport: dict[str, Any] = {"type": t.type if t.type != "h2" else "http"}
        if t.type == "ws":
            if t.path:
                transport["path"] = t.path
            headers = dict(t.headers)
            if t.host and "Host" not in headers:
                headers["Host"] = t.host
            if headers:
                transport["headers"] = headers
        elif t.type == "grpc":
            if t.service_name:
                transport["service_name"] = t.service_name
        elif t.type == "h2":
            if t.path:
                transport["path"] = t.path
            if t.host:
                transport["host"] = [t.host]
        d["transport"] = transport

    # Protocol-specific
    proto = node.protocol

    if proto == "ss":
        d["method"] = node.extra.get("cipher", "aes-256-gcm")
        d["password"] = node.extra.get("password", "")

    elif proto == "vmess":
        d["uuid"] = node.extra.get("uuid", "")
        d["alter_id"] = node.extra.get("alter_id", 0)
        d["security"] = node.extra.get("cipher", "auto")

    elif proto == "vless":
        d["uuid"] = node.extra.get("uuid", "")
        if node.extra.get("flow"):
            d["flow"] = node.extra["flow"]

    elif proto == "trojan":
        d["password"] = node.extra.get("password", "")

    elif proto == "hysteria2":
        d["password"] = node.extra.get("password", "")
        if node.extra.get("obfs"):
            d["obfs"] = {
                "type": node.extra["obfs"],
                "password": node.extra.get("obfs_password", ""),
            }
        if node.extra.get("up") is not None:
            d["up_mbps"] = node.extra["up"]
        if node.extra.get("down") is not None:
            d["down_mbps"] = node.extra["down"]

    elif proto == "tuic":
        d["uuid"] = node.extra.get("uuid", "")
        d["password"] = node.extra.get("password", "")
        d["congestion_control"] = node.extra.get("congestion_controller", "bbr")
        if node.extra.get("alpn"):
            d.setdefault("tls", {})["alpn"] = node.extra["alpn"]

    elif proto in {"socks5", "http"}:
        if node.extra.get("username"):
            d["username"] = node.extra["username"]
        if node.extra.get("password"):
            d["password"] = node.extra["password"]

    return d


# ── Proxy groups → Sing-box outbounds ─────────────────────────────────────


def _group_to_outbound(
    group: dict[str, Any],
    node_tags: list[str],
    group_tags: set[str],
) -> dict[str, Any]:
    name = str(group.get("name") or "")
    gtype = str(group.get("type") or "select")
    raw_members = [str(m) for m in (group.get("proxies") or []) if m is not None]

    # Keep only members that exist (nodes or other groups or builtins)
    known = set(node_tags) | group_tags | set(_BUILTIN_MAP.keys())
    members = [_resolve(m) for m in raw_members if m in known]
    if not members:
        members = node_tags[:] or ["direct"]

    url = str(group.get("url") or "https://www.gstatic.com/generate_204")
    interval = int(group.get("interval") or 300)

    if gtype == "select":
        return {"type": "selector", "tag": name, "outbounds": members}

    if gtype in {"url-test", "fallback"}:
        return {
            "type": "urltest",
            "tag": name,
            "outbounds": members,
            "url": url,
            "interval": f"{interval}s",
            "tolerance": 50,
        }

    if gtype == "load-balance":
        return {
            "type": "loadbalance",
            "tag": name,
            "outbounds": members,
            "url": url,
            "interval": f"{interval}s",
        }

    return {"type": "selector", "tag": name, "outbounds": members}


# ── Rules → Sing-box route rules ──────────────────────────────────────────


def _compile_rules(
    rules: list[Any],
) -> tuple[list[dict[str, Any]], str]:
    """Returns (route_rules, final_outbound_tag)."""
    route_rules: list[dict[str, Any]] = []
    final = "direct"

    for rule in rules:
        if isinstance(rule, str):
            parts = [p.strip() for p in rule.split(",")]
        elif isinstance(rule, dict):
            # Skip complex dict-format rules for now
            continue
        else:
            continue

        if not parts or not parts[0]:
            continue

        rule_type = parts[0].upper()

        if rule_type == "MATCH":
            final = _resolve(parts[1]) if len(parts) > 1 else "direct"
            continue

        if len(parts) < 3:
            continue

        # Handle optional no-resolve suffix
        if len(parts) >= 4 and parts[-1].lower() == "no-resolve":
            target_raw = parts[-2]
            value = parts[1]
        else:
            target_raw = parts[-1]
            value = parts[1]

        sb_field = _RULE_FIELD_MAP.get(rule_type)
        if not sb_field:
            continue

        outbound = _resolve(target_raw)
        route_rules.append({sb_field: [value], "outbound": outbound})

    return route_rules, final


# ── Rule providers → rule_set ─────────────────────────────────────────────


def _compile_rule_sets(
    rule_providers: dict[str, Any],
    download_detour: str,
) -> list[dict[str, Any]]:
    rule_sets: list[dict[str, Any]] = []
    for name, provider in rule_providers.items():
        if not isinstance(provider, dict):
            continue
        url = str(provider.get("url") or "")
        fmt = str(provider.get("format") or "")
        interval = int(provider.get("interval") or 86400)
        sb_format = "binary" if fmt == "mrs" or url.endswith(".mrs") else "source"
        rule_sets.append({
            "type": "remote",
            "tag": str(name),
            "format": sb_format,
            "url": url,
            "download_detour": download_detour,
            "update_interval": f"{interval}s",
        })
    return rule_sets


# ── Main builder ──────────────────────────────────────────────────────────


def build_singbox_config(
    nodes: list[ProxyNode],
    proxy_groups: list[Any],
    rules: list[Any],
    rule_providers: dict[str, Any],
) -> dict[str, Any]:
    """Compile a complete Sing-box JSON config.

    Parameters mirror the Clash/Mihomo template sections so the same
    strategy data produced by ``apply_template`` can be reused directly.
    """
    node_tags = [node.name for node in nodes]
    group_tags = {str(g.get("name")) for g in proxy_groups if isinstance(g, dict) and g.get("name")}

    node_outbounds = [_ir_to_outbound(node) for node in nodes]
    group_outbounds = [
        _group_to_outbound(g, node_tags, group_tags)
        for g in proxy_groups
        if isinstance(g, dict) and g.get("name")
    ]

    route_rules, final_tag = _compile_rules(rules if isinstance(rules, list) else [])
    # Ensure the final tag references an existing outbound
    all_tags = {ob["tag"] for ob in group_outbounds + node_outbounds} | {"direct", "block"}
    if final_tag not in all_tags:
        final_tag = group_outbounds[0]["tag"] if group_outbounds else "direct"

    rule_sets = _compile_rule_sets(rule_providers or {}, final_tag)

    # Identify ad-blocking rule sets for DNS blocking rule
    ad_set_tags = [rs["tag"] for rs in rule_sets if any(
        kw in rs["tag"].lower() for kw in ("ads", "reject", "adrules", "adguard")
    )]

    dns_rules: list[dict[str, Any]] = [{"geosite": ["cn"], "server": "local"}]
    if ad_set_tags:
        dns_rules.append({"rule_set": ad_set_tags, "server": "block"})

    return {
        "log": {"level": "info", "timestamp": True},
        "dns": {
            "servers": [
                {
                    "tag": "remote",
                    "address": "https://dns.google/dns-query",
                    "detour": final_tag,
                },
                {
                    "tag": "local",
                    "address": "https://223.5.5.5/dns-query",
                    "detour": "direct",
                },
                {"tag": "block", "address": "rcode://success"},
            ],
            "rules": dns_rules,
            "final": "remote",
            "strategy": "prefer_ipv4",
        },
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": 7890,
            },
            {
                "type": "tun",
                "tag": "tun-in",
                "inet4_address": "172.19.0.1/30",
                "inet6_address": "fdfe:dcba:9876::1/126",
                "auto_route": True,
                "strict_route": True,
                "stack": "system",
                "sniff": True,
            },
        ],
        "outbounds": (
            group_outbounds
            + node_outbounds
            + [
                {"type": "direct", "tag": "direct"},
                {"type": "block", "tag": "block"},
                {"type": "dns", "tag": "dns-out"},
            ]
        ),
        "route": {
            "rules": [
                {"protocol": "dns", "outbound": "dns-out"},
                {"ip_is_private": True, "outbound": "direct"},
                *route_rules,
            ],
            "rule_set": rule_sets,
            "final": final_tag,
            "auto_detect_interface": True,
        },
    }
