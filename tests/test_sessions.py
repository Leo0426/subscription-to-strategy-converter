"""Behavioral limits for temporary policy sessions."""

import time
from collections import OrderedDict

import pytest

from app.core import sessions


@pytest.fixture(autouse=True)
def isolated_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "_store", OrderedDict())


def test_session_expires_at_creation_deadline_even_after_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(sessions, "SESSION_TTL_SECONDS", 10, raising=False)

    session_id = sessions.create_session({"policy": "chosen"})
    now[0] = 109.0
    assert sessions.get_session(session_id) == {"policy": "chosen"}

    now[0] = 110.0
    assert sessions.get_session(session_id) is None
    assert len(sessions._store) == 0


def test_session_capacity_evicts_least_recently_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sessions, "MAX_SESSIONS", 2, raising=False)

    first = sessions.create_session({"policy": "first"})
    second = sessions.create_session({"policy": "second"})
    assert sessions.get_session(first) == {"policy": "first"}

    third = sessions.create_session({"policy": "third"})
    assert sessions.get_session(second) is None
    assert sessions.get_session(first) == {"policy": "first"}
    assert sessions.get_session(third) == {"policy": "third"}


def test_oversized_utf8_payload_is_rejected_without_evicting_a_valid_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sessions, "MAX_SESSIONS", 1, raising=False)
    monkeypatch.setattr(sessions, "MAX_SESSION_BYTES", 32, raising=False)
    existing = sessions.create_session({"policy": "small"})

    with pytest.raises(ValueError, match="session payload"):
        sessions.create_session({"policy": "多" * 20})

    assert sessions.get_session(existing) == {"policy": "small"}


def test_session_payload_is_snapshot_of_input() -> None:
    data = {"policy": {"rules": ["FIRST"]}}
    session_id = sessions.create_session(data)
    data["policy"]["rules"].append("MUTATED")

    assert sessions.get_session(session_id) == {"policy": {"rules": ["FIRST"]}}
