from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import bms_backup  # noqa: E402
import paths  # noqa: E402
import telemetry_store  # noqa: E402


def _create_wal_db(db_path: Path, table: str, rows: list[tuple[int, str]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, val TEXT NOT NULL)")
        conn.executemany(f"INSERT INTO {table} (id, val) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _read_rows(db_path: Path, table: str) -> list[tuple[int, str]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return list(conn.execute(f"SELECT id, val FROM {table} ORDER BY id").fetchall())
    finally:
        conn.close()


@pytest.fixture
def isolated_bms_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    data_root = tmp_path / "bms_data"
    data_root.mkdir(parents=True, exist_ok=True)

    biomodstack_db = data_root / "biomodstack.db"
    experiments_db = data_root / "experiments.db"
    molbio_ngs_db = data_root / "molbio_ngs.db"
    telemetry_db = data_root / "telemetry" / "telemetry.sqlite3"

    _create_wal_db(biomodstack_db, "jobs", [(1, "job-committed")])
    _create_wal_db(experiments_db, "experiments", [(1, "exp-committed")])
    _create_wal_db(molbio_ngs_db, "samples", [(1, "sample-committed")])
    _create_wal_db(telemetry_db, "metrics", [(1, "metric-committed")])

    results_dir = data_root / "bms_results"
    inputs_dir = data_root / "inputs"
    preserved_dir = data_root / "preserved-artifacts"
    work_dir = data_root / "work"
    cache_dir = data_root / "analysis_cache"

    for directory, filename, body in [
        (results_dir, "run1/result.txt", "result-payload"),
        (inputs_dir, "input.fasta", ">seq1\nACGT\n"),
        (preserved_dir, "keep.json", '{"ok": true}'),
        (work_dir, "scratch.tmp", "should-not-be-backed-up"),
        (cache_dir, "cached.bin", "should-not-be-backed-up"),
    ]:
        target = directory / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")

    monkeypatch.setattr(paths, "get_data_root", lambda: data_root)
    monkeypatch.setattr(paths, "get_db_path", lambda: biomodstack_db)
    monkeypatch.setattr(paths, "get_experiment_db_path", lambda: experiments_db)
    monkeypatch.setattr(paths, "get_molbio_ngs_db_path", lambda: molbio_ngs_db)
    monkeypatch.setattr(telemetry_store, "telemetry_db_path", lambda: telemetry_db)
    monkeypatch.setattr(paths, "get_results_dir", lambda: results_dir)
    monkeypatch.setattr(paths, "get_inputs_dir", lambda: inputs_dir)

    return {
        "data_root": data_root,
        "biomodstack": biomodstack_db,
        "experiments": experiments_db,
        "molbio_ngs": molbio_ngs_db,
        "telemetry": telemetry_db,
        "bms_results": results_dir,
        "inputs": inputs_dir,
        "preserved-artifacts": preserved_dir,
        "work": work_dir,
        "analysis_cache": cache_dir,
    }


def test_four_store_and_artifact_backup_with_active_wal_transaction(
    isolated_bms_env: dict[str, Path],
    tmp_path: Path,
) -> None:
    """Committed WAL row is included, uncommitted WAL row is excluded, manifest is valid."""
    # Keep a second connection holding an uncommitted WAL transaction while a
    # committed row is already in the WAL.
    writer_conn = sqlite3.connect(isolated_bms_env["biomodstack"], isolation_level=None)
    try:
        writer_conn.execute("PRAGMA journal_mode=WAL")
        writer_conn.execute("BEGIN IMMEDIATE")
        writer_conn.execute("INSERT INTO jobs (id, val) VALUES (2, 'job-committed-wal')")
        writer_conn.execute("COMMIT")
        writer_conn.execute("BEGIN IMMEDIATE")
        writer_conn.execute("INSERT INTO jobs (id, val) VALUES (3, 'job-uncommitted-wal')")

        backup_dest = tmp_path / "backup_out"
        exit_code = bms_backup.main([
            "backup",
            "--dest", str(backup_dest),
            "--skip-service-check",
        ])
        assert exit_code == 0
    finally:
        writer_conn.execute("ROLLBACK")
        writer_conn.close()

    manifest_path = backup_dest / "manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["created_at_utc"].endswith("Z")
    assert manifest["git_sha"]

    stores_by_name = {entry["name"]: entry for entry in manifest["stores"]}
    assert set(stores_by_name) == {"biomodstack", "experiments", "molbio_ngs", "telemetry"}
    for entry in stores_by_name.values():
        assert entry["integrity_check"] == "ok"
        assert entry["foreign_key_violations"] == 0
        assert entry["size_bytes"] > 0
        assert len(entry["sha256"]) == 64
        assert (backup_dest / entry["backup_relpath"]).is_file()

    backed_up_jobs = _read_rows(backup_dest / "stores" / "biomodstack.db", "jobs")
    assert backed_up_jobs == [(1, "job-committed"), (2, "job-committed-wal")]

    artifacts_by_name = {entry["name"]: entry for entry in manifest["artifacts"]}
    assert artifacts_by_name["bms_results"]["copied"] is True
    assert artifacts_by_name["inputs"]["copied"] is True
    assert artifacts_by_name["preserved-artifacts"]["copied"] is True
    assert "work" not in artifacts_by_name
    assert "analysis_cache" not in artifacts_by_name
    assert (backup_dest / "artifacts" / "bms_results" / "run1" / "result.txt").read_text() == "result-payload"
    assert (backup_dest / "artifacts" / "inputs" / "input.fasta").read_text() == ">seq1\nACGT\n"
    assert (backup_dest / "artifacts" / "preserved-artifacts" / "keep.json").read_text() == '{"ok": true}'
    assert not (backup_dest / "artifacts" / "work").exists()
    assert not (backup_dest / "artifacts" / "analysis_cache").exists()

    # Refusing to overwrite a non-empty backup destination.
    assert bms_backup.main([
        "backup",
        "--dest", str(backup_dest),
        "--skip-service-check",
    ]) != 0


def test_writers_active_refuses_backup_and_restore(
    isolated_bms_env: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both backup and restore fail closed when systemctl reports an active writer unit."""
    # First create a clean backup with --skip-service-check so restore has a valid source.
    clean_backup = tmp_path / "clean_backup"
    assert bms_backup.main([
        "backup",
        "--dest", str(clean_backup),
        "--skip-service-check",
    ]) == 0
    capsys.readouterr()

    def fake_systemctl_active(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            unit = cmd[3]
            if unit == "biomodstack-api.service":
                return subprocess.CompletedProcess(cmd, 0, stdout="active\n", stderr="")
            return subprocess.CompletedProcess(cmd, 3, stdout="inactive\n", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(subprocess, "run", fake_systemctl_active)

    blocked_dest = tmp_path / "blocked_backup"
    assert bms_backup.main(["backup", "--dest", str(blocked_dest)]) != 0
    assert "biomodstack-api.service (active)" in capsys.readouterr().err
    assert not blocked_dest.exists()

    blocked_target = tmp_path / "blocked_restore"
    assert bms_backup.main([
        "restore",
        "--backup-dir", str(clean_backup),
        "--target-root", str(blocked_target),
    ]) != 0
    assert "biomodstack-api.service (active)" in capsys.readouterr().err
    assert not blocked_target.exists()


def test_verify_passes_clean_and_fails_on_tampered_db(
    isolated_bms_env: dict[str, Path],
    tmp_path: Path,
) -> None:
    backup_dest = tmp_path / "verify_backup"
    assert bms_backup.main([
        "backup",
        "--dest", str(backup_dest),
        "--skip-service-check",
    ]) == 0

    assert bms_backup.main(["verify", "--backup-dir", str(backup_dest)]) == 0

    # Tamper with a single byte in one backed-up SQLite store.
    target_db = backup_dest / "stores" / "experiments.db"
    raw = bytearray(target_db.read_bytes())
    raw[-1] ^= 0xFF
    target_db.write_bytes(bytes(raw))

    assert bms_backup.main(["verify", "--backup-dir", str(backup_dest)]) != 0


def test_restore_recovers_in_place_and_target_root(
    isolated_bms_env: dict[str, Path],
    tmp_path: Path,
) -> None:
    backup_dest = tmp_path / "restore_backup"
    assert bms_backup.main([
        "backup",
        "--dest", str(backup_dest),
        "--skip-service-check",
    ]) == 0

    # Mutate all four live databases and plant stale -wal/-shm sidecars + mutate artifacts.
    store_tables = [
        ("biomodstack", "jobs"),
        ("experiments", "experiments"),
        ("molbio_ngs", "samples"),
        ("telemetry", "metrics"),
    ]
    for store_name, table in store_tables:
        db_path = isolated_bms_env[store_name]
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(f"INSERT INTO {table} (id, val) VALUES (99, 'mutated')")
            conn.commit()
        finally:
            conn.close()
        Path(f"{db_path}-wal").write_bytes(b"stale-wal-bytes")
        Path(f"{db_path}-shm").write_bytes(b"stale-shm-bytes")

    (isolated_bms_env["bms_results"] / "run1" / "result.txt").write_text("corrupted", encoding="utf-8")
    (isolated_bms_env["bms_results"] / "extra.txt").write_text("extra", encoding="utf-8")

    # 1. Restore in-place.
    assert bms_backup.main([
        "restore",
        "--backup-dir", str(backup_dest),
        "--skip-service-check",
    ]) == 0

    expected_rows = {
        "biomodstack": [(1, "job-committed")],
        "experiments": [(1, "exp-committed")],
        "molbio_ngs": [(1, "sample-committed")],
        "telemetry": [(1, "metric-committed")],
    }
    for store_name, table in store_tables:
        db_path = isolated_bms_env[store_name]
        assert not Path(f"{db_path}-wal").exists()
        assert not Path(f"{db_path}-shm").exists()
        assert _read_rows(db_path, table) == expected_rows[store_name]

    assert (isolated_bms_env["bms_results"] / "run1" / "result.txt").read_text() == "result-payload"
    assert not (isolated_bms_env["bms_results"] / "extra.txt").exists()

    # 2. Restore into a separate --target-root.
    target_root = tmp_path / "restored_elsewhere"
    assert bms_backup.main([
        "restore",
        "--backup-dir", str(backup_dest),
        "--target-root", str(target_root),
        "--skip-service-check",
    ]) == 0

    assert _read_rows(target_root / "biomodstack.db", "jobs") == [(1, "job-committed")]
    assert _read_rows(target_root / "experiments.db", "experiments") == [(1, "exp-committed")]
    assert _read_rows(target_root / "molbio_ngs.db", "samples") == [(1, "sample-committed")]
    assert _read_rows(target_root / "telemetry" / "telemetry.sqlite3", "metrics") == [(1, "metric-committed")]
    assert (target_root / "bms_results" / "run1" / "result.txt").read_text() == "result-payload"
    assert (target_root / "inputs" / "input.fasta").read_text() == ">seq1\nACGT\n"
    assert (target_root / "preserved-artifacts" / "keep.json").read_text() == '{"ok": true}'
