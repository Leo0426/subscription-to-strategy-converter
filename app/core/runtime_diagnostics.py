"""Read/probe-only adapters; controller locations and credentials are operator-owned."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import statistics
import math
from urllib.parse import quote, urlsplit

import httpx

from app.core import runtime_evidence as evidence, surge_runtime

_SURGE = '/Applications/Surge.app/Contents/Applications/surge-cli'


def runtime_capabilities() -> dict:
    return {'mihomo': bool(os.environ.get('SUBFLOW_MIHOMO_CONTROLLER')),
            'surge': Path(os.environ.get('SUBFLOW_SURGE_CLI', _SURGE)).is_file()}


async def diagnose_runtime(client: str, service: dict, expected_target: str, *, samples: int = 1) -> dict:
    if not runtime_capabilities().get(client):
        return evidence.unavailable(client, '未配置客户端连接；无法验证实际节点或访问。')
    rounds = []
    timed_out = False
    try:
        async with asyncio.timeout(30):
            for _ in range(max(1, min(3, samples))):
                result = await (_surge(service) if client == 'surge' else _mihomo(service, expected_target))
                rounds.append(result)
                if result['status'] == 'unavailable':
                    break
            if client == 'surge' and rounds and rounds[0].get('actual_node'):
                routes, stable = await _surge_domain_routes(service, rounds[0]['identity'])
                rounds[0]['domain_routes'] = routes
                rounds[0]['selection_stable'] &= stable
    except TimeoutError:
        timed_out = True
    if not rounds:
        return {**evidence.unavailable(client, '诊断超过 30 秒总时限；未能完成采样。'),
                'completed_samples': 0, 'deadline_exceeded': timed_out}
    result = dict(rounds[0])
    nodes = sorted({r['actual_node'] for r in rounds if r.get('actual_node')})
    result.update({'completed_samples':len(rounds), 'requested_samples':samples,
                   'observed_nodes':nodes, 'deadline_exceeded':timed_out,
                   'selection_stable':len(nodes) == 1 and all(r.get('selection_stable', False) for r in rounds)})
    if len(nodes) > 1:
        result['actual_node'] = None
    probes = {}
    for r in rounds:
        for probe in r.get('probes', []):
            probes.setdefault((probe['url'], r.get('actual_node')), []).append(probe)
    result['probes'] = []
    for (url, node), checks in probes.items():
        successes = [p for p in checks if p['status'] == 'reachable']
        latencies = [p['latency_ms'] for p in successes if p.get('latency_ms') is not None]
        failures = len(checks) - len(successes)
        result['probes'].append({**checks[-1], 'url':url, 'node':node, 'sample_count':len(checks),
            'failures':failures, 'failure_rate':failures / len(checks),
            'status':'reachable' if not failures else 'degraded' if successes else 'failed',
            'latency_ms':statistics.median(latencies) if latencies else None,
            'latency_spread_ms':max(latencies)-min(latencies) if len(latencies) > 1 else None})
    routes = result.get('domain_routes', [])
    if routes:
        result['consistent_exit'] = (all(r.get('actual_node') for r in routes)
                                     and len({r['actual_node'] for r in routes} | set(nodes)) == 1)
    if 'identity' in result:
        identities = [r['identity'] for r in rounds]
        if len({i['mode'] for i in identities}) > 1 or len({i['version'] for i in identities}) > 1:
            result['selection_stable'] = False
        if len({i['mode'] for i in identities}) > 1:
            result['identity'] = {**result['identity'], 'mode': 'unknown'}
        if len({i['version'] for i in identities}) > 1:
            result['identity'] = {**result['identity'], 'version': None}
        for item in result['evidence']:
            if item['kind'] in {'group_selection', 'rule_match'} and item['status'] in {'observed', 'partial'}:
                if not result['selection_stable'] or timed_out:
                    item['status'] = 'partial'
                    item['reason'] += ' 采样期间选择或运行上下文变化、不可读，或采样未完成。'
    if result['status'] == 'observed' and (not result['selection_stable'] or timed_out):
        result['status'] = 'partial'
    if routes and (not result['selection_stable'] or timed_out):
        result['consistent_exit'] = False
    if not result['selection_stable']:
        result['message'] += ' 采样期间出口变化或无法确认稳定，请核对客户端当前选择。'
    if timed_out:
        result['message'] += ' 已达到 30 秒总时限，仅展示已完成的采样。'
    return result


def _selected(proxies: dict, target: str) -> tuple[str, list[str]]:
    if not isinstance(proxies, dict):
        raise ValueError('invalid proxy inventory')
    chain = []
    selected = target
    for _ in range(32):
        if not isinstance(selected, str) or selected in chain or selected not in proxies:
            raise ValueError('missing policy or cycle')
        chain.append(selected)
        item = proxies[selected]
        if not isinstance(item, dict):
            raise ValueError('invalid proxy')
        now = item.get('now')
        if now is None:
            kind = item.get('type')
            if 'all' in item or not isinstance(kind, str) or not kind or kind.lower() in {
                'selector', 'urltest', 'fallback', 'loadbalance', 'relay',
            }:
                raise ValueError('group without selection or missing proxy type')
            return selected, chain
        if not isinstance(now, str) or not now:
            raise ValueError('invalid selection')
        selected = now
    raise ValueError('policy chain too deep')


async def _json(client, path):
    response = await client.get(path)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('invalid object')
    return payload


async def _metadata(client, path):
    try:
        return await _json(client, path)
    except (httpx.HTTPError, ValueError, TypeError):
        return {}


def _mode(payload):
    value = payload.get('mode')
    return value.lower() if isinstance(value, str) and value.lower() in {'rule', 'global', 'direct'} else 'unknown'


async def _mihomo(service: dict, target: str) -> dict:
    context = evidence.identity('mihomo')
    base = os.environ['SUBFLOW_MIHOMO_CONTROLLER'].rstrip('/')
    try:
        parsed = urlsplit(base)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('invalid controller')
    except ValueError:
        return evidence.unavailable('mihomo', '管理员配置的 Mihomo Controller 地址无效。', observed_identity=context)
    secret = os.environ.get('SUBFLOW_MIHOMO_SECRET', '')
    headers = {'Authorization': f'Bearer {secret}'} if secret else {}
    try:
        async with httpx.AsyncClient(base_url=base+'/', headers=headers, timeout=12, follow_redirects=False, trust_env=False) as client:
            version, config = await asyncio.gather(_metadata(client, 'version'), _metadata(client, 'configs'))
            context = evidence.identity('mihomo', version=evidence.safe_version(version.get('version')), mode=_mode(config))
            mode = context['mode']
            if mode == 'unknown':
                return evidence.unavailable('mihomo', '无法读取或识别 Mihomo 当前模式；不能将配置目标当作运行出口。', observed_identity=context)
            active_target = {'global': 'GLOBAL', 'direct': 'DIRECT'}.get(mode, target)
            proxies = (await _json(client, 'proxies')).get('proxies')
            selected, chain = _selected(proxies, active_target)
            async def probe(url):
                try:
                    result = await client.get(f'proxies/{quote(selected, safe="")}/delay',
                                              params={'url':url, 'timeout':8000, 'expected':'200-299'})
                    latency = result.json().get('delay') if result.status_code == 200 else None
                    valid = type(latency) in (int, float) and math.isfinite(latency) and latency >= 0
                    return {'url':url,'status':'reachable' if valid else 'failed',
                            'latency_ms':latency if valid else None}
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    return {'url':url, 'status':'failed', 'latency_ms':None}
            checks = await asyncio.gather(*(probe(url) for url in ['https://cp.cloudflare.com/generate_204', *service['probe_urls']][:3]))
            after, after_config, after_version = await asyncio.gather(
                _metadata(client, 'proxies'), _metadata(client, 'configs'), _metadata(client, 'version'))
            try:
                final, final_chain = _selected(after.get('proxies'), active_target)
                stable = (final == selected and final_chain == chain and _mode(after_config) == mode
                          and evidence.safe_version(after_version.get('version')) == context['version'])
            except ValueError:
                stable = False
            complete = bool(context['version']) and stable
            stamp = context['observed_at']
            reason = '读取当前模式下的组选择；未观测关联请求，不能当作服务规则命中。'
            if not context['version']:
                reason += ' 客户端版本缺失或不可读。'
            if not stable:
                reason += ' 探针前后的选择或运行模式变化或不可读。'
            return {'status':'observed' if complete else 'partial', 'actual_node':evidence.public_text(selected),
                    'path':[evidence.public_text(name) for name in chain], 'probes':checks, 'identity': context, 'evidence': [
                        evidence.item('group_selection', 'observed' if complete else 'partial', 'mihomo:/configs,/proxies', stamp, reason),
                        evidence.item('rule_match', 'not_tested', 'mihomo', stamp, '未读取用户连接历史；指定组选择与节点探针不证明请求命中哪条规则。'),
                        evidence.item('probe', 'observed', 'mihomo:/proxies/{node}/delay', stamp, '指定节点的 URL/expected-status 探针结果；不证明手机状态、服务授权或完整登录成功。'),
                        evidence.configuration_identity(context),
                    ], 'selection_stable': stable,
                    'service_tested':bool(service['probe_urls']), 'scope':'client_group',
                    'message':'读取服务器所配置实例的当前模式与组选择，并通过该节点探测；未验证手机状态、服务授权、浏览器登录或对话。'}
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return evidence.unavailable('mihomo', '无法读取 Mihomo Controller，请核对运行状态、地址与管理员凭据。', observed_identity=context)


async def _cli(*args: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(os.environ.get('SUBFLOW_SURGE_CLI', _SURGE), *args,
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20)
    except asyncio.CancelledError:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return 1, 'timeout'
    return process.returncode or 0, stdout.decode(errors='replace')+'\n'+stderr.decode(errors='replace')


async def _surge(service: dict) -> dict:
    return await surge_runtime.diagnose(service, _cli)


async def _surge_domain_routes(service: dict, context: dict) -> tuple[list[dict], bool]:
    return await surge_runtime.domain_routes(service, context, _cli)
