import json
from pathlib import Path

from app.core.policy_analyzer import analyze_workspace
from app.core.rule_source_audit import audit_snapshot_matches_template
from app.core.policy_workspace import compile_mihomo_config, config_to_workspace
from app.core.renderer import render_yaml
from app.core.template_engine import LEO_TEMPLATE_ID, apply_template, load_template
from app.ir import ProxyNode
from app.models.strategy import SelectedPolicy


_LEO_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[1] / "community_templates" / "leo" / "leo.yaml"
)
_LEO_AUDIT_PATH = _LEO_TEMPLATE_PATH.with_name("audit.json")
_CORE_PROVIDER_NAMES = {
    "ai-4",
    "Claude",
    "GitHub-5",
    "Apple-4",
    "Google-2",
    "Microsoft-6",
    "YouTube-6",
    "Telegram",
}


def _node(name: str) -> ProxyNode:
    return ProxyNode(name=name, protocol="ss", server="proxy.example.com", port=443)


def _group(config: dict, name: str) -> dict:
    return next(group for group in config["proxy-groups"] if group["name"] == name)


def _fixed_144_nodes() -> list[ProxyNode]:
    # Keep the 144-node stress scale with both SG nodes and a conservative
    # 23-member US AI pool, without depending on the live subscription shape.
    regions = (("香港", 31), ("美国", 23), ("新加坡", 23), ("其他", 67))
    nodes: list[ProxyNode] = []
    for region, count in regions:
        for index in range(1, count + 1):
            nodes.append(
                ProxyNode(
                    name=f"{region} {index:03d}",
                    protocol="ss",
                    server=f"node-{len(nodes) + 1}.example.com",
                    port=443,
                )
            )
    return nodes


def test_leo_materializes_subscription_backed_groups_from_current_nodes() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(template, [_node("香港 01"), _node("US01"), _node("日本 01")])

    assert "Leo订阅" not in config.get("proxy-providers", {})
    assert _group(config, "自动选择")["proxies"] == ["香港 01", "US01", "日本 01"]
    assert "use" not in _group(config, "自动选择")


def test_leo_keeps_only_core_rule_providers() -> None:
    template = load_template(LEO_TEMPLATE_ID)

    assert set(template["rule-providers"]) == _CORE_PROVIDER_NAMES


def test_leo_lightweight_shape_and_generated_footprint() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    nodes = _fixed_144_nodes()
    generated = apply_template(template, nodes)
    compiled = compile_mihomo_config(generated, nodes)
    groups = compiled["proxy-groups"]
    rules = compiled["rules"]

    assert _LEO_TEMPLATE_PATH.stat().st_size <= 13 * 1024
    assert len(template["rule-providers"]) == 8
    assert len(template["proxy-groups"]) == 14
    assert len(template["rules"]) <= 150
    assert len(groups) == 14
    assert sum(group["type"] == "url-test" for group in groups) == 2
    assert sum(len(group.get("proxies", [])) for group in groups) <= 380
    assert sum(
        len(group.get("proxies", []))
        for group in groups
        if group.get("url")
    ) <= 200
    # Preserve the global automatic fallback: removing it would save roughly
    # 1.5 KiB, but would trade away useful cross-region recovery for a cosmetic
    # size target.  The previous 144-node artifact was over 84 KiB.
    assert len(render_yaml(compiled).encode("utf-8")) <= 34 * 1024
    assert len(rules) <= 150

    provider_rules = [
        rule
        for rule in rules
        if isinstance(rule, str) and rule.startswith("RULE-SET,")
    ]
    assert len(provider_rules) == 8
    referenced_providers = {rule.split(",", 2)[1].strip() for rule in provider_rules}
    assert referenced_providers == set(compiled["rule-providers"])

    dangling_codes = {
        "missing_provider",
        "missing_group_member",
        "missing_rule_target",
    }
    findings = analyze_workspace(config_to_workspace(compiled, nodes))
    assert [
        finding
        for finding in findings
        if finding.code in dangling_codes
    ] == []


