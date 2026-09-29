"""Bounded, temporary in-memory store for policy payloads.

Proxy clients (Mihomo, sing-box) fetch the /subscribe URL via GET, so we
can't put the full policy selection in POST body.  Instead the browser POSTs
to /session, gets back a short UUID, and embeds only that ID in the URL.
Sessions are process-local and expire 24 hours after creation.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import OrderedDict
from threading import Lock
from typing import Any

SESSION_TTL_SECONDS = 24 * 60 * 60
MAX_SESSIONS = 64
MAX_SESSION_BYTES = 1024 * 1024


class SessionPayloadTooLargeError(ValueError):
    """A policy session payload exceeds the in-memory size limit."""


# Store the JSON bytes rather than the original dict so the payload bound is
# an actual bound on retained data and callers cannot enlarge an entry later.
_store: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_lock = Lock()


def _expire_sessions(now: float) -> None:
    for session_id, (expires_at, _) in list(_store.items()):
        if expires_at <= now:
            del _store[session_id]


def create_session(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_SESSION_BYTES:
        raise SessionPayloadTooLargeError(
            f"session payload exceeds {MAX_SESSION_BYTES} bytes"
        )

    session_id = str(uuid.uuid4())
    with _lock:
        now = time.monotonic()
        _expire_sessions(now)
        while len(_store) >= MAX_SESSIONS:
            _store.popitem(last=False)
        _store[session_id] = (now + SESSION_TTL_SECONDS, payload)
    return session_id


def get_session(session_id: str) -> dict[str, Any] | None:
    with _lock:
        _expire_sessions(time.monotonic())
        entry = _store.get(session_id)
        if entry is None:
            return None
        _store.move_to_end(session_id)
        payload = entry[1]
    return json.loads(payload)
