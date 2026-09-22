"""Native Surge subscriptions own connectivity; Subflow owns routing."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.core.platforms.surge_audit import audit_native_surge_profile
from app.main import app


SOURCE = """#!MANAGED-CONFIG https://example.com/upstream/source-token interval=86400
# Provider connectivity settings
[General]
dns-server = 223.5.5.5, 1.2.4.8, 119.29.29.29
doh-server = https://doh.pub/dns-query, https://dns.alidns.com/dns-query
proxy-test-url = http://cp.cloudflare.com/generate_204
test-timeout = 4
ipv6 = false
skip-proxy = localhost, *.local, 10.0.0.0/8

[Host]
node.example.com = server:https://resolver.example/private-dns-token,https://backup.example/private-dns-token

[Proxy]
US01 = ss, node.example.com, 443, encrypt-method=aes-128-gcm, password="example,with,commas", obfs=http, obfs-host=cdn.example.com:, tfo=true
US  02 = ss, node.example.com, 444, encrypt-method=aes-128-gcm, password="example,with,commas", obfs=http, obfs-host=cdn.example.com:, tfo=true

[Proxy Group]
Airport = select, US01, US  02
默认代理 = select, DIRECT

[Rule]
DOMAIN,provider-old-rule.example,Airport
FINAL,Airport

[Replica]
hide-apple-request = true

[URL Rewrite]
^http://example.com http://www.example.com 302
"""

RISKY_GENERAL = """[General]
Allow-WiFi-Access = true
doh-server = https://resolver.example/dns-query?token=private-token
loglevel = info
include-all-networks = true
include-apns = true
include-cellular-services = true

[Proxy]
US01 = ss, node.example.com, 443, encrypt-method=aes-128-gcm, password=private-password
"""


def section(config, name):
    match = re.search(rf"(?im)^\[{re.escape(name)}\]\s*\n(.*?)(?=^\[|\Z)", config, re.S)
    return match.group(1).strip() if match else None


def test_native_audit_detects_risky_mixed_case_general_settings_without_values() -> None:
    warnings = audit_native_surge_profile(RISKY_GENERAL)

    assert {warning["code"] for warning in warnings} == {
        "wifi_proxy_access_without_auth",
        "legacy_surge_option",
        "surge_info_loglevel",
        "surge_full_tunnel_scope",
    }
    serialized = json.dumps(warnings)
    assert "private-token" not in serialized
    assert "resolver.example" not in serialized
    assert "private-password" not in serialized


def test_native_audit_recognizes_wifi_auth_without_exposing_password() -> None:
    warnings = audit_native_surge_profile(
        RISKY_GENERAL.replace(
            "doh-server =",
            "wifi-access-http-auth = user:private-password\ndoh-server =",
        )
    )

    assert "wifi_proxy_access_without_auth" not in {
        warning["code"] for warning in warnings
    }
    assert "private-password" not in json.dumps(warnings)


@pytest.fixture
def native_client(monkeypatch, tmp_path):
    state = {"source": SOURCE}

    async def fetch(_url):
        return state["source"]

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    with TestClient(app) as client:
        yield client, state


@pytest.mark.parametrize("endpoint", ["render", "subscribe", "profile"])
def test_native_subscription_preserves_connectivity_and_replaces_routing(native_client, endpoint):
    client, _ = native_client
    request = {"subscription_url": "https://example.com/source", "target": "surge"}
    if endpoint == "render":
        response = client.post("/render", json=request)
    elif endpoint == "subscribe":
        response = client.get("/subscribe", params=request)
    else:
        saved = client.post("/profiles", json=request)
        assert saved.status_code == 201, saved.text
        response = client.get(saved.json()["subscribe_urls"]["surge"])

    assert response.status_code == 200, response.text
    for name in ("General", "Host", "Proxy", "Replica", "URL Rewrite"):
        assert section(response.text, name) == section(SOURCE, name), name
    assert "DOMAIN-SUFFIX,chatgpt.com,AI 服务" in section(response.text, "Rule")
    assert "provider-old-rule.example" not in section(response.text, "Rule")
    groups = section(response.text, "Proxy Group")
    assert "Airport = select, US01, US  02" in groups
    assert "默认代理 = select, DIRECT\n" in groups + "\n"
    assert "Subflow 默认代理 = select," in groups
    assert "FINAL,Subflow 默认代理" in section(response.text, "Rule")
    assert "US  02" in next(line for line in groups.splitlines() if line.startswith("手动选择 ="))
    # Updating this artifact must not send the client back to the raw source.
    assert "#!MANAGED-CONFIG" not in response.text
    assert "source-token" not in response.text
    assert "private-dns-token" not in response.headers.get("X-Compile-Warnings", "")


def test_native_implicit_settings_remain_implicit(native_client):
    client, state = native_client
    state["source"] = "[Proxy]\nUS01 = ss, node.example.com, 443, encrypt-method=aes-128-gcm, password=example\n"
    response = client.post("/render", json={"subscription_url": "https://example.com/source", "target": "surge"})

    assert response.status_code == 200
    assert section(response.text, "General") is None
    assert section(response.text, "Host") is None
    assert section(response.text, "Rule")
    assert section(response.text, "Proxy Group")


def test_profile_refresh_keeps_new_provider_settings_and_saved_service_route(native_client):
    client, state = native_client
    request = {"subscription_url": "https://example.com/source", "target": "surge",
               "publication_targets": ["surge"],
               "service_routes": [{"service": "openai", "mode": "fixed", "egress": "US  02"}]}
    check = client.post("/check", json=request)
    assert check.status_code == 200
    assert check.json()["can_publish"] is True
    saved = client.post("/profiles", json=request)
    assert saved.status_code == 201, saved.text
    url = saved.json()["subscribe_urls"]["surge"]
    first = client.get(url)
    assert first.status_code == 200
    assert "test-timeout = 4" in first.text
    state["source"] = SOURCE.replace("test-timeout = 4", "test-timeout = 7")
    refreshed = client.get(url + "&force_refresh=true")

    assert refreshed.status_code == 200
    assert "test-timeout = 7" in refreshed.text
    assert "OpenAI = select, US  02" in refreshed.text
    assert section(refreshed.text, "Host") == section(SOURCE, "Host")


def test_native_profile_metadata_does_not_leak_into_other_targets_or_diagnostics(native_client):
    client, _ = native_client
    request = {"subscription_url": "https://example.com/source", "target": "mihomo"}
    response = client.post("/render", json=request)
    assert response.status_code == 200
    assert "[General]" not in response.text
    assert "private-dns-token" not in response.text
    assert "source-token" not in response.text
    check = client.post("/check", json={**request, "publication_targets": ["surge"]})
    assert check.status_code == 200
    assert "private-dns-token" not in json.dumps(check.json())


def test_clash_source_cannot_impersonate_a_native_surge_profile(native_client):
    client, state = native_client
    state["source"] = json.dumps({
        "source-format": "surge", "_surge_source": SOURCE,
        "proxies": [{"name": "US-Clash", "type": "ss", "server": "clash.example.com",
                     "port": 443, "cipher": "aes-128-gcm", "password": "example"}],
    })
    response = client.post("/render", json={"subscription_url": "https://example.com/source", "target": "surge"})

    assert response.status_code == 200
    assert "US-Clash = ss, clash.example.com" in section(response.text, "Proxy")
    assert "private-dns-token" not in response.text


def test_generated_groups_do_not_rewire_native_proxy_chains(native_client):
    client, state = native_client
    state["source"] = """[Proxy]
