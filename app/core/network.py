"""Application-scoped connection pools without cookies shared across fetches."""
import asyncio
from contextlib import asynccontextmanager
from http.cookiejar import CookieJar, DefaultCookiePolicy
import math
import os
from weakref import WeakKeyDictionary

import httpx

_clients = WeakKeyDictionary()


def bounded_setting(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return min(maximum, max(minimum, value)) if math.isfinite(value) else default
    except ValueError:
        return default


def fetch_timeout() -> float:
    return bounded_setting('SUBFLOW_FETCH_TIMEOUT', 20, 0.05, 60)


def max_subscription_bytes() -> int:
    return int(bounded_setting('SUBFLOW_MAX_SUBSCRIPTION_BYTES', 5 * 1024 * 1024, 1024, 20 * 1024 * 1024))


class _NoCookies(DefaultCookiePolicy):
    def set_ok(self, cookie, request):
        return False


def _new_client():
    return httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5, pool=5), follow_redirects=False,
                            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
                            cookies=CookieJar(policy=_NoCookies()))


@asynccontextmanager
async def network_lifespan(_app=None):
    loop = asyncio.get_running_loop()
    async with _new_client() as client:
        _clients[loop] = client
        try:
            yield
        finally:
            _clients.pop(loop, None)


@asynccontextmanager
async def outbound_client():
    client = _clients.get(asyncio.get_running_loop())
    if client is not None:
        yield client
    else:
        # CLI tools and isolated tests have no ASGI lifespan.
        async with _new_client() as client:
            yield client
