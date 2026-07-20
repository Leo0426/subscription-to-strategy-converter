from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.profiles import ProfileStore
from app.core.subscription import SubscriptionError
from app.main import app


LEO_TEMPLATE = "local:community_templates/leo/leo.yaml"


def test_profile_store_persists_profile_without_plaintext_token(tmp_path) -> None:
    database = tmp_path / "subflow.db"
    created = ProfileStore(database).create({"subscription_url": "https://example.com/sub", "target": "mihomo"})

    reopened = ProfileStore(database)

    assert reopened.get(created.id, created.token).request == {
        "subscription_url": "https://example.com/sub",
        "target": "mihomo",
    }
    assert reopened.get(created.id, "wrong-token") is None
    assert created.token not in database.read_bytes().decode("utf-8", errors="ignore")
    assert database.stat().st_mode & 0o777 == 0o600


def test_created_profile_provides_stable_mihomo_subscription(tmp_path, monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return """
proxies:
  - name: HK-01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

    async def fake_load_powerfullz_template(options: object) -> dict:
        return {
            "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["DIRECT"]}],
            "rules": ["MATCH,PROXY"],
        }

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)

    created = client.post(
        "/profiles",
        json={"subscription_url": "https://example.com/private-token", "template": LEO_TEMPLATE, "target": "mihomo"},
    )
    response = client.get(created.json()["subscribe_url"])
    surge = client.get(created.json()["subscribe_urls"]["surge"])

    assert created.status_code == 201
    assert "example.com/private-token" not in created.json()["subscribe_url"]
    assert response.status_code == 200
    assert "name: HK-01" in response.text
    assert surge.status_code == 200
    assert surge.headers["content-disposition"] == 'inline; filename="surge.conf"'
    assert "[General]" in surge.text
    assert "HK-01 = ss" in surge.text


def test_profile_creation_provides_surge_subscription_for_leo(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)

    response = client.post(
        "/profiles",
        json={"subscription_url": "https://example.com/sub", "template": LEO_TEMPLATE, "target": "surge"},
    )

    assert response.status_code == 201
    assert response.json()["subscribe_urls"]["clash"].endswith("&target=clash")
    assert response.json()["subscribe_urls"]["surge"].endswith("&target=surge")


def test_profile_subscription_falls_back_to_last_successful_artifact(tmp_path, monkeypatch) -> None:
    upstream = {"available": True}

    async def fake_fetch_subscription(url: str) -> str:
        if not upstream["available"]:
            raise SubscriptionError("upstream unavailable")
        return """
proxies:
  - name: HK-01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

    async def fake_load_powerfullz_template(options: object) -> dict:
        return {"proxy-groups": [], "rules": ["MATCH,DIRECT"]}

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={"subscription_url": "https://example.com/sub", "template": LEO_TEMPLATE, "target": "mihomo"},
    ).json()
    fresh = client.get(created["subscribe_url"])

    upstream["available"] = False
    stale = client.get(created["subscribe_url"])

    assert stale.status_code == 200
    assert stale.text == fresh.text
    assert stale.headers["X-Subflow-Stale"] == "true"


