"""Native Mihomo publication keeps the source's connectivity envelope."""
from copy import deepcopy
import json

import pytest

from app.core.normalizer import normalize_nodes
from app.core.parsers.clash import clash_to_ir


def node(name="US  01", server="node.example.com", **options):
    return {"name": name, "type": "ss", "server": server, "port": 443,
            "cipher": "aes-128-gcm", "password": "example", **options}


def inventory(source):
    return normalize_nodes([clash_to_ir(proxy) for proxy in source["proxies"]])


def generated():
    return {
        "mixed-port": 7890, "mode": "rule", "dns": {"enable": True, "enhanced-mode": "fake-ip"},
        "tun": {"enable": True},
        "proxy-groups": [{"name": "Default", "type": "select", "proxies": ["US 01"]}],
        "rule-providers": {}, "rules": ["DOMAIN-SUFFIX,chatgpt.com,Default", "MATCH,Default"],
    }


def build(source, policy=None):
    from app.core.platforms.mihomo import build_mihomo_config

    return build_mihomo_config(inventory(source), policy or generated(), source)


def test_native_connectivity_and_raw_node_fields_survive_policy_replacement():
    source = {
        "mixed-port": 8888, "mode": "rule", "ipv6": False,
        "dns": {"enable": True, "enhanced-mode": "redir-host",
                "nameserver": ["https://resolver.example/dns-query#Airport"]},
        "tun": {"enable": False}, "hosts": {"node.example.com": "203.0.113.1"},
        "sniffer": {"enable": False}, "profile": {"store-selected": True},
        "proxies": [node(udp=False, **{"servername": "front.example", "dialer-proxy": "Airport",
                                    "custom-transport": {"value": "keep-me"}})],
        "proxy-groups": [{"name": "Airport", "type": "select", "proxies": ["DIRECT"]}],
        "proxy-providers": {"airport": {"type": "http", "url": "https://source.example/nodes?token=secret",
                                        "proxy": "Airport"}},
        "rule-providers": {"private": {"type": "inline", "behavior": "domain", "payload": ["private.example"]}},
        "rules": ["MATCH,DIRECT"],
    }
    before = deepcopy(source)

    result, warnings = build(source)

    for key in ("mixed-port", "mode", "ipv6", "dns", "tun", "hosts", "sniffer", "profile",
                "proxies", "proxy-providers", "rule-providers"):
        assert result[key] == before[key], key
    assert result["proxy-groups"] == [
        {"name": "Airport", "type": "select", "proxies": ["DIRECT"]},
        {"name": "Default", "type": "select", "proxies": ["US  01"]},
    ]
    assert result["rules"] == ["DOMAIN-SUFFIX,chatgpt.com,Default", "MATCH,Default"]
    assert warnings == []
    assert source == before


def test_absent_native_settings_stay_absent_and_internal_metadata_is_removed():
    source = {"proxies": [node()], "_surge_source": "private-source-token",
              "source-format": "clash", "native_source_format": "clash"}

    result, warnings = build(source)

    assert {"mixed-port", "mode", "dns", "tun", "_surge_source", "source-format",
            "native_source_format"}.isdisjoint(result)
    assert result["proxy-groups"][0]["proxies"] == ["US  01"]
    assert "private-source-token" not in json.dumps(result)
    assert warnings == []


def test_generated_name_collision_does_not_rewire_source_dns_or_proxy_chains():
    source = {
        "proxies": [node(**{"dialer-proxy": "Default"})],
        "dns": {"nameserver": ["https://dns.example/query#Default"]},
        "proxy-groups": [
            {"name": "Default", "type": "select", "proxies": ["DIRECT"]},
            {"name": "Subflow Default", "type": "select", "proxies": ["DIRECT"]},
        ],
    }
    policy = generated()
    policy["proxy-groups"].append({"name": "AI", "type": "select", "proxies": ["Default"]})

    result, _ = build(source, policy)

    assert result["dns"] == source["dns"]
    assert result["proxies"] == source["proxies"]
    assert result["proxy-groups"][:2] == source["proxy-groups"]
    assert result["proxy-groups"][2]["name"] == "Subflow Default 2"
    assert result["proxy-groups"][3]["proxies"] == ["Subflow Default 2"]
    assert result["rules"] == ["DOMAIN-SUFFIX,chatgpt.com,Subflow Default 2", "MATCH,Subflow Default 2"]


