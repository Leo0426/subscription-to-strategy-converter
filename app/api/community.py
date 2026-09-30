"""Read-only Leo template metadata, groups and rule-source ledger."""
from __future__ import annotations

import warnings as py_warnings
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from ruamel.yaml import YAML
from ruamel.yaml.error import ReusedAnchorWarning

from app.core.platforms.surge import build_surge_config

router = APIRouter(prefix="/community", tags=["community"])

_COMMUNITY_DIR = Path(__file__).resolve().parents[2] / "community_templates"
_COMMUNITY_DIR_RESOLVED = _COMMUNITY_DIR.resolve()
_LEO_RELATIVE_PATH = Path("leo/leo.yaml")
_LEO_PATH = _COMMUNITY_DIR / _LEO_RELATIVE_PATH
_LEO_ID = "community:leo/leo.yaml"
_LEO_SOURCE_PATH = "community_templates/leo/leo.yaml"


def _load_yaml_safe(path: Path) -> dict | None:
    yaml = YAML(typ="safe")
    try:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore", ReusedAnchorWarning)
            result = yaml.load(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _is_surge_compatible(loaded: dict | None) -> bool:
    """True when Surge compilation does not have to drop any policy rules."""
    if not isinstance(loaded, dict):
        return False
    if any(isinstance(provider, dict) and str(provider.get("url", "")).endswith(".mrs")
           for provider in (loaded.get("rule-providers") or {}).values()):
        return False
    try:
        _, warnings = build_surge_config(
            [], loaded.get("proxy-groups") or [], loaded.get("rules") or [],
            loaded.get("rule-providers") or {},
        )
    except (TypeError, ValueError, KeyError):
        return False
    return not warnings


def _path_from_id(template_id: str) -> Path:
    if not template_id.startswith("community:"):
        raise HTTPException(status_code=400, detail="invalid community template id")
    relative = Path(template_id.removeprefix("community:"))
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="invalid template path")
    if relative != _LEO_RELATIVE_PATH:
        raise HTTPException(status_code=404, detail="template not found")
    path = _LEO_PATH.resolve()
    if not path.is_relative_to(_COMMUNITY_DIR_RESOLVED):
        raise HTTPException(status_code=400, detail="invalid template path")
    if not path.exists():
        raise HTTPException(status_code=404, detail="template not found")
    return path


@router.get("/templates")
def list_community_templates() -> list[dict[str, Any]]:
    loaded = _load_yaml_safe(_LEO_PATH)
    if loaded is None or not isinstance(loaded.get("proxy-groups"), list):
        return []
    rules = loaded.get("rules")
    return [{
        "id": _LEO_ID,
        "name": "leo",
        "format": "yaml",
        "proxy_group_count": len(loaded["proxy-groups"]),
        "rule_count": len(rules) if isinstance(rules, list) else 0,
        "surge_compatible": _is_surge_compatible(loaded),
        "source_path": _LEO_SOURCE_PATH,
    }]


@router.get("/rules")
def list_community_rules() -> dict[str, Any]:
    """Expose only the supported Leo document; preserve authored rule order."""
    loaded = _load_yaml_safe(_LEO_PATH)
    raw_rules = loaded.get("rules") if loaded else None
    rules = [rule.strip() for rule in raw_rules if isinstance(rule, str) and rule.strip()] if isinstance(raw_rules, list) else []
    providers = []
    raw_providers = loaded.get("rule-providers") if loaded else None
    if isinstance(raw_providers, dict):
        providers = [{
            "name": str(name), "url": str(provider.get("url") or ""),
            "format": str(provider.get("format") or ""),
            "behavior": str(provider.get("behavior") or ""),
        } for name, provider in raw_providers.items() if isinstance(provider, dict)]
    templates = [{
        "id": _LEO_ID, "label": "leo", "collection": "leo / leo.yaml",
        "source_path": _LEO_SOURCE_PATH, "rules": rules,
        "rule_count": len(rules), "unique_rule_count": len(set(rules)),
        "extraction": "yaml", "providers": providers, "provider_count": len(providers),
    }] if rules else []
    return {
        "summary": {
            "files_scanned": int(_LEO_PATH.is_file()),
            "template_count": len(templates),
            "rule_count": len(rules), "unique_rule_count": len(set(rules)),
            "provider_count": len(providers) if templates else 0,
        },
        "templates": templates,
    }


@router.get("/templates/preview")
def preview_community_template(
    id: str = Query(..., description="Leo template id (community:leo/leo.yaml)"),
) -> dict[str, Any]:
    loaded = _load_yaml_safe(_path_from_id(id))
    if loaded is None or not isinstance(loaded.get("proxy-groups"), list):
        raise HTTPException(status_code=422, detail="preview not available for format 'unknown' — only 'yaml' templates are supported")
    groups = [{
        "name": str(group["name"]), "type": str(group.get("type") or "select"),
        "members": [str(member) for member in (group.get("proxies") or []) if member is not None],
    } for group in loaded["proxy-groups"] if isinstance(group, dict) and group.get("name")]
    rules = loaded.get("rules")
    return {
        "id": id, "format": "yaml", "proxy_groups": groups,
        "rule_count": len(rules) if isinstance(rules, list) else 0,
        "surge_compatible": _is_surge_compatible(loaded),
    }
