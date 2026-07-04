from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


@dataclass(frozen=True)
class CreatedProfile:
    id: str
    token: str


@dataclass(frozen=True)
class StoredProfile:
    id: str
    request: dict[str, Any]
    artifact: str | None = None


@dataclass(frozen=True)
class ProfileSummary:
    id: str
    target: str
    template: str
    has_artifact: bool


class ProfileStore:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def create(self, request: dict[str, Any]) -> CreatedProfile:
        profile_id = secrets.token_urlsafe(16)
        token = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO profiles (id, token_hash, request_json) VALUES (?, ?, ?)",
                (profile_id, _token_hash(token), json.dumps(request, ensure_ascii=False)),
            )
        return CreatedProfile(id=profile_id, token=token)

    def get(self, profile_id: str, token: str) -> StoredProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash, request_json, artifact FROM profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        if row is None or not hmac.compare_digest(row[0], _token_hash(token)):
            return None
        return StoredProfile(id=profile_id, request=json.loads(row[1]), artifact=row[2])

    def save_artifact(self, profile_id: str, artifact: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE profiles SET artifact = ? WHERE id = ?", (artifact, profile_id))

    def list(self) -> list[ProfileSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, request_json, artifact FROM profiles ORDER BY id"
            ).fetchall()
        summaries: list[ProfileSummary] = []
        for profile_id, request_json, artifact in rows:
            request = json.loads(request_json)
            summaries.append(
                ProfileSummary(
                    id=profile_id,
                    target=str(request.get("target", "mihomo")),
                    template=str(request.get("template", "powerfullz")),
                    has_artifact=artifact is not None,
                )
            )
        return summaries

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database)
        self.database.chmod(0o600)
        try:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS profiles (
                        id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        artifact TEXT
                    )
                    """
                )
                yield connection
        finally:
            connection.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
