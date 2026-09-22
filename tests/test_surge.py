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
from app.core.platforms.surge_audit import tls_verification_warning
from app.core.template_engine import LEO_TEMPLATE_ID, apply_template, load_template
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


def test_ss_plugin_obfs_maps_to_surge_obfuscation() -> None:
    node = ProxyNode(
        name="TW01",
        protocol="ss",
        server="tw.example.com",
        port=8801,
        extra={
            "cipher": "chacha20-ietf",
            "password": "secret",
            "plugin": "obfs",
            "plugin_opts": {
                "mode": "http",
                "host": "download.microsoft.com",
            },
        },
    )

    line = _node_to_surge_line(node)

    assert "obfs=http" in line
    assert "obfs-host=download.microsoft.com" in line


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


def test_insecure_tls_nodes_are_aggregated_without_node_details() -> None:
    nodes = [
        ProxyNode(
            name=f"TLS-{index}",
            protocol="anytls",
            server=f"tls-{index}.example.com",
            port=443,
            tls=TLSConfig(enabled=True, insecure=index < 2),
            extra={"password": f"secret-{index}"},
        )
        for index in range(3)
    ]

    warning = tls_verification_warning(nodes)

    assert warning == {
        "code": "insecure_tls_nodes",
        "count": 2,
        "suggestion": "2 个 TLS 节点关闭了服务器证书验证；仅在机场要求时保留，并确认节点来源可信",
    }


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
    assert "url=" not in line
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
    assert "url=" not in line
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


def test_rule_set_domain_bare_list_unsupported() -> None:
    # Loyalsoldier clash-rules proxy.txt is a bare-domain list Surge cannot parse.
    with pytest.raises(UnsupportedRuleTypeError):
        _rule_to_surge_line("RULE-SET,proxy,PROXY", _PROVIDERS)


def test_rule_set_ipcidr_bare_list_unsupported() -> None:
    with pytest.raises(UnsupportedRuleTypeError):
        _rule_to_surge_line("RULE-SET,cncidr,DIRECT,no-resolve", _PROVIDERS)


def test_rule_set_unknown_provider_returns_none() -> None:
    assert _rule_to_surge_line("RULE-SET,nonexistent,PROXY", _PROVIDERS) is None


def test_unsupported_rule_type_returns_none() -> None:
    assert _rule_to_surge_line("SCRIPT,somescript,DIRECT", {}) is None


def test_dst_port_single_maps_to_dest_port() -> None:
    # Clash DST-PORT is Surge's DEST-PORT keyword.
    assert _rule_to_surge_line("DST-PORT,19302,DIRECT", {}) == "DEST-PORT,19302,DIRECT"


def test_dst_port_range_maps_to_dest_port() -> None:
    assert _rule_to_surge_line("DST-PORT,10000-65535,默认代理", {}) == "DEST-PORT,10000-65535,默认代理"


def test_dst_port_slash_multiport_dropped() -> None:
    # Slash-joined multi-port is Clash-only; passing it through would make Surge
    # reject the whole ruleset with "Invalid line", so it is dropped instead.
    assert _rule_to_surge_line("DST-PORT,3478/19302,DIRECT", {}) is None


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


def test_config_skips_clash_bare_list_ruleset() -> None:
    # reject.txt is a Clash bare-domain list; it must be skipped (not emitted)
    # and surface as an unsupported_rule_sets warning, while the config still
    # compiles with a FINAL rule.
    rules = ["RULE-SET,reject,REJECT", "MATCH,PROXY"]
    result, warnings = build_surge_config([], [], rules, _PROVIDERS)
    assert "reject.txt" not in result
    assert "FINAL,PROXY" in result
    assert any(w["code"] == "unsupported_rule_sets" for w in warnings)


def test_config_unsupported_protocol_skipped_with_warning() -> None:
    tuic = ProxyNode(
        name="TUIC", protocol="tuic", server="t.example.com", port=443,
        extra={"uuid": "u", "password": "p"},
    )
    conf, warnings = build_surge_config([_ss(), tuic], [], [], {})
    assert "HK = ss" in conf
    assert "TUIC" not in conf
    assert "t.example.com =" not in conf
    assert len(warnings) == 1
    assert warnings[0]["code"] == "unsupported_protocol"
    assert warnings[0]["value"] == "tuic"


