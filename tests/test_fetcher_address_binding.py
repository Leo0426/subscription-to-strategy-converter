"""Exercise DNS pinning through the real HTTP client and TCP boundary."""

import asyncio
from contextlib import asynccontextmanager
import socket
import ssl
from urllib.request import getproxies_environment

import anyio
from anyio.streams.tls import TLSStream
from dns.asyncresolver import Resolver
import pytest

from app.core.fetcher import FetchInvalidError, fetch_subscription, request_text
from app.core.network import network_lifespan
from app.core.rule_source_audit import PublicRuleSourceFetcher


PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def clean_proxy_environment(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    # HTTPX also inherits macOS/Windows system proxies. These socket tests
    # control their own proxy endpoints through environment variables only.
    monkeypatch.setattr("httpx._utils.getproxies", getproxies_environment)


@asynccontextmanager
async def http_server(handler, *, keep_alive=False):
    async def serve(reader, writer):
        try:
            while True:
                try:
                    request = await reader.readuntil(b"\r\n\r\n")
                except asyncio.IncompleteReadError:
                    break
                status, headers, body = handler(request)
                connection = "keep-alive" if keep_alive else "close"
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: {connection}\r\n".encode()
                    + headers + b"\r\n" + body
                )
                await writer.drain()
                if not keep_alive:
                    break
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        yield server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["subscription", "rule-audit"])
async def test_dns_rebinding_cannot_change_the_connected_address(monkeypatch, source_kind):
    dns_answers = []
    connections = []
    requests = []
    original_dns = socket.getaddrinfo
    original_connect = anyio.connect_tcp

    def dns(host, port, *args, **kwargs):
        if host in {"rebind.test", b"rebind.test"}:
            address = PUBLIC_IP if not dns_answers else "127.0.0.1"
            dns_answers.append(address)
            return original_dns(address, port, *args, **kwargs)
        return original_dns(host, port, *args, **kwargs)

    def response(request):
        requests.append(request)
        return "200 OK", b"", b"subscription"

    async with http_server(response) as port:
        async def connect(remote_host, remote_port, **kwargs):
            connections.append(remote_host)
            # Model the approved public endpoint using a local fixture. An
            # unpinned hostname takes the real resolver's rebound loopback path.
            host = "127.0.0.1" if remote_host == PUBLIC_IP else remote_host
            return await original_connect(host, port, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", dns)
        monkeypatch.setattr(anyio, "connect_tcp", connect)
        url = f"http://rebind.test:{port}/sub?token=test"
        if source_kind == "subscription":
            assert await fetch_subscription(url) == "subscription"
        else:
            async with PublicRuleSourceFetcher() as fetcher:
                assert (await fetcher.fetch(url))["content"] == b"subscription"

    assert connections == [PUBLIC_IP]
    assert dns_answers == [PUBLIC_IP]
    assert f"Host: rebind.test:{port}\r\n".encode() in requests[0]


@pytest.mark.asyncio
async def test_http_proxy_receives_verified_ip_with_original_host(monkeypatch):
    requests = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 0)),
    ])

    def response(request):
        requests.append(request)
        return "200 OK", b"", b"proxied subscription"

    async with http_server(response) as port:
        monkeypatch.setenv("HTTP_PROXY", f"http://user:password@127.0.0.1:{port}")
        assert await fetch_subscription("http://rebind.test/sub?token=test") == "proxied subscription"

    assert requests[0].startswith(f"GET http://{PUBLIC_IP}/sub?token=test HTTP/1.1\r\n".encode())
    assert b"Host: rebind.test\r\n" in requests[0]
    assert b"Proxy-Authorization: Basic dXNlcjpwYXNzd29yZA==\r\n" in requests[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy_scheme", ["http", "https"])
@pytest.mark.parametrize("address", [PUBLIC_IP, "2606:4700:4700::1111"])
async def test_https_proxy_connects_to_verified_ip_and_checks_original_tls_name(monkeypatch, proxy_scheme, address):
    requests = []
    tls_names = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 0)),
    ])

    async def tls(cls, stream, *, hostname, ssl_context, **kwargs):
        tls_names.append(hostname)
        assert ssl_context.check_hostname is True
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        return stream

    async def proxy(reader, writer):
        try:
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n\r\nsubscription")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    monkeypatch.setattr(TLSStream, "wrap", classmethod(tls))
    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setenv("HTTPS_PROXY", f"{proxy_scheme}://user:password@127.0.0.1:{port}")
        assert await fetch_subscription("https://rebind.test/sub") == "subscription"

    authority = f"[{address}]:443" if ":" in address else f"{address}:443"
    assert requests[0].startswith(f"CONNECT {authority} HTTP/1.1\r\n".encode())
    assert f"Host: {authority}\r\n".encode() in requests[0]
    assert b"Proxy-Authorization:" in requests[0]
    assert b"Host: rebind.test\r\n" in requests[1]
    assert b"Proxy-Authorization:" not in requests[1]
    assert tls_names == (["127.0.0.1"] if proxy_scheme == "https" else []) + ["rebind.test"]


