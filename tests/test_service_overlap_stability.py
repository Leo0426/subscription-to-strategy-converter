"""Vendor ServiceRoutes must not claim a separate AI service's destinations."""

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace
from app.main import app


SOURCE = """proxies:
- {name: TW01, type: ss, server: tw.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
- {name: US01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url, *, target="mihomo"):
        return SOURCE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    with TestClient(app) as client:
        yield client


def exported_rules(response, target):
    assert response.status_code == 200, response.text[:500]
    if target == "mihomo":
        return YAML(typ="safe").load(response.text)["rules"]
    return response.text.split("[Rule]\n", 1)[1].strip().splitlines()


@pytest.mark.parametrize("target", ["mihomo", "surge", "shadowrocket-config"])
@pytest.mark.parametrize("order", ["vendor-only", "vendor-first", "copilot-first"])
def test_github_override_preserves_separate_copilot_destinations(client, target, order):
    vendor = {"service": "github", "mode": "fixed", "egress": "TW01"}
    copilot = {"service": "github-copilot", "mode": "fixed", "egress": "US01"}
    routes = {"vendor-only": [vendor], "vendor-first": [vendor, copilot],
              "copilot-first": [copilot, vendor]}[order]
    response = client.post("/render", json={
        "subscription_url": "https://example.com/synthetic", "target": target,
        "service_routes": routes,
    })
    rules = exported_rules(response, target)
    workspace = config_to_workspace({"rules": rules})

    for host in ("api.githubcopilot.com", "copilot-proxy.githubusercontent.com",
                 "origin-tracker.githubusercontent.com"):
        trace = simulate_destination(workspace, host)
        assert trace.target == ("AI 服务" if order == "vendor-only" else "GitHub Copilot")
        assert not any(step.matched is None for step in trace.steps)

    for host in ("api.github.com", "raw.githubusercontent.com", "other.githubusercontent.com"):
        trace = simulate_destination(workspace, host)
        assert trace.target == "GitHub"
        assert not any(step.matched is None for step in trace.steps)


def test_openai_owned_geosite_still_precedes_broad_ai_provider(client):
    response = client.post("/render", json={
        "subscription_url": "https://example.com/synthetic", "target": "mihomo",
        "service_routes": [{"service": "openai", "mode": "fixed", "egress": "US01"}],
    })
    rules = exported_rules(response, "mihomo")
    assert rules.index("GEOSITE,openai,OpenAI") < rules.index("RULE-SET,ai-4,AI 服务")


@pytest.mark.parametrize("legacy_first", [True, False])
def test_vendor_guards_preserve_an_enabled_legacy_claude_preference(client, legacy_first):
    routes = [{"service": "claude", "mode": "legacy", "egress": "US01"},
              {"service": "github", "mode": "fixed", "egress": "TW01"}]
    response = client.post("/render", json={
        "subscription_url": "https://example.com/synthetic", "target": "mihomo",
        "service_routes": routes if legacy_first else routes[::-1],
    })
    rules = exported_rules(response, "mihomo")
    trace = simulate_destination(config_to_workspace({"rules": rules}), "claude.ai")
    assert trace.target == "Claude"
    config = YAML(typ="safe").load(response.text)
    claude = next(group for group in config["proxy-groups"] if group["name"] == "Claude")
    assert claude["proxies"][0] == "US01"
