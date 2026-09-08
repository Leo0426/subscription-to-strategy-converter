import socket

import pytest
import httpx

from app.core.fetcher import FetchError, fetch_subscription


@pytest.mark.asyncio
async def test_private_ip_url_is_rejected_before_fetch() -> None:
    with pytest.raises(FetchError, match="private or local IP"):
        await fetch_subscription("http://192.168.1.1/sub")


@pytest.mark.asyncio
async def test_localhost_is_rejected_before_fetch() -> None:
    with pytest.raises(FetchError, match="local hostnames"):
        await fetch_subscription("http://localhost/sub")


@pytest.mark.asyncio
async def test_redirect_to_private_ip_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(hostname: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/sub"})

    original_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.client = original_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self) -> httpx.AsyncClient:
            return self.client

        async def __aexit__(self, *args: object) -> None:
            await self.client.aclose()

    monkeypatch.setattr("app.core.fetcher._ensure_resolved_host_is_public", fake_resolve)
    monkeypatch.setattr("app.core.fetcher.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(FetchError, match="private or local IP"):
        await fetch_subscription("https://example.com/sub")


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_ua", ["ProviderClient/2.0", "", "  "])
async def test_subscription_user_agent_can_be_overridden_for_provider_compatibility(
    monkeypatch: pytest.MonkeyPatch, configured_ua: str,
) -> None:
    seen_agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_agents.append(request.headers["User-Agent"])
        return httpx.Response(200, text="subscription")

    original_client = httpx.AsyncClient

    def client_with_mock_transport(**kwargs: object) -> httpx.AsyncClient:
        return original_client(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setenv("SUBFLOW_SUBSCRIPTION_USER_AGENT", configured_ua)
    monkeypatch.setattr("app.core.fetcher.httpx.AsyncClient", client_with_mock_transport)
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
    ])

    assert await fetch_subscription("https://example.com/sub") == "subscription"
    if configured_ua.strip():
        assert seen_agents == [configured_ua]
    else:
        assert len(seen_agents) == 1
        assert "clash.meta/" in seen_agents[0]
        assert "mihomo/" in seen_agents[0]
