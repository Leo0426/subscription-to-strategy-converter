from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
import warnings
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import ReusedAnchorWarning

from app.core.parsers.clash import ir_to_clash_dict
from app.core.powerfullz import PowerfullzTemplateError, load_powerfullz_template
from app.ir import ProxyNode
from app.models.powerfullz import PowerfullzOptions
from app.models.strategy import CustomStrategy, SelectedPolicy


class TemplateError(ValueError):
    pass


_APP_DIR = Path(__file__).resolve().parent.parent
_PROJECT_DIR = _APP_DIR.parent

LOCAL_TEMPLATE_ROOTS = (
    _PROJECT_DIR / "community_templates",
)
DIRECT_NODE_GROUP_NAMES = {
    "Proxy",
    "手动选择",
    "手动切换",
    "全球手动",
    "全部节点",
    "节点选择",
    "🚀 手动切换",
    "🚀 节点选择",
}
DIRECT_NODE_GROUP_KEYWORDS = ("手动", "全部节点", "节点选择")

_RESOLVED_LOCAL_TEMPLATE_ROOTS = tuple(r.resolve() for r in LOCAL_TEMPLATE_ROOTS)


def _base_template(groups: list[dict], rules: list[str], rule_providers: dict | None = None) -> dict:
    return {
        "mixed-port": 7890,
        "allow-lan": True,
        "mode": "rule",
        "log-level": "info",
        "dns": {
            "enable": True,
            "enhanced-mode": "fake-ip",
            "nameserver": ["https://dns.alidns.com/dns-query", "https://doh.pub/dns-query"],
            "fallback": ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query"],
        },
        "tun": {
            "enable": True,
            "stack": "mixed",
            "auto-route": True,
            "auto-detect-interface": True,
        },
        "proxy-groups": groups,
        "rule-providers": rule_providers or {},
        "rules": rules,
    }


def _core_groups(extra: list[dict] | None = None) -> list[dict]:
    groups = [
        {"name": "Proxy", "type": "select", "proxies": ["Auto", "Fallback", "DIRECT"]},
        {
            "name": "Auto",
            "type": "url-test",
            "include-all": True,
            "url": "https://www.gstatic.com/generate_204",
            "interval": 300,
        },
        {
            "name": "Fallback",
            "type": "fallback",
            "include-all": True,
            "url": "https://www.gstatic.com/generate_204",
            "interval": 300,
        },
        {"name": "Global", "type": "select", "proxies": ["Proxy", "Auto", "Fallback", "DIRECT"]},
        {"name": "China", "type": "select", "proxies": ["DIRECT", "Proxy"]},
        {"name": "Reject", "type": "select", "proxies": ["REJECT", "DIRECT"]},
    ]
    if extra:
        groups.extend(extra)
    return groups


def _ai_groups() -> list[dict]:
    return [
        {"name": "AI", "type": "select", "proxies": ["Proxy", "Auto", "Fallback"]},
        {"name": "Claude", "type": "select", "proxies": ["AI", "Proxy", "Auto"]},
        {"name": "OpenAI", "type": "select", "proxies": ["AI", "Proxy", "Auto"]},
        {"name": "Gemini", "type": "select", "proxies": ["AI", "Proxy", "Auto"]},
        {"name": "Perplexity", "type": "select", "proxies": ["AI", "Proxy", "Auto"]},
        {"name": "Cursor", "type": "select", "proxies": ["AI", "Proxy", "Auto"]},
        {"name": "GitHub Copilot", "type": "select", "proxies": ["AI", "Proxy", "Auto"]},
    ]


def _developer_groups() -> list[dict]:
    return [
        {"name": "Developer", "type": "select", "proxies": ["Proxy", "Auto", "Fallback"]},
        {"name": "GitHub", "type": "select", "proxies": ["Developer", "Proxy", "Auto"]},
        {"name": "Microsoft", "type": "select", "proxies": ["Proxy", "Auto", "DIRECT"]},
        {"name": "Apple", "type": "select", "proxies": ["DIRECT", "Proxy"]},
    ]


def _streaming_groups() -> list[dict]:
    return [
        {"name": "Streaming", "type": "select", "proxies": ["Proxy", "Auto", "Fallback"]},
        {"name": "Netflix", "type": "select", "proxies": ["Streaming", "Proxy", "Auto"]},
        {"name": "YouTube", "type": "select", "proxies": ["Streaming", "Proxy", "Auto"]},
        {"name": "Disney", "type": "select", "proxies": ["Streaming", "Proxy", "Auto"]},
        {"name": "Spotify", "type": "select", "proxies": ["Streaming", "Proxy", "Auto"]},
        {"name": "Telegram", "type": "select", "proxies": ["Proxy", "Auto", "Fallback"]},
    ]


