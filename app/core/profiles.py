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
    artifacts: dict[str, str]


@dataclass(frozen=True)
class ProfileSummary:
    id: str
    target: str
    template: str
    clash_template: str
    surge_template: str
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
                "SELECT token_hash, request_json, artifact, artifacts_json FROM profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        if row is None or not hmac.compare_digest(row[0], _token_hash(token)):
            return None
        request = json.loads(row[1])
        artifacts = _artifacts_from_row(row[3])
        if row[2] is not None and not artifacts:
            legacy_target = _artifact_target(str(request.get("target", "mihomo")))
            artifacts[legacy_target] = row[2]
        return StoredProfile(id=profile_id, request=request, artifacts=artifacts)

    def save_artifact(self, profile_id: str, target: str, artifact: str) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT artifacts_json FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            artifacts = _artifacts_from_row(row[0] if row else None)
            artifacts[_artifact_target(target)] = artifact
            connection.execute(
                "UPDATE profiles SET artifacts_json = ? WHERE id = ?",
                (json.dumps(artifacts, ensure_ascii=False), profile_id),
            )

    def update(self, profile_id: str, token: str, request: dict[str, Any]) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
            if row is None or not hmac.compare_digest(row[0], _token_hash(token)):
                return False
            connection.execute(
                """
                UPDATE profiles
                SET request_json = ?, artifact = NULL, artifacts_json = '{}'
                WHERE id = ?
                """,
                (json.dumps(request, ensure_ascii=False), profile_id),
            )
        return True

    def list(self) -> list[ProfileSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, request_json, artifact, artifacts_json FROM profiles ORDER BY id"
            ).fetchall()
        summaries: list[ProfileSummary] = []
        for profile_id, request_json, artifact, artifacts_json in rows:
            request = json.loads(request_json)
            summaries.append(
                ProfileSummary(
                    id=profile_id,
                    target=str(request.get("target", "mihomo")),
                    template=str(request.get("template", "powerfullz")),
                    clash_template=str(
                        request.get("clash_template") or request.get("template", "powerfullz")
                    ),
                    surge_template=str(
                        request.get("surge_template") or request.get("template", "powerfullz")
                    ),
                    has_artifact=artifact is not None or bool(_artifacts_from_row(artifacts_json)),
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
                        artifact TEXT,
                        artifacts_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                columns = {
                    str(row[1]) for row in connection.execute("PRAGMA table_info(profiles)").fetchall()
                }
                if "artifacts_json" not in columns:
                    connection.execute(
                        "ALTER TABLE profiles ADD COLUMN artifacts_json TEXT NOT NULL DEFAULT '{}'"
                    )
                yield connection
        finally:
            connection.close()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _artifacts_from_row(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {str(target): str(artifact) for target, artifact in loaded.items() if artifact is not None}


def _artifact_target(target: str) -> str:
    return "mihomo" if target == "clash" else target
