from fastapi.testclient import TestClient

from app.main import app


def test_root_and_legacy_advanced_route_serve_one_guided_product() -> None:
    client = TestClient(app)

    root = client.get("/")
    advanced = client.get("/advanced")

    assert root.status_code == 200
    assert advanced.status_code == 200
    assert root.text == advanced.text
    assert "/static/flow.js" in root.text
    assert "/static/flow.css" in root.text
    assert "/static/assets/subflow-logo.png" in root.text


def test_guided_product_exposes_profile_home_four_steps_and_inspector() -> None:
    response = TestClient(app).get("/")

    assert 'id="profile-home"' in response.text
    assert 'id="new-profile-button"' in response.text
    assert 'data-step="source"' in response.text
    assert 'data-step="targets"' in response.text
    assert 'data-step="routing"' in response.text
    assert 'data-step="publish"' in response.text
    assert 'id="context-inspector"' in response.text
    assert 'id="publish-profile-button"' in response.text


def test_expert_tools_are_progressively_disclosed() -> None:
    response = TestClient(app).get("/")

    assert 'data-inspector-view="overview"' in response.text
    assert 'data-inspector-view="findings"' in response.text
    assert 'data-inspector-view="test"' in response.text
    assert 'data-inspector-view="source"' in response.text
    assert "策略组调试" not in response.text
    assert "自动预设源" not in response.text
