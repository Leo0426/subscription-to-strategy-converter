"""Expired or oversized short-link sessions fail explicitly at the API boundary."""

import json

import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_oversized_session_payload_returns_413():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post("/session", json={"payload": "x" * (1024 * 1024)})
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_missing_session_id_does_not_publish_default_policy(monkeypatch):
    source = "proxies:\n  - " + json.dumps({
        "name": "US01", "type": "ss", "server": "us.example.com", "port": 443,
        "cipher": "aes-128-gcm", "password": "synthetic",
    })

    async def fetch(_url):
        return source

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/subscribe", params={
            "subscription_url": "https://example.com/source",
            "session_id": "missing-session",
        })
    assert response.status_code == 410