def test_native_mihomo_nodes_are_preserved_without_cross_format_repairs():
    source = {"proxies": [node(plugin="obfs", **{
        "plugin-opts": {"mode": "http", "host": "front.example:", "custom": True},
        "udp": False, "skip-cert-verify": False,
    })]}
    before = deepcopy(source)

    result, _ = build(source)

    assert result["proxies"] == before["proxies"]
    assert source == before


def test_generated_rule_providers_are_renamed_in_rules_without_changing_source_providers():
    source = {
        "proxies": [node()],
        "proxy-groups": [{"name": "Default", "type": "select", "proxies": ["DIRECT"]}],
        "rule-providers": {
            "shared": {"type": "http", "behavior": "domain", "url": "https://airport.example/private",
                       "path": "./rules/shared.yaml", "proxy": "Default"},
            "Subflow shared": {"type": "inline", "behavior": "domain", "payload": ["private.example"]},
        },
        "dns": {"nameserver-policy": {"rule-set:shared": "https://dns.example/query#Default"}},
    }
    policy = generated()
    policy["rule-providers"] = {
        "shared": {"type": "http", "behavior": "domain", "url": "https://rules.example/generated",
                   "path": "./rules/shared.yaml", "proxy": "Default"},
    }
    policy["rules"] = ["RULE-SET,shared,Default", "AND,((RULE-SET,shared),(NETWORK,UDP)),Default,no-resolve",
                       "DOMAIN,shared,Default", "MATCH,US 01"]

    result, _ = build(source, policy)

    assert result["dns"] == source["dns"]
    assert result["rule-providers"]["shared"] == source["rule-providers"]["shared"]
    provider = result["rule-providers"]["Subflow shared 2"]
    assert provider["proxy"] == "Subflow Default"
    assert provider["path"] != "./rules/shared.yaml"
    assert result["rules"] == [
        "RULE-SET,Subflow shared 2,Subflow Default",
        "AND,((RULE-SET,Subflow shared 2),(NETWORK,UDP)),Subflow Default,no-resolve",
        "DOMAIN,shared,Subflow Default", "MATCH,US  01",
    ]


def test_generated_proxy_provider_references_keep_their_own_download_egress():
    source = {"proxies": [node()], "proxy-providers": {
        "airport": {"type": "http", "url": "https://source.example/nodes", "path": "./providers/nodes.yaml"},
    }}
    policy = generated()
    policy["proxy-providers"] = {
        "airport": {"type": "http", "url": "https://policy.example/nodes", "path": "./providers/nodes.yaml",
                    "proxy": "US 01", "override": {"dialer-proxy": "US 01"}},
    }
    policy["proxy-groups"][0]["use"] = ["airport"]

    result, _ = build(source, policy)

    assert result["proxy-providers"]["airport"] == source["proxy-providers"]["airport"]
    assert result["proxy-groups"][0]["use"] == ["Subflow airport"]
    provider = result["proxy-providers"]["Subflow airport"]
    assert provider["proxy"] == "US  01"
    assert provider["override"]["dialer-proxy"] == "US  01"
    assert provider["path"] != source["proxy-providers"]["airport"]["path"]


def test_generated_rule_provider_still_runs_through_existing_workspace_compiler():
    source = {"proxies": [node()]}
    policy = generated()
    policy["rule-providers"] = {"public": {
        "type": "http", "behavior": "domain",
        "url": "https://raw.githubusercontent.com/example/rules/" + "a" * 40 + "/domains.yaml",
    }}
    policy["rules"] = ["RULE-SET,public,Default", "MATCH,Default"]

    result, _ = build(source, policy)

    assert result["rule-providers"]["public"]["proxy"] == "DIRECT"
    assert result["rule-providers"]["public"]["url"].startswith("https://cdn.jsdelivr.net/gh/")


@pytest.mark.parametrize("mode", ["global", "direct", "Global"])
def test_native_bypass_mode_switches_to_rule_with_a_redacted_warning(mode):
    source = {"proxies": [node()], "mode": mode,
              "dns": {"nameserver": ["https://dns.example/token=private-secret"]}}

    result, warnings = build(source)

    assert result["mode"] == "rule"
    assert any(item["code"] == "source_mode_changed" for item in warnings)
    assert "private-secret" not in json.dumps(warnings)
    assert source["mode"] == mode


