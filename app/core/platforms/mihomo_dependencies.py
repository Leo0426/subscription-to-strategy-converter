"""Prune native policy definitions without changing their connectivity dependants."""
from collections.abc import Iterator
from decimal import Decimal
import re
from typing import Any
from urllib.parse import unquote


_GROUPS = "proxy-groups"
_PROXIES = "proxy-providers"
_RULES = "rule-providers"
_SUBRULES = "sub-rules"
_DNS_OUTBOUNDS = "dns-outbounds"
_DEFINITIONS = {_GROUPS, _PROXIES, _RULES, _SUBRULES}
_RULE_SET = re.compile(r"(?:^|\()\s*RULE-SET\s*,\s*([^,()]+)", re.IGNORECASE)
_OVERLAY_DNS = re.compile(r"^(?:tailscale|ts|easytier|et)://([^/#?]+)")
_DOMAIN_FIELDS = {"fake-ip-filter", "skip-domain", "force-domain", "skip-src-address",
                  "skip-dst-address", "domain", "ipcidr"}
_TARGET_FIELDS = {"proxy", "dialer-proxy", "proxies", "default-selected", "empty-fallback"}
Reference = tuple[str, str | None]  # None denotes all definitions in this namespace.


def _rule_references(rule: Any) -> Iterator[Reference]:
    if isinstance(rule, str):
        for match in _RULE_SET.finditer(rule):
            yield _RULES, match[1].strip()
        parts = [part.strip() for part in rule.split(",")]
        kind = parts[0].upper()
        # Match Mihomo ParseRulePayload: logical/regex payloads may contain
        # commas; their target is last. Ordinary rules put options AFTER it.
        if kind in {"NOT", "OR", "AND", "SUB-RULE", "DOMAIN-REGEX", "PROCESS-NAME-REGEX", "PROCESS-PATH-REGEX"}:
            position = len(parts) - 1
        else:
            position = 1 if kind in {"MATCH", "FINAL"} else 2
        if len(parts) > position:
            yield (_SUBRULES if kind == "SUB-RULE" else _GROUPS), parts[position]
    elif isinstance(rule, dict):
        kind = str(rule.get("type") or rule.get("rule") or "").upper()
        if kind == "RULE-SET":
            for key in ("rule-set", "provider", "match", "value"):
                if isinstance(rule.get(key), str):
                    yield _RULES, rule[key]
        for key in ("proxy", "policy", "target"):
            if isinstance(rule.get(key), str):
                yield (_SUBRULES if kind == "SUB-RULE" else _GROUPS), rule[key]


def _domain_references(value: Any) -> Iterator[Reference]:
    if isinstance(value, str) and value.lower().startswith("rule-set:"):
        for name in value[9:].split(","):
            yield _RULES, name.strip()


def _reference_name(value: Any) -> str | None:
    """Mirror the core's weak numeric-to-string option decoding for references."""
    if isinstance(value, str):
        return value
    if type(value) is int:
        return str(value)
    if isinstance(value, float):
        number = Decimal(str(value))
        if number.is_finite():
            mantissa, exponent = format(number.normalize(), "E").split("E")
            return f"{mantissa}E{int(exponent):+03d}"
    return None


