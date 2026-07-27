from __future__ import annotations

import socket

import httpx
import pytest

from app.core.subconverter import (
    SubconverterError,
    convert_subscription_to_clash,
    is_subconverter_configured,
)


def test_subconverter_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUBFLOW_SUBCONVERTER_URL", raising=False)
    assert is_subconverter_configured() is False

    monkeypatch.setenv("SUBFLOW_SUBCONVERTER_URL", "http://subconverter:25500/")
    assert is_subconverter_configured() is True


async def _fake_ensure_resolved_host_is_public(hostname: str) -> None:
    return None


@pytest.mark.asyncio
async def test_adapter_requests_a_node_only_clash_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.subconverter._ensure_resolved_host_is_public",
        _fake_ensure_resolved_host_is_public,
    )
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        assert request.url.path == "/sub"
        assert request.url.params["target"] == "clash"
        assert request.url.params["list"] == "true"
        assert request.url.params["url"] == "https://example.com/sub?token=a b"
        return httpx.Response(200, text="proxies:\n  - name: HK\n    type: ss\n    server: hk.example.com\n    port: 443\n")

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.client = original_async_client(transport=transport)

        async def __aenter__(self) -> httpx.AsyncClient:
            return self.client

        async def __aexit__(self, *args: object) -> None:
            await self.client.aclose()

    monkeypatch.setenv("SUBFLOW_SUBCONVERTER_URL", "http://subconverter:25500/")
    monkeypatch.setattr("app.core.subconverter.httpx.AsyncClient", FakeAsyncClient)

    content = await convert_subscription_to_clash("https://example.com/sub?token=a b")

    assert content.startswith("proxies:")
    assert seen["url"].startswith("http://subconverter:25500/sub?")


@pytest.mark.asyncio
async def test_adapter_rejects_a_hostname_that_resolves_to_a_private_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A public-looking hostname can still DNS-rebind to an internal address;
    the adapter must reject it before asking subconverter to fetch it, the same
    way fetch_subscription() already does for the direct-parse path."""

    def fake_getaddrinfo(hostname: str, *args: object, **kwargs: object) -> list[object]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.5", 0))]

    monkeypatch.setenv("SUBFLOW_SUBCONVERTER_URL", "http://subconverter:25500")
    monkeypatch.setattr("app.core.fetcher.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(SubconverterError, match="private or local IP"):
        await convert_subscription_to_clash("https://attacker-controlled.example/sub")


@pytest.mark.asyncio
async def test_adapter_reports_a_bounded_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.core.subconverter._ensure_resolved_host_is_public",
        _fake_ensure_resolved_host_is_public,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="x" * 500)

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.client = original_async_client(transport=transport)

        async def __aenter__(self) -> httpx.AsyncClient:
            return self.client

        async def __aexit__(self, *args: object) -> None:
            await self.client.aclose()

    monkeypatch.setenv("SUBFLOW_SUBCONVERTER_URL", "http://subconverter:25500")
    monkeypatch.setattr("app.core.subconverter.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(SubconverterError, match="HTTP 500") as exc_info:
        await convert_subscription_to_clash("https://example.com/sub")

    assert len(str(exc_info.value)) < 400
