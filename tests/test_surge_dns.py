"""Surge must disclose node DNS settings it cannot preserve faithfully."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from app.core.platforms.surge import build_surge_config
from app.ir import ProxyNode


def _node(server: str = "node.example", protocol: str = "ss") -> ProxyNode:
    return ProxyNode(
        name="Node", protocol=protocol, server=server, port=443,
        extra={"cipher": "aes-128-gcm", "password": "example-password"},
    )


@pytest.mark.parametrize("nameservers", [
    ["https://resolver.example/dns-query/private-subscriber-token"],
    ["192.0.2.53", "192.0.2.54"],
    "https://resolver.example/dns-query#DIRECT&h3=true",
    ["tls://resolver.example#source-group"],
])
def test_custom_node_dns_is_reported_without_expanding_its_scope(nameservers) -> None:
    dns = {"proxy-server-nameserver": nameservers}
    original_dns = deepcopy(dns)
    expected_conf, _ = build_surge_config([_node()], [], ["MATCH,DIRECT"], {})

    conf, warnings = build_surge_config(
        [_node()], [], ["MATCH,DIRECT"], {}, dns_config=dns,
    )

    assert conf == expected_conf
    assert dns == original_dns
    assert len(warnings) == 1
    assert warnings[0]["code"] == "unsupported_node_dns"
    assert warnings[0]["fields"] == ["proxy-server-nameserver"]
    assert "[Host]" in warnings[0]["suggestion"]
    serialized = json.dumps(warnings)
    for private_value in ("resolver.example", "private-subscriber-token", "source-group", "192.0.2.53"):
        assert private_value not in serialized


def test_node_dns_policy_is_reported_without_exposing_domains_or_resolvers() -> None:
    _, warnings = build_surge_config(
        [_node()], [], [], {},
        dns_config={
            "proxy-server-nameserver": ["223.5.5.5", "119.29.29.29"],
            "proxy-server-nameserver-policy": {
                "secret-node.example": ["https://resolver.example/private-token"],
            },
        },
    )

    assert warnings[0]["code"] == "unsupported_node_dns"
    assert warnings[0]["fields"] == ["proxy-server-nameserver-policy"]
    assert "secret-node" not in json.dumps(warnings)
    assert "private-token" not in json.dumps(warnings)


@pytest.mark.parametrize("dns", [
    None,
    {},
    {"proxy-server-nameserver": []},
    {"proxy-server-nameserver": ["223.5.5.5", "119.29.29.29"]},
    {"proxy-server-nameserver": ["119.29.29.29", "223.5.5.5", "223.5.5.5"]},
    {"nameserver-policy": {"traffic.example": "https://resolver.example/dns-query"}},
])
def test_default_node_dns_does_not_create_a_compatibility_warning(dns) -> None:
    _, warnings = build_surge_config([_node()], [], [], {}, dns_config=dns)

    assert warnings == []


@pytest.mark.parametrize("nodes", [
    [],
    [_node("192.0.2.1")],
    [_node("2001:db8::1")],
    [_node(protocol="hysteria2")],
    [_node("192.0.2.1"), _node("unsupported.example", protocol="hysteria2")],
])
def test_unused_node_dns_does_not_warn_when_no_domain_nodes_are_emitted(nodes) -> None:
    _, warnings = build_surge_config(
        nodes, [], [], {},
        dns_config={"proxy-server-nameserver": ["https://resolver.example/private-token"]},
    )

    assert all(warning["code"] != "unsupported_node_dns" for warning in warnings)
