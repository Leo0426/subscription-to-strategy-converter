from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.main import app


@pytest.mark.parametrize(
    ("raw", "rule_type", "match", "options", "expected"),
    [
        ("MATCH,DIRECT", "MATCH", "", [], "MATCH,REJECT"),
        ("FINAL,DIRECT", "FINAL", "", [], "FINAL,REJECT"),
        ("IP-CIDR,192.0.2.0/24,DIRECT,no-resolve", "IP-CIDR", "192.0.2.0/24", ["no-resolve"], "IP-CIDR,192.0.2.0/24,REJECT,no-resolve"),
        ("AND,((DST-PORT,443),(NOT,((RULE-SET,defined)))),DIRECT", "AND", "((DST-PORT,443),(NOT,((RULE-SET,defined))))", [], "AND,((DST-PORT,443),(NOT,((RULE-SET,defined)))),REJECT"),
    ],
)
def test_workspace_target_edit_preserves_rule_shape_and_options(
    raw: str, rule_type: str, match: str, options: list[str], expected: str,
) -> None:
    rule = {"id": "rule:0", "index": 0, "type": rule_type, "match": match,
            "target": "REJECT", "provider": "", "options": options, "raw": raw}

    compiled = TestClient(app).post("/compile", json={"workspace": {"target": "mihomo", "rules": [rule]}, "target": "mihomo"})

    assert compiled.status_code == 200
    assert YAML(typ="safe").load(compiled.text)["rules"] == [expected]


def test_workspace_rule_target_edit_agrees_between_simulate_and_compile() -> None:
    workspace = {
        "target": "mihomo",
        "rules": [{
            "id": "rule:0",
            "index": 0,
            "type": "DOMAIN",
            "match": "example.com",
            "target": "REJECT",
            "provider": "",
            "options": [],
            "raw": "DOMAIN,example.com,DIRECT",
        }],
    }
    client = TestClient(app)

    simulated = client.post("/simulate", json={"workspace": workspace, "destination": "example.com"})
    compiled = client.post("/compile", json={"workspace": workspace, "target": "mihomo"})

    assert simulated.status_code == 200
    assert simulated.json()["trace"]["target"] == "REJECT"
    assert compiled.status_code == 200
    assert YAML(typ="safe").load(compiled.text)["rules"] == ["DOMAIN,example.com,REJECT"]


def test_workspace_dict_rule_target_edit_preserves_other_source_fields() -> None:
    workspace = {
        "target": "mihomo",
        "rules": [{
            "id": "rule:0",
            "index": 0,
            "type": "DOMAIN",
            "match": "example.com",
            "target": "REJECT",
            "provider": "",
            "options": ["no-resolve"],
            "raw": {
                "type": "DOMAIN",
                "value": "example.com",
                "policy": "DIRECT",
                "options": ["no-resolve"],
                "comment": "keep",
            },
        }],
    }
    client = TestClient(app)

    simulated = client.post("/simulate", json={"workspace": workspace, "destination": "example.com"})
    compiled = client.post("/compile", json={"workspace": workspace, "target": "mihomo"})

    assert simulated.status_code == 200
    assert simulated.json()["trace"]["target"] == "REJECT"
    assert compiled.status_code == 200
    assert YAML(typ="safe").load(compiled.text)["rules"] == [{
        "type": "DOMAIN",
        "value": "example.com",
        "policy": "REJECT",
        "options": ["no-resolve"],
        "comment": "keep",
    }]


def test_workspace_match_edit_agrees_between_simulate_and_compile() -> None:
    workspace = {
        "target": "mihomo",
        "rules": [{
            "id": "rule:0",
            "index": 0,
            "type": "DOMAIN",
            "match": "new.example.com",
            "target": "DIRECT",
            "provider": "",
            "options": [],
            "raw": "DOMAIN,old.example.com,DIRECT",
        }],
    }
    client = TestClient(app)

    simulated = client.post("/simulate", json={"workspace": workspace, "destination": "new.example.com"})
    compiled = client.post("/compile", json={"workspace": workspace, "target": "mihomo"})

    assert simulated.status_code == 200
    assert simulated.json()["trace"]["target"] == "DIRECT"
    assert compiled.status_code == 200
    assert YAML(typ="safe").load(compiled.text)["rules"] == ["DOMAIN,new.example.com,DIRECT"]


def test_workspace_rule_type_and_options_edits_compile_from_structured_fields() -> None:
    rule = {"id": "rule:0", "index": 0, "type": "DOMAIN-SUFFIX", "match": "example.com",
            "target": "DIRECT", "provider": "", "options": ["no-resolve"],
            "raw": "DOMAIN,example.com,DIRECT"}

    compiled = TestClient(app).post("/compile", json={"workspace": {"target": "mihomo", "rules": [rule]}, "target": "mihomo"})

    assert compiled.status_code == 200
    assert YAML(typ="safe").load(compiled.text)["rules"] == ["DOMAIN-SUFFIX,example.com,DIRECT,no-resolve"]


