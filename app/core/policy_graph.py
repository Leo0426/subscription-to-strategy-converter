from __future__ import annotations

from app.ir import BUILTIN_POLICY_TARGETS, PolicyGraph, PolicyGraphEdge, PolicyGraphNode, PolicyWorkspace


def build_policy_graph(workspace: PolicyWorkspace) -> PolicyGraph:
    nodes: dict[str, PolicyGraphNode] = {}
    edges: list[PolicyGraphEdge] = []
    provider_names = {provider.name for provider in workspace.rule_providers}
    group_names = {group.name for group in workspace.proxy_groups}
    proxy_names = {proxy.name for proxy in workspace.proxies}

    def add_node(node_id: str, node_type: str, label: str, **meta: object) -> None:
        nodes.setdefault(node_id, PolicyGraphNode(id=node_id, type=node_type, label=label, meta=dict(meta)))

    def add_edge(source: str, target: str, edge_type: str, label: str = "") -> None:
        edges.append(
            PolicyGraphEdge(
                id=f"{source}->{target}:{edge_type}:{len(edges)}",
                source=source,
                target=target,
                type=edge_type,
                label=label,
            )
        )

    for builtin in sorted(BUILTIN_POLICY_TARGETS):
        add_node(f"builtin:{builtin}", "builtin", builtin)

    for proxy in workspace.proxies:
        add_node(f"proxy:{proxy.name}", "proxy", proxy.name, protocol=proxy.protocol, server=proxy.server, port=proxy.port)

    for provider in workspace.rule_providers:
        add_node(
            f"provider:{provider.name}",
            "provider",
            provider.name,
            behavior=provider.behavior,
            format=provider.format,
            url=provider.url,
        )

    for group in workspace.proxy_groups:
        group_id = f"group:{group.name}"
        add_node(group_id, "group", group.name, group_type=group.type)
        for member in group.members:
            member_id = _target_node_id(member, group_names, proxy_names)
            if member_id is None:
                member_id = f"missing:{member}"
                add_node(member_id, "missing", member)
            add_edge(group_id, member_id, "group-member")

    for rule in workspace.rules:
        rule_id = f"rule:{rule.index}"
        add_node(rule_id, "rule", rule.type, match=rule.match, target=rule.target, provider=rule.provider)
        if rule.provider:
            provider_id = f"provider:{rule.provider}" if rule.provider in provider_names else f"missing:{rule.provider}"
            if rule.provider not in provider_names:
                add_node(provider_id, "missing", rule.provider)
            add_edge(rule_id, provider_id, "rule-provider")
        if rule.target:
            target_id = _target_node_id(rule.target, group_names, proxy_names)
            if target_id is None:
                target_id = f"missing:{rule.target}"
                add_node(target_id, "missing", rule.target)
            add_edge(rule_id, target_id, "rule-target")

    return PolicyGraph(nodes=list(nodes.values()), edges=edges)


def _target_node_id(target: str, group_names: set[str], proxy_names: set[str]) -> str | None:
    if target in group_names:
        return f"group:{target}"
    if target in proxy_names:
        return f"proxy:{target}"
    if target in BUILTIN_POLICY_TARGETS:
        return f"builtin:{target}"
    return None
