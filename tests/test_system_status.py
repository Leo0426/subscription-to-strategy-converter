from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_system_status_reports_ready_dependencies(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    client = TestClient(app)

    response = client.get("/system/status")

    assert response.status_code == 200
    assert response.json()["app"]["status"] == "ok"
    assert response.json()["profile_db"]["status"] == "ok"
    assert "subconverter" not in response.json()
