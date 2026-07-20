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
from time import perf_counter
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse

import httpx
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from app.core.fetcher import _ensure_resolved_host_is_public, _validate_url
from app.core.template_engine import LEO_TEMPLATE_ID, load_template


FetchRuleSource = Callable[[str], Awaitable[dict[str, Any]]]
DEFAULT_REPORT_DIR = Path(".scratch/leo-rule-source-quality/reports")
LEO_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "community_templates" / "leo" / "leo.yaml"
MAX_RULE_SOURCE_BYTES = 32 * 1024 * 1024

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
    dimensions = {
        "availability": round(50 * valid / total, 2),
        "content_validity": round(15 * valid / inspected, 2),
        "content_uniqueness": round(20 * max(0, 1 - redundant / total), 2),
        "target_consistency": round(15 * consistency_ratio, 2),
    }
    total_score = round(sum(dimensions.values()), 2)
    grade = "A" if total_score >= 90 else "B" if total_score >= 80 else "C" if total_score >= 70 else "D" if total_score >= 60 else "F"
    return {
        "kind": "preliminary-structural",
        "total": total_score,
        "grade": grade,
        "dimensions": dimensions,
        "unmeasured": ["semantic_accuracy", "service_coverage", "long_term_freshness"],
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
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=False,
            headers={"User-Agent": "subflow-rule-audit/0.1"},
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
        f"初步结构质量评分：**{quality.get('total', 0)} / 100（{quality.get('grade', '-')}）**。该分数不包含语义准确率、服务覆盖率和长期新鲜度。",
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Leo RuleProvider availability and content summaries")
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--apply-safe-dedup", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(audit_leo_rule_sources(concurrency=args.concurrency, timeout=args.timeout))
    json_path, markdown_path = write_audit_report(report, args.output_dir)
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(json_path)
    print(markdown_path)
    if args.apply_safe_dedup:
        print(json.dumps(write_safely_deduplicated_leo(report), ensure_ascii=False))


if __name__ == "__main__":
    main()
