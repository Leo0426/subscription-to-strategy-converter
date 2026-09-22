"""Redacted, read-only diagnostics for emitted Surge profiles."""
from __future__ import annotations

import re

from app.ir import ProxyNode


_SECTION = re.compile(r"^\s*\[([^]\r\n]+)\]\s*(?:(?:#|;|//).*)?$")


def _general_options(source: str) -> dict[str, str]:
    options: dict[str, str] = {}
    in_general = False
    for raw_line in source.splitlines():
        line = raw_line.lstrip("\ufeff")
        section = _SECTION.match(line)
        if section is not None:
            in_general = section.group(1).strip().casefold() == "general"
            continue
        stripped = line.strip()
        if not in_general or not stripped or stripped.startswith(("#", ";", "//")):
            continue
        key, separator, value = line.partition("=")
        if separator:
            options[key.strip().casefold()] = value.strip().casefold()
    return options


def audit_native_surge_profile(source: str) -> list[dict]:
    """Return fixed diagnostics without copying native option values."""
    options = _general_options(source)
    warnings: list[dict] = []
    if (
        options.get("allow-wifi-access") == "true"
        and not options.get("wifi-access-http-auth")
    ):
        warnings.append(
            {
                "code": "wifi_proxy_access_without_auth",
                "suggestion": "已允许局域网访问但未配置 HTTP 认证；不需要共享代理时请关闭 allow-wifi-access",
            }
        )
    if "doh-server" in options:
        warnings.append(
            {
                "code": "legacy_surge_option",
                "field": "doh-server",
                "suggestion": "doh-server 是兼容旧名称；可在确认客户端版本后改用 encrypted-dns-server",
            }
        )
    if options.get("loglevel") == "info":
        warnings.append(
            {
                "code": "surge_info_loglevel",
                "suggestion": "长期运行可将 loglevel 调整为 notify；排查问题时再使用 info",
            }
        )
    if options.get("include-all-networks") == "true" and any(
        options.get(key) == "true"
        for key in ("include-apns", "include-cellular-services")
    ):
        warnings.append(
            {
                "code": "surge_full_tunnel_scope",
                "suggestion": "完整网络接管已包含系统或蜂窝服务；若 AirDrop、调试或运营商服务异常，请核对这些范围选项",
            }
        )
    return warnings


def tls_verification_warning(nodes: list[ProxyNode]) -> dict | None:
    count = sum(node.tls.insecure for node in nodes)
    if not count:
        return None
    return {
        "code": "insecure_tls_nodes",
        "count": count,
        "suggestion": f"{count} 个 TLS 节点关闭了服务器证书验证；仅在机场要求时保留，并确认节点来源可信",
    }
