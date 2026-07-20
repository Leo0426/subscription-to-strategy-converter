from fastapi.testclient import TestClient

from app.main import app


LEO_TEMPLATE = "local:community_templates/leo/leo.yaml"


SUBSCRIPTION = """
proxies:
  - name: US-01
    type: ss
    server: us.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""


def test_product_catalog_exposes_one_base_template_and_five_policy_presets() -> None:
    response = TestClient(app).get("/presets")

    assert response.status_code == 200
    assert response.json()["base_template"] == {
        "id": LEO_TEMPLATE,
        "label": "Leo 大而全模板",
    }
    assert [preset["id"] for preset in response.json()["presets"]] == [
        "general",
        "ai",
        "streaming",
        "developer",
        "blank",
    ]
    assert all(preset["selected_policy"]["mode"] == "merge" for preset in response.json()["presets"])


def test_workspace_builds_ai_preset_on_the_canonical_base(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "ai",
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert any(group["name"] == "Claude" for group in workspace["proxy_groups"])
    assert any(rule["raw"] == "DOMAIN-SUFFIX,claude.ai,Claude" for rule in workspace["rules"])


def test_custom_policy_takes_ownership_after_preset_selection(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "ai",
            "target": "mihomo",
            "selected_policy": {
                "mode": "replace",
                "proxy_groups": [
                    {"name": "My Route", "type": "select", "proxies": ["US-01", "DIRECT"]}
                ],
                "rules": ["MATCH,My Route"],
            },
        },
    )

    assert response.status_code == 200
    workspace = response.json()["workspace"]
    assert [group["name"] for group in workspace["proxy_groups"]] == ["My Route"]
    assert [rule["raw"] for rule in workspace["rules"]] == ["MATCH,My Route"]


def test_profile_persists_preset_provenance_and_policy_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "ai",
            "target": "mihomo",
        },
    )

    draft = client.get(
        f"/profiles/{created.json()['id']}/draft",
        params={"token": created.json()["token"]},
    )
    profiles = client.get("/profiles")

    assert created.status_code == 201
    assert draft.json()["request"]["template"] == LEO_TEMPLATE
    assert draft.json()["request"]["preset"] == "ai"
    assert draft.json()["request"]["selected_policy"]["mode"] == "merge"
    assert profiles.json()["profiles"][0]["preset"] == "ai"


def test_every_product_preset_compiles_for_clash(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    client = TestClient(app)

    for preset in ("general", "ai", "streaming", "developer", "blank"):
        response = client.post(
            "/render",
            json={
                "subscription_url": "https://example.com/sub",
                "preset": preset,
                "target": "clash",
            },
        )
        assert response.status_code == 200, (preset, response.text)


def test_leo_base_can_be_used_without_a_scene_preset(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    response = TestClient(app).post(
        "/workspace/preview",
        json={
            "subscription_url": "https://example.com/sub",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
        },
    )

    assert response.status_code == 200
    assert any(group["name"] == "默认代理" for group in response.json()["workspace"]["proxy_groups"])


def test_ai_preset_profile_accepts_optional_claude_egress(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    response = TestClient(app).post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "ai",
            "target": "mihomo",
            "claude_policy": {"enabled": True, "egress": "US-01"},
        },
    )

    assert response.status_code == 201
