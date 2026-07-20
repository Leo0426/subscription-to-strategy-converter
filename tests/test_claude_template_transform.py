from fastapi.testclient import TestClient

from app.main import app


SUBSCRIPTION = """
proxies:
  - name: US-Stable
    type: ss
    server: us.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
  - name: JP-Backup
    type: trojan
    server: jp.example.com
    port: 443
    password: secret
"""

SUBSCRIPTION_WITH_UNSUPPORTED_SURGE_NODE = """
proxies:
  - name: HY2-Only
    type: hysteria2
    server: hy2.example.com
    port: 443
    password: secret
"""

SHARED_AI_TEMPLATE = "local:community_templates/leo/leo.yaml"
DEDICATED_TEMPLATE = SHARED_AI_TEMPLATE
MRS_TEMPLATE = SHARED_AI_TEMPLATE


def test_claude_template_catalog_exposes_only_existing_policies_and_surge_gate() -> None:
    response = TestClient(app).get("/claude/templates")

    assert response.status_code == 200
    templates = response.json()["templates"]
    assert templates
    assert all(item["claude"]["contains_claude"] for item in templates)

    list_template = next(item for item in templates if item["id"] == SHARED_AI_TEMPLATE)
    assert list_template["claude"]["surge_compatible"] is False
    assert list_template["claude"]["current_targets"] == ["AI 服务"]

    assert [item["id"] for item in templates] == [SHARED_AI_TEMPLATE]
    assert list_template["claude"]["surge_incompatibility_reasons"]


def test_workspace_customizes_only_existing_claude_policy_subgraph(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": SHARED_AI_TEMPLATE,
            "target": "clash",
            "claude_policy": {"enabled": True, "egress": "US-Stable"},
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    groups = {group["name"]: group for group in workspace["proxy_groups"]}
    assert groups["Claude"]["members"] == ["US-Stable", "AI 服务"]

    rules = workspace["rules"]
    claude_index = next(i for i, rule in enumerate(rules) if rule["match"] == "Claude / Domain")
    chatgpt_index = next(i for i, rule in enumerate(rules) if rule["match"] == "ChatGPT / Domain")
    assert claude_index > chatgpt_index
    assert rules[claude_index]["target"] == "Claude"
    assert rules[chatgpt_index]["target"] == "AI 服务"
    assert not any(rule["match"] == "api.anthropic.com" for rule in rules)

    provider = next(item for item in workspace["rule_providers"] if item["name"] == "Claude / Domain")
    assert provider["url"].endswith("/rule/Clash/Claude/Claude.list")


def test_workspace_accepts_platform_neutral_service_route(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": SHARED_AI_TEMPLATE,
            "target": "clash",
            "service_routes": [
                {"service": "claude", "egress": "US-Stable", "fallback": "JP-Backup"}
            ],
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    groups = {group["name"]: group for group in workspace["proxy_groups"]}
    assert groups["Claude"]["members"] == ["US-Stable", "JP-Backup"]


def test_workspace_rejects_unsupported_service_route(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": SHARED_AI_TEMPLATE,
            "target": "clash",
            "service_routes": [{"service": "openai", "egress": "US-Stable"}],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported service route: openai"


def test_workspace_rejects_duplicate_routes_for_same_service(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": SHARED_AI_TEMPLATE,
            "target": "clash",
            "service_routes": [
                {"service": "claude", "egress": "US-Stable"},
                {"service": "claude", "egress": "JP-Backup"},
            ],
        },
    )

    assert response.status_code == 422


def test_workspace_preserves_dedicated_claude_group_and_only_prioritizes_egress(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": DEDICATED_TEMPLATE,
            "target": "clash",
            "claude_policy": {"egress": "US-Stable"},
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    groups = [group for group in workspace["proxy_groups"] if "Claude" in group["name"]]
    assert len(groups) == 1
    assert groups[0]["members"][0] == "US-Stable"
    claude_rule = next(rule for rule in workspace["rules"] if rule["match"] == "Claude")
    assert claude_rule["target"] == groups[0]["name"]


def test_customization_rejects_non_leo_template(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "minimal",
            "target": "clash",
            "claude_policy": {"egress": "US-Stable"},
        },
    )

    assert response.status_code == 422
    assert "only leo.yaml template is supported" in response.text


def test_surge_generation_fails_closed_for_incompatible_claude_provider(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": MRS_TEMPLATE,
            "target": "surge",
            "claude_policy": {"egress": "US-Stable"},
        },
    )

    assert response.status_code == 400
    assert "selected template is not Surge-compatible" in response.text


def test_profile_uses_leo_claude_template(tmp_path, monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/sub",
            "template": MRS_TEMPLATE,
            "target": "clash",
            "claude_policy": {"egress": "US-Stable"},
        },
    )

    assert created.status_code == 201
    clash = client.get(created.json()["subscribe_urls"]["clash"])
    assert clash.status_code == 200
    assert "anthropic.mrs" in clash.text


def test_surge_claude_query_fails_closed_for_incompatible_leo_providers(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION_WITH_UNSUPPORTED_SURGE_NODE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).get(
        "/subscribe",
        params={
            "subscription_url": "https://example.com/sub",
                "template": MRS_TEMPLATE,
            "target": "surge",
            "claude": '{"enabled":true,"egress":"AI"}',
        },
    )

    assert response.status_code == 400
    assert "selected template is not Surge-compatible" in response.json()["detail"]
