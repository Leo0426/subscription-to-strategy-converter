"""Native Shadowrocket bytes belong to the airport, never to our node writer."""
import base64
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.main import app


URI_SOURCE = base64.b64encode((
    '# airport subscription\n'
    'anytls://test-secret@native.example:443?insecure=0&sni=edge.example&future-option=keep#US%20%2001\n'
).encode()).decode() + '\n'
INI_SOURCE = '''# Airport native profile
[General]
dns-server = system, 192.0.2.1
future-airport-setting = keep
ipv6 = true

[Host]
edge.example = 192.0.2.2

[Proxy]
US  01 = anytls, native.example, 443, password=test-secret, future-option=keep

[Proxy Group]
默认代理 = select, US  01

[Rule]
DOMAIN,airport-old-rule.example,默认代理
FINAL,默认代理

[URL Rewrite]
^http://provider.example https://provider.example 302
'''
YAML_SOURCE = '''port: 12345
dns:
  nameserver: [192.0.2.11]
proxies:
  - {name: US  01, type: anytls, server: yaml.example, port: 443, password: yaml-secret}
'''


@pytest.fixture
def native_api(monkeypatch, tmp_path):
    state = {'source': URI_SOURCE, 'requests': []}
    original = httpx.AsyncClient

    def handler(request):
        ua = request.headers.get('user-agent', '')
        state['requests'].append(ua)
        return httpx.Response(200, text=state['source'] if 'Shadowrocket/' in ua else YAML_SOURCE)

    monkeypatch.setattr('httpx.AsyncClient', lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)))
    monkeypatch.setattr('socket.getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('93.184.216.34', 0)),
    ])
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    monkeypatch.delenv('SUBFLOW_SUBSCRIPTION_USER_AGENT', raising=False)
    monkeypatch.delenv('SUBFLOW_SHADOWROCKET_USER_AGENT', raising=False)
    with TestClient(app) as client:
        yield client, state


@pytest.mark.parametrize('endpoint', ['render', 'subscribe', 'profile'])
def test_native_shadowrocket_subscription_is_returned_unchanged(native_api, endpoint):
    client, state = native_api
    request = {'subscription_url': 'https://example.com/all/', 'target': 'shadowrocket'}
    if endpoint == 'render':
        response = client.post('/render', json=request)
    elif endpoint == 'subscribe':
        response = client.get('/subscribe', params=request)
    else:
        saved = client.post('/profiles', json={**request, 'publication_targets': ['shadowrocket']})
        assert saved.status_code == 201, saved.text
        url = saved.json()['subscribe_urls']['shadowrocket']
        response = client.get(url)
        assert client.get(url).text == URI_SOURCE
        assert response.headers.get('X-Subflow-Generation')
    assert response.status_code == 200, response.text
    assert response.text == URI_SOURCE
    assert response.headers['content-type'].startswith('text/plain')
    assert all('Shadowrocket/' in ua for ua in state['requests'])


def test_nodes_only_native_source_adds_policy_without_common_settings(native_api):
    client, _ = native_api
    response = client.post('/render', json={
        'subscription_url': 'https://example.com/all/', 'target': 'shadowrocket-config',
        'service_routes': [{'service': 'openai', 'mode': 'fixed', 'egress': 'US 01'}],
    })
    assert response.status_code == 200, response.text
    assert '[General]' not in response.text
    assert '[Host]' not in response.text
    assert '[Proxy]' not in response.text
    assert 'OpenAI = select, US  01' in response.text
    assert 'DOMAIN-SUFFIX,chatgpt.com,OpenAI' in response.text
    assert 'test-secret' not in response.text


@pytest.mark.parametrize('container', ['plain', 'commented', 'base64'])
def test_full_native_shadowrocket_profile_keeps_all_nonrouting_sections(native_api, container):
    from app.core.platforms.surge_profile import _sections
    client, state = native_api
    original = ('// airport comment\n' if container == 'commented' else '') + INI_SOURCE
    state['source'] = base64.b64encode(original.encode()).decode() if container == 'base64' else original
    response = client.post('/render', json={'subscription_url': 'https://example.com/all/', 'target': 'shadowrocket-config'})
    assert response.status_code == 200, response.text
    old, new = dict(_sections(original)), dict(_sections(response.text))
    for name in ['', 'general', 'host', 'proxy', 'url rewrite']:
        assert old[name] == new[name]
    assert '默认代理 = select, US  01' in new['proxy group']
    assert 'Subflow 默认代理 = select,' in new['proxy group']
    assert 'FINAL,Subflow 默认代理' in new['rule']
    assert 'airport-old-rule.example' not in new['rule']


