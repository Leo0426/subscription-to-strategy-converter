"""Claude login, API, downloads and content must survive provider cold start.

Destination fixtures come from https://code.claude.com/docs/en/network-config.
These exercise emitted routing, not remote service acceptance of a node.
"""

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace
from app.main import app


DESTINATIONS = (
    "api.anthropic.com",
    "statsig.anthropic.com",
    "mcp-proxy.anthropic.com",
    "assets-proxy.anthropic.com",
    "claude.ai",
    "downloads.claude.ai",
    "claude.com",
    "platform.claude.com",
    "code.claude.com",
    "bridge.claudeusercontent.com",
    "example.frame.claudeusercontent.com",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url, **_kwargs):
        return """
proxies:
  - {name: HK01, type: ss, server: hk.example.com, port: 443, cipher: aes-128-gcm, password: test}
  - {name: US01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: test}
  - {name: JP01, type: ss, server: jp.example.com, port: 443, cipher: aes-128-gcm, password: test}
"""

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app) as result:
        yield result


@pytest.mark.parametrize("mode", ("default", "fixed", "legacy"))
def test_claude_chain_survives_profile_publication_without_remote_rules(client, mode):
    request = {"subscription_url": "https://example.com/sub"}
    target, node = "AI 服务", "US01"
    if mode == "fixed":
        request["service_routes"] = [{"service": "claude", "mode": "fixed", "egress": "JP01"}]
        target, node = "Claude", "JP01"
    elif mode == "legacy":
        request["claude_policy"] = {"egress": "JP01"}
        target, node = "Claude", "JP01"

    created = client.post("/profiles", json=request)
    assert created.status_code == 201, created.text
    links = created.json()
    mihomo = client.get(links["subscribe_urls"]["clash"])
    assert mihomo.status_code == 200
    config = YAML(typ="safe").load(mihomo.text)
    workspace = config_to_workspace(config)
    required = set()
    for destination in DESTINATIONS:
        trace = simulate_destination(workspace, destination)
        assert (trace.target, trace.resolved) == (target, node), destination
        assert trace.matched_rule.type in {"DOMAIN", "DOMAIN-SUFFIX"}
        required.add(trace.matched_rule.raw)
    if mode != "pack":
        first_external = next(i for i, rule in enumerate(config["rules"]) if rule.startswith("RULE-SET,"))
        assert all(config["rules"].index(rule) < first_external for rule in required)

    # Legacy Claude policy deliberately rejects incompatible Surge templates.
    if mode != "legacy":
        for url in (links["subscribe_urls"]["surge"], links["config_urls"]["shadowrocket"]):
            response = client.get(url)
            assert response.status_code == 200, response.text
            assert required <= set(response.text.splitlines())


@pytest.mark.parametrize("target,port_rule", (("mihomo", "DST-PORT"), ("surge", "DEST-PORT"), ("shadowrocket-config", "DST-PORT")))
def test_voice_ports_do_not_force_unrelated_or_domainless_traffic_direct(client, target, port_rule):
    response = client.post("/render", json={"subscription_url": "https://example.com/sub", "target": target})
    assert response.status_code == 200, response.text
    rules = YAML(typ="safe").load(response.text)["rules"] if target == "mihomo" else response.text.splitlines()
    for port in (3478, 5349, 19302, 10000, 5350):
        assert f"{port_rule},{port},DIRECT" not in rules
    assert "DOMAIN,stun1.l.google.com,DIRECT" in rules


@pytest.mark.parametrize("target", ("mihomo", "surge", "shadowrocket-config"))
@pytest.mark.parametrize("mode", ("default", "fixed", "manual", "fallback"))
def test_claude_widgets_and_desktop_previews_follow_the_selected_service(client, target, mode):
    # Official Desktop network requirements include these service-owned suffixes.
    # The example labels stand for dynamically generated widget/preview hosts.
    routes = []
    if mode != "default":
        route = {"service": "claude", "mode": mode, "egress": "JP01"}
        if mode == "fallback":
            route["fallback"] = "US01"
        routes.append(route)
    response = client.post("/render", json={
        "subscription_url": "https://example.com/synthetic",
        "target": target,
        "service_routes": routes,
    })
    assert response.status_code == 200, response.text
    if target == "mihomo":
        config = YAML(typ="safe").load(response.text)
        rules = config["rules"]
    else:
        section = response.text.split("[Rule]\n", 1)[1].split("\n[", 1)[0]
        rules = [line for line in section.splitlines() if line and not line.startswith("#")]

    workspace = config_to_workspace({"rules": rules}, target=target)
    for destination in (
        "claudemcpcontent.com", "example.claudemcpcontent.com",
        "claude.app", "example.livepreview.claude.app",
    ):
        trace = simulate_destination(workspace, destination)
        assert trace.target == ("AI 服务" if mode == "default" else "Claude"), destination
        assert not any(step.matched is None for step in trace.steps), destination

    if mode != "default":
        members = ["JP01", "US01"] if mode == "fallback" else ["JP01"]
        group_type = "fallback" if mode == "fallback" else "select"
        if target == "mihomo":
            group = next(group for group in config["proxy-groups"] if group["name"] == "Claude")
            assert (group["type"], group["proxies"]) == (group_type, members)
        else:
            group = next(line for line in response.text.splitlines() if line.startswith("Claude = "))
            fields = [field.strip() for field in group.split("=", 1)[1].split(",")]
            assert fields[:len(members) + 1] == [group_type, *members]