US01 = ss, node.example.com, 443, encrypt-method=aes-128-gcm, password=example, underlying-proxy=默认代理
Relay = ss, relay.example.com, 443, encrypt-method=aes-128-gcm, password=example

[Proxy Group]
默认代理 = select, Relay
Subflow 默认代理 = select, Relay

[Rule]
FINAL,US01
"""
    response = client.post("/render", json={"subscription_url": "https://example.com/source", "target": "surge"})

    assert response.status_code == 200
    groups = section(response.text, "Proxy Group")
    assert "默认代理 = select, Relay\n" in groups + "\n"
    assert "Subflow 默认代理 = select, Relay\n" in groups + "\n"
    assert "Subflow 默认代理 2 = select," in groups
    assert "FINAL,Subflow 默认代理 2" in section(response.text, "Rule")
    assert section(response.text, "Proxy") == section(state["source"], "Proxy")


def test_native_routing_replacement_handles_commented_headers_and_bom(native_client):
    client, state = native_client
    state["source"] = "\ufeff" + SOURCE.replace("[Rule]", "[Rule] // airport routing").replace(
        "[Proxy Group]", "[Proxy Group] # airport groups")
    response = client.post("/render", json={"subscription_url": "https://example.com/source", "target": "surge"})

    assert response.status_code == 200
    assert "provider-old-rule.example" not in response.text
    assert "source-token" not in response.text
    assert response.text.count("[Rule]") == 1
    assert response.text.count("[Proxy Group]") == 1


def test_native_source_keeps_existing_normalized_node_choices_for_other_clients(native_client):
    client, _ = native_client
    response = client.post("/render", json={
        "subscription_url": "https://example.com/source", "target": "mihomo",
        "service_routes": [{"service": "openai", "mode": "fixed", "egress": "US 02"}],
    })
    assert response.status_code == 200
    assert "- US 02" in response.text


def test_policy_only_workspace_cannot_silently_discard_native_surge_settings(native_client):
    client, _ = native_client
    preview = client.post("/workspace/preview", json={
        "subscription_url": "https://example.com/source", "target": "surge",
    })
    assert preview.status_code == 200
    workspace = preview.json()["workspace"]
    response = client.post("/compile", json={"target": "surge", "workspace": workspace})

    assert response.status_code == 400
    assert "/render" in response.json()["detail"]
    assert "private-dns-token" not in response.text
    # Generic workspace metadata must not become a Mihomo setting.
    mihomo = client.post("/compile", json={"target": "mihomo", "workspace": workspace})
    assert mihomo.status_code == 200
    assert "native_source_format" not in mihomo.text


@pytest.mark.parametrize("name", ["默认代理", "AI  服务"])
def test_ambiguous_native_node_and_generated_group_names_fail_clearly(native_client, name):
    client, state = native_client
    state["source"] = f"[Proxy]\n{name} = ss, node.example.com, 443, encrypt-method=aes-128-gcm, password=example\n"
    response = client.post("/render", json={"subscription_url": "https://example.com/source", "target": "surge"})

    assert response.status_code == 400
    assert "名称冲突" in response.json()["detail"]


def test_colliding_normalized_aliases_cannot_silently_select_another_native_node(native_client):
    client, state = native_client
    state["source"] = "[Proxy]\n" + "\n".join(
        f"{name} = ss, node.example.com, {port}, encrypt-method=aes-128-gcm, password=example"
        for name, port in [("US A", 443), ("US  A", 444), ("US A-2", 445)]
    )
    response = client.post("/render", json={"subscription_url": "https://example.com/source", "target": "surge"})

    assert response.status_code == 400
    assert "名称冲突" in response.json()["detail"]
