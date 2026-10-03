"""Runtime evidence tests use synthetic controller/CLI responses only."""
import asyncio
from datetime import datetime
import json

import httpx
import pytest

from app.core.runtime_diagnostics import diagnose_runtime

SERVICE = {'destinations': ['service.example'], 'probe_urls': ['https://service.example/status']}


def evidence(result, kind):
    return next(item for item in result['evidence'] if item['kind'] == kind)


def controller(monkeypatch, *, mode='rule', version='v1.19.0', config_status=200,
               version_status=200, proxies=None, config_after=None, proxies_after=None, version_after=None):
    calls = []
    config_reads = 0
    proxy_reads = version_reads = 0
    nodes = proxies if proxies is not None else {
        'AI': {'now': 'TW01'}, 'GLOBAL': {'now': 'US01'},
        'TW01': {'type': 'Shadowsocks'}, 'US01': {'type': 'Shadowsocks'},
        'DIRECT': {'type': 'Direct'},
    }
    def handler(request):
        nonlocal config_reads, proxy_reads, version_reads
        calls.append(request)
        assert request.method == 'GET'
        assert request.headers['Authorization'] == 'Bearer private-controller-secret'
        if request.url.path == '/version':
            version_reads += 1
            return httpx.Response(version_status, json={'meta': True, 'version':
                version_after if version_reads > 1 and version_after is not None else version})
        if request.url.path == '/configs':
            config_reads += 1
            return httpx.Response(config_status, json={
                'mode': config_after if config_reads > 1 and config_after is not None else mode,
                'secret': 'private-controller-secret', 'external-controller': 'private.example:9090',
            })
        if request.url.path == '/proxies':
            proxy_reads += 1
            return httpx.Response(200, json={'proxies':
                proxies_after if proxy_reads > 1 and proxies_after is not None else nodes})
        assert request.url.path in {'/proxies/TW01/delay', '/proxies/US01/delay', '/proxies/DIRECT/delay'}
        assert request.url.params['expected'] == '200-299'
        return httpx.Response(200, json={'delay': 42})
    monkeypatch.setenv('SUBFLOW_MIHOMO_CONTROLLER', 'http://private.example:9090')
    monkeypatch.setenv('SUBFLOW_MIHOMO_SECRET', 'private-controller-secret')
    original = httpx.AsyncClient
    monkeypatch.setattr('app.core.runtime_diagnostics.httpx.AsyncClient',
                        lambda **kw: original(**kw, transport=httpx.MockTransport(handler)))
    return calls


@pytest.mark.parametrize(('mode', 'selected'), [('rule', 'TW01'), ('global', 'US01'), ('direct', 'DIRECT')])
async def test_mihomo_uses_running_mode_and_separates_selection_from_rule_match(monkeypatch, mode, selected):
    calls = controller(monkeypatch, mode=mode)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['actual_node'] == selected
    assert result['identity'] == {
        'client': 'mihomo', 'platform': 'unknown', 'version': 'v1.19.0', 'mode': mode,
        'scope': 'server_configured_instance', 'observed_at': result['identity']['observed_at'],
    }
    assert datetime.fromisoformat(result['identity']['observed_at']).tzinfo is not None
    assert evidence(result, 'group_selection')['status'] == 'observed'
    assert evidence(result, 'rule_match')['status'] == 'not_tested'
    assert evidence(result, 'configuration_identity')['status'] == 'unknown'
    assert evidence(result, 'probe')['status'] == 'observed'
    assert all({'kind', 'status', 'source', 'observed_at', 'reason'} <= item.keys() for item in result['evidence'])
    assert all(item['observed_at'] for item in result['evidence'])
    assert not any(request.url.path.startswith(('/connections', '/group', '/rules')) for request in calls)
    serialized = json.dumps(result)
    assert 'private-controller-secret' not in serialized and 'private.example' not in serialized


@pytest.mark.parametrize('mode', ['script', None, {}, ''])
async def test_mihomo_unknown_mode_cannot_claim_static_target_is_runtime_exit(monkeypatch, mode):
    calls = controller(monkeypatch, mode=mode)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['actual_node'] is None
    assert result['identity']['mode'] == 'unknown'
    assert evidence(result, 'group_selection')['status'] in {'unavailable', 'partial'}
    assert evidence(result, 'probe')['status'] == 'not_tested'
    assert not any(request.url.path.endswith('/delay') for request in calls)


