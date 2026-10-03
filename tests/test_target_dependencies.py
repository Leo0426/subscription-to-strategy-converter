from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json

import pytest

from app.core import target_dependencies


def test_mihomo_inventory_deduplicates_urls_and_preserves_each_provider_declaration() -> None:
    inventory = target_dependencies.collect_target_dependencies("clash", """
rule-providers:
  Domains: {type: http, url: 'https://rules.example/shared', behavior: domain, format: text}
  Classical: {type: http, url: 'https://rules.example/shared', behavior: classical, format: text}
  Local: {type: file, path: ./local.yaml, behavior: classical}
  Inline: {type: inline, behavior: domain, payload: [example.org]}
proxy-providers:
  Airport: {type: http, url: 'https://airport.example/private'}
geox-url:
  geosite: https://rules.example/geosite.dat
  mmdb: https://rules.example/country.mmdb
  geoip: https://rules.example/unused.dat
  asn: https://rules.example/asn.mmdb
rules:
  - RULE-SET,Domains,DIRECT
  - AND,((RULE-SET,Classical),(GEOSITE,google)),Proxy
  - GEOIP,cn,DIRECT
  - MATCH,Proxy
""")
    assert inventory["target"] == "mihomo"
    dependencies = inventory["dependencies"]
    assert inventory["summary"]["total"] == 5
    assert inventory["summary"]["rule_sources"] == 3
    assert inventory["summary"]["geodata"] == 2
    shared = next(item for item in dependencies if item["url"] == "https://rules.example/shared")
    assert len(shared["usages"]) == 2
    assert set(shared["references"]) >= {"rule-providers.Domains", "rule-providers.Classical", "rules[0]", "rules[1]"}
    assert {item["behavior"] for item in shared["usages"]} == {"domain", "classical"}
    assert {item["source"] for item in dependencies} == {"remote", "local", "inline"}
    assert {item["url"] for item in dependencies if item["kind"] == "geodata"} == {
        "https://rules.example/geosite.dat", "https://rules.example/country.mmdb",
    }
    assert all(item["audit_status"] == "not_audited" for item in dependencies)
    assert "airport.example" not in str(inventory)


def test_missing_geodata_url_and_rule_provider_remain_unresolved() -> None:
    inventory = target_dependencies.collect_target_dependencies("mihomo", """
geodata-mode: true
rules:
  - GEOSITE,cn,DIRECT
  - GEOIP,cn,DIRECT
  - IP-ASN,123,DIRECT
  - RULE-SET,missing,DIRECT
""")
    assert inventory["summary"]["unresolved"] == 4
    assert all(item["url"] is None for item in inventory["dependencies"])
    assert {item["behavior"] for item in inventory["dependencies"] if item["kind"] == "geodata"} == {"geosite", "geoip", "asn"}
    assert inventory["coverage"]["resolution_complete"] is False


def test_dns_geosite_usage_and_sub_rules_are_included() -> None:
    inventory = target_dependencies.collect_target_dependencies("mihomo", """
dns:
  nameserver-policy:
    'geosite:cn': 223.5.5.5
  fake-ip-filter: ['rule-set:local']
rule-providers:
  local: {type: file, path: ./local.yaml, behavior: domain}
sub-rules:
  other: ['SRC-GEOIP,cn,DIRECT']
rules: ['MATCH,DIRECT']
""")
    assert {item["behavior"] for item in inventory["dependencies"] if item["kind"] == "geodata"} == {"geosite", "mmdb"}
    local = next(item for item in inventory["dependencies"] if item["kind"] == "rule_provider")
    assert "dns.fake-ip-filter[0]" in local["references"]


@pytest.mark.parametrize("target", ["surge", "shadowrocket-config"])
def test_ini_inventory_uses_only_rule_sections_and_preserves_different_kinds(target: str) -> None:
    inventory = target_dependencies.collect_target_dependencies(target, """
[General]
geoip-maxmind-url = https://rules.example/geo.mmdb
[Proxy Group]
Auto = url-test, Node, url=https://probe.example/
[Rule]
RULE-SET,https://rules.example/shared,Proxy
DOMAIN-SET,https://rules.example/shared,DIRECT
AND,((DOMAIN,example.com),(RULE-SET,https://rules.example/nested)),Proxy
RULE-SET,./local.list,DIRECT
RULE-SET,Embedded,DIRECT
RULE-SET,LAN,DIRECT
GEOIP,CN,DIRECT
FINAL,Proxy
[Ruleset Embedded]
DOMAIN,embedded.example
[Script]
remote = type=http-request,script-path=https://scripts.example/private
""")
    assert inventory["summary"]["remote"] == 3
    shared = next(item for item in inventory["dependencies"] if item["url"] == "https://rules.example/shared")
    assert shared["kinds"] == ["rule_set", "domain_set"]
    assert len(shared["references"]) == 2
    assert {item["source"] for item in inventory["dependencies"]} == {"remote", "local", "inline", "builtin"}
    assert "probe.example" not in str(inventory)
    assert "scripts.example" not in str(inventory)