def test_workspace_unedited_rules_keep_original_spelling() -> None:
    raw_rules = [
        "DOMAIN, example.com , DIRECT , no-resolve",
        "AND,((DST-PORT,443),(NOT,((RULE-SET,defined)))),PROXY",
        {"type": "DOMAIN-SUFFIX", "value": "sample.com", "policy": "DIRECT", "comment": "keep"},
    ]
    workspace = {
        "target": "mihomo",
        "rules": [
            {"id": "rule:0", "index": 0, "type": "DOMAIN", "match": "example.com",
             "target": "DIRECT", "provider": "", "options": ["no-resolve"], "raw": raw_rules[0]},
            {"id": "rule:1", "index": 1, "type": "AND", "match": "((DST-PORT,443),(NOT,((RULE-SET,defined))))",
             "target": "PROXY", "provider": "", "options": [], "raw": raw_rules[1]},
            {"id": "rule:2", "index": 2, "type": "DOMAIN-SUFFIX", "match": "sample.com",
             "target": "DIRECT", "provider": "", "options": [], "raw": raw_rules[2]},
        ],
    }

    compiled = TestClient(app).post("/compile", json={"workspace": workspace, "target": "mihomo"})

    assert compiled.status_code == 200
    assert YAML(typ="safe").load(compiled.text)["rules"] == raw_rules


def test_workspace_rule_provider_edit_updates_match_and_compiled_rule() -> None:
    rule = {"id": "rule:0", "index": 0, "type": "RULE-SET", "match": "old",
            "target": "DIRECT", "provider": "new", "options": [],
            "raw": "RULE-SET,old,DIRECT"}
    workspace = {"target": "mihomo", "rules": [rule]}
    client = TestClient(app)

    simulated = client.post("/simulate", json={"workspace": workspace, "destination": "example.com"})
    compiled = client.post("/compile", json={"workspace": workspace, "target": "mihomo"})

    assert simulated.status_code == 200
    assert "RULE-SET 'new'" in simulated.json()["trace"]["steps"][0]["message"]
    assert compiled.status_code == 200
    assert YAML(typ="safe").load(compiled.text)["rules"] == ["RULE-SET,new,DIRECT"]


def test_workspace_rule_set_match_edit_updates_provider_alias() -> None:
    rule = {"id": "rule:0", "index": 0, "type": "RULE-SET", "match": "new",
            "target": "DIRECT", "provider": "old", "options": [],
            "raw": "RULE-SET,old,DIRECT"}
    workspace = {"target": "mihomo", "rules": [rule]}
    client = TestClient(app)

    simulated = client.post("/simulate", json={"workspace": workspace, "destination": "example.com"})
    compiled = client.post("/compile", json={"workspace": workspace, "target": "mihomo"})

    assert simulated.status_code == 200
    assert "RULE-SET 'new'" in simulated.json()["trace"]["steps"][0]["message"]
    assert compiled.status_code == 200
    assert YAML(typ="safe").load(compiled.text)["rules"] == ["RULE-SET,new,DIRECT"]


def test_workspace_conflicting_rule_set_aliases_are_rejected() -> None:
    rule = {"id": "rule:0", "index": 0, "type": "RULE-SET", "match": "one",
            "target": "DIRECT", "provider": "two", "options": [],
            "raw": "RULE-SET,old,DIRECT"}
    workspace = {"target": "mihomo", "rules": [rule]}
    client = TestClient(app)

    assert client.post("/simulate", json={"workspace": workspace, "destination": "example.com"}).status_code == 422
    assert client.post("/compile", json={"workspace": workspace, "target": "mihomo"}).status_code == 422


@pytest.mark.parametrize("options", [[], ["dns-failed"]])
def test_surge_final_rule_agrees_between_simulation_and_compilation(options):
    raw = ",".join(["FINAL", "REJECT", *options])
    workspace = {"target": "surge", "rules": [{
        "id": "rule:0", "index": 0, "type": "FINAL", "match": "",
        "target": "REJECT", "options": options, "raw": raw,
    }]}
    client = TestClient(app)
    simulated = client.post("/simulate", json={"workspace": workspace, "destination": "example.com"})
    compiled = client.post("/compile", json={"workspace": workspace, "target": "surge"})
    assert simulated.status_code == 200
    assert simulated.json()["trace"]["target"] == "REJECT"
    assert simulated.json()["trace"]["resolved"] == "REJECT"
    assert compiled.status_code == 200
    assert raw in compiled.text.split("[Rule]", 1)[1].splitlines()
