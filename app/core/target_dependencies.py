"""Inventory actual target artifacts and audit their declared rule dependencies.

No Profile data is persisted here. Collection never downloads resources. Binary
formats and client-owned databases remain explicitly unverified; text validation
is structural evidence, not a client import or routing-behavior guarantee.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from ipaddress import ip_network
import json
from pathlib import Path
import re
from typing import Any, Iterator
from urllib.parse import urlparse

from ruamel.yaml import YAML

from app.core.policy_workspace import _split_rule
from app.core.platforms.surge_capabilities import SURGE_IOS_RULE_TYPES
from app.core.rule_source_audit import (
    COLD_START_BYTE_BUDGET, PROVIDER_COUNT_BUDGET, FetchRuleSource,
    PublicRuleSourceFetcher, _TEXT_RULE_TYPES, inspect_rule_source_content,
    supply_chain_facts, template_audit_metadata,
)

_DOMAIN = re.compile(r"(?:\+\.|\*\.|\.)?(?:[\w*-]+\.)*[\w*-]+", re.UNICODE)
_GEO_RULES = {"GEOSITE": "geosite", "GEOIP": "geoip", "SRC-GEOIP": "geoip", "IP-ASN": "asn", "SRC-IP-ASN": "asn"}


def _clauses(rule: str) -> Iterator[tuple[str, str]]:
    """Walk logical clauses without interpreting commas in URLs or regexes."""
    rule = rule.strip()
    if rule.startswith("(") and rule.endswith(")"):
        rule = rule[1:-1]
    parts = [part.strip() for part in _split_rule(rule)]
    if not parts:
        return
    if parts[0].upper() in {"AND", "OR", "NOT", "SUB-RULE"}:
        for part in parts[1:]:
            if part.startswith("("):
                yield from _clauses(part)
    elif parts[0].startswith("("):
        for part in parts:
            yield from _clauses(part)
    elif len(parts) >= 2:
        yield parts[0].upper(), parts[1].strip('\"\'')


class _Inventory:
    def __init__(self) -> None:
        self.dependencies: dict[str, dict] = {}

    def add(self, *, kind: str, location: str, format: str, behavior: str,
            reference: str, source: str | None = None, reason: str = "") -> dict:
        remote = urlparse(location).scheme.lower() in {"http", "https"}
        source = source or ("remote" if remote else "local" if location else "unresolved")
        url = location if source == "remote" else None
        key = url or f"{source}:{kind}:{location or behavior}"
        dependency = self.dependencies.setdefault(key, {
            "id": "dependency:" + sha256(key.encode()).hexdigest()[:16],
            "kind": kind, "kinds": [], "url": url, "source": source,
            "location": location or None, "format": format, "behavior": behavior,
            "references": [], "usages": [], "audit_status": "not_audited",
        })
        if kind not in dependency["kinds"]:
            dependency["kinds"].append(kind)
        if reference not in dependency["references"]:
            dependency["references"].append(reference)
        usage = next((item for item in dependency["usages"] if
                      (item["kind"], item["format"], item["behavior"]) == (kind, format, behavior)), None)
        if usage is None:
            usage = {"kind": kind, "format": format, "behavior": behavior, "references": []}
            dependency["usages"].append(usage)
        if reference not in usage["references"]:
            usage["references"].append(reference)
        if reason:
            dependency["reason"] = reason
        return dependency


def _collect_mihomo(artifact: str, inventory: _Inventory) -> None:
    config = YAML(typ="safe").load(artifact)
    if not isinstance(config, dict):
        raise ValueError("Mihomo artifact must be a YAML mapping")
    providers = config.get("rule-providers") or {}
    if not isinstance(providers, dict):
        raise ValueError("rule-providers must be a mapping")
    geox = config.get("geox-url") or {}

    def provider(name: str, reference: str) -> None:
        raw = providers.get(name)
        if not isinstance(raw, dict):
            inventory.add(kind="rule_provider", location=name, format="unknown", behavior="unknown",
                          reference=reference, source="unresolved", reason="missing_provider_declaration")
            return
        kind = str(raw.get("type") or "http")
        source = {"http": "remote", "file": "local", "inline": "inline"}.get(kind, "unresolved")
        location = str(raw.get("url") or "") if source == "remote" else str(raw.get("path") or name)
        if source == "remote" and urlparse(location).scheme.lower() not in {"http", "https"}:
            source = "unresolved"
        inventory.add(kind="rule_provider", location=location, format=str(raw.get("format") or "yaml"),
                      behavior=str(raw.get("behavior") or "unknown"), reference=reference, source=source)

    def geodata(kind: str, reference: str) -> None:
        key = "geoip" if config.get("geodata-mode", False) else "mmdb"
        key = key if kind == "geoip" else kind
        location = str(geox.get(key) or "") if isinstance(geox, dict) else ""
        inventory.add(kind="geodata", location=location, format="mmdb" if key in {"mmdb", "asn"} else "dat",
                      behavior=key, reference=reference,
                      reason="client_default_url_not_in_artifact" if not location else "")

    def scan(rule: str, reference: str) -> None:
        for kind, match in _clauses(rule):
            if kind == "RULE-SET":
                provider(match, reference)
            elif kind in _GEO_RULES:
                geodata(_GEO_RULES[kind], reference)

    for name in providers:
        provider(str(name), f"rule-providers.{name}")
    for index, rule in enumerate(config.get("rules") or []):
        if isinstance(rule, str):
            scan(rule, f"rules[{index}]")
    sub_rules = config.get("sub-rules")
    if sub_rules is not None and not isinstance(sub_rules, dict):
        raise ValueError("sub-rules must be a mapping")
    for name, rules in (sub_rules or {}).items():
        if not isinstance(rules, list):
            raise ValueError(f"sub-rules.{name} must be a rule list")
        for index, rule in enumerate(rules):
            if isinstance(rule, str):
                scan(rule, f"sub-rules.{name}[{index}]")
    for name, raw in providers.items():
        if isinstance(raw, dict) and raw.get("type") == "inline":
            for index, rule in enumerate(raw.get("payload") or []):
                if isinstance(rule, str):
                    scan(rule, f"rule-providers.{name}.payload[{index}]")

    def dns_refs(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                dns_refs(str(key), f"{path}.{key}")
                dns_refs(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                dns_refs(child, f"{path}[{index}]")
        elif isinstance(value, str):
            prefix, separator, match = value.partition(":")
            if separator and prefix.lower() == "geosite":
                geodata("geosite", path)
            elif separator and prefix.lower() == "rule-set":
                for name in match.split(","):
                    provider(name.strip(), path)
    dns = config.get("dns") or {}
    if not isinstance(dns, dict):
        raise ValueError("dns must be a mapping")
    fallback_filter = dns.get("fallback-filter")
    if fallback_filter is not None and not isinstance(fallback_filter, dict):
        raise ValueError("dns.fallback-filter must be a mapping")
    dns_refs(dns, "dns")
    if isinstance(dns, dict) and dns.get("fake-ip-filter-mode") == "rule":
        for index, rule in enumerate(dns.get("fake-ip-filter") or []):
            if isinstance(rule, str):
                scan(rule, f"dns.fake-ip-filter[{index}]")
    if isinstance(dns, dict) and dns.get("enable") and dns.get("fallback"):
        fallback_filter = fallback_filter or {}
        if fallback_filter.get("geoip", True):
            geodata("geoip", "dns.fallback-filter.geoip")
        if fallback_filter.get("geosite"):
            geodata("geosite", "dns.fallback-filter.geosite")


def _collect_ini(artifact: str, inventory: _Inventory) -> None:
    sections: dict[str, list[tuple[int, str]]] = {}
    section = ""
    for line_number, raw in enumerate(artifact.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            sections.setdefault(section, [])
        else:
            sections.setdefault(section, []).append((line_number, line))
    general = {key.strip().lower(): value.strip() for _, line in sections.get("General", [])
               for key, _, value in [line.partition("=")] if value}
    inline_names = {name[8:]: name for name in sections if name.lower().startswith("ruleset ")}
    visited: set[str] = set()

    def scan(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for number, line in sections.get(name, []):
            for kind, match in _clauses(line):
                reference = f"[{name}]:{number}"
                if kind in {"RULE-SET", "DOMAIN-SET"}:
                    source = "builtin" if kind == "RULE-SET" and match in {"LAN", "SYSTEM"} else "inline" if match in inline_names else None
                    if source is None:
                        scheme = urlparse(match).scheme.lower()
                        source = ("remote" if scheme in {"http", "https"} else "local" if
                                  scheme == "file" or not scheme and ("/" in match or Path(match).suffix)
                                  else "unresolved")
                    inventory.add(kind="rule_set" if kind == "RULE-SET" else "domain_set", location=match,
                                  format="text", behavior="classical" if kind == "RULE-SET" else "domain",
                                  reference=reference, source=source)
                    if source == "inline":
                        scan(inline_names[match])
                elif kind in _GEO_RULES:
                    geo_kind = _GEO_RULES[kind]
                    location = general.get("geoip-maxmind-url", "") if geo_kind == "geoip" else ""
                    inventory.add(kind="geodata", location=location, format="mmdb" if geo_kind == "geoip" else "unknown",
                                  behavior=geo_kind, reference=reference,
                                  reason="client_database_url_not_in_artifact" if not location else "")
    scan("Rule")


def collect_target_dependencies(target: str, artifact: str) -> dict:
    """Return URL-deduplicated rule dependencies without network or local reads.

    Each URL's usages retain every kind/format/behavior declaration. Local,
    inline, built-in and unresolved resources have null URLs. Node subscriptions
    intentionally expose zero rule dependencies; their configuration is separate.
    """
    target = "mihomo" if target == "clash" else target
    inventory = _Inventory()
    if target == "mihomo":
        _collect_mihomo(artifact, inventory)
    elif target in {"surge", "shadowrocket-config"}:
        _collect_ini(artifact, inventory)
    elif target != "shadowrocket":
        raise ValueError(f"Unsupported dependency target: {target}")
    dependencies = list(inventory.dependencies.values())
    sources = Counter(item["source"] for item in dependencies)
    return {
        "target": target, "dependencies": dependencies,
        "summary": {"total": len(dependencies),
                    "rule_sources": sum(any(kind != "geodata" for kind in item["kinds"]) for item in dependencies),
                    "geodata": sum("geodata" in item["kinds"] for item in dependencies),
                    **{source: sources[source] for source in ("remote", "local", "inline", "builtin", "unresolved")}},
        "coverage": {"scope": "artifact_declared_dependencies", "resolution_complete": sources["unresolved"] == 0,
                     "all_dependencies_validated": False, "client_runtime_validated": False},
    }


def _valid_classical(entry: str, rule_types: frozenset[str] = _TEXT_RULE_TYPES) -> bool | None:
    """Return None when a recognized rule needs client-specific validation."""
    parts = [part.strip() for part in _split_rule(entry)]
    if len(parts) < 2 or parts[0].upper() not in rule_types:
        return False
    kind, match = parts[:2]
    kind = kind.upper()
    if not match:
        return False
    if any(option.lower() not in {"no-resolve", "src"} for option in parts[2:]):
        return None
    if kind in {"AND", "OR", "NOT"}:
        if not (match.startswith("(") and match.endswith(")")):
            return False
        clauses = [clause.strip() for clause in _split_rule(match[1:-1])]
        if kind == "NOT" and len(clauses) != 1:
            return False
        if not all(clause.startswith("(") and clause.endswith(")") for clause in clauses):
            return False
        results = [_valid_classical(clause[1:-1], rule_types) for clause in clauses]
        return False if False in results else None if None in results else True
    if kind in {"DOMAIN", "DOMAIN-SUFFIX"}:
        return bool(_DOMAIN.fullmatch(match))
    if kind in {"IP-CIDR", "IP-CIDR6", "SRC-IP-CIDR"}:
        try:
            if "/" not in match:
                return False
            ip_network(match, strict=False)
        except ValueError:
            return False
        return True
    if kind in {"IP-ASN", "SRC-IP-ASN"}:
        return bool(re.fullmatch(r"[0-9]+", match)) and int(match) <= 4294967295
    if kind in {"DST-PORT", "SRC-PORT", "IN-PORT", "DEST-PORT"}:
        for interval in match.split("/"):
            if not re.fullmatch(r"[0-9]+(?:-[0-9]+)?", interval):
                return False
            bounds = [int(bound) for bound in interval.split("-")]
            if not (0 <= bounds[0] <= bounds[-1] <= 65535):
                return False
        return True
    if kind == "NETWORK":
        return match.lower() in {"tcp", "udp"}
    if kind in {"DOMAIN-KEYWORD", "USER-AGENT", "PROCESS-NAME", "PROCESS-PATH", "IN-NAME", "REMATCH-NAME"}:
        return True  # Literal or native wildcard strings; no regex engine involved.
    # Regex engines, external references, geodata tags and other parameter
    # grammars are not validated by this structural audit.
    return None


def _inspect_usage(usage: dict, content: bytes, content_type: str) -> dict:
    format, behavior = usage["format"].lower(), usage["behavior"].lower()
    binary = usage["kind"] == "geodata" or format == "mrs"
    if format == "mrs" and behavior not in {"domain", "ipcidr"}:
        return {"audit_status": "invalid", "reason": "mrs_requires_domain_or_ipcidr_behavior"}
    if binary:
        stripped = content.lstrip().lower()
        if not content or "text/html" in content_type.lower() or stripped.startswith((b"<!doctype html", b"<html", b"{\"error")):
            return {"audit_status": "invalid", "reason": "empty_or_error_binary_response"}
        return {"audit_status": "not_audited", "semantic_status": "not_semantically_validated",
                "reason": "binary_requires_client_validation"}
    try:
        content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"audit_status": "invalid", "reason": "invalid_utf8"}
    # INI RULE-SETs use native syntax (including HTTP rules); the existing
    # content inspector defaults to Mihomo syntax. This remains structural
    # evidence, not proof of a particular client version's rule capabilities.
    rule_types = _TEXT_RULE_TYPES
    if usage["kind"] in {"rule_set", "domain_set"}:
        rule_types |= SURGE_IOS_RULE_TYPES | {"DOMAIN-SET"}
    inspection = inspect_rule_source_content(content, content_type=content_type,
                                             declared_format=format, rule_types=rule_types)
    if not inspection["valid"]:
        return {"audit_status": "invalid", "detected_format": inspection["detected_format"], "reason": "invalid_rule_format"}
    text = content.decode("utf-8")
    entries = YAML(typ="safe").load(text)["payload"] if format == "yaml" else [
        line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith(("#", "//"))]
    invalid_indexes = []
    unvalidated_indexes = []
    for index, entry in enumerate(entries):
        valid: bool | None = True
        if behavior == "domain":
            valid = bool(_DOMAIN.fullmatch(entry))
        elif behavior == "ipcidr":
            try:
                ip_network(entry, strict=False)
            except ValueError:
                valid = False
        elif behavior == "classical":
            valid = _valid_classical(entry, rule_types)
        else:
            valid = False
        if valid is False:
            invalid_indexes.append(index)
        elif valid is None:
            unvalidated_indexes.append(index)
    normalized = "\n".join(sorted({entry.strip().lower() for entry in entries}))
    scope = "native_rule_set_structure" if usage["kind"] in {"rule_set", "domain_set"} else "text_structure_and_behavior"
    status = "invalid" if invalid_indexes else "not_audited" if unvalidated_indexes else "valid"
    return {"audit_status": status, "validation_scope": scope,
            "entry_count": len(entries), "detected_format": inspection["detected_format"],
            "normalized_sha256": sha256(normalized.encode()).hexdigest(),
            **({"reason": "entry_does_not_match_declared_behavior", "invalid_entry_indexes": invalid_indexes[:10]} if invalid_indexes else {}),
            **({"unvalidated_entry_indexes": unvalidated_indexes[:10]} if unvalidated_indexes else {}),
            **({"reason": "rule_parameter_syntax_not_validated"} if status == "not_audited" else {})}


async def audit_target_dependencies(inventory: dict, *, fetch: FetchRuleSource, concurrency: int = 8) -> dict:
    """Audit every declared usage, fetching a repeated URL only once per target."""
    report = deepcopy(inventory)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def audit(dependency: dict) -> None:
        if dependency["source"] != "remote":
            dependency["reason"] = dependency.get("reason") or "resource_not_fetched"
            return
        url = dependency["url"]
        dependency.update(supply_chain_facts(url))
        try:
            async with semaphore:
                response = await fetch(url)
            content = response.get("content")
            status = int(response.get("status_code") or 0)
            dependency.update(status_code=status, final_url=str(response.get("final_url") or url),
                              elapsed_ms=int(response.get("elapsed_ms") or 0))
            if isinstance(content, bytes):
                dependency.update(byte_count=len(content), sha256=sha256(content).hexdigest())
            if not 200 <= status < 300:
                dependency.update(audit_status="failed", error=f"HTTP {status}")
                return
            if not isinstance(content, bytes):
                raise ValueError("fetch result content must be bytes")
            dependency["fetch_status"] = "fetched"
            content_type = str(response.get("content_type") or "")
            dependency["content_type"] = content_type
            for usage in dependency["usages"]:
                usage.update(_inspect_usage(usage, content, content_type))
            statuses = {usage["audit_status"] for usage in dependency["usages"]}
            dependency["audit_status"] = "invalid" if "invalid" in statuses else "not_audited" if "not_audited" in statuses else "valid"
            if any(usage.get("semantic_status") == "not_semantically_validated" for usage in dependency["usages"]):
                dependency["semantic_status"] = "not_semantically_validated"
        except Exception as exc:
            dependency.update(audit_status="failed", error=str(exc).strip() or type(exc).__name__, error_type=type(exc).__name__)

    await asyncio.gather(*(audit(dependency) for dependency in report["dependencies"]))
    dependencies = report["dependencies"]
    statuses = Counter(item["audit_status"] for item in dependencies)
    total_bytes = sum(item.get("byte_count", 0) for item in dependencies)
    report["summary"].update({status: statuses[status] for status in ("valid", "invalid", "failed", "not_audited")}, total_bytes=total_bytes)
    fetched = sum(item.get("fetch_status") == "fetched" for item in dependencies)
    report["coverage"].update(
        fetched=fetched, text_validated=statuses["valid"],
        not_semantically_validated=sum(item.get("semantic_status") == "not_semantically_validated" for item in dependencies),
        all_dependencies_validated=statuses["valid"] == len(dependencies),
        byte_count_complete=fetched == len(dependencies),
        transitive_external_content_validated=False,
    )
    report["budget"] = {"byte_limit": COLD_START_BYTE_BUDGET, "dependency_limit": PROVIDER_COUNT_BUDGET,
                        "byte_limit_exceeded": total_bytes > COLD_START_BYTE_BUDGET,
                        "dependency_limit_exceeded": len(dependencies) > PROVIDER_COUNT_BUDGET}
    failed = statuses["failed"] or statuses["invalid"] or report["budget"]["byte_limit_exceeded"] or report["budget"]["dependency_limit_exceeded"]
    report["check_status"] = "failed" if failed else "incomplete" if statuses["not_audited"] else "passed"
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    return report


def collect_leo_target_dependencies() -> dict:
    """Compile public Leo with a synthetic node; never load a saved Profile."""
    from app.core.platforms.shadowrocket import build_shadowrocket_config
    from app.core.platforms.surge import build_surge_config
    from app.core.policy_workspace import compile_mihomo_config
    from app.core.renderer import render_yaml
    from app.core.template_engine import LEO_TEMPLATE_ID, apply_template, load_template
    from app.ir import ProxyNode

    template = load_template(LEO_TEMPLATE_ID)
    nodes = [ProxyNode(name="AUDIT-SYNTHETIC", protocol="ss", server="audit.example", port=443,
                       extra={"cipher": "aes-128-gcm", "password": "synthetic-public-fixture"})]
    config = apply_template(template, nodes)
    args = (nodes, config.get("proxy-groups", []), config.get("rules", []), config.get("rule-providers", {}))
    surge, surge_warnings = build_surge_config(*args, dns_config=config.get("dns"))
    shadowrocket, shadowrocket_warnings = build_shadowrocket_config(*args)
    artifacts = {"mihomo": render_yaml(compile_mihomo_config(config, nodes)),
                 "surge": surge, "shadowrocket-config": shadowrocket}
    targets = {target: collect_target_dependencies(target, artifact) for target, artifact in artifacts.items()}
    targets["surge"]["compiler_warnings"] = surge_warnings
    targets["shadowrocket-config"]["compiler_warnings"] = shadowrocket_warnings
    return {"scope": "public_leo_synthetic", "template": template_audit_metadata(template), "targets": targets}


async def audit_leo_target_dependencies(*, concurrency: int = 8, timeout: float = 15.0,
                                        fetch: FetchRuleSource | None = None) -> dict:
    report = collect_leo_target_dependencies()

    async def audit_all(fetch_source: FetchRuleSource) -> None:
        # Cache only this run's public fetches, so shared target URLs cost one download.
        tasks: dict[str, asyncio.Task] = {}

        async def shared_fetch(url: str) -> dict:
            if url not in tasks:
                tasks[url] = asyncio.create_task(fetch_source(url))
            return await tasks[url]

        for target, inventory in report["targets"].items():
            report["targets"][target] = await audit_target_dependencies(inventory, fetch=shared_fetch, concurrency=concurrency)
    if fetch is not None:
        await audit_all(fetch)
    else:
        async with PublicRuleSourceFetcher(timeout=timeout) as fetcher:
            await audit_all(fetcher.fetch)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["fetch_scope"] = "server_public_fetcher; client headers, egress and runtime not reproduced"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory and audit public Leo target rule dependencies")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory-only", action="store_true", help="Compile and collect without downloading")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    report = collect_leo_target_dependencies() if args.inventory_only else asyncio.run(
        audit_leo_target_dependencies(concurrency=args.concurrency, timeout=args.timeout))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({target: item["summary"] for target, item in report["targets"].items()}, ensure_ascii=False))
    if args.inventory_only:
        return 0
    statuses = {item["check_status"] for item in report["targets"].values()}
    return 1 if "failed" in statuses else 2 if "incomplete" in statuses else 0


if __name__ == "__main__":
    raise SystemExit(main())