def test_each_selected_client_is_checked_against_its_own_native_response(native_api):
    client, state = native_api
    request = {'subscription_url': 'https://example.com/all/', 'target': 'mihomo',
               'publication_targets': ['mihomo', 'shadowrocket']}
    checked = client.post('/check', json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()['can_publish'] is True
    assert any('Shadowrocket/' in ua for ua in state['requests'])
    assert any('mihomo/' in ua for ua in state['requests'])
    mihomo = client.post('/render', json={**request, 'target': 'mihomo'})
    parsed = YAML(typ='safe').load(mihomo.text)
    assert parsed['port'] == 12345
    assert parsed['dns'] == {'nameserver': ['192.0.2.11']}
    assert parsed['proxies'][0]['server'] == 'yaml.example'


def test_paired_refresh_keeps_native_bytes_and_original_node_names(native_api):
    client, state = native_api
    saved = client.post('/profiles', json={'subscription_url': 'https://example.com/all/',
                        'target': 'shadowrocket', 'publication_targets': ['shadowrocket']})
    assert saved.status_code == 201, saved.text
    links = saved.json()
    client.get(links['subscribe_urls']['shadowrocket'])
    state['source'] = URI_SOURCE.replace(URI_SOURCE, base64.b64encode(
        b'anytls://new-secret@new.example:443?future-option=still-keep#US%20%2002\n').decode())
    nodes = client.get(links['subscribe_urls']['shadowrocket'] + '&force_refresh=true')
    config = client.get(links['config_urls']['shadowrocket'] + '&force_refresh=true')
    assert nodes.status_code == config.status_code == 200
    assert nodes.text == state['source']
    assert 'US  02' in config.text
    assert 'US  01' not in config.text
    assert 'new-secret' not in json.dumps(dict(config.headers))


def test_native_mihomo_workspace_cannot_be_reinterpreted_as_shadowrocket(native_api):
    client, _ = native_api
    preview = client.post('/workspace/preview', json={
        'subscription_url': 'https://example.com/all/', 'target': 'mihomo',
    })
    response = client.post('/compile', json={
        'target': 'shadowrocket-config', 'workspace': preview.json()['workspace'],
    })
    assert response.status_code == 400
    assert '/render' in response.json()['detail']


def test_shadowrocket_builder_does_not_rebuild_airport_common_settings():
    from app.core.parsers.clash import clash_to_ir
    from app.core.platforms.shadowrocket import build_shadowrocket_config
    source = YAML(typ='safe').load(YAML_SOURCE)
    with pytest.raises(ValueError, match='原生'):
        build_shadowrocket_config([clash_to_ir(source['proxies'][0])], [], ['MATCH,DIRECT'], {},
                                  source_config=source)


def test_native_user_agent_change_invalidates_published_artifact(native_api, monkeypatch):
    client, state = native_api
    created = client.post('/profiles', json={
        'subscription_url': 'https://example.com/all/', 'target': 'shadowrocket',
    })
    url = created.json()['subscribe_urls']['shadowrocket']
    before = client.get(url)
    calls = len(state['requests'])
    monkeypatch.setenv('SUBFLOW_SHADOWROCKET_USER_AGENT', 'Shadowrocket/2.3.0')
    after = client.get(url)
    assert len(state['requests']) == calls + 1
    assert state['requests'][-1] == 'Shadowrocket/2.3.0'
    assert before.headers['X-Subflow-Revision'] != after.headers['X-Subflow-Revision']
    assert after.text == URI_SOURCE


def test_shadowrocket_only_selection_does_not_require_a_mihomo_source(native_api, monkeypatch):
    client, _ = native_api

    async def fetch(_url, **_kwargs):
        return URI_SOURCE

    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    request = {'subscription_url': 'https://example.com/native-only', 'publication_targets': ['shadowrocket']}
    checked = client.post('/check', json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()['can_publish'] is True
    created = client.post('/profiles', json=request)
    assert created.status_code == 201, created.text


def test_inventory_only_nodes_cannot_be_serialized_without_source_metadata(native_api):
    client, _ = native_api
    preview = client.post('/workspace/preview', json={
        'subscription_url': 'https://example.com/all/', 'target': 'shadowrocket',
    })
    # A caller may copy the inventory without the top-level provenance flags.
    response = client.post('/compile', json={
        'target': 'mihomo', 'workspace': {'proxies': preview.json()['workspace']['proxies']},
    })
    assert response.status_code == 400
    assert '/render' in response.json()['detail']