def test_rules_targeting_a_protocol_emptied_group_fail_closed() -> None:
    tuic = ProxyNode(
        name="US-TUIC",
        protocol="tuic",
        server="tuic.example.com",
        port=443,
        extra={"uuid": "u", "password": "p"},
    )
    conf, warnings = build_surge_config(
        [tuic],
        [{"name": "OnlyUS", "type": "select", "proxies": ["US-TUIC"]}],
        ["DOMAIN,example.com,OnlyUS", "MATCH,OnlyUS"],
        {},
    )

    assert "OnlyUS =" not in conf
    assert "DOMAIN,example.com,REJECT" in conf
    assert "FINAL,REJECT" in conf
    assert any(warning["code"] == "unavailable_proxy_groups" for warning in warnings)


def test_exclude_only_dynamic_group_cannot_fall_through_to_direct() -> None:
    conf, warnings = build_surge_config(
        [_ss()],
        [{"name": "Excluded", "type": "url-test", "exclude-filter": ".*"}],
        ["MATCH,Excluded"],
        {},
    )

    assert "Excluded =" not in conf
    assert "FINAL,REJECT" in conf
    assert any(warning["code"] == "unavailable_proxy_groups" for warning in warnings)


def test_rules_targeting_an_unsupported_node_fail_closed() -> None:
    tuic = ProxyNode(
        name="US-TUIC",
        protocol="tuic",
        server="tuic.example.com",
        port=443,
        extra={"uuid": "u", "password": "p"},
    )
    conf, _ = build_surge_config(
        [tuic],
        [],
        ["DOMAIN,example.com,US-TUIC", "MATCH,US-TUIC"],
        {},
    )

    assert "DOMAIN,example.com,REJECT" in conf
    assert "FINAL,REJECT" in conf


def test_unsupported_node_name_cannot_shadow_a_builtin_target() -> None:
    tuic = ProxyNode(
        name="DIRECT",
        protocol="tuic",
        server="tuic.example.com",
        port=443,
        extra={"uuid": "u", "password": "p"},
    )

    conf, _ = build_surge_config([tuic], [], ["MATCH,DIRECT"], {})

    assert "FINAL,DIRECT" in conf
    assert "FINAL,REJECT" not in conf


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
    assert "bypass-system" not in result
    assert "proxy-test-url = http://www.apple.com/library/test/success.html" in result
    assert "test-timeout = 3" in result


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
            "suggestion": "Surge 不支持这些规则源（MRS / Clash payload YAML / domain·ipcidr 裸列表），已跳过对应规则",
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
            "rule_count": 1,
            "types": ["DOMAIN-REGEX"],
            "suggestion": "Surge 不支持这些 Mihomo 规则类型，已跳过对应规则",
        }
    ]