def _references(value: Any, field: str = "", *, dns: bool = False) -> Iterator[Reference]:
    """Read reference-bearing fields, never passwords, labels or arbitrary URLs."""
    if isinstance(value, list):
        for item in value:
            yield from _references(item, field, dns=dns)
    elif field == "rules":
        yield from _rule_references(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if dns:
                yield from _domain_references(key)
            key = str(key).lower().replace("_", "-")
            if key in {"include-all", "include-all-providers"} and isinstance(item, int) and item != 0:
                yield _PROXIES, None
            yield from _references(item, key, dns=dns or key == "dns")
    elif (name := _reference_name(value)) is not None:
        if field in _TARGET_FIELDS:
            yield _GROUPS, name
        elif field == "use":
            yield _PROXIES, name
        elif field in {"route-address-set", "route-exclude-address-set"}:
            yield _RULES, name
        elif field in {"rule", "target-sub-rule"}:
            yield _SUBRULES, name
        if not isinstance(value, str):
            return
        if field == "tunnels":
            parts = value.split(",")
            if len(parts) == 4:
                yield _GROUPS, parts[-1].strip()
        if dns or field in _DOMAIN_FIELDS:
            yield from _domain_references(value)
        if field == "fake-ip-filter":
            # Rule-mode filters select fake/real IPs, not outbound policies.
            for match in _RULE_SET.finditer(value):
                yield _RULES, match[1].strip()
        if dns and (overlay := _OVERLAY_DNS.match(value)):
            yield _DNS_OUTBOUNDS, unquote(overlay[1])
        if dns and "#" in value:
            # Mihomo URL-decodes the fragment, then reads bare '&' components
            # as the DNS connection's proxy/interface name (the last one wins).
            components = unquote(value.partition("#")[2]).split("&")
            targets = [part for part in components if part and "=" not in part]
            if targets:
                yield _GROUPS, targets[-1]


def _definitions(config: dict[str, Any]) -> dict[str, dict]:
    return {_GROUPS: {group["name"]: group for group in config.get(_GROUPS, [])},
            **{section: config.get(section, {}) for section in (_PROXIES, _RULES, _SUBRULES)}}


def _options(value: Any) -> dict:
    return {str(key).lower().replace("_", "-"): item for key, item in value.items()} if isinstance(value, dict) else {}


def has_opaque_proxy_payload(provider: dict) -> bool:
    options = _options(provider)
    return options.get("type") != "inline" or bool(_options(options.get("override")).get("override-expr"))


def _overlay_providers(source: dict[str, Any], name: str) -> Iterator[str]:
    for provider_name, provider in source.get(_PROXIES, {}).items():
        options = _options(provider)
        payload = options.get("payload") or []
        overrides = _options(options.get("override"))
        if (has_opaque_proxy_payload(provider)
                or any(overrides.get(key) for key in ("additional-prefix", "additional-suffix", "proxy-name"))
                or any(_reference_name(_options(node).get("name")) == name for node in payload)):
            # Opaque providers may supply this outbound after a refresh.
            yield provider_name


def prune_native_policy(source: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow envelope containing only reachable native definitions.

    Roots are retained common settings, every raw node and the generated policy.
    The airport's replaced rules and unused definitions are never roots. Trace
    native references in their original namespace; generated definitions shadow
    source names only while reading references originating in generated policy.
    """
    definitions = _definitions(source)
    generated_definitions = _definitions(generated)
    generated_symbols = {section: set(items) for section, items in generated_definitions.items()}
    generated_symbols[_SUBRULES] = set()  # Template sub-rules are not merged.
    generated_symbols[_GROUPS].update(node["name"] for node in generated.get("proxies", []))
    pending = list(_references({key: value for key, value in source.items()
                                if key not in _DEFINITIONS | {"rules"}}))
    # Template DNS/TUN and other defaults are not published in the native
    # envelope, so they must not keep otherwise-unused source objects alive.
    generated_objects = [*generated.get(_GROUPS, []), *generated.get(_PROXIES, {}).values(),
                         *generated.get(_RULES, {}).values(), {"rules": generated.get("rules", [])}]
    for item in generated_objects:
        for section, name in _references(item):
            if name is None or name not in generated_symbols.get(section, set()):
                pending.append((section, name))
    retained: dict[str, set[str]] = {section: set() for section in _DEFINITIONS}
    expanded = set()
    while pending:
        section, name = pending.pop()
        if section == _DNS_OUTBOUNDS:
            pending.extend((_PROXIES, provider) for provider in _overlay_providers(source, name))
        elif name is None:
            if section not in expanded:
                pending.extend((section, candidate) for candidate in definitions[section])
                expanded.add(section)
        elif name in definitions[section] and name not in retained[section]:
            retained[section].add(name)
            definition = definitions[section][name]
            pending.extend(_references(definition, "rules" if section == _SUBRULES else ""))
            if section == _PROXIES and has_opaque_proxy_payload(definition):
                # File/HTTP nodes and expression-derived fields are opaque. Their
                # dialer/rematch references may target any native group/subrule.
                # Only a REACHABLE opaque provider requires this conservative
                # closure; unused external providers are still removed.
                pending.extend([(_GROUPS, None), (_SUBRULES, None)])

    result = dict(source)
    if _GROUPS in result:
        result[_GROUPS] = [group for group in source[_GROUPS] if group["name"] in retained[_GROUPS]]
    for section in (_PROXIES, _RULES, _SUBRULES):
        if section in result:
            result[section] = {name: value for name, value in source[section].items() if name in retained[section]}
    return result
