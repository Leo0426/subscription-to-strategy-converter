from app.core.policy_analyzer import analyze_workspace
from app.core.policy_workspace import compile_mihomo_config
from app.core.provider_egress import apply_provider_egress, needs_egress, resolve_egress_group
from app.ir import PolicyWorkspace, ProxyGroup, RuleProvider


def _provider(url: str, **extra: object) -> dict:
    return {"type": "http", "behavior": "domain", "format": "mrs", "url": url, **extra}


def test_github_hosted_provider_needs_egress() -> None:
    assert needs_egress(_provider("https://raw.githubusercontent.com/x/y/main/a.mrs"))
    assert needs_egress(_provider("https://github.com/x/y/raw/release/a.mrs"))


def test_mirrored_and_already_routed_providers_stay_direct() -> None:
    assert not needs_egress(_provider("https://testingcf.jsdelivr.net/gh/x/y/a.mrs"))
    assert not needs_egress(_provider("https://ruleset.skk.moe/Clash/domainset/ai.txt"))
    assert not needs_egress(_provider("https://github.com/x/y/raw/a.mrs", proxy="默认代理"))


def test_egress_group_prefers_url_test_group() -> None:
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
    assert "proxy" not in providers["cdn"]


def test_compiled_mihomo_config_routes_github_providers_through_a_group() -> None:
    config = {
        "proxy-groups": [{"name": "自动选择", "type": "url-test", "proxies": ["A"]}],
        "rule-providers": {
            "gh": _provider("https://github.com/x/y/raw/release/a.mrs"),
            "cdn": _provider("https://cdn.jsdelivr.net/gh/x/y/a.mrs"),
        },
        "rules": ["RULE-SET,gh,自动选择", "MATCH,自动选择"],
    }

    compiled = compile_mihomo_config(config, [])

    assert compiled["rule-providers"]["gh"]["proxy"] == "自动选择"
    assert "proxy" not in compiled["rule-providers"]["cdn"]


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
