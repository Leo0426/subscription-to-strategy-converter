"""Read-only Surge CLI evidence; JSON schemas are capability-checked at runtime.

The field names follow the installed official CLI's formatters. Without a real
response contract, JSON and legacy text remain partial evidence. Rule explanation
is a policy evaluation, never a captured user request.
"""
from __future__ import annotations

import asyncio
import json
import math
import re

from app.core import runtime_evidence as evidence


async def _raw(cli, *args):
    code, output = await cli('--raw', *args)
    if code:
        return None
    try:
        value = json.loads(output)
        return None if isinstance(value, dict) and 'error' in value else value
    except (ValueError, TypeError):
        return None


async def _mode(cli):
    payload = await _raw(cli, 'mode', 'get')
    value = payload.get('mode') if isinstance(payload, dict) else payload
    if not isinstance(value, str):
        code, output = await cli('mode', 'get')
        match = re.search(r'^Mode: (\w+)\s*$', output, re.MULTILINE)
        value = match[1] if not code and match else None
    return value.lower() if isinstance(value, str) and value.lower() in {'rule', 'direct', 'proxy'} else 'unknown'


def _node(value):
    if not isinstance(value, str) or not value.strip() or value.strip().startswith('-') or any(c in value for c in '\r\n\x00'):
        return None
    return value.strip()


async def _route(cli, domain):
    value = await _raw(cli, 'rule', 'explain', domain)
    if (isinstance(value, dict) and _node(value.get('final'))
            and all(isinstance(value.get(key), str) for key in ('rule', 'rule-policy', 'final-type'))
            and isinstance(value.get('steps'), list) and all(isinstance(step, dict) for step in value['steps'])):
        return {'node': _node(value['final']), 'rule': value['rule'], 'source': 'surge:rule-explain:json',
                'selection': [(step.get('group'), step.get('type'), step.get('selected')) for step in value['steps']]}
    code, output = await cli('rule', 'explain', domain)
    final = re.search(r'^Final policy: (.+?)(?: \([^\n]+\))?$', output, re.MULTILINE)
    matched = re.search(r'^Matched rule: (.+)$', output, re.MULTILINE)
    return {'node': _node(final[1]) if not code and final else None,
            'rule': matched[1] if not code and matched else None, 'source': 'surge:rule-explain:text'}


def _duration(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


async def _probe(cli, url, node):
    failed = {'url': url, 'status': 'failed', 'http_status': None, 'latency_ms': None}
    try:
        value = await _raw(cli, 'http', 'probe', url, node)
        if (isinstance(value, dict) and type(value.get('status')) is int
                and 100 <= value['status'] <= 599 and value.get('method') == 'HEAD'):
            status, duration = value['status'], _duration(value.get('duration-ms'))
        else:
            code, output = await cli('http', 'probe', url, node)
            if code:
                return failed
            matched = re.search(r'^Status: (\d+)\s*$', output, re.MULTILINE)
            elapsed = re.search(r'^Duration: ([\d.]+)(?: ms)?\s*$', output, re.MULTILINE)
            status = int(matched[1]) if matched else None
            duration = _duration(float(elapsed[1])) if elapsed else None
        return {'url': url, 'status': 'reachable' if status and 200 <= status < 300 else 'failed',
                'http_status': status, 'latency_ms': duration}
    except (OSError, ValueError, TypeError):
        return failed


async def diagnose(service, cli):
    context = evidence.identity('surge')
    try:
        version, mode = await asyncio.gather(_raw(cli, 'version'), _mode(cli))
        version = version if isinstance(version, dict) else {}
        platform = version.get('system')
        context = evidence.identity('surge', mode=mode, version=evidence.safe_version(version.get('version')),
                                    platform=platform if platform in ('macOS', 'iOS') else 'unknown')
        if mode not in {'rule', 'direct'}:
            reason = ('Surge 当前为全局代理模式；尚无可验证的全局策略链，无法确认出口。' if mode == 'proxy'
                      else '无法读取或识别 Surge 当前模式；无法确认运行出口。')
            return evidence.unavailable('surge', reason, observed_identity=context)
        domain = service['destinations'][0]
        route = (await _route(cli, domain) if mode == 'rule'
                 else {'node': 'DIRECT', 'rule': None, 'source': 'surge:mode'})
        node = route['node']
        if node is None:
            result = evidence.unavailable('surge', '无法读取 Surge 规则解释或最终策略；诊断命令可能不可用。',
                                          observed_identity=context)
            result['evidence'][1] = evidence.item('rule_match', 'unavailable', route['source'],
                                                 context['observed_at'], '规则解释没有可识别的最终策略。')
            return result
        probes = await asyncio.gather(*(_probe(cli, url, node) for url in
            ['https://cp.cloudflare.com/generate_204', *service['probe_urls']][:3]))
        after_mode = await _mode(cli)
        after_route = await _route(cli, domain) if mode == 'rule' and after_mode == mode else route
        stable = (after_mode == mode and after_route['node'] == node and after_route['rule'] == route['rule']
                  and after_route.get('selection') == route.get('selection'))
        stamp = context['observed_at']
        reason = '规则解释只评估给定域名；未观测真实请求，JSON 响应契约尚未经过真实客户端验证。'
        if route['source'].endswith(':text'):
            reason += ' 当前使用旧版 CLI 文本降级解析。'
        if not stable:
            reason += ' 探针前后的模式或策略变化或不可读。'
        return {'status': 'partial', 'actual_node': evidence.public_text(node),
                'scope': 'rule_match' if mode == 'rule' else 'client_mode', 'identity': context,
                'selection_stable': stable, 'probes': probes, 'service_tested': bool(service['probe_urls']),
                'evidence': [
                    evidence.item('group_selection', 'partial', route['source'], stamp,
                                  '读取模式与规则解释的最终策略；没有独立关联请求的组选择证据。'),
                    evidence.item('rule_match', 'partial' if mode == 'rule' else 'not_tested', route['source'], stamp,
                                  reason if mode == 'rule' else '当前为 Direct 模式，没有执行规则解释。'),
                    evidence.item('probe', 'partial', 'surge:http-probe', stamp,
                                  '指定策略的 HEAD 探针结果；响应契约未经过真实客户端验证，不证明手机、服务授权或登录状态。'),
                    evidence.configuration_identity(context),
                ], 'message': '读取服务器所配置 Surge 实例的模式与策略，并执行指定策略探针；规则解释不证明真实请求命中。'}
    except (OSError, ValueError, TypeError, IndexError):
        return evidence.unavailable('surge', '本机 Surge 诊断命令不可用。', observed_identity=context)


async def domain_routes(service, context, cli):
    gate = asyncio.Semaphore(3)
    mode = context['mode']
    async def inspect(domain):
        async with gate:
            try:
                route = (await _route(cli, domain) if mode == 'rule'
                         else {'node': 'DIRECT' if mode == 'direct' else None, 'rule': None})
            except (OSError, ValueError, TypeError):
                route = {'node': None, 'rule': None}
            return {'domain': domain, 'actual_node': evidence.public_text(route['node']) if route['node'] else None,
                    'rule': evidence.public_text(route['rule']) if route['rule'] else None,
                    'scope': 'rule_evaluation' if mode == 'rule' else 'client_mode', 'status': 'partial'}
    domains = service.get('diagnostic_destinations', service['destinations'])[:8]
    routes = await asyncio.gather(*(inspect(domain) for domain in domains))
    try:
        stable = await _mode(cli) == mode
    except (OSError, ValueError, TypeError):
        stable = False
    return routes, stable
