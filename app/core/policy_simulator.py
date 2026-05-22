from __future__ import annotations

import ipaddress

from app.ir import BUILTIN_POLICY_TARGETS, PolicyRule, PolicyWorkspace, SimulationStep, SimulationTrace


def simulate_destination(workspace: PolicyWorkspace, destination: str) -> SimulationTrace:
    destination = destination.strip().lower()
    trace = SimulationTrace(destination=destination)

    for rule in workspace.rules:
        matched = _rule_matches(rule, destination)
        if matched is None:
            trace.steps.append(
                SimulationStep(
                    type="rule",
                    ref=rule.id,
                    message=f"{rule.type} '{rule.match}' depends on provider/runtime data; skipped for deterministic MVP simulation.",
                    matched=None,
                )
            )
            continue

        trace.steps.append(
            SimulationStep(
                type="rule",
                ref=rule.id,
                message=f"{rule.type} '{rule.match}' {'matched' if matched else 'did not match'}.",
                matched=matched,
            )
        )
        if matched:
            trace.matched_rule = rule
            trace.target = rule.target
            trace.resolved = _resolve_target(workspace, rule.target, trace)
            return trace

    trace.warnings.append("No rule matched destination.")
    return trace


def _rule_matches(rule: PolicyRule, destination: str) -> bool | None:
    rule_type = rule.type.upper()
    match = rule.match.lower()

    if rule_type == "DOMAIN":
        return destination == match
    if rule_type == "DOMAIN-SUFFIX":
        return destination == match or destination.endswith(f".{match}")
    if rule_type == "DOMAIN-KEYWORD":
        return match in destination
    if rule_type == "IP-CIDR":
        return _ip_in_cidr(destination, match)
    if rule_type == "GEOIP":
        return None
    if rule_type == "RULE-SET":
        return None
    if rule_type == "MATCH":
        return True
    return False


def _ip_in_cidr(destination: str, cidr: str) -> bool:
    try:
        return ipaddress.ip_address(destination) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def _resolve_target(workspace: PolicyWorkspace, target: str, trace: SimulationTrace) -> str:
    groups = {group.name: group for group in workspace.proxy_groups}
    proxies = {proxy.name for proxy in workspace.proxies}
    seen: set[str] = set()

    def resolve(name: str) -> str:
        if name in BUILTIN_POLICY_TARGETS or name in proxies:
            trace.steps.append(SimulationStep(type="target", ref=name, message=f"Resolved to {name}.", matched=True))
            return name
        group = groups.get(name)
        if group is None:
            trace.warnings.append(f"Target '{name}' is missing.")
            return name
        if name in seen:
            trace.warnings.append(f"Group cycle while resolving '{name}'.")
            return name
        seen.add(name)
        trace.steps.append(
            SimulationStep(
                type="group",
                ref=name,
                message=f"Entered group '{name}' ({group.type}).",
                matched=True,
            )
        )
        if not group.members:
            trace.warnings.append(f"Group '{name}' has no members.")
            return name
        return resolve(group.members[0])

    return resolve(target)
