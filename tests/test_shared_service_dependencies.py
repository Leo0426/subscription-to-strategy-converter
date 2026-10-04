"""Shared identity traffic cannot independently follow two service exits."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

SOURCE = '''proxies:
- {name: US01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
- {name: TW01, type: ss, server: tw.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
'''
CODE = 'shared_service_dependency_egress'


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url, *, target='mihomo'):
        return SOURCE
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    with TestClient(app) as client:
        yield client


def request(routes):
    return {'subscription_url': 'https://source.example.com/sub', 'service_routes': routes,
            'publication_targets': ['mihomo', 'surge']}


def fixed(service, node):
    return {'service': service, 'mode': 'fixed', 'egress': node}


@pytest.mark.parametrize('routes', [
    [fixed('openai', 'US01'), fixed('gemini', 'TW01')],
    [fixed('gemini', 'TW01'), fixed('openai', 'US01')],
    [fixed('gemini', 'TW01')],
    [fixed('claude', 'TW01')],
    [fixed('openai', 'US01')],
    [{'service': s, 'mode': 'fallback', 'egress': 'US01', 'fallback': 'TW01'} for s in ['openai', 'claude', 'gemini']],
])
def test_check_exposes_shared_login_risk_without_changing_routes(client, routes):
    result = client.post('/check', json=request(routes))
    assert result.status_code == 200, result.text
    report = result.json()
    assert report['can_publish'] is True
    assert report['runtime']['status'] == 'not_tested'
    for target in report['clients']:
        warnings = [w for w in target['warnings'] if w['code'] == CODE]
        assert len(warnings) == 1
        warning = warnings[0]
        assert warning['services'] == ['openai', 'claude', 'gemini']
        assert warning['domains'] == ['accounts.google.com', 'oauth2.googleapis.com', 'openidconnect.googleapis.com']
        assert 'Google 登录' in warning['message']
        assert '同一' in warning['suggestion']
        assert warning['scope'] == 'configuration'


@pytest.mark.parametrize('routes', [
    [], [fixed('youtube', 'TW01')],
    [fixed(s, 'US01') for s in ['openai', 'claude', 'gemini']],
    [{'service': s, 'mode': 'manual', 'egress': '手动选择'} for s in ['openai', 'claude', 'gemini']],
    [fixed('openai', 'US01'), *[{'service': s, 'mode': 'manual', 'egress': 'OpenAI'} for s in ['claude', 'gemini']]],
    [{'service': 'openai', 'mode': 'fallback', 'egress': 'US01', 'fallback': 'TW01'},
     *[{'service': s, 'mode': 'manual', 'egress': 'OpenAI'} for s in ['claude', 'gemini']]],
])
def test_shared_policy_or_identical_fixed_exit_needs_no_warning(client, routes):
    response = client.post('/check', json=request(routes))
    assert response.status_code == 200, response.text
    assert not any(w['code'] == CODE for c in response.json()['clients'] for w in c['warnings'])


def test_render_and_saved_subscription_keep_login_warning(client):
    intent = request([fixed('openai', 'US01'), fixed('gemini', 'TW01')])
    rendered = client.post('/render', json=intent)
    assert rendered.status_code == 200
    assert CODE in {w['code'] for w in json.loads(rendered.headers.get('X-Compile-Warnings', '[]'))}
    created = client.post('/profiles', json=intent)
    assert created.status_code == 201, created.text
    subscribed = client.get(created.json()['subscribe_urls']['clash'])
    assert subscribed.status_code == 200
    assert CODE in {w['code'] for w in json.loads(subscribed.headers.get('X-Compile-Warnings', '[]'))}


@pytest.mark.parametrize('service', ['openai', 'claude', 'gemini'])
def test_service_diagnosis_includes_shared_login_risk(client, service):
    intent = request([fixed('openai', 'US01'), fixed('gemini', 'TW01')])
    response = client.post('/diagnose', json={'request': intent, 'service': service})
    assert response.status_code == 200, response.text
    assert [w['code'] for w in response.json()['warnings']] == [CODE]
    assert response.json()['runtime']['status'] == 'not_tested'
    unrelated = client.post('/diagnose', json={'request': intent, 'service': 'youtube'})
    assert unrelated.json()['warnings'] == []
