"""Tests for the Surge .conf compiler."""
from __future__ import annotations

import pytest

from app.core.platforms.surge import (
    UnsupportedProtocolError,
    UnsupportedRuleTypeError,
    _group_to_surge_line,
    _node_to_surge_line,
    _rule_to_surge_line,
    build_surge_config,
)
from app.ir import ProxyNode, TLSConfig, TransportConfig


def _compile(*args, **kwargs) -> str:
    """Extract the conf string from build_surge_config, discarding warnings."""
    conf, _ = build_surge_config(*args, **kwargs)
    return conf


# ── Fixtures ───────────────────────────────────────────────────────────────


def _ss(name: str = "HK", server: str = "hk.example.com", port: int = 443) -> ProxyNode:
    return ProxyNode(
        name=name, protocol="ss", server=server, port=port,
        extra={"cipher": "aes-256-gcm", "password": "secret"},
    )


def _trojan(name: str = "TR") -> ProxyNode:
    return ProxyNode(
        name=name, protocol="trojan", server="tr.example.com", port=443,
        tls=TLSConfig(enabled=True, sni="tr.example.com"),
        extra={"password": "trpass"},
    )


def _http_node(name: str = "HTTP") -> ProxyNode:
    return ProxyNode(
        name=name, protocol="http", server="proxy.example.com", port=8080,
        extra={"username": "user", "password": "pass"},
    )


def _socks5_node(name: str = "SOCKS") -> ProxyNode:
    return ProxyNode(
        name=name, protocol="socks5", server="socks.example.com", port=1080,
        extra={},
    )


_PROVIDERS: dict = {
    "proxy": {
        "type": "http",
        "behavior": "domain",
        "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/proxy.txt",
    },
    "cncidr": {
        "type": "http",
        "behavior": "ipcidr",
        "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/cncidr.txt",
    },
    "reject": {
        "type": "http",
        "behavior": "domain",
        "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/reject.txt",
    },
}


# ── Node mapping ───────────────────────────────────────────────────────────


def test_ss_line_format() -> None:
    line = _node_to_surge_line(_ss())
    assert line == "HK = ss, hk.example.com, 443, encrypt-method=aes-256-gcm, password=secret"


def test_ss_chacha_cipher() -> None:
    node = ProxyNode(
        name="JP", protocol="ss", server="jp.example.com", port=8388,
        extra={"cipher": "chacha20-ietf-poly1305", "password": "p4ss"},
    )
    line = _node_to_surge_line(node)
    assert line is not None
    assert "encrypt-method=chacha20-ietf-poly1305" in line


def test_trojan_line_format() -> None:
    line = _node_to_surge_line(_trojan())
    assert line is not None
    assert "trojan" in line
    assert "tr.example.com" in line
    assert "443" in line
    assert "password=trpass" in line
    assert "tls=true" in line
    assert "sni=tr.example.com" in line


def test_trojan_skip_cert_verify() -> None:
    node = ProxyNode(
        name="TR2", protocol="trojan", server="tr2.example.com", port=443,
        tls=TLSConfig(enabled=True, insecure=True),
        extra={"password": "pass2"},
    )
    line = _node_to_surge_line(node)
    assert line is not None
    assert "skip-cert-verify=true" in line


def test_http_proxy_line() -> None:
    line = _node_to_surge_line(_http_node())
    assert line is not None
    assert "http" in line
    assert "proxy.example.com" in line
    assert "username=user" in line
    assert "password=pass" in line


def test_https_proxy_uses_https_proto() -> None:
    node = ProxyNode(
        name="HTTPS", protocol="https", server="proxy.example.com", port=443,
        tls=TLSConfig(enabled=True),
        extra={},
    )
    line = _node_to_surge_line(node)
    assert line is not None
    assert line.startswith("HTTPS = https,")


def test_socks5_line() -> None:
    line = _node_to_surge_line(_socks5_node())
    assert line is not None
    assert "socks5" in line
    assert "socks.example.com" in line


def test_vmess_basic_line() -> None:
    node = ProxyNode(
        name="VM", protocol="vmess", server="vm.example.com", port=443,
        extra={"uuid": "some-uuid", "alter_id": 0},
    )
    line = _node_to_surge_line(node)
    assert "vmess" in line
    assert "vm.example.com" in line
    assert "username=some-uuid" in line
    assert "alter-id" not in line  # 0 is default, should be omitted