def test_leo_defaults_to_openclash_safe_ipv4_dns() -> None:
    template = load_template(LEO_TEMPLATE_ID)

    assert template["ipv6"] is False
    assert template["dns"]["ipv6"] is False
    assert template["dns"]["proxy-server-nameserver"] == [
        "223.5.5.5",
        "119.29.29.29",
    ]


def test_leo_preserves_source_proxy_server_nameservers() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(
        template,
        [_node("HK-01")],
        source_config={
            "dns": {
                "proxy-server-nameserver": [
                    "https://resolver.example/dns-query/subscriber",
                    "tls://resolver-backup.example",
                ]
            }
        },
    )

    assert config["dns"]["proxy-server-nameserver"] == [
        "https://resolver.example/dns-query/subscriber",
        "tls://resolver-backup.example",
    ]


def test_leo_preserves_dns_fragment_parameters_and_existing_outbounds() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    source_nameservers = [
        "https://params.example/dns-query#h3=true&skip-cert-verify=true",
        "https://group.example/dns-query#默认代理&h3=true",
        "https://builtin.example/dns-query#DIRECT&ecs=1.1.1.1/24",
    ]

    config = apply_template(
        template,
        [_node("HK-01")],
        source_config={"dns": {"proxy-server-nameserver": source_nameservers}},
    )

    assert config["dns"]["proxy-server-nameserver"] == source_nameservers


def test_leo_preserves_dns_interface_and_encoded_outbound_names() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    source_nameservers = [
        "https://interface.example/dns-query#en0&h3=true",
        "https://encoded.example/dns-query#utun%26work",
    ]

    config = apply_template(
        template,
        [_node("HK-01")],
        source_config={"dns": {"proxy-server-nameserver": source_nameservers}},
    )

    assert config["dns"]["proxy-server-nameserver"] == source_nameservers


def test_leo_ignores_source_dns_with_a_dangling_group_reference() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    default_nameservers = list(template["dns"]["proxy-server-nameserver"])

    config = apply_template(
        template,
        [_node("HK-01")],
        source_config={
            "proxy-groups": [{"name": "Source-Only"}],
            "dns": {
                "proxy-server-nameserver": [
                    "https://resolver.example/dns-query#Source-Only&h3=true"
                ]
            }
        },
    )

    assert config["dns"]["proxy-server-nameserver"] == default_nameservers


def test_leo_fake_ip_filter_has_no_duplicate_patterns() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    patterns = template["dns"]["fake-ip-filter"]

    assert len(patterns) == len(set(patterns))


def test_leo_fake_ip_filter_has_no_exact_name_covered_by_plus_suffix() -> None:
    patterns = load_template(LEO_TEMPLATE_ID)["dns"]["fake-ip-filter"]
    plus_suffixes = [pattern[2:].lower() for pattern in patterns if pattern.startswith("+.")]

    assert not {
        pattern
        for pattern in patterns
        if not pattern.startswith("+.")
        and any(
            pattern.lower() == suffix or pattern.lower().endswith(f".{suffix}")
            for suffix in plus_suffixes
        )
    }


def test_leo_sniffer_preserves_sensitive_destinations() -> None:
    template = load_template(LEO_TEMPLATE_ID)

    assert template["sniffer"]["override-destination"] is False
    assert template["sniffer"]["sniff"]["HTTP"]["override-destination"] is True
    assert template["sniffer"]["skip-domain"] == [
        "Mijia Cloud",
        "+.push.apple.com",
    ]


