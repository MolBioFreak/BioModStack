"""Drop retired RFD3/Shape tables and import+drop legacy MolBio core tables (R18)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sqlite3
import tempfile
from pathlib import Path

from paths import get_db_path

_RETIRED_AND_LEGACY_TABLES: tuple[str, ...] = (
    "rfd3_local_redesign_artifacts",
    "rfd3_local_redesign_candidates",
    "rfd3_local_redesign_requests",
    "shape_design_requests",
    "shape_design_geometries",
    "shape_cad_sources",
    "primers",
    "nucleotide_sequences",
)


def _resolve_molbio_destination(core_db_path: Path) -> Path:
    explicit = (os.environ.get("BMS_MOLBIO_DB_PATH") or os.environ.get("BIOMODSTACK_MOLBIO_DB_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    default_core = Path(get_db_path()).expanduser().resolve()
    if core_db_path == default_core:
        from molbio_database import get_molbio_path

        return Path(get_molbio_path()).expanduser().resolve()
    return core_db_path.parent / "molbio.db"


def _has_legacy_molbio_rows(conn: sqlite3.Connection) -> bool:
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    for table_name in ("nucleotide_sequences", "primers"):
        if table_name in tables:
            count = int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
            if count > 0:
                return True
    return False


def _run_coro_sync(coro) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(asyncio.run, coro).result()


def migrate_drop_retired_and_legacy_molbio_tables(db_path: str | None = None) -> None:
    """Import any legacy core nucleotide_sequences/primers into molbio.db, then drop retired tables."""
    resolved_core = Path(db_path or str(get_db_path())).expanduser().resolve()
    conn = sqlite3.connect(str(resolved_core), timeout=30)
    try:
        needs_import = _has_legacy_molbio_rows(conn)
        if needs_import:
            from molbio_migrations import extract_legacy_molbio_data, legacy_molbio_manifest_sha256

            destination = _resolve_molbio_destination(resolved_core)
            manifest_sha256 = legacy_molbio_manifest_sha256(resolved_core)
            with tempfile.TemporaryDirectory(prefix="bms-r18-molbio-") as tmp_dir:
                backup_path = Path(tmp_dir) / "core-legacy-molbio-backup.db"
                _run_coro_sync(
                    extract_legacy_molbio_data(
                        resolved_core,
                        destination,
                        backup_path=backup_path,
                        expected_manifest_sha256=manifest_sha256,
                        allow_existing_destination_rows=True,
                        source_connection=conn,
                    )
                )
    finally:
        conn.close()

    conn = sqlite3.connect(str(resolved_core), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for table_name in _RETIRED_AND_LEGACY_TABLES:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_violations:
            raise RuntimeError(f"R18 core table drop left foreign key violations: {fk_violations[:5]}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
