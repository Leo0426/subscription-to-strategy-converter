from app.core.normalizer import normalize_nodes
from app.ir import ProxyNode, TLSConfig


def _ss(name: str, server: str, port: int = 443) -> ProxyNode:
    return ProxyNode(name=name, protocol="ss", server=server, port=port)


def test_duplicate_nodes_are_deduplicated() -> None:
    nodes = normalize_nodes([
        _ss(" hk ", "example.com"),
        _ss("hk copy", "example.com"),  # same server:port → duplicate
    ])

    assert len(nodes) == 1
    assert nodes[0].name == "hk"


def test_duplicate_names_are_renamed() -> None:
    nodes = normalize_nodes([
        _ss("香港", "a.example.com"),
        _ss("香港", "b.example.com"),
        _ss("香港", "c.example.com"),
    ])

    assert [n.name for n in nodes] == ["香港", "香港-2", "香港-3"]


def test_empty_name_gets_generated() -> None:
    nodes = normalize_nodes([_ss("   ", "example.com")])
    assert nodes[0].name == "node-1"


def test_same_endpoint_with_different_credentials_is_not_deduplicated() -> None:
    nodes = normalize_nodes([
        ProxyNode(
            name="A",
            protocol="ss",
            server="shared.example.com",
            port=443,
            extra={"cipher": "aes-128-gcm", "password": "first"},
        ),
        ProxyNode(
            name="B",
            protocol="ss",
            server="shared.example.com",
            port=443,
            extra={"cipher": "aes-128-gcm", "password": "second"},
        ),
    ])

    assert [node.name for node in nodes] == ["A", "B"]


def test_same_endpoint_with_different_sni_is_not_deduplicated() -> None:
    nodes = normalize_nodes([
        ProxyNode(
            name="A",
            protocol="anytls",
            server="shared.example.com",
            port=443,
            tls=TLSConfig(enabled=True, sni="edge-a.example.com"),
        ),
        ProxyNode(
            name="B",
            protocol="anytls",
            server="shared.example.com",
            port=443,
            tls=TLSConfig(enabled=True, sni="edge-b.example.com"),
        ),
    ])

    assert [node.name for node in nodes] == ["A", "B"]


def test_future_fields_with_bool_and_int_values_are_not_deduplicated() -> None:
    nodes = normalize_nodes([
        ProxyNode(
            name="bool",
            protocol="future",
            server="shared.example.com",
            port=443,
            extra={"future-option": True},
        ),
        ProxyNode(
            name="int",
            protocol="future",
            server="shared.example.com",
            port=443,
            extra={"future-option": 1},
        ),
    ])

    assert [node.name for node in nodes] == ["bool", "int"]


def test_mixed_type_mapping_keys_freeze_deterministically() -> None:
    extra = {"future-options": {1: 0, "1": "zero"}}
    nodes = normalize_nodes([
        ProxyNode(name="A", protocol="future", server="shared.example.com", port=443, extra=extra),
        ProxyNode(name="B", protocol="future", server="shared.example.com", port=443, extra=extra),
    ])

    assert [node.name for node in nodes] == ["A"]
