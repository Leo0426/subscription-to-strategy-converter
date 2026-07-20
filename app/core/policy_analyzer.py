from __future__ import annotations

import json
from collections import Counter, defaultdict

from app.ir import AnalyzerFinding, BUILTIN_POLICY_TARGETS, PolicyWorkspace


def _rule_key(raw: object) -> str:
    if isinstance(raw, str):
        return ",".join(part.strip() for part in raw.split(","))
    return json.dumps(raw, sort_keys=True, ensure_ascii=False)


def analyze_workspace(workspace: PolicyWorkspace) -> list[AnalyzerFinding]:
    findings: list[AnalyzerFinding] = []
    provider_names = {provider.name for provider in workspace.rule_providers}
    group_names = {group.name for group in workspace.proxy_groups}
    proxy_names = {proxy.name for proxy in workspace.proxies}
    valid_targets = BUILTIN_POLICY_TARGETS | group_names | proxy_names

    for rule in workspace.rules:
        if rule.type == "RULE-SET" and rule.provider and rule.provider not in provider_names:
            findings.append(
                AnalyzerFinding(
                    severity="error",
                    code="missing_provider",
                    message=f"Rule references missing provider '{rule.provider}'.",
                    path=f"rules[{rule.index}]",
                    ref=rule.id,
                )
            )
        if rule.target and rule.target not in valid_targets:
            findings.append(
                AnalyzerFinding(
                    severity="error",
                    code="missing_rule_target",
                    message=f"Rule target '{rule.target}' is not a group, proxy, or builtin target.",
                    path=f"rules[{rule.index}].target",
                    ref=rule.id,
                )
            )

    for group_index, group in enumerate(workspace.proxy_groups):
        if not group.members and not group.raw.get("include-all") and not group.raw.get("use"):
            findings.append(
                AnalyzerFinding(
                    severity="error",
                    code="empty_group",
                    message=f"Group '{group.name}' has no available members.",
                    path=f"proxy_groups[{group_index}].members",
                    ref=group.name,
                )
            )
        for member_index, member in enumerate(group.members):
            if member not in valid_targets:
                findings.append(
                    AnalyzerFinding(
                        severity="error",
                        code="missing_group_member",
                        message=f"Group '{group.name}' references missing member '{member}'.",
                        path=f"proxy_groups[{group_index}].members[{member_index}]",
                        ref=group.name,
                    )
                )

    rule_counts = Counter(_rule_key(rule.raw) for rule in workspace.rules)
    for rule in workspace.rules:
        if rule_counts[_rule_key(rule.raw)] > 1:
            findings.append(
                AnalyzerFinding(
                    severity="warning",
                    code="duplicate_rule",
                    message=f"Duplicate rule at index {rule.index}.",
                    path=f"rules[{rule.index}]",
                    ref=rule.id,
                )
            )

    findings.extend(_cycle_findings(workspace))
    findings.extend(_unreachable_group_findings(workspace))
    findings.extend(_unreachable_rule_findings(workspace))
    return findings


def _unreachable_rule_findings(workspace: PolicyWorkspace) -> list[AnalyzerFinding]:
    findings: list[AnalyzerFinding] = []
    terminal_index: int | None = None
    for rule in workspace.rules:
        if terminal_index is not None:
            findings.append(
                AnalyzerFinding(
                    severity="warning",
                    code="unreachable_rule",
                    message=f"Rule at index {rule.index} is unreachable after terminal rule {terminal_index}.",
                    path=f"rules[{rule.index}]",
                    ref=rule.id,
                )
            )
        elif rule.type in {"MATCH", "FINAL"}:
            terminal_index = rule.index
    return findings


def _cycle_findings(workspace: PolicyWorkspace) -> list[AnalyzerFinding]:
    groups = {group.name: group for group in workspace.proxy_groups}
    state: dict[str, str] = {}
    stack: list[str] = []
    findings: list[AnalyzerFinding] = []
    seen_cycles: set[tuple[str, ...]] = set()

    def visit(name: str) -> None:
        state[name] = "visiting"
        stack.append(name)
        for member in groups[name].members:
            if member not in groups:
                continue
            if state.get(member) == "visiting":
                cycle = stack[stack.index(member):] + [member]
                key = tuple(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    findings.append(
                        AnalyzerFinding(
                            severity="error",
                            code="group_cycle",
                            message=f"Group cycle detected: {' -> '.join(cycle)}.",
                            path=f"proxy_groups.{member}",
                            ref=member,
                        )
                    )
            elif state.get(member) is None:
                visit(member)
        stack.pop()
        state[name] = "visited"

    for group in workspace.proxy_groups:
        if state.get(group.name) is None:
            visit(group.name)
    return findings


def _unreachable_group_findings(workspace: PolicyWorkspace) -> list[AnalyzerFinding]:
    groups = {group.name: group for group in workspace.proxy_groups}
    reverse_refs: dict[str, set[str]] = defaultdict(set)
    roots = {rule.target for rule in workspace.rules if rule.target in groups}
    reachable: set[str] = set()

    for group in workspace.proxy_groups:
        for member in group.members:
            if member in groups:
                reverse_refs[member].add(group.name)

    def mark(name: str) -> None:
        if name in reachable or name not in groups:
            return
        reachable.add(name)
        for member in groups[name].members:
            mark(member)

    for root in roots:
        mark(root)

    findings: list[AnalyzerFinding] = []
    for index, group in enumerate(workspace.proxy_groups):
        if group.name not in reachable and group.name not in roots and not reverse_refs[group.name]:
            findings.append(
                AnalyzerFinding(
                    severity="info",
                    code="unreachable_group",
                    message=f"Group '{group.name}' is not referenced by any rule or group.",
                    path=f"proxy_groups[{index}]",
                    ref=group.name,
                )
            )
    return findings
