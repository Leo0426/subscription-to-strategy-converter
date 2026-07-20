from fastapi.testclient import TestClient

from app.main import app


SUBSCRIPTION = """
proxies:
  - name: US-01
    type: ss
    server: us.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""


def test_rule_pack_catalog_exposes_groups_dependencies_and_concrete_rules() -> None:
    response = TestClient(app).get("/rule-packs")

    assert response.status_code == 200
    packs = {pack["id"]: pack for pack in response.json()["packs"]}
    assert {"claude", "openai", "github", "netflix", "youtube"} <= set(packs)
    assert packs["claude"]["category"] == "ai"
    assert packs["claude"]["group"]["name"] == "Claude"
    assert [group["name"] for group in packs["claude"]["dependencies"]] == ["AI"]
    assert "DOMAIN-SUFFIX,claude.ai,Claude" in packs["claude"]["rules"]
    assert packs["claude"]["rule_count"] == len(packs["claude"]["rules"])
    assert set(response.json()["preset_defaults"]["ai"]) >= {"claude", "openai", "gemini"}


def test_every_rule_pack_can_be_used_as_a_service_route(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "rule_packs": ["perplexity"],
            "route_intent": {
                "node_pools": [{"id": "us", "name": "美国", "regions": ["us"]}],
                "routes": [
                    {
                        "service": "perplexity",
                        "primary_pool": "us",
                        "final_target": "DIRECT",
                    }
                ],
            },
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    group = next(
        group for group in response.json()["workspace"]["proxy_groups"]
        if group["name"] == "Perplexity"
    )
    assert group["members"] == ["US-01", "DIRECT"]


def test_selected_rule_pack_cards_assemble_only_the_chosen_business_rules(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "general",
            "rule_packs": ["claude", "netflix"],
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    group_names = {group["name"] for group in workspace["proxy_groups"]}
    raw_rules = [rule["raw"] for rule in workspace["rules"]]
    assert {"AI", "Claude", "Streaming", "Netflix"} <= group_names
    assert "OpenAI" not in group_names
    assert "YouTube" not in group_names
    assert "DOMAIN-SUFFIX,claude.ai,Claude" in raw_rules
    assert "DOMAIN-SUFFIX,netflix.com,Netflix" in raw_rules
    assert all("openai.com" not in rule for rule in raw_rules)


def test_rule_pack_selection_rejects_unknown_card() -> None:
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "rule_packs": ["not-a-pack"],
            "target": "mihomo",
        },
    )

    assert response.status_code == 422
