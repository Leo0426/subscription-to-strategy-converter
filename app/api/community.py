"""Community template browser API.

Exposes the contents of community_templates/ so the UI can let users
discover, preview, and apply community-contributed configurations.
"""
from __future__ import annotations

import warnings as py_warnings
from pathlib import Path
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

# Directory name fragments that identify OpenClash-specific directories
_OPENCLASH_DIR_MARKERS: frozenset[str] = frozenset({"theopenclash", "thenewopenclash"})

# File extensions that are definitely not templates — skip without inspection
_SKIP_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".sh", ".list", ".txt", ".json", ".gitignore", ".lock", ".ini", ".conf",
})


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


# ── Format detection ───────────────────────────────────────────────────────


def _detect_format(path: Path, loaded: dict | None) -> str:
    """Classify the template format from path and parsed content."""
    suffix = path.suffix.lower()
    if suffix not in {".yaml", ".yml"}:
        return "unknown"
    # Directory-based OpenClash detection
    path_parts_lower = {p.lower() for p in path.parts}
    if path_parts_lower & _OPENCLASH_DIR_MARKERS:
        return "openclash"
    # Content-based Clash YAML detection
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
    """List all community templates with lightweight metadata."""
    if not _COMMUNITY_DIR.exists():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(_COMMUNITY_DIR.rglob("*")):
        if not path.is_file():
            continue
        meta = _build_meta(path)
        if meta is not None:
            results.append(meta)
    return results


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


@router.get("/templates/raw", response_class=PlainTextResponse)
def raw_community_template(
    id: str = Query(..., description="Community template id (community:path/to/file)"),
) -> PlainTextResponse:
    path = _path_from_id(id)
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise HTTPException(status_code=422, detail="raw template format is not supported")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/yaml; charset=utf-8")
