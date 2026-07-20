from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


LEO_TEMPLATE = "local:community_templates/leo/leo.yaml"


def test_workspace_rejects_non_leo_template() -> None:
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "minimal",
            "target": "mihomo",
        },
    )

    assert response.status_code == 422
    assert "only leo.yaml template is supported" in response.text


def test_render_rejects_singbox_for_leo() -> None:
    response = TestClient(app).post(
        "/render",
        json={
            "subscription_url": "https://example.com/sub",
            "target": "singbox",
        },
    )

    assert response.status_code == 422
    assert "leo.yaml only supports Clash/Mihomo and Surge" in response.text


CLASH_SUBSCRIPTION = """
proxies:
  - name: HK-01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

MULTI_NODE_SUBSCRIPTION = """
proxies:
  - name: US-New-01
    type: ss
    server: us1.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
  - name: US-Expired
    type: ss
    server: us2.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
  - name: US-Trojan
    type: trojan
    server: us3.example.com
    port: 443
    password: secret
  - name: JP-01
    type: ss
    server: jp.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

SIMPLE_TEMPLATE = {
    "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": []}],
    "rules": ["MATCH,PROXY"],
}

AI_TEMPLATE = {
    "proxy-groups": [
        {"name": "PROXY", "type": "select", "proxies": []},
        {"name": "AI", "type": "select", "proxies": ["PROXY", "DIRECT"]},
    ],
    "rules": ["DOMAIN-SUFFIX,openai.com,AI", "MATCH,PROXY"],
}


def test_workspace_preview_returns_workspace_graph_and_findings(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)

    response = client.post(
        "/workspace/preview",
        json={"subscription_url": "https://example.com/sub", "template": LEO_TEMPLATE, "target": "mihomo"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["node_count"] == 1
    assert body["workspace"]["target"] == "mihomo"
    assert body["workspace"]["proxies"][0]["name"] == "HK-01"
    assert body["graph"]["nodes"]
    assert isinstance(body["findings"], list)


def test_workspace_preview_can_replace_all_policy_sections(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "mode": "replace",
                "proxy_groups": [
                    {"name": "CUSTOM", "type": "select", "proxies": ["HK-01", "DIRECT"]}
                ],
                "rule_providers": {
                    "custom": {
                        "type": "http",
                        "behavior": "classical",
                        "format": "text",
                        "url": "https://rules.example.com/custom.list",
                    }
                },
                "rules": ["RULE-SET,custom,CUSTOM", "MATCH,DIRECT"],
            },
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert [group["name"] for group in workspace["proxy_groups"]] == ["CUSTOM"]
    assert [provider["name"] for provider in workspace["rule_providers"]] == ["custom"]
    assert [rule["raw"] for rule in workspace["rules"]] == [
        "RULE-SET,custom,CUSTOM",
        "MATCH,DIRECT",
    ]


def test_workspace_expands_dynamic_node_selector_into_group_members(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return MULTI_NODE_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "mode": "replace",
                "node_selectors": [
                    {
                        "id": "us-stable",
                        "name_regex": "^US-",
                        "exclude_regex": "Expired",
                        "protocols": ["ss"],
                    }
                ],
                "proxy_groups": [
                    {
                        "name": "US Stable",
                        "type": "select",
                        "proxies": ["selector:us-stable", "DIRECT"],
                    }
                ],
                "rules": ["MATCH,US Stable"],
            },
        },
    )

    assert response.status_code == 200
    group = response.json()["workspace"]["proxy_groups"][0]
    assert group["members"] == ["US-New-01", "DIRECT"]


def test_workspace_reports_selector_that_produces_empty_group(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "mode": "replace",
                "node_selectors": [{"id": "missing", "name_regex": "^ZZ-"}],
                "proxy_groups": [
                    {"name": "Unavailable", "type": "select", "proxies": ["selector:missing"]}
                ],
                "rules": ["MATCH,Unavailable"],
            },
        },
    )

    assert response.status_code == 200
    empty = [item for item in response.json()["findings"] if item["code"] == "empty_group"]
    assert [item["ref"] for item in empty] == ["Unavailable"]
    assert empty[0]["severity"] == "error"


def test_workspace_rejects_unknown_node_selector_reference(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "mode": "replace",
                "proxy_groups": [
                    {"name": "Broken", "type": "select", "proxies": ["selector:not-defined"]}
                ],
                "rules": ["MATCH,Broken"],
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unknown node selector: not-defined"


def test_workspace_rejects_invalid_node_selector_regex() -> None:
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "node_selectors": [{"id": "invalid", "name_regex": "["}],
            },
        },
    )

    assert response.status_code == 422


def test_workspace_rejects_duplicate_node_selector_ids() -> None:
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "node_selectors": [
                    {"id": "stable", "name_regex": "^US-"},
                    {"id": "stable", "name_regex": "^JP-"},
                ],
            },
        },
    )

    assert response.status_code == 422


def test_render_accepts_structured_policy_in_request_body(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    response = TestClient(app).post(
        "/render",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "clash",
            "selected_policy": {
                "mode": "replace",
                "proxy_groups": [{"name": "CUSTOM", "type": "select", "proxies": ["HK-01"]}],
                "rules": ["MATCH,CUSTOM"],
            },
        },
    )

    assert response.status_code == 200
    assert "name: CUSTOM" in response.text
    assert "MATCH,CUSTOM" in response.text


def test_workspace_reports_rules_after_terminal_match_as_unreachable(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "mode": "replace",
                "proxy_groups": [{"name": "PROXY", "type": "select", "proxies": ["HK-01"]}],
                "rules": ["MATCH,PROXY", "DOMAIN-SUFFIX,example.com,DIRECT"],
            },
        },
    )

    assert response.status_code == 200
    unreachable = [item for item in response.json()["findings"] if item["code"] == "unreachable_rule"]
    assert [item["path"] for item in unreachable] == ["rules[1]"]


def test_simulate_endpoint_traces_openai_rule(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return AI_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)
    preview = client.post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "mode": "replace",
                "proxy_groups": AI_TEMPLATE["proxy-groups"],
                "rules": AI_TEMPLATE["rules"],
            },
        },
    ).json()

    response = client.post(
        "/simulate",
        json={"workspace": preview["workspace"], "destination": "chat.openai.com"},
    )

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace["matched_rule"]["type"] == "DOMAIN-SUFFIX"
    assert trace["matched_rule"]["target"] == "AI"
    assert trace["resolved"] in {"HK-01", "DIRECT"}


def test_compile_mihomo_endpoint_returns_yaml(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)
    preview = client.post(
        "/workspace/preview",
        json={"subscription_url": "https://example.com/sub", "template": LEO_TEMPLATE, "target": "mihomo"},
    ).json()

    response = client.post("/compile", json={"workspace": preview["workspace"], "target": "mihomo"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert "proxies:" in response.text
    assert "proxy-groups:" in response.text
    assert "rules:" in response.text
    assert "name: HK-01" in response.text
