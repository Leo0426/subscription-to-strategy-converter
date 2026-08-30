"""Clash/Mihomo YAML proxy dict ↔ ProxyNode IR."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.ir import ProxyNode, TLSConfig, TransportConfig


# ── dict → IR ─────────────────────────────────────────────────────────────


_IDENTITY_FIELDS = {"name", "type", "server", "port"}
_COMMON_OWNED_FIELDS = _IDENTITY_FIELDS | {
    "tls", "sni", "servername",
    "skip-cert-verify", "alpn", "fingerprint",
}
_MODELED_TRANSPORTS = {"ws", "http", "h2", "grpc"}
_PROTOCOL_OWNED_FIELDS = {
    "ss": {"cipher", "password", "plugin", "plugin-opts", "udp"},
    "vmess": {"uuid", "alterId", "cipher", "udp"},
    "vless": {"uuid", "flow", "udp"},
    "trojan": {"password", "udp"},
    "hysteria2": {"password", "obfs", "obfs-password", "up", "down"},
    "tuic": {"uuid", "password", "congestion-controller"},
    "socks5": {"username", "password"},
    "socks": {"username", "password"},
    "http": {"username", "password"},
}


def _passthrough_fields(proxy: dict, protocol: str) -> dict[str, Any]:
    # Only consume common transport/TLS fields for protocols whose semantics
    # this adapter actually models.  New Mihomo protocols must otherwise pass
    # through byte-for-byte (apart from YAML formatting); treating their common
    # looking fields as owned can silently drop connection-critical options.
    if protocol in _PROTOCOL_OWNED_FIELDS:
        owned = _COMMON_OWNED_FIELDS | _PROTOCOL_OWNED_FIELDS[protocol]
        if str(proxy.get("network") or "").lower() in _MODELED_TRANSPORTS:
            owned = owned | {"network"}
    else:
        owned = _IDENTITY_FIELDS
    return deepcopy({key: value for key, value in proxy.items() if key not in owned})


def _tls(proxy: dict) -> TLSConfig:
    protocol = str(proxy.get("type") or "").lower()
    # Trojan and Hysteria2 always use TLS even when the key is absent
    enabled = bool(proxy.get("tls")) or protocol in {"trojan", "hysteria2", "tuic"}

    reality_opts = proxy.get("reality-opts") or {}
    reality: dict[str, Any] = {}
    if isinstance(reality_opts, dict) and reality_opts:
        reality = {
            "public_key": str(reality_opts.get("public-key") or ""),
            "short_id": str(reality_opts.get("short-id") or ""),
        }
        enabled = True

    alpn_raw = proxy.get("alpn", [])
    alpn = list(alpn_raw) if isinstance(alpn_raw, list) else (
        [str(alpn_raw)] if alpn_raw else []
    )

    return TLSConfig(
        enabled=enabled,
        sni=str(proxy.get("sni") or proxy.get("servername") or ""),
        insecure=bool(proxy.get("skip-cert-verify")),
        alpn=alpn,
        fingerprint=str(proxy.get("fingerprint") or ""),
        reality=reality,
    )


def _transport(proxy: dict) -> TransportConfig:
    network = str(proxy.get("network") or "").lower()

    if network == "ws":
        opts = proxy.get("ws-opts") or {}
        raw_headers = opts.get("headers") or {}
        headers = {k: str(v) for k, v in raw_headers.items()}
        host = str(headers.get("Host") or headers.get("host") or "")
        return TransportConfig(
            type="ws",
            path=str(opts.get("path") or ""),
            host=host,
            headers=headers,
        )

    if network == "http":
        # http-opts remains in _clash_passthrough.  Keep the transport kind so
        # rendering does not accidentally turn VMess HTTP into HTTP/2.
        return TransportConfig(type="http")

    if network == "h2":
        opts = proxy.get("h2-opts") or {}
        hosts = opts.get("host") or []
        host = str(hosts[0]) if isinstance(hosts, list) and hosts else ""
        return TransportConfig(type="h2", path=str(opts.get("path") or ""), host=host)

    if network == "grpc":
        opts = proxy.get("grpc-opts") or {}
        return TransportConfig(type="grpc", service_name=str(opts.get("grpc-service-name") or ""))

    return TransportConfig()


def clash_to_ir(proxy: dict) -> ProxyNode:
    """Convert a Clash/Mihomo proxy dict to a ProxyNode IR."""
    protocol = str(proxy.get("type") or "").lower()
    if protocol == "socks":
        protocol = "socks5"

    extra: dict[str, Any] = {}
    passthrough = _passthrough_fields(proxy, protocol)
    if passthrough:
        extra["_clash_passthrough"] = passthrough

    if protocol == "ss":
        extra["cipher"] = str(proxy.get("cipher") or "")
        extra["password"] = str(proxy.get("password") or "")
        if proxy.get("plugin"):
            extra["plugin"] = str(proxy["plugin"])
            if proxy.get("plugin-opts"):
                extra["plugin_opts"] = dict(proxy["plugin-opts"])
        if proxy.get("udp"):
            extra["udp"] = True

    elif protocol == "vmess":
        extra["uuid"] = str(proxy.get("uuid") or "")
        extra["alter_id"] = int(proxy.get("alterId") or 0)
        extra["cipher"] = str(proxy.get("cipher") or "auto")
        if proxy.get("udp"):
            extra["udp"] = True

    elif protocol == "vless":
        extra["uuid"] = str(proxy.get("uuid") or "")
        extra["flow"] = str(proxy.get("flow") or "")
        if proxy.get("udp"):
            extra["udp"] = True

    elif protocol == "trojan":
        extra["password"] = str(proxy.get("password") or "")
        if proxy.get("udp"):
            extra["udp"] = True

    elif protocol == "hysteria2":
        extra["password"] = str(proxy.get("password") or "")
        if proxy.get("obfs"):
            extra["obfs"] = str(proxy["obfs"])
            extra["obfs_password"] = str(proxy.get("obfs-password") or "")
        if proxy.get("up") is not None:
            extra["up"] = proxy["up"]
        if proxy.get("down") is not None:
            extra["down"] = proxy["down"]

    elif protocol == "tuic":
        extra["uuid"] = str(proxy.get("uuid") or "")
        extra["password"] = str(proxy.get("password") or "")
        extra["congestion_controller"] = str(proxy.get("congestion-controller") or "bbr")
        alpn_raw = proxy.get("alpn", [])
        if isinstance(alpn_raw, list) and alpn_raw:
            extra["alpn"] = alpn_raw

    elif protocol in {"socks5", "http"}:
        if proxy.get("username"):
            extra["username"] = str(proxy["username"])
        if proxy.get("password"):
            extra["password"] = str(proxy["password"])

    return ProxyNode(
        name=str(proxy.get("name") or ""),
        protocol=protocol,
        server=str(proxy.get("server") or ""),
        port=int(proxy.get("port") or 0),
        tls=_tls(proxy),
        transport=_transport(proxy),
        extra=extra,
    )


# ── IR → dict ─────────────────────────────────────────────────────────────


def ir_to_clash_dict(node: ProxyNode) -> dict[str, Any]:
    """Convert a ProxyNode IR to a Mihomo-compatible proxy dict."""
    passthrough = node.extra.get("_clash_passthrough")
    d: dict[str, Any] = deepcopy(passthrough) if isinstance(passthrough, dict) else {}
    d.update({
        "name": node.name,
        "type": node.protocol,
        "server": node.server,
        "port": node.port,
    })

    # TLS — trojan/hysteria2/tuic imply TLS; don't duplicate the key
    proto = node.protocol
    tls_implicit = proto in {"trojan", "hysteria2", "tuic"}

    if node.tls.enabled and not tls_implicit:
        d["tls"] = True

    if node.tls.enabled:
        if node.tls.sni:
            d["sni"] = node.tls.sni
        if node.tls.insecure:
            d["skip-cert-verify"] = True
        if node.tls.alpn:
            d["alpn"] = node.tls.alpn
        if node.tls.fingerprint:
            d["fingerprint"] = node.tls.fingerprint
        if node.tls.reality:
            raw_reality_opts = d.get("reality-opts")
            if isinstance(raw_reality_opts, dict):
                reality_opts = raw_reality_opts
                public_key = node.tls.reality.get("public_key", "")
                short_id = node.tls.reality.get("short_id", "")
                if public_key or "public-key" in reality_opts:
                    reality_opts["public-key"] = public_key
                if short_id or "short-id" in reality_opts:
                    reality_opts["short-id"] = short_id
            else:
                reality_opts = {
                    "public-key": node.tls.reality.get("public_key", ""),
                    "short-id": node.tls.reality.get("short_id", ""),
                }
            d["reality-opts"] = reality_opts

    # Transport
    t = node.transport
    if t.type == "ws":
        d["network"] = "ws"
        raw_ws_opts = d.get("ws-opts")
        ws_opts: dict[str, Any] = raw_ws_opts if isinstance(raw_ws_opts, dict) else {}
        if t.path or "path" in ws_opts:
            ws_opts["path"] = t.path
        raw_headers = ws_opts.get("headers")
        headers: dict[str, Any] = raw_headers if isinstance(raw_headers, dict) else {}
        headers.update(t.headers)
        host_key = next(
            (key for key in headers if str(key).lower() == "host"),
            "Host",
        )
        if t.host or host_key in headers:
            headers[host_key] = t.host
        if headers:
            ws_opts["headers"] = headers
        if ws_opts:
            d["ws-opts"] = ws_opts

    elif t.type == "grpc":
        d["network"] = "grpc"
        raw_grpc_opts = d.get("grpc-opts")
        grpc_opts: dict[str, Any] = raw_grpc_opts if isinstance(raw_grpc_opts, dict) else {}
        if t.service_name or "grpc-service-name" in grpc_opts:
            grpc_opts["grpc-service-name"] = t.service_name
        if grpc_opts:
            d["grpc-opts"] = grpc_opts

    elif t.type == "h2":
        d["network"] = "h2"
        raw_h2_opts = d.get("h2-opts")
        h2_opts: dict[str, Any] = raw_h2_opts if isinstance(raw_h2_opts, dict) else {}
        if t.path or "path" in h2_opts:
            h2_opts["path"] = t.path
        if t.host:
            raw_hosts = h2_opts.get("host")
            if isinstance(raw_hosts, list) and raw_hosts:
                h2_opts["host"] = [t.host, *raw_hosts[1:]]
            else:
                h2_opts["host"] = [t.host]
        elif "host" in h2_opts:
            h2_opts["host"] = []
        if h2_opts:
            d["h2-opts"] = h2_opts

    elif t.type == "http":
        d["network"] = "http"

    # Protocol-specific fields
    if proto == "ss":
        d["cipher"] = node.extra.get("cipher", "")
        d["password"] = node.extra.get("password", "")
        if "plugin" in node.extra:
            d["plugin"] = node.extra["plugin"]
            if "plugin_opts" in node.extra:
                d["plugin-opts"] = node.extra["plugin_opts"]
        elif node.extra.get("obfs"):
            d["plugin"] = "obfs"
            plugin_opts = {"mode": node.extra["obfs"]}
            if node.extra.get("obfs_host"):
                plugin_opts["host"] = node.extra["obfs_host"]
            d["plugin-opts"] = plugin_opts
        if node.extra.get("udp"):
            d["udp"] = True

    elif proto == "vmess":
        d["uuid"] = node.extra.get("uuid", "")
        d["alterId"] = node.extra.get("alter_id", 0)
        d["cipher"] = node.extra.get("cipher", "auto")
        if node.extra.get("udp"):
            d["udp"] = True

    elif proto == "vless":
        d["uuid"] = node.extra.get("uuid", "")
        if node.extra.get("flow"):
            d["flow"] = node.extra["flow"]
        if node.extra.get("udp"):
            d["udp"] = True

    elif proto == "trojan":
        d["password"] = node.extra.get("password", "")
        if node.extra.get("udp"):
            d["udp"] = True

    elif proto == "hysteria2":
        d["password"] = node.extra.get("password", "")
        if node.extra.get("obfs"):
            d["obfs"] = node.extra["obfs"]
            d["obfs-password"] = node.extra.get("obfs_password", "")
        if node.extra.get("up") is not None:
            d["up"] = node.extra["up"]
        if node.extra.get("down") is not None:
            d["down"] = node.extra["down"]

    elif proto == "tuic":
        d["uuid"] = node.extra.get("uuid", "")
        d["password"] = node.extra.get("password", "")
        d["congestion-controller"] = node.extra.get("congestion_controller", "bbr")
        if node.extra.get("alpn"):
            d["alpn"] = node.extra["alpn"]

    elif proto in {"socks5", "http"}:
        if node.extra.get("username"):
            d["username"] = node.extra["username"]
        if node.extra.get("password"):
            d["password"] = node.extra["password"]

    return d
