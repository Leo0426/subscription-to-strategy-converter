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
  createElement: () => ({{textContent: "", get innerHTML() {{ return this.textContent.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;"); }} }}),
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
    assert "/static/flow.js?v=54" in root.text
    assert "/static/flow.css?v=53" in root.text
    assert "/static/assets/subflow-logo.png" in root.text


def test_release_metadata_agrees_between_app_package_and_image() -> None:
    root = Path(__file__).resolve().parents[1]
    page = TestClient(app).get("/").text
    schema = TestClient(app).get("/openapi.json").json()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    version = project["project"]["version"]
    assert schema["info"]["version"] == version
    assert f"Subflow {version}" in page
    assert f'<span class="version-badge">{version}</span>' in page
    assert f'org.opencontainers.image.version="{version}"' in (
        root / "Dockerfile"
    ).read_text(encoding="utf-8")


def test_page_exposes_the_current_workbench_contract() -> None:
    response = TestClient(app).get("/")
    assert response.status_code == 200
    page = response.text
    controls = (
        "leo-reference", "data-ledger", "subscription-url", "validate-source-button",
        "service-route-list", "generate-button", "existing-profile-url", "open-profile-button",
        "check-button", "check-results", "preview-upgrade-button", "diagnose-service",
        "diagnose-runtime", "diagnose-result", "surge-auto-test-protocols",
    )
    assert all(f'id="{control}"' in page for control in controls)
    assert 'class="config-workbench"' in page
    for client in ("mihomo", "surge", "shadowrocket"):
        assert f'name="target" value="{client}"' in page
    for output in ("clash", "surge", "shadowrocket", "shadowrocket-config"):
        assert f'id="published-{output}-url"' in page
        assert f'data-copy-output="{output}"' in page
    assert all(text in page for text in ('value="all"', 'value="anytls"', "仅 AnyTLS", "配置 → 添加配置", "完整登录"))
    obsolete = ("policy-workbench", "context-inspector", "profiles-list", "policy-preset",
                "rule-pack-catalog", "community-rule-library", "advanced-routing")
    assert not any(f'id="{control}"' in page for control in obsolete)
    assert "专家编排" not in page and "模板策略矩阵" not in page


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


def test_late_service_catalog_keeps_profile_routes_opened_during_startup() -> None:
    _run_flow_runtime(
        """
        (async () => {
          const makeElement = () => ({
            value: "", textContent: "", innerHTML: "", hidden: false, disabled: false,
            listeners: {}, classList: {toggle() {}},
            addEventListener(name, listener) { this.listeners[name] = listener; },
          });
          for (const element of Object.values(elements)) {
            element.listeners = {};
            element.addEventListener = function (name, listener) { this.listeners[name] = listener; };
          }
          document.querySelector = selector => elements[selector] ||= makeElement();
          document.querySelectorAll = selector => {
            if (selector === 'input[name="target"]') return targets;
            if (selector === 'input[name="target"]:checked') return targets.filter(item => item.checked);
            if (selector === ".config-workbench input, .config-workbench select, .config-workbench button") return [elements["#surge-auto-test-protocols"]];
            return [];
          };
          targets.forEach(target => target.addEventListener = () => {});
          let finishServices;
          const services = new Promise(resolve => { finishServices = resolve; });
          const savedRoute = {service: "openai", mode: "fixed", egress: "US01"};
          globalThis.fetch = async path => {
            let body;
            if (path === "/services") body = services;
            else if (path.startsWith("/profiles/p/draft")) body = {
              mode: "service_routes", generation: 1, publications: {},
              request: {
                subscription_url: "https://example.com/sub", profile_name: "Saved",
                publication_targets: ["mihomo"], service_routes: [savedRoute],
              },
            };
            else if (path === "/preview") body = {nodes: [{name: "US01"}]};
            else if (path.startsWith("/templates/detail")) body = {proxy_groups: [], summary: {}, public_data: []};
            else if (path === "/templates/audit") body = {summary: {}};
            else if (path === "/system/status") body = {app: {status: "ok"}, profile_db: {status: "ok"}};
            else body = {};
            return {ok: true, json: async () => body};
          };
          elements["#existing-profile-url"] = makeElement();
          elements["#existing-profile-url"].value = "https://subflow.example/subscribe/p?token=t";
          const boot = init();
          await Promise.resolve();
          const button = elements["#open-profile-button"];
          await button.listeners.click({currentTarget: button});
          finishServices({services: [{id: "openai", label: "OpenAI", group: "OpenAI", default_target: "默认代理", rules: []}]});
          await boot;
          const routes = payload().service_routes;
          if (routes.length !== 1 || routes[0].service !== "openai" || routes[0].egress !== "US01") {
            throw new Error(`opened Profile route was lost after catalog load: ${JSON.stringify(routes)}`);
          }
        })()
        """
    )


def test_system_status_reports_ready_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)

    response = client.get("/system/status")

    assert response.status_code == 200
    assert response.json()["app"]["status"] == "ok"
    assert response.json()["profile_db"]["status"] == "ok"
    assert "subconverter" not in response.json()


def test_diagnosis_evidence_keeps_unknown_identity_and_escapes_client_text():
    _run_flow_runtime('''
const html = renderRuntimeEvidence({identity: {client: "surge", version: "<script>bad</script>", platform: "macos", mode: "rule", observed_at: "2026-10-03T00:00:00Z"}, evidence: [
  {kind: "rule_match", status: "partial", reason: "<img onerror=bad>"},
  {kind: "configuration_identity", status: "unknown", reason: "无法核对"},
]});
if (!html.includes("配置一致性") || !html.includes("未知") || !html.includes("服务器配置的实例")) throw Error(html);
if (html.includes("<script>") || html.includes("<img")) throw Error("unescaped client response");
if (!html.includes("&lt;script&gt;") || !html.includes("&lt;img")) throw Error("missing evidence");
if (renderRuntimeEvidence({status: "not_tested"}).includes("已观测")) throw Error("unrequested evidence must stay absent");
''')


def test_dependency_counts_are_inventory_not_download_validation():
    _run_flow_runtime('''
const html = renderDependencySummary({summary: {total: 24, remote: 23, geodata: 1, unresolved: 1}});
if (!html.includes("23") || !html.includes("1 项地址未解析") || !html.includes("未下载审计")) throw Error(html);
if (renderDependencySummary(null) !== "") throw Error("old reports should still render");
''')
