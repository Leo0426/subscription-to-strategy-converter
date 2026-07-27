from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from itertools import combinations
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse

import httpx
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from app.core.fetcher import _ensure_resolved_host_is_public, _validate_url
from app.core.policy_analyzer import _PROVIDER_COUNT_BUDGET as PROVIDER_COUNT_BUDGET
from app.core.template_engine import LEO_TEMPLATE_ID, load_template


FetchRuleSource = Callable[[str], Awaitable[dict[str, Any]]]
DEFAULT_REPORT_DIR = Path(".scratch/leo-rule-source-quality/reports")
LEO_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "community_templates" / "leo" / "leo.yaml"
PUBLIC_AUDIT_PATH = LEO_TEMPLATE_PATH.with_name("audit.json")
MAX_RULE_SOURCE_BYTES = 32 * 1024 * 1024

#: Total bytes a client downloads on every cold start across all providers.
#: Shares the provider-count budget with the analyzer so there is one standard.
COLD_START_BYTE_BUDGET = 16 * 1024 * 1024

#: Above this failure ratio a run is treated as an audit-environment failure
#: and cannot replace the published snapshot. Healthy runs fail well under it.
_PUBLISH_MAX_FAILED_RATIO = 0.25

_TARGET_PRIORITY = {
    "REJECT": 0,
    "REJECT-DROP": 0,
    "DIRECT": 10,
    "AI 服务": 20,
    "Apple": 21,
    "Google": 22,
    "Microsoft": 23,
    "开发服务": 24,
    "金融服务": 25,
    "社交通讯": 26,
    "游戏服务": 27,
    "流媒体": 28,
    "默认代理": 90,
    "兜底": 99,
}


def _rule_target(rule: Any) -> str:
    if not isinstance(rule, str):
        return ""
    parts = [part.strip() for part in rule.split(",")]
    if not parts:
        return ""
    if parts[0].upper() in {"MATCH", "FINAL"}:
        return parts[-1]
    if len(parts) >= 2 and parts[-1].lower() == "no-resolve":
        return parts[-2]
    return parts[-1]


def reorder_rules_by_target_priority(rules: list[Any]) -> list[Any]:
    """Order specialized routes before broad fallbacks without mixing rule families."""
    first_non_provider = next(
        (index for index, rule in enumerate(rules) if not str(rule).startswith("RULE-SET,")),
        len(rules),
    )

    def ordered(block: list[Any]) -> list[Any]:
        return sorted(
            block,
            key=lambda rule: _TARGET_PRIORITY.get(_rule_target(rule), 80),
        )

    return ordered(rules[:first_non_provider]) + ordered(rules[first_non_provider:])


def extract_normalized_rule_entries(
    content: bytes,
    *,
    declared_format: str = "",
) -> frozenset[str]:
    """Normalize textual RuleSource entries for content-level comparison."""
    if declared_format.lower() == "mrs":
        return frozenset()
    text = content.decode("utf-8", errors="replace")
    stripped = text.lstrip().lower()
    if stripped.startswith(("<!doctype html", "<html")):
        return frozenset()

    yaml = YAML(typ="safe")
    try:
        loaded = yaml.load(text)
    except YAMLError:
        loaded = None
    if isinstance(loaded, dict) and isinstance(loaded.get("payload"), list):
        candidates = loaded["payload"]
    else:
        candidates = text.splitlines()

    normalized: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        rule = candidate.strip()
        if not rule or rule.startswith(("#", "//")):
            continue
        normalized.add(",".join(part.strip() for part in rule.split(",")).lower())
    return frozenset(normalized)


