from app.core.policy_analyzer import analyze_workspace
from app.core.policy_workspace import compile_mihomo_config
from app.core.provider_egress import (
    apply_provider_egress,
    immutable_github_mirror_url,
    needs_egress,
    resolve_egress_group,
)
from app.ir import PolicyWorkspace, ProxyGroup, RuleProvider


def _provider(url: str, **extra: object) -> dict:
    return {"type": "http", "behavior": "domain", "format": "mrs", "url": url, **extra}


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def test_immutable_github_mirror_url_rewrites_both_raw_file_shapes() -> None:
    assert immutable_github_mirror_url(
        f"https://raw.githubusercontent.com/owner/repo/{COMMIT}/rules/a.mrs"
    ) == f"https://cdn.jsdelivr.net/gh/owner/repo@{COMMIT}/rules/a.mrs"
    assert immutable_github_mirror_url(
        f"https://github.com/owner/repo/raw/{COMMIT}/rules/a.mrs"
    ) == f"https://cdn.jsdelivr.net/gh/owner/repo@{COMMIT}/rules/a.mrs"


def test_immutable_github_mirror_url_rejects_mutable_or_non_file_urls() -> None:
    assert immutable_github_mirror_url("https://raw.githubusercontent.com/owner/repo/main/a.mrs") is None
    assert immutable_github_mirror_url("https://github.com/owner/repo/raw/v1.2.0/a.mrs") is None
    assert immutable_github_mirror_url(
        f"https://github.com/owner/repo/releases/download/{COMMIT}/a.mrs"
    ) is None
    assert immutable_github_mirror_url(
        f"https://raw.githubusercontent.com/owner/repo/{COMMIT}/a.mrs?download=1"
    ) is None
    assert immutable_github_mirror_url(
        f"https://raw.githubusercontent.com.evil/owner/repo/{COMMIT}/a.mrs"
    ) is None


def test_github_hosted_provider_needs_egress() -> None:
    assert needs_egress(_provider("https://raw.githubusercontent.com/x/y/main/a.mrs"))
    assert needs_egress(_provider("https://github.com/x/y/raw/release/a.mrs"))


def test_mirrored_and_already_routed_providers_stay_direct() -> None:
    assert not needs_egress(_provider("https://testingcf.jsdelivr.net/gh/x/y/a.mrs"))
    assert not needs_egress(_provider("https://ruleset.skk.moe/Clash/domainset/ai.txt"))
    assert not needs_egress(_provider("https://github.com/x/y/raw/a.mrs", proxy="默认代理"))


def test_egress_group_prefers_dedicated_rule_update_group() -> None:
    assert resolve_egress_group(["默认代理", "规则更新", "自动选择"]) == "规则更新"
    assert resolve_egress_group(["默认代理", "自动选择", "手动选择"]) == "自动选择"
    assert resolve_egress_group(["默认代理", "手动选择"]) == "默认代理"


def test_egress_group_is_none_when_no_candidate_exists() -> None:
    assert resolve_egress_group(["手动选择"]) is None


