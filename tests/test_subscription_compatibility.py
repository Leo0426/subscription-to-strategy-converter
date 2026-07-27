from __future__ import annotations

import pytest

from app.core.parsers.clash import clash_to_ir, ir_to_clash_dict
from app.core.subscription import SubscriptionError, load_subscription


@pytest.mark.asyncio
async def test_non_clash_subscription_uses_configured_compatibility_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        assert url == "https://example.com/base64-subscription"
        return "c3M6Ly9leGFtcGxl"

    async def fake_convert_subscription(url: str) -> str:
        assert url == "https://example.com/base64-subscription"
        return """
proxies:
  - name: HK-01
    type: ss
    server: hk.example.com
    port: 443
    cipher: aes-128-gcm
    password: secret
"""

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setenv("SUBFLOW_SUBCONVERTER_URL", "http://subconverter:25500")
    monkeypatch.setattr(
        "app.core.subscription.convert_subscription_to_clash",
        fake_convert_subscription,
    )

    nodes, raw_config = await load_subscription("https://example.com/base64-subscription")

    assert [node.name for node in nodes] == ["HK-01"]
    assert raw_config["source-format"] == "subconverter"
    assert raw_config["proxies"][0]["server"] == "hk.example.com"


@pytest.mark.asyncio
async def test_invalid_subscription_keeps_primary_error_when_adapter_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_subscription(url: str) -> str:
        return "not a supported subscription"

    async def fake_convert_subscription(url: str) -> str:
        raise RuntimeError("adapter should not be called")

    monkeypatch.delenv("SUBFLOW_SUBCONVERTER_URL", raising=False)
    monkeypatch.setattr("app.core.subscription.fetch_subscription", fake_fetch_subscription)
    monkeypatch.setattr(
        "app.core.subscription.convert_subscription_to_clash",
        fake_convert_subscription,
    )

    with pytest.raises(SubscriptionError, match="Clash YAML or Surge config"):
        await load_subscription("https://example.com/invalid")


def test_clash_round_trip_preserves_unmodeled_protocol_fields() -> None:
    source = {
        "name": "WG-01",
        "type": "wireguard",
        "server": "wg.example.com",
        "port": 51820,
        "ip": "172.16.0.2",
        "private-key": "private",
        "public-key": "public",
        "pre-shared-key": "psk",
        "reserved": [1, 2, 3],
        "udp": True,
        "mtu": 1280,
    }

    rendered = ir_to_clash_dict(clash_to_ir(source))

    assert rendered == source


def test_clash_round_trip_preserves_new_fields_on_known_protocols() -> None:
    source = {
        "name": "VLESS-01",
        "type": "vless",
        "server": "vless.example.com",
        "port": 443,
        "uuid": "uuid-1",
        "tls": True,
        "client-fingerprint": "chrome",
        "packet-encoding": "xudp",
        "flow": "xtls-rprx-vision",
    }

    rendered = ir_to_clash_dict(clash_to_ir(source))

    assert rendered["client-fingerprint"] == "chrome"
    assert rendered["packet-encoding"] == "xudp"
    assert rendered["flow"] == "xtls-rprx-vision"
