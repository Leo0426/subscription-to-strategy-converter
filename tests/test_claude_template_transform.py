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

SHARED_AI_TEMPLATE = (
    "local:community_templates/THEYAMLS/General_Config/"
    "liandu2024/clash-fallback.yaml"
)
DEDICATED_TEMPLATE = (
    "local:community_templates/THEYAMLS/General_Config/fufu/ConfigForClash.yaml"
)
MRS_TEMPLATE = "local:community_templates/THEYAMLS/General_Config/wanswu/config.yaml"


def test_claude_template_catalog_exposes_only_existing_policies_and_surge_gate() -> None:
    response = TestClient(app).get("/claude/templates")

    assert response.status_code == 200
    templates = response.json()["templates"]
    assert templates
    assert all(item["claude"]["contains_claude"] for item in templates)

    list_template = next(item for item in templates if item["id"] == SHARED_AI_TEMPLATE)
    assert list_template["claude"]["surge_compatible"] is False
    assert list_template["claude"]["current_targets"] == ["AI"]

    inline_template = next(item for item in templates if item["id"] == "ai-tools")
    assert inline_template["claude"]["surge_compatible"] is True

    mrs_template = next(
        item
        for item in templates
        if item["id"].endswith("wanswu/config.yaml")
    )
    assert mrs_template["claude"]["surge_compatible"] is False
    assert mrs_template["claude"]["surge_incompatibility_reasons"]


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
    assert groups["Claude"]["members"] == ["US-Stable", "AI"]

    rules = workspace["rules"]
    claude_index = next(i for i, rule in enumerate(rules) if rule["match"] == "Claude / Domain")
    chatgpt_index = next(i for i, rule in enumerate(rules) if rule["match"] == "ChatGPT / Domain")
    assert claude_index == chatgpt_index + 1
    assert rules[claude_index]["target"] == "Claude"
    assert rules[chatgpt_index]["target"] == "AI"
    assert not any(rule["match"] == "api.anthropic.com" for rule in rules)

    provider = next(item for item in workspace["rule_providers"] if item["name"] == "Claude / Domain")
    assert provider["url"].endswith("/rule/Clash/Claude/Claude.list")


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


def test_customization_rejects_template_without_claude_policy(monkeypatch) -> None:
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

    assert response.status_code == 400
    assert "does not contain" in response.json()["detail"]


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
    assert "not Surge-compatible" in response.json()["detail"]
    assert ".mrs" in response.json()["detail"]


def test_profile_uses_target_specific_claude_templates(tmp_path, monkeypatch) -> None:
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
            "clash_template": MRS_TEMPLATE,
            "surge_template": "ai-tools",
            "target": "clash",
            "claude_policy": {"egress": "US-Stable"},
        },
    )

    assert created.status_code == 201
    clash = client.get(created.json()["subscribe_urls"]["clash"])
    surge = client.get(created.json()["subscribe_urls"]["surge"])
    assert clash.status_code == 200
    assert "anthropic.mrs" in clash.text
    assert surge.status_code == 200
    assert "DOMAIN-SUFFIX,anthropic.com,Claude" in surge.text
    assert "anthropic.mrs" not in surge.text


def test_surge_claude_generation_rejects_unsupported_node_protocol(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION_WITH_UNSUPPORTED_SURGE_NODE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).get(
        "/subscribe",
        params={
            "subscription_url": "https://example.com/sub",
            "template": "ai-tools",
            "target": "surge",
            "claude": '{"enabled":true,"egress":"AI"}',
        },
    )

    assert response.status_code == 400
    assert "unsupported node protocols" in response.json()["detail"]
    assert "hysteria2" in response.json()["detail"]