def test_env_override_can_pin_or_disable_egress(monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_PROVIDER_EGRESS", "DIRECT")
    assert resolve_egress_group(["自动选择"]) is None

    monkeypatch.setenv("SUBFLOW_PROVIDER_EGRESS", "手动选择")
    assert resolve_egress_group(["自动选择", "手动选择"]) == "手动选择"


def test_apply_provider_egress_rewrites_only_stranded_providers() -> None:
    providers = {
        "gh": _provider("https://raw.githubusercontent.com/x/y/a.mrs"),
        "cdn": _provider("https://testingcf.jsdelivr.net/gh/x/y/a.mrs"),
    }

    rewritten = apply_provider_egress(providers, ["自动选择"])

    assert rewritten == ["gh"]
    assert providers["gh"]["proxy"] == "自动选择"
    assert providers["cdn"]["proxy"] == "DIRECT"


def test_apply_provider_egress_mirrors_without_a_group_and_preserves_explicit_proxy() -> None:
    pinned_url = f"https://raw.githubusercontent.com/x/y/{COMMIT}/a.mrs"
    providers = {
        "pinned": _provider(pinned_url),
        "explicit": _provider(pinned_url, proxy=""),
        "branch": _provider("https://raw.githubusercontent.com/x/y/main/a.mrs"),
    }

    rewritten = apply_provider_egress(providers, ["手动选择"])

    assert rewritten == []
    assert providers["pinned"]["url"] == f"https://cdn.jsdelivr.net/gh/x/y@{COMMIT}/a.mrs"
    assert providers["pinned"]["proxy"] == "DIRECT"
    assert providers["explicit"] == _provider(pinned_url, proxy="")
    assert providers["branch"] == _provider("https://raw.githubusercontent.com/x/y/main/a.mrs")


def test_compiled_mihomo_config_routes_github_providers_through_a_group() -> None:
    config = {
        "proxy-groups": [
            {"name": "默认代理", "type": "select", "proxies": ["DIRECT"]},
            {"name": "规则更新", "type": "select", "proxies": ["自动选择"]},
            {"name": "自动选择", "type": "url-test", "proxies": ["A"]},
        ],
        "rule-providers": {
            "immutable-raw": _provider(
                f"https://raw.githubusercontent.com/x/y/{COMMIT}/rules/a.mrs"
            ),
            "immutable-github": _provider(
                f"https://github.com/x/y/raw/{COMMIT}/rules/b.mrs"
            ),
            "branch": _provider("https://github.com/x/y/raw/release/a.mrs"),
            "explicit": _provider(
                f"https://raw.githubusercontent.com/x/y/{COMMIT}/rules/c.mrs",
                proxy="手动选择",
            ),
            "cdn": _provider("https://cdn.jsdelivr.net/gh/x/y/a.mrs"),
        },
        "rules": ["RULE-SET,branch,自动选择", "MATCH,自动选择"],
    }

    compiled = compile_mihomo_config(config, [])

    assert compiled["rule-providers"]["immutable-raw"]["url"] == (
        f"https://cdn.jsdelivr.net/gh/x/y@{COMMIT}/rules/a.mrs"
    )
    assert compiled["rule-providers"]["immutable-raw"]["proxy"] == "DIRECT"
    assert compiled["rule-providers"]["immutable-github"]["url"] == (
        f"https://cdn.jsdelivr.net/gh/x/y@{COMMIT}/rules/b.mrs"
    )
    assert compiled["rule-providers"]["immutable-github"]["proxy"] == "DIRECT"
    assert compiled["rule-providers"]["branch"]["url"] == "https://github.com/x/y/raw/release/a.mrs"
    assert compiled["rule-providers"]["branch"]["proxy"] == "规则更新"
    assert compiled["rule-providers"]["explicit"]["url"] == (
        f"https://raw.githubusercontent.com/x/y/{COMMIT}/rules/c.mrs"
    )
    assert compiled["rule-providers"]["explicit"]["proxy"] == "手动选择"
    assert compiled["rule-providers"]["cdn"]["proxy"] == "DIRECT"
    assert config["rule-providers"]["immutable-raw"]["url"].startswith(
        "https://raw.githubusercontent.com/"
    )


def _workspace(providers: list[RuleProvider], groups: list[str]) -> PolicyWorkspace:
    return PolicyWorkspace(
        target="mihomo",
        proxies=[],
        proxy_groups=[ProxyGroup(name=name, type="select", members=["DIRECT"]) for name in groups],
        rules=[],
        rule_providers=providers,
        settings={},
    )


def test_analyzer_reports_stranded_providers_when_no_egress_group_exists() -> None:
    provider = RuleProvider(
        name="gh",
        type="http",
        format="mrs",
        url="https://raw.githubusercontent.com/x/y/a.mrs",
        raw=_provider("https://raw.githubusercontent.com/x/y/a.mrs"),
    )

    stranded = {f.code for f in analyze_workspace(_workspace([provider], ["手动选择"]))}
    routed = {f.code for f in analyze_workspace(_workspace([provider], ["自动选择"]))}

    assert "provider_unreachable" in stranded
    assert "provider_unreachable" not in routed


def test_analyzer_does_not_report_a_provider_that_compilation_can_mirror() -> None:
    url = f"https://raw.githubusercontent.com/x/y/{COMMIT}/a.mrs"
    provider = RuleProvider(
        name="pinned",
        type="http",
        format="mrs",
        url=url,
        raw=_provider(url),
    )

    findings = {f.code for f in analyze_workspace(_workspace([provider], ["手动选择"]))}

    assert "provider_unreachable" not in findings


def test_analyzer_flags_provider_count_budget_and_mrs_core_requirement() -> None:
    providers = [
        RuleProvider(
            name=f"p{index}",
            type="http",
            format="mrs",
            url=f"https://cdn.jsdelivr.net/gh/x/y/{index}.mrs",
            raw=_provider(f"https://cdn.jsdelivr.net/gh/x/y/{index}.mrs"),
        )
        for index in range(201)
    ]

    findings = {f.code: f for f in analyze_workspace(_workspace(providers, ["自动选择"]))}

    assert findings["provider_count_budget"].severity == "warning"
    assert findings["provider_requires_mihomo_core"].severity == "info"


def test_runtime_feasibility_findings_never_block_publication() -> None:
    providers = [
        RuleProvider(
            name="gh",
            type="http",
            format="mrs",
            url="https://github.com/x/y/raw/a.mrs",
            raw=_provider("https://github.com/x/y/raw/a.mrs"),
        )
    ]

    findings = analyze_workspace(_workspace(providers, ["手动选择"]))

    assert findings
    assert not [f for f in findings if f.severity == "error"]