@pytest.mark.parametrize("proxies,groups", [
    ([node("US", "first.example"), node("US", "second.example")], []),
    ([node("US", "first.example"), node(" US", "second.example"), node("US-2", "third.example")], []),
    ([node(" Default ")], []),
    ([node()], [{"name": "US  01", "type": "select", "proxies": ["DIRECT"]}]),
    ([node()], [{"name": "Airport", "type": "select", "proxies": ["DIRECT"]},
                {"name": "Airport", "type": "select", "proxies": ["DIRECT"]}]),
])
def test_ambiguous_native_names_fail_closed_without_leaking_node_credentials(proxies, groups):
    from app.core.platforms.mihomo import NativeMihomoProfileError

    source = {"proxies": proxies, "proxy-groups": groups}
    with pytest.raises(NativeMihomoProfileError, match="名称冲突") as error:
        build(source)
    assert "example" not in str(error.value)


def test_duplicate_connection_aliases_remain_available_to_native_groups():
    source = {"proxies": [node(), node("Source alias")],
              "proxy-groups": [{"name": "Airport", "type": "select", "proxies": ["Source alias"]}]}

    result, _ = build(source)

    assert len(inventory(source)) == 1
    assert result["proxies"] == source["proxies"]
    assert result["proxy-groups"][0] == source["proxy-groups"][0]


@pytest.mark.parametrize("source_format", ["surge", "subconverter"])
def test_cross_format_compilation_omits_unsupplied_defaults_and_warns(source_format):
    from app.core.platforms.mihomo import build_mihomo_config

    source = {"proxies": [node("US 01")], "source-format": source_format, "_surge_source": "private-secret"}

    result, warnings = build_mihomo_config(inventory(source), generated(), source)

    assert {"dns", "tun", "mixed-port"}.isdisjoint(result)
    assert result["proxies"][0]["name"] == "US 01"
    assert any(item["code"] == "cross_format_source_settings" for item in warnings)
    assert "private-secret" not in json.dumps({"result": result, "warnings": warnings})


def test_subconverter_provided_common_settings_are_preserved_with_a_limit_warning():
    source = {"proxies": [node()], "source-format": "subconverter",
              "dns": {"nameserver": ["https://dns.example/token=private-secret"]}, "mixed-port": 8888}

    result, warnings = build(source)

    assert result["dns"] == source["dns"]
    assert result["mixed-port"] == 8888
    assert "tun" not in result
    assert warnings[0]["code"] == "cross_format_source_settings"
    assert "private-secret" not in json.dumps(warnings)


def test_generic_workspace_keeps_existing_compilation_without_source_warning():
    from app.core.platforms.mihomo import build_mihomo_config

    policy = {**generated(), "source-format": "surge", "_surge_source": "private-secret"}
    result, warnings = build_mihomo_config(inventory({"proxies": [node()]}), policy)

    assert result["dns"] == generated()["dns"]
    assert warnings == []
    assert "private-secret" not in json.dumps(result)


def test_provider_rewriting_does_not_interpret_a_domain_value_as_rule_syntax():
    source = {"proxies": [node()], "rule-providers": {"Default": {"type": "inline", "payload": []}}}
    policy = generated()
    policy["rule-providers"] = {"Default": {"type": "inline", "payload": []}}
    policy["rules"] = ["DOMAIN,RULE-SET,Default", "MATCH,Default"]

    result, _ = build(source, policy)

    assert result["rules"] == ["DOMAIN,RULE-SET,Default", "MATCH,Default"]


def test_ambiguous_generated_group_names_fail_closed():
    from app.core.platforms.mihomo import NativeMihomoProfileError

    source = {"proxies": [node()]}
    policy = generated()
    policy["proxy-groups"].append({"name": "Default", "type": "select", "proxies": ["DIRECT"]})

    with pytest.raises(NativeMihomoProfileError, match="名称冲突"):
        build(source, policy)


def test_missing_native_inventory_cannot_silently_publish_dangling_generated_node_references():
    from app.core.platforms.mihomo import NativeMihomoProfileError, build_mihomo_config

    with pytest.raises(NativeMihomoProfileError):
        build_mihomo_config(inventory({"proxies": [node()]}), generated(), {})


