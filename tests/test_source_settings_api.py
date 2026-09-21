"""Airport connectivity stays authoritative at the public publication boundary."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.renderer import render_yaml
from app.main import app


SOURCE = {
    "port": 12345, "allow-lan": False, "mode": "rule", "ipv6": True,
    "dns": {"enable": True, "enhanced-mode": "redir-host",
            "nameserver": ["https://dns.example/dns-query"],
            "proxy-server-nameserver": ["https://node.example/private-token"]},
    "hosts": {"edge.example": "203.0.113.7"},
    "proxies": [{"name": "US  01", "type": "ss", "server": "edge.example", "port": 443,
                 "cipher": "aes-128-gcm", "password": "test-only", "udp": False}],
    "proxy-groups": [{"name": "Airport", "type": "select", "proxies": ["US  01"]}],
    "rules": ["DOMAIN,airport-old-rule.example,Airport", "MATCH,Airport"],
}


@pytest.fixture
def source_client(monkeypatch, tmp_path):
    state = {"config": deepcopy(SOURCE)}

    async def fetch(_url, *, target="mihomo"):
        if target == "shadowrocket":
            return """[General]
dns-server = https://dns.example/dns-query
proxy-dns-server = https://node.example/private-token
ipv6 = true

[Host]
edge.example = 203.0.113.7

