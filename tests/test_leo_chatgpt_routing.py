"""ChatGPT's page, login and asset requests must share the AI egress.

Official network requirements: https://help.openai.com/en/articles/9247338
These tests establish deterministic template/compiled routing, not endpoint
reachability or a selected node's acceptance by ChatGPT.
"""

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.platforms.surge import build_surge_config
from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import compile_mihomo_config, config_to_workspace
from app.core.renderer import render_yaml
from app.core.template_engine import LEO_TEMPLATE_ID, apply_template, load_template
from app.ir import ProxyNode
from app.main import app


_CHATGPT_DESTINATIONS = (
    "chatgpt.com",
    "ws.chatgpt.com",
    "chat.openai.com",
    "auth0.openai.com",
    "setup.auth.openai.com",
    "cdn.oaistatic.com",
    "files.oaiusercontent.com",
    "challenges.cloudflare.com",
    "ab.chatgpt.oaistatsig.com",
    "cdn.openaimerge.com",
    "cdn.workos.com",
    "forwarder.workos.com",
    "images.workoscdn.com",
    "setup.workos.com",
    "workos.imgix.net",
    "humb.apple.com",
    "js.intercomcdn.com",
    "api-iam.intercom.io",
    "js.stripe.com",
    "o207216.ingest.sentry.io",
    "o33249.ingest.sentry.io",
    "rum.browser-intake-datadoghq.com",
    "url.ct.sendgrid.net",
)


def _nodes() -> list[ProxyNode]:
    # Keep HK first: a missing AI route must not accidentally pass because the
    # global default happens to choose the same US node as AI.
    return [
        ProxyNode(
            name=name,
            protocol="ss",
            server=f"node-{index}.example.com",
            port=443,
            extra={"cipher": "aes-128-gcm", "password": "test-only"},
        )
        for index, name in enumerate(("香港 01", "US01", "美國 02", "RUSSIA 01"))
    ]


@pytest.mark.parametrize("destination", _CHATGPT_DESTINATIONS)
def test_chatgpt_has_an_inline_ai_route_in_both_compiled_targets(destination) -> None:
    nodes = _nodes()
    config = apply_template(load_template(LEO_TEMPLATE_ID), nodes)
    compiled = compile_mihomo_config(config, nodes)
    trace = simulate_destination(config_to_workspace(compiled, nodes), destination)

    assert trace.target == "AI 服务"
    assert trace.resolved == "US01"
    assert trace.matched_rule.type in {"DOMAIN", "DOMAIN-SUFFIX"}
    rule = trace.matched_rule.raw
    assert compiled["rules"].index(rule) < next(
        index for index, entry in enumerate(compiled["rules"])
        if entry.startswith("RULE-SET,")
    )
    surge, _ = build_surge_config(
        nodes, compiled["proxy-groups"], compiled["rules"], compiled["rule-providers"]
    )
    assert rule in surge.splitlines()


def test_chatgpt_route_survives_public_preview_simulation_render_and_subscription(
    monkeypatch,
) -> None:
    nodes = _nodes()
    config = compile_mihomo_config(
        apply_template(load_template(LEO_TEMPLATE_ID), nodes), nodes
    )
    source = render_yaml({"proxies": config["proxies"]})

    async def fake_fetch(_url: str) -> str:
        return source

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch)
    client = TestClient(app)
    request = {
        "subscription_url": "https://example.com/authorized-subscription",
        "template": LEO_TEMPLATE_ID,
        "target": "mihomo",
    }
    preview = client.post("/workspace/preview", json=request)
    assert preview.status_code == 200
    workspace = preview.json()["workspace"]
    required_rules = set()
    for destination in _CHATGPT_DESTINATIONS:
        response = client.post(
            "/simulate", json={"workspace": workspace, "destination": destination}
        )
        assert response.status_code == 200
        trace = response.json()["trace"]
        assert trace["target"] == "AI 服务", destination
        assert trace["resolved"] == "US01", destination
        required_rules.add(trace["matched_rule"]["raw"])

    for target in ("mihomo", "surge"):
        rendered = client.post("/render", json={**request, "target": target})
        subscribed = client.get("/subscribe", params={**request, "target": target})
        for artifact in (rendered, subscribed):
            assert artifact.status_code == 200
            emitted = (
                YAML(typ="safe").load(artifact.text)["rules"]
                if target == "mihomo"
                else artifact.text.splitlines()
            )
            assert required_rules <= set(emitted)


@pytest.mark.parametrize("destination", ("unrelated.workos.com", "other.imgix.net"))
def test_chatgpt_login_exceptions_do_not_capture_entire_shared_providers(destination) -> None:
    nodes = _nodes()
    config = compile_mihomo_config(
        apply_template(load_template(LEO_TEMPLATE_ID), nodes), nodes
    )
    trace = simulate_destination(config_to_workspace(config, nodes), destination)
    assert trace.target == "默认代理"
    assert trace.resolved == "香港 01"
