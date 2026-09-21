"""Read/probe-only adapters; controller locations and credentials are operator-owned."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import statistics
import math
from urllib.parse import quote, urlsplit

import httpx

_SURGE = '/Applications/Surge.app/Contents/Applications/surge-cli'


def runtime_capabilities() -> dict:
    return {'mihomo': bool(os.environ.get('SUBFLOW_MIHOMO_CONTROLLER')),
            'surge': Path(os.environ.get('SUBFLOW_SURGE_CLI', _SURGE)).is_file()}


async def diagnose_runtime(client: str, service: dict, expected_target: str, *, samples: int = 1) -> dict:
    if not runtime_capabilities().get(client):
        return {'status': 'unavailable', 'message': '未配置客户端连接；无法验证实际节点或访问。', 'actual_node': None}
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
                rounds[0]['domain_routes'] = await _surge_domain_routes(service)
    except TimeoutError:
        timed_out = True
    if not rounds:
        return {'status':'unavailable', 'actual_node':None, 'completed_samples':0,
                'deadline_exceeded':timed_out, 'message':'诊断超过 30 秒总时限；未能完成采样。'}
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
    if not result['selection_stable']:
        result['message'] += ' 采样期间出口变化或无法确认稳定，请核对客户端当前选择。'
    if timed_out:
        result['message'] += ' 已达到 30 秒总时限，仅展示已完成的采样。'
    return result


def _selected(proxies: dict, target: str) -> tuple[str, list[str]]:
    chain = []
    selected = target
    for _ in range(32):
        if selected in chain or selected not in proxies:
            raise ValueError('missing policy or cycle')
        chain.append(selected)
        item = proxies[selected]
        now = item.get('now')
        if not now:
            if item.get('all'):
                raise ValueError('group without selection')
            return selected, chain
        selected = now
    raise ValueError('policy chain too deep')


async def _mihomo(service: dict, target: str) -> dict:
    base = os.environ['SUBFLOW_MIHOMO_CONTROLLER'].rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        return {'status':'unavailable','message':'管理员配置的 Mihomo Controller 地址无效。','actual_node':None}
    secret = os.environ.get('SUBFLOW_MIHOMO_SECRET', '')
    headers = {'Authorization': f'Bearer {secret}'} if secret else {}
    try:
        async with httpx.AsyncClient(base_url=base+'/', headers=headers, timeout=12, follow_redirects=False, trust_env=False) as client:
            response = await client.get('proxies')
            response.raise_for_status()
            proxies = response.json().get('proxies', {})
            selected, chain = _selected(proxies, target)
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
            after = await client.get('proxies')
            after.raise_for_status()
            final, _ = _selected(after.json().get('proxies', {}), target)
            return {'status':'observed','actual_node':selected, 'path':chain, 'probes':checks,
                    'selection_stable': final == selected,
                    'service_tested':bool(service['probe_urls']), 'scope':'client_group', 'message':'读取客户端组的当前选择，并通过该节点探测；未验证浏览器登录或对话。'}
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        return {'status':'unavailable','actual_node':None,'message':'无法读取 Mihomo Controller，请核对运行状态、地址与管理员凭据。'}


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
    try:
        domain = service['destinations'][0]
        code, output = await _cli('rule','explain',domain)
        final = re.search(r'^Final policy: (.+?)(?: \([^\n]+\))?$', output, re.MULTILINE)
        if code or not final:
            return {'status':'unavailable','message':'无法读取 Surge 当前规则；请确认本机 Surge 已运行且支持诊断命令。','actual_node':None}
        node = final[1].strip()
        if node.startswith('-'):
            return {'status':'unavailable','message':'节点名称无法安全用于 CLI 探测。','actual_node':None}
        async def probe(url):
            try:
                _, output = await _cli('http','probe',url,node)
            except OSError:
                return {'url':url, 'status':'failed', 'http_status':None, 'latency_ms':None}
            status = re.search(r'^Status: (\d+)', output, re.MULTILINE)
            duration = re.search(r'^Duration: ([\d.]+)', output, re.MULTILINE)
            http_status = int(status[1]) if status else None
            return {'url':url,'status':'reachable' if http_status and 200 <= http_status < 300 else 'failed',
                    'http_status':http_status,'latency_ms':float(duration[1]) if duration else None}
        probes = await asyncio.gather(*(probe(url) for url in ['https://cp.cloudflare.com/generate_204', *service['probe_urls']][:3]))
        code, after = await _cli('rule', 'explain', domain)
        last = re.search(r'^Final policy: (.+?)(?: \([^\n]+\))?$', after, re.MULTILINE)
        return {'status':'observed','actual_node':node,'scope':'rule_match','probes':probes,'service_tested':bool(service['probe_urls']),
                'selection_stable':not code and bool(last) and last[1].strip() == node,
                'message':'读取 Surge 实际规则选择并通过该节点进行 HEAD 探测；未验证登录或对话。'}
    except (OSError, ValueError):
        return {'status':'unavailable','actual_node':None,'message':'本机 Surge 诊断命令不可用。'}


async def _surge_domain_routes(service: dict) -> list[dict]:
    gate = asyncio.Semaphore(3)
    async def inspect(domain):
        async with gate:
            try:
                code, output = await _cli('rule', 'explain', domain)
            except OSError:
                return {'domain':domain, 'actual_node':None, 'rule':None}
            final = re.search(r'^Final policy: (.+?)(?: \([^\n]+\))?$', output, re.MULTILINE)
            matched = re.search(r'^Matched rule: (.+)$', output, re.MULTILINE)
            rule = re.sub(r'https?://[^,\s]+', '<RuleProvider>', matched[1]) if matched else None
            return {'domain':domain, 'actual_node':final[1].strip() if not code and final else None, 'rule':rule}
    domains = service.get('diagnostic_destinations', service['destinations'])[:8]
    return await asyncio.gather(*(inspect(domain) for domain in domains))
