from fastapi.testclient import TestClient

from app.main import app


def test_root_and_legacy_advanced_route_serve_the_same_simple_page() -> None:
    client = TestClient(app)

    root = client.get("/")
    advanced = client.get("/advanced")

    assert root.status_code == 200
    assert advanced.status_code == 200
    assert root.text == advanced.text
    assert "/static/flow.js?v=15" in root.text
    assert "/static/flow.css?v=15" in root.text
    assert "/static/assets/subflow-logo.png" in root.text


def test_page_only_exposes_the_primary_subscription_flow() -> None:
    response = TestClient(app).get("/")

    assert 'id="subscription-url"' in response.text
    assert 'id="validate-source-button"' in response.text
    assert 'id="service-route-list"' in response.text
    assert 'id="generate-button"' in response.text
    assert 'id="published-clash-url"' in response.text


def test_page_removes_secondary_workbench_surfaces() -> None:
    response = TestClient(app).get("/")

    assert 'id="policy-workbench"' not in response.text
    assert 'id="context-inspector"' not in response.text
    assert 'id="profiles-list"' not in response.text
    assert 'id="policy-preset"' not in response.text
    assert 'id="rule-pack-catalog"' not in response.text
    assert 'id="community-rule-library"' not in response.text
    assert 'id="advanced-routing"' not in response.text
    assert "专家编排" not in response.text
    assert "模板策略矩阵" not in response.text


def test_page_loads_leo_groups_and_fine_grained_services() -> None:
    script = TestClient(app).get("/static/flow.js").text

    assert 'const LEO_TEMPLATE = "local:community_templates/leo/leo.yaml"' in script
    assert 'jsonRequest(`/templates/detail?template=' in script
    assert 'jsonRequest("/rule-packs")' in script
    assert "SERVICE_DEFAULTS" in script
    assert "data-service-choice" in script
    assert "具体节点" in script


def test_selected_service_outlets_are_sent_as_fine_grained_merge_rules() -> None:
    script = TestClient(app).get("/static/flow.js").text

    assert 'mode: "merge"' in script
    assert "selected_policy: selectedPolicy()" in script
    assert "rules.push(...pack.rules)" in script
    assert "state.serviceChoices[pack.id]" in script
    assert "route_intent" not in script


def test_page_generates_clash_mihomo_and_surge_profile_links() -> None:
    client = TestClient(app)
    response = client.get("/")
    script = client.get("/static/flow.js")

    assert "Clash / Mihomo" in response.text
    assert "Surge" in response.text
    assert 'id="published-surge-url"' in response.text
    assert 'target: "clash"' in script.text
    assert 'postJson("/workspace/preview"' in script.text
    assert 'postJson("/profiles"' in script.text
    assert "created.subscribe_urls.surge" in script.text
