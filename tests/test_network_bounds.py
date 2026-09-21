import asyncio
import httpx
import pytest

from app.core.fetcher import FetchError, fetch_subscription


@pytest.fixture
def mock_network(monkeypatch):
    async def resolve(_host):
        return None
    monkeypatch.setattr('app.core.fetcher._ensure_resolved_host_is_public', resolve)
    original = httpx.AsyncClient
    def install(handler):
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(handler)))
    return install


@pytest.mark.asyncio
async def test_fetch_has_an_end_to_end_deadline(mock_network, monkeypatch):
    monkeypatch.setenv('SUBFLOW_FETCH_TIMEOUT', '0.05')
    async def handler(request):
        await asyncio.sleep(0.2)
        return httpx.Response(200, text='too late')
    mock_network(handler)
    with pytest.raises(FetchError, match='deadline'):
        await fetch_subscription('https://example.com/private-token')


@pytest.mark.asyncio
async def test_streamed_decoded_body_is_bounded(mock_network, monkeypatch):
    monkeypatch.setenv('SUBFLOW_MAX_SUBSCRIPTION_BYTES', '1024')
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(10):
                yield b'x' * 512
    mock_network(lambda request: httpx.Response(200, stream=Body()))
    with pytest.raises(FetchError, match='size limit'):
        await fetch_subscription('https://example.com/private-token')


@pytest.mark.asyncio
@pytest.mark.parametrize('status,attempts', [(503, 2), (403, 1)])
async def test_only_transient_failures_are_retried_without_echoing_secrets(mock_network, status, attempts):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text='secret error body')
    mock_network(handler)
    with pytest.raises(FetchError) as error:
        await fetch_subscription('https://example.com/private-token')
    assert len(calls) == attempts
    assert 'private-token' not in str(error.value)
    assert 'secret error body' not in str(error.value)


@pytest.mark.asyncio
async def test_fake_ip_fallback_uses_fast_resolver_and_cancels_slow_one(monkeypatch):
    import ipaddress
    import socket
    from app.core.fetcher import _ensure_resolved_host_is_public
    cancelled = asyncio.Event()
    async def slow(_host):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async def fast(_host):
        return [ipaddress.ip_address('93.184.216.34')]
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 0, '', ('198.18.0.1', 0))])
    monkeypatch.setattr('app.core.fetcher._resolve_via_udp_dns', slow)
    monkeypatch.setattr('app.core.fetcher._resolve_via_doh', fast)
    await asyncio.wait_for(_ensure_resolved_host_is_public('example.com'), 0.5)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_shared_pool_reuses_client_without_cross_subscription_cookies(mock_network):
    from app.core.network import network_lifespan
    calls = []
    def handler(request):
        calls.append(request)
        assert 'cookie' not in request.headers
        return httpx.Response(200, text='ok', headers={'set-cookie': 'session=private; Path=/'})
    mock_network(handler)
    async with network_lifespan():
        assert await fetch_subscription('https://example.com/first') == 'ok'
        assert await fetch_subscription('https://example.com/second') == 'ok'
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_redirects_share_one_deadline(mock_network, monkeypatch):
    monkeypatch.setenv('SUBFLOW_FETCH_TIMEOUT', '0.05')
    async def handler(request):
        await asyncio.sleep(0.03)
        return httpx.Response(302, headers={'location': '/again'})
    mock_network(handler)
    with pytest.raises(FetchError, match='deadline'):
        await fetch_subscription('https://example.com/start')


@pytest.mark.asyncio
async def test_transport_error_does_not_echo_subscription_url(mock_network):
    def handler(request):
        raise httpx.ConnectError('could not connect to ' + str(request.url), request=request)
    mock_network(handler)
    with pytest.raises(FetchError) as error:
        await fetch_subscription('https://example.com/private-token')
    assert 'private-token' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [[], None, {'Answer': None}, {'Answer': [None, []]}])
async def test_malformed_doh_response_is_a_resolver_failure(mock_network, payload):
    from app.core.fetcher import _resolve_via_doh
    mock_network(lambda request: httpx.Response(200, json=payload) if payload is not None
                 else httpx.Response(200, text='null'))
    assert await _resolve_via_doh('example.com') == []


@pytest.mark.asyncio
async def test_redirect_cookies_belong_to_one_fetch_over_a_shared_pool(mock_network):
    from app.core.network import network_lifespan
    async def handler(request):
        await asyncio.sleep(0)  # Interleave separate subscription redirect chains.
        if request.url.path in {'/one', '/two'}:
            assert 'cookie' not in request.headers
            ticket = request.url.path[1:]
            return httpx.Response(302, headers={
                'location': '/download?ticket=' + ticket,
                'set-cookie': 'ticket=' + ticket + '; Path=/; Secure',
            })
        if request.url.path == '/download':
            ticket = request.url.params['ticket']
            if request.headers.get('cookie') != 'ticket=' + ticket:
                return httpx.Response(403)
            return httpx.Response(302, headers={'location': 'https://other.example.org/final'})
        assert 'cookie' not in request.headers
        return httpx.Response(200, text='ok')
    mock_network(handler)
    async with network_lifespan():
        assert await asyncio.gather(*(fetch_subscription('https://example.com/' + name)
                                      for name in ('one', 'two'))) == ['ok', 'ok']
        assert await fetch_subscription('https://example.com/final') == 'ok'
