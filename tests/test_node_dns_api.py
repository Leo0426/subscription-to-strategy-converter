"""Source node DNS must survive conversion or be reported as incompatible."""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.parser import parse_clash_yaml_full
from app.main import app


_PRIVATE_RESOLVER = "https://resolver.example/dns-query/subscriber-secret"
_SOURCE = f"""
dns:
  proxy-server-nameserver:
    - {_PRIVATE_RESOLVER}
proxies:
  - {{name: US01, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: test}}
"""


@pytest.fixture
def subscription_client(monkeypatch: pytest.MonkeyPatch):
    async def fetch_source(url: str) -> str:
        return _SOURCE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch_source)
    with TestClient(app) as client:
        yield client


def test_mihomo_subscription_keeps_private_node_dns(subscription_client: TestClient) -> None:
    response = subscription_client.get(
        "/subscribe",
        params={"subscription_url": "https://example.com/sub", "target": "mihomo"},
    )

    assert response.status_code == 200
    _, config = parse_clash_yaml_full(response.text)
    assert config["dns"]["proxy-server-nameserver"] == [_PRIVATE_RESOLVER]
    assert "enhanced-mode" not in config["dns"]


@pytest.mark.parametrize("endpoint", ["subscribe", "render"])
def test_surge_reports_node_dns_loss_without_exposing_resolver_credentials(
    subscription_client: TestClient, endpoint: str,
) -> None:
    request = {"subscription_url": "https://example.com/sub", "target": "surge"}
    if endpoint == "subscribe":
        response = subscription_client.get("/subscribe", params=request)
    else:
        response = subscription_client.post("/render", json=request)

    assert response.status_code == 200
    warnings = json.loads(response.headers.get("X-Compile-Warnings", "[]"))
    assert any(warning["code"] == "unsupported_node_dns" for warning in warnings)
    assert "subscriber-secret" not in response.headers["X-Compile-Warnings"]
    assert _PRIVATE_RESOLVER not in response.headers["X-Compile-Warnings"]
    assert "DOMAIN-SUFFIX,chatgpt.com,AI 服务" in response.text