def test_shadowrocket_node_artifact_has_no_rule_dependencies() -> None:
    inventory = target_dependencies.collect_target_dependencies("shadowrocket", "ss://opaque-native-node")
    assert inventory["dependencies"] == []
    assert inventory["summary"]["total"] == 0


@pytest.mark.parametrize("target,artifact", [
    ("unknown", ""), ("mihomo", "- not a mapping"),
    ("mihomo", "sub-rules: [not-a-mapping]"),
    ("mihomo", "sub-rules: {child: 12}"),
    ("mihomo", "dns: {enable: true, fallback: [tls://1.1.1.1], fallback-filter: [not-a-mapping]}"),
])
def test_inventory_rejects_unsupported_or_malformed_artifacts(target: str, artifact: str) -> None:
    with pytest.raises(ValueError):
        target_dependencies.collect_target_dependencies(target, artifact)


@pytest.mark.asyncio
async def test_target_audit_fails_extra_native_url_even_when_template_source_is_valid() -> None:
    inventory = target_dependencies.collect_target_dependencies("surge", """
[Rule]
RULE-SET,https://rules.example/template,DIRECT
RULE-SET,https://rules.example/extra-html,DIRECT
RULE-SET,https://rules.example/extra-404,DIRECT
""")
    before = deepcopy(inventory)
    bodies = {
        "template": (200, b"DOMAIN-SUFFIX,example.com\n"),
        "extra-html": (200, b"<!doctype html><html>Login</html>"),
        "extra-404": (404, b"missing"),
    }

    async def fetch(url: str) -> dict:
        code, content = bodies[url.rsplit("/", 1)[1]]
        return {"status_code": code, "content": content, "final_url": url, "content_type": "text/plain", "elapsed_ms": 1}

    report = await target_dependencies.audit_target_dependencies(inventory, fetch=fetch)
    assert inventory == before
    assert report["summary"]["valid"] == 1
    assert report["summary"]["invalid"] == 1
    assert report["summary"]["failed"] == 1
    assert report["summary"]["total_bytes"] == sum(len(body) for _, body in bodies.values())
    assert report["check_status"] == "failed"
    assert report["coverage"]["all_dependencies_validated"] is False
    valid = report["dependencies"][0]
    assert valid["sha256"] == sha256(bodies["template"][1]).hexdigest()
    assert "content" not in valid


@pytest.mark.asyncio
@pytest.mark.parametrize("behavior,format,content", [
    ("domain", "text", b"DOMAIN,example.com\n"),
    ("classical", "text", b"example.com\n"),
    ("ipcidr", "yaml", b"payload: ['example.com']"),
    ("classical", "mrs", b"MRS\x00binary"),
    ("domain", "yaml", b"example.com\n"),
    ("classical", "text", b"IP-CIDR,definitely-not-an-ip\n"),
])
async def test_audit_rejects_wrong_behavior_format_or_rule_syntax(behavior: str, format: str, content: bytes) -> None:
    inventory = target_dependencies.collect_target_dependencies("mihomo", f"""
rule-providers:
  P: {{type: http, url: https://rules.example/list, behavior: {behavior}, format: {format}}}
rules: ['RULE-SET,P,DIRECT']
""")

    async def fetch(url: str) -> dict:
        return {"status_code": 200, "content": content}

    report = await target_dependencies.audit_target_dependencies(inventory, fetch=fetch)
    assert report["dependencies"][0]["audit_status"] == "invalid"
    assert report["check_status"] == "failed"


@pytest.mark.asyncio
async def test_binary_and_unresolved_dependencies_cannot_be_reported_as_valid() -> None:
    inventory = target_dependencies.collect_target_dependencies("mihomo", """
rule-providers:
  Binary: {type: http, url: https://rules.example/domain.mrs, behavior: domain, format: mrs}
geox-url:
  geosite: https://rules.example/geosite.dat
rules: ['RULE-SET,Binary,DIRECT', 'GEOSITE,cn,DIRECT', 'GEOIP,cn,DIRECT']
""")

    async def fetch(url: str) -> dict:
        return {"status_code": 200, "content": b"\x00binary\xff"}

    report = await target_dependencies.audit_target_dependencies(inventory, fetch=fetch)
    assert report["summary"]["valid"] == 0
    assert report["summary"]["not_audited"] == 3
    assert report["summary"]["total_bytes"] == 16
    assert report["coverage"]["fetched"] == 2
    assert report["coverage"]["not_semantically_validated"] == 2
    assert report["check_status"] == "incomplete"
    assert report["coverage"]["byte_count_complete"] is False


