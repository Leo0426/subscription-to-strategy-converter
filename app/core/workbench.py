"""Client-independent workbench reports; no endpoint probing or credential echo."""
from __future__ import annotations

from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace
from app.core.service_catalog import service_catalog


def service_report(config: dict, nodes: list, service_id: str | None = None) -> list[dict]:
    workspace = config_to_workspace(config, nodes)
    reports = []
    for service in service_catalog():
        if service_id and service['id'] != service_id:
            continue
        domains = []
        for destination in service['destinations']:
            trace = simulate_destination(workspace, destination)
            certain = trace.matched_rule is not None and trace.matched_rule.type in {'DOMAIN', 'DOMAIN-SUFFIX'}
            domains.append({
                'domain': destination, 'target': trace.target,
                'configured_node': trace.resolved,
                'path': [step.ref for step in trace.steps if step.type in {'group','target'}],
                'rule': trace.matched_rule.raw if trace.matched_rule else None,
                'status': 'matched' if certain else 'runtime_rules_required',
            })
        targets = {domain['target'] for domain in domains if domain['status'] == 'matched'}
        status = 'consistent' if len(targets) == 1 and all(d['status']=='matched' for d in domains) else 'needs_review'
        reports.append({'id': service['id'], 'label': service['label'], 'status': status,
                        'domains': domains, 'actual_node': None,
                        'note': '配置模拟按首个候选展示；远程规则集、客户端已保存选择与运行态需另行核实。'})
    return reports


def profile_mode(request: dict) -> str:
    return 'legacy_snapshot' if any(request.get(key) for key in (
        'selected_policy','preset','rule_packs','route_intent','custom_strategy','claude_policy'
    )) and not any(r.get('mode', 'legacy') != 'legacy' for r in request.get('service_routes', [])) else 'service_preferences'
