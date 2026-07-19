from __future__ import annotations

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

JobState = Literal[
    "validated_offline",
    "submission_pending",
    "submission_blocked",
    "submitted",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "delivery_failed",
    "recovery_required",
]

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "validated_offline": frozenset({"submission_pending", "submission_blocked", "cancelled"}),
    "submission_pending": frozenset(
        {"submitted", "submission_blocked", "delivery_failed", "recovery_required", "cancelled"}
    ),
    "submission_blocked": frozenset({"submission_pending", "cancelled"}),
    "submitted": frozenset({"running", "completed", "failed", "cancelled", "recovery_required"}),
    "running": frozenset({"paused", "completed", "failed", "cancelled", "recovery_required"}),
    "paused": frozenset({"running", "failed", "cancelled", "recovery_required"}),
    "recovery_required": frozenset({"cancelled", "failed"}),
    "delivery_failed": frozenset({"submission_pending", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
_RECOVER_ON_OPEN = ("submission_pending", "submitted", "running", "paused")


class JobConflictError(RuntimeError):
    pass


class JobTransitionError(RuntimeError):
    pass


class BioXpJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    idempotency_key: str
    protocol: dict[str, Any]
    compiled_hash: str
    state: JobState
    created_at: datetime
    updated_at: datetime
    detail: str | None = None
    generation: int | None = None
    remote_job_id: str | None = None


class BioXpJobEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int
    job_id: str
    from_state: str | None
    to_state: JobState
    occurred_at: datetime
    detail: str


class LegacyMigrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    migrated: int = 0
    quarantined: int = 0


class BioXpJobStore:
    """SQLite-backed local authority with append-only transition evidence."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._recover_incomplete_jobs()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    protocol_json TEXT NOT NULL,
                    compiled_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    detail TEXT,
                    generation INTEGER,
                    remote_job_id TEXT
                );
                CREATE TABLE IF NOT EXISTS job_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bioxp_job_events_job
                    ON job_events(job_id, sequence);
                CREATE TRIGGER IF NOT EXISTS bioxp_job_events_no_update
                BEFORE UPDATE ON job_events
                BEGIN
                    SELECT RAISE(ABORT, 'BioXP job_events are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS bioxp_job_events_no_delete
                BEFORE DELETE ON job_events
                BEGIN
                    SELECT RAISE(ABORT, 'BioXP job_events are append-only');
                END;
                """
            )
        os.chmod(self.path, 0o600)

    def _recover_incomplete_jobs(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT job_id, state FROM jobs WHERE state IN ({','.join('?' for _ in _RECOVER_ON_OPEN)})",
                _RECOVER_ON_OPEN,
            ).fetchall()
            for row in rows:
                self._transition_in_transaction(
                    connection,
                    row["job_id"],
                    row["state"],
                    "recovery_required",
                    "Process restarted with an incomplete submission; remote state is unknown",
                )

    def recover_incomplete_jobs(self) -> None:
        self._recover_incomplete_jobs()

    def create_validated_job(
        self,
        *,
        protocol: dict[str, Any],
        compiled_hash: str,
        idempotency_key: str,
    ) -> BioXpJob:
        canonical = json.dumps(protocol, sort_keys=True, separators=(",", ":"))
        now = _utcnow_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                if existing["compiled_hash"] != compiled_hash or existing["protocol_json"] != canonical:
                    raise JobConflictError("Idempotency key is already bound to a different protocol")
                return _job_from_row(existing)
            job_id = str(uuid4())
            try:
                connection.execute(
                    "INSERT INTO jobs(job_id,idempotency_key,protocol_json,compiled_hash,state,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (job_id, idempotency_key, canonical, compiled_hash, "validated_offline", now, now),
                )
                connection.execute(
                    "INSERT INTO job_events(job_id,from_state,to_state,occurred_at,detail) VALUES(?,?,?,?,?)",
                    (job_id, None, "validated_offline", now, "Protocol compiled and validated offline"),
                )
            except sqlite3.IntegrityError:
                connection.rollback()
                with self._connect() as reread:
                    raced = reread.execute(
                        "SELECT * FROM jobs WHERE idempotency_key=?", (idempotency_key,)
                    ).fetchone()
                if raced is None:
                    raise
                if raced["compiled_hash"] != compiled_hash or raced["protocol_json"] != canonical:
                    raise JobConflictError("Idempotency key is already bound to a different protocol")
                return _job_from_row(raced)
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            assert row is not None
            return _job_from_row(row)

    def transition(
        self,
        job_id: str,
        to_state: JobState,
        *,
        detail: str,
        generation: int | None = None,
        remote_job_id: str | None = None,
    ) -> BioXpJob:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = str(row["state"])
            if current == to_state:
                if row["detail"] != detail:
                    raise JobTransitionError(
                        f"BioXP job is already {to_state} with different transition evidence"
                    )
                return _job_from_row(row)
            if to_state not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
                raise JobTransitionError(f"Invalid BioXP job transition: {current} -> {to_state}")
            self._transition_in_transaction(
                connection,
                job_id,
                current,
                to_state,
                detail,
                generation=generation,
                remote_job_id=remote_job_id,
            )
            updated = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            assert updated is not None
            return _job_from_row(updated)

    def _transition_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        from_state: str,
        to_state: JobState,
        detail: str,
        *,
        generation: int | None = None,
        remote_job_id: str | None = None,
    ) -> None:
        now = _utcnow_iso()
        connection.execute(
            "UPDATE jobs SET state=?,updated_at=?,detail=?,generation=COALESCE(?,generation),"
            "remote_job_id=COALESCE(?,remote_job_id) WHERE job_id=?",
            (to_state, now, detail, generation, remote_job_id, job_id),
        )
        connection.execute(
            "INSERT INTO job_events(job_id,from_state,to_state,occurred_at,detail) VALUES(?,?,?,?,?)",
            (job_id, from_state, to_state, now, detail),
        )

    def get(self, job_id: str) -> BioXpJob | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return _job_from_row(row) if row is not None else None

    def list(self, *, limit: int = 100) -> tuple[BioXpJob, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, job_id DESC LIMIT ?", (max(1, min(limit, 500)),)
            ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def events(self, job_id: str) -> tuple[BioXpJobEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_events WHERE job_id=? ORDER BY sequence", (job_id,)
            ).fetchall()
        return tuple(
            BioXpJobEvent(
                sequence=row["sequence"],
                job_id=row["job_id"],
                from_state=row["from_state"],
                to_state=row["to_state"],
                occurred_at=row["occurred_at"],
                detail=row["detail"],
            )
            for row in rows
        )

    def migrate_legacy_json(self, legacy_root: Path, quarantine_root: Path) -> LegacyMigrationResult:
        if not legacy_root.exists():
            return LegacyMigrationResult()
        quarantine_root.mkdir(parents=True, exist_ok=True)
        migrated = 0
        quarantined = 0
        for path in sorted(legacy_root.glob("*.json")):
            suffix = "invalid"
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                job = self._import_legacy(payload)
                suffix = "migrated"
                migrated += 1
                _ = job
            except Exception:
                quarantined += 1
            destination = _unused_destination(quarantine_root, f"{path.name}.{suffix}")
            shutil.move(str(path), destination)
        self._recover_incomplete_jobs()
        return LegacyMigrationResult(migrated=migrated, quarantined=quarantined)

    def _import_legacy(self, payload: object) -> BioXpJob:
        if not isinstance(payload, dict):
            raise ValueError("Legacy job must be an object")
        required = ("job_id", "state", "protocol", "compiled_hash", "idempotency_key")
        if any(key not in payload for key in required) or not isinstance(payload["protocol"], dict):
            raise ValueError("Legacy job fields are incomplete")
        state = str(payload["state"])
        if state not in _ALLOWED_TRANSITIONS:
            raise ValueError("Legacy job state is invalid")
        canonical = json.dumps(payload["protocol"], sort_keys=True, separators=(",", ":"))
        now = _utcnow_iso()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs(job_id,idempotency_key,protocol_json,compiled_hash,state,created_at,updated_at,detail) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(payload["job_id"]),
                    str(payload["idempotency_key"]),
                    canonical,
                    str(payload["compiled_hash"]),
                    state,
                    now,
                    now,
                    "Migrated from legacy JSON metadata",
                ),
            )
            connection.execute(
                "INSERT INTO job_events(job_id,from_state,to_state,occurred_at,detail) VALUES(?,?,?,?,?)",
                (str(payload["job_id"]), None, state, now, "Migrated from legacy JSON metadata"),
            )
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (str(payload["job_id"]),)).fetchone()
            assert row is not None
            return _job_from_row(row)

    def close(self) -> None:
        # Connections are per-operation so TestClient/thread boundaries remain safe.
        return None


def _job_from_row(row: sqlite3.Row) -> BioXpJob:
    return BioXpJob(
        job_id=row["job_id"],
        idempotency_key=row["idempotency_key"],
        protocol=json.loads(row["protocol_json"]),
        compiled_hash=row["compiled_hash"],
        state=row["state"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        detail=row["detail"],
        generation=row["generation"],
        remote_job_id=row["remote_job_id"],
    )


def _unused_destination(root: Path, name: str) -> str:
    destination = root / name
    counter = 1
    while destination.exists():
        destination = root / f"{name}.{counter}"
        counter += 1
    return str(destination)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
