import httpx
import pytest

from app.core.rule_source_audit import (
    COLD_START_BYTE_BUDGET,
    PublicRuleSourceFetcher,
    apply_safe_duplicate_pruning,
    apply_verified_unusable_source_pruning,
    audit_leo_rule_sources,
    audit_snapshot_matches_template,
    audit_rule_sources,
    extract_normalized_rule_entries,
    find_entry_target_conflicts,
    find_high_overlap_pairs,
    find_ordered_entry_conflicts,
    inspect_rule_source_content,
    reorder_rules_by_target_priority,
    score_rule_source_report,
    supply_chain_facts,
    template_audit_metadata,
    write_public_audit_snapshot,
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

    assert score["total"] == pytest.approx(85.39, abs=0.01)
    assert score["dimensions"]["availability"] == 32.0
    assert "semantic_accuracy" in score["unmeasured"]
    assert "content_drift" in score["unmeasured"]
    assert score["grade"] == "B"


def test_supply_chain_facts_classifies_origin_pinning_and_intermediaries() -> None:
    branch = supply_chain_facts(
        "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/refs/heads/meta/geo/geosite/115.mrs"
    )
    tag = supply_chain_facts(
        "https://raw.githubusercontent.com/owner/repo/refs/tags/v1.2.0/rules.yaml"
    )
    sha = supply_chain_facts(
        "https://raw.githubusercontent.com/owner/repo/0123456789abcdef0123456789abcdef01234567/rules.yaml"
    )
    cdn_branch = supply_chain_facts("https://testingcf.jsdelivr.net/gh/owner/repo@master/rules.txt")
    cdn_version = supply_chain_facts("https://cdn.jsdelivr.net/gh/owner/repo@1.2.3/rules.txt")
    proxied = supply_chain_facts(
        "https://gh-proxy.com/https://raw.githubusercontent.com/owner/repo/master/rules.txt"
    )
    first_party = supply_chain_facts("https://ruleset.skk.moe/Clash/domainset/reject.txt")
    ghcom_raw_branch = supply_chain_facts("https://github.com/owner/repo/raw/refs/heads/master/rules.txt")
    ghcom_raw_sha = supply_chain_facts(
        "https://github.com/owner/repo/raw/0123456789abcdef0123456789abcdef01234567/rules.txt"
    )

    assert branch == {"upstream": "github:MetaCubeX", "pinned": False, "via_intermediary": False}
    assert tag["pinned"] is True and tag["via_intermediary"] is False
    assert sha["pinned"] is True
    assert cdn_branch == {"upstream": "github:owner", "pinned": False, "via_intermediary": False}
    assert cdn_version["pinned"] is True
    assert proxied == {"upstream": "github:owner", "pinned": False, "via_intermediary": True}
    assert first_party == {"upstream": "ruleset.skk.moe", "pinned": False, "via_intermediary": False}
    assert ghcom_raw_branch["pinned"] is False
    assert ghcom_raw_sha["pinned"] is True


def test_score_rule_source_report_scores_supply_chain_and_cold_start_cost() -> None:
    half_budget = COLD_START_BYTE_BUDGET // 2
    report = {
        "summary": {"total": 4, "valid": 4, "invalid": 0, "failed": 0},
        "duplicate_content_groups": [],
        "entry_target_conflicts": {},
        "sources": [
            {
                "name": "branch-a",
                "url": "https://raw.githubusercontent.com/owner/repo/master/a.yaml",
                "byte_count": half_budget,
            },
            {
                "name": "branch-b",
                "url": "https://raw.githubusercontent.com/owner/repo/master/b.yaml",
                "byte_count": half_budget,
            },
            {
                "name": "proxied",
                "url": "https://gh-proxy.com/https://raw.githubusercontent.com/owner/repo/master/c.yaml",
                "byte_count": half_budget,
            },
            {
                "name": "pinned",
                "url": "https://raw.githubusercontent.com/owner/repo/refs/tags/v1.0.0/d.yaml",
                "byte_count": half_budget,
            },
        ],
    }

    score = score_rule_source_report(report)

    # direct 3/4, pinned 1/4 → 15 × (0.375 + 0.125); bytes at 2× budget → 10 × (0.5 + 0.25)
    assert score["dimensions"]["supply_chain"] == pytest.approx(7.5)
    assert score["dimensions"]["cold_start_cost"] == pytest.approx(7.5)
    assert score["evidence"]["upstream_count"] == 1
    assert score["evidence"]["via_intermediary_count"] == 1
    assert score["evidence"]["unpinned_count"] == 3
    assert score["evidence"]["total_bytes"] == COLD_START_BYTE_BUDGET * 2


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


def test_find_ordered_entry_conflicts_uses_an_earlier_inline_domain_override() -> None:
    entry = "domain-suffix,crashlytics.com"
    records = [
        {
            "name": "Apple",
            "entries": frozenset({entry}),
            "routes": [{"index": 47, "target": "Apple"}],
        },
        {
            "name": "Google",
            "entries": frozenset({entry}),
            "routes": [{"index": 49, "target": "Google"}],
        },
    ]
    inline_rule = "DOMAIN-SUFFIX,crashlytics.com,Google"

    conflicts = find_ordered_entry_conflicts(
        records,
        inline_rules=[None] * 46 + [inline_rule],
    )

    assert conflicts["ordered_conflict_entry_count"] == 1
    assert conflicts["transition_pairs"] == {"Google -> Apple": 1}
    assert conflicts["affected_providers"] == ["Apple", "Google"]
    winner = conflicts["examples"][0]["winner"]
    assert winner["provider"] == "<inline-rule>"
    assert winner["target"] == "Google"
    assert winner["rule_index"] == 46
    assert winner["rule"] == inline_rule


@pytest.mark.parametrize(
    ("provider_entry", "inline_rule"),
    [
        ("domain,api.example.com", "DOMAIN,api.example.com,DIRECT"),
        ("domain-suffix,api.example.com", "DOMAIN-SUFFIX,example.com,DIRECT"),
        ("domain-suffix,crashlytics.com", "DOMAIN-KEYWORD,crash,DIRECT"),
        ("ip-cidr,10.1.0.0/16", "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve"),
        ("ip-cidr6,2001:db8:1::/48", "IP-CIDR6,2001:db8::/32,DIRECT,no-resolve"),
        ("geoip,cn", "GEOIP,CN,DIRECT,no-resolve"),
    ],
)
def test_ordered_conflicts_include_supported_inline_rule_types(
    provider_entry: str,
    inline_rule: str,
) -> None:
    records = [
        {
            "name": "Provider",
            "entries": frozenset({provider_entry}),
            "routes": [{"index": 5, "target": "默认代理"}],
        }
    ]

    conflicts = find_ordered_entry_conflicts(records, inline_rules=[inline_rule])

    assert conflicts["ordered_conflict_entry_count"] == 1
    assert conflicts["examples"][0]["winner"]["target"] == "DIRECT"
    assert conflicts["examples"][0]["shadowed"][0]["provider"] == "Provider"


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


@pytest.mark.asyncio
async def test_audit_rule_sources_records_hidden_ip_types_in_classical_sources() -> None:
    providers = {
        "Mixed": {
            "url": "https://rules.example/mixed.yaml",
            "format": "yaml",
            "behavior": "classical",
        }
    }

    async def fetch(url: str) -> dict:
        return {
            "status_code": 200,
            "final_url": url,
            "content_type": "text/yaml",
            "content": (
                b"payload:\n"
                b"  - DOMAIN-SUFFIX,example.com\n"
                b"  - IP-CIDR,192.0.2.0/24,no-resolve\n"
                b"  - IP-CIDR6,2001:db8::/32\n"
                b"  - IP-SUFFIX,0.0.0.1/24\n"
                b"  - AND,((IP-CIDR,198.51.100.0/24),(DOMAIN,example.com))\n"
            ),
            "elapsed_ms": 1,
        }

    report = await audit_rule_sources(
        providers,
        {"Mixed": ["流媒体"]},
        fetch=fetch,
    )

    assert report["sources"][0]["rule_type_counts"] == {
        "AND": 1,
        "DOMAIN-SUFFIX": 1,
        "IP-CIDR": 1,
        "IP-CIDR6": 1,
        "IP-SUFFIX": 1,
    }
    assert report["sources"][0]["resolving_ip_rule_count"] == 3


@pytest.mark.asyncio
async def test_audit_rule_sources_does_not_report_uninspected_mrs_as_ip_safe() -> None:
    providers = {
        "Binary": {
            "url": "https://rules.example/ip.mrs",
            "format": "mrs",
            "behavior": "ipcidr",
        }
    }

    async def fetch(url: str) -> dict:
        return {
            "status_code": 200,
            "final_url": url,
            "content_type": "application/octet-stream",
            "content": b"MRS\x00\x01binary",
            "elapsed_ms": 1,
        }

    report = await audit_rule_sources(providers, {}, fetch=fetch)
    source = report["sources"][0]

    assert source["rule_type_counts"] is None
    assert source["resolving_ip_rule_count"] is None


@pytest.mark.asyncio
async def test_audit_leo_rule_sources_records_template_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.rule_source_audit.load_template",
        lambda template_id: {"rule-providers": {}, "rules": []},
    )

    report = await audit_leo_rule_sources()

    assert report["template"]["id"] == "local:community_templates/leo/leo.yaml"
    assert len(report["template"]["sha256"]) == 64
    assert report["template"]["provider_count"] == 0
    assert report["template"]["rule_count"] == 0


