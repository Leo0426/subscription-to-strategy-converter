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


def test_clash_round_trip_preserves_unmodeled_anytls_tls_fields() -> None:
    source = {
        "name": "AnyTLS-01",
        "type": "anytls",
        "server": "shared.example.com",
        "port": 443,
        "password": "secret",
        "udp": True,
        "sni": "edge-01.example.com",
        "skip-cert-verify": True,
    }

    assert ir_to_clash_dict(clash_to_ir(source)) == source


def test_clash_round_trip_keeps_vmess_http_transport_as_http() -> None:
    source = {
        "name": "VMess-HTTP",
        "type": "vmess",
        "server": "vmess.example.com",
        "port": 80,
        "uuid": "uuid-1",
        "alterId": 0,
        "cipher": "auto",
        "network": "http",
        "http-opts": {"path": ["/transport"], "headers": {"Host": ["cdn.example.com"]}},
    }

    assert ir_to_clash_dict(clash_to_ir(source)) == source


@pytest.mark.parametrize(
    "source",
    [
        {
            "name": "VLESS-Reality",
            "type": "vless",
            "server": "vless.example.com",
            "port": 443,
            "uuid": "uuid-1",
            "tls": True,
            "reality-opts": {
                "public-key": "public-key-1",
                "short-id": "0123456789abcdef",
                "support-x25519mlkem768": True,
            },
        },
        {
            "name": "VMess-WS",
            "type": "vmess",
            "server": "ws.example.com",
            "port": 443,
            "uuid": "uuid-2",
            "alterId": 0,
            "cipher": "auto",
            "network": "ws",
            "ws-opts": {
                "path": "/transport",
                "headers": {"Host": "cdn.example.com"},
                "max-early-data": 2048,
                "early-data-header-name": "Sec-WebSocket-Protocol",
            },
        },
        {
            "name": "VMess-gRPC",
            "type": "vmess",
            "server": "grpc.example.com",
            "port": 443,
            "uuid": "uuid-3",
            "alterId": 0,
            "cipher": "auto",
            "network": "grpc",
            "grpc-opts": {
                "grpc-service-name": "transport",
                "grpc-mode": "multi",
            },
        },
        {
            "name": "VMess-H2",
            "type": "vmess",
            "server": "h2.example.com",
            "port": 443,
            "uuid": "uuid-4",
            "alterId": 0,
            "cipher": "auto",
            "network": "h2",
            "h2-opts": {
                "path": "/transport",
                "host": ["primary.example.com", "fallback.example.com"],
                "unknown-option": {"enabled": True},
            },
        },
    ],
    ids=["reality", "websocket", "grpc", "http2"],
)
def test_clash_round_trip_preserves_unknown_nested_transport_options(
    source: dict,
) -> None:
    assert ir_to_clash_dict(clash_to_ir(source)) == source


def test_clash_render_uses_modified_ir_values_and_keeps_unknown_ws_reality_options() -> None:
    source = {
        "name": "VLESS-Reality-WS",
        "type": "vless",
        "server": "vless.example.com",
        "port": 443,
        "uuid": "uuid-1",
        "tls": True,
        "reality-opts": {
            "public-key": "old-public-key",
            "short-id": "old-short-id",
            "support-x25519mlkem768": True,
        },
        "network": "ws",
        "ws-opts": {
            "path": "/old",
            "headers": {"Host": "old.example.com", "X-Preserved": "yes"},
            "max-early-data": 2048,
        },
    }
    node = clash_to_ir(source)
    node.tls.reality["public_key"] = "new-public-key"
    node.tls.reality["short_id"] = "new-short-id"
    node.transport.path = "/new"
    node.transport.host = "new.example.com"

    rendered = ir_to_clash_dict(node)

    assert rendered["reality-opts"] == {
        "public-key": "new-public-key",
        "short-id": "new-short-id",
        "support-x25519mlkem768": True,
    }
    assert rendered["ws-opts"] == {
        "path": "/new",
        "headers": {"Host": "new.example.com", "X-Preserved": "yes"},
        "max-early-data": 2048,
    }


def test_clash_render_uses_modified_grpc_service_and_keeps_unknown_options() -> None:
    source = {
        "name": "VMess-gRPC",
        "type": "vmess",
        "server": "grpc.example.com",
        "port": 443,
        "uuid": "uuid-1",
        "alterId": 0,
        "cipher": "auto",
        "network": "grpc",
        "grpc-opts": {"grpc-service-name": "old", "grpc-mode": "multi"},
    }
    node = clash_to_ir(source)
    node.transport.service_name = "new"

    rendered = ir_to_clash_dict(node)

    assert rendered["grpc-opts"] == {
        "grpc-service-name": "new",
        "grpc-mode": "multi",
    }


