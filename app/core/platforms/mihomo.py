"""Combine generated routing with a native Mihomo connectivity envelope."""
from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
import posixpath
import re
from typing import Any

from app.core.normalizer import normalize_nodes_with_source_names
from app.core.parsers.clash import clash_to_ir, ir_to_clash_dict
from app.core.platforms.mihomo_dependencies import has_opaque_proxy_payload, prune_native_policy
from app.core.policy_workspace import compile_mihomo_config
from app.ir import BUILTIN_POLICY_TARGETS, ProxyNode


_INTERNAL_SETTINGS = {"_surge_source", "source-format", "native_source_format"}
_PROVIDER_REFERENCE = re.compile(r"(^|\()(\s*RULE-SET\s*,\s*)([^,()]+)", re.IGNORECASE)


class NativeMihomoProfileError(ValueError):
    """Native references cannot be preserved without changing their meaning."""


def _validate_source_structure(source: dict[str, Any]) -> None:
    """Check the shapes this merger reads; never echo untrusted source values."""
    if not isinstance(source, dict):
        raise NativeMihomoProfileError("机场原生配置必须为对象")
    for section in ("proxies", "proxy-groups"):
        entries = source.get(section, [])
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise NativeMihomoProfileError(f"机场配置的 {section} 必须为对象数组")
    for group in source.get("proxy-groups", []):
        for field in ("proxies", "use"):
            members = group.get(field, [])
            if not isinstance(members, list) or any(not isinstance(member, str) for member in members):
                raise NativeMihomoProfileError("机场策略组的 proxies/use 必须为名称数组")
    for section in ("rule-providers", "proxy-providers"):
        providers = source.get(section, {})
        if (not isinstance(providers, dict)
                or any(not isinstance(name, str) or not name or not isinstance(provider, dict)
                       for name, provider in providers.items())):
            raise NativeMihomoProfileError(f"机场配置的 {section} 必须为名称到对象的映射")
        for provider in providers.values():
            if any(field in provider and not isinstance(provider[field], str) for field in ("path", "proxy")):
                raise NativeMihomoProfileError("机场提供器的 path/proxy 必须为字符串")
    subrules = source.get("sub-rules", {})
    if (not isinstance(subrules, dict)
            or any(not isinstance(name, str) or not isinstance(rules, list)
                   for name, rules in subrules.items())):
        raise NativeMihomoProfileError("机场配置的 sub-rules 必须为名称到规则数组的映射")
    if source.get("source-format") is not None and not isinstance(source["source-format"], str):
        raise NativeMihomoProfileError("机场来源格式标识无效")


def _renames(generated: set[str], native: set[str]) -> dict[str, str]:
    used = generated | native
    renamed: dict[str, str] = {}
    for name in sorted(generated & native):
        candidate = f"Subflow {name}"
        suffix = 2
        while candidate in used:
            candidate = f"Subflow {name} {suffix}"
            suffix += 1
        renamed[name] = candidate
        used.add(candidate)
    return renamed


def _definition_names(entries: list[dict]) -> set[str]:
    names = [entry.get("name") for entry in entries if isinstance(entry, dict)]
    if (len(names) != len(entries) or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)):
        raise NativeMihomoProfileError("机场节点或策略组存在名称冲突或无效名称，无法无损保留连接引用")
    return set(names)


def _provider_paths(config: dict[str, Any]) -> set[str]:
    return {posixpath.normpath(provider["path"])
            for section in ("proxy-providers", "rule-providers")
            for provider in (config.get(section) or {}).values()
            if isinstance(provider, dict) and isinstance(provider.get("path"), str)}


def _merge_providers(
    source: dict[str, Any], generated: dict[str, Any], names: dict[str, str],
    target_names: dict[str, str], native_paths: set[str], used_paths: set[str],
) -> dict[str, Any]:
    merged = dict(source)
    for name, provider in generated.items():
        provider = deepcopy(provider)
        if "proxy" in provider:
            provider["proxy"] = target_names.get(provider["proxy"], provider["proxy"])
        override = provider.get("override")
        if isinstance(override, dict) and "dialer-proxy" in override:
            override["dialer-proxy"] = target_names.get(override["dialer-proxy"], override["dialer-proxy"])
        path = provider.get("path")
        if isinstance(path, str) and posixpath.normpath(path) in native_paths:
            if provider.get("type") != "http":
                raise NativeMihomoProfileError("生成规则提供器与机场配置共用本地文件，无法无损区分；请调整提供器路径")
            original = PurePosixPath(path)
            candidate = original.with_name(f"subflow-{original.name}")
            suffix = 2
            while posixpath.normpath(str(candidate)) in used_paths:
                candidate = original.with_name(f"subflow-{suffix}-{original.name}")
                suffix += 1
            provider["path"] = str(candidate)
            used_paths.add(posixpath.normpath(str(candidate)))
        merged[names.get(name, name)] = provider
    return merged


