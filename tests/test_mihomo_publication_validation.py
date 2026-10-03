"""Validate the native connectivity graph that the Mihomo client will receive."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


CYCLIC_SOURCE = """proxies:
- {name: US01, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: synthetic, dialer-proxy: SourceA}
proxy-groups:
- {name: SourceA, type: select, proxies: [SourceB]}
- {name: SourceB, type: select, proxies: [SourceA]}
"""
VALID_SOURCE = CYCLIC_SOURCE.replace("proxies: [SourceA]", "proxies: [DIRECT]")
DYNAMIC_SOURCE = """proxies:
- {name: US01, type: ss, server: 192.0.2.1, port: 443, cipher: aes-128-gcm, password: synthetic}
dns:
  nameserver: [https://1.1.1.1/dns-query#Dynamic]
proxy-groups:
- {name: Dynamic, type: select, proxies: [], use: [], DYNAMIC_FLAG: DYNAMIC_ENABLED}
proxy-providers:
  native:
    type: inline
    payload:
    - {name: Exit, type: ss, server: 192.0.2.2, port: 443, cipher: aes-128-gcm, password: synthetic}
"""


@pytest.fixture
def source_client(tmp_path, monkeypatch):
    source = [CYCLIC_SOURCE]
    fetches = []

    async def fetch(url, *, target=None):
        fetches.append((url, target))
        return source[0]

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app) as client:
        yield client, source, fetches


def test_check_agrees_with_preview_for_retained_native_dependency_cycle(source_client) -> None:
    client, _source, fetches = source_client
    request = {"subscription_url": "https://example.com/sub", "publication_targets": ["mihomo"]}
    preview = client.post("/workspace/preview", json=request)
    assert preview.status_code == 200, preview.text
    cycle = next(finding for finding in preview.json()["findings"] if finding["code"] == "group_cycle")
    fetches.clear()

    checked = client.post("/check", json=request)

    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is False
    assert checked.json()["clients"][0]["errors"] == [cycle["message"]]
    assert len(fetches) == 1


@pytest.mark.parametrize(("default_target", "targets"), [
    ("mihomo", ["mihomo"]),
    ("surge", ["surge", "mihomo"]),
    ("shadowrocket", ["shadowrocket", "mihomo"]),
])
def test_publication_rejects_mihomo_cycle_for_every_default_target(
    source_client, default_target: str, targets: list[str],
) -> None:
    client, _source, _fetches = source_client
    request = {"subscription_url": "https://example.com/sub", "target": default_target,
               "publication_targets": targets}

    checked = client.post("/check", json=request)
    created = client.post("/profiles", json=request)

    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is False
    mihomo = next(result for result in checked.json()["clients"] if result["target"] == "mihomo")
    assert mihomo["status"] == "error"
    assert any("SourceA -> SourceB -> SourceA" in error for error in mihomo["errors"])
    assert created.status_code == 400, created.text
    assert client.get("/profiles").json()["profiles"] == []


def test_invalid_native_graph_update_keeps_saved_intent_and_artifact(source_client) -> None:
    client, source, _fetches = source_client
    source[0] = VALID_SOURCE
    request = {"subscription_url": "https://example.com/sub", "profile_name": "saved",
               "target": "surge", "publication_targets": ["surge", "mihomo"]}
    created = client.post("/profiles", json=request)
    assert created.status_code == 201, created.text
    profile = created.json()
    assert client.get(profile["subscribe_urls"]["clash"]).status_code == 200
    draft_url = f"/profiles/{profile['id']}/draft"
    params = {"token": profile["token"]}
    before = client.get(draft_url, params=params).json()
    source[0] = CYCLIC_SOURCE

    updated = client.put(f"/profiles/{profile['id']}", params=params,
                         json={**request, "profile_name": "invalid"})

    assert updated.status_code == 400, updated.text
    after = client.get(draft_url, params=params).json()
    assert after["request"] == before["request"]
    assert after["generation"] == before["generation"]
    assert after["publications"] == before["publications"]


def test_unreachable_native_cycle_is_pruned_before_publication_check(source_client) -> None:
    client, source, _fetches = source_client
    source[0] = CYCLIC_SOURCE.replace(", dialer-proxy: SourceA", "")

    checked = client.post("/check", json={
        "subscription_url": "https://example.com/sub", "publication_targets": ["mihomo"],
    })

    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is True


@pytest.mark.parametrize("flag", [
    "include-all", "include-all-proxies", "include-all-providers",
    "Include-All-Providers", "include_all_proxies",
])
def test_native_dynamic_group_is_available_for_publication(source_client, flag: str) -> None:
    client, source, _fetches = source_client
    source[0] = DYNAMIC_SOURCE.replace("DYNAMIC_FLAG", flag).replace("DYNAMIC_ENABLED", "true")
    request = {"subscription_url": "https://example.com/sub", "publication_targets": ["mihomo"]}

    rendered = client.post("/render", json=request)
    checked = client.post("/check", json=request)
    created = client.post("/profiles", json=request)

    assert rendered.status_code == 200, rendered.text
    assert "name: Dynamic" in rendered.text
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is True, checked.json()
    assert created.status_code == 201, created.text


@pytest.mark.parametrize("flag", [None, "include-all", "include-all-proxies", "include-all-providers"])
def test_empty_native_group_with_no_enabled_dynamic_members_blocks_publication(source_client, flag) -> None:
    client, source, _fetches = source_client
    source[0] = (
        DYNAMIC_SOURCE.replace("DYNAMIC_FLAG", flag).replace("DYNAMIC_ENABLED", "false")
        if flag else DYNAMIC_SOURCE.replace(", DYNAMIC_FLAG: DYNAMIC_ENABLED", "")
    )
    request = {"subscription_url": "https://example.com/sub", "publication_targets": ["mihomo"]}

    checked = client.post("/check", json=request)
    created = client.post("/profiles", json=request)

    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is False
    assert checked.json()["clients"][0]["errors"] == ["Group 'Dynamic' has no available members."]
    assert created.status_code == 400, created.text


@pytest.mark.parametrize("builtin", ["COMPATIBLE", "PASS-RULE"])
def test_native_mihomo_builtin_group_member_is_available_for_publication(source_client, builtin: str) -> None:
    client, source, _fetches = source_client
    source[0] = DYNAMIC_SOURCE.replace(", DYNAMIC_FLAG: DYNAMIC_ENABLED", "").replace(
        "proxies: []", f"proxies: [{builtin}]",
    )
    request = {"subscription_url": "https://example.com/sub", "publication_targets": ["mihomo"]}

    checked = client.post("/check", json=request)
    created = client.post("/profiles", json=request)

    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is True, checked.json()
    assert created.status_code == 201, created.text


@pytest.mark.parametrize("builtin", ["COMPATIBLE", "PASS-RULE"])
def test_native_mihomo_node_cannot_shadow_builtin_target(source_client, builtin: str) -> None:
    client, source, _fetches = source_client
    source[0] = VALID_SOURCE.replace("US01", builtin)

    checked = client.post("/check", json={
        "subscription_url": "https://example.com/sub", "publication_targets": ["mihomo"],
    })

    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is False
    assert any("名称冲突" in error for error in checked.json()["clients"][0]["errors"])


@pytest.mark.parametrize("name", ["COMPATIBLE", "PASS-RULE"])
@pytest.mark.parametrize("target", ["surge", "shadowrocket-config"])
def test_mihomo_builtin_names_remain_valid_native_ini_nodes(source_client, name: str, target: str) -> None:
    client, source, _fetches = source_client
    node = f"{name} = ss, 192.0.2.1, 443, encrypt-method=aes-128-gcm, password=synthetic"
    source[0] = f"[Proxy]\n{node}\n"

    rendered = client.post("/render", json={
        "subscription_url": "https://example.com/sub", "target": target,
    })

    assert rendered.status_code == 200, rendered.text
    assert node in rendered.text.splitlines()
