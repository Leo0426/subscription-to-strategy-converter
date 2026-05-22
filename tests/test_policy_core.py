from __future__ import annotations

from app.core.policy_analyzer import analyze_workspace
from app.core.policy_graph import build_policy_graph
from app.core.policy_simulator import simulate_destination
from app.core.policy_workspace import config_to_workspace, workspace_to_mihomo_config
from app.ir import ProxyNode


def _node(name: str = "HK-01") -> ProxyNode:
    return ProxyNode(name=name, protocol="ss", server="hk.example.com", port=443)


def test_policy_workspace_builds_from_mihomo_config() -> None:
    config = {
        "mixed-port": 7890,
        "proxies": [],
        "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["HK-01", "DIRECT"]}],
        "rule-providers": {"ai": {"type": "http", "behavior": "classical", "url": "https://example.com/ai.yaml"}},
        "rules": ["DOMAIN-SUFFIX,openai.com,PROXY", "RULE-SET,ai,PROXY", "MATCH,DIRECT"],
    }

    workspace = config_to_workspace(config, [_node()], "mihomo")

    assert workspace.target == "mihomo"
    assert workspace.settings["mixed-port"] == 7890
    assert workspace.proxies[0].name == "HK-01"
    assert workspace.proxy_groups[0].members == ["HK-01", "DIRECT"]
    assert workspace.rules[0].type == "DOMAIN-SUFFIX"
    assert workspace.rules[1].provider == "ai"
    assert workspace.rule_providers[0].name == "ai"


def test_workspace_compiles_back_to_mihomo_config() -> None:
    workspace = config_to_workspace(
        {
            "mixed-port": 7890,
            "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["HK-01"]}],
            "rule-providers": {},
            "rules": ["MATCH,PROXY"],
        },
        [_node()],
    )

    config = workspace_to_mihomo_config(workspace)

    assert config["mixed-port"] == 7890
    assert config["proxies"][0]["name"] == "HK-01"
    assert config["proxy-groups"][0]["proxies"] == ["HK-01"]
    assert config["rules"] == ["MATCH,PROXY"]


def test_analyzer_reports_structural_findings() -> None:
    workspace = config_to_workspace(
        {
            "proxy-groups": [
                {"name": "A", "type": "select", "proxies": ["B"]},
                {"name": "B", "type": "select", "proxies": ["A"]},
                {"name": "Unused", "type": "select", "proxies": ["MISSING"]},
            ],
            "rule-providers": {},
            "rules": [
                "RULE-SET,missing,A",
                "DOMAIN,example.com,MISSING_TARGET",
                "DOMAIN,dup.example.com,A",
                "DOMAIN,dup.example.com,A",
            ],
        },
        [_node()],
    )

    codes = {finding.code for finding in analyze_workspace(workspace)}

    assert "missing_provider" in codes
    assert "missing_rule_target" in codes
    assert "missing_group_member" in codes
    assert "duplicate_rule" in codes
    assert "group_cycle" in codes
    assert "unreachable_group" in codes


def test_simulator_matches_domain_ip_and_match_rules() -> None:
    workspace = config_to_workspace(
        {
            "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["HK-01"]}],
            "rule-providers": {"ai": {"type": "http"}},
            "rules": [
                "RULE-SET,ai,PROXY",
                "DOMAIN,exact.example.com,DIRECT",
                "DOMAIN-SUFFIX,openai.com,PROXY",
                "DOMAIN-KEYWORD,claude,PROXY",
                "IP-CIDR,10.0.0.0/8,DIRECT",
                "MATCH,PROXY",
            ],
        },
        [_node()],
    )

    suffix = simulate_destination(workspace, "chat.openai.com")
    exact = simulate_destination(workspace, "exact.example.com")
    keyword = simulate_destination(workspace, "console.claude.ai")
    cidr = simulate_destination(workspace, "10.1.2.3")
    fallback = simulate_destination(workspace, "unknown.example")

    assert suffix.matched_rule is not None and suffix.matched_rule.type == "DOMAIN-SUFFIX"
    assert suffix.resolved == "HK-01"
    assert exact.resolved == "DIRECT"
    assert keyword.matched_rule is not None and keyword.matched_rule.type == "DOMAIN-KEYWORD"
    assert cidr.resolved == "DIRECT"
    assert fallback.matched_rule is not None and fallback.matched_rule.type == "MATCH"


def test_graph_builder_outputs_stable_nodes_and_edges() -> None:
    workspace = config_to_workspace(
        {
            "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": ["HK-01", "DIRECT"]}],
            "rule-providers": {"ai": {"type": "http"}},
            "rules": ["RULE-SET,ai,PROXY"],
        },
        [_node()],
    )

    graph = build_policy_graph(workspace)
    node_ids = {node.id for node in graph.nodes}
    edge_types = {edge.type for edge in graph.edges}

    assert {"provider:ai", "rule:0", "group:PROXY", "proxy:HK-01", "builtin:DIRECT"} <= node_ids
    assert {"rule-provider", "rule-target", "group-member"} <= edge_types
