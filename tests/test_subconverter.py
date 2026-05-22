import httpx
import pytest

from app.core.subconverter import SubconverterError, convert_subscription_to_clash
from app.models.subconverter import SubconverterOptions


@pytest.mark.asyncio
async def test_convert_subscription_to_clash_calls_subconverter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(hostname: str) -> None:
        assert hostname == "example.com"

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        assert request.url.path == "/sub"
        assert request.url.params["target"] == "clash"
        assert request.url.params["url"] == "https://example.com/sub?token=a b"
        assert request.url.params["emoji"] == "true"
        assert request.url.params["udp"] == "true"
        assert request.url.params["include"] == "香港|日本"
        assert request.url.params["exclude"] == "官网|流量"
        assert request.url.params["rename"] == "^香港@HK"
        assert request.url.params["config"] == "https://example.com/profile.ini"
        return httpx.Response(200, text="proxies: []\n")

    original_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.client = original_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self) -> httpx.AsyncClient:
            return self.client

        async def __aexit__(self, *args: object) -> None:
            await self.client.aclose()

    monkeypatch.setenv("SUBCONVERTER_BASE_URL", "http://subconverter.test:25500")
    monkeypatch.setattr("app.core.subconverter._ensure_resolved_host_is_public", fake_resolve)
    monkeypatch.setattr("app.core.subconverter.httpx.AsyncClient", FakeAsyncClient)

    content = await convert_subscription_to_clash(
        "https://example.com/sub?token=a b",
        SubconverterOptions(
            config="https://example.com/profile.ini",
            include="香港|日本",
            exclude="官网|流量",
            rename="^香港@HK",
            emoji=True,
            udp=True,
        ),
    )

    assert content == "proxies: []\n"
    assert seen["url"].startswith("http://subconverter.test:25500/sub?")


@pytest.mark.asyncio
async def test_convert_subscription_to_clash_reports_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(hostname: str) -> None:
        return None

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="failed")

    original_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.client = original_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self) -> httpx.AsyncClient:
            return self.client

        async def __aexit__(self, *args: object) -> None:
            await self.client.aclose()

    monkeypatch.setattr("app.core.subconverter._ensure_resolved_host_is_public", fake_resolve)
    monkeypatch.setattr("app.core.subconverter.httpx.AsyncClient", FakeAsyncClient)

    with pytest.raises(SubconverterError, match="HTTP 500"):
        await convert_subscription_to_clash("https://example.com/sub")


@pytest.mark.asyncio
async def test_convert_subscription_rejects_local_config_outside_community(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(hostname: str) -> None:
        return None

    monkeypatch.setattr("app.core.subconverter._ensure_resolved_host_is_public", fake_resolve)

    with pytest.raises(SubconverterError, match="under community_templates"):
        await convert_subscription_to_clash(
            "https://example.com/sub",
            SubconverterOptions(config="/tmp/profile.ini"),
        )


@pytest.mark.asyncio
async def test_local_community_config_is_exposed_as_raw_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve(hostname: str) -> None:
        return None

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["config"] = request.url.params["config"]
        return httpx.Response(200, text="proxies: []\n")

    original_async_client = httpx.AsyncClient

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            self.client = original_async_client(transport=httpx.MockTransport(handler))

        async def __aenter__(self) -> httpx.AsyncClient:
            return self.client

        async def __aexit__(self, *args: object) -> None:
            await self.client.aclose()

    monkeypatch.setenv("SUBCONVERTER_CONFIG_BASE_URL", "http://host.test:8000")
    monkeypatch.setattr("app.core.subconverter._ensure_resolved_host_is_public", fake_resolve)
    monkeypatch.setattr("app.core.subconverter.httpx.AsyncClient", FakeAsyncClient)

    await convert_subscription_to_clash(
        "https://example.com/sub",
        SubconverterOptions(config="community:Overwrite/THEINI/Ordinary/szkane/kclash.ini"),
    )

    assert seen["config"].startswith("http://host.test:8000/community/templates/raw?id=")
    assert "community%3AOverwrite%2FTHEINI%2FOrdinary%2Fszkane%2Fkclash.ini" in seen["config"]
