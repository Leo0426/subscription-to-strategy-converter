from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


CLASH_SUBSCRIPTION = """
proxies:
  - name: HK-01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

SIMPLE_TEMPLATE = {
    "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": []}],
    "rules": ["MATCH,PROXY"],
}

AI_TEMPLATE = {
    "proxy-groups": [
        {"name": "PROXY", "type": "select", "proxies": []},
        {"name": "AI", "type": "select", "proxies": ["PROXY", "DIRECT"]},
    ],
    "rules": ["DOMAIN-SUFFIX,openai.com,AI", "MATCH,PROXY"],
}


def test_workspace_preview_returns_workspace_graph_and_findings(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)

    response = client.post(
        "/workspace/preview",
        json={"subscription_url": "https://example.com/sub", "template": "powerfullz", "target": "mihomo"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["node_count"] == 1
    assert body["workspace"]["target"] == "mihomo"
    assert body["workspace"]["proxies"][0]["name"] == "HK-01"
    assert body["graph"]["nodes"]
    assert isinstance(body["findings"], list)


def test_simulate_endpoint_traces_openai_rule(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return AI_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)
    preview = client.post(
        "/workspace/preview",
        json={"subscription_url": "https://example.com/sub", "template": "powerfullz", "target": "mihomo"},
    ).json()

    response = client.post(
        "/simulate",
        json={"workspace": preview["workspace"], "destination": "chat.openai.com"},
    )

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace["matched_rule"]["type"] == "DOMAIN-SUFFIX"
    assert trace["matched_rule"]["target"] == "AI"
    assert trace["resolved"] in {"HK-01", "DIRECT"}


def test_compile_mihomo_endpoint_returns_yaml(monkeypatch) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return CLASH_SUBSCRIPTION

    async def fake_load_powerfullz_template(options: object) -> dict:
        return SIMPLE_TEMPLATE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr("app.core.template_engine.load_powerfullz_template", fake_load_powerfullz_template)
    client = TestClient(app)
    preview = client.post(
        "/workspace/preview",
        json={"subscription_url": "https://example.com/sub", "template": "powerfullz", "target": "mihomo"},
    ).json()

    response = client.post("/compile", json={"workspace": preview["workspace"], "target": "mihomo"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert "proxies:" in response.text
    assert "proxy-groups:" in response.text
    assert "rules:" in response.text
    assert "name: HK-01" in response.text