def test_process_name_is_dropped_for_surge_ios_and_counted() -> None:
    conf, warnings = build_surge_config(
        [],
        [],
        [
            "PROCESS-NAME,com.ss.android.ugc.aweme,默认代理",
            "PROCESS-NAME,com.xingin.xhs,社交通讯",
            "PROCESS-NAME,tv.danmaku.bili,流媒体",
            "MATCH,DIRECT",
        ],
        {},
    )

    assert "PROCESS-NAME" not in conf
    assert conf.count("FINAL,") == 1
    assert warnings == [
        {
            "code": "unsupported_rule_types",
            "count": 1,
            "rule_count": 3,
            "types": ["PROCESS-NAME"],
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


# ── blackmatrix7 Clash YAML → Surge .list substitution ─────────────────────

_B7_REF = "8818705adee20571a856daf11c9fc69c4929109a"
_B7_RAW_BASE = (
    "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script"
    f"/{_B7_REF}"
)
_B7_CDN_BASE = f"https://cdn.jsdelivr.net/gh/blackmatrix7/ios_rule_script@{_B7_REF}"

_B7_PROVIDERS: dict = {
    "tencent": {"type": "http", "url": f"{_B7_RAW_BASE}/rule/Clash/Tencent/Tencent_No_Resolve.yaml"},
    "global": {"type": "http", "url": f"{_B7_RAW_BASE}/rule/Clash/Global/Global_Classical_No_Resolve.yaml"},
    "global-resolve": {
        "type": "http",
        "url": f"{_B7_RAW_BASE}/rule/Clash/Global/Global_Classical.yaml",
    },
    "netflix-classical": {
        "type": "http",
        "url": f"{_B7_RAW_BASE}/rule/Clash/Netflix/Netflix_Classical.yaml",
    },
    "teams": {"type": "http", "url": f"{_B7_RAW_BASE}/rule/Clash/Teams/Teams.yaml"},
}


def test_b7_clash_yaml_no_resolve_rewritten_to_surge_list() -> None:
    line = _rule_to_surge_line("RULE-SET,tencent,DIRECT", _B7_PROVIDERS)
    assert line == f"RULE-SET,{_B7_CDN_BASE}/rule/Surge/Tencent/Tencent.list,DIRECT"
    assert ".yaml" not in line
    assert "/rule/Clash/" not in line


def test_b7_classical_no_resolve_maps_to_complete_surge_variant() -> None:
    line = _rule_to_surge_line("RULE-SET,global,Proxy", _B7_PROVIDERS)
    assert line == (
        f"RULE-SET,{_B7_CDN_BASE}/rule/Surge/Global/Global_All_No_Resolve.list,Proxy"
    )


def test_b7_classical_maps_to_complete_surge_variant() -> None:
    line = _rule_to_surge_line("RULE-SET,global-resolve,Proxy", _B7_PROVIDERS)
    assert line == f"RULE-SET,{_B7_CDN_BASE}/rule/Surge/Global/Global_All.list,Proxy"


def test_b7_unverified_classical_variant_fails_closed() -> None:
    with pytest.raises(UnsupportedRuleTypeError):
        _rule_to_surge_line("RULE-SET,netflix-classical,Proxy", _B7_PROVIDERS)


def test_b7_clash_yaml_plain_name_rewritten() -> None:
    line = _rule_to_surge_line("RULE-SET,teams,Microsoft", _B7_PROVIDERS)
    assert line == f"RULE-SET,{_B7_CDN_BASE}/rule/Surge/Teams/Teams.list,Microsoft"


def test_b7_clash_yaml_preserves_no_resolve_flag() -> None:
    line = _rule_to_surge_line("RULE-SET,tencent,DIRECT,no-resolve", _B7_PROVIDERS)
    assert line == f"RULE-SET,{_B7_CDN_BASE}/rule/Surge/Tencent/Tencent.list,DIRECT,no-resolve"


def test_b7_jsdelivr_clash_yaml_is_rewritten_idempotently_to_canonical_cdn() -> None:
    providers = {
        "telegram": {
            "type": "http",
            "url": f"{_B7_CDN_BASE}/rule/Clash/Telegram/Telegram_No_Resolve.yaml",
        }
    }
    line = _rule_to_surge_line(
        "RULE-SET,telegram,社交通讯,no-resolve",
        providers,
    )
    assert line == (
        f"RULE-SET,{_B7_CDN_BASE}/rule/Surge/Telegram/Telegram.list,"
        "社交通讯,no-resolve"
    )


def test_b7_clash_list_left_unchanged() -> None:
    # .list files under rule/Clash/ are already classical text and parse in Surge
    providers = {"ea": {"type": "http", "url": f"{_B7_RAW_BASE}/rule/Clash/EA/EA.list"}}
    line = _rule_to_surge_line("RULE-SET,ea,DIRECT", providers)
    assert line == f"RULE-SET,{_B7_RAW_BASE}/rule/Clash/EA/EA.list,DIRECT"


def test_build_surge_config_has_no_clash_yaml_urls() -> None:
    result = _compile([], [], ["RULE-SET,tencent,DIRECT", "MATCH,DIRECT"], _B7_PROVIDERS)
    assert "/rule/Clash/" not in result
    assert ".yaml" not in result
    assert "/rule/Surge/Tencent/Tencent.list" in result
    assert _B7_CDN_BASE in result


def test_leo_surge_keeps_core_services_when_mihomo_only_rules_are_skipped() -> None:
    node = _ss()
    config = apply_template(load_template(LEO_TEMPLATE_ID), [node])

    conf, warnings = build_surge_config(
        [node],
        config["proxy-groups"],
        config["rules"],
        config["rule-providers"],
    )

    for suffix in (
        "openai.com",
        "chatgpt.com",
        "oaistatic.com",
        "oaiusercontent.com",
    ):
        assert f"DOMAIN-SUFFIX,{suffix},AI 服务" in conf
    native_rule_sets = {
        "Claude": ("Claude", "AI 服务"),
        "GitHub": ("GitHub", "开发服务"),
        "Apple": ("Apple_All_No_Resolve", "Apple"),
        "YouTube": ("YouTube", "流媒体"),
        "Google": ("Google", "Google"),
        "Microsoft": ("Microsoft", "Microsoft"),
        "Telegram": ("Telegram", "社交通讯"),
    }
    for service, (list_name, target) in native_rule_sets.items():
        expected = f"/rule/Surge/{service}/{list_name}.list,{target}"
        if service in {"YouTube", "Google"}:
            expected += ",no-resolve"
        assert expected in conf
    assert "/rule/Surge/Telegram/Telegram.list,社交通讯,no-resolve" in conf
    assert "raw.githubusercontent.com/blackmatrix7" not in conf
    assert "cdn.jsdelivr.net/gh/blackmatrix7" in conf

    assert conf.index("/rule/Surge/Claude/Claude.list") < conf.index(
        "/rule/Surge/Google/Google.list"
    )
    assert conf.index("/rule/Surge/YouTube/YouTube.list") < conf.index(
        "/rule/Surge/Google/Google.list"
    )
    assert {warning["code"] for warning in warnings} == {
        "unsupported_rule_sets",
        "unsupported_rule_types",
    }
    skipped_sets = next(
        warning for warning in warnings if warning["code"] == "unsupported_rule_sets"
    )
    assert skipped_sets["count"] == 1
    assert "category-ai-!cn.list" in skipped_sets["examples"][0]
    assert "DOMAIN-SUFFIX,cn,DIRECT" in conf
    assert "DEST-PORT,10000-65535,默认代理" not in conf
    assert "FINAL,默认代理" in conf


def test_leo_surge_keeps_the_us_node_pool_manual() -> None:
    nodes = [_ss("美国 SS"), _ss("香港 SS")]
    config = apply_template(load_template(LEO_TEMPLATE_ID), nodes)

    conf, _ = build_surge_config(
        nodes,
        config["proxy-groups"],
        config["rules"],
        config["rule-providers"],
    )

    assert "美国节点 = select, 美国 SS" in conf
    assert "美国节点 = url-test" not in conf
    assert "AI 服务 = select, 美国节点, 手动选择" in conf
    assert "proxy-test-url = http://www.apple.com/library/test/success.html" in conf


def test_leo_surge_prunes_manual_us_group_when_all_us_nodes_are_unsupported() -> None:
    nodes = [
        _ss("香港 SS"),
        ProxyNode(
            name="美国 HY2",
            protocol="hysteria2",
            server="hy.example.com",
            port=443,
            extra={"password": "secret"},
        ),
        ProxyNode(
            name="US-TUIC",
            protocol="tuic",
            server="tuic.example.com",
            port=443,
            extra={"uuid": "uuid", "password": "secret"},
        ),
    ]
    config = apply_template(load_template(LEO_TEMPLATE_ID), nodes)

    conf, warnings = build_surge_config(
        nodes,
        config["proxy-groups"],
        config["rules"],
        config["rule-providers"],
    )

    assert "美国节点 =" not in conf
    assert "AI 服务 = select, 手动选择" in conf
    assert [warning["value"] for warning in warnings if warning["code"] == "unsupported_protocol"] == [
        "hysteria2",
        "tuic",
    ]


def test_non_b7_clash_yaml_raises_unsupported() -> None:
    # Clash payload YAML from a repo with no Surge .list equivalent cannot be
    # rewritten; it must be reported as unsupported rather than emitted.
    providers = {
        "fakeip": {
            "type": "http",
            "url": "https://raw.githubusercontent.com/ameyukisora/Clash-Rule/abc/provider/fakeip-filter.yaml",
        }
    }
    with pytest.raises(UnsupportedRuleTypeError) as exc_info:
        _rule_to_surge_line("RULE-SET,fakeip,DIRECT", providers)
    assert exc_info.value.value.endswith("fakeip-filter.yaml")


# ── skk.moe Clash → Surge /List substitution & behavior-aware directives ───

_SKK = "https://ruleset.skk.moe"


def test_skk_domainset_becomes_domain_set() -> None:
    providers = {"cdn": {"behavior": "domain", "url": f"{_SKK}/Clash/domainset/cdn.txt"}}
    line = _rule_to_surge_line("RULE-SET,cdn,Proxy", providers)
    assert line == f"DOMAIN-SET,{_SKK}/List/domainset/cdn.conf,Proxy"


def test_skk_non_ip_becomes_rule_set() -> None:
    providers = {"stream": {"behavior": "classical", "url": f"{_SKK}/Clash/non_ip/stream.txt"}}
    line = _rule_to_surge_line("RULE-SET,stream,Proxy", providers)
    assert line == f"RULE-SET,{_SKK}/List/non_ip/stream.conf,Proxy"


def test_skk_ip_becomes_rule_set_with_no_resolve() -> None:
    providers = {"cnip": {"behavior": "ipcidr", "url": f"{_SKK}/Clash/ip/china_ip.txt"}}
    line = _rule_to_surge_line("RULE-SET,cnip,DIRECT,no-resolve", providers)
    assert line == f"RULE-SET,{_SKK}/List/ip/china_ip.conf,DIRECT,no-resolve"


def test_domain_set_drops_no_resolve_flag() -> None:
    # DOMAIN-SET has no IP rules, so a stray no-resolve must not be appended.
    providers = {"cdn": {"behavior": "domain", "url": f"{_SKK}/Clash/domainset/cdn.txt"}}
    line = _rule_to_surge_line("RULE-SET,cdn,Proxy,no-resolve", providers)
    assert line == f"DOMAIN-SET,{_SKK}/List/domainset/cdn.conf,Proxy"


def test_clash_domain_bare_list_raises_unsupported() -> None:
    providers = {
        "cn": {
            "behavior": "domain",
            "url": "https://raw.githubusercontent.com/DustinWin/ruleset_geodata/abc/cn.list",
        }
    }
    with pytest.raises(UnsupportedRuleTypeError):
        _rule_to_surge_line("RULE-SET,cn,DIRECT", providers)


def test_clash_ipcidr_bare_list_raises_unsupported() -> None:
    providers = {
        "cnip": {
            "behavior": "ipcidr",
            "url": "https://raw.githubusercontent.com/DustinWin/ruleset_geodata/abc/cnip.list",
        }
    }
    with pytest.raises(UnsupportedRuleTypeError):
        _rule_to_surge_line("RULE-SET,cnip,DIRECT,no-resolve", providers)


def test_classical_list_left_as_rule_set() -> None:
    providers = {
        "x": {
            "behavior": "classical",
            "url": "https://example.com/rules/x.list",
        }
    }
    line = _rule_to_surge_line("RULE-SET,x,DIRECT", providers)
    assert line == "RULE-SET,https://example.com/rules/x.list,DIRECT"


def test_build_surge_config_skips_non_b7_clash_yaml() -> None:
    providers = {
        "fakeip": {
            "type": "http",
            "url": "https://raw.githubusercontent.com/ameyukisora/Clash-Rule/abc/provider/fakeip-filter.yaml",
        }
    }
    result, warnings = build_surge_config([], [], ["RULE-SET,fakeip,DIRECT"], providers)
    assert ".yaml" not in result
    assert "fakeip-filter" not in result
    assert warnings[0]["code"] == "unsupported_rule_sets"
    assert warnings[0]["examples"] == [
        "https://raw.githubusercontent.com/ameyukisora/Clash-Rule/abc/provider/fakeip-filter.yaml"
    ]
