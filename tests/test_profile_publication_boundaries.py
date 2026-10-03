"""Regression tests for saved Profile publication boundaries."""

import hashlib
import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.profiles import ProfileStore
from app.main import app


CLASH_SOURCE = """proxies:
  - {name: US01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: test}
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url, *, target=None):
        return CLASH_SOURCE

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_default_subscription_uses_checked_publication_target(client):
    request = {
        "subscription_url": "https://example.com/sub",
        "target": "surge",
        "publication_targets": ["mihomo"],
    }
    checked = client.post("/check", json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is True

    saved = client.post("/profiles", json=request)
    assert saved.status_code == 201, saved.text
    published = client.get(saved.json()["subscribe_url"])
    assert published.status_code == 200, published.text
    assert published.headers["content-disposition"] == 'inline; filename="mihomo.yaml"'


def test_saved_default_target_matches_checked_target_after_create_and_update(client):
    request = {
        "subscription_url": "https://example.com/sub",
        "target": "surge",
        "publication_targets": ["mihomo"],
    }
    created = client.post("/profiles", json=request)
    assert created.status_code == 201, created.text
    profile = created.json()
    draft_path = f"/profiles/{profile['id']}/draft"
    params = {"token": profile["token"]}
    assert client.get(draft_path, params=params).json()["request"]["target"] == "mihomo"

    updated = client.put(
        f"/profiles/{profile['id']}",
        params=params,
        json={**request, "publication_targets": ["shadowrocket"]},
    )
    assert updated.status_code == 200, updated.text
    assert client.get(draft_path, params=params).json()["request"]["target"] == "shadowrocket"
    published = client.get(updated.json()["subscribe_url"])
    assert published.status_code == 200, published.text
    assert published.headers["content-disposition"] == 'inline; filename="shadowrocket.txt"'


def test_preexisting_profile_default_link_uses_checked_target(client):
    old = ProfileStore(os.environ["SUBFLOW_DB_PATH"]).create({
        "subscription_url": "https://example.com/sub",
        "target": "surge",
        "publication_targets": ["mihomo"],
    })

    published = client.get(f"/subscribe/{old.id}", params={"token": old.token})
    assert published.status_code == 200, published.text
    assert published.headers["content-disposition"] == 'inline; filename="mihomo.yaml"'


def test_shadowrocket_config_can_remain_explicit_default_when_companion_is_checked(client):
    request = {
        "subscription_url": "https://example.com/sub",
        "target": "shadowrocket-config",
        "publication_targets": ["shadowrocket"],
    }
    checked = client.post("/check", json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is True

    saved = client.post("/profiles", json=request)
    assert saved.status_code == 201, saved.text
    profile = saved.json()
    draft = client.get(f"/profiles/{profile['id']}/draft", params={"token": profile["token"]})
    assert draft.json()["request"]["target"] == "shadowrocket-config"
    published = client.get(profile["subscribe_url"])
    assert published.status_code == 200, published.text
    assert published.headers["content-disposition"] == 'inline; filename="shadowrocket.conf"'


def test_profile_rejects_cycle_between_legacy_claude_and_modern_service(client):
    request = {
        "subscription_url": "https://example.com/sub",
        "service_routes": [
            {"service": "claude", "mode": "legacy", "egress": "Apple"},
            {"service": "apple", "mode": "manual", "egress": "Claude"},
        ],
    }
    checked = client.post("/check", json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is False
    assert any(finding["code"] == "group_cycle" for finding in checked.json()["findings"])

    saved = client.post("/profiles", json=request)
    assert saved.status_code == 400, saved.text
    assert client.get("/profiles").json()["profiles"] == []


def test_profile_update_rejects_cycle_between_legacy_and_modern_routes(client):
    created = client.post("/profiles", json={"subscription_url": "https://example.com/sub"})
    assert created.status_code == 201, created.text
    profile = created.json()
    params = {"token": profile["token"]}
    updated = client.put(
        f"/profiles/{profile['id']}",
        params=params,
        json={
            "subscription_url": "https://example.com/sub",
            "service_routes": [
                {"service": "claude", "mode": "legacy", "egress": "Apple"},
                {"service": "apple", "mode": "manual", "egress": "Claude"},
            ],
        },
    )

    assert updated.status_code == 400, updated.text
    draft = client.get(f"/profiles/{profile['id']}/draft", params=params)
    assert draft.json()["request"]["service_routes"] == []


def test_offline_save_rejects_route_referring_to_group_created_later(client):
    request = {
        "subscription_url": "https://example.com/sub",
        "service_routes": [
            {"service": "apple", "mode": "manual", "egress": "OpenAI"},
            {"service": "openai", "mode": "fixed", "egress": "US01"},
        ],
    }
    rendered = client.post("/render", json=request)
    assert rendered.status_code == 400, rendered.text

    saved = client.post("/profiles", json=request)
    assert saved.status_code == 400, saved.text
    assert client.get("/profiles").json()["profiles"] == []


def _legacy_store(tmp_path):
    database = tmp_path / "legacy.db"
    request = {"subscription_url": "https://example.com/sub", "target": "surge"}
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE profiles (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, "
            "request_json TEXT NOT NULL, artifact TEXT)"
        )
        connection.execute(
            "INSERT INTO profiles VALUES (?, ?, ?, ?)",
            ("legacy", hashlib.sha256(b"token").hexdigest(), json.dumps(request), "old Surge config"),
        )
    return ProfileStore(database)


def test_legacy_artifact_survives_refresh_of_another_target(tmp_path):
    store = _legacy_store(tmp_path)
    assert store.get("legacy", "token").artifacts == {"surge": "old Surge config"}

    assert store.save_artifact("legacy", "mihomo", "new Mihomo config", expected_generation=1)

    assert store.get("legacy", "token").artifacts == {
        "surge": "old Surge config",
        "mihomo": "new Mihomo config",
    }


def test_legacy_artifact_is_removed_only_when_its_target_is_discarded(tmp_path):
    store = _legacy_store(tmp_path)
    assert store.save_artifact("legacy", "mihomo", "new Mihomo config", expected_generation=1)

    assert store.discard_artifact("legacy", "mihomo", expected_generation=1)
    assert store.get("legacy", "token").artifacts == {"surge": "old Surge config"}

    assert store.save_artifact("legacy", "mihomo", "new Mihomo config", expected_generation=1)
    assert store.discard_artifact("legacy", "surge", expected_generation=1)
    assert store.get("legacy", "token").artifacts == {"mihomo": "new Mihomo config"}