def test_vmess_ws_tls_line() -> None:
    node = ProxyNode(
        name="US",
        protocol="vmess",
        server="us.example.com",
        port=443,
        tls=TLSConfig(enabled=True, sni="cdn.example.com"),
        transport=TransportConfig(type="ws", path="/path", host="cdn.example.com"),
        extra={"uuid": "test-uuid", "alter_id": 0, "cipher": "auto"},
    )
    line = _node_to_surge_line(node)
    assert "vmess" in line
    assert "username=test-uuid" in line
    assert "ws=true" in line
    assert "ws-path=/path" in line
    assert "Host:cdn.example.com" in line
    assert "tls=true" in line
    assert "sni=cdn.example.com" in line
    assert "encrypt-method" not in line  # "auto" should be omitted


def test_vmess_alter_id_nonzero() -> None:
    node = ProxyNode(
        name="VM2", protocol="vmess", server="v.example.com", port=1234,
        extra={"uuid": "u", "alter_id": 64},
    )
    line = _node_to_surge_line(node)
    assert "alter-id=64" in line


def test_vmess_skip_cert_verify() -> None:
    node = ProxyNode(
        name="VM3", protocol="vmess", server="v.example.com", port=443,
        tls=TLSConfig(enabled=True, insecure=True),
        extra={"uuid": "u", "alter_id": 0},
    )
    line = _node_to_surge_line(node)
    assert "tls=true" in line
    assert "skip-cert-verify=true" in line


def test_hysteria2_raises_unsupported_protocol() -> None:
    node = ProxyNode(
        name="HY2", protocol="hysteria2", server="hy.example.com", port=8443,
        extra={"password": "hypass"},
    )
    with pytest.raises(UnsupportedProtocolError) as exc_info:
        _node_to_surge_line(node)
    assert exc_info.value.code == "unsupported_protocol"
    assert exc_info.value.value == "hysteria2"


def test_tuic_raises_unsupported_protocol() -> None:
    node = ProxyNode(
        name="TUIC", protocol="tuic", server="t.example.com", port=443,
        extra={"uuid": "u", "password": "p"},
    )
    with pytest.raises(UnsupportedProtocolError) as exc_info:
        _node_to_surge_line(node)
    assert exc_info.value.code == "unsupported_protocol"
    assert exc_info.value.value == "tuic"


# ── Group mapping ──────────────────────────────────────────────────────────


def test_select_group() -> None:
    group = {"name": "PROXY", "type": "select", "proxies": ["HK", "TR"]}
    line = _group_to_surge_line(group, ["HK", "TR"], {"PROXY"})
    assert line == "PROXY = select, HK, TR"


def test_select_group_includes_direct() -> None:
    group = {"name": "PROXY", "type": "select", "proxies": ["HK", "DIRECT"]}
    line = _group_to_surge_line(group, ["HK"], {"PROXY"})
    assert "DIRECT" in line