@pytest.mark.parametrize(('version', 'version_status'), [(None, 200), ({'oops': 'value'}, 200), ('v1.19', 401)])
async def test_mihomo_missing_version_preserves_only_partial_selection_evidence(monkeypatch, version, version_status):
    controller(monkeypatch, version=version, version_status=version_status)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['actual_node'] == 'TW01'
    assert result['identity']['version'] is None
    assert evidence(result, 'group_selection')['status'] == 'partial'
    assert evidence(result, 'configuration_identity')['status'] == 'unknown'


async def test_mihomo_version_change_during_probe_marks_context_unstable(monkeypatch):
    controller(monkeypatch, version_after='v1.20.0')
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['selection_stable'] is False
    assert result['status'] == 'partial'


async def test_mihomo_group_chain_change_with_same_leaf_is_not_stable(monkeypatch):
    controller(monkeypatch, proxies={'AI': {'now': 'Nested'}, 'Nested': {'now': 'TW01'},
                                    'TW01': {'type': 'Shadowsocks'}},
               proxies_after={'AI': {'now': 'TW01'}, 'TW01': {'type': 'Shadowsocks'}})
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['actual_node'] == 'TW01'
    assert result['selection_stable'] is False


async def test_mihomo_private_url_in_group_name_is_redacted(monkeypatch):
    private_name = 'https://private-group.example/list?token=secret'
    controller(monkeypatch, proxies={private_name: {'now': 'TW01'}, 'TW01': {'type': 'Shadowsocks'}})
    result = await diagnose_runtime('mihomo', SERVICE, private_name)
    assert result['actual_node'] == 'TW01'
    assert 'private-group.example' not in json.dumps(result)
    assert 'token=secret' not in json.dumps(result)
    assert evidence(result, 'configuration_identity')['status'] == 'unknown'


async def test_mihomo_unreadable_configs_does_not_infer_mode_from_group_names(monkeypatch):
    controller(monkeypatch, config_status=403)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['actual_node'] is None
    assert evidence(result, 'group_selection')['status'] == 'unavailable'


@pytest.mark.parametrize('proxies', [[], {'AI': []}, {'AI': {}}, {'AI': {'now': []}}])
async def test_mihomo_malformed_proxy_structure_is_unavailable(monkeypatch, proxies):
    controller(monkeypatch, proxies=proxies)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['actual_node'] is None
    assert evidence(result, 'group_selection')['status'] == 'unavailable'


async def test_mode_change_during_probe_downgrades_selection_evidence(monkeypatch):
    controller(monkeypatch, mode='rule', config_after='global')
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['selection_stable'] is False
    assert evidence(result, 'group_selection')['status'] == 'partial'


async def test_unconfigured_client_keeps_identity_and_all_evidence_categories(monkeypatch):
    monkeypatch.delenv('SUBFLOW_MIHOMO_CONTROLLER', raising=False)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert result['status'] == 'unavailable'
    assert result['identity']['scope'] == 'server_configured_instance'
    assert evidence(result, 'configuration_identity')['status'] == 'unknown'
    assert evidence(result, 'probe')['status'] == 'not_tested'


async def test_total_deadline_returns_unavailable_evidence_without_claiming_observation(monkeypatch):
    monkeypatch.setenv('SUBFLOW_MIHOMO_CONTROLLER', 'http://fake.example')
    async def deadline(*args):
        raise TimeoutError
    monkeypatch.setattr('app.core.runtime_diagnostics._mihomo', deadline)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI', samples=3)
    assert result['deadline_exceeded'] is True
    assert result['completed_samples'] == 0
    assert evidence(result, 'group_selection')['status'] == 'unavailable'
    assert evidence(result, 'probe')['status'] == 'not_tested'


