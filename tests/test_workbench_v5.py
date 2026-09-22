import json

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.main import app

SOURCE = '''
proxies:
  - {name: TW01, type: ss, server: tw.example.com, port: 443, cipher: aes-128-gcm, password: test}
  - {name: US01, type: ss, server: us.example.com, port: 443, cipher: aes-128-gcm, password: test}
'''

@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url):
        return SOURCE
    monkeypatch.setenv('SUBFLOW_DB_PATH', str(tmp_path / 'profiles.db'))
    monkeypatch.setattr('app.core.subscription.fetch_subscription', fetch)
    with TestClient(app) as client:
        yield client


def request(**kwargs):
    return {'subscription_url': 'https://example.com/sub', **kwargs}


def test_fixed_service_uses_one_node_for_main_login_and_assets(client):
    intent = request(service_routes=[{'service': 'openai', 'mode': 'fixed', 'egress': 'TW01'}])
    response = client.post('/render', json=intent)
    assert response.status_code == 200, response.text
    config = YAML(typ='safe').load(response.text)
    group = next(g for g in config['proxy-groups'] if g['name'] == 'OpenAI')
    assert group['type'] == 'select'
    assert group['proxies'] == ['TW01']
    for match in ['DOMAIN-SUFFIX,chatgpt.com', 'DOMAIN,cdn.openaimerge.com', 'DOMAIN-SUFFIX,challenges.cloudflare.com']:
        assert f'{match},OpenAI' in config['rules']
    assert 'dns' not in config


def test_check_reports_each_client_and_does_not_claim_runtime_access(client):
    response = client.post('/check', json=request(publication_targets=['mihomo', 'surge']))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['can_publish'] is True
    assert [c['target'] for c in body['clients']] == ['mihomo', 'surge']
    assert body['clients'][1]['warnings']
    assert body['runtime']['status'] == 'not_tested'
    assert body['services'][0]['actual_node'] is None


def test_fixed_missing_node_blocks_new_publication(client):
    intent = request(publication_targets=['mihomo'], service_routes=[{'service':'openai','mode':'fixed','egress':'Gone'}])
    response = client.post('/profiles', json=intent)
    assert response.status_code in (400, 422)
    assert client.get('/profiles').json()['profiles'] == []


def test_update_keeps_links_and_stores_only_preferences(client):
    original = request(publication_targets=['mihomo','surge'], service_routes=[{'service':'openai','mode':'fixed','egress':'US01'}])
    created = client.post('/profiles', json=original)
    assert created.status_code == 201, created.text
    links = created.json()
    edited = {**original, 'service_routes':[{'service':'openai','mode':'fixed','egress':'TW01'}]}
    updated = client.put('/profiles/'+links['id'], params={'token':links['token']}, json=edited)
    assert updated.status_code == 200, updated.text
    assert updated.json()['subscribe_urls'] == links['subscribe_urls']
    draft = client.get(f"/profiles/{links['id']}/draft", params={'token':links['token']}).json()['request']
    assert draft['selected_policy'] is None
    assert draft['service_routes'][0]['egress'] == 'TW01'
    conf = YAML(typ='safe').load(client.get(links['subscribe_urls']['clash']).text)
    assert next(g for g in conf['proxy-groups'] if g['name']=='OpenAI')['proxies'] == ['TW01']


def test_failover_requires_named_backup_and_fixed_cannot_delegate_to_an_auto_group(client):
    for route in [{'service':'openai','mode':'fallback','egress':'TW01'},
                  {'service':'openai','mode':'fixed','egress':'自动选择'}]:
        assert client.post('/render', json=request(service_routes=[route])).status_code in (400,422)
    result = client.post('/render', json=request(service_routes=[{'service':'openai','mode':'fallback','egress':'TW01','fallback':'US01'}]))
    assert result.status_code == 200
    config = YAML(typ='safe').load(result.text)
    group = next(g for g in config['proxy-groups'] if g['name']=='OpenAI')
    assert group['type'] == 'fallback'
    assert group['proxies'] == ['TW01','US01']