AI_RULES = [
    "DOMAIN-SUFFIX,anthropic.com,Claude",
    "DOMAIN-SUFFIX,claude.ai,Claude",
    "DOMAIN-SUFFIX,openai.com,OpenAI",
    "DOMAIN-SUFFIX,chatgpt.com,OpenAI",
    "DOMAIN-SUFFIX,oaistatic.com,OpenAI",
    "DOMAIN-SUFFIX,oaiusercontent.com,OpenAI",
    "DOMAIN-SUFFIX,generativeai.google,Gemini",
    "DOMAIN-SUFFIX,generativelanguage.googleapis.com,Gemini",
    "DOMAIN-SUFFIX,perplexity.ai,Perplexity",
    "DOMAIN-SUFFIX,cursor.com,Cursor",
    "DOMAIN-SUFFIX,cursor.sh,Cursor",
    "DOMAIN-SUFFIX,githubcopilot.com,GitHub Copilot",
]

DEV_RULES = [
    "DOMAIN-SUFFIX,github.com,GitHub",
    "DOMAIN-SUFFIX,githubusercontent.com,GitHub",
    "DOMAIN-SUFFIX,npmjs.com,Developer",
    "DOMAIN-SUFFIX,docker.com,Developer",
    "DOMAIN-SUFFIX,docker.io,Developer",
    "DOMAIN-SUFFIX,jetbrains.com,Developer",
    "DOMAIN-SUFFIX,sdkman.io,Developer",
    "DOMAIN-SUFFIX,visualstudio.com,Microsoft",
    "DOMAIN-SUFFIX,microsoft.com,Microsoft",
    "DOMAIN-SUFFIX,apple.com,Apple",
]

STREAMING_RULES = [
    "DOMAIN-SUFFIX,netflix.com,Netflix",
    "DOMAIN-SUFFIX,nflxvideo.net,Netflix",
    "DOMAIN-SUFFIX,youtube.com,YouTube",
    "DOMAIN-SUFFIX,googlevideo.com,YouTube",
    "DOMAIN-SUFFIX,disneyplus.com,Disney",
    "DOMAIN-SUFFIX,spotify.com,Spotify",
    "DOMAIN-SUFFIX,t.me,Telegram",
    "DOMAIN-SUFFIX,telegram.org,Telegram",
]

COMMON_RULES = [
    "DOMAIN-SUFFIX,local,DIRECT",
    "DOMAIN-SUFFIX,cn,China",
    "GEOIP,CN,China",
    "MATCH,Proxy",
]


PRESET_TEMPLATES: dict[str, dict[str, Any]] = {
    "minimal": {
        "label": "Minimal",
        "description": "最小策略：Proxy / Auto / Fallback / DIRECT / REJECT。",
        "config": _base_template(_core_groups(), COMMON_RULES),
    },
    "developer": {
        "label": "Developer",
        "description": "开发者策略：GitHub、npm、Docker、JetBrains、Microsoft 独立分流。",
        "config": _base_template(_core_groups(_developer_groups()), DEV_RULES + COMMON_RULES),
    },
    "ai-tools": {
        "label": "AI Tools",
        "description": "AI 工具策略：Claude、OpenAI、Gemini、Perplexity、Cursor、GitHub Copilot 独立分流。",
        "config": _base_template(_core_groups(_ai_groups() + [{"name": "GitHub", "type": "select", "proxies": ["GitHub Copilot", "Proxy", "Auto"]}]), AI_RULES + DEV_RULES[:2] + COMMON_RULES),
    },
    "streaming": {
        "label": "Streaming",
        "description": "流媒体策略：Netflix、YouTube、Disney、Spotify、Telegram 独立分流。",
        "config": _base_template(_core_groups(_streaming_groups()), STREAMING_RULES + COMMON_RULES),
    },
    "full": {
        "label": "Full",
        "description": "全量策略：AI + Developer + Streaming + 地区自动筛选 + DNS/TUN。",
        "config": _base_template(
            _core_groups(
                _ai_groups()
                + _developer_groups()
                + _streaming_groups()
                + [
                    {"name": "HK", "type": "url-test", "include-all": True, "filter": "香港|HK|Hong", "url": "https://www.gstatic.com/generate_204", "interval": 300},
                    {"name": "SG", "type": "url-test", "include-all": True, "filter": "新加坡|SG|Singapore", "url": "https://www.gstatic.com/generate_204", "interval": 300},
                    {"name": "JP", "type": "url-test", "include-all": True, "filter": "日本|JP|Japan", "url": "https://www.gstatic.com/generate_204", "interval": 300},
                    {"name": "US", "type": "url-test", "include-all": True, "filter": "美国|US|United States", "url": "https://www.gstatic.com/generate_204", "interval": 300},
                ]
            ),
            AI_RULES + DEV_RULES + STREAMING_RULES + COMMON_RULES,
        ),
    },
}