def surge(monkeypatch, *, mode='rule', legacy=False, raw_route=None, text_route=None,
          version=None, probe_status=200, mode_after=None, raw_route_after=None):
    calls = []
    mode_reads = 0
    route_reads = 0
    monkeypatch.setenv('SUBFLOW_SURGE_CLI', __file__)
    async def command(*args):
        nonlocal mode_reads, route_reads
        calls.append(args)
        raw = args[0] == '--raw'
        parts = args[1:] if raw else args
        if legacy and raw:
            return 1, 'unsupported --raw'
        if parts == ('version',):
            payload = version if version is not None else {'version': '6.9.0', 'build': '10000', 'system': 'macOS',
                       'system-version': '26.0', 'core-version': 24, 'controller-protocol': 24,
                       'device-name': 'private-device', 'device-identifier': 'private-device-id'}
            return (0, json.dumps(payload)) if raw else (1, 'unsupported version')
        if parts == ('mode', 'get'):
            mode_reads += 1
            current = mode_after if mode_reads > 1 and mode_after is not None else mode
            return (0, json.dumps({'mode': current})) if raw else (0, 'Mode: '+current)
        if parts[:2] == ('rule', 'explain'):
            default = {'rule': 'RULE-SET,https://private.rules/list?token=secret,AI', 'rule-policy': 'AI',
                       'final': 'US01', 'final-type': 'Shadowsocks',
                       'steps': [{'group': 'AI', 'type': 'select', 'selected': 'US01', 'reason': 'manual'}],
                       'notes': [], 'underlying-chain': [], 'duration-ms': 0.12}
            if raw:
                route_reads += 1
                if route_reads > 1 and raw_route_after is not None:
                    return 0, raw_route_after
                return 0, raw_route if raw_route is not None else json.dumps(default)
            return 0, text_route if text_route is not None else 'Matched rule: DOMAIN,service.example,AI\nFinal policy: US01 (Shadowsocks)\n'
        if parts[:2] == ('http', 'probe'):
            assert parts[3] == ('DIRECT' if mode == 'direct' else 'US01')
            if raw:
                return 0, json.dumps({'status': probe_status, 'duration-ms': 12.5, 'method': 'HEAD',
                                    'policy': parts[3], 'headers': [{'name': 'Set-Cookie', 'value': 'secret'}]})
            return 0, f'Status: {probe_status}\nDuration: 12.5 ms\n'
        raise AssertionError(args)
    monkeypatch.setattr('app.core.runtime_diagnostics._cli', command)
    return calls


async def test_surge_raw_identity_is_server_mac_and_rule_evaluation_remains_partial(monkeypatch):
    calls = surge(monkeypatch)
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['identity']['platform'] == 'macOS'
    assert result['identity']['version'] == '6.9.0'
    assert result['identity']['mode'] == 'rule'
    assert result['actual_node'] == 'US01'
    assert evidence(result, 'rule_match')['status'] == 'partial'
    assert evidence(result, 'rule_match')['source'].endswith(':json')
    assert evidence(result, 'configuration_identity')['status'] == 'unknown'
    assert all(probe['status'] == 'reachable' for probe in result['probes'])
    assert all(call[0] == '--raw' for call in calls)
    assert all(call[1] in {'version', 'mode', 'rule', 'http'} for call in calls)
    output = json.dumps(result)
    assert 'private.rules' not in output and 'secret' not in output and 'private-device' not in output


async def test_surge_legacy_text_is_partial_and_does_not_invent_version(monkeypatch):
    surge(monkeypatch, legacy=True)
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['actual_node'] == 'US01'
    assert result['status'] == 'partial'
    assert result['identity']['version'] is None
    assert evidence(result, 'rule_match')['status'] == 'partial'
    assert evidence(result, 'rule_match')['source'].endswith(':text')
    assert evidence(result, 'probe')['status'] == 'partial'


@pytest.mark.parametrize('raw_route', ['not-json', '[]', '{"final":"US01"}', '{"error":"private-secret", "final":"US01"}'])
async def test_surge_invalid_raw_falls_back_with_partial_evidence(monkeypatch, raw_route):
    surge(monkeypatch, raw_route=raw_route)
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['actual_node'] == 'US01'
    assert evidence(result, 'rule_match')['status'] == 'partial'
    assert evidence(result, 'rule_match')['source'].endswith(':text')
    assert 'private-secret' not in json.dumps(result)


async def test_surge_missing_final_policy_in_both_formats_is_unavailable(monkeypatch):
    surge(monkeypatch, raw_route='{}', text_route='schema changed')
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['actual_node'] is None
    assert evidence(result, 'rule_match')['status'] == 'unavailable'
    assert evidence(result, 'probe')['status'] == 'not_tested'