def test_urltest_group_format() -> None:
    group = {
        "name": "AUTO",
        "type": "url-test",
        "proxies": ["HK", "TR"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
        "tolerance": 50,
    }
    line = _group_to_surge_line(group, ["HK", "TR"], {"AUTO"})
    assert "url-test" in line
    assert "url=http://www.gstatic.com/generate_204" in line
    assert "interval=300" in line
    assert "tolerance=50" in line


def test_fallback_group_format() -> None:
    group = {
        "name": "FB",
        "type": "fallback",
        "proxies": ["HK", "TR"],
        "url": "http://www.gstatic.com/generate_204",
        "interval": 300,
    }
    line = _group_to_surge_line(group, ["HK", "TR"], {"FB"})
    assert "fallback" in line
    assert "interval=300" in line


def test_load_balance_group_format() -> None:
    group = {"name": "LB", "type": "load-balance", "proxies": ["HK", "TR"]}
    line = _group_to_surge_line(group, ["HK", "TR"], {"LB"})
    assert "load-balance" in line
    assert "persistent=true" in line


def test_empty_proxies_injects_all_nodes() -> None:
    group = {"name": "AUTO", "type": "url-test", "proxies": []}
    line = _group_to_surge_line(group, ["HK", "TR"], {"AUTO"})
    assert "HK" in line
    assert "TR" in line


def test_use_group_filters_nodes_by_name() -> None:
    group = {
        "name": "美国自动",
        "type": "url-test",
        "use": ["Leo订阅"],
        "filter": "(?i)(美|🇺🇸|US|USA|United States|LAX|SJC|SFO)",
    }

    line = _group_to_surge_line(
        group,
        ["TW01", "JP01", "US01", "香港01", "美国02"],
        {"美国自动"},
    )

    assert line.startswith("美国自动 = url-test, US01, 美国02,")
    assert "TW01" not in line
    assert "JP01" not in line
    assert "香港01" not in line


# ── Rule mapping ───────────────────────────────────────────────────────────


def test_domain_rule() -> None:
    assert _rule_to_surge_line("DOMAIN,example.com,PROXY", {}) == "DOMAIN,example.com,PROXY"


def test_domain_suffix_rule() -> None:
    assert _rule_to_surge_line("DOMAIN-SUFFIX,example.com,PROXY", {}) == "DOMAIN-SUFFIX,example.com,PROXY"


def test_domain_keyword_rule() -> None:
    assert _rule_to_surge_line("DOMAIN-KEYWORD,google,PROXY", {}) == "DOMAIN-KEYWORD,google,PROXY"


def test_ip_cidr_no_resolve() -> None:
    line = _rule_to_surge_line("IP-CIDR,192.168.0.0/16,DIRECT,no-resolve", {})
    assert line == "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve"


def test_ip_cidr6_no_resolve() -> None:
    line = _rule_to_surge_line("IP-CIDR6,::1/128,DIRECT,no-resolve", {})
    assert line == "IP-CIDR6,::1/128,DIRECT,no-resolve"


def test_geoip_no_resolve() -> None:
    line = _rule_to_surge_line("GEOIP,CN,DIRECT,no-resolve", {})
    assert line == "GEOIP,CN,DIRECT,no-resolve"


def test_geoip_lan() -> None:
    line = _rule_to_surge_line("GEOIP,LAN,DIRECT,no-resolve", {})
    assert line == "GEOIP,LAN,DIRECT,no-resolve"


def test_match_converts_to_final() -> None:
    assert _rule_to_surge_line("MATCH,PROXY", {}) == "FINAL,PROXY"


def test_match_default_direct() -> None:
    assert _rule_to_surge_line("MATCH", {}) == "FINAL,DIRECT"


def test_rule_set_resolves_url() -> None:
    line = _rule_to_surge_line("RULE-SET,proxy,PROXY", _PROVIDERS)
    assert line == (
        "RULE-SET,"
        "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/proxy.txt,"
        "PROXY"
    )


def test_rule_set_no_resolve_preserved() -> None:
    line = _rule_to_surge_line("RULE-SET,cncidr,DIRECT,no-resolve", _PROVIDERS)
    assert line is not None
    assert line.endswith(",no-resolve")
    assert "cncidr.txt" in line


def test_rule_set_unknown_provider_returns_none() -> None:
    assert _rule_to_surge_line("RULE-SET,nonexistent,PROXY", _PROVIDERS) is None


def test_unsupported_rule_type_returns_none() -> None:
    assert _rule_to_surge_line("SCRIPT,somescript,DIRECT", {}) is None


# ── build_surge_config integration ────────────────────────────────────────


def test_config_contains_four_sections() -> None:
    result = _compile(
        [_ss(), _trojan()],
        [{"name": "PROXY", "type": "select", "proxies": []}],
        ["DOMAIN-SUFFIX,example.com,PROXY", "MATCH,PROXY"],
        {},
    )
    assert "[General]" in result
    assert "[Proxy]" in result
    assert "[Proxy Group]" in result
    assert "[Rule]" in result


def test_config_nodes_present() -> None:
    result = _compile([_ss(), _trojan()], [], [], {})
    assert "HK = ss" in result
    assert "TR = trojan" in result


def test_config_match_becomes_final() -> None:
    result = _compile([], [], ["MATCH,PROXY"], {})
    assert "FINAL,PROXY" in result


def test_config_adds_final_if_missing() -> None:
    result = _compile([], [], ["DOMAIN,example.com,PROXY"], {})
    assert "FINAL," in result


def test_config_rule_set_resolves() -> None:
    rules = ["RULE-SET,reject,REJECT", "MATCH,PROXY"]
    result = _compile([], [], rules, _PROVIDERS)
    assert "reject.txt" in result
    assert "FINAL,PROXY" in result


def test_config_unsupported_protocol_skipped_with_warning() -> None:
    tuic = ProxyNode(
        name="TUIC", protocol="tuic", server="t.example.com", port=443,
        extra={"uuid": "u", "password": "p"},
    )
    conf, warnings = build_surge_config([_ss(), tuic], [], [], {})
    assert "HK = ss" in conf
    assert "TUIC" not in conf
    assert len(warnings) == 1
    assert warnings[0]["code"] == "unsupported_protocol"
    assert warnings[0]["value"] == "tuic"


def test_config_vmess_node_compiled() -> None:
    vmess = ProxyNode(
        name="VM", protocol="vmess", server="vm.example.com", port=443,
        extra={"uuid": "some-uuid", "alter_id": 0},
    )
    result = _compile([_ss(), vmess], [], [], {})
    assert "HK = ss" in result
    assert "VM = vmess" in result


def test_general_section_fields() -> None:
    result = _compile([], [], [], {})
    assert "loglevel" in result
    assert "dns-server = 223.5.5.5, 119.29.29.29" in result
    assert "skip-proxy" in result
    assert "bypass-system" in result
    assert "proxy-test-url = http://www.apple.com/library/test/success.html" in result


def test_host_section_assigns_proxy_hostnames_to_real_dns() -> None:
    result = _compile(
        [_ss(), _ss(name="HK-2"), _trojan(), _ss(name="IP", server="203.0.113.8")],
        [],
        [],
        {},
    )

    assert "[Host]" in result
    assert result.count("hk.example.com = server:https://dns.alidns.com/dns-query") == 1
    assert "tr.example.com = server:https://dns.alidns.com/dns-query" in result
    assert "203.0.113.8 = server:" not in result


# ── MRS URL substitution ───────────────────────────────────────────────────

_HENRYCHIAO_BASE = (
    "https://raw.githubusercontent.com/HenryChiao/mihomo_yamls"
    "/refs/heads/ruleset/meta"
)

_HENRYCHIAO_MRS_PROVIDERS: dict = {
    "ai": {
        "type": "http",
        "behavior": "domain",
        "url": f"{_HENRYCHIAO_BASE}/domain/ai.mrs",
    },
    "cn-ipcidr": {
        "type": "http",
        "behavior": "ipcidr",
        "url": f"{_HENRYCHIAO_BASE}/ipcidr/cn.mrs",
    },
}

_UNKNOWN_MRS_PROVIDERS: dict = {
    "custom": {
        "type": "http",
        "behavior": "domain",
        "url": "https://example.com/rules/custom.mrs",
    }
}


def test_mrs_henrychiao_domain_substituted() -> None:
    line = _rule_to_surge_line("RULE-SET,ai,PROXY", _HENRYCHIAO_MRS_PROVIDERS)
    assert line is not None
    assert ".mrs" not in line
    assert ".txt" in line
    assert line.endswith(",PROXY")
    assert "domain/ai.txt" in line


def test_mrs_henrychiao_ipcidr_substituted() -> None:
    line = _rule_to_surge_line("RULE-SET,cn-ipcidr,DIRECT,no-resolve", _HENRYCHIAO_MRS_PROVIDERS)
    assert line is not None
    assert ".mrs" not in line
    assert ".txt" in line
    assert line.endswith(",no-resolve")
    assert "ipcidr/cn.txt" in line


def test_mrs_unknown_provider_raises_error() -> None:
    with pytest.raises(UnsupportedRuleTypeError) as exc_info:
        _rule_to_surge_line("RULE-SET,custom,PROXY", _UNKNOWN_MRS_PROVIDERS)
    err = exc_info.value
    assert err.code == "unsupported_rule_type"
    assert err.field == "rule_set_url"
    assert "custom.mrs" in err.value
    assert err.suggestion


def test_mrs_error_to_dict() -> None:
    err = UnsupportedRuleTypeError(
        code="unsupported_rule_type",
        field="rule_set_url",
        value="https://example.com/x.mrs",
        suggestion="hint",
    )
    d = err.to_dict()
    assert d["code"] == "unsupported_rule_type"
    assert d["field"] == "rule_set_url"
    assert d["value"] == "https://example.com/x.mrs"
    assert d["suggestion"] == "hint"


def test_build_surge_config_skips_unknown_mrs() -> None:
    result, warnings = build_surge_config(
        [], [], ["RULE-SET,custom,DIRECT"], _UNKNOWN_MRS_PROVIDERS
    )
    assert "custom.mrs" not in result
    assert "FINAL," in result
    assert warnings == [
        {
            "code": "unsupported_rule_sets",
            "count": 1,
            "examples": ["https://example.com/rules/custom.mrs"],
            "suggestion": "Surge 不支持这些 MRS 规则源，已跳过对应规则",
        }
    ]


def test_build_surge_config_skips_domain_regex_and_reports_rule_type() -> None:
    conf, warnings = build_surge_config(
        [],
        [],
        [
            r"DOMAIN-REGEX,^dl-[A-Za-z0-9-]+\.mypikpak\.com$,DIRECT",
            "MATCH,DIRECT",
        ],
        {},
    )

    assert "DOMAIN-REGEX" not in conf
    assert "FINAL,DIRECT" in conf
    assert warnings == [
        {
            "code": "unsupported_rule_types",
            "count": 1,
            "types": ["DOMAIN-REGEX"],
            "suggestion": "Surge 不支持这些 Mihomo 规则类型，已跳过对应规则",
        }
    ]


def test_build_surge_config_substitutes_known_mrs() -> None:
    result = _compile(
        [],
        [],
        ["RULE-SET,ai,PROXY", "MATCH,PROXY"],
        _HENRYCHIAO_MRS_PROVIDERS,
    )
    assert ".mrs" not in result
    assert "domain/ai.txt" in result
    assert "FINAL,PROXY" in result