def _load_yaml_file(path: Path, template_name: str) -> dict:
    yaml = YAML(typ="safe")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ReusedAnchorWarning)
            loaded = yaml.load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TemplateError(f"failed to load template: {template_name}") from exc

    if not isinstance(loaded, dict):
        raise TemplateError(f"template must be a YAML object: {template_name}")
    return loaded


def _local_template_path(template_id: str) -> Path:
    relative_name = template_id.removeprefix("local:")
    if not relative_name:
        raise TemplateError("invalid local template name")

    relative_path = Path(relative_name)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise TemplateError("invalid local template path")
    if relative_path.suffix.lower() not in {".yaml", ".yml"}:
        raise TemplateError("local template must be a YAML file")

    path = (_PROJECT_DIR / relative_path).resolve()
    if not any(path.is_relative_to(root) for root in _RESOLVED_LOCAL_TEMPLATE_ROOTS):
        raise TemplateError("local template path is outside allowed template roots")
    if not path.exists():
        raise TemplateError(f"template not found: {template_id}")
    return path


def load_template(name: str) -> dict:
    if name in PRESET_TEMPLATES:
        return deepcopy(PRESET_TEMPLATES[name]["config"])
    if name.startswith("local:"):
        return _load_yaml_file(_local_template_path(name), name)
    raise TemplateError(f"template not found: {name}")


def _template_summary(
    template_id: str,
    label: str,
    source: str,
    path: str | None = None,
    description: str = "",
    proxy_group_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": template_id,
        "label": label,
        "source": source,
        "path": path,
        "description": description,
        "proxy_group_count": proxy_group_count,
    }


def _local_label(path: Path) -> str:
    relative = path.relative_to(_PROJECT_DIR)
    return str(relative.with_suffix(""))


def _local_template_meta(path: Path) -> dict | None:
    """Load a local template and return its metadata, or None if unsupported."""
    try:
        loaded = _load_yaml_file(path, str(path))
    except TemplateError:
        return None
    groups = loaded.get("proxy-groups")
    if not isinstance(groups, list):
        return None
    return {"proxy_group_count": len(groups)}


@lru_cache(maxsize=1)
def list_templates() -> list[dict[str, Any]]:
    templates = [
        _template_summary(
            template_id,
            preset["label"],
            "preset",
            description=preset["description"],
            proxy_group_count=len(preset["config"].get("proxy-groups", [])),
        )
        for template_id, preset in PRESET_TEMPLATES.items()
    ]
    templates.append(
        _template_summary(
            "powerfullz",
            "powerfullz override",
            "built-in",
            description="基于 powerfullz/override-rules 静态 YAML 覆写，支持按需开关负载均衡、IPv6、Fake-IP 等组件。",
        )
    )

    for root in LOCAL_TEMPLATE_ROOTS:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            meta = _local_template_meta(path)
            if meta is None:
                continue
            relative = path.relative_to(_PROJECT_DIR)
            templates.append(
                _template_summary(
                    f"local:{relative.as_posix()}",
                    _local_label(path),
                    "local",
                    relative.as_posix(),
                    proxy_group_count=meta["proxy_group_count"],
                )
            )
    return templates


def _expand_members(members: list[str], node_names: list[str]) -> list[str]:
    if not members:
        return list(node_names)

    expanded: list[str] = []
    for member in members:
        if member == "__ALL_NODES__":
            expanded.extend(node_names)
        else:
            expanded.append(member)
    return list(dict.fromkeys(expanded))


def _upsert_group(groups: list[dict], group: dict) -> None:
    for index, existing in enumerate(groups):
        if isinstance(existing, dict) and existing.get("name") == group.get("name"):
            groups[index] = group
            return
    groups.append(group)


