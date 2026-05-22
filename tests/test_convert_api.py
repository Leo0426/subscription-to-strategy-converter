import json

import pytest
from fastapi.testclient import TestClient

from app.core.subconverter import SubconverterError
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


def test_convert_returns_full_yaml(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "mihomo"
    assert body["template"] == "powerfullz"
    assert body["node_count"] == 2
    assert "mixed-port: 7890" in body["config"]
    assert "proxy-groups:" in body["config"]
    assert "rule-providers:" in body["config"]
    assert "RULE-SET,ai,AI服务" in body["config"]
    assert "name: 香港 01" in body["config"]
    assert "name: 日本" in body["config"]


def test_convert_passes_subconverter_options(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        assert url == "https://example.com/sub"
        assert options is not None
        assert options.include == "香港|日本"
        assert options.exclude == "官网|流量"
        assert options.rename == "^香港@HK"
        assert options.emoji is True
        assert options.udp is True
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
            "subconverter": {
                "include": "香港|日本",
                "exclude": "官网|流量",
                "rename": "^香港@HK",
                "emoji": True,
                "udp": True,
            },
        },
    )

    assert response.status_code == 200


def test_subconverter_targets_endpoint_lists_converter_targets(client: TestClient) -> None:
    response = client.get("/subconverter/targets")

    assert response.status_code == 200
    targets = response.json()["targets"]
    ids = {item["id"] for item in targets}
    assert "mihomo" in ids
    assert "subconverter:clash" in ids
    assert "subconverter:quanx" in ids
    assert "subconverter:surge" in ids


def test_preview_returns_nodes(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.post(
        "/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["node_count"] == 2
    assert body["tree"]["label"] == "Mihomo 配置"
    assert [child["label"] for child in body["tree"]["children"]] == ["代理节点", "策略组", "规则", "Rule Providers"]
    # tree reflects the raw subscription structure (3 proxies before dedup/normalization)
    assert body["tree"]["children"][0]["meta"] == "3 个"


def test_subscribe_returns_yaml(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
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


def test_convert_uses_local_yaml_template(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": _LOCAL_TEMPLATE,
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    config = response.json()["config"]
    assert "proxy-groups:" in config
    assert "proxies:" in config
    assert "name: 香港 01" in config
    assert "name: 日本" in config


def test_convert_applies_custom_proxy_groups(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
            "custom_strategy": {
                "proxy_groups": [
                    {
                        "name": "Streaming",
                        "type": "url-test",
                        "proxies": [],
                        "url": "http://www.gstatic.com/generate_204",
                        "interval": 600,
                    },
                    {
                        "name": "Manual",
                        "type": "select",
                        "proxies": ["Streaming", "DIRECT"],
                    },
                ]
            },
        },
    )

    assert response.status_code == 200
    config = response.json()["config"]
    assert "name: Streaming" in config
    assert "type: url-test" in config
    assert "interval: 600" in config
    assert "name: Manual" in config
    assert "  - Streaming" in config
    assert "  - DIRECT" in config
    assert "name: 香港 01" in config
    assert "name: 日本" in config


def test_custom_include_all_group_preserves_filter(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
            "custom_strategy": {
                "proxy_groups": [
                    {
                        "name": "HK Auto",
                        "type": "url-test",
                        "include-all": True,
                        "filter": "香港|HK",
                        "exclude-filter": "官网|流量",
                    }
                ]
            },
        },
    )

    assert response.status_code == 200
    config = response.json()["config"]
    assert "name: HK Auto" in config
    assert "include-all: true" in config
    assert "filter: 香港|HK" in config
    assert "exclude-filter: 官网|流量" in config
    hk_section = config.split("name: HK Auto", 1)[1].split("rules:", 1)[0]
    assert "hk.example.com" not in hk_section


def test_selected_policy_expands_all_nodes_sentinel(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
            "selected_policy": {
                "proxy_groups": [
                    {
                        "name": "手动选择",
                        "type": "select",
                        "proxies": ["__ALL_NODES__", "DIRECT"],
                    }
                ]
            },
        },
    )

    assert response.status_code == 200
    config = response.json()["config"]
    manual_section = config.split("name: 手动选择", 1)[1].split("- name:", 1)[0]
    assert "香港 01" in manual_section
    assert "日本" in manual_section
    assert "DIRECT" in manual_section


def test_subscribe_accepts_encoded_custom_strategy(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
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


def test_convert_uses_powerfullz_template(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        assert options.full is True
        assert options.fakeip is True
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
            "powerfullz": {"full": True, "fakeip": True, "quic": False},
        },
    )

    assert response.status_code == 200
    config = response.json()["config"]
    assert "name: 选择代理" in config
    assert "include-all: true" in config
    assert "RULE-SET,ai,AI服务" in config
    assert "proxies:" in config
    assert "name: 香港 01" in config


def test_subscribe_accepts_powerfullz_options(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        assert options.loadbalance is True
        assert options.quic is True
        return POWERFULLZ_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
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


def test_illegal_url_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/convert",
        json={
            "subscription_url": "ftp://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
        },
    )

    assert response.status_code == 422


def test_private_ip_url_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/convert",
        json={
            "subscription_url": "http://127.0.0.1/sub",
            "template": "powerfullz",
            "target": "mihomo",
        },
    )

    assert response.status_code == 400
    assert "private or local IP" in response.json()["detail"]


def test_empty_subscription_returns_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        return "   "

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "subscription content is empty"


def test_subconverter_error_is_returned_as_400(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        raise SubconverterError("boom")

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)

    response = client.post(
        "/convert",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "powerfullz",
            "target": "mihomo",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "boom"


# ── Unsupported-protocol warning propagation ────────────────────────────────


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


def test_surge_unsupported_protocol_skipped_in_convert(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(url: str, options: object | None = None) -> str:
        return _MIXED_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return _SIMPLE_SURGE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)

    response = client.post(
        "/convert",
        json={"subscription_url": "https://example.com/sub", "template": "powerfullz", "target": "surge"},
    )

    assert response.status_code == 200
    body = response.json()
    assert "HK-SS = ss" in body["config"]
    assert "TUIC-NODE" not in body["config"]
    assert len(body["warnings"]) == 1
    assert body["warnings"][0]["code"] == "unsupported_protocol"
    assert body["warnings"][0]["value"] == "tuic"


def test_surge_unsupported_protocol_header_in_subscribe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(url: str, options: object | None = None) -> str:
        return _MIXED_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return _SIMPLE_SURGE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert)
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
