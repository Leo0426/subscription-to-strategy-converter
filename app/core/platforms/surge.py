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
import json
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any

from app.ir import ProxyNode
from app.core.parsers.clash import ir_to_clash_dict
from app.core.platforms.surge_profile import replace_surge_routing
from app.core.platforms.surge_capabilities import SURGE_IOS_RULE_TYPES
from app.core.platforms.surge_audit import (
    audit_native_surge_profile,
    tls_verification_warning,
)
from app.core.platforms.ini import (
    IniDialect, UnsupportedNodeOptionError, UnsupportedProtocolError, UnsupportedRuleTypeError, build_ini_config,
    incompatible_node_names,
)


# ── MRS → text-format URL substitution ────────────────────────────────────


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


# ── blackmatrix7 Clash YAML → Surge .list substitution ─────────────────────
# Surge cannot parse Clash provider YAML (files that begin with `payload:`);
# feeding it one yields "Invalid line: payload:". The blackmatrix7/ios_rule_script
# repo ships a native Surge `.list` for every rule set, so a Clash YAML URL is
# rewritten to its Surge counterpart. The Surge filename drops the Clash-only
# `_Classical` / `_No_Resolve` name segments — Surge `.list` files are already
# classical text format and carry `no-resolve` inline on their IP rules.
# Host-agnostic so mirror/proxy fronts of the same repo are handled too.
_BLACKMATRIX7_CANONICAL_CLASH_YAML = re.compile(
    r"^https://(?:raw\.githubusercontent\.com/blackmatrix7/ios_rule_script/"
    r"(?P<raw_ref>[^/]+)|cdn\.jsdelivr\.net/gh/blackmatrix7/ios_rule_script@"
    r"(?P<cdn_ref>[^/]+))/rule/Clash/"
    r"(?P<category>[^/]+)/(?P<name>[^/]+)\.yaml$"
)
_BLACKMATRIX7_CLASH_YAML = re.compile(
    r"^(?P<prefix>.*/blackmatrix7/ios_rule_script/[^/]+)/rule/Clash/"
    r"(?P<category>[^/]+)/(?P<name>[^/]+)\.yaml$"
)


_BLACKMATRIX7_COMPLETE_SURGE_VARIANTS = {
    # These mappings are pinned-revision audited. Other categories use their
    # base list only when the Clash source is not a `_Classical` variant.
    ("Apple", "Apple_Classical_No_Resolve"): "Apple_All_No_Resolve",
    ("Global", "Global_Classical_No_Resolve"): "Global_All_No_Resolve",
    ("Global", "Global_Classical"): "Global_All",
}


def _blackmatrix7_surge_list_name(category: str, clash_name: str) -> str | None:
    audited_variant = _BLACKMATRIX7_COMPLETE_SURGE_VARIANTS.get((category, clash_name))
    if audited_variant is not None:
        return audited_variant
    if "_Classical" in clash_name:
        return None
    return clash_name.removesuffix("_No_Resolve")


def _resolve_blackmatrix7_url(url: str) -> str | None:
    """Return the Surge `.list` URL for a blackmatrix7 Clash YAML URL.

    Returns None when the URL is not a blackmatrix7 Clash YAML rule set.
    """
    canonical = _BLACKMATRIX7_CANONICAL_CLASH_YAML.match(url)
    if canonical is not None:
        ref = canonical["raw_ref"] or canonical["cdn_ref"]
        name = _blackmatrix7_surge_list_name(canonical["category"], canonical["name"])
        if name is None:
            return None
        return (
            "https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@"
            f"{ref}/rule/Surge/{canonical['category']}/{name}.list"
        )

    match = _BLACKMATRIX7_CLASH_YAML.match(url)
    if match is None:
        return None
    name = _blackmatrix7_surge_list_name(match["category"], match["name"])
    if name is None:
        return None
    return f"{match['prefix']}/rule/Surge/{match['category']}/{name}.list"


