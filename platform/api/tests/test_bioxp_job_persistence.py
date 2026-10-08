from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest


def _load():
    from services.bioxp.job_store import BioXpJobStore, JobConflictError, JobTransitionError

    return BioXpJobStore, JobConflictError, JobTransitionError


def _protocol(name: str = "demo") -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "steps": [{"action": "initialize_motors"}],
    }


def test_job_state_and_events_survive_process_restart(tmp_path: Path) -> None:
    Store, _, _ = _load()
    db = tmp_path / "jobs.sqlite3"
    store = Store(db)
    job = store.create_validated_job(
        protocol=_protocol(),
        compiled_hash="abc123",
        idempotency_key="submit-1",
    )
    store.transition(job.job_id, "submission_pending", detail="delivery starting")
    store.close()

    restarted = Store(db)
    recovered = restarted.get(job.job_id)
    events = restarted.events(job.job_id)

    assert recovered is not None
    assert recovered.state == "recovery_required"
    assert [event.to_state for event in events] == ["validated_offline", "submission_pending", "recovery_required"]
    assert events[-1].detail == "Process restarted with an incomplete submission; remote state is unknown"


def test_idempotency_reuses_same_submission_and_rejects_payload_change(tmp_path: Path) -> None:
    Store, Conflict, _ = _load()
    store = Store(tmp_path / "jobs.sqlite3")
    first = store.create_validated_job(protocol=_protocol(), compiled_hash="abc123", idempotency_key="same")
    second = store.create_validated_job(protocol=_protocol(), compiled_hash="abc123", idempotency_key="same")

    assert second.job_id == first.job_id
    with pytest.raises(Conflict):
        store.create_validated_job(protocol=_protocol("changed"), compiled_hash="different", idempotency_key="same")


def test_invalid_transition_is_rejected_without_mutating_history(tmp_path: Path) -> None:
    Store, _, Transition = _load()
    store = Store(tmp_path / "jobs.sqlite3")
    job = store.create_validated_job(protocol=_protocol(), compiled_hash="abc123", idempotency_key="one")

    with pytest.raises(Transition):
        store.transition(job.job_id, "completed", detail="cannot skip delivery")

    assert store.get(job.job_id).state == "validated_offline"
    assert len(store.events(job.job_id)) == 1


def test_legacy_json_migration_quarantines_malformed_files(tmp_path: Path) -> None:
    Store, _, _ = _load()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "valid.json").write_text(
        json.dumps(
            {
                "job_id": "legacy-valid",
                "state": "completed",
                "protocol": _protocol("legacy"),
                "compiled_hash": "legacyhash",
                "idempotency_key": "legacy-key",
            }
        ),
        encoding="utf-8",
    )
    (legacy / "broken.json").write_text("{not-json", encoding="utf-8")
    quarantine = tmp_path / "quarantine"

    store = Store(tmp_path / "jobs.sqlite3")
    result = store.migrate_legacy_json(legacy, quarantine)

    assert result.migrated == 1
    assert result.quarantined == 1
    assert store.get("legacy-valid").state == "completed"
    assert not (legacy / "valid.json").exists()
    assert not (legacy / "broken.json").exists()
    quarantined = list(quarantine.iterdir())
    assert len(quarantined) == 2
    assert any(path.name.startswith("broken.json.invalid") for path in quarantined)
    assert any(path.name.startswith("valid.json.migrated") for path in quarantined)


def test_legacy_active_job_is_recovered_immediately_during_migration(tmp_path: Path) -> None:
    Store, _, _ = _load()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "active.json").write_text(
        json.dumps(
            {
                "job_id": "legacy-active",
                "state": "running",
                "protocol": _protocol("legacy-active"),
                "compiled_hash": "legacy-active-hash",
                "idempotency_key": "legacy-active-key",
            }
        ),
        encoding="utf-8",
    )
    store = Store(tmp_path / "jobs.sqlite3")

    store.migrate_legacy_json(legacy, tmp_path / "quarantine")

    assert store.get("legacy-active").state == "recovery_required"
    assert [event.to_state for event in store.events("legacy-active")] == ["running", "recovery_required"]


def test_sqlite_enforces_one_job_per_idempotency_key(tmp_path: Path) -> None:
    Store, _, _ = _load()
    db = tmp_path / "jobs.sqlite3"
    store = Store(db)
    store.create_validated_job(protocol=_protocol(), compiled_hash="abc", idempotency_key="unique")

    with sqlite3.connect(db) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO jobs(job_id,idempotency_key,protocol_json,compiled_hash,state,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("other", "unique", "{}", "x", "validated_offline", "now", "now"),
        )


def test_concurrent_same_job_idempotency_key_converges_without_sqlite_error(tmp_path: Path) -> None:
    Store, _, _ = _load()
    store = Store(tmp_path / "jobs.sqlite3")

    def create(_: int):
        return store.create_validated_job(
            protocol=_protocol("concurrent"),
            compiled_hash="concurrent-hash",
            idempotency_key="concurrent-key",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(create, range(16)))

    assert len({job.job_id for job in jobs}) == 1
    assert len(store.list()) == 1
    assert len(store.events(jobs[0].job_id)) == 1


def test_job_events_are_append_only_at_sqlite_layer(tmp_path: Path) -> None:
    Store, _, _ = _load()
    db = tmp_path / "jobs.sqlite3"
    store = Store(db)
    job = store.create_validated_job(protocol=_protocol(), compiled_hash="abc", idempotency_key="append-only")

    with sqlite3.connect(db) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE job_events SET detail='tampered' WHERE job_id=?", (job.job_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM job_events WHERE job_id=?", (job.job_id,))

    events = store.events(job.job_id)
    assert len(events) == 1
    assert events[0].detail == "Protocol compiled and validated offline"


def test_concurrent_compound_submission_transition_converges_once(tmp_path: Path) -> None:
    Store, _, _ = _load()
    store = Store(tmp_path / "jobs.sqlite3")
    barrier = Barrier(8)
    detail = "Normal OEM command mappings are not verified; no robot delivery was attempted"

    def submit(_: int):
        job = store.create_validated_job(
            protocol=_protocol("compound"),
            compiled_hash="compound-hash",
            idempotency_key="compound-key",
        )
        barrier.wait()
        if job.state == "validated_offline":
            job = store.transition(job.job_id, "submission_blocked", detail=detail)
        return job

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(submit, range(8)))

    assert {job.state for job in jobs} == {"submission_blocked"}
    job_id = jobs[0].job_id
    assert [event.to_state for event in store.events(job_id)] == ["validated_offline", "submission_blocked"]
