from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
import re
from typing import Any
from urllib.parse import urlparse

from app.ir import ProxyNode
from app.models.strategy import ClaudePolicy, ServiceRoute
from app.core.service_catalog import service_catalog, service_rules


class TemplatePolicyTransformError(ValueError):
    pass


_CLAUDE_RE = re.compile(r"claude|anthropic", re.IGNORECASE)
_SURGE_RULE_EXTENSIONS = {".list", ".txt", ".conf"}
_SURGE_RULE_TYPES = {
    "DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
    "IP-CIDR", "IP-CIDR6", "GEOIP", "PROCESS-NAME", "USER-AGENT",
    "URL-REGEX", "DEST-PORT", "RULE-SET", "MATCH", "FINAL",
}


def transform_service_routes(
    config: dict[str, Any],
    nodes: list[ProxyNode],
    routes: list[ServiceRoute],
    *,
    target: str = "clash",
) -> dict[str, Any]:
    result = config
    for route in routes:
        if not route.enabled:
            continue
        if route.mode != "legacy":
            result = _transform_current_service(result, nodes, route)
            continue
        if route.service != "claude":
            raise TemplatePolicyTransformError(
                f"unsupported service route: {route.service}"
            )
        result = transform_claude_policy(
            result,
            nodes,
            ClaudePolicy(
                enabled=True,
                egress=route.egress,
                fallback=route.fallback,
            ),
            target=target,
        )
    return result


def _transform_current_service(config: dict, nodes: list[ProxyNode], route: ServiceRoute) -> dict:
    service = next((item for item in service_catalog() if item["id"] == route.service), None)
    if service is None:
        raise TemplatePolicyTransformError(f"unknown service: {route.service}")
    node_names = {node.name for node in nodes}
    groups = {group["name"] for group in config.get("proxy-groups", [])}
    allowed = node_names | {"DIRECT", "REJECT"}
    if route.mode == "manual":
        allowed |= groups - {service["group"]}
    if route.egress not in allowed:
        raise TemplatePolicyTransformError(f"{service['label']} 的出口不存在或不符合模式：{route.egress}")
    if route.mode == "fallback" and (route.egress not in node_names or route.fallback not in node_names):
        raise TemplatePolicyTransformError("故障切换的主备必须都是当前订阅中的节点")
    result = deepcopy(config)
    group = {"name": service["group"], "type": "select", "proxies": [route.egress]}
    if route.mode == "fallback":
        group.update(type="fallback", proxies=[route.egress, route.fallback],
                     url="https://cp.cloudflare.com/generate_204", interval=600,
                     timeout=5000, **{"expected-status": 204})
    result["proxy-groups"] = [g for g in result["proxy-groups"] if g["name"] != group["name"]] + [group]
    overrides = service_rules(service)
    # Providers for a single service (not broad shared providers) follow its override.
    original = []
    matches = {rule.rsplit(",", 1)[0] for rule in overrides}
    for rule in result.get("rules", []):
        parts = str(rule).split(",")
        owned_reference = len(parts) >= 3 and (
            (parts[0] == "RULE-SET" and parts[1] in service.get("rule_providers", []))
            or (parts[0] == "GEOSITE" and parts[1] in service.get("geosite_tags", []))
        )
        if owned_reference:
            parts[2] = group["name"]
            overrides.append(",".join(parts))
        elif str(rule).rsplit(",", 1)[0] not in matches:
            original.append(rule)
    result["rules"] = list(dict.fromkeys(overrides + original))
    return result


@dataclass(frozen=True)
class ClaudeTemplateCapability:
    contains_claude: bool
    rule_count: int
    rule_provider_names: tuple[str, ...]
    current_targets: tuple[str, ...]
    dedicated_group: str | None
    surge_compatible: bool
    surge_incompatibility_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_claude_template(config: dict[str, Any]) -> ClaudeTemplateCapability:
    rules = config.get("rules", [])
    providers = config.get("rule-providers", {})
    groups = config.get("proxy-groups", [])
    group_names = {
        str(group.get("name"))
        for group in groups
        if isinstance(group, dict) and group.get("name")
    }

    matches = [_parse_rule(rule) for rule in rules if isinstance(rule, str)]
    matches = [parts for parts in matches if parts and _is_claude_rule(parts)]
    provider_names = tuple(
        dict.fromkeys(parts[1] for parts in matches if parts[0].upper() == "RULE-SET")
    )
    targets = tuple(dict.fromkeys(parts[2] for parts in matches if len(parts) >= 3))
    dedicated = next((target for target in targets if target in group_names and _is_claude(target)), None)
    if dedicated is None:
        dedicated = next(
            (
                str(group.get("name"))
                for group in groups
                if isinstance(group, dict) and group.get("name") and _is_claude(str(group["name"]))
            ),
            None,
        )

    reasons: list[str] = []
    referenced_providers = tuple(
        dict.fromkeys(
            parts[1]
            for parts in (_parse_rule(rule) for rule in rules if isinstance(rule, str))
            if len(parts) >= 3 and parts[0].upper() == "RULE-SET"
        )
    )
    missing_provider_urls: list[str] = []
    unsupported_formats: dict[str, list[str]] = {}
    for name in referenced_providers:
        provider = providers.get(name) if isinstance(providers, dict) else None
        url = provider.get("url") if isinstance(provider, dict) else None
        if not isinstance(url, str) or not url.strip():
            missing_provider_urls.append(name)
            continue
        extension = PurePosixPath(urlparse(url).path).suffix.lower()
        if extension not in _SURGE_RULE_EXTENSIONS:
            unsupported_formats.setdefault(extension or "unknown", []).append(name)
    if missing_provider_urls:
        reasons.append("rule providers without URL: " + _summarize_names(missing_provider_urls))
    if unsupported_formats:
        details = "; ".join(
            f"{extension}: {_summarize_names(names)}"
            for extension, names in sorted(unsupported_formats.items())
        )
        reasons.append(
            f"{sum(len(names) for names in unsupported_formats.values())} rule providers use unsupported Surge formats ({details})"
        )
    unsupported_rule_types = tuple(
        dict.fromkeys(
            parts[0].upper()
            for parts in (_parse_rule(rule) for rule in rules if isinstance(rule, str))
            if parts and parts[0].upper() not in _SURGE_RULE_TYPES
        )
    )
    if unsupported_rule_types:
        reasons.append("unsupported Surge rule types: " + ", ".join(unsupported_rule_types))

    return ClaudeTemplateCapability(
        contains_claude=bool(matches),
        rule_count=len(matches),
        rule_provider_names=provider_names,
        current_targets=targets,
        dedicated_group=dedicated,
        surge_compatible=bool(matches) and not reasons,
        surge_incompatibility_reasons=tuple(dict.fromkeys(reasons)),
    )


