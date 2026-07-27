"""Service-level RuleSource consolidation planner (ADR 0011).

Groups the Leo template's providers into consolidation units of
(service family × routed targets), ranks each unit's members by the ADR's
best-source order, and emits a reviewable removal plan. Nothing here edits
the template; execution happens per batch after key-domain regression.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.rule_source_audit import (
    DEFAULT_REPORT_DIR,
    LEO_TEMPLATE_PATH,
    PublicRuleSourceFetcher,
    extract_normalized_rule_entries,
    rule_provider_targets,
    supply_chain_facts,
)
from app.core.template_engine import LEO_TEMPLATE_ID, load_template


#: Admission-checklist preferred upstreams (community_templates/leo/README.md).
TRUSTED_UPSTREAMS = frozenset(
    {
        "github:MetaCubeX",
        "github:blackmatrix7",
        "github:DustinWin",
        "ruleset.skk.moe",
    }
)

_TRAILING_INDEX = re.compile(r"[-_ ]?\d+$")
_SEPARATORS = re.compile(r"[-_ ]")


def service_family(name: str) -> str:
    """Collapse copy-numbered provider names (Netflix-7, netflix_2) to one family."""
    return _SEPARATORS.sub("", _TRAILING_INDEX.sub("", name).lower())


def rank_key(record: Mapping[str, Any]) -> tuple:
    """ADR 0011 best-source order: dual-target > trusted upstream > coverage > cost."""
    facts = supply_chain_facts(str(record.get("url") or ""))
    dual_target = str(record.get("declared_format") or "").lower() != "mrs"
    trusted = facts["upstream"] in TRUSTED_UPSTREAMS
    direct = not facts["via_intermediary"]
    coverage = len(record.get("entries") or ())
    byte_count = int(record.get("byte_count") or 0)
    return (
        0 if dual_target else 1,
        0 if trusted else 1,
        0 if direct else 1,
        -coverage,
        byte_count,
        str(record.get("name") or ""),
    )


def build_consolidation_plan(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Group records into (family × targets) units and rank each unit's members.

    Records must carry name, url, targets, declared_format, byte_count and,
    when the content is textual, the normalized `entries` frozenset.
    """
    units: dict[tuple[str, tuple[str, ...]], list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        key = (service_family(str(record["name"])), tuple(sorted(record.get("targets") or ())))
        units[key].append(record)

    plan_units: list[dict[str, Any]] = []
    removed_total = 0
    bytes_removed = 0
    for (family, targets), members in sorted(units.items()):
        if len(members) < 2:
            continue
        ranked = sorted(members, key=rank_key)
        retained, removed = ranked[0], ranked[1:]
        retained_entries = frozenset(retained.get("entries") or ())
        removed_names: list[dict[str, Any]] = []
        for member in removed:
            member_entries = frozenset(member.get("entries") or ())
            if member_entries and retained_entries:
                lost = member_entries - retained_entries
                loss = {"lost_entry_count": len(lost), "lost_examples": sorted(lost)[:5]}
            else:
                loss = {"lost_entry_count": None, "lost_examples": []}
            removed_names.append({"name": member["name"], "url": member["url"], **loss})
            bytes_removed += int(member.get("byte_count") or 0)
        removed_total += len(removed)
        plan_units.append(
            {
                "family": family,
                "targets": list(targets),
                "retained": {"name": retained["name"], "url": retained["url"]},
                "removed": removed_names,
            }
        )

    total = len(records)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "provider_count_before": total,
            "provider_count_after": total - removed_total,
            "removed": removed_total,
            "bytes_removed": bytes_removed,
            "multi_member_units": len(plan_units),
        },
        "units": plan_units,
    }


def apply_consolidation_plan(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    families: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Remove a plan's pruned providers and their rules from a config copy.

    `families` limits execution to one reviewable batch; None applies all units.
    """
    from copy import deepcopy

    removed_names = {
        entry["name"]
        for unit in plan.get("units") or []
        if families is None or unit["family"] in families
        for entry in unit["removed"]
    }
    optimized = deepcopy(dict(config))
    providers = optimized.get("rule-providers")
    rules = optimized.get("rules")
    if not isinstance(providers, dict) or not isinstance(rules, list):
        raise ValueError("config must contain rule-providers and rules")
    for name in removed_names & set(providers):
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
    return optimized, {"providers_removed": len(removed_names), "rules_removed": removed_rule_count}


async def plan_leo_consolidation(*, concurrency: int = 24, timeout: float = 15.0) -> dict[str, Any]:
    """Fetch every Leo provider's content and build the full consolidation plan."""
    template = load_template(LEO_TEMPLATE_ID)
    providers = template.get("rule-providers") or {}
    targets = rule_provider_targets(template.get("rules") or [])
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async with PublicRuleSourceFetcher(timeout=timeout) as fetcher:

        async def load_record(name: str, provider: Any) -> dict[str, Any]:
            raw = provider if isinstance(provider, dict) else {}
            record: dict[str, Any] = {
                "name": name,
                "url": str(raw.get("url") or ""),
                "declared_format": str(raw.get("format") or ""),
                "targets": sorted(set(targets.get(name, []))),
                "byte_count": 0,
                "entries": frozenset(),
            }
            try:
                async with semaphore:
                    response = await fetcher.fetch(record["url"])
                content = response.get("content")
                if isinstance(content, bytes) and 200 <= int(response.get("status_code") or 0) < 300:
                    record["byte_count"] = len(content)
                    record["entries"] = extract_normalized_rule_entries(
                        content, declared_format=record["declared_format"]
                    )
            except Exception:  # A fetch failure only weakens this record's ranking.
                pass
            return record

        records = await asyncio.gather(
            *(load_record(name, providers[name]) for name in sorted(providers))
        )
    return build_consolidation_plan(list(records))


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan Leo rule source consolidation (ADR 0011)")
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    plan = asyncio.run(plan_leo_consolidation(concurrency=args.concurrency, timeout=args.timeout))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = args.output_dir / f"consolidation-plan-{stamp}.json"
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan["summary"], ensure_ascii=False))
    print(path)


if __name__ == "__main__":
    main()