def find_high_overlap_pairs(
    records: list[Mapping[str, Any]],
    *,
    threshold: float = 0.95,
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for left, right in combinations(records, 2):
        left_entries = frozenset(left.get("entries") or ())
        right_entries = frozenset(right.get("entries") or ())
        if not left_entries or not right_entries:
            continue
        shared_count = len(left_entries & right_entries)
        union_count = len(left_entries | right_entries)
        jaccard = shared_count / union_count
        if jaccard < threshold:
            continue
        smaller_count = min(len(left_entries), len(right_entries))
        pairs.append(
            {
                "providers": sorted([str(left["name"]), str(right["name"])]),
                "jaccard": round(jaccard, 6),
                "containment": round(shared_count / smaller_count, 6),
                "shared_count": shared_count,
                "entry_counts": sorted([len(left_entries), len(right_entries)]),
                "same_targets": set(left.get("targets") or ()) == set(right.get("targets") or ()),
                "left_targets": sorted(set(left.get("targets") or ())),
                "right_targets": sorted(set(right.get("targets") or ())),
            }
        )
    return sorted(pairs, key=lambda pair: (-pair["jaccard"], pair["providers"]))


def find_entry_target_conflicts(
    records: list[Mapping[str, Any]],
    *,
    example_limit: int = 50,
) -> dict[str, Any]:
    entry_index: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for record in records:
        name = str(record["name"])
        targets = {str(target) for target in record.get("targets") or () if str(target)}
        for entry in record.get("entries") or ():
            for target in targets:
                entry_index[str(entry)][target].add(name)

    conflicts = [
        (entry, target_map)
        for entry, target_map in entry_index.items()
        if len(target_map) > 1
    ]
    affected_providers: set[str] = set()
    target_pairs: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    for entry, target_map in sorted(conflicts):
        targets = sorted(target_map)
        for providers in target_map.values():
            affected_providers.update(providers)
        for left, right in combinations(targets, 2):
            target_pairs[f"{left} <> {right}"] += 1
        if len(examples) < example_limit:
            examples.append(
                {
                    "entry": entry,
                    "targets": {
                        target: sorted(providers)
                        for target, providers in sorted(target_map.items())
                    },
                }
            )
    return {
        "indexed_entry_count": len(entry_index),
        "conflict_entry_count": len(conflicts),
        "affected_providers": sorted(affected_providers),
        "target_pairs": dict(sorted(target_pairs.items())),
        "examples": examples,
    }


def find_ordered_entry_conflicts(
    records: list[Mapping[str, Any]],
    *,
    example_limit: int = 50,
) -> dict[str, Any]:
    entry_routes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        for entry in record.get("entries") or ():
            for route in record.get("routes") or ():
                target = str(route.get("target") or "")
                if not target:
                    continue
                entry_routes[str(entry)].append(
                    {
                        "provider": str(record["name"]),
                        "target": target,
                        "rule_index": int(route.get("index") or 0),
                    }
                )

    transition_pairs: Counter[str] = Counter()
    risk_directions: Counter[str] = Counter()
    transition_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    affected_providers: set[str] = set()
    examples: list[dict[str, Any]] = []
    conflict_count = 0
    for entry, routes in sorted(entry_routes.items()):
        ordered = sorted(routes, key=lambda route: (route["rule_index"], route["provider"]))
        if len({route["target"] for route in ordered}) < 2:
            continue
        conflict_count += 1
        winner = ordered[0]
        later_by_target: dict[str, dict[str, Any]] = {}
        for route in ordered[1:]:
            if route["target"] == winner["target"]:
                continue
            later_by_target.setdefault(route["target"], route)
        for target, route in later_by_target.items():
            transition = f"{winner['target']} -> {target}"
            transition_pairs[transition] += 1
            if len(transition_examples[transition]) < 3:
                transition_examples[transition].append(
                    {"entry": entry, "winner": winner, "shadowed": route}
                )
            affected_providers.update({winner["provider"], route["provider"]})
            if winner["target"] == "REJECT" and target == "DIRECT":
                risk_directions["reject_overrides_direct"] += 1
            elif winner["target"] == "DIRECT" and target == "REJECT":
                risk_directions["direct_overrides_reject"] += 1
            elif winner["target"] == "REJECT":
                risk_directions["reject_overrides_service"] += 1
            elif target == "REJECT":
                risk_directions["service_overrides_reject"] += 1
            else:
                risk_directions["earlier_target_overrides_later"] += 1
        if len(examples) < example_limit:
            examples.append(
                {
                    "entry": entry,
                    "winner": winner,
                    "shadowed": list(later_by_target.values()),
                }
            )
    return {
        "indexed_entry_count": len(entry_routes),
        "ordered_conflict_entry_count": conflict_count,
        "affected_providers": sorted(affected_providers),
        "transition_pairs": dict(sorted(transition_pairs.items())),
        "risk_directions": dict(sorted(risk_directions.items())),
        "transition_examples": dict(sorted(transition_examples.items())),
        "examples": examples,
    }


_GITHUB_ORIGIN_HOSTS = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "gist.githubusercontent.com",
        "objects.githubusercontent.com",
        "codeload.github.com",
    }
)
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_VERSION_TAG = re.compile(r"^v?\d+(\.\d+)+$")