def _rewrite_rule(rule: Any, targets: dict[str, str], providers: dict[str, str]) -> Any:
    if isinstance(rule, dict):
        rewritten = deepcopy(rule)
        for key in ("proxy", "policy", "target"):
            if isinstance(rewritten.get(key), str):
                rewritten[key] = targets.get(rewritten[key], rewritten[key])
        if str(rule.get("type") or rule.get("rule") or "").upper() == "RULE-SET":
            for key in ("rule-set", "provider", "match", "value"):
                if isinstance(rewritten.get(key), str):
                    rewritten[key] = providers.get(rewritten[key], rewritten[key])
        return rewritten
    if not isinstance(rule, str):
        raise NativeMihomoProfileError("生成分流规则格式无效，无法保留原生引用")
    rule = _PROVIDER_REFERENCE.sub(
        lambda match: match[1] + match[2] + providers.get(match[3].strip(), match[3]), rule,
    )
    parts = rule.split(",")
    position = -2 if parts[-1].strip().lower() == "no-resolve" else -1
    parts[position] = targets.get(parts[position].strip(), parts[position].strip())
    return ",".join(parts)


def build_mihomo_config(
    nodes: list[ProxyNode],
    generated_config: dict[str, Any],
    source_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict]]:
    """Compile policy while preserving the native source's common settings.

    Source groups, providers and aliases remain available to DNS and dialer
    references. Generated references alone are renamed when namespaces overlap.
    """
    if source_config is not None:
        _validate_source_structure(source_config)
    compiled = compile_mihomo_config(deepcopy(generated_config), nodes)
    if source_config is None:
        return {key: value for key, value in compiled.items() if key not in _INTERNAL_SETTINGS}, []

    warnings = [{
        "code": "cross_format_source_settings", "target": "mihomo",
        "suggestion": "跨格式订阅只能保留已转换的连接设置，未添加模板通用设置；需完整保留时请提供 Clash/Mihomo 订阅",
    }] if source_config.get("source-format") in {"surge", "subconverter"} else []

    result = deepcopy({key: value for key, value in source_config.items() if key not in _INTERNAL_SETTINGS})
    raw_nodes = result.get("proxies", [])
    raw_names = _definition_names(raw_nodes)
    native_groups = result.get("proxy-groups") or []
    native_group_names = _definition_names(native_groups)
    generated_groups = compiled["proxy-groups"]
    generated_names = _definition_names(generated_groups)
    try:
        source_nodes = [clash_to_ir(proxy) for proxy in raw_nodes]
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise NativeMihomoProfileError("机场原生节点字段格式无效，无法保留连接引用") from None
    normalized = normalize_nodes_with_source_names(source_nodes)
    node_names = {node.name: original for node, original in normalized}
    if (len(node_names) != len(normalized) or node_names.keys() & (generated_names | BUILTIN_POLICY_TARGETS)
            or raw_names & native_group_names):
        raise NativeMihomoProfileError("机场节点或生成策略组存在名称冲突，无法无损区分连接与分流引用；请调整节点或策略组名称")
    if any(node.name not in node_names for node in nodes):
        raise NativeMihomoProfileError("生成策略引用的节点不在机场原生配置中，无法保留连接引用；请重新加载订阅")
    # Native nodes are already authored for this client. Compatibility repairs
    # belong exclusively to actual cross-format inputs, never to native nodes.
    if source_config.get("source-format") in {"surge", "subconverter"}:
        for raw, parsed in zip(raw_nodes, source_nodes):
            if raw.get("plugin") == "obfs" and isinstance(raw.get("plugin-opts"), dict):
                adapted = ir_to_clash_dict(parsed)
                raw["plugin-opts"] = deepcopy(adapted["plugin-opts"])

    # Drop obsolete airport routing before resolving names/paths, so dead
    # definitions neither clutter clients nor force unnecessary renames.
    result = prune_native_policy(result, compiled)
    if ((result.get("proxy-groups") or result.get("sub-rules"))
            and any(has_opaque_proxy_payload(provider) for provider in result.get("proxy-providers", {}).values())):
        warnings.append({"code": "opaque_proxy_provider_dependencies", "field": "proxy-providers",
                         "suggestion": "仍使用外部或动态改写的节点集合，已保留其节点可能依赖的机场策略组和子规则，避免清理后断开连接"})
    native_groups = result.get("proxy-groups") or []
    native_group_names = _definition_names(native_groups)
    group_names = _renames(generated_names, raw_names | native_group_names)
    target_names = {**node_names, **group_names}
    rule_provider_names = _renames(set(compiled["rule-providers"]), set(result.get("rule-providers") or {}))
    proxy_provider_names = _renames(set(compiled.get("proxy-providers") or {}), set(result.get("proxy-providers") or {}))

    def target(name: str) -> str:
        return target_names.get(name, name)

    for group in generated_groups:
        group["name"] = group_names.get(group["name"], group["name"])
        group["proxies"] = [target(member) for member in group.get("proxies", [])]
        if "use" in group:
            group["use"] = [proxy_provider_names.get(name, name) for name in group["use"]]
    result["proxy-groups"] = native_groups + generated_groups
    native_paths = _provider_paths(result)
    used_paths = native_paths | _provider_paths(compiled)
    for section, names in (("rule-providers", rule_provider_names), ("proxy-providers", proxy_provider_names)):
        if section in result or section in compiled:
            result[section] = _merge_providers(
                result.get(section) or {}, compiled.get(section) or {}, names,
                target_names, native_paths, used_paths,
            )
    result["rules"] = [_rewrite_rule(rule, target_names, rule_provider_names) for rule in compiled["rules"]]
    if str(result.get("mode", "")).lower() in {"global", "direct"}:
        result["mode"] = "rule"
        warnings.append({"code": "source_mode_changed", "field": "mode", "value": "rule",
                         "suggestion": "机场配置使用全局或直连模式；已切换为规则模式以应用所选分流策略"})
    return result, warnings
