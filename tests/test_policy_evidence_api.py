from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.core.workbench import service_report
from app.main import app


def test_static_evidence_keeps_earlier_unknown_rule_and_candidate_selection_explicit():
    config = {
        'proxy-groups': [{'name': 'OpenAI', 'type': 'select', 'proxies': ['DIRECT', 'REJECT']}],
        'rules': ['GEOSITE,unknown,REJECT', 'DOMAIN-SUFFIX,chatgpt.com,OpenAI', 'MATCH,DIRECT'],
    }
    report = service_report(config, [], 'openai')[0]
    domain = next(d for d in report['domains'] if d['domain'] == 'chatgpt.com')
    assert domain['configured_node'] == 'DIRECT'
    assert domain['evidence']['status'] == 'partial'
    assert 'earlier_rule_requires_runtime' in domain['evidence']['reasons']
    assert 'client_selection_not_observed' in domain['evidence']['reasons']
    assert report['evidence']['scope'] == 'configuration_simulation'
    assert report['evidence']['status'] == 'partial'
    assert datetime.fromisoformat(report['evidence']['observed_at']).tzinfo is not None
    assert report['actual_node'] is None


def test_static_domain_match_is_inference_even_when_no_runtime_rule_precedes_it():
    config = {'rules': ['DOMAIN-SUFFIX,chatgpt.com,DIRECT', 'MATCH,DIRECT']}
    report = service_report(config, [], 'openai')[0]
    domain = next(d for d in report['domains'] if d['domain'] == 'chatgpt.com')
    assert domain['status'] == 'matched'
    assert domain['evidence']['status'] == 'inferred'
    assert domain['evidence']['reasons'] == []
    assert report['evidence']['catalog_revision']


def test_static_evidence_is_partial_when_the_matched_target_is_missing():
    report = service_report({'rules': ['DOMAIN-SUFFIX,chatgpt.com,Missing']}, [], 'openai')[0]
    domain = next(d for d in report['domains'] if d['domain'] == 'chatgpt.com')
    assert 'target_resolution_incomplete' in domain['evidence']['reasons']
    assert domain['evidence']['status'] == 'partial'


@pytest.fixture
def client(tmp_path, monkeypatch):
    calls = []

    async def fetch(url, **kwargs):
        calls.append(url)
        return '''proxies:
  - {name: Synthetic, type: ss, server: example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
'''

    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    with TestClient(app) as session:
        yield session, calls


def test_check_inventory_comes_from_each_compiled_target_without_fetching_rules(client):
    session, calls = client
    request = {'subscription_url': 'https://example.com/synthetic',
               'publication_targets': ['mihomo', 'surge', 'shadowrocket']}
    result = session.post('/check', json=request)
    assert result.status_code == 200, result.text
    clients = {c['target']: c for c in result.json()['clients']}
    assert clients['mihomo']['dependencies']['summary']['rule_sources'] == 9
    assert clients['surge']['dependencies']['summary']['remote'] == 23
    assert clients['shadowrocket']['dependencies']['summary']['remote'] == 23
    assert clients['shadowrocket']['dependencies']['target'] == 'shadowrocket-config'
    assert all(c['dependencies']['summary']['geodata'] for c in clients.values())
    assert all(d['audit_status'] == 'not_audited' for c in clients.values()
               for d in c['dependencies']['dependencies'])
    assert all(url == request['subscription_url'] for url in calls)


def test_diagnose_static_evidence_does_not_call_runtime(client, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Static diagnosis must not query a client')

    monkeypatch.setattr('app.api.convert.diagnose_runtime', unexpected)
    session, _ = client
    result = session.post('/diagnose', json={
        'request': {'subscription_url': 'https://example.com/synthetic'}, 'service': 'openai',
    })
    assert result.status_code == 200
    assert result.json()['service']['evidence']['scope'] == 'configuration_simulation'
    assert result.json()['runtime']['status'] == 'not_tested'