@pytest.mark.asyncio
async def test_audit_leo_rule_sources_passes_inline_template_rules(monkeypatch) -> None:
    rules = ["DOMAIN-SUFFIX,crashlytics.com,Google"]
    captured: dict = {}

    monkeypatch.setattr(
        "app.core.rule_source_audit.load_template",
        lambda template_id: {"rule-providers": {}, "rules": rules},
    )

    async def fake_audit_rule_sources(providers, targets, **kwargs):
        captured.update(kwargs)
        return {"summary": {"total": 0, "valid": 0, "invalid": 0, "failed": 0}}

    monkeypatch.setattr(
        "app.core.rule_source_audit.audit_rule_sources",
        fake_audit_rule_sources,
    )

    await audit_leo_rule_sources()

    assert captured["inline_rules"] == rules


@pytest.mark.asyncio
async def test_public_fetcher_retries_transient_transport_errors(monkeypatch) -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("transient", request=request)
        return httpx.Response(200, content=b"payload", request=request)

    async def skip_public_url_validation(url: str) -> None:
        return None

    fetcher = PublicRuleSourceFetcher()
    fetcher._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(fetcher, "_validate_public_url", skip_public_url_validation)
    try:
        result = await fetcher.fetch("https://rules.example/list.txt")
    finally:
        await fetcher._client.aclose()

    assert attempts == 3
    assert result["status_code"] == 200
    assert result["content"] == b"payload"