def _github_ref_is_pinned(segments: list[str]) -> bool:
    """segments: [owner, repo, ref, ...] or [owner, repo, "refs", "heads"|"tags", ref, ...]."""
    if len(segments) < 3:
        return False
    if segments[2] == "refs" and len(segments) >= 5:
        return segments[3] == "tags"
    return bool(_COMMIT_SHA.match(segments[2]) or _VERSION_TAG.match(segments[2]))


def supply_chain_facts(url: str) -> dict[str, Any]:
    """Machine-checkable supply-chain properties of one RuleSource URL.

    - `upstream`: who can change the content (`github:<owner>` or the hostname).
    - `pinned`: the URL names an immutable ref (commit sha or tag), so upstream
      pushes cannot silently change what clients download.
    - `via_intermediary`: the content passes through a third-party proxy front
      (gh-proxy style) that could rewrite it in transit; official CDNs with a
      declared upstream (jsDelivr `gh/<owner>@<ref>`) are not intermediaries.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    segments = [segment for segment in parsed.path.split("/") if segment]

    if host in _GITHUB_ORIGIN_HOSTS:
        upstream = f"github:{segments[0]}" if segments else host
        return {"upstream": upstream, "pinned": _github_ref_is_pinned(segments), "via_intermediary": False}

    if host.endswith(".jsdelivr.net") and len(segments) >= 2 and segments[0] == "gh":
        owner, _, ref = segments[1].partition("@")
        pinned = bool(_COMMIT_SHA.match(ref) or _VERSION_TAG.match(ref))
        return {"upstream": f"github:{owner}", "pinned": pinned, "via_intermediary": False}

    embedded = next(
        (index for index, segment in enumerate(segments) if segment.lower() in _GITHUB_ORIGIN_HOSTS),
        None,
    )
    if embedded is not None:
        inner = segments[embedded + 1 :]
        upstream = f"github:{inner[0]}" if inner else host
        return {"upstream": upstream, "pinned": _github_ref_is_pinned(inner), "via_intermediary": True}

    return {"upstream": host, "pinned": False, "via_intermediary": False}


def score_rule_source_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Compute a preliminary structural score; semantic accuracy remains unscored."""
    summary = report.get("summary") or {}
    total = max(1, int(summary.get("total") or 0))
    valid = int(summary.get("valid") or 0)
    invalid = int(summary.get("invalid") or 0)
    inspected = max(1, valid + invalid)
    redundant = sum(
        max(0, len(group.get("providers") or []) - 1)
        for group in report.get("duplicate_content_groups") or []
    )
    conflicts = report.get("entry_target_conflicts") or {}
    conflict_entry_count = int(conflicts.get("conflict_entry_count") or 0)
    indexed_entry_count = int(conflicts.get("indexed_entry_count") or 0)
    if indexed_entry_count:
        consistency_ratio = max(0, 1 - conflict_entry_count / indexed_entry_count)
    else:
        affected = len(conflicts.get("affected_providers") or [])
        consistency_ratio = max(0, 1 - affected / max(1, valid))

    sources = [source for source in report.get("sources") or [] if isinstance(source, dict)]
    facts = [supply_chain_facts(str(source.get("url") or "")) for source in sources]
    if facts:
        direct_ratio = sum(not fact["via_intermediary"] for fact in facts) / len(facts)
        pinned_ratio = sum(bool(fact["pinned"]) for fact in facts) / len(facts)
    else:
        direct_ratio = pinned_ratio = 1.0
    total_bytes = sum(int(source.get("byte_count") or 0) for source in sources)
    count_ratio = min(1.0, PROVIDER_COUNT_BUDGET / total)
    bytes_ratio = min(1.0, COLD_START_BYTE_BUDGET / total_bytes) if total_bytes else 1.0

    dimensions = {
        "availability": round(40 * valid / total, 2),
        "content_validity": round(10 * valid / inspected, 2),
        "content_uniqueness": round(15 * max(0, 1 - redundant / total), 2),
        "target_consistency": round(10 * consistency_ratio, 2),
        "supply_chain": round(15 * (0.5 * direct_ratio + 0.5 * pinned_ratio), 2),
        "cold_start_cost": round(10 * (0.5 * count_ratio + 0.5 * bytes_ratio), 2),
    }
    total_score = round(sum(dimensions.values()), 2)
    grade = "A" if total_score >= 90 else "B" if total_score >= 80 else "C" if total_score >= 70 else "D" if total_score >= 60 else "F"
    return {
        "kind": "structural-v2",
        "total": total_score,
        "grade": grade,
        "dimensions": dimensions,
        "evidence": {
            "upstream_count": len({fact["upstream"] for fact in facts}),
            "via_intermediary_count": sum(bool(fact["via_intermediary"]) for fact in facts),
            "unpinned_count": sum(not fact["pinned"] for fact in facts),
            "total_bytes": total_bytes,
            "provider_count_budget": PROVIDER_COUNT_BUDGET,
            "cold_start_byte_budget": COLD_START_BYTE_BUDGET,
        },
        "unmeasured": ["semantic_accuracy", "service_coverage", "long_term_freshness", "content_drift"],
    }


