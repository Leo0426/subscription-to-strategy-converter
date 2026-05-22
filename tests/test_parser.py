import pytest

from app.core.parser import ParseError, parse_clash_yaml


def test_clash_yaml_can_be_parsed() -> None:
    nodes = parse_clash_yaml(
        """
proxies:
  - name: 香港-01
    type: ss
    server: example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""
    )

    assert nodes == [
        {
            "name": "香港-01",
            "type": "ss",
            "server": "example.com",
            "port": 443,
            "cipher": "aes-128-gcm",
            "password": "secret",
        }
    ]


def test_empty_subscription_returns_error() -> None:
    with pytest.raises(ParseError, match="YAML must be an object"):
        parse_clash_yaml("")


def test_proxy_requires_core_fields() -> None:
    with pytest.raises(ParseError, match="missing required fields"):
        parse_clash_yaml(
            """
proxies:
  - name: bad
    type: ss
"""
        )
