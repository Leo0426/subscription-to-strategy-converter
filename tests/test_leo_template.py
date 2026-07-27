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
