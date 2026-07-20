import pytest

from app.core.rule_source_audit import (
    apply_safe_duplicate_pruning,
    apply_verified_unusable_source_pruning,
    audit_rule_sources,
    extract_normalized_rule_entries,
    find_entry_target_conflicts,
    find_high_overlap_pairs,
    find_ordered_entry_conflicts,
    inspect_rule_source_content,
    reorder_rules_by_target_priority,
    score_rule_source_report,
)


def test_inspect_rule_source_content_summarizes_yaml_payload_without_storing_rules() -> None:
    summary = inspect_rule_source_content(
        b"payload:\n  - DOMAIN-SUFFIX,openai.com\n  - DOMAIN,api.openai.com\n",
        content_type="text/yaml",
        declared_format="yaml",
    )

    assert summary["detected_format"] == "yaml-payload"
    assert summary["entry_count"] == 2
    assert summary["byte_count"] > 0
    assert len(summary["sha256"]) == 64
    assert "payload" not in summary


def test_inspect_rule_source_content_rejects_html_success_pages() -> None:
    summary = inspect_rule_source_content(
        b"<!doctype html><html><title>Sign in</title></html>",
        content_type="text/html; charset=utf-8",
    )

    assert summary["detected_format"] == "html"
    assert summary["valid"] is False
    assert summary["entry_count"] == 0


def test_inspect_rule_source_content_counts_plain_rules_and_ignores_comments() -> None:
    summary = inspect_rule_source_content(
        b"# generated\n\nDOMAIN-SUFFIX,openai.com\napi.openai.com\n",
        content_type="text/plain",
        declared_format="text",
    )

    assert summary["detected_format"] == "text-rules"
    assert summary["entry_count"] == 2
    assert summary["valid"] is True


def test_inspect_rule_source_content_accepts_nonempty_declared_mrs_binary() -> None:
    summary = inspect_rule_source_content(
        b"MRS\x00\x01\x02binary",
        content_type="application/octet-stream",
        declared_format="mrs",
    )

    assert summary["detected_format"] == "mrs-binary"
    assert summary["entry_count"] is None
    assert summary["valid"] is True


def test_extract_normalized_rule_entries_ignores_order_case_and_yaml_noise() -> None:
    yaml_entries = extract_normalized_rule_entries(
        b"payload:\n  - DOMAIN-SUFFIX, OpenAI.COM\n  - DOMAIN,api.openai.com\n",
        declared_format="yaml",
    )
    text_entries = extract_normalized_rule_entries(
        b"# generated\nDOMAIN, api.openai.com\nDOMAIN-SUFFIX,openai.com\n",
        declared_format="text",
    )

    assert yaml_entries == text_entries == frozenset(
        {"domain,api.openai.com", "domain-suffix,openai.com"}
    )


def test_find_high_overlap_pairs_reports_jaccard_containment_and_target_agreement() -> None:
    common = {f"domain,service-{index}.example" for index in range(96)}
    records = [
        {"name": "A", "entries": frozenset(common | {f"domain,a-{index}.example" for index in range(4)}), "targets": ["AI 服务"]},
        {"name": "B", "entries": frozenset(common), "targets": ["AI 服务"]},
        {"name": "C", "entries": frozenset(common), "targets": ["DIRECT"]},
    ]

    pairs = find_high_overlap_pairs(records, threshold=0.95)

    assert pairs[0]["jaccard"] == 1.0
    assert pairs[0]["same_targets"] is False
    assert any(
        pair["providers"] == ["A", "B"]
        and pair["jaccard"] == 0.96
        and pair["containment"] == 1.0
        and pair["same_targets"] is True
        for pair in pairs
    )


def test_find_entry_target_conflicts_reports_shared_entries_with_different_targets() -> None:
    records = [
        {"name": "AI", "entries": frozenset({"domain,a.example", "domain,shared.example"}), "targets": ["AI 服务"]},
        {"name": "Direct", "entries": frozenset({"domain,shared.example"}), "targets": ["DIRECT"]},
        {"name": "AI supplement", "entries": frozenset({"domain,a.example"}), "targets": ["AI 服务"]},
    ]

    conflicts = find_entry_target_conflicts(records)

    assert conflicts["conflict_entry_count"] == 1
    assert conflicts["indexed_entry_count"] == 2
    assert conflicts["affected_providers"] == ["AI", "Direct"]
    assert conflicts["target_pairs"] == {"AI 服务 <> DIRECT": 1}
    assert conflicts["examples"][0]["entry"] == "domain,shared.example"


def test_score_rule_source_report_is_weighted_and_explains_unmeasured_semantics() -> None:
    report = {
        "summary": {"total": 10, "valid": 8, "invalid": 1, "failed": 1},
        "duplicate_content_groups": [{"providers": ["A", "B", "C"]}],
        "entry_target_conflicts": {
            "conflict_entry_count": 2,
            "indexed_entry_count": 8,
            "affected_providers": ["D", "E"],
        },
    }

    score = score_rule_source_report(report)

    assert score["total"] == pytest.approx(80.58, abs=0.01)
    assert score["dimensions"]["availability"] == 40.0
    assert "semantic_accuracy" in score["unmeasured"]
    assert score["grade"] == "B"


