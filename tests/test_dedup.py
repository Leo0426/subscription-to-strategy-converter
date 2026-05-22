"""Tests for deduplication and merging logic across 策略包, 规则, and 依赖."""
from __future__ import annotations

import pytest

from app.core.policy_catalog import (
    _deduplicate_groups,
    _deduplicate_providers,
    _deduplicate_rules,
    _normalize_provider_url,
    _rule_sig,
)
from app.core.template_engine import _rule_key


# ── _normalize_provider_url ────────────────────────────────────────────────


def test_normalize_strips_single_mirror_prefix() -> None:
    url = "https://ghfast.top/raw.githubusercontent.com/foo/bar/file.yaml"
    assert _normalize_provider_url(url) == "raw.githubusercontent.com/foo/bar/file.yaml"


def test_normalize_strips_chained_mirror_prefixes() -> None:
    # Double-wrapped URL: ghproxy wrapping ghfast wrapping the real URL
    url = "https://mirror.ghproxy.com/https://ghfast.top/raw.githubusercontent.com/a/b/f.yaml"
    assert _normalize_provider_url(url) == "raw.githubusercontent.com/a/b/f.yaml"


def test_normalize_lowercases_result() -> None:
    url = "https://raw.githubusercontent.com/Foo/Bar/file.YAML"
    assert _normalize_provider_url(url) == "raw.githubusercontent.com/foo/bar/file.yaml"


def test_normalize_strips_trailing_slash() -> None:
    url = "https://raw.githubusercontent.com/foo/bar/"
    assert _normalize_provider_url(url) == "raw.githubusercontent.com/foo/bar"


# ── _rule_sig (policy_catalog) ─────────────────────────────────────────────


def test_rule_sig_normalizes_whitespace() -> None:
    r1 = {"raw": "RULE-SET,ai,PROXY", "target": "PROXY"}
    r2 = {"raw": "RULE-SET, ai , PROXY", "target": "PROXY"}
    assert _rule_sig(r1) == _rule_sig(r2)


def test_rule_sig_distinguishes_different_rules() -> None:
    r1 = {"raw": "RULE-SET,ai,PROXY", "target": "PROXY"}
    r2 = {"raw": "RULE-SET,ai,DIRECT", "target": "DIRECT"}
    assert _rule_sig(r1) != _rule_sig(r2)


# ── _rule_key (template_engine) ────────────────────────────────────────────


def test_rule_key_normalizes_whitespace_in_string_rule() -> None:
    assert _rule_key("DOMAIN-SUFFIX,google.com,PROXY") == _rule_key("DOMAIN-SUFFIX, google.com , PROXY")


def test_rule_key_sorts_dict_keys_for_consistency() -> None:
    r1 = {"proxy": "PROXY", "type": "DOMAIN-SUFFIX", "match": "google.com"}
    r2 = {"match": "google.com", "proxy": "PROXY", "type": "DOMAIN-SUFFIX"}
    assert _rule_key(r1) == _rule_key(r2)


def test_rule_key_distinguishes_different_rules() -> None:
    assert _rule_key("DOMAIN,a.com,PROXY") != _rule_key("DOMAIN,b.com,PROXY")


# ── _deduplicate_providers (依赖去重) ─────────────────────────────────────


def _make_provider(template: str, name: str, url: str, fmt: str = "yaml") -> dict:
    return {
        "id": f"{template}::provider::{name}",
        "template": template,
        "name": name,
        "url": url,
        "format": fmt,
        "behavior": "domain",
        "type": "http",
        "raw": {"url": url},
    }


def test_providers_with_same_url_are_merged() -> None:
    providers = [
        _make_provider("t1", "ads", "https://ghfast.top/raw.githubusercontent.com/owner/repo/ads.yaml"),
        _make_provider("t2", "ads", "https://raw.githubusercontent.com/owner/repo/ads.yaml"),
    ]
    canonical, alias_map = _deduplicate_providers(providers)
    assert len(canonical) == 1
    # Canonical should be the direct GitHub URL (higher quality)
    assert "raw.githubusercontent.com" in canonical[0]["url"]


