"""Egress selection for RuleProvider downloads.

A generated profile is only useful if the target client can actually fetch the
RuleProviders it references.  Without an explicit ``proxy`` setting, Mihomo's
provider request can follow the ordinary routing rules.  Sending hundreds of
bootstrap downloads through the newly-started proxy pool creates a connection
storm and can leave providers empty.

This module is the single place that mirrors safely pinned GitHub files and
decides which remaining providers must download through a ProxyGroup instead
of the direct route.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse


#: Hosts that a client on a restricted network cannot reach directly.
DIRECT_UNREACHABLE_HOSTS = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "objects.githubusercontent.com",
        "gist.githubusercontent.com",
        "codeload.github.com",
    }
)

#: Rule-source fronts verified to work directly in the supported deployment.
#: Pinning them explicitly prevents provider bootstrap traffic from being
#: captured by the profile's own GEO/rule-provider policy while it is still
#: loading.  Keep this exact-host list deliberately narrow.
DIRECT_PROVIDER_HOSTS = frozenset(
    {
        "cdn.jsdelivr.net",
        "fastly.jsdelivr.net",
        "gcore.jsdelivr.net",
        "testingcf.jsdelivr.net",
        "ruleset.skk.moe",
    }
)

#: Preferred egress groups in descending order.  Leo's hidden ``规则更新`` group
#: is independent from the operator's persisted ``默认代理`` choice, which may be
#: DIRECT.  Older/custom templates retain the existing fallback order.
PREFERRED_EGRESS_GROUPS = (
    "规则更新",
    "自动选择",
    "默认代理",
    "故障转移",
)

#: Set to a group name to override the choice, or to `DIRECT` to disable rewriting.
EGRESS_ENV_VAR = "SUBFLOW_PROVIDER_EGRESS"

_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


def immutable_github_mirror_url(url: str) -> str | None:
    """Return a jsDelivr URL for an immutable GitHub file, if eligible.

    Only exact HTTPS raw-file URLs pinned to a full commit SHA are safe to
    mirror. Branches, tags, releases, URLs with query/fragment semantics, and
    all other GitHub URL shapes deliberately return ``None``.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or parsed.params or parsed.query or parsed.fragment:
        return None

    host = parsed.hostname or ""
    if parsed.netloc.lower() != host.lower():
        return None

    parts = parsed.path.removeprefix("/").split("/")
    if host.lower() == "raw.githubusercontent.com":
        if len(parts) < 4:
            return None
        owner, repo, commit, *path = parts
    elif host.lower() == "github.com":
        if len(parts) < 5 or parts[2] != "raw":
            return None
        owner, repo, _, commit, *path = parts
    else:
        return None

    if (
        not owner
        or not repo
        or not path
        or any(part in {"", ".", ".."} for part in (owner, repo, *path))
        or not _COMMIT_SHA.fullmatch(commit)
    ):
        return None
    return f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{commit}/{'/'.join(path)}"


def provider_host(provider: Mapping[str, Any]) -> str:
    return urlparse(str(provider.get("url") or "")).hostname or ""


def needs_egress(provider: Mapping[str, Any]) -> bool:
    """Report whether compilation must attach a proxy for this provider."""
    if str(provider.get("type") or "http") != "http":
        return False
    if "proxy" in provider:
        return False
    if immutable_github_mirror_url(str(provider.get("url") or "")) is not None:
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
    """Mirror immutable files and proxy remaining unreachable providers.

    Immutable mirrors are explicitly pinned to ``DIRECT`` so their bootstrap
    requests cannot be captured by the profile's own proxy rules.  Returns only
    names routed through the selected fallback group; direct mirror rewrites are
    deliberately omitted to preserve the function's existing contract.
    """
    group = resolve_egress_group(group_names)
    rewritten: list[str] = []
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            continue

        if str(provider.get("type") or "http") == "http" and "proxy" not in provider:
            mirror_url = immutable_github_mirror_url(str(provider.get("url") or ""))
            if mirror_url is not None:
                provider["url"] = mirror_url
                provider["proxy"] = "DIRECT"
            elif provider_host(provider).lower() in DIRECT_PROVIDER_HOSTS:
                provider["proxy"] = "DIRECT"

        if group is None or not needs_egress(provider):
            continue
        provider["proxy"] = group
        rewritten.append(name)
    return rewritten
