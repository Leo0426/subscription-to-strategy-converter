from fastapi.testclient import TestClient

from app.main import app


SUBSCRIPTION = """
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
  - name: JP-01
    type: ss
    server: jp.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""


def test_user_can_route_claude_through_a_filtered_node_pool(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": "local:community_templates/leo/leo.yaml",
            "preset": "ai",
            "target": "mihomo",
            "route_intent": {
                "node_pools": [
                    {
                        "id": "us-stable",
                        "name": "美国稳定节点",
                        "regions": ["us"],
                        "exclude_keywords": ["Expired"],
                        "protocols": ["ss"],
                    }
                ],
                "routes": [
                    {
                        "service": "claude",
                        "primary_pool": "us-stable",
                        "final_target": "DIRECT",
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    claude = next(group for group in workspace["proxy_groups"] if group["name"] == "Claude")
    assert claude["members"] == ["US-New-01", "DIRECT"]
    assert any(rule["raw"] == "DOMAIN-SUFFIX,claude.ai,Claude" for rule in workspace["rules"])


def test_service_route_can_fallback_to_a_second_node_pool(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "ai",
            "target": "mihomo",
            "route_intent": {
                "node_pools": [
                    {"id": "us", "name": "美国", "regions": ["us"], "exclude_keywords": ["Expired"]},
                    {"id": "jp", "name": "日本", "regions": ["jp"]},
                ],
                "routes": [
                    {
                        "service": "claude",
                        "primary_pool": "us",
                        "fallback_pool": "jp",
                        "final_target": "DIRECT",
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    claude = next(
        group for group in response.json()["workspace"]["proxy_groups"] if group["name"] == "Claude"
    )
    assert claude["members"] == ["US-New-01", "JP-01", "DIRECT"]


def test_route_intent_rejects_unknown_node_pool() -> None:
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "ai",
            "target": "mihomo",
            "route_intent": {
                "node_pools": [],
                "routes": [{"service": "claude", "primary_pool": "missing"}],
            },
        },
    )

    assert response.status_code == 422


def test_intent_catalog_exposes_supported_services_and_regions() -> None:
    response = TestClient(app).get("/intent/catalog")

    assert response.status_code == 200
    assert {service["id"] for service in response.json()["services"]} >= {
        "claude",
        "openai",
        "gemini",
        "netflix",
        "youtube",
        "github",
    }
    assert {region["id"] for region in response.json()["regions"]} >= {"hk", "us", "jp", "sg"}


def test_route_intent_can_add_a_service_not_present_in_the_preset(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "general",
            "target": "mihomo",
            "route_intent": {
                "node_pools": [{"id": "jp", "name": "日本", "regions": ["jp"]}],
                "routes": [
                    {
                        "service": "netflix",
                        "primary_pool": "jp",
                        "final_target": "DIRECT",
                    }
                ],
            },
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    netflix = next(group for group in workspace["proxy_groups"] if group["name"] == "Netflix")
    assert netflix["members"] == ["JP-01", "DIRECT"]
    assert workspace["rules"][0]["raw"] == "DOMAIN-SUFFIX,netflix.com,Netflix"
