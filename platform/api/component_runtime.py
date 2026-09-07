"""Placement-neutral component grouping and durable expansion contracts.

This is not a scheduler: Nextflow/host adapters still own execution and process
termination. In particular cancel_requested is intent, never proof of quiescence.
No database server, scientific dependencies, or host callbacks are needed here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath
import sqlite3
from typing import Any, Callable, Mapping, Sequence, TypeVar

T = TypeVar("T")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def durable_write(path: Path, payload: bytes) -> None:
    """Atomic publication with durable file and containing-directory metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass(frozen=True, order=True)
class CandidateIdentity:
    producer_candidate_key: str
    candidate_id: str

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> CandidateIdentity:
        # Prepared native requests preserve the producer key as source_artifact's
        # logical relative path. Never sort by the staging directory or hash ID.
        key = record.get("producer_candidate_key")
        if key is None:
            key = (record.get("source_artifact") or {}).get("relative_path")
        candidate = record.get("candidate_id")
        if not isinstance(key, str) or not key or not isinstance(candidate, str) or not candidate:
            raise ValueError("candidate ordering authority is missing")
        return cls(key, candidate)


def ordered_candidates(items: Sequence[T], record: Callable[[T], Mapping[str, Any]]) -> tuple[T, ...]:
    identities = [CandidateIdentity.from_record(record(item)) for item in items]
    if len(set(identities)) != len(identities):
        raise ValueError("terminal structure dataset has duplicate ordering identities")
    ids = [item.candidate_id for item in identities]
    if len(set(ids)) != len(ids):
        raise ValueError("terminal structure dataset has duplicate candidate IDs")
    return tuple(item for _, item in sorted(zip(identities, items), key=lambda pair: pair[0]))


def partition_ordered(items: Sequence[T], *, batching_enabled: bool,
                      structures_per_job: int) -> tuple[tuple[T, ...], ...]:
    """Preserve the caller's authoritative order; disabled batching means singles."""
    if not isinstance(batching_enabled, bool):
        raise ValueError("batching_enabled must be a boolean")
    if type(structures_per_job) is not int or structures_per_job < 1:
        raise ValueError("structures_per_job must be a positive integer")
    if not items:
        raise ValueError("terminal structure dataset is empty")
    size = structures_per_job if batching_enabled else 1
    return tuple(tuple(items[start:start + size]) for start in range(0, len(items), size))


@dataclass(frozen=True)
class GroupingPlan:
    parent_job_id: str
    parent_workflow_id: str
    settings_sha256: str
    candidate_authority_sha256: str
    groups: tuple[tuple[CandidateIdentity, ...], ...]

    @property
    def payload(self) -> dict[str, Any]:
        return {"schema_name": "bms.component-grouping.v1", "schema_version": 1, **asdict(self)}

    @property
    def plan_id(self) -> str:
        return digest(self.payload)

    def component_id(self, ordinal: int) -> str:
        if not 0 <= ordinal < len(self.groups):
            raise ValueError("foreign group ordinal")
        # Logical identity is placement-neutral; the ledger additionally binds
        # the full adapter-native input closure through candidate_authority_sha256.
        logical = dict(self.payload)
        logical.pop("candidate_authority_sha256")
        return f"frustrampnn:{digest(logical)}:{ordinal}"

    def require_groups(self, observed: Sequence[Sequence[str]]) -> None:
        expected = [[member.candidate_id for member in group] for group in self.groups]
        if [list(group) for group in observed] != expected:
            raise ValueError("component grouping/order does not match required exact join")


def plan_frustrampnn(records: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]) -> GroupingPlan:
    ordered = ordered_candidates(records, lambda record: record)
    if not ordered:
        raise ValueError("terminal structure dataset is empty")
    first = ordered[0]
    for record in ordered:
        if any(record.get(key) != first.get(key) for key in ("parent_job_id", "parent_workflow_id")):
            raise ValueError("candidates mix parent authority")
        if record.get("requiredness") != "required":
            raise ValueError("FrustraMPNN grouping requires explicit required candidates")
        if "requested_settings" in record and record["requested_settings"] != settings:
            raise ValueError("candidates mix requested settings authority")
    if not first.get("parent_job_id") or not first.get("parent_workflow_id"):
        raise ValueError("parent identity is required")
    groups = partition_ordered(ordered, batching_enabled=settings["batching_enabled"],
                               structures_per_job=settings["structures_per_job"])
    return GroupingPlan(first["parent_job_id"], first["parent_workflow_id"], digest(settings),
                        digest(ordered), tuple(tuple(CandidateIdentity.from_record(r) for r in g) for g in groups))


