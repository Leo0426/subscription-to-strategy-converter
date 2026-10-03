import json

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

import app.api.convert as convert_api
from app.main import app


CLASH_SUBSCRIPTION = """
proxies:
  - name: 香港  01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
  - name: 香港 01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
  - name: 日本
    type: trojan
    server: jp.example.com
    port: 443
    password: secret
"""


# A local community template present in the repository
_LOCAL_TEMPLATE = "local:community_templates/leo/leo.yaml"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_template_detail_exposes_leo_policy_and_rejects_other_templates(client: TestClient) -> None:
    response = client.get("/templates/detail")
    assert response.status_code == 200
    body = response.json()
    assert body["template"]["id"] == _LOCAL_TEMPLATE
    assert body["template"]["source"] == "local"
    assert body["template"]["path"] == "community_templates/leo/leo.yaml"
    groups = body["proxy_groups"]
    assert groups and all({"name", "type"} <= group.keys() for group in groups)
    assert body["summary"]["proxy_group_count"] == len(groups)
    assert "proxy-groups:" in body["yaml"]
    us_nodes = next(group for group in groups if group["name"] == "美国节点")
    assert us_nodes["type"] == "select"
    assert us_nodes["url"] == "https://cp.cloudflare.com/generate_204"
    assert us_nodes["expected-status"] == 204
    assert us_nodes["lazy"] is True
    assert not any(group["name"] == "AI自动" for group in groups)
    rejected = client.get("/templates/detail", params={"template": "minimal"})
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "only leo.yaml template is supported"