def test_leo_routes_core_providers_and_builtin_services_to_expected_targets() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    rules = template["rules"]

    provider_rules = [
        rule
        for rule in rules
        if isinstance(rule, str) and rule.startswith("RULE-SET,")
    ]
    assert len(provider_rules) == 8
    provider_targets = {
        parts[1]: parts[2]
        for rule in provider_rules
        if len(parts := [part.strip() for part in rule.split(",")]) >= 3
    }
    assert provider_targets == {
        "ai-4": "AI 服务",
        "Claude": "AI 服务",
        "GitHub-5": "开发服务",
        "Apple-4": "Apple",
        "Google-2": "Google",
        "Microsoft-6": "Microsoft",
        "YouTube-6": "流媒体",
        "Telegram": "社交通讯",
    }

    assert {
        "DOMAIN-SUFFIX,openai.com,AI 服务",
        "DOMAIN-SUFFIX,chatgpt.com,AI 服务",
        "DOMAIN-SUFFIX,oaistatic.com,AI 服务",
        "DOMAIN-SUFFIX,oaiusercontent.com,AI 服务",
        "GEOSITE,openai,AI 服务",
        "GEOSITE,google,Google",
        "GEOSITE,microsoft,Microsoft",
        "GEOSITE,apple,Apple",
        "GEOSITE,github,开发服务",
        "GEOSITE,youtube,流媒体",
        "GEOSITE,category-ads-all,REJECT",
    } <= set(rules)
    assert "RULE-SET,Telegram,社交通讯,no-resolve" in rules


def test_leo_hong_kong_group_does_not_match_unrelated_names() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(
        template,
        [_node("香港 01"), _node("RUSSIA 01"), _node("新西兰 01"), _node("新加坡 01")],
    )

    assert _group(config, "香港自动")["proxies"] == ["香港 01"]


def test_leo_prunes_empty_region_groups_and_their_parent_references() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(template, [_node("香港 01"), _node("其他 01")])

    group_names = {group["name"] for group in config["proxy-groups"]}
    assert "美国节点" not in group_names
    assert all(
        "美国节点" not in group.get("proxies", [])
        for group in config["proxy-groups"]
    )


def test_leo_prunes_stale_profile_members_after_policy_merge() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(
        template,
        [_node("其他 01")],
        selected_policy=SelectedPolicy(
            mode="merge",
            proxy_groups=[
                {
                    "name": "开发服务",
                    "type": "select",
                    "proxies": ["香港自动", "日本自动", "默认代理"],
                }
            ],
            rules=["DOMAIN-SUFFIX,legacy.example,香港自动"],
        ),
    )

    assert "香港自动" not in {group["name"] for group in config["proxy-groups"]}
    assert _group(config, "开发服务")["proxies"] == ["默认代理"]
    assert config["rules"][0] == "DOMAIN-SUFFIX,legacy.example,默认代理"
    findings = analyze_workspace(config_to_workspace(config, [_node("其他 01")]))
    assert not any(
        finding.code in {"missing_group_member", "missing_rule_target"}
        for finding in findings
    )


def test_leo_closes_group_members_after_selected_policy_replace() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(
        template,
        [_node("其他 01")],
        selected_policy=SelectedPolicy(
            mode="replace",
            proxy_groups=[
                {
                    "name": "CUSTOM",
                    "type": "select",
                    "proxies": ["日本自动", "其他 01", "DIRECT"],
                }
            ],
            rules=[
                "IP-CIDR,192.0.2.0/24,日本自动,no-resolve",
                "DOMAIN-SUFFIX,node.example,已下线节点",
                {
                    "type": "DOMAIN-SUFFIX",
                    "value": "legacy.example",
                    "policy": "日本自动",
                    "options": ["no-resolve"],
                },
                "MATCH,CUSTOM",
            ],
        ),
    )

    assert config["proxy-groups"] == [
        {
            "name": "CUSTOM",
            "type": "select",
            "proxies": ["其他 01", "DIRECT"],
        }
    ]
    assert config["rules"] == [
        "IP-CIDR,192.0.2.0/24,DIRECT,no-resolve",
        "DOMAIN-SUFFIX,node.example,DIRECT",
        {
            "type": "DOMAIN-SUFFIX",
            "value": "legacy.example",
            "policy": "DIRECT",
            "options": ["no-resolve"],
        },
        "MATCH,CUSTOM",
    ]


