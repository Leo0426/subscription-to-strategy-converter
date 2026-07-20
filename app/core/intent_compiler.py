from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from app.models.intent import NodePoolIntent, RouteIntent
from app.models.strategy import NodeSelector, SelectedPolicy
from app.core.rule_packs import list_rule_packs


REGIONS: dict[str, dict[str, str]] = {
    "hk": {"label": "香港", "pattern": r"香港|Hong\s*Kong|\bHK\b"},
    "us": {"label": "美国", "pattern": r"美国|美國|United\s*States|\bUSA?\b"},
    "jp": {"label": "日本", "pattern": r"日本|Japan|\bJP\b"},
    "sg": {"label": "新加坡", "pattern": r"新加坡|狮城|獅城|Singapore|\bSG\b"},
    "tw": {"label": "台湾", "pattern": r"台湾|台灣|Taiwan|\bTW\b"},
    "kr": {"label": "韩国", "pattern": r"韩国|韓國|Korea|\bKR\b"},
    "gb": {"label": "英国", "pattern": r"英国|英國|United\s*Kingdom|\bUK\b"},
    "de": {"label": "德国", "pattern": r"德国|德國|Germany|\bDE\b"},
    "ca": {"label": "加拿大", "pattern": r"加拿大|Canada|\bCA\b"},
    "au": {"label": "澳大利亚", "pattern": r"澳大利亚|澳洲|Australia|\bAU\b"},
}

def intent_catalog() -> dict[str, list[dict[str, str]]]:
    rule_packs = list_rule_packs()["packs"]
    return {
        "regions": [
            {"id": key, "label": value["label"], "pattern": value["pattern"]}
            for key, value in REGIONS.items()
        ],
        "services": [
            {
                "id": pack["id"],
                "label": pack["label"],
                "description": pack["description"],
                "category": pack["category"],
            }
            for pack in rule_packs
        ],
    }


def _pool_selector(pool: NodePoolIntent) -> NodeSelector:
    requirements: list[str] = []
    if pool.regions:
        requirements.append("(?:" + "|".join(REGIONS[region]["pattern"] for region in pool.regions) + ")")
    if pool.include_keywords:
        requirements.append("(?:" + "|".join(re.escape(item) for item in pool.include_keywords) + ")")
    name_regex = "(?i)" + "".join(f"(?=.*{requirement})" for requirement in requirements) + ".*"
    exclude_regex = (
        "(?i)(?:" + "|".join(re.escape(item) for item in pool.exclude_keywords) + ")"
        if pool.exclude_keywords
        else None
    )
    return NodeSelector(
        id=pool.id,
        name_regex=name_regex,
        exclude_regex=exclude_regex,
        protocols=pool.protocols,
    )


def _rule_target(rule: Any) -> str:
    if not isinstance(rule, str):
        return ""
    parts = [part.strip() for part in rule.split(",")]
    if len(parts) >= 4 and parts[-1].lower() == "no-resolve":
        return parts[-2]
    return parts[-1] if len(parts) > 1 else ""


def compile_route_intent(base_policy: SelectedPolicy, intent: RouteIntent) -> SelectedPolicy:
    services = {pack["id"]: pack for pack in list_rule_packs()["packs"]}
    unknown_services = sorted({route.service for route in intent.routes} - set(services))
    if unknown_services:
        raise ValueError("unknown services: " + ", ".join(unknown_services))
    selectors = {selector.id: selector for selector in base_policy.node_selectors}
    selectors.update({pool.id: _pool_selector(pool) for pool in intent.node_pools})

    groups = [deepcopy(group) for group in base_policy.proxy_groups]
    group_indexes = {
        str(group.get("name")): index
        for index, group in enumerate(groups)
        if isinstance(group, dict) and group.get("name")
    }
    routed_targets: set[str] = set()
    route_rules: list[str] = []
    for route in intent.routes:
        service = services[route.service]
        target = str(service["group"]["name"])
        members = [f"selector:{route.primary_pool}"]
        if route.fallback_pool:
            members.append(f"selector:{route.fallback_pool}")
        members.append(route.final_target)
        group = {"name": target, "type": "select", "proxies": list(dict.fromkeys(members))}
        if target in group_indexes:
            groups[group_indexes[target]] = group
        else:
            group_indexes[target] = len(groups)
            groups.append(group)
        routed_targets.add(target)
        route_rules.extend(service["rules"])

    existing_rules = [
        deepcopy(rule)
        for rule in base_policy.rules
        if _rule_target(rule) not in routed_targets
    ]
    return SelectedPolicy(
        mode="replace",
        node_selectors=list(selectors.values()),
        proxy_groups=groups,
        rule_providers=deepcopy(base_policy.rule_providers),
        rules=list(dict.fromkeys(route_rules + existing_rules)),
    )
