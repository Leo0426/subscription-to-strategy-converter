import pytest

from app.core.parsers.surge import SurgeParseError, looks_like_surge_config, parse_surge_nodes


def test_parse_surge_proxy_protocols_and_options() -> None:
    nodes = parse_surge_nodes(
        """
[Proxy]
VMess-WS = vmess, vm.example.com, 443, username=uuid-1, ws=true, ws-path=/socket, ws-headers=Host:cdn.example.com|X-Test:yes, tls=true, sni=edge.example.com
HTTPS = https, http.example.com, 8443, username=leo, password=secret, skip-cert-verify=true
SOCKS = socks5-tls, socks.example.com, 1080, username=leo, password=secret
Built-in = direct

[Proxy Group]
Proxy = select, VMess-WS, HTTPS, SOCKS
"""
    )

    assert [node.protocol for node in nodes] == ["vmess", "http", "socks5"]
    assert nodes[0].extra["uuid"] == "uuid-1"
    assert nodes[0].transport.type == "ws"
    assert nodes[0].transport.path == "/socket"
    assert nodes[0].transport.host == "cdn.example.com"
    assert nodes[0].tls.sni == "edge.example.com"
    assert nodes[1].tls.enabled is True
    assert nodes[1].tls.insecure is True
    assert nodes[2].tls.enabled is True


def test_surge_detection_requires_proxy_section() -> None:
    assert looks_like_surge_config("[Proxy]\nHK = ss, example.com, 443") is True
    assert looks_like_surge_config("not YAML; text mentions [Proxy] inline") is False


def test_surge_parser_preserves_quoted_commas_in_option_values() -> None:
    nodes = parse_surge_nodes(
        '[Proxy]\nQuoted = ss, example.com, 443, encrypt-method=aes-128-gcm, password="secret,with,commas"'
    )

    assert nodes[0].extra["password"] == "secret,with,commas"


def test_recognized_surge_proxy_rejects_invalid_port() -> None:
    with pytest.raises(SurgeParseError, match="invalid proxy port at line 3"):
        parse_surge_nodes("\n[Proxy]\nBad = ss, example.com, nope, password=secret")
