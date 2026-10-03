import pytest

from fastapi.testclient import TestClient

from app.core.template_policy_transform import analyze_claude_template
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


@pytest.fixture
def client(monkeypatch, tmp_path):
    async def fetch(_url):
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    with TestClient(app) as client:
        yield client


def test_claude_capability_rejects_mac_only_process_rules_for_surge_ios() -> None:
    config = {
        "proxy-groups": [{"name": "Claude", "type": "select", "proxies": ["DIRECT"]}],
        "rule-providers": {},
        "rules": ["PROCESS-NAME,Claude,Claude"],
    }

    capability = analyze_claude_template(config)

    assert capability.surge_compatible is False
    assert any(
        "PROCESS-NAME" in reason
        for reason in capability.surge_incompatibility_reasons
    )


def test_workspace_customizes_only_existing_claude_policy_subgraph(client) -> None:
    response = client.post(
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
    assert [group["name"] for group in workspace["proxy_groups"] if "Claude" in group["name"]] == ["Claude"]
    assert groups["Claude"]["members"] == ["US-Stable", "AI 服务"]

    rules = workspace["rules"]
    # The concrete Claude provider may change through ADR 0011 consolidation;
    # the transform must re-point whichever recognizable Claude rule remains
    # while leaving non-Claude AI rules untouched.
    claude_index = next(i for i, rule in enumerate(rules) if rule["match"] == "Claude")
    generic_ai_index = next(i for i, rule in enumerate(rules) if rule["match"] == "ai-4")
    assert claude_index < generic_ai_index
    assert rules[claude_index]["target"] == "Claude"
    assert rules[generic_ai_index]["target"] == "AI 服务"
    assert not any(rule["match"] == "api.anthropic.com" for rule in rules)

    provider = next(item for item in workspace["rule_providers"] if item["name"] == "Claude")
    assert "/rule/Clash/Claude/" in provider["url"]


def test_workspace_accepts_platform_neutral_service_route(client) -> None:
    response = client.post(
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


def test_workspace_rejects_unsupported_service_route(client) -> None:
    response = client.post(
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


def test_workspace_rejects_duplicate_routes_for_same_service(client) -> None:
    response = client.post(
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


def test_surge_generation_fails_closed_for_incompatible_claude_provider(client) -> None:
    response = client.post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": SHARED_AI_TEMPLATE,
            "target": "surge",
            "claude_policy": {"egress": "US-Stable"},
        },
    )

    assert response.status_code == 400
    assert "selected template is not Surge-compatible" in response.text


def test_profile_uses_leo_claude_template(client) -> None:
    created = client.post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/sub",
            "template": SHARED_AI_TEMPLATE,
            "target": "clash",
            "claude_policy": {"egress": "US-Stable"},
        },
    )

    assert created.status_code == 201
    clash = client.get(created.json()["subscribe_urls"]["clash"])
    assert clash.status_code == 200
    # The template must keep a recognizable Claude RuleProvider; the concrete
    # source URL may change through ADR 0011 consolidation.
    assert "RULE-SET,Claude" in clash.text
    assert "/Claude/" in clash.text


def test_surge_claude_render_fails_closed_for_incompatible_leo_providers(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION_WITH_UNSUPPORTED_SURGE_NODE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/render",
        json={
            "subscription_url": "https://example.com/sub",
            "template": SHARED_AI_TEMPLATE,
            "target": "surge",
            "claude_policy": {"enabled": True, "egress": "AI"},
        },
    )

    assert response.status_code == 400
    assert "selected template is not Surge-compatible" in response.json()["detail"]


def test_claude_egress_cannot_reference_template_claude_group(client) -> None:
    response = client.post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": SHARED_AI_TEMPLATE,
            "target": "clash",
            "claude_policy": {"egress": "Claude"},
        },
    )

    assert response.status_code == 400
    assert "cannot reference" in response.json()["detail"]
