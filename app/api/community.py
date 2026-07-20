"""Community template browser API.

Exposes the contents of community_templates/ so the UI can let users
discover, preview, and apply community-contributed configurations.
"""
from __future__ import annotations

import warnings as py_warnings
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from ruamel.yaml import YAML
from ruamel.yaml.error import ReusedAnchorWarning

router = APIRouter(prefix="/community", tags=["community"])

_APP_DIR = Path(__file__).resolve().parent.parent
_PROJECT_DIR = _APP_DIR.parent
_COMMUNITY_DIR = _PROJECT_DIR / "community_templates"
_COMMUNITY_DIR_RESOLVED = _COMMUNITY_DIR.resolve()
_LEO_RELATIVE_PATH = Path("leo/leo.yaml")
_LEO_PATH = _COMMUNITY_DIR / _LEO_RELATIVE_PATH

# File extensions that are definitely not templates — skip without inspection
_SKIP_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".sh", ".list", ".txt", ".json", ".gitignore", ".lock", ".ini", ".conf",
})

_RULE_SOURCE_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml", ".conf"})
_RULE_PREFIXES = (
    "DOMAIN,",
    "DOMAIN-SUFFIX,",
    "DOMAIN-KEYWORD,",
    "IP-CIDR,",
    "IP-CIDR6,",
    "SRC-IP-CIDR,",
    "GEOIP,",
    "GEOSITE,",
    "RULE-SET,",
    "PROCESS-NAME,",
    "PROCESS-PATH,",
    "DST-PORT,",
    "SRC-PORT,",
    "MATCH,",
    "FINAL,",
)


# ── YAML helpers ───────────────────────────────────────────────────────────


