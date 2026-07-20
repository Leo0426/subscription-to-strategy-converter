import json

import pytest
from fastapi.testclient import TestClient

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

POWERFULLZ_TEMPLATE = {
    "mixed-port": 7890,
    "proxy-groups": [
        {
            "name": "选择代理",
            "type": "select",
            "proxies": ["自动选择", "手动选择", "DIRECT"],
        },
        {
            "name": "自动选择",
            "type": "url-test",
            "include-all": True,
            "filter": "香港|日本",
            "url": "https://cp.cloudflare.com/generate_204",
            "interval": 60,
        },
    ],
    "rule-providers": {
        "ai": {
            "type": "http",
            "behavior": "classical",
            "url": "https://cdn.jsdelivr.net/gh/powerfullz/override-rules@release/rules/ai.yaml",
            "path": "./ruleset/ai.yaml",
            "interval": 86400,
        }
    },
    "rules": ["RULE-SET,ai,AI服务", "MATCH,选择代理"],
}

# A local community template present in the repository
_LOCAL_TEMPLATE = "local:community_templates/leo/leo.yaml"


def test_templates_endpoint_exposes_only_leo_template(client: TestClient) -> None:
    response = client.get("/templates")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["templates"]] == [_LOCAL_TEMPLATE]


def test_template_detail_defaults_to_leo_and_rejects_other_templates(client: TestClient) -> None:
    default_response = client.get("/templates/detail")
    rejected_response = client.get("/templates/detail", params={"template": "minimal"})

    assert default_response.status_code == 200
    assert default_response.json()["template"]["id"] == _LOCAL_TEMPLATE
    assert rejected_response.status_code == 400
    assert rejected_response.json()["detail"] == "only leo.yaml template is supported"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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


def test_subscribe_returns_yaml(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.get(
        "/subscribe",
        params={
            "subscription_url": "https://example.com/sub",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert "mixed-port: 7890" in response.text
    assert "RULE-SET,Claude / Domain,AI 服务" in response.text
    assert "name: 香港 01" in response.text


def test_templates_endpoint_lists_local_templates(client: TestClient) -> None:
    response = client.get("/templates")

    assert response.status_code == 200
    templates = response.json()["templates"]
    template_ids = {template["id"] for template in templates}
    assert template_ids == {_LOCAL_TEMPLATE}


def test_templates_endpoint_includes_proxy_group_count(client: TestClient) -> None:
    response = client.get("/templates")

    assert response.status_code == 200
    local_templates = [
        t for t in response.json()["templates"]
        if t["id"] == _LOCAL_TEMPLATE
    ]
    assert local_templates
    assert local_templates[0]["proxy_group_count"] > 0


def test_template_detail_returns_summary_and_yaml(client: TestClient) -> None:
    response = client.get("/templates/detail", params={"template": _LOCAL_TEMPLATE})

    assert response.status_code == 200
    body = response.json()
    assert body["template"]["id"] == _LOCAL_TEMPLATE
    assert body["summary"]["proxy_group_count"] > 0
    assert "proxy-groups:" in body["yaml"]


def test_template_detail_returns_proxy_groups(client: TestClient) -> None:
    response = client.get("/templates/detail", params={"template": _LOCAL_TEMPLATE})

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["proxy_groups"], list)
    assert len(body["proxy_groups"]) > 0
    first = body["proxy_groups"][0]
    assert "name" in first
    assert "type" in first


def test_local_template_detail_returns_source_path(client: TestClient) -> None:
    response = client.get(
        "/templates/detail",
        params={"template": _LOCAL_TEMPLATE},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["template"]["source"] == "local"
    assert body["template"]["path"] == "community_templates/leo/leo.yaml"
    assert body["summary"]["proxy_group_count"] > 0
    assert "proxy-groups:" in body["yaml"]


def test_subscribe_accepts_encoded_custom_strategy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
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

    response = client.get(
        "/subscribe",
        params={
            "subscription_url": "https://example.com/sub",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
            "strategy": strategy,
        },
    )

    assert response.status_code == 200
    assert "name: Work" in response.text
    assert "  - 香港 01" in response.text
    assert "  - DIRECT" in response.text


def test_subscribe_rejects_powerfullz_options(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        assert options.loadbalance is True
        assert options.quic is True
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.get(
        "/subscribe",
        params={
            "subscription_url": "https://example.com/sub",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
            "powerfullz": json.dumps({"loadbalance": True, "quic": True}),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "powerfullz options are not supported with leo.yaml"


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

_SIMPLE_SURGE_TEMPLATE = {
    "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": []}],
    "rules": ["MATCH,PROXY"],
}


def test_subscribe_returns_surge_conf_for_leo(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(url: str) -> str:
        return _MIXED_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return _SIMPLE_SURGE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_convert)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.get(
        "/subscribe",
        params={"subscription_url": "https://example.com/sub", "template": _LOCAL_TEMPLATE, "target": "surge"},
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

    response = client.get(
        "/subscribe",
        params={
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

    response = client.get(
        "/subscribe",
        params={
            "subscription_url": "https://example.com/subscribe/surge/",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    assert "plugin: obfs" in response.text
    assert "mode: http" in response.text
    assert "host: cdn.example.com" in response.text