# ── skk.moe (Sukka) Clash → Surge-native substitution ──────────────────────
# Sukka publishes the same rule sets in both Clash and Surge form:
#   /Clash/domainset/<n>.txt  (bare domains)      → /List/domainset/<n>.conf  (DOMAIN-SET)
#   /Clash/non_ip/<n>.txt     (DOMAIN/-SUFFIX,…)  → /List/non_ip/<n>.conf     (RULE-SET)
#   /Clash/ip/<n>.txt         (IP-CIDR,…)         → /List/ip/<n>.conf         (RULE-SET)
# The Clash `.txt` variants are NOT Surge-loadable (bare domains fail RULE-SET
# parsing); the `/List/*.conf` variants are the native Surge format.
_SKK_CLASH_PATTERN = re.compile(
    r"^(?P<scheme>https?://ruleset\.skk\.moe)/Clash/"
    r"(?P<kind>domainset|non_ip|ip)/(?P<name>[^/]+)\.txt$"
)


@dataclass
class ResolvedRuleSet:
    """A Surge external rule set: the directive keyword plus its URL."""
    directive: str  # "RULE-SET" or "DOMAIN-SET"
    url: str


def _resolve_skk_ruleset(url: str) -> ResolvedRuleSet | None:
    """Return the Surge-native rule set for a skk.moe Clash URL, else None."""
    match = _SKK_CLASH_PATTERN.match(url)
    if match is None:
        return None
    surge_url = f"{match['scheme']}/List/{match['kind']}/{match['name']}.conf"
    directive = "DOMAIN-SET" if match["kind"] == "domainset" else "RULE-SET"
    return ResolvedRuleSet(directive, surge_url)


def _resolve_surge_ruleset(url: str, behavior: str) -> ResolvedRuleSet:
    """Resolve a Clash rule-provider URL to a Surge-loadable rule set.

    Rewrites URLs from repositories that publish a Surge-native variant
    (blackmatrix7, skk.moe). Everything else is emitted as a best-effort
    RULE-SET when it is plausibly classical text, and raised as
    UnsupportedRuleTypeError (→ skipped + warned) when the format is one Surge
    cannot parse by URL: Clash `payload:` YAML, MRS with no text equivalent, or
    Clash `domain`/`ipcidr` provider bare-lists (`+.`/wildcards/bare CIDR).
    """
    surge_list = _resolve_blackmatrix7_url(url)
    if surge_list is not None:
        # blackmatrix7 `.list` files are classical Surge rules.
        return ResolvedRuleSet("RULE-SET", surge_list)

    skk = _resolve_skk_ruleset(url)
    if skk is not None:
        return skk

    if url.endswith(".yaml"):
        raise UnsupportedRuleTypeError(
            code="unsupported_rule_type",
            field="rule_set_url",
            value=url,
            suggestion="Surge 无法解析 Clash payload YAML 规则集，请替换为 Surge 原生规则源",
        )

    if url.endswith(".mrs"):
        # _resolve_mrs_url substitutes known MRS repos or raises for the rest.
        return ResolvedRuleSet("RULE-SET", _resolve_mrs_url(url))

    if behavior in {"domain", "ipcidr"}:
        # Clash domain/ipcidr providers ship bare-domain (`+.`/wildcard) or
        # bare-CIDR lists that Surge cannot consume in any external ruleset form.
        raise UnsupportedRuleTypeError(
            code="unsupported_rule_type",
            field="rule_set_url",
            value=url,
            suggestion="Surge 无法解析 Clash domain/ipcidr 裸列表，请替换为 Surge 原生规则源",
        )

    # Classical (or unspecified) providers are plain-text Surge rules.
    return ResolvedRuleSet("RULE-SET", url)


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

# These rule types support the optional no-resolve flag in Surge
_IP_RULE_TYPES: frozenset[str] = frozenset({"IP-CIDR", "IP-CIDR6", "GEOIP"})

_BUILTIN_TARGETS: frozenset[str] = frozenset({"DIRECT", "REJECT", "REJECT-DROP"})


