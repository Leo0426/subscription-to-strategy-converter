from __future__ import annotations

from app.core.rule_consolidation import (
    apply_consolidation_plan,
    build_consolidation_plan,
    rank_key,
    service_family,
)


def test_service_family_collapses_copy_numbered_names() -> None:
    assert service_family("Netflix-7") == "netflix"
    assert service_family("netflix_2") == "netflix"
    assert service_family("TelegramIP") == "telegramip"
    assert service_family("telegram_ip-4") == "telegramip"
    assert service_family("China") == "china"


def test_rank_key_prefers_dual_target_trusted_direct_then_coverage() -> None:
    mrs_trusted = {
        "name": "mrs",
        "url": "https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/x.mrs",
        "declared_format": "mrs",
        "byte_count": 100,
        "entries": frozenset(),
    }
    text_untrusted = {
        "name": "text-untrusted",
        "url": "https://example.com/rules.txt",
        "declared_format": "text",
        "byte_count": 100,
        "entries": frozenset({"domain,a.example"}),
    }
    text_trusted_small = {
        "name": "text-trusted-small",
        "url": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/x.yaml",
        "declared_format": "yaml",
        "byte_count": 100,
        "entries": frozenset({"domain,a.example"}),
    }
    text_trusted_big = {
        "name": "text-trusted-big",
        "url": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/y.yaml",
        "declared_format": "yaml",
        "byte_count": 999,
        "entries": frozenset({"domain,a.example", "domain,b.example"}),
    }
    proxied = {
        "name": "proxied",
        "url": "https://gh-proxy.com/https://raw.githubusercontent.com/blackmatrix7/x/master/z.yaml",
        "declared_format": "yaml",
        "byte_count": 100,
        "entries": frozenset({"domain,a.example", "domain,b.example", "domain,c.example"}),
    }

    ranked = sorted([mrs_trusted, text_untrusted, text_trusted_small, text_trusted_big, proxied], key=rank_key)

    assert [r["name"] for r in ranked] == [
        "text-trusted-big",
        "text-trusted-small",
        "proxied",
        "text-untrusted",
        "mrs",
    ]


def test_build_consolidation_plan_reports_retained_removed_and_coverage_loss() -> None:
    records = [
        {
            "name": "Netflix",
            "url": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/netflix.yaml",
            "declared_format": "yaml",
            "targets": ["流媒体"],
            "byte_count": 1000,
            "entries": frozenset({"domain,netflix.com", "domain,nflxvideo.net"}),
        },
        {
            "name": "Netflix-2",
            "url": "https://example.com/netflix.txt",
            "declared_format": "text",
            "targets": ["流媒体"],
            "byte_count": 500,
            "entries": frozenset({"domain,netflix.com", "domain,fast.com"}),
        },
        {
            "name": "Spotify",
            "url": "https://example.com/spotify.txt",
            "declared_format": "text",
            "targets": ["流媒体"],
            "byte_count": 200,
            "entries": frozenset({"domain,spotify.com"}),
        },
    ]

    plan = build_consolidation_plan(records)

    assert plan["summary"] == {
        "provider_count_before": 3,
        "provider_count_after": 2,
        "removed": 1,
        "bytes_removed": 500,
        "multi_member_units": 1,
    }
    unit = plan["units"][0]
    assert unit["family"] == "netflix"
    assert unit["retained"]["name"] == "Netflix"
    assert unit["removed"][0]["name"] == "Netflix-2"
    assert unit["removed"][0]["lost_entry_count"] == 1
    assert unit["removed"][0]["lost_examples"] == ["domain,fast.com"]


def test_build_consolidation_plan_separates_same_family_with_different_targets() -> None:
    records = [
        {"name": "Apple", "url": "https://a.example/x", "declared_format": "text", "targets": ["Apple"], "byte_count": 1, "entries": frozenset()},
        {"name": "Apple-2", "url": "https://a.example/y", "declared_format": "text", "targets": ["DIRECT"], "byte_count": 1, "entries": frozenset()},
    ]

    plan = build_consolidation_plan(records)

    assert plan["summary"]["removed"] == 0
    assert plan["units"] == []


def test_apply_consolidation_plan_removes_providers_and_rules_per_batch() -> None:
    config = {
        "rule-providers": {
            "Netflix": {"url": "https://a.example/n1"},
            "Netflix-2": {"url": "https://a.example/n2"},
            "Telegram": {"url": "https://a.example/t1"},
            "Telegram-2": {"url": "https://a.example/t2"},
        },
        "rules": [
            "RULE-SET,Netflix,流媒体",
            "RULE-SET,Netflix-2,流媒体",
            "RULE-SET,Telegram,社交通讯",
            "RULE-SET,Telegram-2,社交通讯",
            "MATCH,兜底",
        ],
    }
    plan = {
        "units": [
            {"family": "netflix", "targets": ["流媒体"], "retained": {"name": "Netflix", "url": ""}, "removed": [{"name": "Netflix-2"}]},
            {"family": "telegram", "targets": ["社交通讯"], "retained": {"name": "Telegram", "url": ""}, "removed": [{"name": "Telegram-2"}]},
        ]
    }

    batch, changes = apply_consolidation_plan(config, plan, families={"netflix"})

    assert list(batch["rule-providers"]) == ["Netflix", "Telegram", "Telegram-2"]
    assert "RULE-SET,Netflix-2,流媒体" not in batch["rules"]
    assert "RULE-SET,Telegram-2,社交通讯" in batch["rules"]
    assert changes == {"providers_removed": 1, "rules_removed": 1}

    everything, changes_all = apply_consolidation_plan(config, plan)

    assert list(everything["rule-providers"]) == ["Netflix", "Telegram"]
    assert changes_all == {"providers_removed": 2, "rules_removed": 2}