def test_preview_accepts_surge_subscription(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surge_subscription = """
#!MANAGED-CONFIG https://example.com/surge interval=86400

[General]
loglevel = notify

[Proxy]
HK-SS = ss, hk.example.com, 443, encrypt-method=aes-128-gcm, password=secret
JP-Trojan = trojan, jp.example.com, 443, password=secret, tls=true, sni=jp.example.com
"""

    async def fake_fetch_subscription(url: str) -> str:
        return surge_subscription

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)

    response = client.post(
        "/preview",
        json={"subscription_url": "https://example.com/subscribe/surge/"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["node_count"] == 2
    assert [(node["name"], node["type"]) for node in body["nodes"]] == [
        ("HK-SS", "ss"),
        ("JP-Trojan", "trojan"),
    ]


def test_preview_does_not_misclassify_arbitrary_non_yaml_as_surge(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return "an upstream HTML or plain-text error"

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)

    response = client.post(
        "/preview",
        json={"subscription_url": "https://example.com/subscribe/surge/"},
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("subscription returned unexpected content:")


def test_render_returns_yaml(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION


    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)

    response = client.post(
        "/render",
        json={
            "subscription_url": "https://example.com/sub",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert "mixed-port:" not in response.text
    assert "RULE-SET,Claude,AI 服务" in response.text
    assert "name: 香港 01" in response.text


def test_render_preserves_all_source_dns_instead_of_template_defaults(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clash_subscription = """
dns:
  enable: false
  enhanced-mode: redir-host
  fake-ip-range: 203.0.113.1/24
  nameserver:
    - https://traffic-dns.example/dns-query
  proxy-server-nameserver:
    - https://node-dns.example/dns-query/subscriber
proxies:
  - name: HK-01
    type: ss
    server: node.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

    async def fake_fetch_subscription(url: str) -> str:
        return clash_subscription

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)

    response = client.post(
        "/render",
        json={
            "subscription_url": "https://example.com/sub",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    config = YAML(typ="safe").load(response.text)
    assert config["dns"]["proxy-server-nameserver"] == [
        "https://node-dns.example/dns-query/subscriber"
    ]
    assert config["dns"]["enable"] is False
    assert config["dns"]["enhanced-mode"] == "redir-host"
    assert config["dns"]["fake-ip-range"] == "203.0.113.1/24"
    assert config["dns"]["nameserver"] == [
        "https://traffic-dns.example/dns-query",
    ]


def test_leo_public_data_endpoints_expose_source_rules_and_audit(client: TestClient) -> None:
    detail = client.get("/templates/detail", params={"template": _LOCAL_TEMPLATE})
    source = client.get("/templates/source")
    rules = client.get("/community/rules")
    audit = client.get("/templates/audit")

    assert detail.status_code == source.status_code == rules.status_code == audit.status_code == 200
    assert source.headers["content-type"].startswith("text/yaml")
    assert "rule-providers:" in source.text
    assert rules.json()["summary"]["provider_count"] == detail.json()["summary"]["rule_provider_count"]
    audit_body = audit.json()
    assert audit_body["summary"]["total"] == detail.json()["summary"]["rule_provider_count"]
    assert len(audit_body["sources"]) == audit_body["summary"]["total"]
    assert audit_body["quality_score"]["kind"] == "structural-v2"
    assert "supply_chain" in audit_body["quality_score"]["dimensions"]
    assert "cold_start_cost" in audit_body["quality_score"]["dimensions"]
    publication = audit_body["publication"]
    assert publication["template_current"] is (
        bool(publication["audited_template_sha256"])
        and publication["audited_template_sha256"] == publication["current_template_sha256"]
    )
    assert publication["template_current"] is True
    assert {item["href"] for item in detail.json()["public_data"]} == {
        "/templates/source",
        "/community/rules",
        "/templates/audit",
    }


def test_leo_audit_marks_matching_template_fingerprint_current(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    audit_path = tmp_path / "audit.json"
    report = json.loads(convert_api._LEO_AUDIT_PATH.read_text(encoding="utf-8"))
    report["template"] = {
        "sha256": convert_api.template_content_sha256(convert_api._LEO_SOURCE_PATH)
    }
    audit_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(convert_api, "_LEO_AUDIT_PATH", audit_path)

    response = client.get("/templates/audit")

    assert response.status_code == 200
    assert response.json()["publication"]["template_current"] is True


def test_leo_audit_marks_snapshot_stale_when_template_content_changes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    audit_path = tmp_path / "audit.json"
    source_path = tmp_path / "leo.yaml"
    report = json.loads(convert_api._LEO_AUDIT_PATH.read_text(encoding="utf-8"))
    report["template"] = {
        "sha256": convert_api.template_content_sha256(convert_api._LEO_SOURCE_PATH)
    }
    audit_path.write_text(json.dumps(report), encoding="utf-8")
    source_path.write_bytes(convert_api._LEO_SOURCE_PATH.read_bytes() + b"\n# changed\n")
    monkeypatch.setattr(convert_api, "_LEO_AUDIT_PATH", audit_path)
    monkeypatch.setattr(convert_api, "_LEO_SOURCE_PATH", source_path)

    response = client.get("/templates/audit")

    assert response.status_code == 200
    publication = response.json()["publication"]
    assert publication["template_current"] is False
    assert publication["audited_template_sha256"] != publication["current_template_sha256"]


def test_render_accepts_custom_strategy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION


    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    strategy = json.dumps(
        {
            "proxy_groups": [
                {
                    "name": "Work",
                    "type": "select",
                    "proxies": ["香港 01", "DIRECT"],
                }
            ]
        }
    )

    response = client.post(
        "/render",
        json={
            "subscription_url": "https://example.com/sub",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
            "custom_strategy": json.loads(strategy),
        },
    )

    assert response.status_code == 200
    assert "name: Work" in response.text
    assert "  - 香港  01" in response.text
    assert "  - DIRECT" in response.text


_MIXED_SUBSCRIPTION = """
proxies:
  - name: HK-SS
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
  - name: TUIC-NODE
    type: tuic
    server: t.example.com
    port: 443
    uuid: some-uuid
    password: pass
    alpn: [h3]
"""


def test_render_returns_surge_conf_for_leo(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(url: str) -> str:
        return _MIXED_SUBSCRIPTION


    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_convert)

    response = client.post(
        "/render",
        json={"subscription_url": "https://example.com/sub", "template": _LOCAL_TEMPLATE, "target": "surge"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"] == 'inline; filename="surge.conf"'
    assert "[General]" in response.text
    assert "[Host]" in response.text
    assert "hk.example.com = server:https://dns.alidns.com/dns-query" in response.text
    assert "[Proxy]" in response.text
    assert "HK-SS = ss" in response.text
    assert "wificalling.list" not in response.text
    assert "wildrift.yaml" not in response.text
    assert "qichiyuhub/rule/refs/heads/main/proxy.list" not in response.text
    assert "geo/geosite/claude.yaml" not in response.text


def test_surge_subscription_preserves_ss_obfuscation_in_surge_output(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surge_subscription = """
[Proxy]
TW01 = ss, tw.example.com, 443, encrypt-method=aes-128-gcm, password=secret, obfs=http, obfs-host=cdn.example.com
"""

    async def fake_fetch_subscription(url: str) -> str:
        return surge_subscription

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)

    response = client.post(
        "/render",
        json={
            "subscription_url": "https://example.com/subscribe/surge/",
            "template": _LOCAL_TEMPLATE,
            "target": "surge",
        },
    )

    assert response.status_code == 200
    assert "obfs=http" in response.text
    assert "obfs-host=cdn.example.com" in response.text


def test_surge_subscription_maps_ss_obfuscation_to_mihomo_plugin(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surge_subscription = """
[Proxy]
TW01 = ss, tw.example.com, 443, encrypt-method=aes-128-gcm, password=secret, obfs=http, obfs-host=cdn.example.com
"""

    async def fake_fetch_subscription(url: str) -> str:
        return surge_subscription

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)

    response = client.post(
        "/render",
        json={
            "subscription_url": "https://example.com/subscribe/surge/",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    assert "plugin: obfs" in response.text
    assert "mode: http" in response.text
    assert "host: cdn.example.com" in response.text


def test_clash_subscription_maps_ss_plugin_obfs_to_surge_output(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clash_subscription = """
proxies:
  - name: TW01
    type: ss
    server: tw.example.com
    port: 8801
    cipher: chacha20-ietf
    password: secret
    plugin: obfs
    plugin-opts:
      mode: http
      host: download.microsoft.com
"""

    async def fake_fetch_subscription(url: str) -> str:
        return clash_subscription

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)

    response = client.post(
        "/render",
        json={
            "subscription_url": "https://example.com/subscribe/clash/",
            "template": _LOCAL_TEMPLATE,
            "target": "surge",
        },
    )

    assert response.status_code == 200
    assert "TW01 = ss, tw.example.com, 8801" in response.text
    assert "obfs=http" in response.text
    assert "obfs-host=download.microsoft.com" in response.text


@pytest.mark.parametrize("source_format", ["surge", "clash"])
@pytest.mark.parametrize(
    ("host", "expected"),
    [("cdn.example.com:", "cdn.example.com"),
     ("cdn.example.com", "cdn.example.com"),
     ("cdn.example.com:8080", "cdn.example.com:8080"),
     ("[2001:db8::1]", "[2001:db8::1]")],
)
def test_mihomo_only_repairs_http_obfs_host_for_cross_format_sources(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    source_format: str,
    host: str,
    expected: str,
) -> None:
    if source_format == "surge":
        source = (
            "[Proxy]\nTW01 = ss, tw.example.com, 8801, "
            "encrypt-method=chacha20-ietf, password=secret, "
            f"obfs=http, obfs-host={host}\n"
        )
    else:
        source = json.dumps({"proxies": [{
            "name": "TW01", "type": "ss", "server": "tw.example.com",
            "port": 8801, "cipher": "chacha20-ietf", "password": "secret",
            "plugin": "obfs", "plugin-opts": {"mode": "http", "host": host},
        }]})

    async def fake_fetch_subscription(url: str) -> str:
        return source

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = client.post("/render", json={
        "subscription_url": "https://example.com/subscription",
        "template": _LOCAL_TEMPLATE, "target": "mihomo",
    })
    assert response.status_code == 200
    node = next(p for p in YAML(typ="safe").load(response.text)["proxies"] if p["name"] == "TW01")
    assert node["plugin-opts"] == {"mode": "http", "host": expected if source_format == "surge" else host}