def test_local_file_provider_collision_fails_without_disclosing_private_path():
    from app.core.platforms.mihomo import NativeMihomoProfileError

    source = {"proxies": [node()], "rule-providers": {
        "source": {"type": "file", "path": "./private-token.yaml", "behavior": "domain"},
    }}
    policy = generated()
    policy["rule-providers"] = {
        "generated": {"type": "file", "path": "private-token.yaml", "behavior": "domain"},
    }

    with pytest.raises(NativeMihomoProfileError) as error:
        build(source, policy)
    assert "private-token" not in str(error.value)


def test_provider_cache_collision_uses_an_unoccupied_path_across_both_provider_types():
    source = {"proxies": [node()], "proxy-providers": {
        "source": {"type": "http", "path": "./cache/shared.yaml", "url": "https://airport.example/nodes"},
    }, "rule-providers": {
        "source-rules": {"type": "file", "path": "./cache/subflow-shared.yaml", "behavior": "domain"},
    }}
    policy = generated()
    policy["rule-providers"] = {
        "generated": {"type": "http", "path": "cache/shared.yaml", "url": "https://rules.example/new"},
    }

    result, _ = build(source, policy)

    assert result["rule-providers"]["generated"]["path"] == "cache/subflow-2-shared.yaml"
    assert result["proxy-providers"] == source["proxy-providers"]


@pytest.mark.parametrize("name", [" DIRECT ", "REJECT", "GLOBAL"])
def test_normalized_node_cannot_capture_a_builtin_routing_target(name):
    from app.core.platforms.mihomo import NativeMihomoProfileError

    with pytest.raises(NativeMihomoProfileError, match="名称冲突"):
        build({"proxies": [node(name)]})


def test_structured_generated_rule_references_are_rewritten_consistently():
    source = {
        "proxies": [node()],
        "proxy-groups": [{"name": "Default", "type": "select", "proxies": ["DIRECT"]}],
        "rule-providers": {"shared": {"type": "inline", "payload": []}},
    }
    policy = generated()
    policy["rule-providers"] = {"shared": {"type": "inline", "payload": ["service.example"]}}
    policy["rules"] = [
        {"type": "RULE-SET", "provider": "shared", "target": "Default"},
        {"type": "DOMAIN", "match": "shared", "proxy": "US 01"},
    ]

    result, _ = build(source, policy)

    assert result["rules"] == [
        {"type": "RULE-SET", "provider": "Subflow shared", "target": "Subflow Default"},
        {"type": "DOMAIN", "match": "shared", "proxy": "US  01"},
    ]


@pytest.mark.parametrize("section,value", [
    ("proxies", None), ("proxies", {}), ("proxies", "provider-secret"),
    ("proxy-groups", None), ("proxy-groups", {}), ("proxy-groups", "provider-secret"),
    ("proxy-groups", [{"name": "Airport", "type": "select", "proxies": "provider-secret"}]),
    ("proxy-groups", [{"name": "Airport", "type": "select", "use": {"provider-secret": True}}]),
    ("rule-providers", None), ("rule-providers", []),
    ("rule-providers", [{"name": "provider-secret"}]),
    ("rule-providers", {"Airport": "provider-secret"}),
    ("rule-providers", {"Airport": None}),
    ("rule-providers", {123: {"type": "inline", "payload": []}}),
    ("proxy-providers", None), ("proxy-providers", []),
    ("proxy-providers", [{"name": "provider-secret"}]),
    ("proxy-providers", {"Airport": "provider-secret"}),
    ("proxy-providers", {"Airport": {"type": "http", "path": ["provider-secret"]}}),
    ("proxy-providers", {"Airport": {"type": "http", "proxy": {"provider-secret": True}}}),
])
def test_malformed_native_structure_fails_with_a_static_safe_error(section, value):
    from app.core.platforms.mihomo import NativeMihomoProfileError, build_mihomo_config

    nodes = inventory({"proxies": [node()]})
    source = {"proxies": [node()], section: value}

    with pytest.raises(NativeMihomoProfileError) as error:
        build_mihomo_config(nodes, generated(), source)
    assert "provider-secret" not in str(error.value)


def test_malformed_native_node_value_does_not_escape_as_an_unhandled_parser_error():
    from app.core.platforms.mihomo import NativeMihomoProfileError, build_mihomo_config

    source = {"proxies": [node(port="provider-secret")]}
    with pytest.raises(NativeMihomoProfileError) as error:
        build_mihomo_config(inventory({"proxies": [node()]}), generated(), source)
    assert "provider-secret" not in str(error.value)
