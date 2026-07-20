from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from app.core.template_engine import AI_RULES, COMMON_RULES, DEV_RULES, PRESET_TEMPLATES, STREAMING_RULES
from app.models.strategy import SelectedPolicy


_CATEGORY_META = {
    "ai": {"label": "AI 工具", "accent": "blue"},
    "developer": {"label": "开发服务", "accent": "green"},
    "streaming": {"label": "流媒体", "accent": "orange"},
}

_PACK_META = {
    "Developer": {"label": "开发工具", "description": "npm、Docker、JetBrains 与 SDK 资源"},
    "GitHub Copilot": {"label": "GitHub Copilot", "description": "Copilot 服务与模型请求"},
    "Microsoft": {"label": "Microsoft", "description": "Microsoft 与 Visual Studio 服务"},
    "Apple": {"label": "Apple", "description": "Apple 服务与系统资源"},
    "Disney": {"label": "Disney+", "description": "Disney+ 流媒体服务"},
    "Telegram": {"label": "Telegram", "description": "Telegram 消息与资源服务"},
}

_FOUNDATION_GROUPS = {"Proxy", "Auto", "Fallback", "Global", "China", "Reject"}


def _rule_target(rule: str) -> str:
    parts = [part.strip() for part in rule.split(",")]
    return parts[-1] if len(parts) > 1 else ""


def _pack_id(group_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", group_name.lower()).strip("-")


def _category_for_rule(rule: str) -> str:
    if rule in AI_RULES:
        return "ai"
    if rule in DEV_RULES:
        return "developer"
    return "streaming"


def _dependency_groups(group: dict[str, Any], groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen or name not in groups or name in _FOUNDATION_GROUPS:
            return
        seen.add(name)
        candidate = groups[name]
        for member in candidate.get("proxies", []):
            if isinstance(member, str):
                visit(member)
        dependencies.append(deepcopy(candidate))

    for member in group.get("proxies", []):
        if isinstance(member, str):
            visit(member)
    return dependencies


def list_rule_packs() -> dict[str, Any]:
    config = PRESET_TEMPLATES["full"]["config"]
    groups = {
        group["name"]: group
        for group in config["proxy-groups"]
        if isinstance(group, dict) and group.get("name")
    }
    business_rules = AI_RULES + DEV_RULES + STREAMING_RULES
    rules_by_target: dict[str, list[str]] = {}
    categories: dict[str, str] = {}
    for rule in business_rules:
        target = _rule_target(rule)
        rules_by_target.setdefault(target, []).append(rule)
        categories.setdefault(target, _category_for_rule(rule))

    packs: list[dict[str, Any]] = []
    for target, rules in rules_by_target.items():
        group = groups[target]
        category = categories[target]
        meta = _PACK_META.get(target, {})
        packs.append(
            {
                "id": _pack_id(target),
                "label": meta.get("label", target),
                "description": meta.get("description", f"{target} 相关域名与服务请求"),
                "category": category,
                "category_label": _CATEGORY_META[category]["label"],
                "accent": _CATEGORY_META[category]["accent"],
                "group": deepcopy(group),
                "dependencies": _dependency_groups(group, groups),
                "rules": deepcopy(rules),
                "rule_count": len(rules),
            }
        )
    preset_defaults = {
        "general": [],
        "ai": [pack["id"] for pack in packs if pack["category"] == "ai" or pack["id"] == "github"],
        "streaming": [pack["id"] for pack in packs if pack["category"] == "streaming"],
        "developer": [pack["id"] for pack in packs if pack["category"] == "developer"],
        "blank": [],
    }
    return {
        "foundation": {
            "label": "基础路由",
            "description": "Proxy、Auto、Fallback、国内直连与最终匹配始终保留。",
            "groups": deepcopy(PRESET_TEMPLATES["minimal"]["config"]["proxy-groups"]),
            "rules": deepcopy(COMMON_RULES),
        },
        "categories": [
            {"id": category_id, **meta}
            for category_id, meta in _CATEGORY_META.items()
        ],
        "preset_defaults": preset_defaults,
        "packs": packs,
    }


def assemble_rule_packs(pack_ids: list[str]) -> SelectedPolicy:
    catalog = list_rule_packs()
    packs_by_id = {pack["id"]: pack for pack in catalog["packs"]}
    unknown = sorted(set(pack_ids) - set(packs_by_id))
    if unknown:
        raise ValueError("unknown rule packs: " + ", ".join(unknown))

    groups = deepcopy(catalog["foundation"]["groups"])
    group_names = {group["name"] for group in groups}
    rules: list[str] = []
    selected = set(pack_ids)
    for pack in catalog["packs"]:
        if pack["id"] not in selected:
            continue
        for group in [*pack["dependencies"], pack["group"]]:
            if group["name"] not in group_names:
                groups.append(deepcopy(group))
                group_names.add(group["name"])
        rules.extend(deepcopy(pack["rules"]))

    return SelectedPolicy(
        mode="replace",
        proxy_groups=groups,
        rule_providers={},
        rules=list(dict.fromkeys(rules + deepcopy(catalog["foundation"]["rules"]))),
    )
