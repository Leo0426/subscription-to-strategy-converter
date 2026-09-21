"""Read policy inventory from native Shadowrocket; never reconstruct nodes.

Callers must retain and publish the original source text. These ProxyNodes carry
only identity and an opaque source fingerprint, not convertible credentials or
transport settings. Native INI syntax follows the community-authored examples:
https://github.com/LOWERTOP/Shadowrocket/blob/main/README.md#编写本地节点
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit

from app.core.parsers.surge import _split_fields
from app.core.platforms.surge_profile import _sections
from app.ir import ProxyNode


class ShadowrocketParseError(ValueError):
    """Native inventory is missing, malformed or ambiguous; never echo input."""


_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_ERROR = "Invalid native Shadowrocket subscription"
# Subscription display metadata documented by the community-authored manual:
# https://github.com/LOWERTOP/Shadowrocket/blob/main/README.md#流量统计信息
_METADATA = re.compile(r"^(?:STATUS|REMARKS)\s*=")


def _decode_base64(value: str) -> str:
    data = "".join(value.split()).encode("ascii")
    return base64.b64decode(data + b"=" * (-len(data) % 4), altchars=b"-_", validate=True).decode("utf-8")


def _decode_name(value: str) -> str:
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError
    return unquote(value, encoding="utf-8", errors="strict")


def _inventory_node(name: object, protocol: str, server: object, port: object, raw: str) -> ProxyNode:
    if (not isinstance(name, str) or not name.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in name)):
        raise ValueError
    if not isinstance(server, str) or not server or re.search(r"[\s/?#@,]", server):
        raise ValueError
    if server.startswith("[") and server.endswith("]"):
        server = server[1:-1]
    if ":" in server:
        ipaddress.ip_address(server)
    elif not re.fullmatch(r"[\w.-]+", server):
        raise ValueError
    if isinstance(port, bool) or not re.fullmatch(r"[0-9]+", str(port)):
        raise ValueError
    number = int(port)
    if not 1 <= number <= 65535 or not re.fullmatch(r"[a-z][a-z0-9+.-]*", protocol):
        raise ValueError
    return ProxyNode(
        name=name, protocol="hysteria2" if protocol == "hy2" else protocol,
        server=server, port=number,
        extra={"_shadowrocket_inventory_only": True,
               "_shadowrocket_native_fingerprint": hashlib.sha256(raw.encode("utf-8")).hexdigest()},
    )


def _unique_json_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _uri_node(raw: str) -> ProxyNode:
    if not _URI.match(raw):
        raise ValueError
    scheme, body = raw.split("://", 1)
    scheme = scheme.lower()
    if scheme == "ssr":
        decoded = _decode_base64(body)
        endpoint, _, query = decoded.partition("/?")
        server, port, protocol, cipher, obfs, password = endpoint.rsplit(":", 5)
        if not all((protocol, cipher, obfs, password)):
            raise ValueError
        _decode_base64(password)
        # SSR also appears with ordinary Base64 remarks; '+' is data, not
        # form-encoded whitespace. Percent-encoded remarks still decode once.
        remarks = parse_qs(query.replace("+", "%2B"), keep_blank_values=True).get("remarks", [])
        if len(remarks) != 1:
            raise ValueError
        return _inventory_node(_decode_base64(remarks[0]), scheme, server, port, raw)
    if scheme == "vmess" and "@" not in body:
        value = json.loads(_decode_base64(body), object_pairs_hook=_unique_json_object)
        if not isinstance(value, dict):
            raise ValueError
        return _inventory_node(value.get("ps"), scheme, value.get("add"), value.get("port"), raw)
    parsed = urlsplit(raw)
    name = _decode_name(parsed.fragment)
    if scheme == "ss" and "@" not in parsed.netloc:
        # Legacy whole-authority Base64; SIP002 encodes only userinfo and can
        # use the ordinary URL endpoint path below. Neither needs auth in IR.
        parsed = urlsplit("ss://" + _decode_base64(parsed.netloc))
    if scheme in {"ss", "vmess", "vless", "trojan", "anytls", "tuic"} and not parsed.username:
        raise ValueError
    return _inventory_node(name, scheme, parsed.hostname, parsed.port, raw)


def _ini_nodes(content: str) -> list[ProxyNode]:
    nodes = []
    for section, body in _sections(content):
        if section != "proxy":
            continue
        for number, raw in enumerate(body.splitlines()[1:], start=1):
            line = raw.strip()
            if not line or line.startswith(("#", ";", "//")):
                continue
            name, separator, value = line.partition("=")
            if not separator or not name.strip():
                raise ValueError
            fields = _split_fields(value, number)
            protocol = fields[0].lower()
            if protocol in {"direct", "reject", "reject-drop"} and len(fields) == 1:
                continue
            if len(fields) < 3:
                raise ValueError
            nodes.append(_inventory_node(name.strip(), protocol, fields[1], fields[2], raw))
    return nodes


def _inventory_lines(content: str) -> list[str]:
    return [line.strip() for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", ";", "//"))
            and not _METADATA.match(line.strip())]


def parse_shadowrocket_source(content: str) -> tuple[list[ProxyNode], str | None]:
    """Read inventory and return the full INI, if present, without rewriting it.

    Plaintext INI retains the original source including comments and BOM. A
    Base64-wrapped INI returns its decoded text with the same preservation.
    URI subscriptions return None as their native policy profile.

    Exact duplicate names cannot be safely addressed by generated policy, so
    reject them even if the endpoint differs. Unknown native INI protocols keep
    their original type and opaque options; this is not a compatibility filter.
    """
    try:
        original_profile = content
        text = content.lstrip("\ufeff")
        lines = _inventory_lines(text)
        if not lines:
            raise ValueError
        if not lines[0].startswith("[") and not _URI.match(lines[0]):
            original_profile = _decode_base64("\n".join(lines))
            text = original_profile.lstrip("\ufeff")
            lines = _inventory_lines(text)
        native_profile = None
        if lines and lines[0].startswith("["):
            nodes = _ini_nodes(text)
            native_profile = original_profile
        else:
            nodes = [_uri_node(line) for line in lines]
        names = [node.name for node in nodes]
        if not nodes or len(names) != len(set(names)):
            raise ValueError
        return nodes, native_profile
    except (ValueError, TypeError, AttributeError, OverflowError):
        # Includes JSON, Base64, URL/port, quote and Unicode failures. Suppress
        # exception chaining because parser exceptions can include credentials.
        raise ShadowrocketParseError(_ERROR) from None


def parse_shadowrocket_inventory(content: str) -> list[ProxyNode]:
    """Read raw/Base64 URI subscriptions or native INI without normalizing names."""
    return parse_shadowrocket_source(content)[0]
