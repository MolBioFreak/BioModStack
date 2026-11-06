"""Retired execution-target inventories must compact without touching receipts.

Real scratch SQLite fixtures; the production compaction script is imported from
the repository so the test exercises the same selection and rewrite rules.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "compact_retired_execution_targets.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("compact_retired_execution_targets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE execution_targets (
            id TEXT PRIMARY KEY, provider TEXT, provider_instance_id TEXT, name TEXT,
            state TEXT, active INTEGER, host TEXT, port INTEGER, username TEXT,
            remote_root TEXT, host_key_sha256 TEXT, capabilities TEXT, pricing TEXT,
            provider_metadata TEXT, leased_job_id TEXT, lease_acquired_at TEXT,
            last_error TEXT, last_seen_at TEXT, activated_at TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, execution_target_id TEXT);
        """
    )
    return connection


def _target(connection, identifier, *, active=0, leased=None, bulk=True):
    metadata: dict[str, object] = {"inventory": {"status": "complete"}, "setup": {"phase": "verified"}}
    capabilities: dict[str, object] = {"gpu_count": 1, "gpu_name": "RTX 4090"}
    if bulk:
        metadata["managed_inventory"] = {"files": ["x" * 2000]}
        metadata["preload"] = {"artifacts": ["y" * 2000]}
        metadata["critical_runtime_manifest"] = {"entries": ["z" * 2000]}
        capabilities["critical_runtime"] = {"digest": "q" * 2000}
    connection.execute(
        "INSERT INTO execution_targets (id, provider, provider_instance_id, name, state, active,"
        " capabilities, provider_metadata, leased_job_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (identifier, "vast", identifier, identifier, "inactive" if not active else "ready",
         active, json.dumps(capabilities), json.dumps(metadata), leased),
    )


def test_dry_run_reports_without_writing(tmp_path):
    script = _load_script()
    database = tmp_path / "core.db"
    connection = _database(database)
    _target(connection, "vast:1")
    connection.commit()
    connection.close()

    assert script.main([str(database)]) == 0
    connection = sqlite3.connect(database)
    stored = connection.execute("select provider_metadata from execution_targets").fetchone()[0]
    assert "managed_inventory" in json.loads(stored)


def test_apply_drops_bulk_but_keeps_identity_and_jobs(tmp_path, capsys):
    script = _load_script()
    database = tmp_path / "core.db"
    connection = _database(database)
    _target(connection, "vast:1")
    connection.execute("INSERT INTO jobs VALUES ('job-1', 'completed', 'vast:1')")
    connection.execute("INSERT INTO jobs VALUES ('job-2', 'failed', 'vast:1')")
    connection.commit()
    connection.close()

    assert script.main([str(database), "--apply", "--backup", str(tmp_path / "backup.db")]) == 0
    assert (tmp_path / "backup.db").is_file()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    row = connection.execute("select * from execution_targets").fetchone()
    metadata = json.loads(row["provider_metadata"])
    capabilities = json.loads(row["capabilities"])
    assert row["id"] == "vast:1" and row["state"] == "inactive" and row["active"] == 0
    assert metadata["inventory"] == {"status": "complete"}
    assert metadata["setup"] == {"phase": "verified"}
    for dropped in ("managed_inventory", "preload", "critical_runtime_manifest"):
        assert dropped not in metadata
    assert "critical_runtime" not in capabilities
    assert capabilities["gpu_name"] == "RTX 4090"
    assert metadata["retired_runtime"]["schema"] == "bms.execution-target-retirement.v1"
    assert metadata["retired_runtime"]["dropped_metadata_bytes"]["managed_inventory"] > 0
    assert connection.execute("select count(*) from jobs").fetchone()[0] == 2
    assert connection.execute("select status from jobs where id='job-1'").fetchone()[0] == "completed"


@pytest.mark.parametrize("kwargs", [
    {"active": 1},
    {"leased": "job-lease"},
])
def test_active_or_leased_targets_are_never_compacted(tmp_path, kwargs):
    script = _load_script()
    database = tmp_path / "core.db"
    connection = _database(database)
    _target(connection, "vast:1", **kwargs)
    connection.commit()
    connection.close()
    assert script.main([str(database), "--apply", "--backup", str(tmp_path / "b.db")]) == 0
    connection = sqlite3.connect(database)
    assert "managed_inventory" in connection.execute("select provider_metadata from execution_targets").fetchone()[0]


def test_target_with_nonterminal_job_is_skipped(tmp_path):
    script = _load_script()
    database = tmp_path / "core.db"
    connection = _database(database)
    _target(connection, "vast:1")
    connection.execute("INSERT INTO jobs VALUES ('job-live', 'running', 'vast:1')")
    connection.commit()
    connection.close()
    assert script.main([str(database), "--apply", "--backup", str(tmp_path / "b.db")]) == 0
    connection = sqlite3.connect(database)
    assert "managed_inventory" in connection.execute("select provider_metadata from execution_targets").fetchone()[0]