def _load_yaml_safe(path: Path) -> dict | None:
    yaml = YAML(typ="safe")
    try:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore", ReusedAnchorWarning)
            result = yaml.load(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def _string_rules(value: Any) -> list[str]:
    """Keep rule order exactly as authored, while discarding malformed entries."""
    if not isinstance(value, list):
        return []
    return [str(rule).strip() for rule in value if isinstance(rule, str) and rule.strip()]


def _yaml_rules_from_source(path: Path) -> list[str]:
    """Recover a YAML ``rules:`` sequence when a template uses non-YAML extensions.

    Community overwrite files sometimes embed OpenClash placeholders which make
    an otherwise ordinary Mihomo document invalid for a strict YAML parser.
    This deliberately only recognizes the explicit rules list; it never tries
    to reinterpret the surrounding configuration.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    rules: list[str] = []
    rule_indent: int | None = None
    commented_block = False
    for raw_line in lines:
        if rule_indent is None:
            match = re.match(r"^(?P<indent>\s*)(?P<comment>#\s*)?rules:\s*(?:#.*)?$", raw_line)
            if match:
                rule_indent = len(match.group("indent"))
                commented_block = bool(match.group("comment"))
            continue
        if not raw_line.strip():
            continue
        candidate = raw_line
        if commented_block:
            comment = re.match(r"^(?P<indent>\s*)#\s?(?P<body>.*)$", raw_line)
            if comment is None:
                break
            indent = len(comment.group("indent"))
            candidate = comment.group("body")
        else:
            if raw_line.lstrip().startswith("#"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip())
        if not candidate.strip():
            continue
        match = re.match(r"^\s*-\s+(?P<rule>.+?)\s*$", candidate)
        if match:
            rules.append(match.group("rule"))
            continue
        if indent <= rule_indent:
            break
    return rules


def _conf_rules(path: Path) -> list[str]:
    """Extract explicit Surge/OpenClash [Rule] entries without treating settings as rules."""
    rules: list[str] = []
    in_rule_section = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            in_rule_section = line.casefold() in {"[rule]", "[rules]"}
            continue
        if not in_rule_section or not line or line.startswith(("#", ";")):
            continue
        candidate = line.split("#", 1)[0].strip()
        if candidate.upper().startswith(_RULE_PREFIXES):
            rules.append(candidate)
    return rules


def _rule_provider_entries(loaded: dict | None) -> list[dict[str, str]]:
    if not isinstance(loaded, dict) or not isinstance(loaded.get("rule-providers"), dict):
        return []
    providers: list[dict[str, str]] = []
    for name, provider in loaded["rule-providers"].items():
        if not isinstance(provider, dict):
            continue
        providers.append(
            {
                "name": str(name),
                "url": str(provider.get("url") or ""),
                "format": str(provider.get("format") or ""),
                "behavior": str(provider.get("behavior") or ""),
            }
        )
    return providers


def _community_rule_entry(path: Path) -> dict[str, Any] | None:
    """Build one auditable source card for every file that contains explicit rules."""
    suffix = path.suffix.lower()
    if suffix not in _RULE_SOURCE_SUFFIXES:
        return None
    loaded = _load_yaml_safe(path) if suffix in {".yaml", ".yml"} else None
    if suffix in {".yaml", ".yml"}:
        rules = _string_rules(loaded.get("rules")) if loaded is not None else _yaml_rules_from_source(path)
        extraction = "yaml" if loaded is not None else "source"
    else:
        rules = _conf_rules(path)
        extraction = "conf"
    providers = _rule_provider_entries(loaded)
    if not rules:
        return None
    relative = path.relative_to(_COMMUNITY_DIR)
    parts = relative.parts
    collection = " / ".join(parts[:2]) if len(parts) > 1 else parts[0]
    return {
        "id": _template_id(path),
        "label": path.stem,
        "collection": collection,
        "source_path": f"community_templates/{relative.as_posix()}",
        "rules": rules,
        "rule_count": len(rules),
        "unique_rule_count": len(set(rules)),
        "extraction": extraction,
        "providers": providers,
        "provider_count": len(providers),
    }


# ── Format detection ───────────────────────────────────────────────────────


def _detect_format(path: Path, loaded: dict | None) -> str:
    """Classify the template format from path and parsed content."""
    suffix = path.suffix.lower()
    if suffix not in {".yaml", ".yml"}:
        return "unknown"
    if isinstance(loaded, dict) and isinstance(loaded.get("proxy-groups"), list):
        return "yaml"
    return "unknown"


def _has_mrs_providers(loaded: dict) -> bool:
    for provider in (loaded.get("rule-providers") or {}).values():
        if isinstance(provider, dict) and str(provider.get("url", "")).endswith(".mrs"):
            return True
    return False


def _is_surge_compatible(loaded: dict | None, fmt: str) -> bool:
    """True when the template can be compiled for Surge without URL substitution."""
    if fmt != "yaml" or not isinstance(loaded, dict):
        return False
    return not _has_mrs_providers(loaded)


# ── ID ↔ path helpers ──────────────────────────────────────────────────────


def _template_id(path: Path) -> str:
    return "community:" + path.relative_to(_COMMUNITY_DIR).as_posix()


def _path_from_id(template_id: str) -> Path:
    if not template_id.startswith("community:"):
        raise HTTPException(status_code=400, detail="invalid community template id")
    relative = template_id.removeprefix("community:")
    rel_path = Path(relative)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise HTTPException(status_code=400, detail="invalid template path")
    if rel_path != _LEO_RELATIVE_PATH:
        raise HTTPException(status_code=404, detail="template not found")
    path = (_COMMUNITY_DIR / rel_path).resolve()
    if not path.is_relative_to(_COMMUNITY_DIR_RESOLVED):
        raise HTTPException(status_code=400, detail="invalid template path")
    if not path.exists():
        raise HTTPException(status_code=404, detail="template not found")
    return path


# ── Per-file metadata builder ──────────────────────────────────────────────


def _build_meta(path: Path) -> dict[str, Any] | None:
    """Return a metadata dict for *path*, or None to skip this file."""
    if path.name.startswith("."):
        return None
    suffix = path.suffix.lower()
    if suffix in _SKIP_EXTENSIONS or not suffix:
        return None

    loaded: dict | None = None
    if suffix in {".yaml", ".yml"}:
        loaded = _load_yaml_safe(path)

    fmt = _detect_format(path, loaded)
    if fmt == "unknown":
        return None  # silently skip unrecognised files

    groups = loaded.get("proxy-groups") if loaded else None
    rules = loaded.get("rules") if loaded else None
    relative = path.relative_to(_COMMUNITY_DIR)

    return {
        "id": _template_id(path),
        "name": path.stem,
        "format": fmt,
        "proxy_group_count": len(groups) if isinstance(groups, list) else 0,
        "rule_count": len(rules) if isinstance(rules, list) else 0,
        "surge_compatible": _is_surge_compatible(loaded, fmt),
        "source_path": f"community_templates/{relative.as_posix()}",
    }


# ── Endpoints ──────────────────────────────────────────────────────────────


@router.get("/templates")
def list_community_templates() -> list[dict[str, Any]]:
    """Return metadata for the single supported Leo template."""
    if not _LEO_PATH.is_file():
        return []
    meta = _build_meta(_LEO_PATH)
    return [meta] if meta is not None else []


@router.get("/rules")
def list_community_rules() -> dict[str, Any]:
    """Expose the normalized rules and providers from leo.yaml."""
    if not _LEO_PATH.is_file():
        return {
            "summary": {"files_scanned": 0, "template_count": 0, "rule_count": 0, "unique_rule_count": 0, "provider_count": 0},
            "templates": [],
        }
    files = [_LEO_PATH]
    templates = [entry for path in files if (entry := _community_rule_entry(path)) is not None]
    all_rules = [rule for template in templates for rule in template["rules"]]
    return {
        "summary": {
            "files_scanned": len(files),
            "template_count": len(templates),
            "rule_count": len(all_rules),
            "unique_rule_count": len(set(all_rules)),
            "provider_count": sum(template["provider_count"] for template in templates),
        },
        "templates": templates,
    }


@router.get("/templates/preview")
def preview_community_template(
    id: str = Query(..., description="Community template id (community:path/to/file.yaml)"),
) -> dict[str, Any]:
    """Return the full proxy-group structure of a single community template."""
    path = _path_from_id(id)
    loaded = _load_yaml_safe(path)
    fmt = _detect_format(path, loaded)

    if fmt != "yaml":
        raise HTTPException(
            status_code=422,
            detail=f"preview not available for format '{fmt}' — only 'yaml' templates are supported",
        )
    if not isinstance(loaded, dict):
        raise HTTPException(status_code=422, detail="template could not be parsed")

    groups_raw = loaded.get("proxy-groups") or []
    proxy_groups: list[dict[str, Any]] = []
    for g in groups_raw:
        if not isinstance(g, dict) or not g.get("name"):
            continue
        members = [str(m) for m in (g.get("proxies") or []) if m is not None]
        proxy_groups.append({
            "name": str(g["name"]),
            "type": str(g.get("type") or "select"),
            "members": members,
        })

    rules = loaded.get("rules") or []

    return {
        "id": id,
        "format": fmt,
        "proxy_groups": proxy_groups,
        "rule_count": len(rules) if isinstance(rules, list) else 0,
        "surge_compatible": _is_surge_compatible(loaded, fmt),
    }
