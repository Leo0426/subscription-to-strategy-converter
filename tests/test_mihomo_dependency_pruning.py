"""Keep only airport policy definitions reachable from the published config."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.normalizer import normalize_nodes
from app.core.parsers.clash import clash_to_ir
from app.core.platforms.mihomo import build_mihomo_config
from app.core.renderer import render_yaml
from app.main import app


def node(**options):
    return {"name": "US  01", "type": "ss", "server": "node.example", "port": 443,
            "cipher": "aes-128-gcm", "password": "synthetic", **options}


def group(name, *members, **options):
    return {"name": name, "type": "select", "proxies": list(members), **options}


def rule_provider(**options):
    return {"type": "inline", "behavior": "domain", "payload": ["example.com"], **options}


def policy():
    return {"proxy-groups": [group("Default", "US 01")], "rule-providers": {},
            "rules": ["DOMAIN-SUFFIX,chatgpt.com,Default", "MATCH,Default"]}


def build(source, generated=None):
    nodes = normalize_nodes([clash_to_ir(proxy) for proxy in source["proxies"]])
    return build_mihomo_config(nodes, generated or policy(), source)[0]


def test_removes_old_routing_definitions_before_name_and_path_disambiguation():
    source = {
        "proxies": [node()],
        "proxy-groups": [group("Default", "US  01"), group("Unused", "DIRECT")],
        "proxy-providers": {"old-nodes": {"type": "http", "url": "https://example.com/old-nodes"}},
        "rule-providers": {"shared": rule_provider(path="./rules/shared.yaml")},
        "rules": ["RULE-SET,shared,Default", "MATCH,Unused"],
    }
    before = deepcopy(source)
    generated = policy()
    generated["rule-providers"] = {"shared": rule_provider(path="./rules/shared.yaml")}
    generated["rules"].insert(0, "RULE-SET,shared,Default")

    result = build(source, generated)

    assert result["proxy-groups"] == [group("Default", "US  01")]
    assert result.get("proxy-providers", {}) == {}
    assert result["rule-providers"] == generated["rule-providers"]
    assert result["rules"] == generated["rules"]
    assert result["proxies"] == source["proxies"]
    assert source == before


def test_retains_transitive_connection_dependencies_but_removes_unreachable_cycles():
    source = {
        "proxies": [node(**{"dialer-proxy": "Relay"})],
        "proxy-groups": [group("Relay", "Fallback"), group("Fallback", use=["live"]),
                         group("Download", "DIRECT"), group("Override", "DIRECT"),
                         group("Orphan A", "Orphan B"), group("Orphan B", "Orphan A")],
        "proxy-providers": {
            "live": {"type": "inline", "payload": [], "proxy": "Download",
                     "override": {"dialer-proxy": "Override"}},
            "orphan": {"type": "http", "url": "https://example.com/orphan", "proxy": "Orphan A"},
        },
        "rule-providers": {"orphan": rule_provider(proxy="Orphan B")},
        "rules": ["RULE-SET,orphan,Orphan A", "MATCH,Relay"],
    }

    result = build(source)

    assert result["proxy-groups"] == source["proxy-groups"][:4] + [group("Default", "US  01")]
    assert result["proxy-providers"] == {"live": source["proxy-providers"]["live"]}
    assert result.get("rule-providers", {}) == {}
    assert result["proxies"] == source["proxies"]


@pytest.mark.parametrize("settings", [
    {"dns": {"nameserver-policy": {"rule-set:needed": "https://dns.example/query#DNS%20Exit&h3=true"}}},
    {"dns": {"proxy-server-nameserver-policy": {"rule-set:needed": "https://dns.example/query#DNS Exit"}}},
    {"dns": {"fake-ip-filter": ["rule-set:needed"], "nameserver": ["https://dns.example/query#DNS Exit"]}},
    {"dns": {"fake-ip-filter-mode": "rule", "fake-ip-filter": ["RULE-SET,needed,real-ip"],
             "proxy-server-nameserver": ["https://dns.example/query#DNS Exit"]}},
    {"tun": {"route-address-set": ["needed"]}, "ntp": {"proxy": "DNS Exit"}},
    {"tun": {"route-exclude-address-set": ["needed"]}, "listeners": [{"proxy": "DNS Exit"}]},
    {"sniffer": {"skip-domain": ["rule-set:needed"]},
     "tunnels": ["tcp,127.0.0.1:3333,example.com:443,DNS Exit"]},
])
def test_keeps_provider_and_group_references_outside_the_main_rules(settings):
    source = {"proxies": [node()], **settings,
              "proxy-groups": [group("DNS Exit", "DIRECT"), group("Unused", "DIRECT")],
              "rule-providers": {"needed": rule_provider(), "unused": rule_provider()}}

    result = build(source)

    assert result["proxy-groups"] == [source["proxy-groups"][0], group("Default", "US  01")]
    assert result["rule-providers"] == {"needed": source["rule-providers"]["needed"]}
    for key, value in settings.items():
        assert result[key] == value


@pytest.mark.parametrize("include", ["include-all", "include-all-providers"])
def test_reachable_dynamic_groups_keep_all_proxy_providers(include):
    source = {"proxies": [node(**{"dialer-proxy": "Dynamic"})],
              "proxy-groups": [group("Dynamic", **{include: True}), group("Unused", "DIRECT")],
              "proxy-providers": {"one": {"type": "inline", "payload": []},
                                  "two": {"type": "inline", "payload": []}}}

    result = build(source)

    assert result["proxy-providers"] == source["proxy-providers"]
    assert result["proxy-groups"] == [source["proxy-groups"][0], group("Default", "US  01")]


def test_retains_listener_subrules_and_their_dependencies_only():
    source = {"proxies": [node()], "listeners": [{"name": "local", "type": "mixed", "port": 1234, "rule": "entry"}],
              "proxy-groups": [group("Keep", "DIRECT"), group("Unused", "DIRECT")],
              "rule-providers": {"needed": rule_provider(), "unused": rule_provider()},
              "sub-rules": {"entry": ["SUB-RULE,(NETWORK,TCP),nested", "MATCH,Keep"],
                            "nested": ["AND,((RULE-SET,needed),(NETWORK,TCP)),Keep"],
                            "unused": ["RULE-SET,unused,Unused", "MATCH,Unused"]},
              "rules": ["SUB-RULE,(NETWORK,UDP),unused", "MATCH,Unused"]}

    result = build(source)

    assert result["sub-rules"] == {name: source["sub-rules"][name] for name in ("entry", "nested")}
    assert result["proxy-groups"] == [source["proxy-groups"][0], group("Default", "US  01")]
    assert result["rule-providers"] == {"needed": source["rule-providers"]["needed"]}


def test_generated_policy_keeps_native_provider_dependencies_with_distinct_namespaces():
    source = {"proxies": [node()],
              "proxy-groups": [group("Download", "DIRECT"), group("Default", "DIRECT")],
              "proxy-providers": {"needed": {"type": "inline", "payload": [node(**{"dialer-proxy": "Download"})]},
                                  "unused": {"type": "inline", "payload": []}},
              "rule-providers": {"needed": rule_provider()}}
    generated = policy()
    generated["proxy-groups"][0]["use"] = ["needed"]

    result = build(source, generated)

    assert result["proxy-groups"] == [source["proxy-groups"][0], group("Default", "US  01", use=["needed"])]
    assert result["proxy-providers"] == {"needed": source["proxy-providers"]["needed"]}
    assert result.get("rule-providers", {}) == {}


def test_generated_dynamic_group_keeps_native_providers_even_on_name_collision():
    source = {"proxies": [node()], "proxy-providers": {
        "shared": {"type": "inline", "payload": [node()]},
    }}
    generated = policy()
    generated["proxy-groups"][0]["include-all-providers"] = True
    generated["proxy-providers"] = {"shared": {"type": "inline", "payload": []}}

    result = build(source, generated)

    assert result["proxy-providers"]["shared"] == source["proxy-providers"]["shared"]
    assert result["proxy-providers"]["Subflow shared"] == generated["proxy-providers"]["shared"]


def test_nonreference_strings_do_not_keep_an_unrelated_policy_definition_alive():
    source = {"proxies": [node(password="Unused", **{"sni": "Unused"})],
              "proxy-groups": [group("Unused", "DIRECT")],
              "hosts": {"Unused": "192.0.2.1"},
              "profile": {"store-selected": True}}

    result = build(source)

    assert result["proxy-groups"] == [group("Default", "US  01")]
    assert result["proxies"] == source["proxies"]
    assert result["hosts"] == source["hosts"]


def test_template_common_settings_that_are_not_published_cannot_keep_old_definitions():
    source = {"proxies": [node()], "dns": {"nameserver": ["192.0.2.1"]},
              "proxy-groups": [group("Unused", "DIRECT")],
              "rule-providers": {"unused": rule_provider()}}
    generated = policy()
    generated["dns"] = {"nameserver-policy": {"rule-set:unused": "https://dns.example/query#Unused"}}
    generated["tun"] = {"route-address-set": ["unused"]}

    result = build(source, generated)

    assert result["dns"] == source["dns"]
    assert "tun" not in result
    assert result["proxy-groups"] == [group("Default", "US  01")]
    assert result.get("rule-providers", {}) == {}


def test_group_and_provider_are_kept_by_dns_multiset_and_encoded_fragments():
    source = {"proxies": [node()],
              "dns": {"nameserver-policy": {"RULE-SET:first,second": "https://dns.example/query#h3=true&Exit%2BName"}},
              "proxy-groups": [group("Exit+Name", "DIRECT"), group("Unused", "DIRECT")],
              "rule-providers": {"first": rule_provider(), "second": rule_provider(), "unused": rule_provider()}}

    result = build(source)

    assert result["proxy-groups"] == [source["proxy-groups"][0], group("Default", "US  01")]
    assert set(result["rule-providers"]) == {"first", "second"}
    assert result["dns"] == source["dns"]


@pytest.mark.parametrize("target", ["src", "dst", "no-resolve"])
@pytest.mark.parametrize("rule", ["MATCH,{target}", "DOMAIN,example.com,{target}",
                                   "RULE-SET,needed,{target},src,no-resolve",
                                   "AND,((RULE-SET,needed),(NETWORK,TCP)),{target}"])
def test_subrule_targets_named_like_rule_options_remain_reachable(target, rule):
    source = {"proxies": [node()], "listeners": [{"rule": "entry"}],
              "sub-rules": {"entry": [rule.format(target=target)]},
              "proxy-groups": [group(target, "DIRECT"), group("Unused", "DIRECT")],
              "rule-providers": {"needed": rule_provider()}}

    result = build(source)

    assert result["proxy-groups"] == [source["proxy-groups"][0], group("Default", "US  01")]
    assert result["sub-rules"] == source["sub-rules"]


def test_normalized_node_reference_does_not_keep_unrelated_native_group():
    source = {"proxies": [node()], "proxy-groups": [group("US 01", "DIRECT")]}

    result = build(source)

    assert result["proxy-groups"] == [group("Default", "US  01")]


@pytest.mark.parametrize("inline_provider", [False, True])
def test_rematch_outbound_keeps_target_subrule_and_its_dependencies(inline_provider):
    rematch = {"name": "Rematch", "type": "rematch", "target-sub-rule": "entry"}
    source = {"proxies": [node()], "sub-rules": {"entry": ["MATCH,Keep"]},
              "proxy-groups": [group("Keep", "DIRECT"), group("Unused", "DIRECT")]}
    if inline_provider:
        source["proxy-groups"].append(group("Dynamic", use=["inline"]))
        source["proxy-providers"] = {"inline": {"type": "inline", "payload": [rematch]}}
        source["dns"] = {"nameserver": ["https://dns.example/query#Dynamic"]}
    else:
        source["proxies"].append(rematch)

    result = build(source)

    assert result["sub-rules"] == source["sub-rules"]
    assert group("Keep", "DIRECT") in result["proxy-groups"]
    assert group("Unused", "DIRECT") not in result["proxy-groups"]
    assert result["proxies"] == source["proxies"]


@pytest.mark.parametrize("rule", [r"DOMAIN-REGEX,^example\(test$,Keep", "DOMAIN-REGEX,^a,b$,Keep",
                                  "PROCESS-NAME-REGEX,^a,b$,Keep", "PROCESS-PATH-REGEX,^a,b$,Keep"])
def test_regex_subrules_preserve_targets_when_patterns_contain_commas_or_parentheses(rule):
    source = {"proxies": [node()], "listeners": [{"rule": "entry"}],
              "sub-rules": {"entry": [rule]}, "proxy-groups": [group("Keep", "DIRECT")]}

    result = build(source)

    assert group("Keep", "DIRECT") in result["proxy-groups"]
    assert result["sub-rules"] == source["sub-rules"]


@pytest.mark.parametrize("options,target", [({"dialer_proxy": "Keep"}, "Keep"),
                                           ({"DIALER-PROXY": "Keep"}, "Keep"),
                                           ({"dialer-proxy": 123}, "123"),
                                           ({"dialer-proxy": 123.5}, "1.235E+02")])
def test_native_option_aliases_and_numeric_references_preserve_dependencies(options, target):
    source = {"proxies": [node(**options)], "proxy-groups": [group(target, "DIRECT"), group("Unused", "DIRECT")]}

    result = build(source)

    assert result["proxy-groups"] == [source["proxy-groups"][0], group("Default", "US  01")]
    assert result["proxies"] == source["proxies"]


@pytest.mark.parametrize("include", ["include-all", "include-all-providers", "Include-All-Providers"])
def test_dynamic_provider_flags_accept_mihomo_integer_boolean_values(include):
    source = {"proxies": [node(**{"dialer-proxy": "Dynamic"})],
              "proxy-groups": [group("Dynamic", **{include: 1})],
              "proxy-providers": {"live": {"type": "inline", "payload": []}}}

    result = build(source)

    assert result["proxy-providers"] == source["proxy-providers"]


@pytest.mark.parametrize("scheme,protocol", [("tailscale", "tailscale"), ("ts", "tailscale"),
                                             ("easytier", "easytier"), ("et", "easytier")])
def test_overlay_dns_keeps_provider_containing_the_named_outbound(scheme, protocol):
    source = {"proxies": [node()], "dns": {"nameserver": [f"{scheme}://overlay"]},
              "proxy-providers": {
                  "overlay-dns": {"type": "inline", "payload": [{"name": "overlay", "type": protocol}]},
                  "unused": {"type": "inline", "payload": []},
              }}

    result = build(source)

    assert result["proxy-providers"] == {"overlay-dns": source["proxy-providers"]["overlay-dns"]}
    assert result["dns"] == source["dns"]


@pytest.mark.parametrize("options,target", [
    ({"override": {"additional-prefix": "vpn-"}}, "vpn-overlay"),
    ({"override": {"additional-suffix": "-vpn"}}, "overlay-vpn"),
    ({"override": {"proxy-name": [{"pattern": "overlay", "target": "renamed"}]}}, "renamed"),
    ({"override": {"override-expr": ['.name = "renamed"']}}, "renamed"),
])
def test_overlay_dns_keeps_inline_provider_with_dynamic_name_overrides(options, target):
    source = {"proxies": [node()], "dns": {"nameserver": [f"tailscale://{target}"]},
              "proxy-providers": {
                  "overlay-dns": {"type": "inline", "payload": [{"name": "overlay", "type": "tailscale"}], **options},
                  "unused": {"type": "inline", "payload": []},
              }}

    result = build(source)

    assert result["proxy-providers"] == {"overlay-dns": source["proxy-providers"]["overlay-dns"]}


def test_overlay_dns_understands_provider_node_name_aliases_and_numeric_values():
    source = {"proxies": [node()], "dns": {"nameserver": ["tailscale://123"]},
              "proxy-providers": {"overlay-dns": {"type": "inline", "payload": [{"Name": 123, "type": "tailscale"}]}}}

    result = build(source)

    assert result["proxy-providers"] == source["proxy-providers"]


def test_same_named_raw_proxy_does_not_hide_the_provider_dns_outbound():
    source = {"proxies": [node(), node(name="overlay")], "dns": {"nameserver": ["tailscale://overlay"]},
              "proxy-providers": {"overlay-dns": {"type": "inline", "payload": [{"name": "overlay", "type": "tailscale"}]}}}

    result = build(source)

    assert result["proxy-providers"] == source["proxy-providers"]


@pytest.mark.parametrize("provider", [{"type": "http", "url": "https://example.com/remote-nodes"},
                                     {"type": "file", "path": "./providers/nodes.yaml"},
                                     {"type": "inline", "payload": [node()],
                                      "override": {"override-expr": ['.dialer-proxy = "Remote relay"']}}])
def test_reachable_opaque_provider_preserves_possible_hidden_node_dependencies(provider):
    source = {"proxies": [node(**{"dialer-proxy": "Dynamic"})],
              "proxy-groups": [group("Dynamic", use=["remote"]), group("Remote relay", "DIRECT")],
              "proxy-providers": {"remote": provider},
              "sub-rules": {"remote-entry": ["RULE-SET,remote-rule,Remote relay", "MATCH,Remote relay"]},
              "rule-providers": {"remote-rule": rule_provider(), "unused": rule_provider()}}

    result = build(source)

    assert result["proxy-groups"] == source["proxy-groups"] + [group("Default", "US  01")]
    assert result["sub-rules"] == source["sub-rules"]
    assert result["rule-providers"] == {"remote-rule": source["rule-providers"]["remote-rule"]}
    nodes = normalize_nodes([clash_to_ir(proxy) for proxy in source["proxies"]])
    _, warnings = build_mihomo_config(nodes, policy(), source)
    assert any(w["code"] == "opaque_proxy_provider_dependencies" for w in warnings)
    assert "remote-nodes" not in str(warnings)


def test_unpublished_template_subrules_do_not_shadow_retained_native_subrules():
    source = {"proxies": [node()], "proxy-groups": [group("Keep", "DIRECT")],
              "sub-rules": {"entry": ["MATCH,Keep"]}}
    generated = policy()
    generated["sub-rules"] = {"entry": ["MATCH,DIRECT"]}
    generated["rules"].insert(0, "SUB-RULE,(NETWORK,TCP),entry")

    result = build(source, generated)

    assert result["sub-rules"] == source["sub-rules"]
    assert group("Keep", "DIRECT") in result["proxy-groups"]


@pytest.mark.parametrize("subrules", [None, [], {"entry": "MATCH,DIRECT"}])
def test_malformed_subrules_fail_without_leaking_source_values(subrules):
    from app.core.platforms.mihomo import NativeMihomoProfileError

    with pytest.raises(NativeMihomoProfileError, match="sub-rules"):
        build({"proxies": [node()], "sub-rules": subrules})


@pytest.mark.parametrize("endpoint", ["render", "subscribe", "profile", "workspace"])
def test_publication_prunes_unused_airport_definitions(monkeypatch, tmp_path, endpoint):
    source = {"proxies": [node()], "dns": {"nameserver": ["https://dns.example/query#DNS Exit"]},
              "proxy-groups": [group("DNS Exit", "DIRECT"), group("Airport unused", "DIRECT")],
              "rule-providers": {"unused": rule_provider()},
              "proxy-providers": {"unused": {"type": "http", "url": "https://example.com/nodes"}},
              "rules": ["RULE-SET,unused,Airport unused", "MATCH,Airport unused"]}

    async def fetch(_url):
        return render_yaml(source)

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    request = {"subscription_url": "https://example.com/source", "target": "mihomo"}
    with TestClient(app) as client:
        if endpoint == "render":
            response = client.post("/render", json=request)
        elif endpoint == "subscribe":
            response = client.get("/subscribe", params=request)
        elif endpoint == "profile":
            saved = client.post("/profiles", json={**request, "publication_targets": ["mihomo"]})
            assert saved.status_code == 201, saved.text
            response = client.get(saved.json()["subscribe_urls"]["clash"])
        else:
            preview = client.post("/workspace/preview", json=request)
            assert preview.status_code == 200, preview.text
            response = client.post("/compile", json={"target": "mihomo", "workspace": preview.json()["workspace"]})
        assert response.status_code == 200, response.text
        result = YAML(typ="safe").load(response.text)

    assert group("DNS Exit", "DIRECT") in result["proxy-groups"]
    assert not any(g["name"] == "Airport unused" for g in result["proxy-groups"])
    assert "unused" not in result.get("rule-providers", {})
    assert "unused" not in result.get("proxy-providers", {})
    assert result["dns"] == source["dns"]
    assert result["proxies"] == source["proxies"]
