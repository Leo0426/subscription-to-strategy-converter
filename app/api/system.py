from __future__ import annotations

from fastapi import APIRouter

from app.api.convert import _profile_store

router = APIRouter()


@router.get("/system/status")
async def system_status() -> dict[str, dict[str, str]]:
    return {
        "app": {"status": "ok"},
        "profile_db": _profile_db_status(),
    }


def _profile_db_status() -> dict[str, str]:
    try:
        store = _profile_store()
        store.list()
    except Exception as exc:  # pragma: no cover - platform-specific filesystem failures
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok", "path": str(store.database)}
