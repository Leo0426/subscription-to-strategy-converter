from fastapi.testclient import TestClient

from app.core.subscription import SubscriptionError
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

CLAUDE_TEMPLATE = (
    "local:community_templates/THEYAMLS/General_Config/"
    "liandu2024/clash-fallback.yaml"
)


def test_subscription_preview_returns_nodes_for_egress_selection(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/preview",
        json={"subscription_url": "https://example.com/sub", "target": "clash"},
    )

    assert response.status_code == 200
    assert [node["name"] for node in response.json()["nodes"]] == ["US-Stable", "JP-Backup"]


def test_claude_egress_cannot_reference_template_claude_group(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "ai-tools",
            "target": "clash",
            "claude_policy": {"egress": "Claude"},
        },
    )

    assert response.status_code == 400
    assert "cannot reference" in response.json()["detail"]


def test_profile_stale_fallback_is_isolated_by_target(tmp_path, monkeypatch) -> None:
    upstream = {"available": True}

    async def fake_fetch_subscription(url: str) -> str:
        if not upstream["available"]:
            raise SubscriptionError("upstream unavailable")
        return SUBSCRIPTION

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/sub",
            "template": CLAUDE_TEMPLATE,
            "clash_template": CLAUDE_TEMPLATE,
            "surge_template": "ai-tools",
            "target": "clash",
            "claude_policy": {"egress": "US-Stable"},
        },
    ).json()

    fresh_clash = client.get(created["subscribe_urls"]["clash"])
    fresh_surge = client.get(created["subscribe_urls"]["surge"])
    upstream["available"] = False
    stale_clash = client.get(created["subscribe_urls"]["clash"])
    stale_surge = client.get(created["subscribe_urls"]["surge"])

    assert stale_clash.headers["X-Subflow-Stale"] == "true"
    assert stale_surge.headers["X-Subflow-Stale"] == "true"
    assert stale_clash.text == fresh_clash.text
    assert stale_surge.text == fresh_surge.text
    assert stale_clash.text != stale_surge.text
