"""Bind verified DNS answers at HTTPX's socket boundary.

The logical URL stays intact for Host, TLS, cookies, redirects and proxy
selection. This small adapter isolates the hooks in the locked httpcore 1.0
connection pool; socket-level regression tests cover those hooks.
"""

import httpcore
import httpx
from httpcore._async.http_proxy import AsyncForwardHTTPConnection, AsyncTunnelHTTPConnection


ADDRESS_EXTENSION = "subflow_verified_addresses"


class _BoundOrigin(httpcore.Origin):
    def __init__(self, origin, addresses):
        super().__init__(origin.scheme, origin.host, origin.port)
        self.addresses = addresses

    def __eq__(self, other):
        # A trusted private adapter may share the logical hostname, but its
        # unbound socket must never serve a public fetch from this pool.
        return isinstance(other, _BoundOrigin) and super().__eq__(other)


class _BoundURL(httpcore.URL):
    def __init__(self, url, addresses):
        super().__init__(scheme=url.scheme, host=url.host, port=url.port, target=url.target)
        self.addresses = addresses

    @property
    def origin(self):
        return _BoundOrigin(super().origin, self.addresses)


class _BoundBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, backend, addresses):
        self.backend = backend
        self.addresses = addresses

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        for index, address in enumerate(self.addresses):
            try:
                return await self.backend.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout):
                if index == len(self.addresses) - 1:
                    raise

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


class _BoundForwardConnection:
    def __init__(self, connection, addresses):
        self.connection = connection
        self.address = addresses[0]

    def __getattr__(self, name):
        return getattr(self.connection, name)

    async def handle_async_request(self, request):
        # The proxy receives an IP absolute URI and the original Host header.
        # Pool ownership remains keyed by the logical origin, not a shared IP.
        url = request.url
        forwarded = httpcore.Request(
            request.method,
            httpcore.URL(scheme=url.scheme, host=self.address.encode(), port=url.port, target=url.target),
            headers=request.headers, content=request.stream, extensions=request.extensions,
        )
        return await self.connection.handle_async_request(forwarded)


class _BoundProxyConnect:
    def __init__(self, connection, origin, addresses):
        self.connection = connection
        address = addresses[0]
        authority = f"[{address}]:{origin.port}" if ":" in address else f"{address}:{origin.port}"
        self.authority = authority.encode()

    def __getattr__(self, name):
        return getattr(self.connection, name)

    async def handle_async_request(self, request):
        url = request.url
        connected = httpcore.Request(
            request.method,
            httpcore.URL(scheme=url.scheme, host=url.host, port=url.port, target=self.authority),
            headers=[(name, self.authority if name.lower() == b"host" else value)
                     for name, value in request.headers],
            content=request.stream, extensions=request.extensions,
        )
        return await self.connection.handle_async_request(connected)


class _BoundPool:
    def __init__(self, pool):
        self.pool = pool
        self.create_connection = pool.create_connection
        pool.create_connection = self._create_connection

    def _create_connection(self, origin):
        connection = self.create_connection(origin)
        addresses = getattr(origin, "addresses", ())
        if addresses and isinstance(connection, httpcore.AsyncHTTPConnection):
            connection._network_backend = _BoundBackend(connection._network_backend, addresses)
        elif addresses and isinstance(connection, AsyncForwardHTTPConnection):
            connection = _BoundForwardConnection(connection, addresses)
        elif addresses and isinstance(connection, AsyncTunnelHTTPConnection):
            # Only the CONNECT authority changes. Keep the tunnel's logical
            # remote origin so its subsequent TLS handshake verifies that name.
            connection._connection = _BoundProxyConnect(connection._connection, origin, addresses)
        elif addresses:
            raise httpx.UnsupportedProtocol("outbound transport cannot bind verified addresses")
        return connection

    async def handle_async_request(self, request):
        addresses = request.extensions.get(ADDRESS_EXTENSION)
        if addresses:
            request.url = _BoundURL(request.url, addresses)
        return await self.pool.handle_async_request(request)

    async def __aenter__(self):
        await self.pool.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self.pool.__aexit__(*args)

    async def aclose(self):
        await self.pool.aclose()


def bind_public_addresses(client: httpx.AsyncClient) -> httpx.AsyncClient:
    # Route on the original URL first, so NO_PROXY and proxy credentials retain
    # their normal HTTPX semantics. Mock/ASGI transports have no TCP pool.
    transports = {client._transport, *client._mounts.values()} - {None}
    for transport in transports:
        if isinstance(transport, httpx.AsyncHTTPTransport):
            transport._pool = _BoundPool(transport._pool)
    return client
