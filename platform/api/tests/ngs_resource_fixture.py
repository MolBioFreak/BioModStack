"""Isolated migrated global resource owner and real verified-cache lifecycle."""
from collections import OrderedDict
import threading

import pytest


@pytest.fixture
def ngs_resources(tmp_path, monkeypatch):
    from experiment_migrations import run_all
    from services import ngs_alignment_sessions as storage
    from services import job_result_roots

    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    database = tmp_path / "resources.db"
    for key, value in {
        "BMS_DATA": tmp_path,
        "BMS_RESULTS_DIR": results,
        "BMS_RESULTS_ROOT": results,
        "BMS_EXPERIMENT_DB_PATH": database,
        "BMS_DB_PATH": tmp_path / "core.db",
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'core.db'}",
        "BMS_EXECUTION_TARGET_ID": "local",
    }.items():
        monkeypatch.setenv(key, str(value))
    run_all(database)
    monkeypatch.setattr(storage, "get_results_dir", lambda: results)
    monkeypatch.setattr(job_result_roots, "get_results_dir", lambda: results)
    monkeypatch.setattr(storage, "_snapshot_cache_condition", threading.Condition(threading.RLock()))
    for name, value in {
        "_snapshot_cache_dir": None, "_snapshot_cache": OrderedDict(),
        "_snapshot_cache_leases": {}, "_snapshot_receipts": {},
        "_snapshot_invalid": set(), "_snapshot_cache_bytes": 0,
        "_snapshot_allocations": {}, "_snapshot_storage_locks": {},
        "_snapshot_sources": {}, "_snapshot_inflight": set(),
    }.items():
        monkeypatch.setattr(storage, name, value)
    try:
        yield results
    finally:
        assert not storage._snapshot_cache_leases, "verified snapshot readers leaked"
        assert not storage._snapshot_inflight, "verified snapshot imports leaked"
        with storage._snapshot_cache_condition:
            for digest in tuple(storage._snapshot_cache):
                storage._snapshot_invalid.discard(digest)
                storage._discard_cached_snapshot_locked(digest)
        assert not storage._snapshot_allocations
        assert not storage._snapshot_storage_locks
