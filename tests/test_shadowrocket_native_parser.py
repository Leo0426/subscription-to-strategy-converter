import base64
from dataclasses import asdict
import json
from urllib.parse import quote

import pytest

from app.core.normalizer import normalize_nodes_with_source_names


def _base64(value):
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _ssr(name, password="test-password"):
    body = f"edge.example.com:443:origin:chacha20-ietf:http_simple:{_base64(password)}/?remarks={_base64(name)}&uot=0&udpport=0"
    return "ssr://" + _base64(body)


def _parse(content):
    from app.core.parsers.shadowrocket import parse_shadowrocket_inventory
    return parse_shadowrocket_inventory(content)


def test_outer_base64_inventory_keeps_all_original_ssr_and_anytls_names():
    uris = [_ssr(f" 香港  {index:02} ") for index in range(83)]
    uris += [f"anytls://test-password@any.example.com:8443?insecure=1&sni=peer.example.com#{quote(f'美国  {index:02}') }"
             for index in range(61)]
    source = _base64("#provider metadata\n" + "\r\n".join(uris))
    nodes = _parse(source)
    assert len(nodes) == 144
    assert (nodes[0].name, nodes[0].protocol, nodes[0].server, nodes[0].port) == (
        " 香港  00 ", "ssr", "edge.example.com", 443,
    )
    assert (nodes[-1].name, nodes[-1].protocol, nodes[-1].server, nodes[-1].port) == (
        "美国  60", "anytls", "any.example.com", 8443,
    )
    pairs = normalize_nodes_with_source_names(nodes)
    assert len(pairs) == 144
    assert pairs[0][0].name == "香港 00"
    assert pairs[0][1] == " 香港  00 "
    assert "test-password" not in json.dumps([asdict(node) for node in nodes])
    assert "peer.example.com" not in json.dumps([asdict(node) for node in nodes])


@pytest.mark.parametrize("scheme", ["vless", "trojan", "anytls", "tuic", "hysteria", "hysteria2", "hy2"])
def test_named_uri_inventory_reads_ipv6_endpoint_without_interpreting_credentials(scheme):
    nodes = _parse(f"# metadata\n{scheme}://opaque-password@[2001:db8::1]:8443?unknown=opaque#US%20%20One\n")
    assert len(nodes) == 1
    assert nodes[0].name == "US  One"
    assert nodes[0].server == "2001:db8::1"
    assert nodes[0].port == 8443
    assert nodes[0].protocol == ("hysteria2" if scheme == "hy2" else scheme)


@pytest.mark.parametrize("uri", [
    "ss://" + _base64("aes-128-gcm:test-password") + "@edge.example.com:443#US%20One",
    "ss://" + _base64("aes-128-gcm:test-password@edge.example.com:443") + "#US%20One",
    "ss://aes-128-gcm:test-password@edge.example.com:443#US%20One",
    "vmess://" + _base64(json.dumps({"ps": "US One", "add": "edge.example.com", "port": "443", "id": "test-password"})),
])
def test_ss_variants_and_vmess_json_expose_only_policy_inventory(uri):
    node = _parse(uri)[0]
    assert (node.name, node.server, node.port) == ("US One", "edge.example.com", 443)
    assert "test-password" not in json.dumps(asdict(node))


def test_native_ini_inventory_keeps_protocols_and_opaque_options_without_conversion():
    source = '''#!MANAGED-CONFIG https://example.com/sub?token=test-secret
[General]
dns-server = https://dns.example.com/private-token
[Proxy]
  US  One = ss, edge.example.com, 443, password="test-secret,with,commas", obfs=http, obfs-host=cdn.example.com:
WG Two = wireguard, 203.0.113.2, 51820, privateKey=private-key, publicKey=public-key
Custom Three = future-native-protocol, future.example.com, 5555, opaque="untouched"
Local = direct
[Proxy Group]
Airport = select, US  One
[Rule]
FINAL,Airport
'''
    nodes = _parse(source)
    assert [(node.name, node.protocol, node.server, node.port) for node in nodes] == [
        ("US  One", "ss", "edge.example.com", 443),
        ("WG Two", "wireguard", "203.0.113.2", 51820),
        ("Custom Three", "future-native-protocol", "future.example.com", 5555),
    ]
    assert "test-secret" not in json.dumps([asdict(node) for node in nodes])
    assert "private-key" not in json.dumps([asdict(node) for node in nodes])