def test_leo_iteratively_prunes_empty_parent_groups_but_keeps_explicit_empty_group() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    template["proxy-groups"].append(
        {"name": "显式空组", "type": "select", "proxies": []}
    )
    nodes = [_node("其他 01")]
    config = apply_template(
        template,
        nodes,
        selected_policy=SelectedPolicy(
            mode="merge",
            proxy_groups=[
                {
                    "name": "旧地区叶子",
                    "type": "url-test",
                    "include-all": True,
                    "filter": "(?i)日本",
                },
                {"name": "旧地区父组", "type": "select", "proxies": ["旧地区叶子"]},
                {"name": "旧地区根组", "type": "select", "proxies": ["旧地区父组"]},
            ],
            rules=[
                "DOMAIN-SUFFIX,legacy.example,旧地区根组",
                {"type": "DOMAIN", "value": "legacy.example", "target": "旧地区父组"},
            ],
        ),
    )

    group_names = {group["name"] for group in config["proxy-groups"]}
    assert {"旧地区叶子", "旧地区父组", "旧地区根组"}.isdisjoint(group_names)
    assert _group(config, "显式空组")["proxies"] == []
    assert config["rules"][:2] == [
        "DOMAIN-SUFFIX,legacy.example,默认代理",
        {"type": "DOMAIN", "value": "legacy.example", "target": "默认代理"},
    ]

    findings = analyze_workspace(config_to_workspace(config, nodes))
    assert any(
        finding.code == "empty_group" and finding.ref == "显式空组"
        for finding in findings
    )
    assert not any(finding.code == "missing_rule_target" for finding in findings)


def test_leo_missing_rule_target_uses_first_available_compatibility_fallback() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    cases = [
        (["Proxy", "PROXY"], "PROXY"),
        (["Proxy"], "Proxy"),
        ([], "DIRECT"),
    ]

    for group_names, expected in cases:
        config = apply_template(
            template,
            [_node("其他 01")],
            selected_policy=SelectedPolicy(
                mode="replace",
                proxy_groups=[
                    {"name": name, "type": "select", "proxies": ["其他 01"]}
                    for name in group_names
                ],
                rules=["MATCH,已删除出口"],
            ),
        )
        assert config["rules"] == [f"MATCH,{expected}"]


def test_leo_has_no_explicit_ip_provider_or_inline_route_for_shared_services() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    providers = template["rule-providers"]

    assert "AIIP" not in providers

    shared_targets = {"AI 服务", "Google", "流媒体"}
    ipcidr = {name for name, p in providers.items() if str(p.get("behavior")) == "ipcidr"}
    for rule in template["rules"]:
        if not isinstance(rule, str):
            continue
        parts = [part.strip() for part in rule.split(",")]
        if parts[0] == "RULE-SET" and parts[1] in ipcidr:
            assert parts[2] not in shared_targets, f"shared-infra IP provider: {rule}"
        if parts[0] in {"GEOIP", "IP-CIDR", "IP-CIDR6"}:
            assert parts[2] not in shared_targets, f"shared-infra inline IP rule: {rule}"

    assert not {
        rule
        for rule in template["rules"]
        if isinstance(rule, str)
        and rule.startswith("GEOIP,")
        and not rule.startswith(("GEOIP,private,", "GEOIP,cn,"))
    }


def test_leo_audit_exposes_and_contains_shared_service_ip_exceptions() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    report = json.loads(_LEO_AUDIT_PATH.read_text(encoding="utf-8"))

    assert audit_snapshot_matches_template(report, _LEO_TEMPLATE_PATH)
    shared_targets = {"AI 服务", "Google", "流媒体"}
    shared_providers = {
        parts[1]
        for rule in template["rules"]
        if isinstance(rule, str)
        and len(parts := [part.strip() for part in rule.split(",")]) >= 3
        and parts[0] == "RULE-SET"
        and parts[2] in shared_targets
    }
    source_by_name = {source["name"]: source for source in report["sources"]}

    assert shared_providers <= set(source_by_name)
    ip_counts: dict[str, int] = {}
    for name in shared_providers:
        assert "rule_type_counts" in source_by_name[name]
        type_counts = source_by_name[name]["rule_type_counts"]
        ip_counts[name] = sum(
            int(type_counts.get(rule_type, 0))
            for rule_type in (
                "IP-CIDR",
                "IP-CIDR6",
                "IP-SUFFIX",
                "GEOIP",
                "IP-ASN",
            )
        )
        assert source_by_name[name]["resolving_ip_rule_count"] == 0

    assert {name: count for name, count in ip_counts.items() if count} == {
        "Google-2": 5,
        "YouTube-6": 3,
    }
    for name, count in ip_counts.items():
        if not count:
            continue
        route = next(
            rule
            for rule in template["rules"]
            if isinstance(rule, str) and rule.startswith(f"RULE-SET,{name},")
        )
        assert route.endswith(",no-resolve")


