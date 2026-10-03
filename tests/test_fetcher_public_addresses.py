"""Public subscription validation at the HTTP/DNS boundary, without real I/O."""

import ipaddress
import socket

import httpx
import pytest

from app.core import fetcher


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [
    "100.64.0.1", "100.127.255.254", "224.0.0.1", "ff02::1",
    "::ffff:100.64.0.1", "::ffff:224.0.0.1", "fec0::1",
    "64:ff9b::7f00:1", "64:ff9b::6440:1", "64:ff9b::e000:1",
])
@pytest.mark.parametrize("entry", ["url", "dns", "redirect", "fake-ip"])
async def test_public_subscription_rejects_non_public_unicast_addresses(monkeypatch, address, entry):
    requests = []
    original_client = httpx.AsyncClient
    literal = f"[{address}]" if ":" in address else address

    def resolve(host, *_args, **_kwargs):
        result = "93.184.216.34" if host == "source.test" else address
        if entry == "fake-ip":
            result = "198.18.0.1"
        family = socket.AF_INET6 if ":" in result else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (result, 0))]

    async def fallback(_host):
        return [ipaddress.ip_address(address)]

    def respond(request):
        requests.append(str(request.url))
        if request.url.host == "source.test":
            return httpx.Response(302, headers={"Location": "http://blocked.test/sub"})
        return httpx.Response(200, text="must not be fetched")

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(fetcher, "_resolve_via_udp_dns", fallback)
    monkeypatch.setattr(fetcher, "_resolve_via_doh", fallback)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(respond)))
    host = literal if entry == "url" else "source.test" if entry == "redirect" else "blocked.test"

    with pytest.raises(fetcher.FetchInvalidError):
        await fetcher.fetch_subscription(f"http://{host}/sub")
    assert requests == (["http://source.test/sub"] if entry == "redirect" else [])


@pytest.mark.asyncio
@pytest.mark.parametrize("address", [
    "93.184.216.34", "2606:4700:4700::1111", "::ffff:93.184.216.34", "64:ff9b::808:808",
])
async def test_public_subscription_still_accepts_public_unicast_addresses(monkeypatch, address):
    original_client = httpx.AsyncClient
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0)),
    ])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(
        **kw, transport=httpx.MockTransport(lambda _r: httpx.Response(200, text="subscription"))))

    assert await fetcher.fetch_subscription("https://public.test/sub") == "subscription"
