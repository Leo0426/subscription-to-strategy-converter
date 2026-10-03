"""A multi-client check evaluates one source snapshot per negotiated family."""
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.api import convert
from app.main import app


SOURCE = """proxies:
- {name: US01, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
"""


@pytest.fixture
def source_client(monkeypatch):
    requests = []
    respond = [lambda request: httpx.Response(200, text=SOURCE)]

    async def resolve(_hostname):
        return ("93.184.216.34",)

    def handler(request):
        requests.append(request)
        return respond[0](request)

    @asynccontextmanager
    async def outbound():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            yield client

    monkeypatch.delenv("SUBFLOW_SUBSCRIPTION_USER_AGENT", raising=False)
    monkeypatch.delenv("SUBFLOW_SHADOWROCKET_USER_AGENT", raising=False)
    monkeypatch.setattr("app.core.fetcher._ensure_resolved_host_is_public", resolve)
    monkeypatch.setattr("app.core.fetcher.outbound_client", outbound)
    with TestClient(app) as client:
        yield client, requests, respond


@pytest.mark.parametrize(("default_target", "targets"), [
    ("mihomo", ["mihomo", "surge"]),
    ("surge", ["surge", "mihomo"]),
    ("clash", ["mihomo", "surge"]),
])
def test_check_uses_one_changing_source_snapshot_for_mihomo_and_surge(
    source_client, default_target, targets,
):
    client, requests, respond = source_client
    respond[0] = lambda request: httpx.Response(
        200, text=SOURCE if len(requests) == 1 else SOURCE.replace("US01", "US02"),
    )

    response = client.post("/check", json={
        "subscription_url": "https://example.com/synthetic", "target": default_target,
        "publication_targets": targets,
        "service_routes": [{"service": "openai", "mode": "fixed", "egress": "US01"}],
    })

    assert response.status_code == 200, response.text
    assert response.json()["can_publish"] is True, response.json()
    assert all(not result["errors"] for result in response.json()["clients"])
    assert len(requests) == 1


def test_check_negotiates_shadowrocket_separately_and_next_request_fetches_again(source_client):
    client, requests, respond = source_client
    respond[0] = lambda request: httpx.Response(200, text=(
        "[Proxy]\nSR01 = ss, sr.example.com, 443, encrypt-method=aes-128-gcm, password=synthetic\n"
        if request.headers["user-agent"].startswith("Shadowrocket/") else SOURCE
    ))
    request = {"subscription_url": "https://example.com/synthetic",
               "publication_targets": ["mihomo", "surge", "shadowrocket"]}

    for expected_fetches in (2, 4):
        response = client.post("/check", json=request)

        assert response.status_code == 200, response.text
        assert response.json()["can_publish"] is True, response.json()
        assert len(requests) == expected_fetches
        assert requests[-2].headers["user-agent"].startswith("clash.meta/")
        assert requests[-1].headers["user-agent"].startswith("Shadowrocket/")


def test_check_reports_same_failed_source_snapshot_to_both_mihomo_family_clients(source_client):
    client, requests, respond = source_client

    def flaky_source(request):
        if request.headers["user-agent"].startswith("Shadowrocket/"):
            return httpx.Response(200, text=SOURCE)
        mihomo_fetches = sum(not item.headers["user-agent"].startswith("Shadowrocket/")
                            for item in requests)
        return httpx.Response(500) if mihomo_fetches == 1 else httpx.Response(200, text=SOURCE)

    respond[0] = flaky_source
    response = client.post("/check", json={
        "subscription_url": "https://example.com/synthetic", "target": "shadowrocket",
        "publication_targets": ["shadowrocket", "mihomo", "surge"],
    })

    assert response.status_code == 200, response.text
    clients = {result["target"]: result for result in response.json()["clients"]}
    assert response.json()["can_publish"] is False
    assert not clients["shadowrocket"]["errors"]
    assert clients["mihomo"]["errors"] == clients["surge"]["errors"]
    assert "HTTP 500" in clients["surge"]["errors"][0]
    assert len(requests) == 2


@pytest.mark.parametrize("default_target", ["surge", "mihomo"])
def test_check_keeps_surge_protocol_filter_out_of_mihomo_artifact(
    source_client, monkeypatch, default_target,
):
    client, requests, respond = source_client
    source = SOURCE + "- {name: US02, type: trojan, server: tls.example.com, port: 443, password: synthetic}\n"
    respond[0] = lambda request: httpx.Response(200, text=source)
    artifacts = {}
    collect = convert.collect_target_dependencies

    def collect_artifact(target, artifact):
        artifacts[target] = artifact
        return collect(target, artifact)

    monkeypatch.setattr(convert, "collect_target_dependencies", collect_artifact)
    response = client.post("/check", json={
        "subscription_url": "https://example.com/synthetic", "target": default_target,
        "publication_targets": ["surge", "mihomo"],
        "surge_preferences": {"auto_test_protocols": ["ss"]},
    })

    assert response.status_code == 200, response.text
    assert response.json()["can_publish"] is True, response.json()
    clients = {result["target"]: result for result in response.json()["clients"]}
    assert any(warning["code"] == "auto_test_protocol_filter" for warning in clients["surge"]["warnings"])
    assert not any(warning["code"] == "auto_test_protocol_filter" for warning in clients["mihomo"]["warnings"])
    mihomo = YAML(typ="safe").load(artifacts["mihomo"])
    automatic = next(group for group in mihomo["proxy-groups"] if group["name"] == "自动选择")
    assert automatic["proxies"] == ["US01", "US02"]
    surge_automatic = next(line for line in artifacts["surge"].splitlines() if line.startswith("自动选择 ="))
    assert "US01" in surge_automatic and "US02" not in surge_automatic
    assert len(requests) == 1
