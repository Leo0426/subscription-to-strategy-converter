"""Tests for the community template browser API (GET /community/templates*)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.community import _build_meta, _detect_format, _is_surge_compatible
from pathlib import Path


_YAML_ID = "community:THEYAMLS/General_Config/666OS/OneTouch_Config.yaml"
_OPENCLASH_ID = "community:Overwrite/THENEWOPENCLASH/Official_Examples/Metacubex/rule-set_config.yaml"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── List endpoint ──────────────────────────────────────────────────────────


def test_list_returns_yaml_templates(client: TestClient) -> None:
    response = client.get("/community/templates")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    assert len(items) > 0


def test_list_includes_yaml_entry(client: TestClient) -> None:
    response = client.get("/community/templates")
    ids = {item["id"] for item in response.json()}
    assert _YAML_ID in ids


def test_list_item_has_required_fields(client: TestClient) -> None:
    response = client.get("/community/templates")
    yaml_items = [item for item in response.json() if item["id"] == _YAML_ID]
    assert yaml_items, "YAML template not found in list"
    item = yaml_items[0]
    assert item["format"] == "yaml"
    assert isinstance(item["proxy_group_count"], int)
    assert item["proxy_group_count"] > 0
    assert isinstance(item["rule_count"], int)
    assert isinstance(item["surge_compatible"], bool)
    assert "source_path" in item
    assert item["source_path"].startswith("community_templates/")


def test_list_openclash_item_has_openclash_format(client: TestClient) -> None:
    response = client.get("/community/templates")
    oc_items = [item for item in response.json() if item["id"] == _OPENCLASH_ID]
    assert oc_items, "OpenClash template not found in list"
    assert oc_items[0]["format"] == "openclash"


def test_list_excludes_md_and_list_files(client: TestClient) -> None:
    items = client.get("/community/templates").json()
    for item in items:
        assert not item["source_path"].endswith(".md")
        assert not item["source_path"].endswith(".list")
        assert not item["source_path"].endswith(".sh")
        assert not item["source_path"].endswith(".txt")
        assert not item["source_path"].endswith(".ini")
        assert not item["source_path"].endswith(".conf")


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


def test_preview_proxy_group_shape(client: TestClient) -> None:
    response = client.get("/community/templates/preview", params={"id": _YAML_ID})
    groups = response.json()["proxy_groups"]
    for g in groups:
        assert "name" in g
        assert "type" in g
        assert "members" in g
        assert isinstance(g["members"], list)


def test_preview_nonexistent_returns_404(client: TestClient) -> None:
    response = client.get(
        "/community/templates/preview",
        params={"id": "community:THEYAMLS/does_not_exist.yaml"},
    )
    assert response.status_code == 404


def test_preview_invalid_id_prefix_returns_400(client: TestClient) -> None:
    response = client.get(
        "/community/templates/preview",
        params={"id": "local:THEYAMLS/General_Config/666OS/OneTouch_Config.yaml"},
    )
    assert response.status_code == 400


def test_preview_path_traversal_blocked(client: TestClient) -> None:
    response = client.get(
        "/community/templates/preview",
        params={"id": "community:../app/main.py"},
    )
    assert response.status_code == 400


# ── Unit tests for format detection ───────────────────────────────────────


def test_detect_format_yaml_with_proxy_groups() -> None:
    path = Path("/project/community_templates/THEYAMLS/config.yaml")
    loaded = {"proxy-groups": [{"name": "PROXY", "type": "select"}]}
    assert _detect_format(path, loaded) == "yaml"


def test_detect_format_yaml_without_proxy_groups_is_unknown() -> None:
    path = Path("/project/community_templates/THEYAMLS/config.yaml")
    loaded = {"rules": ["MATCH,DIRECT"]}
    assert _detect_format(path, loaded) == "unknown"


def test_detect_format_openclash_by_directory() -> None:
    path = Path("/project/community_templates/Overwrite/THEOPENCLASH/General_Config/config.yaml")
    loaded = {"rules": ["MATCH,DIRECT"]}
    assert _detect_format(path, loaded) == "openclash"


def test_detect_format_thenewopenclash_by_directory() -> None:
    path = Path("/project/community_templates/Overwrite/THENEWOPENCLASH/config.yaml")
    loaded = {}
    assert _detect_format(path, loaded) == "openclash"


def test_detect_format_none_loaded_yaml_is_unknown() -> None:
    path = Path("/project/community_templates/THEYAMLS/bad.yaml")
    assert _detect_format(path, None) == "unknown"


# ── Unit tests for surge_compatible ───────────────────────────────────────


def test_surge_compatible_true_for_clean_yaml() -> None:
    loaded = {
        "proxy-groups": [{"name": "PROXY", "type": "select"}],
        "rules": ["MATCH,DIRECT"],
    }
    assert _is_surge_compatible(loaded, "yaml") is True


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
    assert _is_surge_compatible(loaded, "yaml") is False


def test_surge_compatible_false_for_openclash_format() -> None:
    loaded = {"proxy-groups": [{"name": "P", "type": "select"}]}
    assert _is_surge_compatible(loaded, "openclash") is False