def _apply_custom_strategy(config: dict, node_names: list[str], custom_strategy: CustomStrategy) -> None:
    groups = config["proxy-groups"]
    custom_group_names: list[str] = []

    for custom_group in custom_strategy.proxy_groups:
        group = {
            "name": custom_group.name,
            "type": custom_group.type,
        }
        if custom_group.include_all:
            group["include-all"] = True
            if custom_group.filter:
                group["filter"] = custom_group.filter
            if custom_group.exclude_filter:
                group["exclude-filter"] = custom_group.exclude_filter
        else:
            group["proxies"] = _expand_members(custom_group.proxies, node_names)

        if custom_group.type in {"url-test", "fallback", "load-balance"}:
            group["url"] = custom_group.url or "http://www.gstatic.com/generate_204"
            group["interval"] = custom_group.interval or 300

        _upsert_group(groups, group)
        custom_group_names.append(custom_group.name)

    main_group_names = {"PROXY", "选择代理", "默认代理", "🚀 节点选择", "节点选择"}
    for group in groups:
        if not isinstance(group, dict):
            continue
        if group.get("name") in main_group_names and group.get("type") == "select":
            existing = [str(item) for item in group.get("proxies", [])]
            group["proxies"] = list(dict.fromkeys(custom_group_names + existing))


def _expand_selected_group(group: dict, node_names: list[str]) -> dict:
    expanded = deepcopy(group)
    proxies = expanded.get("proxies")
    if isinstance(proxies, list):
        expanded["proxies"] = _expand_members([str(item) for item in proxies], node_names)
    return expanded


def _apply_selected_policy(config: dict, selected_policy: SelectedPolicy, node_names: list[str]) -> None:
    groups = config["proxy-groups"]
    for group in selected_policy.proxy_groups:
        if isinstance(group, dict) and group.get("name"):
            _upsert_group(groups, _expand_selected_group(group, node_names))

    if selected_policy.rule_providers:
        providers = config.setdefault("rule-providers", {})
        if not isinstance(providers, dict):
            raise TemplateError("template rule-providers must be a mapping")
        for name, provider in selected_policy.rule_providers.items():
            providers[str(name)] = deepcopy(provider)

    if selected_policy.rules:
        rules = config.setdefault("rules", [])
        if not isinstance(rules, list):
            raise TemplateError("template rules must be a list")
        existing_rule_keys = {_rule_key(rule) for rule in rules}
        new_rules = []
        for rule in selected_policy.rules:
            key = _rule_key(rule)
            if key in existing_rule_keys:
                continue
            existing_rule_keys.add(key)
            new_rules.append(deepcopy(rule))
        rules[0:0] = new_rules


def _rule_key(rule: object) -> str:
    """Canonical key for rule deduplication.

    Normalizes whitespace in string rules and sorts dict keys so that
    semantically identical rules expressed with different formatting or
    key ordering compare as equal.
    """
    if isinstance(rule, str):
        return ",".join(part.strip() for part in rule.split(","))
    if isinstance(rule, dict):
        return json.dumps(rule, sort_keys=True, ensure_ascii=False)
    return repr(rule)


def _should_fill_group(group: dict) -> bool:
    name = str(group.get("name", ""))
    if name in DIRECT_NODE_GROUP_NAMES:
        return True
    return any(keyword in name for keyword in DIRECT_NODE_GROUP_KEYWORDS)


def _fill_node_groups(groups: list[dict], node_names: list[str]) -> None:
    for group in groups:
        if not isinstance(group, dict):
            continue
        if group.get("name") in {"PROXY", "AUTO"}:
            group["proxies"] = list(node_names)
            continue
        if _should_fill_group(group):
            existing = [str(item) for item in group.get("proxies", [])]
            group["proxies"] = list(dict.fromkeys(node_names + existing))


def apply_template(
    template: dict,
    nodes: list[ProxyNode],
    custom_strategy: CustomStrategy | None = None,
    selected_policy: SelectedPolicy | None = None,
) -> dict:
    config = deepcopy(template)
    node_names = [node.name for node in nodes]
    config["proxies"] = [ir_to_clash_dict(node) for node in nodes]

    groups = config.get("proxy-groups")
    if not isinstance(groups, list):
        raise TemplateError("template must contain proxy-groups")

    _fill_node_groups(groups, node_names)

    if custom_strategy is not None:
        _apply_custom_strategy(config, node_names, custom_strategy)

    if selected_policy is not None:
        _apply_selected_policy(config, selected_policy, node_names)

    return config


async def load_any_template(name: str, options: PowerfullzOptions | None = None) -> dict:
    if name == "powerfullz":
        try:
            return await load_powerfullz_template(options or PowerfullzOptions())
        except PowerfullzTemplateError as exc:
            raise TemplateError(str(exc)) from exc
    return load_template(name)