@pytest.mark.asyncio
async def test_same_url_is_fetched_once_and_validated_for_every_usage() -> None:
    inventory = target_dependencies.collect_target_dependencies("surge", """
[Rule]
RULE-SET,https://rules.example/shared,Proxy
DOMAIN-SET,https://rules.example/shared,DIRECT
""")
    fetch_count = 0

    async def fetch(url: str) -> dict:
        nonlocal fetch_count
        fetch_count += 1
        return {"status_code": 200, "content": b"DOMAIN,example.com\n"}

    report = await target_dependencies.audit_target_dependencies(inventory, fetch=fetch)
    assert fetch_count == 1
    assert report["summary"]["invalid"] == 1
    assert [item["audit_status"] for item in report["dependencies"][0]["usages"]] == ["valid", "invalid"]


@pytest.mark.asyncio
async def test_complete_dependency_budget_includes_geodata(monkeypatch) -> None:
    monkeypatch.setattr(target_dependencies, "COLD_START_BYTE_BUDGET", 20)
    inventory = target_dependencies.collect_target_dependencies("mihomo", """
geox-url: {geosite: 'https://rules.example/geosite.dat'}
rules: ['GEOSITE,cn,DIRECT']
""")

    async def fetch(url: str) -> dict:
        return {"status_code": 200, "content": b"\x00" * 21}

    report = await target_dependencies.audit_target_dependencies(inventory, fetch=fetch)
    assert report["budget"]["byte_limit_exceeded"] is True
    assert report["check_status"] == "failed"


def test_public_leo_inventory_compiles_all_targets_without_private_sources() -> None:
    report = target_dependencies.collect_leo_target_dependencies()
    assert set(report["targets"]) == {"mihomo", "surge", "shadowrocket-config"}
    assert report["template"]["provider_count"] == 9
    assert report["targets"]["surge"]["summary"]["remote"] == 23
    assert report["targets"]["shadowrocket-config"]["summary"]["remote"] == 23
    assert report["targets"]["mihomo"]["summary"]["remote"] == 11
    assert "AUDIT-SYNTHETIC" not in str(report)


def test_dns_rule_mode_fallback_geosite_and_sub_rule_conditions_keep_dependencies() -> None:
    inventory = target_dependencies.collect_target_dependencies("mihomo", """
dns:
  enable: true
  fallback: [tls://1.1.1.1]
  fallback-filter: {geosite: [gfw]}
  fake-ip-filter-mode: rule
  fake-ip-filter: ['RULE-SET,Missing,fake-ip', 'GEOSITE,cn,real-ip']
rules:
  - SUB-RULE,(AND,((IP-ASN,123),(GEOSITE,cn))),other
sub-rules:
  other: ['MATCH,DIRECT']
""")
    dependencies = inventory["dependencies"]
    assert {item["behavior"] for item in dependencies if item["kind"] == "geodata"} == {"asn", "geosite", "mmdb"}
    geosite = next(item for item in dependencies if item["behavior"] == "geosite")
    assert set(geosite["references"]) == {"dns.fallback-filter.geosite", "dns.fake-ip-filter[1]", "rules[0]"}
    missing = next(item for item in dependencies if item["kind"] == "rule_provider")
    assert missing["source"] == "unresolved"
    assert missing["references"] == ["dns.fake-ip-filter[0]"]


def test_empty_dns_geosite_filter_does_not_invent_a_database_dependency() -> None:
    inventory = target_dependencies.collect_target_dependencies("mihomo", """
dns:
  enable: true
  fallback: [tls://1.1.1.1]
  fallback-filter: {geoip: false, geosite: []}
rules: ['MATCH,DIRECT']
""")
    assert inventory["dependencies"] == []


def test_ini_unknown_rule_locations_are_unresolved_instead_of_local() -> None:
    inventory = target_dependencies.collect_target_dependencies("surge", """
[Rule]
RULE-SET,Undeclared,DIRECT
RULE-SET,ftp://rules.example/list,DIRECT
RULE-SET,./local.list,DIRECT
""")
    assert inventory["summary"]["unresolved"] == 2
    assert inventory["summary"]["local"] == 1
    assert inventory["coverage"]["resolution_complete"] is False