def test_distinct_native_options_are_not_deduplicated_by_endpoint():
    first = _parse("[Proxy]\nFirst = anytls, edge.example.com, 443, password=first-secret\n")[0]
    second = _parse("[Proxy]\nSecond = anytls, edge.example.com, 443, password=second-secret\n")[0]
    assert first.extra != second.extra
    assert len(normalize_nodes_with_source_names([first, second])) == 2
    assert first.extra == _parse("[Proxy]\nFirst = anytls, edge.example.com, 443, password=first-secret\n")[0].extra


def test_ssr_standard_base64_remarks_keep_plus_characters():
    remarks = base64.b64encode("😀 One".encode()).decode()
    body = f"edge.example.com:443:origin:chacha20-ietf:http_simple:cGFzcw/?remarks={remarks}"
    assert _parse("ssr://" + _base64(body))[0].name == "😀 One"


def test_native_ini_accepts_comments_before_its_first_section():
    nodes = _parse("; source comment\n// source comment\n[Proxy]\nUS = anytls, edge.example.com, 443, password=opaque\n")
    assert nodes[0].name == "US"


@pytest.mark.parametrize("encoded", [False, True])
def test_native_subscription_status_and_remarks_metadata_are_not_nodes(encoded):
    source = "STATUS=metadata-only\nREMARKS=opaque-title\n# metadata comment\n" + _ssr("US")
    nodes = _parse(_base64(source) if encoded else source)
    assert len(nodes) == 1
    assert nodes[0].name == "US"


@pytest.mark.parametrize("encoded", [False, True])
def test_source_parser_returns_complete_native_ini_with_comments_and_bom(encoded):
    from app.core.parsers.shadowrocket import parse_shadowrocket_source
    profile = ("\ufeff// source comment\r\n# metadata\r\n[General]\r\nipv6 = true\r\n"
               "[Proxy]\r\nUS = anytls, edge.example.com, 443, password=opaque\r\n"
               "[Rule]\r\nFINAL,DIRECT\r\n")
    nodes, native_profile = parse_shadowrocket_source(_base64(profile) if encoded else profile)
    assert [node.name for node in nodes] == ["US"]
    assert native_profile == profile


@pytest.mark.parametrize("encoded", [False, True])
def test_source_parser_does_not_return_a_native_ini_for_uri_subscriptions(encoded):
    from app.core.parsers.shadowrocket import parse_shadowrocket_source
    source = "STATUS=metadata\n" + _ssr("US")
    nodes, native_profile = parse_shadowrocket_source(_base64(source) if encoded else source)
    assert [node.name for node in nodes] == ["US"]
    assert native_profile is None


@pytest.mark.parametrize("source", [
    "", "#metadata only", "not a subscription secret-token",
    "anytls://test-secret@edge.example.com:443",  # no node name
    "anytls://test-secret@edge.example.com:443#%20",  # blank name
    "anytls://test-secret@edge.example.com:443#invalid%ZZ",
    "anytls://test-secret@edge.example.com:443#injected%0ANode",
    "anytls://test-secret@edge.example.com:invalid#US",
    "anytls://test-secret@edge.example.com:65536#US",
    "anytls://test-secret@edge.example.com#US",
    "anytls://test-secret@:443#US",
    "ssr://invalid-secret!",
    "ssr://" + _base64("edge.example.com:443:origin:aes-128-gcm:http_simple:cGFzcw/?remarks=QQ&remarks=Qg"),
    "ssr://" + _base64("edge.example.com:443:origin:aes-128-gcm:http_simple:cGFzcw/"),
    "vmess://" + _base64('{"ps":"US","ps":"Ambiguous","add":"edge.example.com","port":443}'),
    "vmess://" + _base64('{"ps":"US","add":"edge.example.com","port":"test-secret"}'),
    "[Proxy]\nMissing = anytls, edge.example.com\n",
    "[Proxy]\nMissing = anytls, edge.example.com, test-secret\n",
    "[Proxy]\nMalformed line with test-secret\n",
    "[Proxy]\nMissing = anytls, edge.example.com, 443, password=\"unterminated-secret\n",
    "[General]\nipv6 = true\n",  # no inventory
])
def test_malformed_native_source_fails_with_static_error_without_credentials(source):
    with pytest.raises(ValueError) as error:
        _parse(source)
    assert type(error.value).__name__ == "ShadowrocketParseError"
    assert str(error.value) == "Invalid native Shadowrocket subscription"


@pytest.mark.parametrize("source", [
    "anytls://first@edge.example.com:443#US\nanytls://second@other.example.com:443#US",
    "[Proxy]\nUS = anytls, edge.example.com, 443, password=first\nUS = anytls, other.example.com, 443, password=second\n",
])
def test_duplicate_native_node_names_fail_instead_of_guessing_policy_references(source):
    with pytest.raises(ValueError, match="^Invalid native Shadowrocket subscription$"):
        _parse(source)