# ── Node mapping layer ─────────────────────────────────────────────────────

# Surge owns session pooling and its TLS client fingerprint. These options
# cannot be mapped, but do not change the node's endpoint/authentication.
_ANYTLS_TUNING = frozenset({
    "client-fingerprint", "client-metadata", "idle-session-check-interval",
    "idle-session-timeout", "min-idle-session",
})
_ANYTLS_MAPPED = frozenset({
    "name", "type", "server", "port", "password", "tls", "sni", "skip-cert-verify",
    "alpn", "fingerprint", "name-cert-verify", "disable-reuse", "udp",
})


def _anytls_line(node: ProxyNode) -> str:
    proxy = ir_to_clash_dict(node)
    unsupported = set(proxy) - _ANYTLS_MAPPED - _ANYTLS_TUNING
    if not proxy.get("password"):
        unsupported.add("password")
    for field in ("disable-reuse", "skip-cert-verify", "udp"):
        if field in proxy and not isinstance(proxy[field], bool):
            unsupported.add(field)
    if not 1 <= node.port <= 65535:
        unsupported.add("port")
    for field, value in (("name", node.name), ("server", node.server)):
        if not value or any(char in value for char in ',=\r\n"'):
            unsupported.add(field)
    if node.name.lstrip().startswith(("#", ";")):
        unsupported.add("name")
    for field in ("password", "sni", "name-cert-verify", "fingerprint"):
        if any(ord(char) < 32 for char in str(proxy.get(field, ""))):
            unsupported.add(field)
    alpn = proxy.get("alpn", [])
    if not isinstance(alpn, list) or any(
        not isinstance(value, str) or not value or "," in value or any(ord(c) < 32 for c in value)
        for value in alpn
    ):
        unsupported.add("alpn")
    fingerprint = str(proxy.get("fingerprint") or "").replace(":", "")
    if fingerprint and not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint):
        unsupported.add("fingerprint")
    if unsupported:
        fields = sorted(field if re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", field) else "unknown" for field in unsupported)
        raise UnsupportedNodeOptionError(node.name, fields)

    # Quote strings so commas, quotes and whitespace in credentials survive INI.
    parts = [f"anytls, {node.server}, {node.port}",
             "password=" + json.dumps(str(proxy["password"]), ensure_ascii=False)]
    for source, target in (("sni", "sni"), ("name-cert-verify", "server-cert-verify-name")):
        if proxy.get(source):
            parts.append(target + "=" + json.dumps(str(proxy[source]), ensure_ascii=False))
    if proxy.get("skip-cert-verify"):
        parts.append("skip-cert-verify=true")
    if alpn:
        parts.append("alpn=" + json.dumps(",".join(alpn), ensure_ascii=False))
    if fingerprint:
        parts.append("server-cert-fingerprint-sha256=" + fingerprint)
    if "disable-reuse" in proxy:
        parts.append("reuse=" + ("false" if proxy["disable-reuse"] else "true"))
    return f"{node.name} = {', '.join(parts)}"


def _anytls_warnings(nodes: list[ProxyNode]) -> list[dict]:
    warnings: list[dict] = []
    minimum = ("5.17.0", "6.4.3")
    for node in nodes:
        proxy = ir_to_clash_dict(node)
        ignored = set(proxy) & _ANYTLS_TUNING
        if proxy.get("udp") is False:
            ignored.add("udp")  # Surge AnyTLS always offers UDP-over-TCP relay.
        if ignored:
            warnings.append({"code": "ignored_node_options", "node": node.name,
                             "fields": sorted(ignored),
                             "suggestion": "Surge AnyTLS 使用客户端自己的 TLS 指纹、会话池和 UDP 行为；列出的选项未转换，Mihomo 输出仍保留原值"})
        if proxy.get("alpn") and minimum < ("5.20.0", "6.7.0"):
            minimum = ("5.20.0", "6.7.0")
        if proxy.get("name-cert-verify"):
            minimum = ("5.21.0", "6.8.0")
    if nodes:
        warnings.append({"code": "client_version_requirement", "value": "anytls",
                         "minimum_versions": {"ios": minimum[0], "mac": minimum[1]},
                         "suggestion": f"本次 AnyTLS 及 TLS 参数要求 Surge iOS {minimum[0]}+ / Mac {minimum[1]}+；未检测实际客户端版本"})
    return warnings


def _ss_line(node: ProxyNode) -> str:
    cipher = _SS_CIPHER_MAP.get(
        node.extra.get("cipher", "aes-256-gcm"),
        node.extra.get("cipher", "aes-256-gcm"),
    )
    password = node.extra.get("password", "")
    parts = [f"ss, {node.server}, {node.port}"]
    parts.append(f"encrypt-method={cipher}")
    parts.append(f"password={password}")
    obfs = str(node.extra.get("obfs") or "")
    obfs_host = str(node.extra.get("obfs_host") or "")
    plugin = str(node.extra.get("plugin") or "").lower()
    plugin_opts = node.extra.get("plugin_opts")
    if not obfs and plugin in {"obfs", "simple-obfs"} and isinstance(plugin_opts, dict):
        obfs = str(plugin_opts.get("mode") or "")
        obfs_host = str(plugin_opts.get("host") or "")
    if obfs:
        parts.append(f"obfs={obfs}")
    if obfs_host:
        parts.append(f"obfs-host={obfs_host}")
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

    Raises a compatibility error for protocols/options this exporter cannot emit.
    """
    proto = node.protocol
    if proto == "ss":
        return _ss_line(node)
    if proto == "trojan":
        return _trojan_line(node)
    if proto == "anytls":
        return _anytls_line(node)
    if proto == "vmess":
        return _vmess_line(node)
    if proto in {"http", "https"}:
        return _http_line(node)
    if proto == "socks5":
        return _socks5_line(node)
    raise UnsupportedProtocolError(
        code="unsupported_protocol",
        value=proto,
        suggestion=f"当前转换器尚未支持 {proto} 的 Surge 输出，该节点已跳过",
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
    # Surge 5.21+ uses General's proxy-test-url; group-level `url=` is ignored.
    # Keep only scheduling and switching parameters on the group itself.
    interval = int(group.get("interval") or 300)
    tolerance = int(group.get("tolerance") or 100)

    if gtype == "select":
        return f"{name} = select, {member_str}"
    if gtype == "url-test":
        return f"{name} = url-test, {member_str}, interval={interval}, tolerance={tolerance}"
    if gtype == "fallback":
        return f"{name} = fallback, {member_str}, interval={interval}"
    if gtype == "load-balance":
        return f"{name} = load-balance, {member_str}, persistent=true"
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
        behavior = str(provider.get("behavior") or "").strip().lower()
        # raises UnsupportedRuleTypeError for formats Surge cannot parse
        resolved = _resolve_surge_ruleset(raw_url, behavior)
        # no-resolve only applies to IP matching; DOMAIN-SET has no IP rules.
        suffix = ",no-resolve" if (no_resolve and resolved.directive == "RULE-SET") else ""
        return f"{resolved.directive},{resolved.url},{target}{suffix}"

    if rule_type == "DST-PORT":
        # Clash's DST-PORT is Surge's DEST-PORT. Emit only the port forms Surge
        # accepts natively — a single port or a hyphen range; other Clash-only
        # forms (e.g. slash-joined "3478/19302") are dropped rather than passed
        # through to become a Surge "Invalid line" load failure.
        if re.fullmatch(r"\d+(?:-\d+)?", value):
            return f"DEST-PORT,{value},{target}"
        return None

    if rule_type not in SURGE_IOS_RULE_TYPES:
        return None

    suffix = ",no-resolve" if (no_resolve and rule_type in _IP_RULE_TYPES) else ""
    return f"{rule_type},{value},{target}{suffix}"


# ── [General] section ──────────────────────────────────────────────────────


_GENERAL_DNS_SERVERS = ("223.5.5.5", "119.29.29.29")


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
        f"dns-server = {', '.join(_GENERAL_DNS_SERVERS)}",
        "proxy-test-url = http://www.apple.com/library/test/success.html",
        "test-timeout = 3",
        (
            "skip-proxy = 127.0.0.1, 192.168.0.0/16, 10.0.0.0/8, "
            "172.16.0.0/12, 100.64.0.0/10, localhost, *.local"
        ),
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


def _node_dns_warning(dns_config: dict[str, Any] | None) -> dict | None:
    """Report node-only resolver semantics without widening their DNS scope.

    Surge's documented proxy-server lookup bypasses [Host] entries, so copying
    resolver URLs there cannot promise node DNS preservation. Putting them in
    [General] would instead change resolution for all traffic domains.
    https://manual.nssurge.com/dns/local-dns-mapping.html
    """
    if not isinstance(dns_config, dict):
        return None
    fields: list[str] = []
    nameservers = dns_config.get("proxy-server-nameserver")
    if isinstance(nameservers, str):
        nameservers = [nameservers]
    if nameservers:
        matches_general = (
            isinstance(nameservers, list)
            and all(isinstance(value, str) for value in nameservers)
            and {value.strip() for value in nameservers} == set(_GENERAL_DNS_SERVERS)
        )
        if not matches_general:
            fields.append("proxy-server-nameserver")
    if dns_config.get("proxy-server-nameserver-policy"):
        fields.append("proxy-server-nameserver-policy")
    if not fields:
        return None
    # Resolver URLs may carry subscriber tokens; never include their values or
    # policy domains in diagnostics, which are also sent in response headers.
    return {
        "code": "unsupported_node_dns",
        "fields": fields,
        "suggestion": (
            "Surge 无法等价保留这些节点专用 DNS 设置；代理服务器域名不使用 "
            "[Host] 的 server: 映射。已保留当前通用 DNS，请在客户端核实节点解析"
        ),
    }


# ── Main compiler ──────────────────────────────────────────────────────────


def build_surge_config(
    nodes: list[ProxyNode],
    proxy_groups: list[Any],
    rules: list[Any],
    rule_providers: dict[str, Any],
    *,
    dns_config: dict[str, Any] | None = None,
    source_profile: str | None = None,
) -> tuple[str, list[dict]]:
    """Compile a complete Surge .conf string.

    Returns ``(conf, warnings)``. Unsupported node protocols and rule-set URLs
    are reported and skipped while compilation continues. Node-only DNS
    settings that cannot be preserved are reported without changing their scope.
    A native source owns all non-routing sections, including the proxy entries.
    """
    conf, warnings = build_ini_config(
        nodes, proxy_groups, rules, rule_providers,
        dialect=IniDialect(
            name="Surge",
            node=None if source_profile is not None else _node_to_surge_line,
            group=_group_to_surge_line,
            rule=_rule_to_surge_line,
            rule_types=SURGE_IOS_RULE_TYPES,
            general="" if source_profile is not None else _general_section(),
            host=(lambda _nodes: None) if source_profile is not None else _host_section,
        ),
    )
    skipped_nodes = incompatible_node_names(nodes, warnings)
    emitted_nodes = [node for node in nodes if node.name not in skipped_nodes]
    warnings.extend(_anytls_warnings([node for node in emitted_nodes if node.protocol == "anytls"]))
    tls_warning = tls_verification_warning(emitted_nodes)
    if tls_warning is not None:
        warnings.append(tls_warning)
    if source_profile is not None:
        warnings.extend(audit_native_surge_profile(source_profile))
        return replace_surge_routing(source_profile, conf), warnings
    if _proxy_hostnames(emitted_nodes):
        dns_warning = _node_dns_warning(dns_config)
        if dns_warning is not None:
            warnings.append(dns_warning)
    return conf, warnings