@dataclass(frozen=True)
class ResultReference:
    component_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    schema: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (not self.relative_path or path.is_absolute() or ".." in path.parts
                or "\\" in self.relative_path or path.as_posix() != self.relative_path
                or self.relative_path == "."):
            raise ValueError("result reference must have a contained relative path")
        if (len(self.sha256) != 64 or any(c not in "0123456789abcdef" for c in self.sha256)
                or type(self.size_bytes) is not int or self.size_bytes < 0 or not self.schema):
            raise ValueError("invalid result reference binding")

    def resolve(self, root: Path) -> Path:
        root = Path(root).resolve(strict=True)
        candidate = root / self.relative_path
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("result reference escapes artifact root")
        payload = resolved.read_bytes()
        if len(payload) != self.size_bytes or hashlib.sha256(payload).hexdigest() != self.sha256:
            raise ValueError("result reference byte binding mismatch")
        return resolved


class GroupingLedger:
    """Attempt-local SQLite expansion journal, safe on restart and concurrent writers.

    Sealing records adapter-validated result references; it is not scientific
    validation or process cancellation. Adapters must prove both separately.
    """
    def __init__(self, path: Path, *, attempt_id: str, plan: GroupingPlan):
        if not attempt_id:
            raise ValueError("attempt identity is required")
        self.path, self.attempt_id, self.plan = Path(path), attempt_id, plan
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS authority (singleton INTEGER PRIMARY KEY CHECK(singleton=1), attempt TEXT NOT NULL, plan BLOB NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS results (component TEXT PRIMARY KEY, reference BLOB NOT NULL)")
            db.execute("INSERT OR IGNORE INTO authority(singleton,attempt,plan) VALUES(1,?,?)",
                       (attempt_id, canonical_bytes(plan.payload)))
            row = db.execute("SELECT attempt,plan FROM authority WHERE singleton=1").fetchone()
            if row != (attempt_id, canonical_bytes(plan.payload)):
                raise ValueError("ledger attempt or immutable grouping plan conflicts")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def check_active(self) -> None:
        with self._connect() as db:
            if db.execute("SELECT cancel_requested FROM authority").fetchone()[0]:
                raise RuntimeError("component cancellation requested; quiescence is not yet established")

    def request_cancel(self) -> None:
        with self._connect() as db:
            db.execute("UPDATE authority SET cancel_requested=1 WHERE singleton=1")

    def seal(self, reference: ResultReference) -> None:
        if reference.component_id not in {self.plan.component_id(i) for i in range(len(self.plan.groups))}:
            raise ValueError("foreign component result")
        payload = canonical_bytes(asdict(reference))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT cancel_requested FROM authority").fetchone()[0]:
                raise RuntimeError("cannot seal after cancellation intent")
            db.execute("INSERT OR IGNORE INTO results VALUES(?,?)", (reference.component_id, payload))
            if db.execute("SELECT reference FROM results WHERE component=?", (reference.component_id,)).fetchone()[0] != payload:
                raise ValueError("component result reference conflicts")

    def join(self) -> tuple[ResultReference, ...]:
        with self._connect() as db:
            db.execute("BEGIN")
            if db.execute("SELECT cancel_requested FROM authority").fetchone()[0]:
                raise RuntimeError("cannot join after cancellation intent")
            rows = dict(db.execute("SELECT component,reference FROM results").fetchall())
        ids = [self.plan.component_id(i) for i in range(len(self.plan.groups))]
        if set(rows) != set(ids):
            raise RuntimeError("required component result join is incomplete")
        return tuple(ResultReference(**json.loads(rows[identity])) for identity in ids)
