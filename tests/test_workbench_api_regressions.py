"""Public API regressions for publication and policy workspace boundaries."""

import json
import os
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.service_catalog import service_catalog
from app.core.profiles import ProfileStore
from app.main import app


CLASH_SOURCE = """proxies:
  - {name: TW01, type: ss, server: tw.example.com, port: 443, cipher: aes-128-gcm, password: test}
  - {name: US01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: test}
"""
SURGE_SOURCE = """[General]
dns-server = 1.1.1.1

[Proxy]
US01 = ss, us.example.com, 443, encrypt-method=aes-128-gcm, password=private-password
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url):
        return CLASH_SOURCE

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _cycle_request() -> dict:
    return {
        "subscription_url": "https://example.com/sub",
        "service_routes": [
            {"service": "apple", "mode": "manual", "egress": "Microsoft"},
            {"service": "microsoft", "mode": "manual", "egress": "Apple"},
        ],
    }


def test_modern_profile_without_publication_targets_cannot_save_group_cycle(client):
    request = _cycle_request()
    checked = client.post("/check", json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is False
    assert any(item["code"] == "group_cycle" for item in checked.json()["findings"])

    saved = client.post("/profiles", json=request)
    assert saved.status_code == 400, saved.text
    assert client.get("/profiles").json()["profiles"] == []


def test_modern_profile_update_without_publication_targets_cannot_save_group_cycle(client):
    saved = client.post("/profiles", json={
        "subscription_url": "https://example.com/sub",
        "service_routes": [{"service": "apple", "mode": "fixed", "egress": "TW01"}],
    })
    assert saved.status_code == 201, saved.text
    profile = saved.json()

    updated = client.put(
        f"/profiles/{profile['id']}",
        params={"token": profile["token"]},
        json=_cycle_request(),
    )
    assert updated.status_code == 400, updated.text
    draft = client.get(
        f"/profiles/{profile['id']}/draft", params={"token": profile["token"]}
    )
    assert draft.json()["request"]["service_routes"] == [
        {"service": "apple", "enabled": True, "mode": "fixed", "egress": "TW01", "fallback": None}
    ]


def test_legacy_profile_without_publication_targets_remains_savable(client):
    saved = client.post("/profiles", json={"subscription_url": "https://example.com/sub"})
    assert saved.status_code == 201, saved.text


def test_modern_profile_without_publication_targets_keeps_offline_save(client, monkeypatch):
    async def unavailable(_url):
        raise AssertionError("saving without client targets must not fetch upstream")

    monkeypatch.setattr("app.core.subscription.fetch_subscription", unavailable)
    saved = client.post("/profiles", json={
        "subscription_url": "https://example.com/sub",
        "service_routes": [{"service": "openai", "mode": "fixed", "egress": "US01"}],
    })
    assert saved.status_code == 201, saved.text


def test_preexisting_cyclic_profile_cannot_publish(client):
    saved = ProfileStore(os.environ["SUBFLOW_DB_PATH"]).create(_cycle_request())

    published = client.get(
        f"/subscribe/{saved.id}", params={"token": saved.token, "target": "clash"}
    )

    assert published.status_code == 400, published.text
    assert "cycle" in published.text.lower()


def test_upgrade_preview_reports_rule_precedence_change_without_rule_additions(client):
    rendered = client.post("/render", json={"subscription_url": "https://example.com/sub"})
    assert rendered.status_code == 200, rendered.text
    template = YAML(typ="safe").load(rendered.text)
    template_rules = list(template["rules"])
    final_rule = next(rule for rule in template_rules if str(rule).startswith("MATCH,"))
    selected_rules = [final_rule, *(rule for rule in template_rules if rule != final_rule)]
    service_groups = {service["group"] for service in service_catalog()}
    selected_groups = [
        {**group, "proxies": ["selector:all", *group["proxies"][1:]]}
        if group["name"] in service_groups else group
        for group in template["proxy-groups"]
    ]
    request = {
        "subscription_url": "https://example.com/sub",
        "selected_policy": {
            "mode": "replace",
            "node_selectors": [{"id": "all"}],
            "proxy_groups": selected_groups,
            "rule_providers": template["rule-providers"],
            "rules": selected_rules,
        },
    }
    checked = client.post("/check", json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is True
    saved = client.post("/profiles", json=request)
    assert saved.status_code == 201, saved.text
    profile = saved.json()

    preview = client.post(
        f"/profiles/{profile['id']}/upgrade-preview", params={"token": profile["token"]}
    )
    assert preview.status_code == 200, preview.text
    changes = preview.json()["changes"]
    assert changes["added_rules"] == []
    assert changes["removed_rules"] == []
    assert changes["rule_order_changed"] is True


def test_upgrade_preview_page_displays_rule_order_change():
    script_path = Path(__file__).resolve().parents[1] / "app" / "static" / "flow.js"
    program = f"""
