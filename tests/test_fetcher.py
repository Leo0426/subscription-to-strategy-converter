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
