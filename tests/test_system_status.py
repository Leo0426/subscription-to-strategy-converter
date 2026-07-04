from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from app.main import app


def test_system_status_reports_ready_dependencies(tmp_path, monkeypatch) -> None:
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str):
            return httpx.Response(200, text="subconverter v0.9.0")

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.api.system.httpx.AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.get("/system/status")

    assert response.status_code == 200
    assert response.json()["app"]["status"] == "ok"
    assert response.json()["profile_db"]["status"] == "ok"
    assert response.json()["subconverter"]["status"] == "ok"


def test_system_status_reports_subconverter_offline(tmp_path, monkeypatch) -> None:
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            pass

        async def get(self, url: str):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setenv("SUBFLOW_DB_PATH", str(tmp_path / "subflow.db"))
    monkeypatch.setattr("app.api.system.httpx.AsyncClient", FakeAsyncClient)
    client = TestClient(app)

    response = client.get("/system/status")

    assert response.status_code == 200
    body = response.json()
    assert body["app"]["status"] == "ok"
    assert body["profile_db"]["status"] == "ok"
    assert body["subconverter"]["status"] == "error"
    assert "connection refused" in body["subconverter"]["detail"]
