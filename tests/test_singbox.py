"""Tests for the Sing-box config compiler and IR → Sing-box outbound rendering."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.platforms.singbox import build_singbox_config, _ir_to_outbound
from app.ir import ProxyNode, TLSConfig, TransportConfig
from app.main import app


def _ss(name: str = "HK", server: str = "hk.example.com", port: int = 443) -> ProxyNode:
    return ProxyNode(
        name=name, protocol="ss", server=server, port=port,
        extra={"cipher": "aes-256-gcm", "password": "secret"},
    )


def _vmess_ws(name: str = "US") -> ProxyNode:
    return ProxyNode(
        name=name, protocol="vmess", server="us.example.com", port=443,
        tls=TLSConfig(enabled=True, sni="cdn.example.com"),
        transport=TransportConfig(type="ws", path="/path", host="cdn.example.com"),
        extra={"uuid": "test-uuid", "alter_id": 0, "cipher": "auto"},
    )


def _trojan(name: str = "TR") -> ProxyNode:
    return ProxyNode(
        name=name, protocol="trojan", server="tr.example.com", port=443,
        tls=TLSConfig(enabled=True, sni="tr.example.com"),
        extra={"password": "trpass"},
    )


# ── _ir_to_outbound ────────────────────────────────────────────────────────


def test_ss_outbound_fields() -> None:
    ob = _ir_to_outbound(_ss())
    assert ob["type"] == "shadowsocks"
    assert ob["tag"] == "HK"
    assert ob["server"] == "hk.example.com"
    assert ob["server_port"] == 443
    assert ob["method"] == "aes-256-gcm"
    assert ob["password"] == "secret"
    assert "tls" not in ob


def test_vmess_ws_outbound_has_tls_and_transport() -> None:
    ob = _ir_to_outbound(_vmess_ws())
    assert ob["type"] == "vmess"
    assert ob["tls"]["enabled"] is True
    assert ob["tls"]["server_name"] == "cdn.example.com"
    assert ob["transport"]["type"] == "ws"
    assert ob["transport"]["path"] == "/path"
    assert ob["transport"]["headers"]["Host"] == "cdn.example.com"


def test_trojan_outbound() -> None:
    ob = _ir_to_outbound(_trojan())
    assert ob["type"] == "trojan"
    assert ob["password"] == "trpass"
    assert ob["tls"]["enabled"] is True


def test_hysteria2_outbound() -> None:
    node = ProxyNode(
        name="HY2", protocol="hysteria2", server="hy.example.com", port=8443,
        tls=TLSConfig(enabled=True, sni="hy.example.com"),
        extra={"password": "hypass", "obfs": "salamander", "obfs_password": "obfspass", "up": 100, "down": 200},
    )
    ob = _ir_to_outbound(node)
    assert ob["type"] == "hysteria2"
    assert ob["obfs"]["type"] == "salamander"
    assert ob["up_mbps"] == 100


# ── build_singbox_config ───────────────────────────────────────────────────


PROXY_GROUPS = [
    {
        "name": "PROXY",
        "type": "select",
        "proxies": ["手动切换", "自动选择", "DIRECT"],
    },
    {
        "name": "手动切换",
        "type": "select",
        "proxies": ["HK", "US", "TR"],
    },
    {
        "name": "自动选择",
        "type": "url-test",
        "proxies": ["HK", "US", "TR"],
        "url": "https://www.gstatic.com/generate_204",
        "interval": 300,
    },
]

RULES = [
    "DOMAIN-SUFFIX,google.com,PROXY",
    "DOMAIN-SUFFIX,baidu.com,DIRECT",
    "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
    "RULE-SET,ads,block",
    "MATCH,PROXY",
]

RULE_PROVIDERS = {
    "ads": {
        "type": "http",
        "behavior": "domain",
        "url": "https://raw.githubusercontent.com/foo/bar/ads.yaml",
        "interval": 86400,
    }
}


def test_config_has_required_top_level_sections() -> None:
    nodes = [_ss("HK"), _vmess_ws("US"), _trojan("TR")]
    config = build_singbox_config(nodes, PROXY_GROUPS, RULES, RULE_PROVIDERS)
    assert "log" in config
    assert "dns" in config
    assert "inbounds" in config
    assert "outbounds" in config
    assert "route" in config


def test_outbounds_contain_nodes_and_groups() -> None:
    nodes = [_ss("HK"), _vmess_ws("US")]
    config = build_singbox_config(nodes, PROXY_GROUPS, RULES, RULE_PROVIDERS)
    tags = [ob["tag"] for ob in config["outbounds"]]
    assert "HK" in tags
    assert "US" in tags
    assert "PROXY" in tags
    assert "手动切换" in tags
    assert "自动选择" in tags
    assert "direct" in tags
    assert "block" in tags
    assert "dns-out" in tags


def test_url_test_group_rendered_correctly() -> None:
    nodes = [_ss("HK")]
    config = build_singbox_config(nodes, PROXY_GROUPS, [], {})
    auto = next(ob for ob in config["outbounds"] if ob["tag"] == "自动选择")
    assert auto["type"] == "urltest"
    assert "interval" in auto


def test_route_rules_compiled() -> None:
    nodes = [_ss("HK")]
    config = build_singbox_config(nodes, PROXY_GROUPS, RULES, RULE_PROVIDERS)
    rules = config["route"]["rules"]
    # DNS rule and private IP rule are always first
    assert rules[0] == {"protocol": "dns", "outbound": "dns-out"}
    assert rules[1] == {"ip_is_private": True, "outbound": "direct"}
    # User rules follow
    rule_texts = [str(r) for r in rules]
    assert any("google.com" in t for t in rule_texts)
    assert any("baidu.com" in t for t in rule_texts)


def test_match_rule_sets_final_outbound() -> None:
    nodes = [_ss("HK")]
    config = build_singbox_config(nodes, PROXY_GROUPS, ["MATCH,DIRECT"], {})
    assert config["route"]["final"] == "direct"


def test_rule_sets_compiled() -> None:
    nodes = [_ss("HK")]
    config = build_singbox_config(nodes, PROXY_GROUPS, RULES, RULE_PROVIDERS)
    rule_sets = config["route"]["rule_set"]
    assert len(rule_sets) == 1
    assert rule_sets[0]["tag"] == "ads"
    assert rule_sets[0]["format"] == "source"


def test_mrs_rule_set_uses_binary_format() -> None:
    nodes = [_ss("HK")]
    providers = {"cn": {"type": "http", "url": "https://example.com/cn.mrs", "interval": 86400}}
    config = build_singbox_config(nodes, [], [], providers)
    rs = config["route"]["rule_set"][0]
    assert rs["format"] == "binary"


def test_dns_block_rule_added_for_ad_providers() -> None:
    nodes = [_ss("HK")]
    config = build_singbox_config(nodes, PROXY_GROUPS, RULES, RULE_PROVIDERS)
    dns_rules = config["dns"]["rules"]
    assert any(r.get("server") == "block" for r in dns_rules)


def test_empty_nodes_produces_valid_config() -> None:
    config = build_singbox_config([], [], [], {})
    assert isinstance(config, dict)
    # At minimum the required sections exist
    for key in ("log", "dns", "inbounds", "outbounds", "route"):
        assert key in config


# ── API integration: singbox target ───────────────────────────────────────


CLASH_SUBSCRIPTION = """
proxies:
  - name: 香港 01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-256-gcm
    password: secret
  - name: 日本
    type: trojan
    server: jp.example.com
    port: 443
    password: trpass
    sni: jp.example.com
"""


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


_SIMPLE_TEMPLATE = {
    "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": []}],
    "rules": ["MATCH,PROXY"],
}


def test_workspace_preview_rejects_singbox_for_leo(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_convert(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return _SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_convert)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    preview = client.post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "local:community_templates/leo/leo.yaml",
            "target": "singbox",
        },
    )

    assert preview.status_code == 422
    assert "leo.yaml only supports Clash/Mihomo and Surge" in preview.text


def test_subscribe_rejects_singbox_for_leo(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_convert(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return _SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_convert)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.get(
        "/subscribe",
        params={
            "subscription_url": "https://example.com/sub",
            "template": "local:community_templates/leo/leo.yaml",
            "target": "singbox",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "leo.yaml only supports Clash/Mihomo and Surge targets"


def test_unsupported_target_returns_400(client: TestClient) -> None:
    response = client.post(
        "/compile",
        json={"workspace": {}, "target": "quantumult"},
    )
    assert response.status_code == 400
    assert "unsupported target" in response.json()["detail"]
