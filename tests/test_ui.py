import json
from pathlib import Path
import subprocess
import tomllib

from fastapi.testclient import TestClient

from app.main import app


def _run_flow_runtime(assertions: str) -> None:
    flow_path = Path(__file__).resolve().parents[1] / "app" / "static" / "flow.js"
    program = f"""
const fs = require("node:fs");
const vm = require("node:vm");
const targets = [
  {{value: "mihomo", checked: true}},
  {{value: "surge", checked: false}},
  {{value: "shadowrocket", checked: false}},
];
const elements = {{
  "#global-notice": {{textContent: "", hidden: true}},
  "#profile-name": {{value: ""}},
  "#subscription-url": {{value: "https://example.com/sub"}},
  "#surge-auto-test-row": {{hidden: true}},
  "#surge-auto-test-protocols": {{value: "all", disabled: true}},
  "#surge-auto-test-custom": {{hidden: true}},
}};
const controls = [elements["#surge-auto-test-protocols"]];
const document = {{
  querySelector: selector => elements[selector] || null,
  querySelectorAll: selector => {{
    if (selector === 'input[name="target"]:checked') return targets.filter(item => item.checked);
    if (selector === ".config-workbench input, .config-workbench select, .config-workbench button") return controls;
    return [];
  }},
  createElement: () => ({{textContent: "", innerHTML: ""}}),
}};
const context = vm.createContext({{
  document,
  elements,
  targets,
  console,
  structuredClone,
  setTimeout,
  clearTimeout,
  URL,
  location: {{origin: "https://subflow.example"}},
}});
let source = fs.readFileSync({json.dumps(str(flow_path))}, "utf8");
source = source.replace("\\ninit();\\n", "\\n");
vm.runInContext(source, context);
const result = vm.runInContext({json.dumps(assertions)}, context);
Promise.resolve(result).catch(error => {{ console.error(error); process.exitCode = 1; }});
"""
    completed = subprocess.run(
        ["node", "-e", program],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_root_and_legacy_advanced_route_serve_the_same_simple_page() -> None:
    client = TestClient(app)

    root = client.get("/")
    advanced = client.get("/advanced")

    assert root.status_code == 200
    assert advanced.status_code == 200
    assert root.text == advanced.text
    assert "/static/flow.js?v=52" in root.text
    assert "/static/flow.css?v=52" in root.text
    assert "/static/assets/subflow-logo.png" in root.text


def test_release_metadata_is_consistently_6_5() -> None:
    root = Path(__file__).resolve().parents[1]
    page = TestClient(app).get("/").text
    schema = TestClient(app).get("/openapi.json").json()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert schema["info"]["version"] == "6.5.0"
    assert project["project"]["version"] == "6.5.0"
    assert "Subflow 6.5" in page
    assert '<span class="version-badge">6.5</span>' in page
    assert 'org.opencontainers.image.version="6.5.0"' in (
        root / "Dockerfile"
    ).read_text(encoding="utf-8")


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


def test_page_exposes_surge_auto_test_protocol_preference() -> None:
    page = TestClient(app).get("/").text

    assert 'id="surge-auto-test-protocols"' in page
    assert 'value="all"' in page
    assert 'value="anytls"' in page
    assert "仅 AnyTLS" in page


def test_workbench_round_trips_and_scopes_surge_auto_test_preference() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "app" / "static" / "flow.js"
    ).read_text(encoding="utf-8")

    assert "surge_preferences" in script
    assert "auto_test_protocols" in script
    assert "request.surge_preferences" in script
    assert "updateSurgePreferenceVisibility" in script
    assert 'selectedTargets().includes("surge")' in script


def test_workbench_preserves_custom_protocol_lists_until_user_changes_control() -> None:
    _run_flow_runtime(
        """
        (() => {
          targets[0].checked = false;
          targets[1].checked = true;
          restoreSurgePreferences({auto_test_protocols: ["ss", "future-protocol"]});
          if (elements["#surge-auto-test-protocols"].value !== "custom") {
            throw new Error("custom protocol list was not represented as custom");
          }
          const preserved = payload().surge_preferences.auto_test_protocols;
          if (JSON.stringify(preserved) !== JSON.stringify(["ss", "future-protocol"])) {
            throw new Error(`custom protocols changed: ${JSON.stringify(preserved)}`);
          }
          elements["#surge-auto-test-protocols"].value = "anytls";
          const changed = payload().surge_preferences.auto_test_protocols;
          if (JSON.stringify(changed) !== JSON.stringify(["anytls"])) {
            throw new Error(`explicit AnyTLS choice was not applied: ${JSON.stringify(changed)}`);
          }
        })()
        """
    )


def test_opening_surge_profile_reenables_preference_after_busy_cleanup() -> None:
    _run_flow_runtime(
        """
        (async () => {
          updateSurgePreferenceVisibility();
          if (!elements["#surge-auto-test-protocols"].disabled) {
            throw new Error("precondition: selector should start disabled");
          }
          renderServices = () => {};
          updateActions = () => {};
          setNotice = () => {};
          const button = {textContent: "打开"};
          await busy(button, "打开中…", async () => {
            targets[1].checked = true;
            restoreSurgePreferences({auto_test_protocols: ["anytls"]});
          });
          if (elements["#surge-auto-test-row"].hidden) {
            throw new Error("Surge preference row stayed hidden");
          }
          if (elements["#surge-auto-test-protocols"].disabled) {
            throw new Error("Surge preference selector stayed disabled");
          }
        })()
        """
    )