def test_clash_render_uses_modified_h2_values_and_keeps_unknown_options() -> None:
    source = {
        "name": "VMess-H2",
        "type": "vmess",
        "server": "h2.example.com",
        "port": 443,
        "uuid": "uuid-1",
        "alterId": 0,
        "cipher": "auto",
        "network": "h2",
        "h2-opts": {
            "path": "/old",
            "host": ["old.example.com", "fallback.example.com"],
            "unknown-option": {"enabled": True},
        },
    }
    node = clash_to_ir(source)
    node.transport.path = "/new"
    node.transport.host = "new.example.com"

    rendered = ir_to_clash_dict(node)

    assert rendered["h2-opts"] == {
        "path": "/new",
        "host": ["new.example.com", "fallback.example.com"],
        "unknown-option": {"enabled": True},
    }


def test_clash_render_does_not_restore_cleared_modeled_nested_values() -> None:
    ws_node = clash_to_ir({
        "name": "WS",
        "type": "vless",
        "server": "ws.example.com",
        "port": 443,
        "uuid": "uuid-ws",
        "tls": True,
        "reality-opts": {"public-key": "old", "short-id": "old", "future": True},
        "network": "ws",
        "ws-opts": {"path": "/old", "headers": {"Host": "old.example.com"}},
    })
    ws_node.tls.reality["public_key"] = ""
    ws_node.tls.reality["short_id"] = ""
    ws_node.transport.path = ""
    ws_node.transport.host = ""

    grpc_node = clash_to_ir({
        "name": "gRPC",
        "type": "vmess",
        "server": "grpc.example.com",
        "port": 443,
        "uuid": "uuid-grpc",
        "alterId": 0,
        "cipher": "auto",
        "network": "grpc",
        "grpc-opts": {"grpc-service-name": "old", "future": True},
    })
    grpc_node.transport.service_name = ""

    h2_node = clash_to_ir({
        "name": "H2",
        "type": "vmess",
        "server": "h2.example.com",
        "port": 443,
        "uuid": "uuid-h2",
        "alterId": 0,
        "cipher": "auto",
        "network": "h2",
        "h2-opts": {"path": "/old", "host": ["old.example.com"], "future": True},
    })
    h2_node.transport.path = ""
    h2_node.transport.host = ""

    rendered_ws = ir_to_clash_dict(ws_node)
    rendered_grpc = ir_to_clash_dict(grpc_node)
    rendered_h2 = ir_to_clash_dict(h2_node)

    assert rendered_ws["reality-opts"] == {
        "public-key": "",
        "short-id": "",
        "future": True,
    }
    assert rendered_ws["ws-opts"] == {"path": "", "headers": {"Host": ""}}
    assert rendered_grpc["grpc-opts"] == {"grpc-service-name": "", "future": True}
    assert rendered_h2["h2-opts"] == {"path": "", "host": [], "future": True}


@pytest.mark.parametrize(
    "source",
    [
        {
            "name": "VLESS-XHTTP",
            "type": "vless",
            "server": "xhttp.example.com",
            "port": 443,
            "uuid": "uuid-1",
            "network": "xhttp",
            "xhttp-opts": {"path": "/transport", "mode": "stream-up"},
        },
        {
            "name": "VMess-mKCP",
            "type": "vmess",
            "server": "mkcp.example.com",
            "port": 443,
            "uuid": "uuid-2",
            "alterId": 0,
            "cipher": "auto",
            "network": "mkcp",
            "mkcp-opts": {"mtu": 1350, "congestion": False},
        },
        {
            "name": "VMess-Mekya",
            "type": "vmess",
            "server": "mekya.example.com",
            "port": 443,
            "uuid": "uuid-3",
            "alterId": 0,
            "cipher": "auto",
            "network": "mekya",
            "mekya-opts": {
                "url": "https://transport.example.com/mekya",
                "max-write-delay": 80,
            },
        },
    ],
    ids=["xhttp", "mkcp", "mekya"],
)
def test_clash_round_trip_preserves_unmodeled_transports(source: dict) -> None:
    assert ir_to_clash_dict(clash_to_ir(source)) == source