def test_apply_safe_duplicate_pruning_keeps_first_ordered_equivalent_provider() -> None:
    config = {
        "rule-providers": {
            "A": {"behavior": "classical", "format": "yaml", "url": "https://a.example/rules"},
            "B": {"behavior": "classical", "format": "yaml", "url": "https://b.example/rules"},
            "C": {"behavior": "classical", "format": "text", "url": "https://c.example/rules"},
        },
        "rules": ["RULE-SET,B,AI 服务", "RULE-SET,A,AI 服务", "RULE-SET,C,AI 服务", "MATCH,兜底"],
    }
    report = {
        "duplicate_content_groups": [
            {"providers": ["A", "B", "C"]},
        ],
        "sources": [
            {"name": "A", "targets": ["AI 服务"], "behavior": "classical", "declared_format": "yaml"},
            {"name": "B", "targets": ["AI 服务"], "behavior": "classical", "declared_format": "yaml"},
            {"name": "C", "targets": ["AI 服务"], "behavior": "classical", "declared_format": "text"},
        ],
    }

    optimized, changes = apply_safe_duplicate_pruning(config, report)

    assert list(optimized["rule-providers"]) == ["B", "C"]
    assert optimized["rules"] == ["RULE-SET,B,AI 服务", "RULE-SET,C,AI 服务", "MATCH,兜底"]
    assert changes == {"groups": 1, "providers_removed": 1, "rules_removed": 1}


def test_apply_verified_unusable_source_pruning_removes_only_explicit_names() -> None:
    config = {
        "rule-providers": {
            "Dead": {"url": "https://example.invalid/dead"},
            "Transient": {"url": "https://example.invalid/transient"},
            "Healthy": {"url": "https://example.invalid/healthy"},
        },
        "rules": [
            "RULE-SET,Dead,DIRECT",
            "RULE-SET,Transient,DIRECT",
            "RULE-SET,Healthy,DIRECT",
            "MATCH,兜底",
        ],
    }

    optimized, changes = apply_verified_unusable_source_pruning(config, {"Dead"})

    assert list(optimized["rule-providers"]) == ["Transient", "Healthy"]
    assert optimized["rules"] == [
        "RULE-SET,Transient,DIRECT",
        "RULE-SET,Healthy,DIRECT",
        "MATCH,兜底",
    ]
    assert changes == {"providers_removed": 1, "rules_removed": 1}


def test_reorder_rules_by_target_priority_keeps_rule_families_stable() -> None:
    rules = [
        "RULE-SET,Global,默认代理",
        "RULE-SET,Google,Google",
        "RULE-SET,China,DIRECT",
        "GEOIP,CN,默认代理",
        "GEOSITE,google,Google",
        "DOMAIN-SUFFIX,cn,DIRECT",
        "MATCH,兜底",
    ]

    reordered = reorder_rules_by_target_priority(rules)

    assert reordered == [
        "RULE-SET,China,DIRECT",
        "RULE-SET,Google,Google",
        "RULE-SET,Global,默认代理",
        "DOMAIN-SUFFIX,cn,DIRECT",
        "GEOSITE,google,Google",
        "GEOIP,CN,默认代理",
        "MATCH,兜底",
    ]


def test_find_ordered_entry_conflicts_identifies_effective_winner_and_risk_direction() -> None:
    records = [
        {
            "name": "Ads",
            "entries": frozenset({"domain,shared.example"}),
            "routes": [{"index": 5, "target": "REJECT"}],
        },
        {
            "name": "Direct",
            "entries": frozenset({"domain,shared.example"}),
            "routes": [{"index": 10, "target": "DIRECT"}],
        },
    ]

    conflicts = find_ordered_entry_conflicts(records)

    assert conflicts["ordered_conflict_entry_count"] == 1
    assert conflicts["transition_pairs"] == {"REJECT -> DIRECT": 1}
    assert conflicts["risk_directions"] == {"reject_overrides_direct": 1}
    assert conflicts["transition_examples"]["REJECT -> DIRECT"][0]["entry"] == "domain,shared.example"
    assert conflicts["examples"][0]["winner"] == {
        "provider": "Ads",
        "target": "REJECT",
        "rule_index": 5,
    }


@pytest.mark.asyncio
async def test_audit_rule_sources_isolates_fetch_failures_and_summarizes_results() -> None:
    providers = {
        "OpenAI": {"url": "https://rules.example/openai.yaml", "format": "yaml", "behavior": "classical"},
        "Broken": {"url": "https://rules.example/broken.txt", "format": "text", "behavior": "domain"},
    }

    async def fetch(url: str) -> dict:
        if url.endswith("broken.txt"):
            raise RuntimeError("timeout")
        return {
            "status_code": 200,
            "final_url": url,
            "content_type": "text/yaml",
            "content": b"payload:\n  - DOMAIN-SUFFIX,openai.com\n",
            "elapsed_ms": 12,
        }

    report = await audit_rule_sources(providers, {"OpenAI": ["AI 服务"]}, fetch=fetch, concurrency=2)

    assert report["summary"] == {"total": 2, "valid": 1, "invalid": 0, "failed": 1}
    assert report["sources"][0]["name"] == "Broken"
    assert report["sources"][0]["error"] == "timeout"
    assert "content" not in report["sources"][1]
