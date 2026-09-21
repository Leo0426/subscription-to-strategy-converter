"""AnyTLS conversion through shared nodes, client compilers and publications."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.parsers.clash import clash_to_ir, ir_to_clash_dict
from app.core.parsers.surge import SurgeParseError, parse_surge_nodes
from app.core.platforms.surge import build_surge_config
from app.core.policy_workspace import config_to_workspace, workspace_from_dict, workspace_to_dict
from app.main import app


def source(**options):
    return {"name": "US-AnyTLS", "type": "anytls", "server": "proxy.example.com",
            "port": 443, "password": "test-only", **options}


def test_anytls_models_tls_and_password_without_changing_mihomo_fields():
    proxy = source(sni="edge.example.com", **{
        "udp": False, "skip-cert-verify": False, "client-fingerprint": "chrome",
        "fingerprint": "ab" * 32, "alpn": ["h2", "http/1.1"],
        "idle-session-check-interval": 30, "idle-session-timeout": 45,
        "min-idle-session": 0, "disable-reuse": True, "future-option": {"enabled": False},
    })
    original = deepcopy(proxy)
    node = clash_to_ir(proxy)
    assert node.tls.enabled is True
    assert node.extra["password"] == "test-only"
    assert node.extra["disable_reuse"] is True
    restored = workspace_from_dict(workspace_to_dict(config_to_workspace({"proxies": [proxy]}))).proxies[0]
    assert ir_to_clash_dict(restored) == original
    assert proxy == original


def test_anytls_ir_tls_edits_override_original_values():
    node = clash_to_ir(source(sni="original.example.com", **{
        "skip-cert-verify": True, "alpn": ["h2"], "fingerprint": "ab" * 32,
    }))
    node.tls.sni = ""
    node.tls.insecure = False
    node.tls.alpn = []
    node.tls.fingerprint = ""
    proxy = ir_to_clash_dict(node)
    assert proxy["sni"] == ""
    assert proxy["skip-cert-verify"] is False
    assert proxy["alpn"] == []
    assert proxy["fingerprint"] == ""


def test_anytls_workspace_from_older_versions_keeps_passthrough_password():
    workspace = workspace_to_dict(config_to_workspace({"proxies": [source()]}))
    proxy = workspace["proxies"][0]
    proxy["extra"] = {"_clash_passthrough": {"password": "old-workspace-password"}}
    proxy["tls"]["enabled"] = False
    restored = workspace_from_dict(workspace).proxies[0]
    assert ir_to_clash_dict(restored)["password"] == "old-workspace-password"
    conf, _ = build_surge_config([restored], [], ["MATCH,US-AnyTLS"], {})
    assert parse_surge_nodes(conf)[0].extra["password"] == "old-workspace-password"


@pytest.mark.parametrize("field", ("skip-cert-verify", "udp", "disable-reuse"))
def test_anytls_rejects_quoted_booleans_instead_of_coercing_security_settings(field):
    with pytest.raises(ValueError, match=field):
        clash_to_ir(source(**{field: "false"}))


def test_anytls_numeric_zero_password_is_preserved():
    node = clash_to_ir(source(password=0))
    assert ir_to_clash_dict(node)["password"] == "0"
    conf, _ = build_surge_config([node], [], ["MATCH,US-AnyTLS"], {})
    assert parse_surge_nodes(conf)[0].extra["password"] == "0"


@pytest.mark.parametrize("name", ("#US-AnyTLS", ";US-AnyTLS"))
def test_anytls_comment_prefixed_names_are_not_counted_as_emitted_nodes(name):
    conf, warnings = build_surge_config([clash_to_ir(source(name=name))], [], [f"MATCH,{name}"], {})
    assert any(w["code"] == "unsupported_node_options" and w["node"] == name for w in warnings)
    assert "FINAL,REJECT" in conf


def test_anytls_surge_import_preserves_auth_tls_reuse_and_udp():
    nodes = parse_surge_nodes('''[Proxy]
US-AnyTLS = anytls, proxy.example.com, 443, password="test,with=comma", reuse=false, sni=edge.example.com, skip-cert-verify=true, alpn="h2,http/1.1", server-cert-verify-name=cert.example.com, server-cert-fingerprint-sha256=''' + "ab" * 32)
    assert len(nodes) == 1
    node = nodes[0]
    assert node.tls.enabled is True
    assert node.extra["password"] == "test,with=comma"
    proxy = ir_to_clash_dict(node)
    assert proxy["type"] == "anytls"
    assert proxy["disable-reuse"] is True
    assert proxy["udp"] is True
    assert proxy["sni"] == "edge.example.com"
    assert proxy["skip-cert-verify"] is True
    assert proxy["alpn"] == ["h2", "http/1.1"]
    assert proxy["name-cert-verify"] == "cert.example.com"
    assert proxy["fingerprint"] == "ab" * 32
    assert "tls" not in proxy


@pytest.mark.parametrize("options", ("", "password=", "password=test-only, reuse=maybe",
                                    "password=test-only, sni=off", "password=test-only, client-cert=private-keystore"))
def test_anytls_surge_import_rejects_missing_auth_or_unrepresentable_options(options):
    with pytest.raises(SurgeParseError):
        parse_surge_nodes(f"[Proxy]\nUS-AnyTLS = anytls, proxy.example.com, 443, {options}")


def test_anytls_surge_export_roundtrips_quoted_password_and_tls_options():
    proxy = source(password='test,quote"back\\slash', sni="edge.example.com", **{
        "alpn": ["h2", "http/1.1"], "disable-reuse": True,
        "skip-cert-verify": True, "name-cert-verify": "cert.example.com", "fingerprint": "ab" * 32,
    })
    conf, warnings = build_surge_config([clash_to_ir(proxy)], [], ["MATCH,US-AnyTLS"], {})
    parsed = parse_surge_nodes(conf)
    assert len(parsed) == 1
    assert parsed[0].extra["password"] == proxy["password"]
    assert parsed[0].tls.sni == "edge.example.com"
    assert parsed[0].tls.alpn == ["h2", "http/1.1"]
    assert parsed[0].tls.insecure is True
    assert ir_to_clash_dict(parsed[0])["disable-reuse"] is True
    assert "FINAL,US-AnyTLS" in conf
    requirement = next(w for w in warnings if w["code"] == "client_version_requirement")
    assert requirement["minimum_versions"] == {"ios": "5.21.0", "mac": "6.8.0"}


@pytest.mark.parametrize("password", (' spaces at both ends ', 'test,"quotes"\\slash', '密码=值#;'))
def test_anytls_quoted_credentials_survive_surge_roundtrip(password):
    conf, _ = build_surge_config([clash_to_ir(source(password=password))], [], ["MATCH,US-AnyTLS"], {})
    assert parse_surge_nodes(conf)[0].extra["password"] == password


@pytest.mark.parametrize("options,minimum", (({}, {"ios": "5.17.0", "mac": "6.4.3"}),
                                           ({"alpn": ["h2"]}, {"ios": "5.20.0", "mac": "6.7.0"})))
def test_anytls_version_hint_tracks_the_options_actually_emitted(options, minimum):
    _, warnings = build_surge_config([clash_to_ir(source(**options))], [], ["MATCH,US-AnyTLS"], {})
    assert next(w for w in warnings if w["code"] == "client_version_requirement")["minimum_versions"] == minimum


def test_anytls_surge_warns_about_unmapped_tuning_without_exposing_values():
    proxy = source(**{"client-fingerprint": "chrome", "idle-session-timeout": 45,
                      "client-metadata": "private-value"})
    conf, warnings = build_surge_config([clash_to_ir(proxy)], [], ["MATCH,US-AnyTLS"], {})
    assert "US-AnyTLS = anytls," in conf
    warning = next(w for w in warnings if w["code"] == "ignored_node_options")
    assert set(warning["fields"]) == {"client-fingerprint", "idle-session-timeout", "client-metadata"}
    assert "test-only" not in json.dumps(warnings)
    assert "private-value" not in json.dumps(warnings)
    assert ir_to_clash_dict(clash_to_ir(proxy)) == proxy


@pytest.mark.parametrize("option", ({"ech-opts": {"enable": True, "config": "private-config"}},
                                   {"private-key": "private-key-value"}, {"dialer-proxy": "Private-Chain"}))
def test_anytls_unsupported_security_options_skip_only_the_affected_node(option):
    good = clash_to_ir(source(name="US-Good"))
    bad = clash_to_ir(source(name="US-Bad", **option))
    conf, warnings = build_surge_config(
        [good, bad], [{"name": "AI", "type": "select", "proxies": ["US-Bad"]}],
        ["DOMAIN,api.anthropic.com,AI", "MATCH,US-Good"], {},
    )
    assert "US-Good = anytls," in conf
    assert "US-Bad =" not in conf
    assert "DOMAIN,api.anthropic.com,REJECT" in conf
    assert "FINAL,US-Good" in conf
    warning = next(w for w in warnings if w["code"] == "unsupported_node_options")
    assert warning["node"] == "US-Bad"
    assert "private-config" not in json.dumps(warnings)
    assert "private-key-value" not in json.dumps(warnings)


@pytest.fixture
def client(tmp_path, monkeypatch):
    state = {"content": ""}
    async def fetch(_url, **_kwargs):
        return state["content"]
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app) as result:
        yield result, state


@pytest.mark.parametrize("input_format", ("mihomo", "surge"))
def test_anytls_only_profile_keeps_fixed_ai_nodes_in_all_clients(client, input_format):
    client, state = client
    state["content"] = ("proxies:\n  - " + json.dumps(source(sni="edge.example.com"))) if input_format == "mihomo" else (
        "[Proxy]\nUS-AnyTLS = anytls, proxy.example.com, 443, password=test-only, sni=edge.example.com, reuse=false")
    request = {"subscription_url": "https://example.com/sub", "publication_targets": ["mihomo", "surge", "shadowrocket"],
               "service_routes": [{"service": s, "mode": "fixed", "egress": "US-AnyTLS"} for s in ("claude", "openai")]}
    checked = client.post("/check", json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is True, checked.text
    created = client.post("/profiles", json=request)
    assert created.status_code == 201, created.text
    links = created.json()
    for target in ("clash", "surge", "shadowrocket"):
        response = client.get(links["subscribe_urls"][target])
        assert response.status_code == 200, response.text
        if target == "surge":
            assert "US-AnyTLS = anytls," in response.text
            assert "Claude = select, US-AnyTLS" in response.text
            assert "OpenAI = select, US-AnyTLS" in response.text
            assert "DOMAIN-SUFFIX,claude.com,Claude" in response.text
        elif target == "shadowrocket":
            assert response.text == state["content"]
        else:
            proxy = YAML(typ="safe").load(response.text)["proxies"][0]
            assert proxy["type"] == "anytls"
            assert proxy["password"] == "test-only"
            if input_format == "surge":
                assert proxy["disable-reuse"] is True
    paired = client.get(links["config_urls"]["shadowrocket"])
    assert paired.status_code == 200
    assert "Claude = select, US-AnyTLS" in paired.text


def test_fixed_incompatible_anytls_node_blocks_publication_but_compatible_peer_does_not(client):
    client, state = client
    state["content"] = "proxies:\n" + "".join("  - " + json.dumps(p) + "\n" for p in [
        source(name="US-Good"), source(name="US-Bad", **{"ech-opts": {"enable": True}})])
    request = {"subscription_url": "https://example.com/sub", "publication_targets": ["surge"],
               "service_routes": [{"service": "claude", "mode": "fixed", "egress": "US-Bad"}]}
    result = client.post("/check", json=request)
    assert result.status_code == 200
    assert result.json()["can_publish"] is False
    assert client.post("/profiles", json=request).status_code == 400
    assert client.post("/render", json={**request, "target": "surge"}).status_code == 400
    request["service_routes"][0]["egress"] = "US-Good"
    assert client.post("/check", json=request).json()["can_publish"] is True


def test_anytls_invalid_source_boolean_returns_a_redacted_client_error(client):
    client, state = client
    state["content"] = "proxies:\n  - " + json.dumps(source(**{"skip-cert-verify": "false"}))
    response = client.post("/render", json={"subscription_url": "https://example.com/sub"})
    assert response.status_code == 400
    assert "skip-cert-verify" in response.text
    assert "test-only" not in response.text


@pytest.mark.parametrize("endpoint", ("/compile", "/simulate"))
def test_anytls_invalid_workspace_boolean_returns_a_redacted_client_error(client, endpoint):
    client, _ = client
    workspace = {"proxies": [source(**{"skip-cert-verify": "false"})]}
    response = client.post(endpoint, json={"workspace": workspace, "destination": "claude.ai"})
    assert response.status_code == 400
    assert "skip-cert-verify" in response.text
    assert "test-only" not in response.text


def test_anytls_profile_refresh_rejects_new_incompatible_options_on_fixed_node(client):
    client, state = client
    state["content"] = "proxies:\n  - " + json.dumps(source())
    request = {"subscription_url": "https://example.com/sub", "publication_targets": ["surge"],
               "service_routes": [{"service": "claude", "mode": "fixed", "egress": "US-AnyTLS"}]}
    created = client.post("/profiles", json=request)
    assert created.status_code == 201
    url = created.json()["subscribe_urls"]["surge"]
    assert client.get(url).status_code == 200
    state["content"] = "proxies:\n  - " + json.dumps(source(**{"ech-opts": {"enable": True}}))
    refreshed = client.get(url + "&force_refresh=true")
    assert refreshed.status_code == 400
    assert "X-Subflow-Stale" not in refreshed.headers
