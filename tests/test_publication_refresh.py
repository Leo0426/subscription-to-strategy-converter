import asyncio
import json

import httpx
import pytest

from app.core.fetcher import FetchError
from app.main import app


SOURCE = 'proxies:\n' + '\n'.join('  - ' + json.dumps({
    'name': name, 'type': 'ss', 'server': name.lower() + '.example.com', 'port': 443,
    'cipher': 'aes-128-gcm', 'password': 'synthetic',
}) for name in ('US01', 'US02'))


def intent(node='US01'):
    return {'subscription_url': 'https://example.com/synthetic', 'target': 'surge',
            'service_routes': [{'service': 'openai', 'mode': 'fixed', 'egress': node}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('old_request_fails', [False, True])
async def test_edit_during_refresh_cannot_publish_or_cache_superseded_intent(tmp_path, monkeypatch, old_request_fails):
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    entered, release = asyncio.Event(), asyncio.Event()
    state = {'hold': False, 'fail': False}

    async def fetch(_url):
        if state['hold']:
            state['hold'] = False
            entered.set()
            await release.wait()
            if old_request_fails:
                raise FetchError('old refresh failed')
        if state['fail']:
            raise FetchError('upstream unavailable')
        return SOURCE

    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        saved = (await client.post('/profiles', json=intent())).json()
        url = saved['subscribe_urls']['surge']
        assert (await client.get(url)).status_code == 200
        state['hold'] = True
        pending = asyncio.create_task(client.get(url + '&force_refresh=true'))
        await asyncio.wait_for(entered.wait(), 3)
        updated = await client.put('/profiles/' + saved['id'], params={'token': saved['token']}, json=intent('US02'))
        assert updated.status_code == 200
        release.set()
        response = await asyncio.wait_for(pending, 3)
        assert response.status_code == 200, response.text
        assert 'OpenAI = select, US02' in response.text
        assert 'OpenAI = select, US01' not in response.text
        state['fail'] = True
        fallback = await client.get(url + '&force_refresh=true')
        assert fallback.status_code == 200
        assert fallback.headers['x-subflow-stale'] == 'true'
        assert 'OpenAI = select, US02' in fallback.text


@pytest.mark.asyncio
async def test_refreshes_coalesce_and_fresh_cache_can_be_bypassed(tmp_path, monkeypatch):
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    calls = []
    async def fetch(url):
        calls.append(url)
        await asyncio.sleep(0.03)
        return SOURCE
    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        saved = (await client.post('/profiles', json=intent())).json()
        url = saved['subscribe_urls']['surge']
        responses = await asyncio.gather(*(client.get(url) for _ in range(5)))
        assert all(r.status_code == 200 for r in responses)
        assert len(calls) == 1
        assert len({r.text for r in responses}) == 1
        cached = await client.get(url)
        assert cached.headers['x-subflow-cache'] == 'hit'
        assert len(calls) == 1
        assert cached.text.startswith('# Subflow ')
        assert 'example.com/synthetic' not in cached.text
        assert saved['token'] not in cached.text
        assert (await client.get(url + '&force_refresh=true')).status_code == 200
        assert len(calls) == 2
        denied = await client.get(url.replace(saved['token'], 'wrong'))
        assert denied.status_code == 404
        assert len(calls) == 2
        # A different target has its own artifact and retains warnings on hits.
        mihomo = await client.get(saved['subscribe_urls']['clash'])
        assert 'proxies:' in mihomo.text and '[General]' not in mihomo.text
        assert len(calls) == 3
        draft = (await client.get('/profiles/' + saved['id'] + '/draft', params={'token':saved['token']})).json()
        assert draft['generation'] == 1
        assert set(draft['publications']) == {'surge', 'mihomo'}
        assert draft['publications']['surge']['generated_at']


@pytest.mark.asyncio
async def test_malformed_new_source_never_falls_back_or_keeps_a_fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    content = [SOURCE]
    async def fetch(_url):
        return content[0]
    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        saved = (await client.post('/profiles', json=intent())).json()
        url = saved['subscribe_urls']['surge']
        assert (await client.get(url)).status_code == 200
        content[0] = 'proxies: invalid-structure'
        invalid = await client.get(url + '&force_refresh=true')
        assert invalid.status_code == 400
        assert 'x-subflow-stale' not in invalid.headers
        assert (await client.get(url)).status_code == 400



@pytest.mark.asyncio
async def test_parallel_targets_share_fetch_and_cancelled_waiter_does_not_cancel_peer(tmp_path, monkeypatch):
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    calls = []
    entered, release = asyncio.Event(), asyncio.Event()
    async def fetch(url):
        calls.append(url)
        entered.set()
        await release.wait()
        return SOURCE
    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        saved = (await client.post('/profiles', json=intent())).json()
        first = asyncio.create_task(client.get(saved['subscribe_urls']['surge']))
        await asyncio.wait_for(entered.wait(), 3)
        second = asyncio.create_task(client.get(saved['subscribe_urls']['clash']))
        await asyncio.sleep(0.02)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        release.set()
        response = await asyncio.wait_for(second, 3)
        assert response.status_code == 200, response.text
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_cache_expiry_and_edit_invalidate_old_artifacts(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.core import publication
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    now = [1000.0]
    monkeypatch.setattr(publication, 'time', SimpleNamespace(time=lambda: now[0]))
    calls = []
    async def fetch(url):
        calls.append(url)
        return SOURCE
    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        saved = (await client.post('/profiles', json=intent())).json()
        url = saved['subscribe_urls']['surge']
        first = await client.get(url)
        now[0] += 31
        second = await client.get(url)
        assert len(calls) == 2
        assert second.headers['x-subflow-generated-at'] != first.headers['x-subflow-generated-at']
        await client.put('/profiles/' + saved['id'], params={'token': saved['token']}, json=intent('US02'))
        third = await client.get(url)
        assert third.headers['x-subflow-generation'] == '2'
        assert 'OpenAI = select, US02' in third.text
        assert len(calls) == 3


@pytest.mark.asyncio
async def test_revision_change_requires_refresh_but_outage_retains_truthful_stale_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    revision, failing, calls = ['old'], [False], []
    monkeypatch.setattr('app.core.publication.publication_revision', lambda: revision[0])
    async def fetch(url):
        calls.append(url)
        if failing[0]:
            raise FetchError('upstream unavailable')
        return SOURCE
    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        saved = (await client.post('/profiles', json=intent())).json()
        url = saved['subscribe_urls']['surge']
        original = await client.get(url)
        revision[0], failing[0] = 'new', True
        stale = await client.get(url)
        assert len(calls) == 2
        assert stale.headers['x-subflow-cache'] == 'stale'
        assert stale.headers['x-subflow-revision'] == 'old'
        assert stale.text == original.text
        failing[0] = False
        fresh = await client.get(url)
        assert len(calls) == 3
        assert fresh.headers['x-subflow-cache'] == 'fresh'
        assert fresh.headers['x-subflow-revision'] == 'new'
        assert (await client.get(url)).headers['x-subflow-cache'] == 'hit'


def test_legacy_profile_migration_preserves_authorization_and_invalidates_on_edit(tmp_path):
    import hashlib
    import sqlite3
    from app.core.profiles import ProfileStore
    database = tmp_path / 'legacy.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE profiles (id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, request_json TEXT NOT NULL, artifact TEXT)')
        connection.execute('INSERT INTO profiles VALUES (?, ?, ?, ?)', (
            'legacy', hashlib.sha256(b'synthetic').hexdigest(), json.dumps(intent()), 'old config'))
    store = ProfileStore(database)
    assert store.get('legacy', 'wrong') is None
    before = store.get('legacy', 'synthetic')
    assert before.generation == 1 and before.artifacts == {'surge': 'old config'}
    assert before.artifact_metadata == {}
    assert store.update('legacy', 'synthetic', intent('US02'))
    assert not store.save_artifact('legacy', 'surge', 'late old config', expected_generation=1)
    after = store.get('legacy', 'synthetic')
    assert after.generation == 2 and after.artifacts == {}


@pytest.mark.asyncio
async def test_all_waiters_cancel_releases_inflight_capacity_and_upstream_task():
    from app.core.inflight import BusyError, SingleFlight
    flight = SingleFlight(limit=1)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def work():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    caller = asyncio.create_task(flight.run('one', work))
    await asyncio.wait_for(entered.wait(), 1)
    with pytest.raises(BusyError):
        await flight.run('two', work)
    caller.cancel()
    await asyncio.gather(caller, return_exceptions=True)
    assert cancelled.is_set()
    async def finished():
        return 'done'
    assert await flight.run('two', finished) == 'done'
