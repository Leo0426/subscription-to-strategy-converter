from app.core.normalizer import normalize_nodes
from app.ir import ProxyNode


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
