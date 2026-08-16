from app.core.template_engine import LEO_TEMPLATE_ID, apply_template, load_template
from app.ir import ProxyNode


def _node(name: str) -> ProxyNode:
    return ProxyNode(name=name, protocol="ss", server="proxy.example.com", port=443)


def _group(config: dict, name: str) -> dict:
    return next(group for group in config["proxy-groups"] if group["name"] == name)


def test_leo_materializes_subscription_backed_groups_from_current_nodes() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(template, [_node("香港 01"), _node("US01"), _node("日本 01")])

    assert "Leo订阅" not in config.get("proxy-providers", {})
    assert _group(config, "自动选择")["proxies"] == ["香港 01", "US01", "日本 01"]
    assert "use" not in _group(config, "自动选择")


def test_leo_defaults_to_openclash_safe_ipv4_dns() -> None:
    template = load_template(LEO_TEMPLATE_ID)

    assert template["ipv6"] is False
    assert template["dns"]["ipv6"] is False
    assert template["dns"]["proxy-server-nameserver"] == [
        "223.5.5.5",
        "119.29.29.29",
    ]


def test_leo_does_not_require_optional_biliintl_geosite_tag() -> None:
    template = load_template(LEO_TEMPLATE_ID)

    # Some OpenClash GeoSite.dat builds omit this tag. The equivalent
    # RuleProvider is already declared and ordered earlier in the rule graph.
    assert "RULE-SET,biliintl,流媒体" in template["rules"]
    assert not any(rule.startswith("GEOSITE,biliintl,") for rule in template["rules"])


def test_leo_region_groups_do_not_match_ambiguous_country_fragments() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(
        template,
        [_node("US01"), _node("RUSSIA 01"), _node("新西兰 01"), _node("新加坡 01")],
    )

    assert _group(config, "美国自动")["proxies"] == ["US01"]
    assert _group(config, "新加坡自动")["proxies"] == ["新加坡 01"]


def test_leo_prunes_empty_region_groups_and_their_parent_references() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(template, [_node("香港 01"), _node("US01")])

    group_names = {group["name"] for group in config["proxy-groups"]}
    assert "韩国自动" not in group_names
    assert "日本自动" not in group_names
    assert all(
        "韩国自动" not in group.get("proxies", [])
        and "日本自动" not in group.get("proxies", [])
        for group in config["proxy-groups"]
    )


def test_leo_has_no_ip_layer_routing_for_shared_infrastructure_services() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    providers = template["rule-providers"]

    assert "AIIP" not in providers

    ipcidr = {name for name, p in providers.items() if str(p.get("behavior")) == "ipcidr"}
    for rule in template["rules"]:
        if not isinstance(rule, str) or not rule.startswith("RULE-SET,"):
            continue
        parts = [part.strip() for part in rule.split(",")]
        if parts[1] in ipcidr and parts[2] != "DIRECT":
            assert "no-resolve" in parts, f"resolving service IP rule: {rule}"


def test_leo_ai_group_prefers_a_claude_reachability_aware_auto_test() -> None:
    # Plain latency probes (e.g. gstatic 204) can't tell a Cloudflare-blocked
    # exit apart from a clean one — both answer just as fast. AI 服务 must
    # try a group that actually verifies claude.ai is reachable before
    # falling back to a latency-only pool.
    template = load_template(LEO_TEMPLATE_ID)
    config = apply_template(template, [_node("US01"), _node("香港 01")])

    ai_auto = _group(config, "AI自动")
    assert ai_auto["type"] == "url-test"
    assert ai_auto["url"] == "https://claude.ai/"
    assert ai_auto["expected-status"] == 200

    ai_service = _group(config, "AI 服务")
    assert ai_service["proxies"][0] == "AI自动"


# "cn"/"ChinaIPs"-style broad domestic/private catch-alls must never be
# ordered ahead of a named service's own RULE-SET — first match wins, so a
# domain merely miscategorized into one of these huge aggregated lists would
# get force-DIRECTed before it ever reaches its intended proxy group (e.g.
# Netflix/IQIYI/Bilibili going DIRECT and breaking instead of unlocking).
_BROAD_CATCHALL_RULE_SETS = {
    "china_ip_ipv6", "ChinaAPP", "ChinaIPs", "cn", "cn_v6", "cnip", "private-2",
}
_NAMED_SERVICE_TARGETS = {
    "AI 服务", "开发服务", "Apple", "Google", "Microsoft",
    "金融服务", "社交通讯", "游戏服务", "流媒体",
}


def test_leo_broad_china_catchall_rules_never_precede_named_service_rules() -> None:
    template = load_template(LEO_TEMPLATE_ID)
    rules = [rule for rule in template["rules"] if isinstance(rule, str)]

    catchall_indexes = [
        index
        for index, rule in enumerate(rules)
        if rule.startswith("RULE-SET,") and rule.split(",")[1] in _BROAD_CATCHALL_RULE_SETS
    ]
    assert catchall_indexes, "expected to find the broad China/private catch-all RULE-SETs"
    earliest_catchall = min(catchall_indexes)

    for index, rule in enumerate(rules):
        if not rule.startswith("RULE-SET,"):
            continue
        parts = rule.split(",")
        name, target = parts[1], parts[2]
        if name in _BROAD_CATCHALL_RULE_SETS:
            continue
        if target in _NAMED_SERVICE_TARGETS:
            assert index < earliest_catchall, (
                f"named-service rule '{rule}' (index {index}) is shadowed by a broad "
                f"catch-all RULE-SET at index {earliest_catchall}"
            )
