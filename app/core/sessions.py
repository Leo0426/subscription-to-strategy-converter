"""In-memory session store for large policy payloads.

Proxy clients (Mihomo, sing-box) fetch the /subscribe URL via GET, so we
can't put the full policy selection in POST body.  Instead the browser POSTs
to /session, gets back a short UUID, and embeds only that ID in the URL.
"""
from __future__ import annotations

import uuid
from typing import Any

_store: dict[str, dict[str, Any]] = {}


def create_session(data: dict[str, Any]) -> str:
    session_id = str(uuid.uuid4())
    _store[session_id] = data
    return session_id


def get_session(session_id: str) -> dict[str, Any] | None:
    return _store.get(session_id)
