"""Read/probe-only adapters; controller locations and credentials are operator-owned."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

import httpx

_SURGE = '/Applications/Surge.app/Contents/Applications/surge-cli'


def runtime_capabilities() -> dict:
    return {'mihomo': bool(os.environ.get('SUBFLOW_MIHOMO_CONTROLLER')),
            'surge': Path(os.environ.get('SUBFLOW_SURGE_CLI', _SURGE)).is_file()}


async def diagnose_runtime(client: str, service: dict, expected_target: str) -> dict:
    if not runtime_capabilities().get(client):
        return {'status': 'unavailable', 'message': '未配置客户端连接；无法验证实际节点或访问。', 'actual_node': None}
    if client == 'surge':
        return await _surge(service)
    return await _mihomo(service, expected_target)


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
            chain = []
            selected = target
            for _ in range(32):
                if selected in chain or selected not in proxies:
                    return {'status':'mismatch','message':'客户端中找不到预期策略或存在循环，请先更新对应订阅。','actual_node':None}
                chain.append(selected)
                now = proxies[selected].get('now')
                if not now:
                    if proxies[selected].get('all'):
                        return {'status':'mismatch','message':'该策略组没有唯一当前节点，无法进行单节点验证。','actual_node':None}
                    break
                selected = now
            else:
                return {'status':'mismatch','message':'客户端策略链过深。','actual_node':None}
            checks = []
            for url in ['https://cp.cloudflare.com/generate_204', *service['probe_urls']]:
                try:
                    result = await client.get(f'proxies/{quote(selected, safe="")}/delay',
                                              params={'url':url, 'timeout':8000, 'expected':'200-299'})
                    latency = result.json().get('delay') if result.status_code == 200 else None
                    checks.append({'url':url,'status':'reachable' if isinstance(latency, (int,float)) else 'failed',
                                   'latency_ms':latency if isinstance(latency,(int,float)) else None})
                except (httpx.HTTPError, ValueError):
                    checks.append({'url':url, 'status':'failed', 'latency_ms':None})
            return {'status':'observed','actual_node':selected, 'path':chain, 'probes':checks,
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
        probes = []
        for url in ['https://cp.cloudflare.com/generate_204', *service['probe_urls']]:
            _, output = await _cli('http','probe',url,node)
            status = re.search(r'^Status: (\d+)', output, re.MULTILINE)
            duration = re.search(r'^Duration: ([\d.]+)', output, re.MULTILINE)
            http_status = int(status[1]) if status else None
            probes.append({'url':url,'status':'reachable' if http_status and 200 <= http_status < 300 else 'failed',
                           'http_status':http_status,'latency_ms':float(duration[1]) if duration else None})
        return {'status':'observed','actual_node':node,'scope':'rule_match','probes':probes,'service_tested':bool(service['probe_urls']),
                'message':'读取 Surge 实际规则选择并通过该节点进行 HEAD 探测；未验证登录或对话。'}
    except (OSError, ValueError):
        return {'status':'unavailable','actual_node':None,'message':'本机 Surge 诊断命令不可用。'}
