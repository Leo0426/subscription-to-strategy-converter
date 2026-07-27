from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from app.core.provider_egress import needs_egress, resolve_egress_group
from app.ir import AnalyzerFinding, BUILTIN_POLICY_TARGETS, PolicyWorkspace


_RULE_SET_REFERENCE = re.compile(r"\bRULE-SET,\s*([^(),]+)", re.IGNORECASE)

#: Providers are downloaded on every cold start. Past this many, a client on
#: modest hardware (router, OpenClash) risks a startup timeout.
_PROVIDER_COUNT_BUDGET = 200

#: `format: mrs` rule sets require a Mihomo core; older Clash cores reject them.
_MODERN_CORE_FORMATS = {"mrs"}


def _rule_key(raw: object) -> str:
    if isinstance(raw, str):
        return ",".join(part.strip() for part in raw.split(","))
    return json.dumps(raw, sort_keys=True, ensure_ascii=False)


def _rule_provider_references(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str):
        return ()
    return tuple(dict.fromkeys(match.strip() for match in _RULE_SET_REFERENCE.findall(raw)))


def analyze_workspace(workspace: PolicyWorkspace) -> list[AnalyzerFinding]:
    findings: list[AnalyzerFinding] = []
    provider_names = {provider.name for provider in workspace.rule_providers}
    group_names = {group.name for group in workspace.proxy_groups}
    proxy_names = {proxy.name for proxy in workspace.proxies}
    valid_targets = BUILTIN_POLICY_TARGETS | group_names | proxy_names

    for rule in workspace.rules:
        for provider in _rule_provider_references(rule.raw):
            if provider not in provider_names:
                findings.append(
                    AnalyzerFinding(
                        severity="error",
                        code="missing_provider",
                        message=f"Rule references missing provider '{provider}'.",
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
    findings.extend(_runtime_feasibility_findings(workspace))
    findings.extend(_shared_infra_ip_rule_findings(workspace))
    return findings


def _shared_infra_ip_rule_findings(workspace: PolicyWorkspace) -> list[AnalyzerFinding]:
    """Warn when an ipcidr RULE-SET routes to a service group without no-resolve.

    Service IPs are shared infrastructure (Google front IPs carry YouTube and
    Gemini alike), so a resolving IP rule hijacks domain traffic that earlier
    domain rules did not claim and splits one page across two egresses.
    Geo/private fallbacks targeting DIRECT legitimately resolve and are exempt.
    """
    ipcidr_providers = {
        provider.name
        for provider in workspace.rule_providers
        if str(provider.raw.get("behavior") or "").lower() == "ipcidr"
    }
    if not ipcidr_providers:
        return []
    offending = [
        rule
        for rule in workspace.rules
        if rule.type == "RULE-SET"
        and rule.provider in ipcidr_providers
        and rule.target not in {"DIRECT", ""}
        and "no-resolve" not in str(rule.raw)
    ]
    if not offending:
        return []
    return [
        AnalyzerFinding(
            severity="warning",
            code="ip_rule_resolves_shared_infra",
            message=(
                f"{len(offending)} ipcidr rules route to service groups without no-resolve; "
                f"they force DNS resolution and can hijack shared-infrastructure domains "
                f"(e.g. Google front IPs) away from their domain rules."
            ),
            path=f"rules[{offending[0].index}]",
            ref=offending[0].id,
        )
    ]


def _runtime_feasibility_findings(workspace: PolicyWorkspace) -> list[AnalyzerFinding]:
    """Report conditions that keep a structurally valid profile from running.

    These never block publication — the operator may knowingly target a client
    that copes — so they stay at warning severity and are aggregated rather than
    emitted once per provider.
    """
    findings: list[AnalyzerFinding] = []
    providers = workspace.rule_providers
    if not providers:
        return findings

    stranded = [provider.name for provider in providers if needs_egress(provider.raw)]
    if stranded and resolve_egress_group(group.name for group in workspace.proxy_groups) is None:
        findings.append(
            AnalyzerFinding(
                severity="warning",
                code="provider_unreachable",
                message=(
                    f"{len(stranded)} rule providers download from hosts with no direct route "
                    f"and no proxy group is available to route them; their rules will be empty."
                ),
                path="rule_providers",
                ref=stranded[0],
            )
        )

    if len(providers) > _PROVIDER_COUNT_BUDGET:
        findings.append(
            AnalyzerFinding(
                severity="warning",
                code="provider_count_budget",
                message=(
                    f"{len(providers)} rule providers are downloaded on every cold start, "
                    f"above the {_PROVIDER_COUNT_BUDGET} budget; constrained clients may time out."
                ),
                path="rule_providers",
            )
        )

    modern = [provider.name for provider in providers if provider.format.lower() in _MODERN_CORE_FORMATS]
    if modern:
        findings.append(
            AnalyzerFinding(
                severity="info",
                code="provider_requires_mihomo_core",
                message=(
                    f"{len(modern)} rule providers use the mrs format and require a Mihomo "
                    f"core (>= 1.18.0); older Clash cores reject them."
                ),
                path="rule_providers",
                ref=modern[0],
            )
        )

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
