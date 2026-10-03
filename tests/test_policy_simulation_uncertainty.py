"""Policy simulation preserves routing order and distinguishes runtime evidence."""

import pytest
from fastapi.testclient import TestClient

from app.core.service_catalog import service_catalog
from app.core.workbench import service_report
from app.main import app


def test_simulation_matches_ipv6_cidr_before_final_route() -> None:
    response = TestClient(app).post("/simulate", json={
        "workspace": {"rules": [
            "IP-CIDR6,2001:db8::/32,DIRECT,no-resolve",
            "MATCH,REJECT",
        ]},
        "destination": "2001:db8::1",
    })

    assert response.status_code == 200, response.text
    trace = response.json()["trace"]
    assert trace["target"] == "DIRECT"
    assert trace["matched_rule"]["type"] == "IP-CIDR6"
    assert trace["warnings"] == []


@pytest.mark.parametrize("runtime_rule", [
    "RULE-SET,remote,REJECT",
    "GEOSITE,openai,REJECT",
    "DST-PORT,443,REJECT",
    "AND,((DOMAIN,example.com),(DST-PORT,443)),REJECT",
])
def test_simulation_marks_route_after_runtime_rule_as_conditional(runtime_rule: str) -> None:
    response = TestClient(app).post("/simulate", json={
        "workspace": {"rules": [runtime_rule, "DOMAIN,example.com,DIRECT", "MATCH,REJECT"]},
        "destination": "example.com",
    })

    assert response.status_code == 200, response.text
    trace = response.json()["trace"]
    assert trace["target"] == "DIRECT"
    assert trace["steps"][0]["matched"] is None
    assert any("conditional" in warning.lower() for warning in trace["warnings"])


@pytest.mark.parametrize("runtime_first", [True, False])
def test_service_report_requires_runtime_rules_only_before_domain_match(runtime_first: bool) -> None:
    service = next(service for service in service_catalog() if service["id"] == "openai")
    exact_rules = [f"DOMAIN,{domain},DIRECT" for domain in service["destinations"]]
    runtime_rules = ["RULE-SET,remote,REJECT"]
    config = {"rules": (runtime_rules + exact_rules if runtime_first else exact_rules + runtime_rules)}

    report = service_report(config, [], "openai")[0]

    assert report["status"] == ("needs_review" if runtime_first else "consistent")
    expected = "runtime_rules_required" if runtime_first else "matched"
    assert {domain["status"] for domain in report["domains"]} == {expected}
    assert {domain["target"] for domain in report["domains"]} == {"DIRECT"}


@pytest.mark.parametrize("cidr_rule", [
    "IP-CIDR,192.0.2.0/24,REJECT",
    "IP-CIDR6,2001:db8::/32,REJECT",
])
@pytest.mark.parametrize("no_resolve", [False, True])
def test_domain_before_ip_rule_requires_dns_unless_no_resolve(cidr_rule: str, no_resolve: bool) -> None:
    response = TestClient(app).post("/simulate", json={
        "workspace": {"rules": [
            cidr_rule + (",no-resolve" if no_resolve else ""),
            "DOMAIN,example.com,DIRECT",
        ]},
        "destination": "example.com",
    })

    assert response.status_code == 200, response.text
    trace = response.json()["trace"]
    assert trace["target"] == "DIRECT"
    assert trace["steps"][0]["matched"] is (False if no_resolve else None)
    assert bool(trace["warnings"]) is (not no_resolve)
