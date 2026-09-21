from fastapi.testclient import TestClient

from app.main import app


def test_root_and_legacy_advanced_route_serve_the_same_simple_page() -> None:
    client = TestClient(app)

    root = client.get("/")
    advanced = client.get("/advanced")

    assert root.status_code == 200
    assert advanced.status_code == 200
    assert root.text == advanced.text
    assert "/static/flow.js?v=51" in root.text
    assert "/static/flow.css?v=51" in root.text
    assert "/static/assets/subflow-logo.png" in root.text


def test_page_only_exposes_the_primary_subscription_flow() -> None:
    response = TestClient(app).get("/")

    assert 'id="leo-reference"' in response.text
    assert 'id="data-ledger"' in response.text
    assert 'class="config-workbench"' in response.text
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


def test_page_exposes_client_checks_stable_editing_and_optional_diagnosis() -> None:
    page = TestClient(app).get("/").text
    for control in ("existing-profile-url", "open-profile-button", "check-button", "check-results",
                    "preview-upgrade-button", "diagnose-service", "diagnose-runtime", "diagnose-result"):
        assert f'id="{control}"' in page
    assert 'name="target" value="mihomo"' in page
    assert 'name="target" value="surge"' in page
    assert 'name="target" value="shadowrocket"' in page
    assert '完整登录' in page


def test_page_generates_all_three_client_links_and_shadowrocket_policy() -> None:
    page = TestClient(app).get("/").text
    for output in ("clash", "surge", "shadowrocket", "shadowrocket-config"):
        assert f'id="published-{output}-url"' in page
        assert f'data-copy-output="{output}"' in page
    assert "配置 → 添加配置" in page
