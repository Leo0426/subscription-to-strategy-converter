"""OpenAI customization must preserve the login/asset routes from Leo.

RulePacks replace the template rule graph. Exercise the public composition path
so keeping leo.yaml current cannot hide a stale compatibility RulePack source.
"""

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace
from app.core.template_engine import LEO_TEMPLATE_ID, load_template
from app.main import app


_SUBSCRIPTION = """
proxies:
  - name: HK01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: test-only
  - name: US01
    type: ss
    server: us1.example.com
    port: 443
    cipher: aes-128-gcm
    password: test-only
  - name: US02
    type: ss
    server: us2.example.com
    port: 443
    cipher: aes-128-gcm
    password: test-only
"""


_DESTINATIONS = (
    "chatgpt.com",
    "ws.chatgpt.com",
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
)


def test_openai_rule_pack_keeps_the_same_core_domain_contract_as_leo() -> None:
    response = TestClient(app).get("/rule-packs")
    assert response.status_code == 200
    openai = next(pack for pack in response.json()["packs"] if pack["id"] == "openai")
    workspace = config_to_workspace(load_template(LEO_TEMPLATE_ID))
    for destination in _DESTINATIONS:
        trace = simulate_destination(workspace, destination)
        assert trace.target == "AI 服务", destination
        assert trace.matched_rule.type in {"DOMAIN", "DOMAIN-SUFFIX"}
        equivalent = f"{trace.matched_rule.type},{trace.matched_rule.match},OpenAI"
        assert equivalent in openai["rules"], destination


@pytest.mark.parametrize("target", ("mihomo", "surge"))
@pytest.mark.parametrize("mode", ("pack", "pack-and-route", "route"))
def test_openai_composition_keeps_login_assets_and_challenge_on_the_chosen_egress(
    monkeypatch, target, mode,
) -> None:
    async def fake_fetch(_url: str) -> str:
        return _SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch)
    client = TestClient(app)
    request = {
        "subscription_url": "https://example.com/authorized-subscription",
        "target": target,
    }
    if mode in {"pack", "pack-and-route"}:
        request["rule_packs"] = ["openai"]
    if mode in {"pack-and-route", "route"}:
        request["route_intent"] = {
            "node_pools": [
                {
                    "id": "chosen-us",
                    "name": "Chosen US node",
                    "regions": ["us"],
                    "include_keywords": ["US02"],
                }
            ],
            "routes": [
                {
                    "service": "openai",
                    "primary_pool": "chosen-us",
                    "final_target": "REJECT",
                }
            ],
        }
    expected_node = "US01" if mode == "pack" else "US02"
    preview = client.post("/workspace/preview", json=request)
    assert preview.status_code == 200
    body = preview.json()
    assert body["resolved_policy"]["mode"] == "replace"
    required_rules = set()
    for destination in _DESTINATIONS:
        response = client.post(
            "/simulate",
            json={"workspace": body["workspace"], "destination": destination},
        )
        assert response.status_code == 200
        trace = response.json()["trace"]
        assert trace["target"] == "OpenAI", destination
        assert trace["resolved"] == expected_node, destination
        required_rules.add(trace["matched_rule"]["raw"])

    rendered = client.post("/render", json=request)
    compiled = client.post(
        "/compile", json={"workspace": body["workspace"], "target": target}
    )
    for artifact in (rendered, compiled):
        assert artifact.status_code == 200
        emitted = (
            YAML(typ="safe").load(artifact.text)["rules"]
            if target == "mihomo"
            else artifact.text.splitlines()
        )
        assert required_rules <= set(emitted)
        fallback = next(
            index for index, rule in enumerate(emitted)
            if rule.startswith(("MATCH,", "FINAL,"))
        )
        assert all(emitted.index(rule) < fallback for rule in required_rules)
