"""ServiceRoute dependencies are declarations, independent of request order."""

from copy import deepcopy
from itertools import permutations

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.parsers.clash import clash_to_ir
from app.core.template_engine import LEO_TEMPLATE_ID, apply_template, load_template
from app.core.template_policy_transform import transform_service_routes
from app.main import app
from app.models.strategy import ServiceRoute


SOURCE = """
proxies:
  - {name: TW01, type: ss, server: tw.example.com, port: 443, cipher: aes-128-gcm, password: test}
  - {name: US01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: test}
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url, *, target="mihomo"):
        return SOURCE

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("target", ["mihomo", "surge", "shadowrocket-config"])
def test_manual_service_can_reference_another_generated_service_in_either_order(client, target):
    routes = [
        {"service": "openai", "mode": "manual", "egress": "Gemini"},
        {"service": "gemini", "mode": "fixed", "egress": "TW01"},
    ]
    for order in permutations(routes):
        response = client.post("/render", json={
            "subscription_url": "https://example.com/sub",
            "target": target,
            "service_routes": order,
        })

        assert response.status_code == 200, response.text
        if target == "mihomo":
            groups = {group["name"]: group for group in YAML(typ="safe").load(response.text)["proxy-groups"]}
            assert groups["OpenAI"]["proxies"] == ["Gemini"]
            assert groups["Gemini"]["proxies"] == ["TW01"]
        else:
            assert "OpenAI = select, Gemini" in response.text
            assert "Gemini = select, TW01" in response.text


@pytest.mark.parametrize("publication_targets", [None, ["mihomo", "surge", "shadowrocket"]])
def test_profile_accepts_forward_service_reference(client, publication_targets):
    intent = {
        "subscription_url": "https://example.com/sub",
        "publication_targets": publication_targets,
        "service_routes": [
            {"service": "openai", "mode": "manual", "egress": "Gemini"},
            {"service": "gemini", "mode": "fixed", "egress": "TW01"},
        ],
    }

    checked = client.post("/check", json=intent)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is True
    created = client.post("/profiles", json=intent)
    assert created.status_code == 201, created.text
    links = created.json()
    draft = client.get(f"/profiles/{links['id']}/draft", params={"token": links["token"]})
    assert draft.json()["request"]["service_routes"][0]["egress"] == "Gemini"


@pytest.mark.parametrize("egress,other_routes", [
    ("Missing", []),
    ("Gemini", []),
    ("Gemini", [{"service": "gemini", "mode": "fixed", "egress": "TW01", "enabled": False}]),
    ("OpenAI", []),
])
def test_manual_service_rejects_missing_disabled_or_self_group(client, egress, other_routes):
    response = client.post("/render", json={
        "subscription_url": "https://example.com/sub",
        "service_routes": [{"service": "openai", "mode": "manual", "egress": egress}, *other_routes],
    })

    assert response.status_code == 400
    assert "出口不存在或不符合模式" in response.json()["detail"]


@pytest.mark.parametrize("route", [
    {"service": "openai", "mode": "fixed", "egress": "Gemini"},
    {"service": "openai", "mode": "fallback", "egress": "Gemini", "fallback": "TW01"},
    {"service": "openai", "mode": "fallback", "egress": "TW01", "fallback": "Gemini"},
])
def test_fixed_and_fallback_services_cannot_delegate_to_generated_group(client, route):
    response = client.post("/render", json={
        "subscription_url": "https://example.com/sub",
        "service_routes": [route, {"service": "gemini", "mode": "fixed", "egress": "US01"}],
    })

    assert response.status_code == 400


@pytest.mark.parametrize("publication_targets", [None, ["mihomo", "surge", "shadowrocket"]])
def test_generated_service_cycle_still_blocks_checks_and_profiles(client, publication_targets):
    routes = [
        {"service": "openai", "mode": "manual", "egress": "Gemini"},
        {"service": "gemini", "mode": "manual", "egress": "OpenAI"},
    ]
    for order in permutations(routes):
        intent = {
            "subscription_url": "https://example.com/sub",
            "publication_targets": publication_targets,
            "service_routes": order,
        }
        checked = client.post("/check", json=intent)
        assert checked.status_code == 200, checked.text
        assert checked.json()["can_publish"] is False
        assert any(finding["code"] == "group_cycle" for finding in checked.json()["findings"])
        assert client.post("/profiles", json=intent).status_code == 400
        for target in ("mihomo", "surge", "shadowrocket-config"):
            assert client.post("/render", json={**intent, "target": target}).status_code == 400
    assert client.get("/profiles").json()["profiles"] == []


def test_forward_service_reference_does_not_change_source_policy_or_nodes():
    source = YAML(typ="safe").load(SOURCE)
    nodes = [clash_to_ir(proxy) for proxy in source["proxies"]]
    config = apply_template(load_template(LEO_TEMPLATE_ID), nodes)
    original = deepcopy((config, nodes, source))

    transformed = transform_service_routes(config, nodes, [
        ServiceRoute(service="openai", mode="manual", egress="Gemini"),
        ServiceRoute(service="gemini", mode="fixed", egress="TW01"),
    ])

    assert (config, nodes, source) == original
    assert next(group for group in transformed["proxy-groups"] if group["name"] == "OpenAI")["proxies"] == ["Gemini"]