def apply_safe_duplicate_pruning(
    config: Mapping[str, Any],
    report: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Remove only content-identical providers with identical routing semantics."""
    optimized = deepcopy(config)
    if not isinstance(optimized, dict):
        raise ValueError("config must be a mapping")
    providers = optimized.get("rule-providers")
    rules = optimized.get("rules")
    if not isinstance(providers, dict) or not isinstance(rules, list):
        raise ValueError("config must contain rule-providers and rules")

    source_by_name = {
        str(source.get("name")): source
        for source in report.get("sources") or []
        if isinstance(source, dict) and source.get("name")
    }
    rule_order: dict[str, int] = {}
    for index, rule in enumerate(rules):
        if not isinstance(rule, str):
            continue
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) >= 3 and parts[0] == "RULE-SET":
            rule_order.setdefault(parts[1], index)

    removed_names: set[str] = set()
    safe_group_count = 0
    for group in report.get("duplicate_content_groups") or []:
        names = [str(name) for name in group.get("providers") or []]
        partitions: dict[tuple[tuple[str, ...], str, str], list[str]] = defaultdict(list)
        for name in names:
            source = source_by_name.get(name)
            if source is None:
                continue
            signature = (
                tuple(source.get("targets") or []),
                str(source.get("behavior") or ""),
                str(source.get("declared_format") or ""),
            )
            partitions[signature].append(name)
        for partition_names in partitions.values():
            available_names = [
                name for name in partition_names if name in providers and name not in removed_names
            ]
            if len(available_names) < 2:
                continue
            canonical = min(available_names, key=lambda name: rule_order.get(name, len(rules)))
            removed_names.update(name for name in available_names if name != canonical)
            safe_group_count += 1

    for name in removed_names:
        providers.pop(name, None)
    kept_rules = []
    removed_rule_count = 0
    for rule in rules:
        parts = [part.strip() for part in rule.split(",")] if isinstance(rule, str) else []
        if len(parts) >= 2 and parts[0] == "RULE-SET" and parts[1] in removed_names:
            removed_rule_count += 1
            continue
        kept_rules.append(rule)
    optimized["rules"] = kept_rules
    return optimized, {
        "groups": safe_group_count,
        "providers_removed": len(removed_names),
        "rules_removed": removed_rule_count,
    }


def apply_verified_unusable_source_pruning(
    config: Mapping[str, Any],
    provider_names: set[str],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Remove providers explicitly verified unusable across repeated audits.

    Selection is intentionally kept outside this function: a single transient
    fetch failure must never be enough to delete a source automatically.
    """
    optimized = deepcopy(config)
    if not isinstance(optimized, dict):
        raise ValueError("config must be a mapping")
    providers = optimized.get("rule-providers")
    rules = optimized.get("rules")
    if not isinstance(providers, dict) or not isinstance(rules, list):
        raise ValueError("config must contain rule-providers and rules")

    removed_names = provider_names & set(providers)
    for name in removed_names:
        providers.pop(name)

    kept_rules: list[Any] = []
    removed_rule_count = 0
    for rule in rules:
        parts = [part.strip() for part in rule.split(",")] if isinstance(rule, str) else []
        if len(parts) >= 2 and parts[0] == "RULE-SET" and parts[1] in removed_names:
            removed_rule_count += 1
            continue
        kept_rules.append(rule)
    optimized["rules"] = kept_rules
    return optimized, {
        "providers_removed": len(removed_names),
        "rules_removed": removed_rule_count,
    }


def write_safely_deduplicated_leo(
    report: Mapping[str, Any],
    path: Path = LEO_TEMPLATE_PATH,
) -> dict[str, int]:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    config = yaml.load(path.read_text(encoding="utf-8"))
    optimized, changes = apply_safe_duplicate_pruning(config, report)
    with path.open("w", encoding="utf-8") as handle:
        yaml.dump(optimized, handle)
    return changes


def inspect_rule_source_content(
    content: bytes,
    *,
    content_type: str = "",
    declared_format: str = "",
) -> dict[str, Any]:
    """Return a privacy-safe structural summary of one RuleSource body."""
    digest = sha256(content).hexdigest()
    text = content.decode("utf-8", errors="replace")
    detected_format = "unknown"
    entry_count: int | None = 0

    normalized_type = content_type.lower()
    stripped = text.lstrip().lower()
    is_html = "text/html" in normalized_type or stripped.startswith(("<!doctype html", "<html"))
    if is_html:
        detected_format = "html"
    elif declared_format.lower() == "mrs" and content:
        detected_format = "mrs-binary"
        entry_count = None

    if not is_html and detected_format != "mrs-binary":
        yaml = YAML(typ="safe")
        try:
            loaded = yaml.load(text)
        except YAMLError:
            loaded = None
        if isinstance(loaded, dict) and isinstance(loaded.get("payload"), list):
            detected_format = "yaml-payload"
            entry_count = len(loaded["payload"])
        elif declared_format.lower() != "mrs":
            rule_lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith(("#", "//"))
            ]
            if rule_lines:
                detected_format = "text-rules"
                entry_count = len(rule_lines)

    return {
        "declared_format": declared_format,
        "detected_format": detected_format,
        "content_type": content_type,
        "byte_count": len(content),
        "entry_count": entry_count,
        "sha256": digest,
        "valid": detected_format == "mrs-binary"
        or (detected_format not in {"unknown", "html"} and bool(entry_count)),
    }