const fs = require('node:fs');
const vm = require('node:vm');
const result = {{hidden: true, innerHTML: ''}};
const response = {{request: {{service_routes: []}}, changes: {{
  message: '预览', added_rules: [], removed_rules: [], rule_order_changed: true,
  removed_groups: [], discarded_preferences: []
}}}};
const context = vm.createContext({{
  document: {{
    querySelector: selector => selector === '#upgrade-result' ? result : null,
    createElement: () => ({{textContent: '', innerHTML: ''}}),
  }},
  fetch: async () => ({{ok: true, json: async () => response}}),
}});
let source = fs.readFileSync({json.dumps(str(script_path))}, 'utf8');
source = source.replace(/\\ninit\\(\\);\\s*$/, '\\n');
vm.runInContext(source, context);
vm.runInContext("state.profile = {{id: 'p', token: 't'}}; previewUpgrade()", context)
  .then(() => {{
    if (result.hidden || !result.innerHTML.includes('顺序改变')) {{
      throw new Error('rule precedence warning is absent from upgrade preview');
    }}
  }}).catch(error => {{console.error(error); process.exitCode = 1;}});
"""
    completed = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_surge_to_mihomo_workspace_compile_keeps_cross_format_warning(client, monkeypatch):
    async def fetch(_url):
        return SURGE_SOURCE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    request = {"subscription_url": "https://example.com/sub", "target": "mihomo"}
    direct = client.post("/render", json=request)
    assert direct.status_code == 200, direct.text
    assert any(warning["code"] == "cross_format_source_settings" for warning in
               json.loads(direct.headers["X-Compile-Warnings"]))

    preview = client.post("/workspace/preview", json=request)
    assert preview.status_code == 200, preview.text
    workspace = preview.json()["workspace"]
    assert workspace["native_source_format"] == "surge"
    assert "_surge_source" not in json.dumps(workspace)
    assert "dns-server = 1.1.1.1" not in json.dumps(workspace)
    compiled = client.post("/compile", json={"target": "mihomo", "workspace": workspace})
    assert compiled.status_code == 200, compiled.text
    assert any(warning["code"] == "cross_format_source_settings" for warning in
               json.loads(compiled.headers["X-Compile-Warnings"]))


@pytest.mark.parametrize("endpoint", ["/simulate", "/compile"])
def test_malformed_workspace_port_returns_client_error(client, endpoint):
    workspace = {
        "proxies": [{"name": "Bad", "protocol": "ss", "server": "edge.example",
                     "port": "invalid", "extra": {"cipher": "aes-128-gcm", "password": "test"}}],
        "proxy_groups": [], "rules": [], "rule_providers": [],
    }
    response = client.post(endpoint, json={"workspace": workspace, "destination": "example.com"})
    assert response.status_code == 422, response.text
    assert "workspace" in response.json()["detail"].lower()


@pytest.mark.parametrize("key,value", [
    ("preset", "ai"),
    ("rule_packs", ["openai"]),
    ("route_intent", {"node_pools": [], "service_routes": []}),
])
def test_saved_policy_snapshot_remains_publishable_after_legacy_api_removal(client, key, value):
    store = ProfileStore(os.environ["SUBFLOW_DB_PATH"])
    saved = store.create({
        "subscription_url": "https://example.com/sub",
        key: value,
        "selected_policy": {
            "mode": "merge",
            "proxy_groups": [{"name": "OpenAI", "type": "select", "proxies": ["TW01"]}],
            "rules": ["DOMAIN-SUFFIX,chatgpt.com,OpenAI"],
        },
    })
    path = f"/profiles/{saved.id}"
    params = {"token": saved.token}
    draft = client.get(path + "/draft", params=params)
    assert draft.status_code == 200, draft.text
    assert draft.json()["mode"] == "legacy_snapshot"
    assert key not in draft.json()["request"]
    published = client.get(f"/subscribe/{saved.id}", params=params)
    assert published.status_code == 200, published.text
    assert "DOMAIN-SUFFIX,chatgpt.com,OpenAI" in published.text
    preview = client.post(path + "/upgrade-preview", params=params)
    assert preview.status_code == 200, preview.text
    assert preview.json()["request"]["service_routes"][0]["egress"] == "TW01"
    assert store.get(saved.id, saved.token).request[key] == value
    assert client.post("/profiles", json={"subscription_url": "https://example.com/sub", key: value}).status_code == 422


def test_incomplete_legacy_profile_cannot_silently_publish_default_rules(client):
    saved = ProfileStore(os.environ["SUBFLOW_DB_PATH"]).create({
        "subscription_url": "https://example.com/sub", "preset": "ai",
    })
    response = client.get(f"/subscribe/{saved.id}", params={"token": saved.token})
    assert response.status_code == 400
    assert "缺少已保存的策略快照" in response.json()["detail"]


def test_experimental_compile_target_and_old_catalogs_are_unavailable(client):
    for path in ("/templates", "/claude/templates", "/presets", "/rule-packs", "/intent/catalog", "/policy-catalog", "/subscribe"):
        assert client.get(path).status_code == 404
    assert client.post("/session", json={}).status_code == 404
    response = client.post("/compile", json={"target": "singbox", "workspace": {}})
    assert response.status_code == 400
    assert response.json()["detail"] == "unsupported target: singbox"
