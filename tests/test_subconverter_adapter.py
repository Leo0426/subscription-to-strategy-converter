from __future__ import annotations

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


@pytest.mark.asyncio
async def test_adapter_requests_a_node_only_clash_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
async def test_adapter_reports_a_bounded_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