async def audit_rule_sources(
    providers: Mapping[str, Any],
    targets: Mapping[str, list[str]],
    *,
    fetch: FetchRuleSource,
    concurrency: int = 20,
    routes: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Audit RuleProviders concurrently while isolating every remote failure."""
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def audit_one(name: str, provider: Any) -> dict[str, Any]:
        raw = provider if isinstance(provider, dict) else {}
        url = str(raw.get("url") or "")
        base = {
            "name": name,
            "url": url,
            "behavior": str(raw.get("behavior") or ""),
            "declared_format": str(raw.get("format") or ""),
            "targets": sorted(set(targets.get(name, []))),
            "routes": list((routes or {}).get(name, [])),
            **supply_chain_facts(url),
        }
        try:
            async with semaphore:
                response = await fetch(url)
            status_code = int(response.get("status_code") or 0)
            if status_code < 200 or status_code >= 300:
                return {**base, "status": "failed", "status_code": status_code, "error": f"HTTP {status_code}"}
            content = response.get("content")
            if not isinstance(content, bytes):
                raise ValueError("fetch result content must be bytes")
            inspection = inspect_rule_source_content(
                content,
                content_type=str(response.get("content_type") or ""),
                declared_format=base["declared_format"],
            )
            entries = extract_normalized_rule_entries(
                content,
                declared_format=base["declared_format"],
            )
            normalized_digest = (
                sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()
                if entries
                else ""
            )
            return {
                **base,
                **inspection,
                "unique_entry_count": len(entries) if entries else inspection["entry_count"],
                "normalized_sha256": normalized_digest,
                "_entries": entries,
                "status": "valid" if inspection["valid"] else "invalid",
                "status_code": status_code,
                "final_url": str(response.get("final_url") or url),
                "elapsed_ms": int(response.get("elapsed_ms") or 0),
            }
        except Exception as exc:  # Each external dependency must fail independently.
            error = str(exc).strip() or exc.__class__.__name__
            return {
                **base,
                "status": "failed",
                "status_code": 0,
                "error": error,
                "error_type": exc.__class__.__name__,
            }

    sources = await asyncio.gather(
        *(audit_one(name, providers[name]) for name in sorted(providers))
    )
    summary = {
        "total": len(sources),
        "valid": sum(source["status"] == "valid" for source in sources),
        "invalid": sum(source["status"] == "invalid" for source in sources),
        "failed": sum(source["status"] == "failed" for source in sources),
    }
    comparison_records = [
        {
            "name": source["name"],
            "entries": source.get("_entries") or frozenset(),
            "targets": source.get("targets") or [],
            "routes": source.get("routes") or [],
        }
        for source in sources
        if source.get("status") == "valid" and source.get("_entries")
    ]
    duplicate_digests = [
        {"sha256": digest, "providers": sorted(names)}
        for digest, names in _digest_groups(sources).items()
        if len(names) > 1
    ]
    high_overlap_pairs = find_high_overlap_pairs(comparison_records)
    entry_target_conflicts = find_entry_target_conflicts(comparison_records)
    ordered_entry_conflicts = find_ordered_entry_conflicts(comparison_records)
    for source in sources:
        source.pop("_entries", None)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "duplicate_content_groups": duplicate_digests,
        "high_overlap_pairs": high_overlap_pairs,
        "entry_target_conflicts": entry_target_conflicts,
        "ordered_entry_conflicts": ordered_entry_conflicts,
        "sources": sources,
    }
    report["quality_score"] = score_rule_source_report(report)
    return report


def _digest_groups(sources: list[dict[str, Any]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for source in sources:
        digest = str(source.get("normalized_sha256") or source.get("sha256") or "")
        if source.get("status") == "valid" and digest:
            groups[digest].append(str(source["name"]))
    return groups


def rule_provider_targets(rules: list[Any]) -> dict[str, list[str]]:
    targets: dict[str, set[str]] = defaultdict(set)
    for rule in rules:
        if not isinstance(rule, str):
            continue
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) >= 3 and parts[0] == "RULE-SET":
            targets[parts[1]].add(parts[2])
    return {name: sorted(values) for name, values in targets.items()}


def rule_provider_routes(rules: list[Any]) -> dict[str, list[dict[str, Any]]]:
    routes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, rule in enumerate(rules):
        if not isinstance(rule, str):
            continue
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) >= 3 and parts[0] == "RULE-SET":
            routes[parts[1]].append({"index": index, "target": parts[2]})
    return dict(routes)


class PublicRuleSourceFetcher:
    def __init__(self, *, timeout: float = 15.0, max_bytes: int = MAX_RULE_SOURCE_BYTES) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._client: httpx.AsyncClient | None = None
        self._host_tasks: dict[str, asyncio.Task[None]] = {}
        self._host_lock = asyncio.Lock()

    async def __aenter__(self) -> PublicRuleSourceFetcher:
        # The audit answers "can the target client fetch this?", so it must send
        # the UA a real Mihomo core sends (global-ua defaults to clash.meta);
        # hosts like kelee.one allow-list that prefix and 403 everything else.
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            headers={"User-Agent": "clash.meta/1.18.0 (subflow-rule-audit)"},
        )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _validate_public_url(self, url: str) -> None:
        _validate_url(url)
        hostname = urlparse(url).hostname
        if not hostname:
            raise ValueError("rule source URL has no hostname")
        async with self._host_lock:
            task = self._host_tasks.get(hostname)
            if task is None:
                task = asyncio.create_task(_ensure_resolved_host_is_public(hostname))
                self._host_tasks[hostname] = task
        await task

    async def fetch(self, url: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("fetcher must be used as an async context manager")
        current_url = url
        started = perf_counter()
        for _ in range(6):
            await self._validate_public_url(current_url)
            response = await self._client.get(current_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise RuntimeError("redirect response is missing Location")
                current_url = str(response.url.join(location))
                continue
            content = response.content
            if len(content) > self.max_bytes:
                raise RuntimeError(f"rule source exceeds {self.max_bytes} bytes")
            return {
                "status_code": response.status_code,
                "final_url": str(response.url),
                "content_type": response.headers.get("content-type", ""),
                "content": content,
                "elapsed_ms": round((perf_counter() - started) * 1000),
            }
        raise RuntimeError("rule source redirect limit exceeded")


def _render_supply_chain_line(evidence: Mapping[str, Any]) -> str:
    return (
        f"供应链与冷启动：**{evidence.get('upstream_count', 0)}** 个独立上游，"
        f"**{evidence.get('via_intermediary_count', 0)}** 个源经第三方代理中转，"
        f"**{evidence.get('unpinned_count', 0)}** 个源不可固定版本；"
        f"冷启动总下载量 **{evidence.get('total_bytes', 0) / 1048576:.1f} MiB**"
        f"（预算 {evidence.get('cold_start_byte_budget', COLD_START_BYTE_BUDGET) / 1048576:.0f} MiB，"
        f"数量预算 {evidence.get('provider_count_budget', PROVIDER_COUNT_BUDGET)}）。"
    )


def render_markdown_report(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    sources = list(report.get("sources", []))
    failures = [source for source in sources if source.get("status") == "failed"]
    invalid = [source for source in sources if source.get("status") == "invalid"]
    formats = Counter(str(source.get("detected_format") or "failed") for source in sources)
    overlap_pairs = list(report.get("high_overlap_pairs", []))
    cross_target_pairs = [pair for pair in overlap_pairs if not pair.get("same_targets")]
    entry_conflicts = report.get("entry_target_conflicts") or {}
    ordered_conflicts = report.get("ordered_entry_conflicts") or {}
    quality = report.get("quality_score") or {}
    lines = [
        "# Leo RuleSource 首轮审计",
        "",
        f"生成时间：{report.get('generated_at', '')}",
        "",
        "## 汇总",
        "",
        "| 总数 | 有效 | 内容无效 | 获取失败 | 完全重复内容组 | ≥95% 重叠对 | 跨目标重叠对 |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {summary['total']} | {summary['valid']} | {summary['invalid']} | {summary['failed']} | {len(report.get('duplicate_content_groups', []))} | {len(overlap_pairs)} | {len(cross_target_pairs)} |",
        "",
        f"逐条目目标冲突：**{entry_conflicts.get('conflict_entry_count', 0)}** 条，涉及 **{len(entry_conflicts.get('affected_providers', []))}** 个 RuleProvider。",
        "",
        f"按实际规则顺序生效的冲突：**{ordered_conflicts.get('ordered_conflict_entry_count', 0)}** 条。",
        "",
        f"初步结构质量评分：**{quality.get('total', 0)} / 100（{quality.get('grade', '-')}）**。该分数不包含语义准确率、服务覆盖率、长期新鲜度和内容漂移。",
        "",
        _render_supply_chain_line(quality.get("evidence") or {}),
        "",
        "## 检测格式",
        "",
        "| 格式 | 数量 |",
        "|---|---:|",
        *[f"| {name} | {count} |" for name, count in sorted(formats.items())],
        "",
        "## 内容无效",
        "",
        *([f"- `{source['name']}`：{source.get('detected_format', 'unknown')}" for source in invalid] or ["- 无"]),
        "",
        "## 获取失败",
        "",
        *([f"- `{source['name']}`：{source.get('error', 'unknown error')}" for source in failures] or ["- 无"]),
        "",
        "## 后续",
        "",
        "成功下载的文本规则将在第二阶段做条目归一化、内容重叠与目标冲突分析。MRS 二进制本阶段仅验证可下载性和内容摘要。",
        "",
    ]
    return "\n".join(lines)


async def audit_leo_rule_sources(*, concurrency: int = 24, timeout: float = 15.0) -> dict[str, Any]:
    template = load_template(LEO_TEMPLATE_ID)
    providers = template.get("rule-providers") or {}
    rules = template.get("rules") or []
    async with PublicRuleSourceFetcher(timeout=timeout) as fetcher:
        return await audit_rule_sources(
            providers,
            rule_provider_targets(rules),
            fetch=fetcher.fetch,
            concurrency=concurrency,
            routes=rule_provider_routes(rules),
        )


def write_audit_report(report: Mapping[str, Any], output_dir: Path = DEFAULT_REPORT_DIR) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"rule-source-audit-{stamp}.json"
    markdown_path = output_dir / f"rule-source-audit-{stamp}.md"
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown_text = render_markdown_report(report)
    json_path.write_text(json_text, encoding="utf-8")
    markdown_path.write_text(markdown_text, encoding="utf-8")
    (output_dir / "latest.json").write_text(json_text, encoding="utf-8")
    (output_dir / "latest.md").write_text(markdown_text, encoding="utf-8")
    return json_path, markdown_path


def write_public_audit_snapshot(
    report: Mapping[str, Any],
    path: Path = PUBLIC_AUDIT_PATH,
) -> Path:
    """Publish the complete metadata-only audit beside leo.yaml.

    An audit where nothing succeeded — or where failures dominate — describes
    the audit environment (blocked DNS, degraded proxy route), not the sources;
    publishing it would replace real evidence with noise, so it is refused.
    Real source decay arrives gradually and passes this gate.
    """
    summary = report.get("summary") or {}
    total = max(1, int(summary.get("total") or 0))
    valid = int(summary.get("valid") or 0)
    failed = int(summary.get("failed") or 0)
    if not valid or failed / total > _PUBLISH_MAX_FAILED_RATIO:
        raise ValueError(
            f"refusing to publish an audit with {failed}/{total} failed sources; "
            "this indicates an audit-environment failure, not source quality"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Leo RuleProvider availability and content summaries")
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--apply-safe-dedup", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(audit_leo_rule_sources(concurrency=args.concurrency, timeout=args.timeout))
    json_path, markdown_path = write_audit_report(report, args.output_dir)
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(json_path)
    print(markdown_path)
    if args.apply_safe_dedup:
        print(json.dumps(write_safely_deduplicated_leo(report), ensure_ascii=False))
    if args.publish:
        print(write_public_audit_snapshot(report))


if __name__ == "__main__":
    main()
