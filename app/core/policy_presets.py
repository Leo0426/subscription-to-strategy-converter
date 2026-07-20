from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.core.template_engine import LEO_TEMPLATE_ID, PRESET_TEMPLATES


BASE_TEMPLATE = {
    "id": LEO_TEMPLATE_ID,
    "label": "Leo 大而全模板",
}

_PRESET_DEFINITIONS = (
    ("general", "通用代理", "适合日常使用的自动选择、故障转移和国内直连策略。", "minimal"),
    ("ai", "AI / Claude", "为 Claude、OpenAI、Gemini 等 AI 服务提供独立路由。", "ai-tools"),
    ("streaming", "流媒体", "为 Netflix、YouTube、Disney+、Spotify 等服务独立分流。", "streaming"),
    ("developer", "开发者", "为 GitHub、Docker、npm、Microsoft 等开发服务独立分流。", "developer"),
    ("blank", "空白策略", "只保留最小可发布策略，适合从头编排。", None),
)


def _selected_policy(template_id: str | None) -> dict[str, Any]:
    if template_id is None:
        groups = [
            {
                "name": "Proxy",
                "type": "select",
                "proxies": ["__ALL_NODES__", "DIRECT"],
            }
        ]
        rules = ["MATCH,Proxy"]
        providers: dict[str, Any] = {}
    else:
        config = PRESET_TEMPLATES[template_id]["config"]
        groups = deepcopy(config.get("proxy-groups", []))
        rules = deepcopy(config.get("rules", []))
        providers = deepcopy(config.get("rule-providers", {}))
    return {
        "mode": "merge",
        "node_selectors": [],
        "proxy_groups": groups,
        "rule_providers": providers,
        "rules": rules,
    }


def list_policy_presets() -> dict[str, Any]:
    return {
        "base_template": dict(BASE_TEMPLATE),
        "presets": [
            {
                "id": preset_id,
                "label": label,
                "description": description,
                "selected_policy": _selected_policy(template_id),
            }
            for preset_id, label, description, template_id in _PRESET_DEFINITIONS
        ],
    }


def get_policy_preset(preset_id: str) -> dict[str, Any] | None:
    return next(
        (preset for preset in list_policy_presets()["presets"] if preset["id"] == preset_id),
        None,
    )
