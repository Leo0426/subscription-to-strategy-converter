from __future__ import annotations

import httpx
from fastapi import APIRouter

from app.api.convert import _profile_store
from app.core.subconverter import subconverter_base_url

router = APIRouter()


@router.get("/system/status")
async def system_status() -> dict[str, dict[str, str]]:
    return {
        "app": {"status": "ok"},
        "profile_db": _profile_db_status(),
        "subconverter": await _subconverter_status(),
    }


def _profile_db_status() -> dict[str, str]:
    try:
        store = _profile_store()
        store.list()
    except Exception as exc:  # pragma: no cover - platform-specific filesystem failures
        return {"status": "error", "detail": str(exc)}
    return {"status": "ok", "path": str(store.database)}


async def _subconverter_status() -> dict[str, str]:
    base_url = subconverter_base_url()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{base_url}/version")
    except httpx.HTTPError as exc:
        return {"status": "error", "base_url": base_url, "detail": str(exc)}

    if response.status_code < 200 or response.status_code >= 300:
        return {
            "status": "error",
            "base_url": base_url,
            "detail": f"HTTP {response.status_code}",
        }
    return {"status": "ok", "base_url": base_url, "version": response.text.strip()}
