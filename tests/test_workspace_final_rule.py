"""A Surge FINAL rule keeps the same destination in simulation and output."""

from fastapi.testclient import TestClient

from app.main import app


def test_surge_final_rule_matches_and_compiles_with_selected_target() -> None:
    workspace = {
        "target": "surge",
        "rules": [{
            "id": "rule:0",
            "index": 0,
            "type": "FINAL",
            "match": "",
            "target": "REJECT",
            "options": [],
            "raw": "FINAL,REJECT",
        }],
    }
    client = TestClient(app)

    simulated = client.post(
        "/simulate", json={"workspace": workspace, "destination": "example.com"},
    )
    compiled = client.post("/compile", json={"workspace": workspace, "target": "surge"})

    assert simulated.status_code == 200
    assert simulated.json()["trace"]["target"] == "REJECT"
    assert simulated.json()["trace"]["resolved"] == "REJECT"
    assert compiled.status_code == 200
    assert "FINAL,REJECT" in compiled.text.split("[Rule]", 1)[1]


def test_surge_final_rule_keeps_dns_failed_option() -> None:
    workspace = {
        "target": "surge",
        "rules": [{
            "id": "rule:0",
            "index": 0,
            "type": "FINAL",
            "match": "",
            "target": "REJECT",
            "options": ["dns-failed"],
            "raw": "FINAL,REJECT,dns-failed",
        }],
    }

    compiled = TestClient(app).post("/compile", json={"workspace": workspace, "target": "surge"})

    assert compiled.status_code == 200
    assert "FINAL,REJECT,dns-failed" in compiled.text.split("[Rule]", 1)[1]