def test_legacy_upgrade_is_preview_only_and_preserves_link_on_explicit_save(client):
    old = client.post('/profiles', json=request(
        surge_preferences={'auto_test_protocols':['anytls']},
        selected_policy={'mode':'merge','proxy_groups':[{'name':'OpenAI','type':'select','proxies':['TW01','DIRECT']}],'rules':['DOMAIN-SUFFIX,chatgpt.com,OpenAI']},
    )).json()
    path = '/profiles/'+old['id']
    params = {'token':old['token']}
    upgrade = client.post(path+'/upgrade-preview', params=params)
    assert upgrade.status_code == 200, upgrade.text
    candidate = upgrade.json()['request']
    assert candidate['selected_policy'] is None
    assert candidate['service_routes'][0]['egress']=='TW01'
    assert candidate['surge_preferences']=={'auto_test_protocols':['anytls']}
    assert upgrade.json()['changes']['added_rules']
    assert client.get(path+'/draft', params=params).json()['request']['selected_policy'] is not None
    result = client.put(path, params=params, json=candidate)
    assert result.status_code == 200, result.text
    assert result.json()['subscribe_urls']==old['subscribe_urls']


def test_diagnostics_are_static_until_runtime_is_explicitly_requested(client):
    response = client.post('/diagnose', json={'request':request(),'service':'openai'})
    assert response.status_code == 200, response.text
    assert response.json()['runtime']['status']=='not_tested'
    assert response.json()['service']['id']=='openai'


