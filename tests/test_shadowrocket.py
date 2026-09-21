from __future__ import annotations

import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.main import app
from app.core.parsers.clash import clash_to_ir
from app.core.platforms.shadowrocket import build_shadowrocket_config
from app.core.renderer import render_yaml


@pytest.fixture
def shadowrocket_client(tmp_path, monkeypatch):
    state = {"content": """
proxies:
  - {name: 美国 01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: test-password, udp: true}
""", "status": 200}
    original_client = httpx.AsyncClient

    def handler(request):
        assert request.url.host == "example.com"
        return httpx.Response(state["status"], text=state["content"])

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
    ])
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.delenv("SUBFLOW_SUBCONVERTER_URL", raising=False)
    with TestClient(app) as client:
        yield client, state


def test_profile_publishes_three_clients_with_shadowrocket_native_policy(shadowrocket_client):
    client, _ = shadowrocket_client
    created = client.post("/profiles", json={
        "subscription_url": "https://example.com/sub?token=source-secret",
        "target": "shadowrocket",
    })
    assert created.status_code == 201, created.text
    urls = created.json()["subscribe_urls"]
    assert set(urls) == {"clash", "surge", "shadowrocket"}
    assert all("source-secret" not in url for url in urls.values())

    nodes = client.get(urls["shadowrocket"])
    assert nodes.status_code == 200
    assert nodes.headers["content-type"].startswith("text/plain")
    parsed_nodes = YAML(typ="safe").load(nodes.text)
    assert set(parsed_nodes) == {"proxies"}
    assert parsed_nodes["proxies"][0]["password"] == "test-password"
    response = client.get(created.json()["config_urls"]["shadowrocket"])
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"] == 'inline; filename="shadowrocket.conf"'
    for section in ("[Proxy Group]", "[Rule]"):
        assert section in response.text
    assert "[General]" not in response.text
    assert "[Proxy]" not in response.text
    assert "美国节点 = select, 美国 01" in response.text
    assert "AI 服务 = select, 美国节点" in response.text
    assert "DOMAIN-SUFFIX,openai.com,AI 服务" in response.text
    assert "FINAL,默认代理" in response.text
    assert "encrypt-method=" not in response.text
    assert "proxy-test-url =" not in response.text
    warnings = json.loads(response.headers["X-Compile-Warnings"])
    assert any(warning["code"] == "unsupported_rule_types" for warning in warnings)


def test_shadowrocket_config_rejects_rules_to_filtered_nodes_and_groups():
    nodes = [clash_to_ir(proxy) for proxy in [
        {"name": "可用", "type": "ss", "server": "ss.example.com", "port": 443},
        {"name": "未支持", "type": "wireguard", "server": "wg.example.com", "port": 443},
    ]]
    config, warnings = build_shadowrocket_config(nodes, [
        {"name": "美国节点", "type": "select", "proxies": ["未支持"]},
        {"name": "AI 服务", "type": "select", "proxies": ["美国节点", "可用"]},
    ], [
        "DOMAIN,api.example.com,未支持",
        "DOMAIN,group.example.com,美国节点",
        "MATCH,AI 服务",
    ], {})
    assert "DOMAIN,api.example.com,REJECT" in config
    assert "DOMAIN,group.example.com,REJECT" in config
    assert "AI 服务 = select, 可用" in config
    assert any(warning["code"] == "unsupported_protocol" for warning in warnings)


@pytest.mark.parametrize("options", [
    {"type": "vless", "uuid": "test-id", "tls": True, "flow": "xtls-rprx-vision", "client-fingerprint": "chrome", "reality-opts": {"public-key": "test-public", "short-id": "abcd"}},
    {"type": "hysteria2", "password": "test-pass", "obfs": "salamander", "obfs-password": "test-obfs", "ports": "20000-30000"},
    {"type": "tuic", "uuid": "test-id", "password": "test-pass", "congestion-controller": "bbr", "udp-relay-mode": "native"},
    {"type": "anytls", "password": "test-pass", "sni": "edge.example.com", "idle-session-check-interval": 30},
])
def test_shadowrocket_nodes_keep_modern_protocol_fields_and_policy_members(shadowrocket_client, options):
    client, state = shadowrocket_client
    source = {"name": "美国 01", "server": "us.example.com", "port": 443, **options}
    state["content"] = render_yaml({"proxies": [source]})
    request = {"subscription_url": "https://example.com/sub", "target": "shadowrocket"}
    nodes = client.post("/render", json=request)
    assert nodes.status_code == 200, nodes.text
    rendered = YAML(typ="safe").load(nodes.text)["proxies"][0]
    for key, value in source.items():
        assert rendered[key] == value
    config = client.post("/render", json={**request, "target": "shadowrocket-config"})
    assert config.status_code == 200, config.text
    assert "美国节点 = select, 美国 01" in config.text
    assert "AI 服务 = select, 美国节点" in config.text
    assert "DST-PORT,123,DIRECT" in config.text
    assert "DEST-PORT," not in config.text


def test_shadowrocket_profile_keeps_each_artifact_cache_separate(shadowrocket_client):
    client, state = shadowrocket_client
    created = client.post("/profiles", json={"subscription_url": "https://example.com/sub"}).json()
    urls = list(created["subscribe_urls"].values()) + [created["config_urls"]["shadowrocket"]]
    artifacts = {url: client.get(url) for url in urls}
    assert all(response.status_code == 200 for response in artifacts.values())
    state["status"] = 503
    for url, previous in artifacts.items():
        fallback = client.get(url + "&force_refresh=true")
        assert fallback.status_code == 200
        assert fallback.text == previous.text
        assert fallback.headers["X-Subflow-Stale"] == "true"
        assert fallback.headers["content-type"] == previous.headers["content-type"]
    assert client.get(created["config_urls"]["shadowrocket"].replace(created["token"], "wrong")).status_code == 404


def test_shadowrocket_native_protocols_are_not_filtered_by_our_legacy_serializer(shadowrocket_client):
    client, state = shadowrocket_client
    state["content"] = "proxies:\n  - {name: WG, type: wireguard, server: wg.example.com, port: 443}\n"
    for target in ("shadowrocket", "shadowrocket-config"):
        response = client.get("/subscribe", params={"subscription_url": "https://example.com/sub", "target": target})
        assert response.status_code == 200
        assert "WG" in response.text
        if target == "shadowrocket":
            assert response.text == state["content"]


def test_shadowrocket_pair_refreshes_nodes_and_service_members_without_new_urls(shadowrocket_client):
    client, state = shadowrocket_client
    created = client.post("/profiles", json={"subscription_url": "https://example.com/sub"}).json()
    urls = [created["subscribe_urls"]["shadowrocket"], created["config_urls"]["shadowrocket"]]
    for url in urls:
        assert "美国 01" in client.get(url).text
    state["content"] = state["content"].replace("美国 01", "美国 02")
    for url in urls:
        refreshed = client.get(url + "&force_refresh=true")
        assert refreshed.status_code == 200
        assert "美国 02" in refreshed.text
        assert "美国 01" not in refreshed.text
        assert "X-Subflow-Stale" not in refreshed.headers
