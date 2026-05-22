from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import json
import warnings
from typing import Any, Iterable

from ruamel.yaml import YAML
from ruamel.yaml.error import ReusedAnchorWarning


APP_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = APP_DIR.parent
COMMUNITY_TEMPLATE_ROOT = PROJECT_DIR / "community_templates"

# Known mirror proxy prefixes — strip these to find the canonical URL
_MIRROR_PREFIXES = (
    "https://git.imee.me/",
    "https://mirror.ghproxy.com/",
    "https://ghproxy.com/",
    "https://ghfast.top/",
    "https://fastgit.org/",
    "https://raw.fastgit.org/",
)


def _load_yaml(path: Path) -> Any:
    yaml = YAML(typ="safe")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ReusedAnchorWarning)
        return yaml.load(path.read_text(encoding="utf-8"))


def _iter_yaml_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    yield from sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    )


def _all_values(data: Any, key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(data, dict):
        if key in data:
            values.append(data[key])
        for value in data.values():
            values.extend(_all_values(value, key))
    elif isinstance(data, list):
        for item in data:
            values.extend(_all_values(item, key))
    return values


def _first_value(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _first_value(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _first_value(item, key)
            if found is not None:
                return found
    return None


def _first_list(data: Any, key: str) -> list[Any]:
    for value in _all_values(data, key):
        if isinstance(value, list):
            return value
    return []


def _first_mapping(data: Any, key: str) -> dict[str, Any]:
    for value in _all_values(data, key):
        if isinstance(value, dict):
            return value
    return {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _rule_parts(rule: Any) -> dict[str, str]:
    if isinstance(rule, str):
        parts = [part.strip() for part in rule.split(",")]
        target = ""
        if len(parts) >= 4 and parts[-1].lower() == "no-resolve":
            target = parts[-2]
        elif len(parts) > 1:
            target = parts[-1]
        return {
            "type": parts[0] if parts else "",
            "match": parts[1] if len(parts) > 1 else "",
            "target": target,
            "provider": parts[1] if parts and parts[0] == "RULE-SET" and len(parts) > 1 else "",
            "text": rule,
        }
    if isinstance(rule, dict):
        provider = _text(rule.get("rule-set") or rule.get("provider"))
        return {
            "type": _text(rule.get("type") or rule.get("rule")),
            "match": _text(rule.get("match") or provider),
            "target": _text(rule.get("proxy") or rule.get("policy") or rule.get("target")),
            "provider": provider,
            "text": json.dumps(_jsonable(rule), ensure_ascii=False, sort_keys=True),
        }
    return {
        "type": type(rule).__name__,
        "match": "",
        "target": "",
        "provider": "",
        "text": _text(rule),
    }


def _group_refs(group: Any) -> list[str]:
    if not isinstance(group, dict):
        return []
    refs: list[str] = []
    for key in ("proxies", "use"):
        value = group.get(key)
        if isinstance(value, list):
            refs.extend(_text(item) for item in value if _text(item))
    return refs


# ── Deduplication pass 1: rule-providers by URL ────────────────────────────

def _normalize_provider_url(url: str) -> str:
    """Strip mirror prefixes then lowercase for stable comparison.

    Loops until stable so that double-wrapped mirrors
    (e.g. ghproxy.com/https://ghfast.top/raw.githubusercontent.com/…) are
    fully unwrapped before the canonical URL is returned.

    After stripping all mirror prefixes the protocol is also removed so that
    a mirror-wrapped URL and the corresponding direct URL resolve to the same
    canonical string. Without this step ghfast.top-wrapped URLs drop the
    protocol while direct github URLs keep it, making identical paths look
    different to the deduplicator.
    """
    url = url.strip().rstrip("/")
    changed = True
    while changed:
        changed = False
        for prefix in _MIRROR_PREFIXES:
            if url.startswith(prefix):
                url = url[len(prefix):]
                changed = True
                break
    for proto in ("https://", "http://"):
        if url.startswith(proto):
            url = url[len(proto):]
            break
    return url.lower()


def _provider_quality(p: dict[str, Any]) -> int:
    """Higher score = better canonical choice. Prefer direct sources and MRS format."""
    url = p.get("url", "")
    score = 0
    if "raw.githubusercontent.com" in url:
        score += 30
    elif "github.com" in url:
        score += 20
    elif "cdn.jsdelivr.net" in url:
        score += 5
    for prefix in _MIRROR_PREFIXES:
        if url.startswith(prefix):
            score -= 25
            break
    if url.endswith(".mrs"):
        score += 10
    # Prefer shorter, more canonical names as a tiebreaker
    score -= len(p.get("name", "")) // 8
    return score


def _deduplicate_providers(
    providers: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], str]]:
    """
    Group providers by normalized URL. Keep the highest-quality one per URL.
    Returns (canonical_list, alias_map) where alias_map maps
    every (template, name) pair to the canonical provider id.
    """
    url_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    no_url: list[dict[str, Any]] = []

    for p in providers:
        url = p.get("url", "").strip()
        norm = _normalize_provider_url(url) if url else ""
        if norm:
            url_groups[norm].append(p)
        else:
            no_url.append(p)

    canonical: list[dict[str, Any]] = []
    alias_map: dict[tuple[str, str], str] = {}

    for _norm, group in url_groups.items():
        best = max(group, key=_provider_quality)
        sources = [[p["template"], p["name"]] for p in group]
        canonical.append({**best, "sources": sources})
        for p in group:
            alias_map[(p["template"], p["name"])] = best["id"]

    for p in no_url:
        canonical.append({**p, "sources": [[p["template"], p["name"]]]})
        alias_map[(p["template"], p["name"])] = p["id"]

    return canonical, alias_map


# ── Deduplication pass 2: rules by (raw_text, target) ─────────────────────

def _rule_sig(r: dict[str, Any]) -> tuple[str, str]:
    raw = r["raw"]
    if isinstance(raw, str):
        # Normalize whitespace around commas so "A, b , C" == "A,b,C"
        raw_text = ",".join(part.strip() for part in raw.split(","))
    else:
        raw_text = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    return (raw_text, r.get("target", ""))


def _rule_quality(
    r: dict[str, Any],
    provider_alias_map: dict[tuple[str, str], str],
) -> int:
    """Prefer rules whose template also owns the canonical provider."""
    prov_key = (r["template"], r.get("provider", ""))
    canonical_id = provider_alias_map.get(prov_key, "")
    if canonical_id and "::provider::" in canonical_id:
        prov_template = canonical_id.split("::provider::")[0]
        if prov_template == r["template"]:
            return 20
    return 0


def _deduplicate_rules(
    rules: list[dict[str, Any]],
    provider_alias_map: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    """
    Remove MATCH rules (template-level fallbacks, not user-selectable).
    Then deduplicate identical (raw_text, target) pairs across templates.
    """
    filtered = [r for r in rules if r.get("type") != "MATCH"]

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in filtered:
        groups[_rule_sig(r)].append(r)

    canonical: list[dict[str, Any]] = []
    for _sig, group in groups.items():
        if len(group) == 1:
            canonical.append(group[0])
        else:
            best = max(group, key=lambda r: _rule_quality(r, provider_alias_map))
            canonical.append(best)

    return canonical


# ── Deduplication pass 3: proxy-groups by (name, type) ────────────────────

def _group_quality(g: dict[str, Any]) -> int:
    """Prefer groups with explicit proxy lists, url-test config, or filters.

    For url-test / fallback / load-balance groups, include-all is ranked
    higher than a static proxy list because it automatically picks up new
    nodes without requiring a template update.
    """
    raw = g.get("raw", {})
    if not isinstance(raw, dict):
        return 0
    score = 0
    group_type = raw.get("type", "")
    is_test_group = group_type in {"url-test", "fallback", "load-balance"}
    proxies = raw.get("proxies", [])
    if isinstance(proxies, list) and proxies:
        score += len(proxies) + 5
    if raw.get("url"):
        score += 3
    if raw.get("filter"):
        score += 2
    if raw.get("include-all"):
        # Dynamic inclusion beats a static list for test-type groups
        score += 15 if is_test_group else 1
    return score


def _deduplicate_groups(
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Deduplicate by (name, type). Keep best representative.
    Add a `sources` field listing every (template, name) merged in.
    """
    key_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for g in groups:
        key = (g["name"], g.get("type", ""))
        key_groups[key].append(g)

    canonical: list[dict[str, Any]] = []
    for _key, group in key_groups.items():
        sources = [[g["template"], g["name"]] for g in group]
        if len(group) == 1:
            canonical.append({**group[0], "sources": sources})
        else:
            best = max(group, key=_group_quality)
            canonical.append({**best, "sources": sources})

    return canonical


# ── Main catalog loader ────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_policy_catalog() -> dict[str, Any]:
    templates: list[dict[str, Any]] = []
    proxy_groups: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    rule_providers: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for path in _iter_yaml_files(COMMUNITY_TEMPLATE_ROOT):
        rel = path.relative_to(COMMUNITY_TEMPLATE_ROOT).as_posix()
        parts = path.relative_to(COMMUNITY_TEMPLATE_ROOT).parts
        section = parts[0] if len(parts) >= 1 else ""
        author = parts[1] if len(parts) >= 2 else ""

        try:
            data = _load_yaml(path)
            if not isinstance(data, dict):
                raise ValueError(f"YAML root is {type(data).__name__}, expected mapping")
        except Exception as exc:
            errors.append(
                {
                    "template": rel,
                    "section": section,
                    "author": author,
                    "error": f"{type(exc).__name__}: {exc}".splitlines()[0],
                }
            )
            templates.append(
                {
                    "id": rel,
                    "path": rel,
                    "section": section,
                    "author": author,
                    "mode": "",
                    "dnsMode": "",
                    "proxyGroupCount": 0,
                    "ruleCount": 0,
                    "ruleProviderCount": 0,
                    "status": "parse_error",
                }
            )
            continue

        groups = _first_list(data, "proxy-groups")
        template_rules = _first_list(data, "rules")
        providers = _first_mapping(data, "rule-providers")
        dns = _first_mapping(data, "dns")

        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            name = _text(group.get("name")) or f"group-{index + 1}"
            group_type = _text(group.get("type"))
            proxy_groups.append(
                {
                    "id": f"{rel}::group::{index}",
                    "template": rel,
                    "section": section,
                    "author": author,
                    "name": name,
                    "type": group_type,
                    "refs": _group_refs(group),
                    "raw": _jsonable(group),
                }
            )

        for index, rule in enumerate(template_rules):
            parts_info = _rule_parts(rule)
            rules.append(
                {
                    "id": f"{rel}::rule::{index}",
                    "template": rel,
                    "section": section,
                    "author": author,
                    "index": index + 1,
                    "type": parts_info["type"],
                    "match": parts_info["match"],
                    "target": parts_info["target"],
                    "provider": parts_info["provider"],
                    "text": parts_info["text"],
                    "raw": _jsonable(rule),
                }
            )

        for name, provider in providers.items():
            provider_data = provider if isinstance(provider, dict) else {}
            rule_providers.append(
                {
                    "id": f"{rel}::provider::{name}",
                    "template": rel,
                    "section": section,
                    "author": author,
                    "name": str(name),
                    "type": _text(provider_data.get("type")),
                    "behavior": _text(provider_data.get("behavior")),
                    "format": _text(provider_data.get("format")),
                    "url": _text(provider_data.get("url")),
                    "raw": _jsonable(provider),
                }
            )

        templates.append(
            {
                "id": rel,
                "path": rel,
                "section": section,
                "author": author,
                "mode": _text(_first_value(data, "mode")).lower(),
                "dnsMode": _text(dns.get("enhanced-mode")).lower() if dns else "",
                "proxyGroupCount": len(groups),
                "ruleCount": len(template_rules),
                "ruleProviderCount": len(providers),
                "status": "ok",
            }
        )

    # ── Three-pass deduplication ─────────────────────────────────────────────
    raw_totals = (len(rule_providers), len(rules), len(proxy_groups))

    rule_providers, provider_alias_map = _deduplicate_providers(rule_providers)
    rules = _deduplicate_rules(rules, provider_alias_map)
    proxy_groups = _deduplicate_groups(proxy_groups)

    dedup_saved = {
        "providers": raw_totals[0] - len(rule_providers),
        "rules": raw_totals[1] - len(rules),
        "groups": raw_totals[2] - len(proxy_groups),
    }

    return {
        "meta": {
            "templateCount": len(templates),
            "proxyGroupCount": len(proxy_groups),
            "ruleCount": len(rules),
            "ruleProviderCount": len(rule_providers),
            "parseErrorCount": len(errors),
            "dedupSaved": dedup_saved,
        },
        "facets": {
            "sections": sorted({item["section"] for item in templates if item["section"]}),
            "authors": sorted({item["author"] for item in templates if item["author"]}),
            "groupTypes": sorted({item["type"] for item in proxy_groups if item["type"]}),
            "ruleTypes": sorted({item["type"] for item in rules if item["type"]}),
            "ruleTargets": sorted({item["target"] for item in rules if item["target"]}),
        },
        "templates": templates,
        "proxyGroups": proxy_groups,
        "rules": rules,
        "ruleProviders": rule_providers,
        "errors": errors,
    }


def selected_policy_from_ids(
    proxy_group_ids: list[str] | None = None,
    rule_ids: list[str] | None = None,
    rule_provider_ids: list[str] | None = None,
    rule_targets: dict[str, str] | None = None,
) -> dict[str, Any]:
    catalog = load_policy_catalog()
    group_id_set = set(proxy_group_ids or [])
    rule_id_set = set(rule_ids or [])
    provider_id_set = set(rule_provider_ids or [])

    # Build alias-aware lookup: (template, name) -> canonical provider
    # Uses the `sources` field populated during dedup.
    prov_by_source: dict[tuple[str, str], dict[str, Any]] = {}
    for p in catalog["ruleProviders"]:
        for src in p.get("sources", [[p["template"], p["name"]]]):
            prov_by_source[(src[0], src[1])] = p

    selected_rules = [item for item in catalog["rules"] if item["id"] in rule_id_set]
    for rule in selected_rules:
        provider_name = rule.get("provider")
        if not provider_name:
            continue
        prov = prov_by_source.get((rule["template"], provider_name))
        if prov:
            provider_id_set.add(prov["id"])

    target_overrides = rule_targets or {}

    return {
        "proxy_groups": [
            item["raw"] for item in catalog["proxyGroups"] if item["id"] in group_id_set
        ],
        "rules": [
            _rewrite_rule_target(item["raw"], target_overrides.get(item["id"], ""))
            for item in selected_rules
        ],
        "rule_providers": {
            item["name"]: item["raw"]
            for item in catalog["ruleProviders"]
            if item["id"] in provider_id_set
        },
    }


def _rewrite_rule_target(rule: Any, target: str) -> Any:
    if not target:
        return rule
    if isinstance(rule, str):
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) < 2:
            return rule
        if len(parts) >= 4 and parts[-1].lower() == "no-resolve":
            parts[-2] = target
        else:
            parts[-1] = target
        return ",".join(parts)
    if isinstance(rule, dict):
        updated = dict(rule)
        if "proxy" in updated:
            updated["proxy"] = target
        elif "policy" in updated:
            updated["policy"] = target
        else:
            updated["target"] = target
        return updated
    return rule