def test_leo_uses_no_resolve_variants_for_mixed_google_sources() -> None:
    providers = load_template(LEO_TEMPLATE_ID)["rule-providers"]

    assert providers["Google-2"]["url"].endswith("/Google_No_Resolve.yaml")
    assert providers["YouTube-6"]["url"].endswith("/YouTube_No_Resolve.yaml")


def test_leo_ai_without_us_nodes_requires_manual_selection_not_latency_switching() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(template, [_node("其他 01"), _node("香港 01")])

    assert "美国节点" not in {group["name"] for group in config["proxy-groups"]}
    ai_service = _group(config, "AI 服务")
    assert ai_service["proxies"] == ["手动选择"]


def test_leo_ai_egress_cannot_reach_an_automatic_or_direct_policy() -> None:
    nodes = [_node("香港 01"), _node("US01"), _node("日本 01")]
    config = compile_mihomo_config(apply_template(load_template(LEO_TEMPLATE_ID), nodes), nodes)
    groups = {g["name"]: g for g in config["proxy-groups"]}
    pending = ["AI 服务"]
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        assert name not in {"DIRECT", "REJECT"}
        if name in groups:
            assert groups[name]["type"] == "select"
            pending.extend(groups[name]["proxies"])


def test_leo_ai_service_uses_a_manual_us_group_with_a_generic_connectivity_probe() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(
        template,
        [
            _node("美国 01"),
            _node("US02"),
            _node("LAX 01"),
            _node("RUSSIA 01"),
            _node("新加坡 01"),
            _node("香港 01"),
        ],
    )

    us_nodes = _group(config, "美国节点")
    assert us_nodes["type"] == "select"
    assert us_nodes["proxies"] == ["美国 01", "US02", "LAX 01"]
    assert us_nodes["url"] == "https://cp.cloudflare.com/generate_204"
    assert us_nodes["expected-status"] == 204
    assert us_nodes["timeout"] == 5000
    assert us_nodes["lazy"] is True
    assert us_nodes["interval"] == 600
    assert "max-failed-times" not in us_nodes
    assert "tolerance" not in us_nodes
    assert _group(config, "AI 服务")["proxies"][0] == "美国节点"
    assert "AI自动" not in {group["name"] for group in config["proxy-groups"]}


def test_leo_latency_groups_use_a_bounded_lightweight_probe() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    latency_groups = {"自动选择", "香港自动"}

    for name in latency_groups:
        group = _group(template, name)
        assert group["url"] == "https://cp.cloudflare.com/generate_204"
        assert group["expected-status"] == 204
        assert group["timeout"] == 5000
        assert group["max-failed-times"] == 2
        assert group["lazy"] is True

    assert {
        group["name"]
        for group in template["proxy-groups"]
        if group.get("type") == "url-test"
    } == {"自动选择", "香港自动"}


def test_leo_keeps_non_ai_services_on_nearby_default_routes() -> None:
    template = load_template(LEO_TEMPLATE_ID)

    assert _group(template, "默认代理")["proxies"][0] == "香港自动"
    for name in ("开发服务", "Google"):
        assert _group(template, name)["proxies"][0] == "默认代理"
    # Apple and Microsoft both default to DIRECT: they run China datacenters
    # (GCBD / 21Vianet) whose endpoints an overseas node serves slowly or refuses.
    for name in ("Apple", "Microsoft"):
        assert _group(template, name)["proxies"][:2] == ["DIRECT", "默认代理"]


