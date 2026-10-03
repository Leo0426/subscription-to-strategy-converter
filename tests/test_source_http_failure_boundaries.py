"""Exercise HTTP classification through saved subscriptions and their cache."""
from contextlib import asynccontextmanager

import httpx
import pytest

from app.core.profiles import ProfileStore
from app.main import app


SOURCE = '''proxies:
- {name: Synthetic, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
'''


@pytest.fixture
async def source_client(tmp_path, monkeypatch):
    database = tmp_path / 'profiles.db'
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(database))
    state = {'status': 200, 'redirect': False, 'calls': 0}

    async def resolve(_hostname):
        return ('93.184.216.34',)

    def upstream(request):
        state['calls'] += 1
        if state['redirect'] and request.url.path == '/synthetic':
            return httpx.Response(302, headers={'Location': 'https://redirect.example/subscription'})
        return httpx.Response(state['status'], text=SOURCE if state['status'] == 200 else 'private-upstream-error')

    @asynccontextmanager
    async def outbound():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            yield client

    monkeypatch.setattr('app.core.fetcher._ensure_resolved_host_is_public', resolve)
    monkeypatch.setattr('app.core.fetcher.outbound_client', outbound)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client, state, ProfileStore(database)


async def saved_link(client, target):
    response = await client.post('/profiles', json={
        'subscription_url': 'https://example.com/synthetic?token=private-source-token', 'target': target,
    })
    assert response.status_code == 201, response.text
    profile = response.json()
    first = await client.get(profile['subscribe_url'])
    assert first.status_code == 200
    assert first.headers['x-subflow-cache'] == 'fresh'
    return profile, first


@pytest.mark.parametrize('status', [401, 403, 404, 410])
@pytest.mark.parametrize('target', ['mihomo', 'surge', 'shadowrocket-config'])
async def test_source_denial_discards_cached_target_and_cannot_revive_it(source_client, status, target):
    client, state, store = source_client
    profile, _ = await saved_link(client, target)
    state['status'] = status
    state['redirect'] = True
    denied = await client.get(profile['subscribe_url'] + '&force_refresh=true')
    assert denied.status_code == 400, denied.text[:200]
    assert f'HTTP {status}' in denied.json()['detail']
    assert 'x-subflow-cache' not in denied.headers
    assert 'private-source-token' not in denied.text
    assert 'private-upstream-error' not in denied.text
    current = store.get(profile['id'], profile['token'])
    assert target not in current.artifacts
    assert target not in current.artifact_metadata

    # A later temporary outage must not resurrect the rejected artifact.
    state['status'] = 503
    unavailable = await client.get(profile['subscribe_url'])
    assert unavailable.status_code == 400
    assert 'x-subflow-cache' not in unavailable.headers

    state['status'] = 200
    recovered = await client.get(profile['subscribe_url'])
    assert recovered.status_code == 200
    assert recovered.headers['x-subflow-cache'] == 'fresh'


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
async def test_temporary_source_failure_keeps_generation_matched_fallback(source_client, status):
    client, state, _ = source_client
    profile, first = await saved_link(client, 'surge')
    state['status'] = status
    response = await client.get(profile['subscribe_url'] + '&force_refresh=true')
    assert response.status_code == 200
    assert response.headers['x-subflow-cache'] == 'stale'
    assert response.text == first.text