def test_template_audit_metadata_detects_any_template_content_change(tmp_path) -> None:
    template_path = tmp_path / "leo.yaml"
    template_path.write_text("rules:\n  - MATCH,DIRECT\n", encoding="utf-8")
    report = {
        "template": template_audit_metadata(
            {"rule-providers": {"A": {}}, "rules": ["MATCH,DIRECT"]},
            template_path,
        )
    }

    assert report["template"]["provider_count"] == 1
    assert report["template"]["rule_count"] == 1
    assert audit_snapshot_matches_template(report, template_path) is True

    template_path.write_text("rules:\n  - MATCH,Proxy\n", encoding="utf-8")

    assert audit_snapshot_matches_template(report, template_path) is False


def _report_for_template(template_path, *, total: int, valid: int, failed: int) -> dict:
    return {
        "summary": {"total": total, "valid": valid, "invalid": 0, "failed": failed},
        "sources": [],
        "template": template_audit_metadata(
            {"rule-providers": {}, "rules": []},
            template_path,
        ),
    }


def test_write_public_audit_snapshot_refuses_a_fully_failed_audit(tmp_path) -> None:
    template_path = tmp_path / "leo.yaml"
    template_path.write_text("rules: []\n", encoding="utf-8")
    all_failed = _report_for_template(template_path, total=508, valid=0, failed=508)
    target = tmp_path / "audit.json"

    with pytest.raises(ValueError, match="audit-environment failure"):
        write_public_audit_snapshot(all_failed, target, template_path=template_path)

    assert not target.exists()


def test_write_public_audit_snapshot_publishes_when_failures_are_a_minority(tmp_path) -> None:
    template_path = tmp_path / "leo.yaml"
    template_path.write_text("rules: []\n", encoding="utf-8")
    report = _report_for_template(template_path, total=10, valid=8, failed=2)
    target = tmp_path / "audit.json"

    assert write_public_audit_snapshot(report, target, template_path=template_path) == target
    assert target.exists()


def test_write_public_audit_snapshot_refuses_stale_template(tmp_path) -> None:
    template_path = tmp_path / "leo.yaml"
    template_path.write_text("rules: []\n", encoding="utf-8")
    report = _report_for_template(template_path, total=1, valid=1, failed=0)
    template_path.write_text("rules:\n  - MATCH,DIRECT\n", encoding="utf-8")
    target = tmp_path / "audit.json"

    with pytest.raises(ValueError, match="different template content"):
        write_public_audit_snapshot(report, target, template_path=template_path)

    assert not target.exists()


def test_write_public_audit_snapshot_refuses_a_majority_failed_audit(tmp_path) -> None:
    template_path = tmp_path / "leo.yaml"
    template_path.write_text("rules: []\n", encoding="utf-8")
    degraded = _report_for_template(template_path, total=373, valid=171, failed=202)
    target = tmp_path / "audit.json"

    with pytest.raises(ValueError, match="audit-environment failure"):
        write_public_audit_snapshot(degraded, target, template_path=template_path)

    assert not target.exists()
