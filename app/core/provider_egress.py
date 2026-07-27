"""Egress selection for RuleProvider downloads.

A generated profile is only useful if the target client can actually fetch the
RuleProviders it references. Mihomo downloads every `type: http` provider on a
direct connection unless the provider declares `proxy: <group>`, so providers
hosted where the client has no direct route silently produce empty rule sets.

This module is the single place that decides which providers must download
through a ProxyGroup instead of the direct route.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse


#: Hosts that a client on a restricted network cannot reach directly. Mirrors
#: and CDN fronts (jsDelivr, gh-proxy, skk.moe, kelee.one) stay direct because
#: routing them through a node is slower and usually unnecessary.
DIRECT_UNREACHABLE_HOSTS = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
        "gist.githubusercontent.com",
        "codeload.github.com",
    }
)

#: Preferred egress groups in descending order. `自动选择` is a url-test group,
#: so it stays usable regardless of what the operator selected in `默认代理`.
PREFERRED_EGRESS_GROUPS = ("自动选择", "默认代理", "故障转移")

#: Set to a group name to override the choice, or to `DIRECT` to disable rewriting.
EGRESS_ENV_VAR = "SUBFLOW_PROVIDER_EGRESS"


def provider_host(provider: Mapping[str, Any]) -> str:
    return urlparse(str(provider.get("url") or "")).hostname or ""


def needs_egress(provider: Mapping[str, Any]) -> bool:
    """Report whether this provider would fail to download on a direct route."""
    if str(provider.get("type") or "http") != "http":
        return False
    if provider.get("proxy"):
        return False
    return provider_host(provider).lower() in DIRECT_UNREACHABLE_HOSTS


def resolve_egress_group(group_names: Iterable[str]) -> str | None:
    """Pick the ProxyGroup that provider downloads should traverse."""
    available = list(group_names)
    override = os.environ.get(EGRESS_ENV_VAR, "").strip()
    if override:
        if override == "DIRECT":
            return None
        return override if override in available else None
    for candidate in PREFERRED_EGRESS_GROUPS:
        if candidate in available:
            return candidate
    return None


def apply_provider_egress(
    providers: dict[str, dict[str, Any]],
    group_names: Iterable[str],
) -> list[str]:
    """Set `proxy` on every provider that cannot download directly.

    Returns the names that were rewritten. Mutates `providers` in place.
    """
    group = resolve_egress_group(group_names)
    if group is None:
        return []
    rewritten: list[str] = []
    for name, provider in providers.items():
        if not isinstance(provider, dict) or not needs_egress(provider):
            continue
        provider["proxy"] = group
        rewritten.append(name)
    return rewritten
