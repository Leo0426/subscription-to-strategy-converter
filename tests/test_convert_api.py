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
_LOCAL_TEMPLATE = "local:community_templates/THEYAMLS/General_Config/666OS/OneTouch_Config.yaml"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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
            "template": "powerfullz",
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert "mixed-port: 7890" in response.text
    assert "RULE-SET,ai,AI服务" in response.text
    assert "name: 香港 01" in response.text


def test_templates_endpoint_lists_local_templates(client: TestClient) -> None:
    response = client.get("/templates")

    assert response.status_code == 200
    templates = response.json()["templates"]
    template_ids = {template["id"] for template in templates}
    assert "powerfullz" in template_ids
    assert "local:community_templates/THEYAMLS/General_Config/666OS/OneTouch_Config.yaml" in template_ids


def test_templates_endpoint_includes_proxy_group_count(client: TestClient) -> None:
    response = client.get("/templates")

    assert response.status_code == 200
    local_templates = [
        t for t in response.json()["templates"]
        if t["id"] == "local:community_templates/THEYAMLS/General_Config/666OS/OneTouch_Config.yaml"
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
    assert body["template"]["path"] == "community_templates/THEYAMLS/General_Config/666OS/OneTouch_Config.yaml"
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
            "template": "powerfullz",
            "target": "mihomo",
            "strategy": strategy,
        },
    )

    assert response.status_code == 200
    assert "name: Work" in response.text
    assert "  - 香港 01" in response.text
    assert "  - DIRECT" in response.text


def test_subscribe_accepts_powerfullz_options(
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
            "template": "powerfullz",
            "target": "mihomo",
            "powerfullz": json.dumps({"loadbalance": True, "quic": True}),
        },
    )

    assert response.status_code == 200
    assert "name: 选择代理" in response.text
    assert "MATCH,选择代理" in response.text


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


def test_surge_unsupported_protocol_header_in_subscribe(
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
        params={"subscription_url": "https://example.com/sub", "template": "powerfullz", "target": "surge"},
    )

    assert response.status_code == 200
    assert "HK-SS = ss" in response.text
    assert "TUIC-NODE" not in response.text
    assert "X-Compile-Warnings" in response.headers
    warnings = json.loads(response.headers["X-Compile-Warnings"])
    assert warnings[0]["code"] == "unsupported_protocol"