async def test_surge_direct_mode_does_not_use_rule_explain_exit(monkeypatch):
    calls = surge(monkeypatch, mode='direct')
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['actual_node'] == 'DIRECT'
    assert result['identity']['mode'] == 'direct'
    assert evidence(result, 'rule_match')['status'] == 'not_tested'
    assert not any(call[1:3] == ('rule', 'explain') for call in calls)


async def test_surge_unknown_mode_cannot_promote_rule_evaluation_to_actual_exit(monkeypatch):
    calls = surge(monkeypatch, mode='future-mode')
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['actual_node'] is None
    assert result['identity']['mode'] == 'unknown'
    assert not any(call[1:3] == ('http', 'probe') for call in calls)


async def test_surge_mode_change_during_probe_marks_selection_unstable(monkeypatch):
    surge(monkeypatch, mode='rule', mode_after='proxy')
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['selection_stable'] is False
    assert evidence(result, 'rule_match')['status'] == 'partial'
    assert result['consistent_exit'] is False


async def test_surge_changed_group_chain_with_same_final_policy_is_unstable(monkeypatch):
    changed = {'rule': 'RULE-SET,https://private.rules/list?token=secret,AI', 'rule-policy': 'AI',
               'final': 'US01', 'final-type': 'Shadowsocks',
               'steps': [{'group': 'AI', 'type': 'select', 'selected': 'Nested'}]}
    surge(monkeypatch, raw_route_after=json.dumps(changed))
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['actual_node'] == 'US01'
    assert result['selection_stable'] is False


async def test_surge_proxy_mode_without_verified_global_binding_does_not_probe(monkeypatch):
    calls = surge(monkeypatch, mode='proxy')
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['identity']['mode'] == 'proxy'
    assert result['actual_node'] is None
    assert evidence(result, 'probe')['status'] == 'not_tested'
    assert not any(call[1:3] in {('rule', 'explain'), ('http', 'probe')} for call in calls)


async def test_surge_missing_metadata_does_not_assume_installed_cli_platform(monkeypatch):
    surge(monkeypatch, version={'version': 'https://private.example/version?secret=1', 'system': 'private-device'})
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['identity']['platform'] == 'unknown'
    assert result['identity']['version'] is None
    assert 'private' not in json.dumps(result)


async def test_surge_cli_option_like_node_cannot_be_probed(monkeypatch):
    calls = surge(monkeypatch, raw_route='{}', text_route='Final policy:  --private-option (Shadowsocks)\n')
    result = await diagnose_runtime('surge', SERVICE, 'AI')
    assert result['actual_node'] is None
    assert not any(call[1:3] == ('http', 'probe') for call in calls)


async def test_runtime_metadata_is_inside_total_deadline(monkeypatch):
    monkeypatch.setenv('SUBFLOW_MIHOMO_CONTROLLER', 'http://fake.example')
    original_timeout = asyncio.timeout
    budgets = []
    def short_timeout(delay):
        budgets.append(delay)
        return original_timeout(0.01)
    async def stalled_metadata(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr('app.core.runtime_diagnostics.asyncio.timeout', short_timeout)
    monkeypatch.setattr('app.core.runtime_diagnostics._metadata', stalled_metadata)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI')
    assert budgets == [30]
    assert result['deadline_exceeded'] is True
    assert result['completed_samples'] == 0
    assert evidence(result, 'probe')['status'] == 'not_tested'


async def test_context_changes_between_samples_downgrade_overall_observation(monkeypatch):
    from app.core import runtime_evidence
    monkeypatch.setenv('SUBFLOW_MIHOMO_CONTROLLER', 'http://fake.example')
    versions = iter(['v1.19.0', 'v1.19.1'])
    async def sample(*args):
        identity = runtime_evidence.identity('mihomo', version=next(versions), mode='rule')
        return {'status': 'observed', 'actual_node': 'US01', 'identity': identity,
                'selection_stable': True, 'message': 'Observed',
                'evidence': [runtime_evidence.item('group_selection', 'observed', 'mihomo',
                             identity['observed_at'], 'Selection read')], 'probes': []}
    monkeypatch.setattr('app.core.runtime_diagnostics._mihomo', sample)
    result = await diagnose_runtime('mihomo', SERVICE, 'AI', samples=2)
    assert result['selection_stable'] is False
    assert result['status'] == 'partial'
    assert evidence(result, 'group_selection')['status'] == 'partial'