def test_leo_direct_cloud_routes_precede_broad_vendor_providers() -> None:
    rules = load_template(LEO_TEMPLATE_ID)["rules"]

    microsoft_provider = rules.index("RULE-SET,Microsoft-6,Microsoft")
    apple_provider = rules.index("RULE-SET,Apple-4,Apple")
    github_provider = rules.index("RULE-SET,GitHub-5,开发服务")
    # formulae.brew.sh is GitHub Pages infrastructure the GFW resets on a direct
    # TLS handshake, so it is pinned to 开发服务; it must still be matched before
    # the broad vendor providers (including its own GitHub-5 list).
    assert rules.index("DOMAIN,formulae.brew.sh,开发服务") < github_provider
    assert rules.index("GEOSITE,microsoft@cn,DIRECT") < microsoft_provider
    assert rules.index("GEOSITE,apple@cn,DIRECT") < apple_provider
    assert rules.index("GEOSITE,icloud,DIRECT") < apple_provider
    assert rules.index("DOMAIN,t-ring-fdv2.msedge.net,REJECT,no-resolve") < microsoft_provider
    assert rules.index("DOMAIN-SUFFIX,ls.apple.com,DIRECT") < apple_provider
    for rule in (
        "DOMAIN-SUFFIX,api.microsoftapp.net,AI 服务",
        "DOMAIN-SUFFIX,copilot.azure.com,AI 服务",
        "DOMAIN-SUFFIX,openai.azure.com,AI 服务",
        "DOMAIN,edgeservices.bing.com,AI 服务",
        "DOMAIN,sydney.bing.com,AI 服务",
        "DOMAIN,img.bing.com,AI 服务",
    ):
        assert rules.index(rule) < microsoft_provider


def test_leo_has_no_specific_suffix_shadowed_by_an_earlier_same_target_suffix() -> None:
    rules = load_template(LEO_TEMPLATE_ID)["rules"]
    earlier_suffixes: list[tuple[str, str]] = []
    shadowed: list[str] = []
    for rule in rules:
        if not isinstance(rule, str):
            continue
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) < 3 or parts[0] != "DOMAIN-SUFFIX":
            continue
        domain, target = parts[1].lower(), parts[2]
        if any(
            target == earlier_target and domain.endswith(f".{earlier_domain}")
            for earlier_domain, earlier_target in earlier_suffixes
        ):
            shadowed.append(rule)
        earlier_suffixes.append((domain, target))

    assert shadowed == []


def test_leo_has_no_domain_rule_subsumed_before_the_next_target_change() -> None:
    rules = load_template(LEO_TEMPLATE_ID)["rules"]
    parsed = [
        [part.strip() for part in rule.split(",")]
        for rule in rules
        if isinstance(rule, str)
    ]
    redundant: list[str] = []
    domain_types = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}
    for index, parts in enumerate(parsed):
        if len(parts) < 3 or parts[0] not in domain_types:
            continue
        value, target = parts[1].lower(), parts[2]
        for later in parsed[index + 1 :]:
            if len(later) < 3:
                continue
            if later[2] != target:
                break
            if (
                later[0] == "DOMAIN-KEYWORD"
                and later[1].lower() in value
            ):
                redundant.append(",".join(parts))
                break

    assert redundant == []