def test_profile_re_evaluates_node_selectors_when_upstream_nodes_change(tmp_path, monkeypatch) -> None:
    upstream = {"name": "US-Old"}

    async def fake_fetch_subscription(url: str) -> str:
        return f"""
proxies:
  - name: {upstream["name"]}
    type: ss
    server: us.example.com
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

    async def fake_load_powerfullz_template(options: object) -> dict:
        return {"proxy-groups": [], "rules": ["MATCH,DIRECT"]}

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/sub",
                "template": LEO_TEMPLATE,
            "target": "mihomo",
            "selected_policy": {
                "mode": "replace",
                "node_selectors": [{"id": "us", "name_regex": "^US-", "protocols": ["ss"]}],
                "proxy_groups": [
                    {"name": "US Stable", "type": "select", "proxies": ["selector:us"]}
                ],
                "rules": ["MATCH,US Stable"],
            },
        },
    ).json()

    first = client.get(created["subscribe_url"])
    upstream["name"] = "US-New"
    second = client.get(created["subscribe_url"])

    assert "- US-Old" in first.text
    assert "- US-New" not in first.text
    assert "- US-New" in second.text
    assert "- US-Old" not in second.text


def test_profile_persists_route_intent_and_compiled_policy(tmp_path, monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return """
proxies:
  - name: JP-01
    type: ss
    server: jp.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "general",
            "target": "mihomo",
            "route_intent": {
                "node_pools": [{"id": "jp", "name": "日本", "regions": ["jp"]}],
                "routes": [
                    {
                        "service": "github",
                        "primary_pool": "jp",
                        "final_target": "DIRECT",
                    }
                ],
            },
        },
    )

    assert created.status_code == 201
    body = created.json()
    draft = client.get(
        f"/profiles/{body['id']}/draft",
        params={"token": body["token"]},
    ).json()["request"]
    assert draft["route_intent"]["routes"][0]["service"] == "github"
    assert any(selector["id"] == "jp" for selector in draft["selected_policy"]["node_selectors"])

    subscription = client.get(body["subscribe_url"])
    assert subscription.status_code == 200
    assert "name: GitHub" in subscription.text
    assert "JP-01" in subscription.text


def test_profile_persists_selected_rule_pack_cards(tmp_path, monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return """
proxies:
  - name: US-01
    type: ss
    server: us.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/sub",
            "preset": "general",
            "rule_packs": ["claude", "github"],
            "target": "mihomo",
        },
    )

    assert created.status_code == 201
    body = created.json()
    draft = client.get(
        f"/profiles/{body['id']}/draft",
        params={"token": body["token"]},
    ).json()["request"]
    assert draft["rule_packs"] == ["claude", "github"]
    assert {group["name"] for group in draft["selected_policy"]["proxy_groups"]} >= {
        "Claude",
        "GitHub",
    }

    subscription = client.get(body["subscribe_url"])
    assert subscription.status_code == 200
    assert "name: Claude" in subscription.text
    assert "name: GitHub" in subscription.text
    assert "name: Netflix" not in subscription.text


def test_profiles_list_redacts_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)

    created = client.post(
        "/profiles",
        json={"subscription_url": "https://example.com/private-token", "template": LEO_TEMPLATE, "target": "mihomo"},
    ).json()
    response = client.get("/profiles")

    assert response.status_code == 200
    assert response.json() == {
        "profiles": [
            {
                "id": created["id"],
                    "target": "mihomo",
                    "template": LEO_TEMPLATE,
                    "has_artifact": False,
            }
        ]
    }
    assert created["token"] not in response.text
    assert "private-token" not in response.text


def test_profile_draft_can_be_read_and_updated_with_its_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={
            "subscription_url": "https://example.com/private-token",
            "template": LEO_TEMPLATE,
            "target": "mihomo",
        },
    ).json()

    denied = client.get(f"/profiles/{created['id']}/draft", params={"token": "wrong"})
    draft = client.get(
        f"/profiles/{created['id']}/draft",
        params={"token": created["token"]},
    )
    updated = client.put(
        f"/profiles/{created['id']}",
        params={"token": created["token"]},
        json={
            "subscription_url": "https://example.com/private-token",
            "template": LEO_TEMPLATE,
            "target": "clash",
        },
    )
    refreshed = client.get(
        f"/profiles/{created['id']}/draft",
        params={"token": created["token"]},
    )

    assert denied.status_code == 404
    assert draft.status_code == 200
    assert draft.json()["request"]["subscription_url"] == "https://example.com/private-token"
    assert updated.status_code == 200
    assert updated.json()["subscribe_urls"]["clash"].endswith("&target=clash")
    assert refreshed.json()["request"]["template"] == LEO_TEMPLATE