def test_providers_with_different_urls_are_kept_separate() -> None:
    providers = [
        _make_provider("t1", "ads", "https://raw.githubusercontent.com/owner/repo/ads.yaml"),
        _make_provider("t2", "cn", "https://raw.githubusercontent.com/owner/repo/cn.yaml"),
    ]
    canonical, _ = _deduplicate_providers(providers)
    assert len(canonical) == 2


def test_providers_without_url_are_kept() -> None:
    provider = _make_provider("t1", "local", "")
    canonical, _ = _deduplicate_providers([provider])
    assert len(canonical) == 1


# ── _deduplicate_rules (规则去重) ──────────────────────────────────────────


def _make_rule(template: str, idx: int, raw: str, target: str) -> dict:
    return {
        "id": f"{template}::rule::{idx}",
        "template": template,
        "type": raw.split(",")[0],
        "match": raw.split(",")[1] if raw.count(",") >= 1 else "",
        "target": target,
        "provider": "",
        "text": raw,
        "raw": raw,
    }


def test_identical_rules_across_templates_are_deduplicated() -> None:
    rules = [
        _make_rule("t1", 0, "DOMAIN-SUFFIX,google.com,PROXY", "PROXY"),
        _make_rule("t2", 0, "DOMAIN-SUFFIX,google.com,PROXY", "PROXY"),
    ]
    result = _deduplicate_rules(rules, {})
    assert len(result) == 1


def test_whitespace_variant_rules_are_deduplicated() -> None:
    rules = [
        _make_rule("t1", 0, "RULE-SET,ads,REJECT", "REJECT"),
        _make_rule("t2", 0, "RULE-SET, ads , REJECT", "REJECT"),
    ]
    result = _deduplicate_rules(rules, {})
    assert len(result) == 1


def test_match_rules_are_filtered_out() -> None:
    rules = [
        _make_rule("t1", 0, "DOMAIN,a.com,PROXY", "PROXY"),
        {"id": "t1::rule::99", "template": "t1", "type": "MATCH", "match": "", "target": "DIRECT",
         "provider": "", "text": "MATCH,DIRECT", "raw": "MATCH,DIRECT"},
    ]
    result = _deduplicate_rules(rules, {})
    assert len(result) == 1
    assert result[0]["type"] != "MATCH"


# ── _deduplicate_groups (策略包去重) ──────────────────────────────────────


def _make_group(template: str, idx: int, name: str, gtype: str, raw: dict) -> dict:
    return {
        "id": f"{template}::group::{idx}",
        "template": template,
        "name": name,
        "type": gtype,
        "refs": [],
        "raw": {"name": name, "type": gtype, **raw},
    }


def test_same_name_and_type_groups_are_merged() -> None:
    groups = [
        _make_group("t1", 0, "香港", "url-test", {"proxies": ["node1", "node2"]}),
        _make_group("t2", 0, "香港", "url-test", {"proxies": ["node1"]}),
    ]
    result = _deduplicate_groups(groups)
    assert len(result) == 1
    # Should keep the group with more proxies (higher quality)
    assert len(result[0]["raw"]["proxies"]) == 2


def test_include_all_url_test_group_beats_static_list() -> None:
    # A url-test group with include-all should win over a small static list
    static = _make_group("t1", 0, "Auto", "url-test", {"proxies": ["a", "b", "c"], "url": "http://x"})
    dynamic = _make_group("t2", 0, "Auto", "url-test", {"include-all": True, "url": "http://x"})
    result = _deduplicate_groups([static, dynamic])
    assert len(result) == 1
    assert result[0]["raw"].get("include-all") is True


def test_groups_with_different_names_kept_separate() -> None:
    groups = [
        _make_group("t1", 0, "香港", "url-test", {}),
        _make_group("t1", 1, "日本", "url-test", {}),
    ]
    result = _deduplicate_groups(groups)
    assert len(result) == 2


def test_sources_field_lists_all_merged_origins() -> None:
    groups = [
        _make_group("t1", 0, "HK", "select", {}),
        _make_group("t2", 0, "HK", "select", {}),
    ]
    result = _deduplicate_groups(groups)
    assert len(result[0]["sources"]) == 2
