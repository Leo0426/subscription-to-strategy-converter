"""Surge .conf compiler.

Takes ProxyNode IR + Clash-format strategy (proxy-groups, rules, rule-providers)
and produces a complete Surge .conf string.

Flow:
  ProxyNode list  →  [Proxy] section
  proxy-groups    →  [Proxy Group] section
  rules           →  [Rule] section  (Clash rule syntax → Surge rule syntax)
  rule-providers  →  URL lookup for RULE-SET rules
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any

from app.ir import ProxyNode


# ── MRS → text-format URL substitution ────────────────────────────────────


@dataclass
class UnsupportedRuleTypeError(Exception):
    """Raised when a RULE-SET URL uses a format Surge cannot process (e.g. MRS)."""
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
    """Raised when a ProxyNode uses a protocol Surge does not support."""
    code: str
    value: str       # the protocol name
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "value": self.value,
            "suggestion": self.suggestion,
        }


# Regex patterns for MRS URL → Surge-compatible URL substitution.
# Each tuple: (compiled pattern, re.sub replacement string).
# First match wins. Add new patterns here as repositories are verified to
# provide both MRS and text-format rule files.
_MRS_SUBSTITUTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # HenryChiao/mihomo_yamls: meta/{domain|ipcidr}/*.mrs → *.txt
    (
        re.compile(
            r"^(https://raw\.githubusercontent\.com/HenryChiao/mihomo_yamls"
            r"/.+/ruleset/meta/(?:domain|ipcidr)/[^/]+)\.mrs$"
        ),
        r"\1.txt",
    ),
]


def _resolve_mrs_url(url: str) -> str:
    """Return a Surge-compatible URL for the given provider URL.

    If the URL is not MRS it is returned unchanged.
    If it is MRS and a substitution pattern matches, the text-format URL is
    returned.  Otherwise UnsupportedRuleTypeError is raised.
    """
    if not url.endswith(".mrs"):
        return url

    for pattern, replacement in _MRS_SUBSTITUTION_PATTERNS:
        if pattern.match(url):
            return pattern.sub(replacement, url)

    raise UnsupportedRuleTypeError(
        code="unsupported_rule_type",
        field="rule_set_url",
        value=url,
        suggestion="请替换为 txt/domain 格式规则源",
    )


# ── Shadowsocks cipher passthrough map ─────────────────────────────────────
# Clash and Surge share the same cipher names; this map is kept explicit so
# that any divergence can be patched without touching the render logic.

_SS_CIPHER_MAP: dict[str, str] = {
    "aes-128-gcm": "aes-128-gcm",
    "aes-256-gcm": "aes-256-gcm",
    "chacha20-ietf-poly1305": "chacha20-ietf-poly1305",
    "aes-128-cfb": "aes-128-cfb",
    "aes-192-cfb": "aes-192-cfb",
    "aes-256-cfb": "aes-256-cfb",
    "rc4-md5": "rc4-md5",
    "xchacha20-ietf-poly1305": "xchacha20-ietf-poly1305",
}

# Rule types Surge 5 natively supports (MATCH → FINAL handled separately)
_SURGE_RULE_TYPES: frozenset[str] = frozenset({
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
    "IP-CIDR", "IP-CIDR6", "GEOIP",
    "PROCESS-NAME", "USER-AGENT", "URL-REGEX",
    "DEST-PORT", "RULE-SET", "FINAL",
})

# These rule types support the optional no-resolve flag in Surge
_IP_RULE_TYPES: frozenset[str] = frozenset({"IP-CIDR", "IP-CIDR6", "GEOIP"})

_BUILTIN_TARGETS: frozenset[str] = frozenset({"DIRECT", "REJECT", "REJECT-DROP"})


# ── Node mapping layer ─────────────────────────────────────────────────────


def _ss_line(node: ProxyNode) -> str:
    cipher = _SS_CIPHER_MAP.get(
        node.extra.get("cipher", "aes-256-gcm"),
        node.extra.get("cipher", "aes-256-gcm"),
    )
    password = node.extra.get("password", "")
    parts = [f"ss, {node.server}, {node.port}"]
    parts.append(f"encrypt-method={cipher}")
    parts.append(f"password={password}")
    if node.extra.get("obfs"):
        parts.append(f"obfs={node.extra['obfs']}")
    if node.extra.get("obfs_host"):
        parts.append(f"obfs-host={node.extra['obfs_host']}")
    if node.extra.get("udp"):
        parts.append("udp-relay=true")
    return f"{node.name} = {', '.join(parts)}"


def _trojan_line(node: ProxyNode) -> str:
    password = node.extra.get("password", "")
    parts = [f"trojan, {node.server}, {node.port}"]
    parts.append(f"password={password}")
    parts.append("tls=true")
    if node.tls.sni:
        parts.append(f"sni={node.tls.sni}")
    if node.tls.insecure:
        parts.append("skip-cert-verify=true")
    return f"{node.name} = {', '.join(parts)}"


def _http_line(node: ProxyNode) -> str:
    proto = "https" if node.tls.enabled else "http"
    parts = [f"{proto}, {node.server}, {node.port}"]
    if node.extra.get("username"):
        parts.append(f"username={node.extra['username']}")
    if node.extra.get("password"):
        parts.append(f"password={node.extra['password']}")
    if node.tls.insecure:
        parts.append("skip-cert-verify=true")
    return f"{node.name} = {', '.join(parts)}"


def _socks5_line(node: ProxyNode) -> str:
    proto = "socks5-tls" if node.tls.enabled else "socks5"
    parts = [f"{proto}, {node.server}, {node.port}"]
    if node.extra.get("username"):
        parts.append(f"username={node.extra['username']}")
    if node.extra.get("password"):
        parts.append(f"password={node.extra['password']}")
    if node.tls.insecure:
        parts.append("skip-cert-verify=true")
    return f"{node.name} = {', '.join(parts)}"


def _vmess_line(node: ProxyNode) -> str:
    parts = [f"vmess, {node.server}, {node.port}"]
    parts.append(f"username={node.extra.get('uuid', '')}")
    cipher = node.extra.get("cipher", "auto")
    if cipher and cipher != "auto":
        parts.append(f"encrypt-method={cipher}")
    if node.transport.type == "ws":
        parts.append("ws=true")
        if node.transport.path:
            parts.append(f"ws-path={node.transport.path}")
        ws_headers: dict[str, str] = {}
        if node.transport.host:
            ws_headers["Host"] = node.transport.host
        ws_headers.update(node.transport.headers)
        if ws_headers:
            parts.append("ws-headers=" + "|".join(f"{k}:{v}" for k, v in ws_headers.items()))
    if node.tls.enabled:
        parts.append("tls=true")
        if node.tls.sni:
            parts.append(f"sni={node.tls.sni}")
        if node.tls.insecure:
            parts.append("skip-cert-verify=true")
    alter_id = node.extra.get("alter_id", 0)
    if alter_id:
        parts.append(f"alter-id={alter_id}")
    return f"{node.name} = {', '.join(parts)}"


def _node_to_surge_line(node: ProxyNode) -> str:
    """Return a Surge [Proxy] line.

    Raises UnsupportedProtocolError for protocols Surge does not support.
    """
    proto = node.protocol
    if proto == "ss":
        return _ss_line(node)
    if proto == "trojan":
        return _trojan_line(node)
    if proto == "vmess":
        return _vmess_line(node)
    if proto in {"http", "https"}:
        return _http_line(node)
    if proto == "socks5":
        return _socks5_line(node)
    raise UnsupportedProtocolError(
        code="unsupported_protocol",
        value=proto,
        suggestion=f"Surge 不支持 {proto}，该节点已跳过",
    )


# ── Group mapping layer ────────────────────────────────────────────────────


def _group_to_surge_line(
    group: dict[str, Any],
    node_names: list[str],
    group_names: set[str],
) -> str:
    name = str(group.get("name") or "")
    gtype = str(group.get("type") or "select")
    raw_members = [str(m) for m in (group.get("proxies") or []) if m is not None]

    known = set(node_names) | group_names | _BUILTIN_TARGETS
    if raw_members:
        members = [m for m in raw_members if m in known]
    else:
        members = node_names[:]

    filter_expression = str(group.get("filter") or "").strip()
    if filter_expression and not raw_members:
        pattern = re.compile(filter_expression)
        members = [member for member in members if pattern.search(member)]

    exclude_expression = str(group.get("exclude-filter") or "").strip()
    if exclude_expression and not raw_members:
        pattern = re.compile(exclude_expression)
        members = [member for member in members if not pattern.search(member)]

    if not members:
        members = ["DIRECT"]

    member_str = ", ".join(members)
    url = str(group.get("url") or "http://www.gstatic.com/generate_204")
    interval = int(group.get("interval") or 300)
    tolerance = int(group.get("tolerance") or 100)

    if gtype == "select":
        return f"{name} = select, {member_str}"
    if gtype == "url-test":
        return f"{name} = url-test, {member_str}, url={url}, interval={interval}, tolerance={tolerance}"
    if gtype == "fallback":
        return f"{name} = fallback, {member_str}, url={url}, interval={interval}"
    if gtype == "load-balance":
        return f"{name} = load-balance, {member_str}, url={url}, persistent=true"
    return f"{name} = select, {member_str}"


# ── Rule mapping layer ─────────────────────────────────────────────────────


def _rule_to_surge_line(
    rule: str,
    rule_providers: dict[str, Any],
) -> str | None:
    """Convert a Clash rule string to a Surge rule line.

    Returns None for rule types Surge does not support.
    MATCH is converted to FINAL.
    RULE-SET resolves the provider name to a URL via rule_providers.
    """
    parts = [p.strip() for p in rule.split(",")]
    if not parts or not parts[0]:
        return None

    rule_type = parts[0].upper()

    if rule_type == "MATCH":
        target = parts[1].strip() if len(parts) > 1 else "DIRECT"
        return f"FINAL,{target}"

    if len(parts) < 3:
        return None

    no_resolve = len(parts) >= 4 and parts[-1].strip().lower() == "no-resolve"
    target = parts[-2].strip() if no_resolve else parts[-1].strip()
    value = parts[1].strip()

    if rule_type == "RULE-SET":
        provider = rule_providers.get(value)
        if not isinstance(provider, dict):
            return None
        raw_url = str(provider.get("url") or "")
        if not raw_url:
            return None
        url = _resolve_mrs_url(raw_url)  # raises UnsupportedRuleTypeError for unknown MRS
        suffix = ",no-resolve" if no_resolve else ""
        return f"RULE-SET,{url},{target}{suffix}"

    if rule_type not in _SURGE_RULE_TYPES:
        return None

    suffix = ",no-resolve" if (no_resolve and rule_type in _IP_RULE_TYPES) else ""
    return f"{rule_type},{value},{target}{suffix}"


# ── [General] section ──────────────────────────────────────────────────────


def _proxy_hostnames(nodes: list[ProxyNode]) -> list[str]:
    proxy_hostnames: list[str] = []
    for node in nodes:
        server = node.server.strip()
        if not server:
            continue
        try:
            ip_address(server.strip("[]"))
        except ValueError:
            if server not in proxy_hostnames:
                proxy_hostnames.append(server)
    return proxy_hostnames


def _general_section() -> str:
    lines = [
        "[General]",
        "loglevel = notify",
        "dns-server = 223.5.5.5, 119.29.29.29",
        "proxy-test-url = http://www.apple.com/library/test/success.html",
        (
            "skip-proxy = 127.0.0.1, 192.168.0.0/16, 10.0.0.0/8, "
            "172.16.0.0/12, 100.64.0.0/10, localhost, *.local"
        ),
        "bypass-system = true",
    ]
    return "\n".join(lines)


def _host_section(nodes: list[ProxyNode]) -> str | None:
    proxy_hostnames = _proxy_hostnames(nodes)
    if not proxy_hostnames:
        return None
    lines = ["[Host]"]
    lines.extend(
        f"{hostname} = server:https://dns.alidns.com/dns-query"
        for hostname in proxy_hostnames
    )
    return "\n".join(lines)


# ── Main compiler ──────────────────────────────────────────────────────────


def build_surge_config(
    nodes: list[ProxyNode],
    proxy_groups: list[Any],
    rules: list[Any],
    rule_providers: dict[str, Any],
) -> tuple[str, list[dict]]:
    """Compile a complete Surge .conf string.

    Returns ``(conf, warnings)``. Unsupported node protocols and rule-set URLs
    are reported and skipped while compilation continues.
    """
    group_names = {
        str(g.get("name"))
        for g in proxy_groups
        if isinstance(g, dict) and g.get("name")
    }

    warnings: list[dict] = []
    proxy_lines: list[str] = []
    compiled_node_names: list[str] = []
    for node in nodes:
        try:
            proxy_lines.append(_node_to_surge_line(node))
            compiled_node_names.append(node.name)
        except UnsupportedProtocolError as exc:
            warnings.append(exc.to_dict())

    group_lines: list[str] = []
    for group in proxy_groups:
        if isinstance(group, dict) and group.get("name"):
            group_lines.append(_group_to_surge_line(group, compiled_node_names, group_names))

    providers = rule_providers if isinstance(rule_providers, dict) else {}
    rule_lines: list[str] = []
    unsupported_rule_set_urls: list[str] = []
    unsupported_rule_types: list[str] = []
    has_final = False
    for rule in (rules if isinstance(rules, list) else []):
        if not isinstance(rule, str):
            continue
        try:
            line = _rule_to_surge_line(rule, providers)
        except UnsupportedRuleTypeError as exc:
            unsupported_rule_set_urls.append(exc.value)
            continue
        if line is None:
            rule_type = rule.split(",", 1)[0].strip().upper()
            if rule_type and rule_type not in _SURGE_RULE_TYPES and rule_type != "MATCH":
                unsupported_rule_types.append(rule_type)
            continue
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
                "suggestion": "Surge 不支持这些 MRS 规则源，已跳过对应规则",
            }
        )
    if unsupported_rule_types:
        unique_types = list(dict.fromkeys(unsupported_rule_types))
        warnings.append(
            {
                "code": "unsupported_rule_types",
                "count": len(unique_types),
                "types": unique_types,
                "suggestion": "Surge 不支持这些 Mihomo 规则类型，已跳过对应规则",
            }
        )

    sections: list[str] = [_general_section()]
    host_section = _host_section(nodes)
    if host_section:
        sections.extend(["", host_section])
    sections.extend([
        "",
        "[Proxy]",
        *proxy_lines,
        "",
        "[Proxy Group]",
        *group_lines,
        "",
        "[Rule]",
        *rule_lines,
    ])
    return "\n".join(sections) + "\n", warnings
