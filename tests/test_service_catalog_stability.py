"""Catalog service ownership stays narrow in each public compiled artifact."""

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace
from app.main import app


SOURCE = """proxies:
- {name: HK01, type: ss, server: hk.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
- {name: US01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
- {name: TW01, type: ss, server: tw.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url, *, target="mihomo"):
        return SOURCE

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app) as test_client:
        yield test_client


def compiled_workspace(client, target, service=None):
    response = client.post("/render", json={
        "subscription_url": "https://source.example.com/subscription",
        "target": target,
        "service_routes": [{"service": service, "mode": "fixed", "egress": "TW01"}] if service else [],
    })
    assert response.status_code == 200, response.text
    if target == "mihomo":
        config = YAML(typ="safe").load(response.text)
    else:
        section = response.text.split("[Rule]\n", 1)[1].split("\n[", 1)[0]
        config = {"rules": [line for line in section.splitlines() if line and not line.startswith("#")]}
    return config_to_workspace(config, target=target)


@pytest.mark.parametrize("target", ["mihomo", "surge", "shadowrocket-config"])
def test_openai_route_owns_only_catalogued_intercom_hosts(client, target):
    workspace = compiled_workspace(client, target, "openai")

    for destination in ("api-iam.intercom.io", "js.intercomcdn.com"):
        trace = simulate_destination(workspace, destination)
        assert trace.target == "OpenAI", (destination, trace.matched_rule)

    # api.intercom.io is Intercom's general REST API, not the catalogued
    # ChatGPT integration host. Other CDN tenants must remain outside it too.
    for destination in ("api.intercom.io", "api.eu.intercom.io", "other.intercomcdn.com"):
        trace = simulate_destination(workspace, destination)
        assert trace.target not in {"OpenAI", "AI 服务"}, (destination, trace.matched_rule)


@pytest.mark.parametrize("target", ["mihomo", "surge", "shadowrocket-config"])
def test_copilot_route_keeps_suggestions_and_public_code_detection_on_its_egress(client, target):
    workspace = compiled_workspace(client, target, "github-copilot")

    for destination in (
        "api.githubcopilot.com",
        "copilot-proxy.githubusercontent.com",
        "origin-tracker.githubusercontent.com",
    ):
        trace = simulate_destination(workspace, destination)
        assert trace.target == "GitHub Copilot", (destination, trace.matched_rule)

    trace = simulate_destination(workspace, "raw.githubusercontent.com")
    assert trace.target != "GitHub Copilot"


@pytest.mark.parametrize("target", ["mihomo", "surge", "shadowrocket-config"])
def test_default_copilot_public_code_detection_has_a_definite_ai_route(client, target):
    workspace = compiled_workspace(client, target)

    trace = simulate_destination(workspace, "origin-tracker.githubusercontent.com")

    assert trace.target == "AI 服务"
    assert not any(step.matched is None for step in trace.steps)


@pytest.mark.parametrize('target', ['mihomo', 'surge', 'shadowrocket-config'])
def test_gemini_route_keeps_official_script_assets_on_its_exit(client, target):
    workspace = compiled_workspace(client, target, 'gemini')
    trace = simulate_destination(workspace, 'gemini.gstatic.com')
    assert trace.target == 'Gemini', trace.matched_rule
    assert not any(step.matched is None for step in trace.steps)
    for unrelated in ('fonts.gstatic.com', 'other.gemini.gstatic.com'):
        assert simulate_destination(workspace, unrelated).target != 'Gemini'


@pytest.mark.parametrize('target', ['surge', 'shadowrocket-config'])
def test_ini_default_gemini_assets_keep_ai_provider_semantics(client, target):
    workspace = compiled_workspace(client, target)
    trace = simulate_destination(workspace, 'gemini.gstatic.com')
    assert trace.target == 'AI 服务', trace.matched_rule
    assert trace.matched_rule.type == 'DOMAIN'
    # The earlier Claude provider also targets AI; its contents are deliberately
    # unknown to the simulator. The broad Google provider must remain later.
    google = next(rule for rule in workspace.rules if '/Google/' in rule.match)
    assert trace.matched_rule.index < google.index


@pytest.mark.parametrize('target', ['mihomo', 'surge', 'shadowrocket-config'])
def test_openai_challenge_exception_is_an_exact_shared_host(client, target):
    workspace = compiled_workspace(client, target, 'openai')
    assert simulate_destination(workspace, 'challenges.cloudflare.com').target == 'OpenAI'
    assert simulate_destination(workspace, 'unrelated.challenges.cloudflare.com').target != 'OpenAI'
