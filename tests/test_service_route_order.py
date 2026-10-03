"""Service overrides keep specific domains ahead of broader vendor routes."""

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.template_policy_transform import transform_service_routes
from app.ir import ProxyNode
from app.main import app
from app.models.strategy import ServiceRoute


SUBSCRIPTION = """
proxies:
  - name: TW01
    type: ss
    server: tw.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
  - name: US01
    type: ss
    server: us.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""


@pytest.mark.parametrize("order", [("openai", "apple"), ("apple", "openai")])
def test_render_prioritizes_openai_exact_domain_over_apple_suffix(monkeypatch, order) -> None:
    async def fake_fetch(_url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch)
    routes = {
        "openai": {"service": "openai", "mode": "fixed", "egress": "TW01"},
        "apple": {"service": "apple", "mode": "fixed", "egress": "US01"},
    }

    response = TestClient(app).post(
        "/render",
        json={
            "subscription_url": "https://example.com/sub",
            "target": "mihomo",
            "service_routes": [routes[service] for service in order],
        },
    )

    assert response.status_code == 200
    config = YAML(typ="safe").load(response.text)
    rules = config["rules"]
    assert rules.index("DOMAIN,humb.apple.com,OpenAI") < rules.index(
        "DOMAIN-SUFFIX,apple.com,Apple"
    )
    groups = {group["name"]: group for group in config["proxy-groups"]}
    assert groups["OpenAI"]["proxies"] == ["TW01"]
    assert groups["Apple"]["proxies"] == ["US01"]


def test_apple_override_keeps_existing_openai_exception_first(monkeypatch) -> None:
    async def fake_fetch(_url: str) -> str:
        return SUBSCRIPTION

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch)
    response = TestClient(app).post(
        "/render",
        json={
            "subscription_url": "https://example.com/sub",
            "target": "mihomo",
            "service_routes": [{"service": "apple", "mode": "fixed", "egress": "US01"}],
        },
    )

    assert response.status_code == 200
    rules = YAML(typ="safe").load(response.text)["rules"]
    assert rules.index("DOMAIN,humb.apple.com,AI 服务") < rules.index(
        "DOMAIN-SUFFIX,apple.com,Apple"
    )


def test_more_specific_suffix_precedes_broad_suffix_across_services(monkeypatch) -> None:
    services = [
        {
            "id": "narrow",
            "group": "Narrow",
            "label": "Narrow",
            "rules": [{"match": "DOMAIN-SUFFIX,api.example.com"}],
        },
        {
            "id": "broad",
            "group": "Broad",
            "label": "Broad",
            "rules": [{"match": "DOMAIN-SUFFIX,example.com"}],
        },
    ]
    monkeypatch.setattr("app.core.template_policy_transform.service_catalog", lambda: services)
    nodes = [
        ProxyNode(name="TW01", protocol="ss", server="tw.example.com", port=443),
        ProxyNode(name="US01", protocol="ss", server="us.example.com", port=443),
    ]
    config = {"proxy-groups": [], "rules": ["MATCH,DIRECT"]}

    result = transform_service_routes(
        config,
        nodes,
        [
            ServiceRoute(service="narrow", mode="fixed", egress="TW01"),
            ServiceRoute(service="broad", mode="fixed", egress="US01"),
        ],
    )

    assert result["rules"][:2] == [
        "DOMAIN-SUFFIX,api.example.com,Narrow",
        "DOMAIN-SUFFIX,example.com,Broad",
    ]
