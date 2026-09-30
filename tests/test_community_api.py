"""Tests for the community template browser API (GET /community/templates*)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.community import _is_surge_compatible


_YAML_ID = "community:leo/leo.yaml"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── List endpoint ──────────────────────────────────────────────────────────


def test_list_item_has_required_fields(client: TestClient) -> None:
    response = client.get("/community/templates")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [_YAML_ID]
    yaml_items = response.json()
    assert yaml_items, "YAML template not found in list"
    item = yaml_items[0]
    assert item["format"] == "yaml"
    assert isinstance(item["proxy_group_count"], int)
    assert item["proxy_group_count"] == 17
    assert isinstance(item["rule_count"], int)
    assert item["surge_compatible"] is False
    assert "source_path" in item
    assert item["source_path"].startswith("community_templates/")


def test_rule_catalog_exposes_every_parseable_community_rule_with_its_source(client: TestClient) -> None:
    response = client.get("/community/rules")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["files_scanned"] >= 1
    assert body["summary"]["template_count"] >= 1
    assert body["summary"]["rule_count"] >= body["summary"]["unique_rule_count"] >= 1
    assert body["templates"]
    assert all(template["source_path"].startswith("community_templates/") for template in body["templates"])
    assert all(template["rules"] for template in body["templates"])
    assert any(
        rule == "GEOSITE,category-ads-all,REJECT"
        for template in body["templates"]
        for rule in template["rules"]
    )


def test_rule_catalog_uses_the_consolidated_template(client: TestClient) -> None:
    body = client.get("/community/rules").json()
    leo = next(
        template
        for template in body["templates"]
        if template["source_path"].endswith("leo/leo.yaml")
    )

    assert leo["extraction"] == "yaml"
    assert leo["provider_count"] == 8
    assert {provider["name"] for provider in leo["providers"]} == {
        "ai-4",
        "Claude",
        "GitHub-5",
        "Apple-4",
        "Google-2",
        "Microsoft-6",
        "YouTube-6",
        "Telegram",
    }
    assert "GEOSITE,category-ads-all,REJECT" in leo["rules"]


# ── Preview endpoint ───────────────────────────────────────────────────────


def test_preview_returns_proxy_groups(client: TestClient) -> None:
    response = client.get("/community/templates/preview", params={"id": _YAML_ID})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == _YAML_ID
    assert body["format"] == "yaml"
    assert isinstance(body["proxy_groups"], list)
    assert len(body["proxy_groups"]) > 0
    assert isinstance(body["rule_count"], int)
    assert isinstance(body["surge_compatible"], bool)
    for group in body["proxy_groups"]:
        assert {"name", "type", "members"} <= group.keys()
        assert isinstance(group["members"], list)


def test_preview_nonexistent_returns_404(client: TestClient) -> None:
    response = client.get(
        "/community/templates/preview",
        params={"id": "community:leo/does_not_exist.yaml"},
    )
    assert response.status_code == 404


def test_preview_invalid_id_prefix_returns_400(client: TestClient) -> None:
    response = client.get(
        "/community/templates/preview",
        params={"id": "local:community_templates/leo/leo.yaml"},
    )
    assert response.status_code == 400


def test_preview_path_traversal_blocked(client: TestClient) -> None:
    response = client.get(
        "/community/templates/preview",
        params={"id": "community:../app/main.py"},
    )
    assert response.status_code == 400


# ── Unit tests for surge_compatible ───────────────────────────────────────


def test_surge_compatible_true_for_clean_yaml() -> None:
    loaded = {
        "proxy-groups": [{"name": "PROXY", "type": "select"}],
        "rules": ["MATCH,DIRECT"],
    }
    assert _is_surge_compatible(loaded) is True


def test_surge_compatible_false_for_mrs_provider() -> None:
    loaded = {
        "proxy-groups": [{"name": "PROXY", "type": "select"}],
        "rule-providers": {
            "custom": {
                "type": "http",
                "url": "https://example.com/rules.mrs",
            }
        },
    }
    assert _is_surge_compatible(loaded) is False


def test_surge_compatible_false_when_compiler_drops_domain_provider() -> None:
    loaded = {
        "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["DIRECT"]}],
        "rule-providers": {
            "ai": {
                "type": "http",
                "behavior": "domain",
                "url": "https://example.com/ai.txt",
            }
        },
        "rules": ["RULE-SET,ai,PROXY", "MATCH,DIRECT"],
    }

    assert _is_surge_compatible(loaded) is False


@pytest.mark.parametrize("content", [None, "rules: [unterminated"])
def test_missing_or_invalid_leo_is_not_reinterpreted_as_another_template(client, tmp_path, monkeypatch, content):
    path = tmp_path / "leo.yaml"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr("app.api.community._LEO_PATH", path)
    monkeypatch.setattr("app.api.community._COMMUNITY_DIR_RESOLVED", tmp_path)

    assert client.get("/community/templates").json() == []
    ledger = client.get("/community/rules").json()
    assert ledger == {
        "summary": {
            "files_scanned": int(content is not None), "template_count": 0,
            "rule_count": 0, "unique_rule_count": 0, "provider_count": 0,
        },
        "templates": [],
    }
    preview = client.get("/community/templates/preview", params={"id": _YAML_ID})
    assert preview.status_code == (404 if content is None else 422)
