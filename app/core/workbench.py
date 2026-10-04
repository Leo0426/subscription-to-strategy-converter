"""Client-independent workbench reports; no endpoint probing or credential echo."""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace
from app.core.service_catalog import catalog_revision, service_catalog, shared_service_dependencies


def shared_dependency_warnings(config: dict, selected_services: set[str]) -> list[dict]:
    """Compare policy anchors without assuming a group's current selection."""
    if not selected_services:
        return []
    services = {service['id']: service for service in service_catalog()}
    workspace = config_to_workspace(config)
    groups = {group.name: group for group in workspace.proxy_groups}

    def anchor(target: str) -> str:
        seen = set()
        while target in groups and target not in seen:
            seen.add(target)
            group = groups[target]
            # Different auto groups can independently fail over, even with the
            # same members. Collapse only a select with exactly one choice.
            if group.type != 'select' or len(group.members) != 1:
                break
            target = group.members[0]
        return target

    def route(destination: str) -> str | None:
        trace = simulate_destination(workspace, destination)
        if trace.matched_rule is None or any(step.matched is None for step in trace.steps):
            return None
        return anchor(trace.target)

    warnings = []
    for dependency in shared_service_dependencies():
        if not selected_services.intersection(dependency['services']):
            continue
        service_routes = {
            service_id: route(services[service_id]['destinations'][0])
            for service_id in dependency['services']
        }
        dependency_routes = {domain: route(domain) for domain in dependency['destinations']}
        anchors = set(service_routes.values()) | set(dependency_routes.values())
        if len(anchors) == 1 and None not in anchors:
            continue
        labels = '、'.join(services[service_id]['label'] for service_id in dependency['services'])
        warnings.append({
            'code': 'shared_service_dependency_egress',
            'scope': 'configuration', 'dependency': dependency['id'],
            'services': dependency['services'], 'domains': dependency['destinations'],
            'message': f"{labels} 使用{dependency['label']}时共用身份域名，当前策略可能使用不同出口；域名规则无法区分由哪个服务发起登录。",
            'suggestion': f"{labels} 使用{dependency['label']}时共用身份域名。若需登录链保持同一出口，请使用同一固定节点或共用同一手选策略组，并在客户端核对登录请求。",
        })
    return warnings


def service_report(config: dict, nodes: list, service_id: str | None = None) -> list[dict]:
    workspace = config_to_workspace(config, nodes)
    observed_at = datetime.now(timezone.utc).isoformat()
    revision = catalog_revision()
    reports = []
    for service in service_catalog():
        if service_id and service['id'] != service_id:
            continue
        domains = []
        for destination in service['destinations']:
            trace = simulate_destination(workspace, destination)
            certain = (
                trace.matched_rule is not None
                and trace.matched_rule.type in {'DOMAIN', 'DOMAIN-SUFFIX'}
                and not any(step.type == 'rule' and step.matched is None for step in trace.steps)
            )
            reasons = []
            if any(step.type == 'rule' and step.matched is None for step in trace.steps):
                reasons.append('earlier_rule_requires_runtime')
            if trace.matched_rule is None or trace.matched_rule.type not in {'DOMAIN', 'DOMAIN-SUFFIX'}:
                reasons.append('no_domain_rule_match')
            if any(step.type == 'group' for step in trace.steps):
                reasons.append('client_selection_not_observed')
            if trace.resolved and not any(step.type == 'target' for step in trace.steps):
                reasons.append('target_resolution_incomplete')
            domains.append({
                'domain': destination, 'target': trace.target,
                'configured_node': trace.resolved,
                'path': [step.ref for step in trace.steps if step.type in {'group','target'}],
                'rule': trace.matched_rule.raw if trace.matched_rule else None,
                'status': 'matched' if certain else 'runtime_rules_required',
                'evidence': {
                    'status': 'inferred' if certain and 'target_resolution_incomplete' not in reasons else 'partial',
                    'reasons': reasons,
                },
            })
        targets = {domain['target'] for domain in domains if domain['status'] == 'matched'}
        status = 'consistent' if len(targets) == 1 and all(d['status']=='matched' for d in domains) else 'needs_review'
        reports.append({'id': service['id'], 'label': service['label'], 'status': status,
                        'domains': domains, 'actual_node': None,
                        'evidence': {
                            'scope': 'configuration_simulation',
                            'status': 'inferred' if all(d['evidence']['status'] == 'inferred' for d in domains) else 'partial',
                            'observed_at': observed_at, 'catalog_revision': revision,
                            'reasons': list(dict.fromkeys(r for d in domains for r in d['evidence']['reasons'])),
                        },
                        'note': '配置模拟按首个候选展示；远程规则集、客户端已保存选择与运行态需另行核实。'})
    return reports


def profile_mode(request: dict) -> str:
    return 'legacy_snapshot' if any(request.get(key) for key in (
        'selected_policy','preset','rule_packs','route_intent','custom_strategy','claude_policy'
    )) and not any(r.get('mode', 'legacy') != 'legacy' for r in request.get('service_routes', [])) else 'service_preferences'