@pytest.mark.asyncio
async def test_redirect_hostname_resolving_private_is_rejected_before_connection(monkeypatch):
    connections = []
    original_dns = socket.getaddrinfo
    original_connect = anyio.connect_tcp

    def dns(host, port, *args, **kwargs):
        address = "127.0.0.1" if host == "redirect.test" else PUBLIC_IP
        return original_dns(address, port, *args, **kwargs)

    async with http_server(lambda request: (
        "302 Found", b"Location: http://redirect.test/private\r\n", b"",
    )) as port:
        async def connect(remote_host, remote_port, **kwargs):
            connections.append(remote_host)
            return await original_connect("127.0.0.1", port, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", dns)
        monkeypatch.setattr(anyio, "connect_tcp", connect)
        with pytest.raises(FetchInvalidError, match="private or local IP"):
            await fetch_subscription("http://source.test/sub")

    assert connections == [PUBLIC_IP]


@pytest.mark.asyncio
async def test_fake_ip_connects_to_the_public_fallback_answer(monkeypatch):
    connections = []
    original_dns = socket.getaddrinfo
    original_connect = anyio.connect_tcp

    def dns(host, port, *args, **kwargs):
        address = "198.18.0.1" if host == "fake.test" else host
        return original_dns(address, port, *args, **kwargs)

    async def public_dns(self, host, kind):
        return [PUBLIC_IP] if kind == "A" else []

    async with http_server(lambda request: ("200 OK", b"", b"subscription")) as port:
        async def connect(remote_host, remote_port, **kwargs):
            connections.append(remote_host)
            if remote_host != PUBLIC_IP:
                raise OSError("test DNS-over-HTTPS endpoint unavailable")
            return await original_connect("127.0.0.1", port, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", dns)
        monkeypatch.setattr(Resolver, "resolve", public_dns)
        monkeypatch.setattr(anyio, "connect_tcp", connect)
        assert await fetch_subscription("http://fake.test/sub") == "subscription"

    assert PUBLIC_IP in connections
    assert "fake.test" not in connections
    assert "198.18.0.1" not in connections


@pytest.mark.asyncio
async def test_no_proxy_matches_original_hostname_before_address_binding(monkeypatch):
    connections = []
    original_connect = anyio.connect_tcp
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 0)),
    ])

    async with http_server(lambda request: ("200 OK", b"", b"direct subscription")) as port:
        async def connect(remote_host, remote_port, **kwargs):
            connections.append(remote_host)
            return await original_connect("127.0.0.1", port, **kwargs)

        monkeypatch.setattr(anyio, "connect_tcp", connect)
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("NO_PROXY", ".test")
        assert await fetch_subscription("http://direct.test/sub") == "direct subscription"

    assert connections == [PUBLIC_IP]


@pytest.mark.asyncio
async def test_https_pool_keeps_tls_names_separate_for_a_shared_ip(monkeypatch):
    tls_names = []
    connections = []
    requests = []
    original_connect = anyio.connect_tcp
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC_IP, 0)),
    ])

    async def tls(cls, stream, *, hostname, ssl_context, **kwargs):
        tls_names.append(hostname)
        assert ssl_context.check_hostname is True
        assert ssl_context.verify_mode == ssl.CERT_REQUIRED
        return stream

    def response(request):
        requests.append(request)
        return "200 OK", b"", b"subscription"

    async with http_server(response, keep_alive=True) as port:
        async def connect(remote_host, remote_port, **kwargs):
            connections.append(remote_host)
            return await original_connect("127.0.0.1", port, **kwargs)

        monkeypatch.setattr(anyio, "connect_tcp", connect)
        monkeypatch.setattr(TLSStream, "wrap", classmethod(tls))
        async with network_lifespan():
            for host in ("first.test", "second.test", "first.test"):
                assert await fetch_subscription(f"https://{host}:{port}/sub") == "subscription"

    assert connections == [PUBLIC_IP, PUBLIC_IP]
    assert tls_names == ["first.test", "second.test"]
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_retry_rejects_rebound_private_address(monkeypatch):
    dns_answers = []
    connections = []
    original_dns = socket.getaddrinfo

    def dns(host, port, *args, **kwargs):
        address = PUBLIC_IP if not dns_answers else "127.0.0.1"
        dns_answers.append(address)
        return original_dns(address, port, *args, **kwargs)

    async def connect(remote_host, remote_port, **kwargs):
        connections.append(remote_host)
        raise OSError("controlled connection failure")

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(anyio, "connect_tcp", connect)
    with pytest.raises(FetchInvalidError, match="private or local IP"):
        await fetch_subscription("http://retry.test/sub")

    assert connections == [PUBLIC_IP]
    assert dns_answers == [PUBLIC_IP, "127.0.0.1"]


@pytest.mark.asyncio
async def test_public_fetch_cannot_reuse_an_unbound_private_adapter_connection(monkeypatch):
    connections = []
    original_dns = socket.getaddrinfo
    original_connect = anyio.connect_tcp

    def dns(host, port, *args, **kwargs):
        address = PUBLIC_IP if port is None else "127.0.0.1"
        return original_dns(address, port, *args, **kwargs)

    async with http_server(lambda request: ("200 OK", b"", b"private adapter"), keep_alive=True) as private_port:
        async with http_server(lambda request: ("200 OK", b"", b"public subscription"), keep_alive=True) as public_port:
            async def connect(remote_host, remote_port, **kwargs):
                connections.append(remote_host)
                port = public_port if remote_host == PUBLIC_IP else private_port
                return await original_connect("127.0.0.1", port, **kwargs)

            monkeypatch.setattr(socket, "getaddrinfo", dns)
            monkeypatch.setattr(anyio, "connect_tcp", connect)
            async with network_lifespan():
                assert await request_text("http://shared.test/adapter", public=False) == "private adapter"
                assert await fetch_subscription("http://shared.test/sub") == "public subscription"

    assert connections == ["shared.test", PUBLIC_IP]