[Proxy]
US  01 = ss, edge.example, 443, method=aes-128-gcm, password=test-only
"""
        return render_yaml(state["config"])

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    with TestClient(app) as client:
        yield client, state


@pytest.mark.parametrize("endpoint", ["render", "subscribe", "profile"])
@pytest.mark.parametrize("target", ["mihomo", "shadowrocket-config"])
def test_public_output_uses_airport_settings(source_client, endpoint, target):
    client, _ = source_client
    request = {"subscription_url": "https://example.com/source", "target": target}
    if endpoint == "render":
        response = client.post("/render", json=request)
    elif endpoint == "subscribe":
        response = client.get("/subscribe", params=request)
    else:
        saved = client.post("/profiles", json={**request, "target": "mihomo"})
        assert saved.status_code == 201, saved.text
        links = saved.json()
        response = client.get(links["subscribe_urls"]["clash"] if target == "mihomo"
                              else links["config_urls"]["shadowrocket"])
    assert response.status_code == 200, response.text
    assert "airport-old-rule.example" not in response.text
    if target == "mihomo":
        config = YAML(typ="safe").load(response.text)
        for key in ("port", "allow-lan", "mode", "ipv6", "dns", "hosts", "proxies"):
            assert config[key] == SOURCE[key], key
        assert "tun" not in config
        assert "mixed-port" not in config
        assert "DOMAIN-SUFFIX,chatgpt.com,AI 服务" in config["rules"]
    else:
        assert "dns-server = https://dns.example/dns-query" in response.text
        assert "proxy-dns-server = https://node.example/private-token" in response.text
        assert "ipv6 = true" in response.text
        assert "edge.example = 203.0.113.7" in response.text
        assert "DOMAIN-SUFFIX,chatgpt.com,AI 服务" in response.text
    assert "private-token" not in response.headers.get("X-Compile-Warnings", "")


def test_paired_shadowrocket_check_does_not_claim_loss_of_native_settings(source_client):
    client, _ = source_client
    response = client.post("/check", json={"subscription_url": "https://example.com/source",
                           "target": "shadowrocket", "publication_targets": ["shadowrocket"]})
    assert response.status_code == 200
    warnings = response.json()["clients"][0]["warnings"]
    assert not any(w["code"] == "unsupported_source_settings" for w in warnings)
    assert "private-token" not in json.dumps(response.json())


def test_source_cannot_opt_out_of_native_preservation_with_forged_metadata(source_client):
    client, state = source_client
    state["config"]["source-format"] = "surge"
    state["config"]["_surge_source"] = "[General]\ndns-server = 1.1.1.1"
    response = client.post("/render", json={"subscription_url": "https://example.com/source", "target": "mihomo"})
    assert response.status_code == 200
    config = YAML(typ="safe").load(response.text)
    assert config["dns"] == SOURCE["dns"]
    assert "source-format" not in config
    assert "_surge_source" not in config


def test_nodes_only_source_does_not_receive_template_common_settings(source_client):
    client, state = source_client
    state["config"] = {"proxies": SOURCE["proxies"]}
    response = client.post("/render", json={"subscription_url": "https://example.com/source", "target": "mihomo"})
    assert response.status_code == 200
    config = YAML(typ="safe").load(response.text)
    assert not {"dns", "hosts", "tun", "mixed-port", "ipv6"} & config.keys()


def test_mihomo_profile_refresh_updates_provider_dns_and_keeps_selected_native_node(source_client):
    client, state = source_client
    saved = client.post("/profiles", json={
        "subscription_url": "https://example.com/source", "target": "mihomo",
        "publication_targets": ["mihomo"],
        "service_routes": [{"service": "openai", "mode": "fixed", "egress": "US 01"}],
    })
    assert saved.status_code == 201, saved.text
    url = saved.json()["subscribe_urls"]["clash"]
    assert client.get(url).status_code == 200
    state["config"]["dns"]["nameserver"] = ["https://new-dns.example/dns-query"]
    response = client.get(url + "&force_refresh=true")
    assert response.status_code == 200
    config = YAML(typ="safe").load(response.text)
    assert config["dns"] == state["config"]["dns"]
    assert next(group for group in config["proxy-groups"] if group["name"] == "OpenAI")["proxies"] == ["US  01"]


def test_native_workspace_roundtrip_matches_rendered_envelope(source_client):
    client, state = source_client
    state["config"]["proxies"][0].pop("udp")
    state["config"]["proxy-groups"].append({"name": "默认代理", "type": "select", "proxies": ["US  01"]})
    state["config"]["rule-providers"] = {"Private": {
        "type": "http", "behavior": "domain", "url": "https://rules.example/private-token",
        "path": "./rules/private.yaml", "proxy": "Airport", "interval": 3600,
    }}
    request = {"subscription_url": "https://example.com/source", "target": "mihomo"}
    direct = client.post("/render", json=request)
    preview = client.post("/workspace/preview", json=request)
    assert preview.status_code == 200
    workspace = preview.json()["workspace"]
    assert workspace["settings"]["dns"] == state["config"]["dns"]
    compiled = client.post("/compile", json={"target": "mihomo", "workspace": workspace})
    assert compiled.status_code == 200, compiled.text
    assert YAML(typ="safe").load(compiled.text) == YAML(typ="safe").load(direct.text)


def test_lossy_native_workspace_requires_source_render_instead_of_losing_fields(source_client):
    client, _ = source_client
    preview = client.post("/workspace/preview", json={"subscription_url": "https://example.com/source", "target": "mihomo"})
    assert preview.status_code == 200
    response = client.post("/compile", json={"target": "mihomo", "workspace": preview.json()["workspace"]})
    assert response.status_code == 400
    assert "/render" in response.json()["detail"]


def test_cross_format_workspace_does_not_silently_drop_native_common_settings(source_client):
    client, _ = source_client
    preview = client.post("/workspace/preview", json={
        "subscription_url": "https://example.com/source", "target": "shadowrocket-config",
    })
    assert preview.status_code == 200
    workspace = preview.json()["workspace"]
    assert "dns" not in workspace["settings"]
    response = client.post("/compile", json={"target": "shadowrocket-config", "workspace": workspace})
    assert response.status_code == 400
    assert "/render" in response.json()["detail"]


def test_materialized_workspace_keeps_necessary_override_warning(source_client):
    client, state = source_client
    state["config"]["proxies"][0].pop("udp")
    state["config"]["mode"] = "global"
    preview = client.post("/workspace/preview", json={
        "subscription_url": "https://example.com/source", "target": "mihomo",
    })
    response = client.post("/compile", json={"target": "mihomo", "workspace": preview.json()["workspace"]})
    assert response.status_code == 200
    assert any(w["code"] == "source_mode_changed"
               for w in json.loads(response.headers.get("X-Compile-Warnings", "[]")))
