"""Vendor ServiceRoutes must preserve more specific services' destinations."""

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace
from app.core.template_policy_transform import transform_service_routes
from app.main import app
from app.models.strategy import ServiceRoute


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


@pytest.mark.parametrize("target", ["mihomo", "surge", "shadowrocket-config"])
@pytest.mark.parametrize("services", [(), ("microsoft",), ("game-downloads", "microsoft"),
                                      ("microsoft", "game-downloads")])
def test_xbox_package_host_precedes_microsoft_provider(client, target, services):
    exits = {"microsoft": "TW01", "game-downloads": "US01"}
    response = client.post("/render", json={
        "subscription_url": "https://example.com/synthetic", "target": target,
        "service_routes": [{"service": service, "mode": "fixed", "egress": exits[service]}
                           for service in services],
    })
    rules = exported_rules(response, target)
    workspace = config_to_workspace({"rules": rules})
    trace = simulate_destination(workspace, "packagespc.xboxlive.com")
    assert trace.target == "游戏下载"
    assert not any(step.matched is None for step in trace.steps)
    assert simulate_destination(workspace, "login.live.com").target != "游戏下载"

    expected_first = "US01" if "game-downloads" in services else "DIRECT"
    if target == "mihomo":
        groups = YAML(typ="safe").load(response.text)["proxy-groups"]
        assert next(g for g in groups if g["name"] == "游戏下载")["proxies"][0] == expected_first
    else:
        line = next(line for line in response.text.splitlines() if line.startswith("游戏下载 = "))
        assert line.split(", ")[1] == expected_first


def test_vendor_route_preserves_existing_catalog_targets_and_only_prioritizes_present_rules():
    config = {
        "proxy-groups": [], "rules": [
            "DOMAIN-KEYWORD,steam,REJECT", "DOMAIN,cdn.steamcontent.com,DIRECT",
            "DOMAIN,packagespc.xboxlive.com,REJECT", "MATCH,DIRECT",
        ],
    }
    route = ServiceRoute(service="microsoft", mode="fixed", egress="DIRECT")
    rules = transform_service_routes(config, [], [route])["rules"]
    assert "DOMAIN,packagespc.xboxlive.com,REJECT" in rules
    assert not any(rule.startswith("DOMAIN-SUFFIX,steamcontent.com,") for rule in rules)
    assert rules.index("DOMAIN-KEYWORD,steam,REJECT") < rules.index("DOMAIN,cdn.steamcontent.com,DIRECT")


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
