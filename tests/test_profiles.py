from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.profiles import ProfileStore
from app.core.subconverter import SubconverterError
from app.main import app


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
    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
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
    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)

    created = client.post(
        "/profiles",
        json={"subscription_url": "https://example.com/private-token", "template": "powerfullz", "target": "mihomo"},
    )
    response = client.get(created.json()["subscribe_url"])

    assert created.status_code == 201
    assert "example.com/private-token" not in created.json()["subscribe_url"]
    assert response.status_code == 200
    assert "name: HK-01" in response.text


def test_profile_creation_rejects_non_mihomo_target(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)

    response = client.post(
        "/profiles",
        json={"subscription_url": "https://example.com/sub", "template": "developer", "target": "surge"},
    )

    assert response.status_code == 422
    assert "Mihomo" in response.json()["detail"]


def test_profile_subscription_falls_back_to_last_successful_artifact(tmp_path, monkeypatch) -> None:
    upstream = {"available": True}

    async def fake_convert_subscription_to_clash(url: str, options: object | None = None) -> str:
        if not upstream["available"]:
            raise SubconverterError("upstream unavailable")
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
    monkeypatch.setattr("app.core.subscription.convert_subscription_to_clash", fake_convert_subscription_to_clash)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)
    created = client.post(
        "/profiles",
        json={"subscription_url": "https://example.com/sub", "template": "powerfullz", "target": "mihomo"},
    ).json()
    fresh = client.get(created["subscribe_url"])

    upstream["available"] = False
    stale = client.get(created["subscribe_url"])

    assert stale.status_code == 200
    assert stale.text == fresh.text
    assert stale.headers["X-Subflow-Stale"] == "true"


def test_profiles_list_redacts_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)

    created = client.post(
        "/profiles",
        json={"subscription_url": "https://example.com/private-token", "template": "developer", "target": "mihomo"},
    ).json()
    response = client.get("/profiles")

    assert response.status_code == 200
    assert response.json() == {
        "profiles": [
            {
                "id": created["id"],
                "target": "mihomo",
                "template": "developer",
                "has_artifact": False,
            }
        ]
    }
    assert created["token"] not in response.text
    assert "private-token" not in response.text
