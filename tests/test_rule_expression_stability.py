"""Workspace edits and provider findings follow actual rule expressions."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from app.core.policy_analyzer import analyze_workspace
from app.core.policy_graph import build_policy_graph
from app.core.policy_workspace import workspace_from_dict
from app.main import app


SOURCE = """proxies:
- {name: US01, type: ss, server: node.example.com, port: 443, cipher: aes-128-gcm, password: synthetic}
"""
INLINE_PROVIDER = {"type": "inline", "behavior": "domain", "payload": ["+.example.com"]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fetch(_url, **_kwargs):
        return SOURCE

    monkeypatch.setattr("app.core.subscription.fetch_subscription", fetch)
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "profiles.db"))
    with TestClient(app) as session:
        yield session


def request_for(rule, providers=None):
    return {"subscription_url": "https://example.com/synthetic", "publication_targets": ["mihomo"],
            "selected_policy": {"mode": "replace", "rules": [rule, "MATCH,REJECT"],
                                "rule_providers": providers or {}}}


@pytest.mark.parametrize(("raw", "kind", "match", "edited"), [
    (r"DOMAIN-REGEX,^a{1,3}\.example\.com$,DIRECT", "DOMAIN-REGEX", r"^a{1,3}\.example\.com$",
     r"DOMAIN-REGEX,^a{1,3}\.example\.com$,REJECT"),
    (r"PROCESS-NAME-REGEX,^app\(test$,DIRECT", "PROCESS-NAME-REGEX", r"^app\(test$",
     r"PROCESS-NAME-REGEX,^app\(test$,REJECT"),
    ("PROCESS-PATH-REGEX,^/opt/a,b$,DIRECT", "PROCESS-PATH-REGEX", "^/opt/a,b$",
     "PROCESS-PATH-REGEX,^/opt/a,b$,REJECT"),
    (r"DOMAIN-REGEX, ^a{1,3}\.example\.com$ ,DIRECT", "DOMAIN-REGEX", r"^a{1,3}\.example\.com$",
     r"DOMAIN-REGEX, ^a{1,3}\.example\.com$ ,REJECT"),
])
def test_regex_workspace_roundtrip_and_target_edit_keep_complete_expression(client, raw, kind, match, edited):
    request = request_for(raw)
    preview = client.post("/workspace/preview", json=request)
    assert preview.status_code == 200, preview.text
    workspace = preview.json()["workspace"]
    rule = workspace["rules"][0]
    assert (rule["type"], rule["match"], rule["target"], rule["options"]) == (kind, match, "DIRECT", [])
    assert not [finding for finding in preview.json()["findings"] if finding["severity"] == "error"]
    assert client.post("/check", json=request).json()["can_publish"] is True

    unchanged = client.post("/compile", json={"workspace": workspace})
    assert unchanged.status_code == 200, unchanged.text
    assert YAML(typ="safe").load(unchanged.text)["rules"][0] == raw

    rule["target"] = "REJECT"
    compiled = client.post("/compile", json={"workspace": workspace})
    assert compiled.status_code == 200, compiled.text
    assert YAML(typ="safe").load(compiled.text)["rules"][0] == edited


def test_regex_workspace_match_edit_replaces_the_complete_old_expression(client):
    preview = client.post("/workspace/preview", json=request_for(r"DOMAIN-REGEX,^a{1,3}\.example$,DIRECT"))
    workspace = preview.json()["workspace"]
    workspace["rules"][0]["match"] = r"^b{2,4}\.example$"

    compiled = client.post("/compile", json={"workspace": workspace})

    assert compiled.status_code == 200, compiled.text
    assert YAML(typ="safe").load(compiled.text)["rules"][0] == r"DOMAIN-REGEX,^b{2,4}\.example$,DIRECT"


@pytest.mark.parametrize("rule", [
    "RULE-SET ,missing,DIRECT",
    "AND,((RULE-SET ,missing),(NOT,((RULE-SET,defined)))),DIRECT",
    "OR,((RULE-SET,defined),(AND,((RULE-SET ,missing),(RULE-SET ,missing)))),DIRECT",
    "AND,((PROCESS-NAME-REGEX,^Bob's App$),(RULE-SET ,missing)),DIRECT",
])
def test_missing_provider_with_whitespace_blocks_check_and_profile_and_appears_in_graph(client, rule):
    request = request_for(rule, {"defined": INLINE_PROVIDER})
    preview = client.post("/workspace/preview", json=request)
    assert preview.status_code == 200, preview.text
    missing = [finding for finding in preview.json()["findings"] if finding["code"] == "missing_provider"]
    assert len(missing) == 1
    assert "'missing'" in missing[0]["message"]
    edges = preview.json()["graph"]["edges"]
    assert sum(edge["source"] == "rule:0" and edge["target"] == "missing:missing"
               and edge["type"] == "rule-provider" for edge in edges) == 1

    checked = client.post("/check", json=request)
    assert checked.status_code == 200, checked.text
    assert checked.json()["can_publish"] is False
    dependencies = checked.json()["clients"][0]["dependencies"]["dependencies"]
    assert any(dependency["source"] == "unresolved" and dependency["location"] == "missing"
               and "rules[0]" in dependency["references"] for dependency in dependencies)
    assert client.post("/profiles", json=request).status_code == 400
    assert client.get("/profiles").json()["profiles"] == []


@pytest.mark.parametrize("rule", [
    "PROCESS-NAME-REGEX,^RULE-SET,imaginary$,DIRECT",
    "AND,((PROCESS-NAME-REGEX,^RULE-SET,imaginary$),(RULE-SET ,defined)),DIRECT",
    "AND,((PROCESS-NAME-REGEX,^Bob's App$),(RULE-SET ,defined)),DIRECT",
])
def test_regex_payload_text_is_not_a_provider_reference(client, rule):
    request = request_for(rule, {"defined": INLINE_PROVIDER})
    preview = client.post("/workspace/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert not [finding for finding in preview.json()["findings"] if finding["severity"] == "error"]
    assert preview.json()["workspace"]["rules"][0]["target"] == "DIRECT"
    references = {edge["target"] for edge in preview.json()["graph"]["edges"] if edge["type"] == "rule-provider"}
    assert references == ({"provider:defined"} if rule.startswith("AND,") else set())
    checked = client.post("/check", json=request).json()
    assert checked["can_publish"] is True
    inventory = checked["clients"][0]["dependencies"]
    assert inventory["summary"]["unresolved"] == 0
    if rule.startswith("AND,"):
        assert any(dependency["location"] == "defined" and "rules[0]" in dependency["references"]
                   for dependency in inventory["dependencies"])
    assert client.post("/profiles", json=request).status_code == 201


def test_analyzer_and_graph_use_edited_logical_match_instead_of_original_raw(client):
    data = {"target": "mihomo", "rules": [{
        "id": "rule:0", "index": 0, "type": "AND", "target": "DIRECT", "options": [],
        "match": "((RULE-SET,new),(NETWORK,TCP))", "provider": "",
        "raw": "AND,((RULE-SET,old),(NETWORK,TCP)),DIRECT",
    }], "rule_providers": {"new": INLINE_PROVIDER}}
    original = deepcopy(data)
    workspace = workspace_from_dict(data)

    assert not [finding for finding in analyze_workspace(workspace) if finding.severity == "error"]
    references = {edge.target for edge in build_policy_graph(workspace).edges if edge.type == "rule-provider"}
    assert references == {"provider:new"}
    compiled = client.post("/compile", json={"workspace": data})
    assert compiled.status_code == 200, compiled.text
    assert YAML(typ="safe").load(compiled.text)["rules"] == ["AND,((RULE-SET,new),(NETWORK,TCP)),DIRECT"]
    assert data == original