@pytest.mark.parametrize("statuses,expected", [
    (["passed"], 0), (["passed", "incomplete"], 2), (["incomplete", "failed"], 1),
])
def test_cli_writes_report_and_returns_a_nonzero_exit_for_failed_or_incomplete_audits(
    tmp_path, monkeypatch, statuses, expected,
) -> None:
    report = {"targets": {str(i): {"check_status": status, "summary": {"total": 1}}
                          for i, status in enumerate(statuses)}}

    async def audit(**kwargs):
        return report

    monkeypatch.setattr(target_dependencies, "audit_leo_target_dependencies", audit)
    output = tmp_path / "audit.json"
    monkeypatch.setattr("sys.argv", ["target_dependencies", "--output", str(output)])
    assert target_dependencies.main() == expected
    assert json.loads(output.read_text()) == report


def test_cli_inventory_only_writes_report_without_network(tmp_path, monkeypatch) -> None:
    output = tmp_path / "inventory.json"
    monkeypatch.setattr("sys.argv", ["target_dependencies", "--inventory-only", "--output", str(output)])
    assert target_dependencies.main() == 0
    report = json.loads(output.read_text())
    assert set(report["targets"]) == {"mihomo", "surge", "shadowrocket-config"}
    assert all(item["audit_status"] == "not_audited" for target in report["targets"].values()
               for item in target["dependencies"])


@pytest.mark.asyncio
@pytest.mark.parametrize("target,want", [("surge", "valid"), ("shadowrocket-config", "valid"), ("mihomo", "invalid")])
async def test_native_http_rule_source_is_validated_with_the_target_rule_types(target, want) -> None:
    artifact = ("[Rule]\nRULE-SET,https://rules.example/http,DIRECT\n" if target != "mihomo" else """
rule-providers:
  Http: {type: http, url: https://rules.example/http, behavior: classical, format: text}
rules: ['RULE-SET,Http,DIRECT']
""")

    async def fetch(url):
        return {"status_code": 200, "content": b"# Public native syntax\nDOMAIN-SUFFIX,example.com\nUSER-AGENT,YouTube*\n"}

    report = await target_dependencies.audit_target_dependencies(
        target_dependencies.collect_target_dependencies(target, artifact), fetch=fetch,
    )
    assert report["dependencies"][0]["audit_status"] == want
    assert report["coverage"]["client_runtime_validated"] is False
    if want == "valid":
        assert report["dependencies"][0]["usages"][0]["validation_scope"] == "native_rule_set_structure"


@pytest.mark.asyncio
@pytest.mark.parametrize("entry,want", [
    ("DST-PORT,not-a-port", "invalid"),
    ("DST-PORT,70000", "invalid"),
    ("DST-PORT,100-80", "invalid"),
    ("DST-PORT,80/443/8000-8100", "valid"),
    ("NETWORK,nonsense", "invalid"),
    ("NETWORK,udp", "valid"),
    ("DOMAIN-REGEX,[", "not_audited"),
    ("PROCESS-NAME-REGEX,^(?P<name>app)$", "not_audited"),
    ("IN-TYPE,unknown", "not_audited"),
    ("RULE-SET,undeclared", "not_audited"),
    ("AND,((DOMAIN,example.com),(BOGUS))", "invalid"),
    ("AND,((DOMAIN,example.com),(DOMAIN-REGEX,[))", "not_audited"),
    ("AND,((DOMAIN,example.com),(NETWORK,nonsense))", "invalid"),
    ("DOMAIN,example.com,unvalidated-option", "not_audited"),
])
async def test_classical_rule_parameters_cannot_claim_unimplemented_syntax_as_valid(entry, want) -> None:
    inventory = target_dependencies.collect_target_dependencies("mihomo", """
rule-providers:
  P: {type: http, url: https://rules.example/list, behavior: classical, format: text}
rules: ['RULE-SET,P,DIRECT']
""")

    async def fetch(url):
        return {"status_code": 200, "content": (entry + "\n").encode()}

    report = await target_dependencies.audit_target_dependencies(inventory, fetch=fetch)
    assert report["dependencies"][0]["audit_status"] == want
    assert report["check_status"] == {"invalid": "failed", "not_audited": "incomplete", "valid": "passed"}[want]
    if want == "not_audited":
        assert report["dependencies"][0]["usages"][0]["reason"] == "rule_parameter_syntax_not_validated"