def transform_claude_policy(
    config: dict[str, Any],
    nodes: list[ProxyNode],
    policy: ClaudePolicy | None,
    *,
    target: str = "clash",
) -> dict[str, Any]:
    if policy is None or not policy.enabled:
        return config

    capability = analyze_claude_template(config)
    if not capability.contains_claude:
        raise TemplatePolicyTransformError(
            "selected template does not contain a recognizable Claude policy"
        )
    if target == "surge" and not capability.surge_compatible:
        raise TemplatePolicyTransformError(
            "selected template is not Surge-compatible: "
            + "; ".join(capability.surge_incompatibility_reasons)
        )

    result = deepcopy(config)
    groups = result.get("proxy-groups")
    rules = result.get("rules")
    if not isinstance(groups, list) or not isinstance(rules, list):
        raise TemplatePolicyTransformError("template groups and rules must be lists")

    group_by_name = {
        str(group.get("name")): group
        for group in groups
        if isinstance(group, dict) and group.get("name")
    }
    available = {node.name for node in nodes} | set(group_by_name)
    egress = policy.egress or _default_egress(list(group_by_name))
    if _is_claude(egress):
        raise TemplatePolicyTransformError("Claude egress cannot reference a Claude policy group")
    if egress not in available:
        raise TemplatePolicyTransformError(f"Claude egress not found: {egress}")
    fallback = policy.fallback
    if fallback is not None:
        if _is_claude(fallback):
            raise TemplatePolicyTransformError("Claude fallback cannot reference a Claude policy group")
        if fallback not in available:
            raise TemplatePolicyTransformError(f"Claude fallback not found: {fallback}")

    dedicated_name = capability.dedicated_group
    if dedicated_name:
        group = group_by_name[dedicated_name]
        members = group.get("proxies", [])
        if not isinstance(members, list):
            raise TemplatePolicyTransformError(f"Claude group '{dedicated_name}' proxies must be a list")
        preferred = [egress, *([fallback] if fallback else [])]
        group["proxies"] = list(dict.fromkeys([*preferred, *(str(item) for item in members)]))
        result["rules"] = [
            _retarget_rule(rule, dedicated_name) if isinstance(rule, str) else rule
            for rule in rules
        ]
        return result

    group_name = _unique_group_name("Claude", set(group_by_name))
    original_targets = [fallback] if fallback else list(capability.current_targets)
    groups.append(
        {
            "name": group_name,
            "type": "select",
            "proxies": list(dict.fromkeys([egress, *original_targets])),
        }
    )
    result["rules"] = [
        _retarget_rule(rule, group_name) if isinstance(rule, str) else rule
        for rule in rules
    ]
    return result


def _parse_rule(rule: str) -> list[str]:
    return [part.strip() for part in rule.split(",")]


def _is_claude_rule(parts: list[str]) -> bool:
    if len(parts) < 3:
        return False
    rule_type = parts[0].upper()
    if rule_type == "RULE-SET":
        return _is_claude(parts[1])
    return _is_claude(parts[1])


def _retarget_rule(rule: str, target: str) -> str:
    parts = _parse_rule(rule)
    if not _is_claude_rule(parts):
        return rule
    if parts[2] == target:
        return rule
    parts[2] = target
    return ",".join(parts)


def _is_claude(value: str) -> bool:
    return bool(_CLAUDE_RE.search(value))


def _unique_group_name(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    index = 2
    while f"{base} {index}" in existing:
        index += 1
    return f"{base} {index}"


def _default_egress(group_names: list[str]) -> str:
    for candidate in ("Proxy", "PROXY", "选择代理", "节点选择", "🚀 节点选择"):
        if candidate in group_names:
            return candidate
    non_claude = next((name for name in group_names if not _is_claude(name)), None)
    if non_claude:
        return non_claude
    raise TemplatePolicyTransformError("Claude policy requires a non-Claude proxy group")


def _summarize_names(names: list[str], limit: int = 3) -> str:
    visible = names[:limit]
    remainder = len(names) - len(visible)
    return ", ".join(visible) + (f" +{remainder}" if remainder else "")