def test_catalog_generated_template_and_rule_packs_share_the_same_rules(client):
    import subprocess
    result = subprocess.run(['.venv/bin/python','scripts/sync-service-rules.py','--check'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    services = client.get('/services').json()['services']
    packs = {pack['id']:pack for pack in client.get('/rule-packs').json()['packs']}
    for service in services:
        assert packs[service['id']]['rules'] == [f"{r['match']},{service['group']}" for r in service['rules']]


def test_diagnostic_request_cannot_supply_a_controller_or_arbitrary_probe_url(client):
    response = client.post('/diagnose', json={'request':request(),'controller':'http://localhost:1234','url':'http://localhost/private'})
    assert response.status_code == 422


def test_mihomo_runtime_reads_configured_controller_and_probes_selected_node(client, monkeypatch):
    import httpx
    monkeypatch.setenv('SUBFLOW_MIHOMO_CONTROLLER', 'http://controller.example:9090')
    monkeypatch.setenv('SUBFLOW_MIHOMO_SECRET', 'operator-secret')
    calls=[]
    def handler(req):
        calls.append(req)
        assert req.headers['Authorization']=='Bearer operator-secret'
        if req.url.path=='/proxies':
            return httpx.Response(200, json={'proxies':{'AI 服务':{'now':'TW01'},'TW01':{'type':'Shadowsocks'}}})
        assert req.method=='GET'
        assert req.url.path=='/proxies/TW01/delay'
        assert req.url.params['url'].startswith('https://')
        return httpx.Response(200, json={'delay':123})
    original=httpx.AsyncClient
    monkeypatch.setattr('app.core.runtime_diagnostics.httpx.AsyncClient', lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(handler)))
    response=client.post('/diagnose',json={'request':request(),'service':'openai','runtime':True})
    assert response.status_code==200
    runtime=response.json()['runtime']
    assert runtime['actual_node']=='TW01'
    assert len(runtime['probes'])==3
    assert all(p['status']=='reachable' for p in runtime['probes'])
    assert 'operator-secret' not in response.text
    assert all(r.url.host=='controller.example' for r in calls)


def test_runtime_samples_report_failures_spread_and_selection_changes(client, monkeypatch):
    import httpx
    monkeypatch.setenv('SUBFLOW_MIHOMO_CONTROLLER', 'http://controller.example:9090')
    group_reads = 0
    counts = {}
    def handler(req):
        nonlocal group_reads
        if req.url.path == '/proxies':
            group_reads += 1
            node = 'TW01' if group_reads < 6 else 'US01'
            return httpx.Response(200, json={'proxies':{'AI 服务':{'now':node},node:{'type':'Shadowsocks'}}})
        url = req.url.params['url']
        counts[url] = counts.get(url, 0) + 1
        if counts[url] == 2:
            return httpx.Response(504, json={'message':'unavailable'})
        return httpx.Response(200, json={'delay':100 if counts[url] == 1 else 140})
    original = httpx.AsyncClient
    monkeypatch.setattr('app.core.runtime_diagnostics.httpx.AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(handler)))
    response = client.post('/diagnose', json={'request':request(),'service':'openai','runtime':True,'samples':3})
    assert response.status_code == 200, response.text
    runtime = response.json()['runtime']
    assert runtime['selection_stable'] is False
    assert runtime['completed_samples'] == 3
    assert runtime['probes'][0]['failures'] == 1
    assert runtime['probes'][0]['latency_spread_ms'] == 40
    assert runtime['probes'][0]['sample_count'] == 3
    assert client.post('/diagnose', json={'request':request(),'samples':100}).status_code == 422


def test_surge_diagnostics_compare_mobile_domains_and_redact_rule_urls(client, monkeypatch):
    monkeypatch.setenv('SUBFLOW_SURGE_CLI', __file__)
    calls = []
    async def command(*args):
        calls.append(args)
        if args[:2] == ('http', 'probe'):
            return 0, 'Status: 200\nDuration: 123\n'
        assert args[:2] == ('rule', 'explain')
        node = 'DIRECT' if args[2] == 'humb.apple.com' else 'US01'
        return 0, f'Matched rule: RULE-SET,https://rules.example/list?secret=private,{node}\nFinal policy: {node} (Shadowsocks)\n'
    monkeypatch.setattr('app.core.runtime_diagnostics._cli', command)
    response = client.post('/diagnose', json={'request':request(),'service':'openai','runtime':True,'client':'surge'})
    assert response.status_code == 200, response.text
    runtime = response.json()['runtime']
    assert runtime['consistent_exit'] is False
    assert runtime['selection_stable'] is True
    assert {r['domain'] for r in runtime['domain_routes']} >= {'ios.chat.openai.com','humb.apple.com','ws.chatgpt.com'}
    assert 'secret=private' not in response.text


def test_incompatible_fixed_node_cannot_publish_even_with_another_supported_node(client, monkeypatch):
    async def fetch(_url):
        return SOURCE+'  - {name: US-VLESS, type: vless, server: v.example.com, port: 443, uuid: test}\n'
    monkeypatch.setattr('app.core.subscription.fetch_subscription',fetch)
    intent=request(publication_targets=['surge'],service_routes=[{'service':'openai','mode':'fixed','egress':'US-VLESS'}])
    result=client.post('/check',json=intent)
    assert result.status_code==200
    assert result.json()['can_publish'] is False
    assert client.post('/profiles',json=intent).status_code==400
    assert client.get('/profiles').json()['profiles']==[]


def test_failed_update_preserves_saved_intent_and_requires_auth_before_fetch(client, monkeypatch):
    original = request(publication_targets=['mihomo'], service_routes=[{'service':'openai','mode':'fixed','egress':'TW01'}])
    saved = client.post('/profiles', json=original).json()
    path = '/profiles/'+saved['id']
    params = {'token':saved['token']}
    invalid = {**original, 'service_routes':[{'service':'openai','mode':'fixed','egress':'Gone'}]}
    assert client.put(path, params=params, json=invalid).status_code == 400
    assert client.get(path+'/draft', params=params).json()['request']['service_routes'][0]['egress']=='TW01'
    async def must_not_fetch(_url):
        raise AssertionError('unauthorized update fetched upstream')
    monkeypatch.setattr('app.core.subscription.fetch_subscription',must_not_fetch)
    assert client.put(path, params={'token':'wrong'}, json=original).status_code == 404


def test_upgrade_reports_custom_group_that_cannot_be_preserved(client):
    old = client.post('/profiles', json=request(selected_policy={'mode':'merge','proxy_groups':[
        {'name':'CustomPool','type':'select','proxies':['TW01']},
        {'name':'OpenAI','type':'select','proxies':['CustomPool']}],
        'rules':['DOMAIN-SUFFIX,chatgpt.com,OpenAI']})).json()
    result = client.post('/profiles/'+old['id']+'/upgrade-preview',params={'token':old['token']})
    assert result.status_code == 200, result.text
    assert result.json()['changes']['discarded_preferences']
    assert result.json()['request']['service_routes'] == []


def test_private_profile_responses_are_not_cacheable(client):
    result = client.post('/profiles', json=request())
    assert result.headers['cache-control'] == 'no-store'
    saved = result.json()
    draft = client.get('/profiles/'+saved['id']+'/draft',params={'token':saved['token']})
    assert draft.headers['cache-control'] == 'no-store'


def test_service_override_retargets_its_geosite_and_single_service_provider(client):
    result = client.post('/render', json=request(service_routes=[
        {'service':'openai','mode':'fixed','egress':'TW01'},
        {'service':'youtube','mode':'fixed','egress':'US01'}]))
    config = YAML(typ='safe').load(result.text)
    assert 'GEOSITE,openai,OpenAI' in config['rules']
    assert 'GEOSITE,youtube,YouTube' in config['rules']
    assert 'GEOSITE,openai,AI 服务' not in config['rules']
    assert any(r.startswith('RULE-SET,YouTube-6,YouTube') for r in config['rules'])