def test_leo_specific_services_precede_overlapping_vendor_sources() -> None:
    rules = load_template(LEO_TEMPLATE_ID)["rules"]

    claude = rules.index("RULE-SET,Claude,AI 服务")
    generic_ai = rules.index("RULE-SET,ai-4,AI 服务")
    youtube = rules.index("RULE-SET,YouTube-6,流媒体,no-resolve")
    google = rules.index("RULE-SET,Google-2,Google,no-resolve")
    apple = rules.index("RULE-SET,Apple-4,Apple")

    assert claude < generic_ai
    assert youtube < google
    assert rules.index("GEOSITE,youtube,流媒体") > youtube
    assert rules.index("DOMAIN-SUFFIX,crashlytics.com,Google") < apple
    # Apple stays before the broader Google/Microsoft lists so Apple-specific
    # Akamai CNAMEs keep the template's DIRECT-first Apple policy.
    assert apple < google < rules.index("RULE-SET,Microsoft-6,Microsoft")


_NAMED_SERVICE_TARGETS = {
    "AI 服务",
    "开发服务",
    "Apple",
    "Google",
    "Microsoft",
    "金融服务",
    "社交通讯",
    "游戏服务",
    "流媒体",
}


def test_leo_builtin_china_and_private_catchalls_follow_named_service_rules() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    rules = [rule for rule in template["rules"] if isinstance(rule, str)]
    catchalls = {
        "GEOSITE,private,DIRECT",
        "GEOIP,private,DIRECT,no-resolve",
        "DOMAIN-SUFFIX,cn,DIRECT",
        "GEOSITE,cn,DIRECT",
        "GEOIP,cn,DIRECT,no-resolve",
    }

    assert catchalls <= set(rules)
    earliest_catchall = min(rules.index(rule) for rule in catchalls)

    for index, rule in enumerate(rules):
        parts = [part.strip() for part in rule.split(",")]
        if len(parts) < 2 or rule in catchalls:
            continue
        target = parts[-2] if parts[-1].lower() == "no-resolve" else parts[-1]
        # Tail PROCESS-NAME fallbacks intentionally run after CN/private
        # domain matching so domestic app traffic can remain direct.  The
        # provider and domain/IP service rules must still precede catchalls.
        if parts[0] != "PROCESS-NAME" and target in _NAMED_SERVICE_TARGETS:
            assert index < earliest_catchall, (
                f"named-service rule '{rule}' (index {index}) is shadowed by a broad "
                f"China/private catch-all at index {earliest_catchall}"
            )


def test_leo_service_categories_precede_broad_proxy_geosites() -> None:
    # A destination can belong to both a named service and geolocation-!cn.
    # Check the compiled order because that is what the client actually reads.
    nodes = [_node("香港 01"), _node("美国 01")]
    config = apply_template(load_template(LEO_TEMPLATE_ID), nodes)
    rules = compile_mihomo_config(config, nodes)["rules"]
    broad_proxy = min(
        rules.index(rule)
        for rule in ("GEOSITE,gfw,默认代理", "GEOSITE,geolocation-!cn,默认代理")
    )
    for index, rule in enumerate(rules):
        parts = rule.split(",")
        if parts[0] == "GEOSITE" and (
            parts[2] in _NAMED_SERVICE_TARGETS or parts[1] == "category-games@cn"
        ):
            assert index < broad_proxy, f"service category shadowed: {rule}"


def test_leo_generic_port_and_inbound_routes_do_not_bypass_direct_catchalls() -> None:
    nodes = [_node("香港 01")]
    config = apply_template(load_template(LEO_TEMPLATE_ID), nodes)
    rules = compile_mihomo_config(config, nodes)["rules"]
    # A NAS on :50001, a .cn site on :10443, or a named mixed listener must
    # reach private/China matching before any generic proxy fallback.
    last_direct_catchall = max(
        rules.index(rule)
        for rule in (
            "GEOSITE,private,DIRECT",
            "GEOIP,private,DIRECT,no-resolve",
            "DOMAIN-SUFFIX,cn,DIRECT",
            "GEOSITE,cn,DIRECT",
            "GEOIP,cn,DIRECT,no-resolve",
        )
    )
    for index, rule in enumerate(rules):
        parts = rule.split(",")
        if parts[0] in {"DST-PORT", "IN-NAME"} and parts[2] == "默认代理":
            assert index > last_direct_catchall, f"direct catchall bypassed: {rule}"
    assert rules[-1] == "MATCH,默认代理"
