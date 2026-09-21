"""Surge ``[Proxy]`` section to the shared proxy-node IR."""
from __future__ import annotations

import re
from typing import Any

from app.ir import ProxyNode, TLSConfig, TransportConfig


_PROXY_SECTION = re.compile(r"^\s*\[proxy\]\s*$", re.IGNORECASE | re.MULTILINE)
_SECTION = re.compile(r"^\s*\[[^]]+\]\s*$")
_SUPPORTED_PROTOCOLS = {"ss", "trojan", "vmess", "http", "https", "socks5", "socks5-tls", "anytls"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


class SurgeParseError(ValueError):
    """Raised when a recognized Surge proxy entry is malformed."""


def looks_like_surge_config(content: str) -> bool:
    """Return whether content contains an actual Surge ``[Proxy]`` section."""
    return _PROXY_SECTION.search(content) is not None


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _parse_options(parts: list[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for part in parts:
        key, separator, value = part.partition("=")
        if separator:
            value = value.strip()
            if len(value) >= 2 and value[0] in {'"', "'"} and value[-1] == value[0]:
                value = re.sub(r"\\(.)", r"\1", value[1:-1])
            options[key.strip().lower()] = value
    return options


def _parse_headers(value: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in value.split("|"):
        key, separator, header_value = item.partition(":")
        if separator and key.strip():
            headers[key.strip()] = header_value.strip()
    return headers


def _split_fields(value: str, line_number: int) -> list[str]:
    """Split a Surge entry on commas while honoring quoted option values."""
    fields: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False

    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif quote and character == "\\":
            current.append(character)
            escaped = True
        elif quote and character == quote:
            current.append(character)
            quote = ""
        elif quote:
            current.append(character)
        elif character in {'"', "'"}:
            current.append(character)
            quote = character
        elif character == ",":
            fields.append("".join(current).strip())
            current = []
        else:
            current.append(character)

    if quote:
        raise SurgeParseError(f"unterminated quote at line {line_number}")
    if escaped:
        current.append("\\")
    fields.append("".join(current).strip())
    return fields


def _anytls_extra(options: dict[str, str], line_number: int) -> dict[str, Any]:
    supported = {"password", "reuse", "sni", "skip-cert-verify", "alpn",
                 "server-cert-verify-name", "server-cert-fingerprint-sha256"}
    if options.keys() - supported:
        # Do not silently discard wrappers, client certificates or proxy chains.
        raise SurgeParseError(f"unsupported AnyTLS options at line {line_number}; use a Mihomo subscription for this node")
    if not options.get("password"):
        raise SurgeParseError(f"AnyTLS password is required at line {line_number}")
    for field in ("reuse", "skip-cert-verify"):
        if field in options and options[field].lower() not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            raise SurgeParseError(f"invalid AnyTLS {field} at line {line_number}")
    if options.get("sni", "").lower() == "off":
        raise SurgeParseError(f"AnyTLS sni=off has no equivalent Mihomo setting at line {line_number}")
    passthrough: dict[str, Any] = {"udp": True}
    if options.get("server-cert-verify-name"):
        passthrough["name-cert-verify"] = options["server-cert-verify-name"]
    extra: dict[str, Any] = {"password": options["password"], "_clash_passthrough": passthrough}
    if "reuse" in options:
        extra["disable_reuse"] = not _as_bool(options["reuse"])
    return extra


def _node_from_parts(name: str, parts: list[str], line_number: int) -> ProxyNode | None:
    if not parts:
        return None

    surge_protocol = parts[0].strip().lower()
    if surge_protocol not in _SUPPORTED_PROTOCOLS:
        return None
    if len(parts) < 3:
        raise SurgeParseError(f"invalid proxy entry at line {line_number}: server or port is missing")

    server = parts[1].strip()
    try:
        port = int(parts[2].strip())
    except ValueError as exc:
        raise SurgeParseError(f"invalid proxy port at line {line_number}") from exc
    if not server or not 1 <= port <= 65535:
        raise SurgeParseError(f"invalid proxy endpoint at line {line_number}")

    options = _parse_options(parts[3:])
    protocol = surge_protocol
    tls_enabled = surge_protocol in {"trojan", "https", "socks5-tls", "anytls"} or _as_bool(options.get("tls"))
    if surge_protocol == "https":
        protocol = "http"
    elif surge_protocol == "socks5-tls":
        protocol = "socks5"

    extra: dict[str, Any] = {}
    transport = TransportConfig()
    if protocol == "ss":
        extra = {
            "cipher": options.get("encrypt-method", options.get("cipher", "")),
            "password": options.get("password", ""),
        }
        if options.get("obfs"):
            extra["obfs"] = options["obfs"]
        if options.get("obfs-host"):
            extra["obfs_host"] = options["obfs-host"]
        if _as_bool(options.get("udp-relay")):
            extra["udp"] = True
    elif protocol == "anytls":
        extra = _anytls_extra(options, line_number)
    elif protocol == "trojan":
        extra = {"password": options.get("password", "")}
        if _as_bool(options.get("udp-relay")):
            extra["udp"] = True
    elif protocol == "vmess":
        alter_id = options.get("alter-id", "0")
        try:
            parsed_alter_id = int(alter_id)
        except ValueError as exc:
            raise SurgeParseError(f"invalid VMess alter-id at line {line_number}") from exc
        extra = {
            "uuid": options.get("username", options.get("uuid", "")),
            "alter_id": parsed_alter_id,
            "cipher": options.get("encrypt-method", "auto"),
        }
        if _as_bool(options.get("udp-relay")):
            extra["udp"] = True
        if _as_bool(options.get("ws")):
            headers = _parse_headers(options.get("ws-headers", ""))
            host = headers.get("Host", headers.get("host", ""))
            transport = TransportConfig(
                type="ws",
                path=options.get("ws-path", ""),
                host=host,
                headers=headers,
            )
    elif protocol in {"http", "socks5"}:
        if options.get("username"):
            extra["username"] = options["username"]
        if options.get("password"):
            extra["password"] = options["password"]

    return ProxyNode(
        name=name,
        protocol=protocol,
        server=server,
        port=port,
        tls=TLSConfig(
            enabled=tls_enabled,
            sni=options.get("sni", ""),
            insecure=_as_bool(options.get("skip-cert-verify")),
            alpn=[value.strip() for value in options.get("alpn", "").split(",") if value.strip()] if protocol == "anytls" else [],
            fingerprint=options.get("server-cert-fingerprint-sha256", "") if protocol == "anytls" else "",
        ),
        transport=transport,
        extra=extra,
    )


def parse_surge_nodes(content: str) -> list[ProxyNode]:
    """Parse supported entries from a Surge ``[Proxy]`` section.

    Built-in targets and protocols outside this converter's IR are ignored.
    Recognized protocols with malformed endpoints raise ``SurgeParseError``.
    """
    in_proxy_section = False
    nodes: list[ProxyNode] = []

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if _SECTION.match(line):
            if line.lower() == "[proxy]":
                in_proxy_section = True
                continue
            if in_proxy_section:
                break
            continue
        if not in_proxy_section:
            continue

        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            continue
        parts = _split_fields(value, line_number)
        node = _node_from_parts(name.strip(), parts, line_number)
        if node is not None:
            nodes.append(node)

    return nodes
