from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import database as database_models

from database import (
    Base,
    InputFile,
    Job,
    OntInstrumentRun,
    OntInstrumentRunEvent,
    OntExternalMoveBamRegistrationReceipt,
    OntMoveTableSource,
    OntRawSignalRepresentation,
    OntSignalCalibrationArtifact,
    OntSignalCalibrationJob,
    OntSignalMappingArtifact,
    OntSignalMappingEvent,
    OntSignalMappingJob,
    OntSignalMappingProfile,
    OntSignalViewerSession,
    OntSquigualiserViewJob,
)
from migrations.add_ont_signal_workbench import migrate
from migrations.add_ont_external_move_bam_receipts import migrate as migrate_external_move_bam
from migrations.add_ont_move_source_attempt_lineage import migrate as migrate_move_source_lineage
from migrations.seal_ont_move_source_terminal_immutability import migrate as migrate_terminal_move_source_immutability
from migrations.seal_ont_external_move_bam_receipt_binding import migrate as migrate_external_receipt_binding
from migrations.seal_ont_raw_signal_lookup_terminal_immutability import (
    migrate as migrate_lookup_terminal_immutability,
)
from migrations import runner as migration_runner
from molbio_ngs_models import (
    MolBioNGSDomainState,
    MolBioNGSDomainStateRevision,
    MolBioNGSGlobalBinding,
)
from migrations.runner import MIGRATIONS
from routers import ont_signal_workbench as router
from services import ont_signal_workbench as service
from services import ont_signal_worker as worker_service
from services.ont_signal_worker import OntSignalWorker, RetainedParentSet


MODEL_ID = "dna_r10.4.1_e8.2_400bps_sup@v4.3.0"
READ_IDS = ["read-1", "read-2"]
READ_INVENTORY_SHA256 = hashlib.sha256("read-1\nread-2\n".encode()).hexdigest()
OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@pytest.mark.asyncio
async def test_domain_revision_authority_returns_current_binding_and_digests() -> None:
    domain_id = "domain-1"
    state_revision = SimpleNamespace(
        id="state-rev-2",
        global_domain_experiment_id=domain_id,
        binding_revision_id="binding-rev-2",
        payload_sha256="a" * 64,
        membership_graph_sha256="b" * 64,
        global_domain_experiment_revision_id="global-rev-2",
    )
    domain_state = SimpleNamespace(
        global_domain_experiment_id=domain_id,
        current_state_revision_id=state_revision.id,
        current_binding_revision_id=state_revision.binding_revision_id,
        head_generation=7,
    )
    binding = SimpleNamespace(
        binding_revision_id="binding-rev-2",
        global_domain_experiment_id=domain_id,
        global_domain_experiment_revision_id="global-rev-2",
        global_domain_experiment_revision_digest="c" * 64,
    )

    class DomainSession:
        async def get(self, model: Any, identifier: str) -> Any:
            values = {
                MolBioNGSDomainState: domain_state,
                MolBioNGSDomainStateRevision: state_revision,
                MolBioNGSGlobalBinding: binding,
            }
            if model is MolBioNGSDomainState:
                return values[model] if identifier == domain_id else None
            if model is MolBioNGSDomainStateRevision:
                return values[model] if identifier == state_revision.id else None
            return values[model] if identifier == binding.binding_revision_id else None

    authority = await service._resolve_domain_revision_authority(DomainSession(), domain_id)
    assert authority == {
        "schema": "bms.molbio.domain-revision-authority.v1",
        "global_domain_experiment_id": domain_id,
        "state_revision_id": "state-rev-2",
        "state_revision_sha256": "a" * 64,
        "membership_graph_sha256": "b" * 64,
        "binding_revision_id": "binding-rev-2",
        "binding_revision_digest": "c" * 64,
        "head_generation": 7,
    }


def _viewer_create_states() -> dict[str, Any]:
    return {
        "igv_state": {
            "alignment_display_mode": "FULL",
            "alignment_color_by": "strand",
            "alignment_group_by": "none",
            "reads_track_loaded": True,
        },
        "signal_state": {
            "mode": "read",
            "render_params": {},
            "view_job_id": None,
            "read_mapping_job_id": None,
            "reference_mapping_job_id": None,
        },
    }


@dataclass(frozen=True)
class WorkbenchStore:
    factory: async_sessionmaker[AsyncSession]
    root: Path
    filtered_bam: Path
    inventory: Path


def _bootstrap_migration_parents(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE ont_instrument_runs (id VARCHAR(80) PRIMARY KEY NOT NULL);
            CREATE TABLE ont_raw_signal_representations (id VARCHAR(96) PRIMARY KEY NOT NULL);
            CREATE TABLE input_files (id VARCHAR(36) PRIMARY KEY NOT NULL);
            CREATE TABLE jobs (id VARCHAR(36) PRIMARY KEY NOT NULL);
            """
        )


def _materialize_exact_ont_migration_chain(db_path: Path) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE IF EXISTS ont_external_move_bam_registration_receipts")
        connection.execute("DROP TABLE IF EXISTS ont_move_table_sources")
        connection.commit()
    migrate(str(db_path))
    migrate_external_move_bam(str(db_path))
    migrate_move_source_lineage(str(db_path))
    migrate_terminal_move_source_immutability(str(db_path))
    migrate_lookup_terminal_immutability(str(db_path))


def test_migration_registers_closed_tables_checks_foreign_keys_and_is_idempotent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "migration.db"
    _bootstrap_migration_parents(db_path)

    migrate(str(db_path))
    migrate(str(db_path))

    expected_tables = {
        "ont_move_table_sources",
        "ont_signal_calibration_artifacts",
        "ont_signal_calibration_jobs",
        "ont_signal_mapping_profiles",
        "ont_signal_mapping_jobs",
        "ont_signal_mapping_events",
        "ont_signal_mapping_artifacts",
        "ont_squigualiser_view_jobs",
        "ont_signal_viewer_sessions",
    }
    with sqlite3.connect(db_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert expected_tables <= tables
        assert len(expected_tables & tables) == 9

        calibration_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ont_signal_calibration_jobs'"
        ).fetchone()[0]
        profile_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ont_signal_mapping_profiles'"
        ).fetchone()[0]
        mapping_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ont_signal_mapping_jobs'"
        ).fetchone()[0]
        assert "sample_count >= 1 AND sample_count <= 100" in calibration_sql
        assert "state IN ('requested','running','ready','failed','cancelled')" in calibration_sql
        assert "primary_alignment_policy = 'primary_only'" in profile_sql
        assert "minimum_mapq = 0" in profile_sql
        assert "include_supplementary = 0" in profile_sql
        assert "read_set_selection = 'immutable_full_set'" in profile_sql
        assert "parameter_source = 'approved_calibration'" in profile_sql
        assert "calibration_artifact_id VARCHAR(96) NOT NULL" in profile_sql
        assert "mode IN ('signal_to_read','signal_to_reference')" in mapping_sql
        assert "request_fingerprint VARCHAR(64) NOT NULL UNIQUE" in mapping_sql

        calibration_fks = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list('ont_signal_calibration_jobs')"
            )
        }
        assert {
            ("run_id", "ont_instrument_runs", "id", "RESTRICT"),
            ("raw_representation_id", "ont_raw_signal_representations", "id", "RESTRICT"),
            ("move_source_id", "ont_move_table_sources", "id", "RESTRICT"),
            ("calibration_artifact_id", "ont_signal_calibration_artifacts", "id", "RESTRICT"),
        } <= calibration_fks

        profile_fks = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list('ont_signal_mapping_profiles')"
            )
        }
        assert (
            "calibration_artifact_id",
            "ont_signal_calibration_artifacts",
            "id",
            "RESTRICT",
        ) in profile_fks

        mapping_fks = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute(
                "PRAGMA foreign_key_list('ont_signal_mapping_jobs')"
            )
        }
        assert {
            ("mapping_profile_id", "ont_signal_mapping_profiles", "id", "RESTRICT"),
            ("parent_mapping_job_id", "ont_signal_mapping_jobs", "id", "RESTRICT"),
        } <= mapping_fks

        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert {
            "trg_ont_signal_calibration_artifact_no_update",
            "trg_ont_signal_calibration_artifact_no_delete",
            "trg_ont_signal_calibration_job_identity_no_update",
            "trg_ont_signal_mapping_profiles_no_update",
            "trg_ont_signal_mapping_profiles_no_delete",
            "trg_ont_signal_mapping_jobs_identity_no_update",
            "trg_ont_signal_mapping_jobs_terminal_no_update",
            "trg_ont_signal_mapping_jobs_receipts_append_only",
            "trg_ont_signal_mapping_jobs_no_delete",
            "trg_ont_signal_mapping_artifacts_no_update",
            "trg_ont_squigualiser_views_identity_no_update",
            "trg_ont_squigualiser_views_terminal_no_update",
            "trg_ont_squigualiser_views_no_delete",
        } <= triggers

    registration = [item for item in MIGRATIONS if item.name == "add_ont_signal_workbench"]
    assert [(item.version, item.fn) for item in registration] == [(32, migrate)]


def test_external_move_bam_receipt_migration_is_registered_and_immutable(tmp_path: Path) -> None:
    from migrations.add_ont_external_move_bam_receipts import migrate as migrate_external_move_bam

    db_path = tmp_path / "external-move-receipts.db"
    _bootstrap_migration_parents(db_path)
    migrate(str(db_path))
    migrate_external_move_bam(str(db_path))
    migrate_external_move_bam(str(db_path))

    with sqlite3.connect(db_path) as connection:
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='ont_external_move_bam_registration_receipts'"
        ).fetchone()[0]
        assert "server_relative_path TEXT NOT NULL" in table_sql
        assert "artifact_sha256 VARCHAR(64) NOT NULL" in table_sql
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert {
            "trg_ont_external_move_bam_receipt_no_update",
            "trg_ont_external_move_bam_receipt_no_delete",
        } <= triggers
        connection.execute("INSERT INTO ont_instrument_runs(id) VALUES ('run-external')")
        connection.execute("INSERT INTO ont_raw_signal_representations(id) VALUES ('raw-external')")
        connection.execute(
            """
            INSERT INTO ont_external_move_bam_registration_receipts(
                id, candidate_id, run_id, observed_generation, raw_representation_id,
                server_relative_path, root_device, root_inode, file_device, file_inode,
                file_mtime_ns, file_ctime_ns, artifact_sha256, artifact_size_bytes,
                molecule_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "receipt-1", "a" * 64, "run-external", 1, "raw-external", "nested/moves.bam",
                1, 2, 3, 4, 5, 6, "b" * 64, 7, "dna", "2026-08-21T00:00:00",
            ),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE ont_external_move_bam_registration_receipts SET artifact_size_bytes = 8 WHERE id = 'receipt-1'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="retained authority"):
            connection.execute(
                "DELETE FROM ont_external_move_bam_registration_receipts WHERE id = 'receipt-1'"
            )
        connection.rollback()

    registration = [item for item in MIGRATIONS if item.name == "add_ont_external_move_bam_receipts"]
    assert [(item.version, item.fn) for item in registration] == [(33, migrate_external_move_bam)]


def test_move_source_attempt_lineage_migration_preserves_failed_external_row_exactly(
    tmp_path: Path,
) -> None:
    from migrations.add_ont_external_move_bam_receipts import migrate as migrate_external_move_bam

    db_path = tmp_path / "move-source-attempt-lineage.db"
    _bootstrap_migration_parents(db_path)
    migrate(str(db_path))
    migrate_external_move_bam(str(db_path))
    validation_receipt = json.dumps(
        {
            "external_registration_receipt_id": "receipt-failed",
            "raw_manifest_sha256": "d" * 64,
            "retry": {"failures": [{"code": "SourceRepairRequired"}]},
        },
        separators=(",", ":"),
    )
    source_runtime_identity = json.dumps(
        {
            "schema": "bms.ont-move-source-producer-runtime.v1",
            "authority_state": "legacy_unknown",
            "source_job_id": None,
            "source_bam_sha256": "b" * 64,
            "reason_code": "producer_runtime_provenance_unavailable",
            "requires_independent_move_validation": True,
        },
        separators=(",", ":"),
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO ont_instrument_runs(id) VALUES ('run-failed')")
        connection.execute("INSERT INTO ont_raw_signal_representations(id) VALUES ('raw-failed')")
        connection.execute("INSERT INTO input_files(id) VALUES ('input-failed')")
        connection.execute(
            """
            INSERT INTO ont_external_move_bam_registration_receipts(
                id, candidate_id, run_id, observed_generation, raw_representation_id,
                server_relative_path, root_device, root_inode, file_device, file_inode,
                file_mtime_ns, file_ctime_ns, artifact_sha256, artifact_size_bytes,
                molecule_type, created_at
            ) VALUES ('receipt-failed', ?, 'run-failed', 7, 'raw-failed',
                      'retained/moves.bam', 11, 12, 13, 14, 15, 16, ?, 17,
                      'dna', '2026-08-21T01:02:03.000004')
            """,
            ("a" * 64, "b" * 64),
        )
        connection.execute(
            """
            INSERT INTO ont_move_table_sources(
                id, run_id, observed_generation, raw_representation_id, input_file_id,
                source_job_id, external_registration_receipt_id, artifact_sha256,
                artifact_size_bytes, bam_header_sha256, record_count, unique_read_count,
                mv_tag_count, ts_tag_count, ns_tag_count, basecall_model_id, molecule_type,
                source_runtime_identity, read_inventory_sha256, validation_state,
                reason_code, validation_receipt, claim_token, lease_expires_at,
                created_at, validated_at
            ) VALUES (
                'source-failed', 'run-failed', 7, 'raw-failed', 'input-failed',
                NULL, 'receipt-failed', ?, 17, ?, 101, 99, 98, 97, 96,
                'model-exact', 'dna', ?, ?, 'failed', 'SourceRepairRequired', ?,
                NULL, NULL, '2026-08-21T01:02:03.000004', '2026-08-21T01:03:04.000005'
            )
            """,
            (
                "b" * 64,
                "c" * 64,
                source_runtime_identity,
                "e" * 64,
                validation_receipt,
            ),
        )
        connection.commit()
        columns_before = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info('ont_move_table_sources')")
        ]
        row_before = connection.execute(
            "SELECT * FROM ont_move_table_sources WHERE id='source-failed'"
        ).fetchone()

    migration = next(
        (
            item
            for item in MIGRATIONS
            if item.name == "add_ont_move_source_attempt_lineage"
        ),
        None,
    )
    assert migration is not None, "move-source attempt-lineage migration is not registered"
    assert migration.version == 34
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                content_sha256 TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations VALUES (?, ?, 'legacy', ?)",
            [
                (
                    item.version,
                    item.name,
                    migration_runner._migration_content_sha256(item)
                    if item.version == 33
                    else None,
                )
                for item in MIGRATIONS
                if item.version < 34
            ],
        )
        connection.commit()
    migration_runner.run_all(str(db_path))
    migration.fn(str(db_path))

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT name, content_sha256 FROM schema_migrations WHERE version=34"
        ).fetchone() == (
            "add_ont_move_source_attempt_lineage",
            migration_runner._migration_content_sha256(migration),
        )
        connection.execute("PRAGMA foreign_keys=ON")
        columns_after = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info('ont_move_table_sources')")
        ]
        row_after = connection.execute(
            "SELECT * FROM ont_move_table_sources WHERE id='source-failed'"
        ).fetchone()
        preserved = dict(zip(columns_after, row_after, strict=True))
        assert columns_after == columns_before + [
            "attempt_number",
            "predecessor_move_source_id",
        ]
        assert tuple(preserved[column] for column in columns_before) == row_before
        assert preserved["attempt_number"] == 1
        assert preserved["predecessor_move_source_id"] is None
        assert preserved["validation_state"] == "failed"
        assert preserved["reason_code"] == "SourceRepairRequired"
        assert preserved["validation_receipt"] == validation_receipt
        assert preserved["external_registration_receipt_id"] == "receipt-failed"
        source_fks = {
            (str(row[3]), str(row[2]), str(row[4]), str(row[6]))
            for row in connection.execute(
                "PRAGMA foreign_key_list('ont_move_table_sources')"
            )
        }
        assert (
            "predecessor_move_source_id",
            "ont_move_table_sources",
            "id",
            "RESTRICT",
        ) in source_fks
        for child_table in (
            "ont_signal_calibration_artifacts",
            "ont_signal_calibration_jobs",
            "ont_signal_mapping_jobs",
            "ont_signal_viewer_sessions",
        ):
            assert any(
                str(row[2]) == "ont_move_table_sources"
                for row in connection.execute(f"PRAGMA foreign_key_list('{child_table}')")
            )
        index_columns = {
            str(index[1]): tuple(
                str(column[2])
                for column in connection.execute(
                    f"PRAGMA index_info('{str(index[1])}')"
                )
            )
            for index in connection.execute("PRAGMA index_list('ont_move_table_sources')")
        }
        assert (
            "run_id",
            "observed_generation",
            "artifact_sha256",
            "attempt_number",
        ) in index_columns.values()
        assert ("predecessor_move_source_id",) in index_columns.values()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _assert_v33_startup_authority(db_path: Path) -> None:
    expected_checksum = migration_runner._migration_content_sha256(
        next(migration for migration in MIGRATIONS if migration.version == 33)
    )
    with sqlite3.connect(db_path) as connection:
        migration_runner.register_sqlite_sha256(connection)
        assert connection.execute(
            "SELECT name, content_sha256 FROM schema_migrations WHERE version=33"
        ).fetchone() == ("add_ont_external_move_bam_receipts", expected_checksum)
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert {
            "trg_ont_external_move_bam_receipt_no_update",
            "trg_ont_external_move_bam_receipt_no_delete",
            "trg_ont_move_source_exact_producer_insert",
            "trg_ont_move_source_exact_producer_update",
            "trg_ont_move_source_external_receipt_insert",
            "trg_ont_move_source_external_receipt_update",
        } <= triggers
        invalid_source = """
            INSERT INTO ont_move_table_sources(
                id, run_id, observed_generation, raw_representation_id, input_file_id,
                source_job_id, external_registration_receipt_id, artifact_sha256,
                artifact_size_bytes, molecule_type, source_runtime_identity,
                validation_state, reason_code, validation_receipt, created_at
            ) VALUES (?, 'startup-run', 1, 'startup-raw', 'startup-input',
                      NULL, ?, ?, 7, 'dna', '{}', 'requested',
                      'move_source_validation_requested', '{}', '2026-08-21T00:00:00')
        """
        with pytest.raises(sqlite3.IntegrityError, match="exactly one producer authority"):
            connection.execute(invalid_source, ("startup-xor", None, "1" * 64))
        with pytest.raises(sqlite3.IntegrityError, match="external receipt.*"):
            connection.execute(
                invalid_source,
                ("startup-dangling", "missing-receipt", "2" * 64),
            )


def _assert_v34_startup_authority(db_path: Path) -> None:
    from migrations.add_ont_move_source_attempt_lineage import attest

    migration = next(item for item in MIGRATIONS if item.version == 34)
    expected_checksum = migration_runner._migration_content_sha256(migration)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute(
            "SELECT name, content_sha256 FROM schema_migrations WHERE version=34"
        ).fetchone() == ("add_ont_move_source_attempt_lineage", expected_checksum)
        attest(connection)


@pytest.mark.asyncio
async def test_fresh_database_startup_applies_and_attests_migration_33_before_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "fresh-startup.db"
    startup_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(database_models, "engine", startup_engine)
    try:
        with pytest.raises(RuntimeError, match="migration 33 authority is absent"):
            await database_models.init_db()
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ont_move_table_sources'"
            ).fetchone() is None
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone() is None
    finally:
        await startup_engine.dispose()


@pytest.mark.asyncio
async def test_upgraded_database_startup_applies_and_attests_migrations_33_and_34_before_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "upgraded-startup.db"
    startup_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with startup_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                applied_at TEXT NOT NULL, content_sha256 TEXT
            )"""
        )
        connection.executemany(
            "INSERT INTO schema_migrations VALUES (?, ?, 'legacy', NULL)",
            [(migration.version, migration.name) for migration in MIGRATIONS],
        )
        connection.execute(
            "UPDATE schema_migrations SET content_sha256=? WHERE version=33",
            (
                migration_runner._migration_content_sha256(
                    next(item for item in MIGRATIONS if item.version == 33)
                ),
            ),
        )
        connection.execute(
            "UPDATE schema_migrations SET content_sha256=? WHERE version=34",
            (migration_runner._migration_content_sha256(next(item for item in MIGRATIONS if item.version == 34)),),
        )
        for version in (35, 36):
            connection.execute(
                "UPDATE schema_migrations SET content_sha256=? WHERE version=?",
                (
                    migration_runner._migration_content_sha256(
                        next(item for item in MIGRATIONS if item.version == version)
                    ),
                    version,
                ),
            )
        connection.commit()
    _materialize_exact_ont_migration_chain(db_path)
    next(item for item in MIGRATIONS if item.version == 34).fn(str(db_path))
    migrate_terminal_move_source_immutability(str(db_path))
    migrate_external_receipt_binding(str(db_path))
    migrate_lookup_terminal_immutability(str(db_path))
    with sqlite3.connect(db_path) as connection:
        for version in (37, 38, 39, 40):
            connection.execute(
                "UPDATE schema_migrations SET content_sha256=? WHERE version=?",
                (
                    migration_runner._migration_content_sha256(
                        next(item for item in MIGRATIONS if item.version == version)
                    ),
                    version,
                ),
            )
        connection.commit()
    monkeypatch.setattr(database_models, "engine", startup_engine)
    try:
        await database_models.init_db()
        _assert_v33_startup_authority(db_path)
        _assert_v34_startup_authority(db_path)
    finally:
        await startup_engine.dispose()


@pytest.mark.asyncio
async def test_upgraded_database_startup_rejects_same_name_altered_migration_33_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "altered-v33-trigger.db"
    startup_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with startup_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                applied_at TEXT NOT NULL, content_sha256 TEXT
            )"""
        )
        connection.executemany(
            "INSERT INTO schema_migrations VALUES (?, ?, 'legacy', NULL)",
            [(migration.version, migration.name) for migration in MIGRATIONS],
        )
        connection.execute(
            "UPDATE schema_migrations SET content_sha256=? WHERE version=33",
            (
                migration_runner._migration_content_sha256(
                    next(item for item in MIGRATIONS if item.version == 33)
                ),
            ),
        )
        connection.execute(
            "UPDATE schema_migrations SET content_sha256=? WHERE version=34",
            (migration_runner._migration_content_sha256(next(item for item in MIGRATIONS if item.version == 34)),),
        )
        for version in (35, 36):
            connection.execute(
                "UPDATE schema_migrations SET content_sha256=? WHERE version=?",
                (
                    migration_runner._migration_content_sha256(
                        next(item for item in MIGRATIONS if item.version == version)
                    ),
                    version,
                ),
            )
        connection.commit()
    _materialize_exact_ont_migration_chain(db_path)
    next(item for item in MIGRATIONS if item.version == 34).fn(str(db_path))
    migrate_external_receipt_binding(str(db_path))
    migrate_lookup_terminal_immutability(str(db_path))
    with sqlite3.connect(db_path) as connection:
        for version in (37, 38, 39, 40):
            connection.execute(
                "UPDATE schema_migrations SET content_sha256=? WHERE version=?",
                (
                    migration_runner._migration_content_sha256(
                        next(item for item in MIGRATIONS if item.version == version)
                    ),
                    version,
                ),
            )
        connection.commit()
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TRIGGER trg_ont_move_source_exact_producer_insert")
        connection.execute(
            """
            CREATE TRIGGER trg_ont_move_source_exact_producer_insert
            BEFORE INSERT ON ont_move_table_sources
            BEGIN SELECT 1; END
            """
        )
        connection.commit()

    monkeypatch.setattr(database_models, "engine", startup_engine)
    try:
        with pytest.raises(RuntimeError, match="migration 33 startup attestation failed"):
            await database_models.init_db()
    finally:
        await startup_engine.dispose()


def test_migration_runner_preserves_legacy_unknown_content_and_seals_new_v33_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "migration-content-ledger.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, 'legacy')",
            [(migration.version, migration.name) for migration in MIGRATIONS if migration.version < 33],
        )
        connection.commit()

    module_path = tmp_path / "fake_migration_33.py"
    module_path.write_bytes(b"MIGRATION_CONTENT = 'v1'\n")

    def fake_v33(db_path: str) -> None:
        del db_path

    fake_module = SimpleNamespace(__file__=str(module_path))
    fake_migration = migration_runner.Migration(
        33,
        "add_ont_external_move_bam_receipts",
        fake_v33,
    )
    registered = [migration for migration in MIGRATIONS if migration.version < 33] + [fake_migration]
    monkeypatch.setattr(migration_runner, "MIGRATIONS", registered)
    monkeypatch.setattr(
        migration_runner,
        "getmodule",
        lambda fn: fake_module if fn is fake_v33 else None,
        raising=False,
    )

    migration_runner.run_all(str(db_path))
    expected_sha256 = hashlib.sha256(module_path.read_bytes()).hexdigest()
    with sqlite3.connect(db_path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info('schema_migrations')")
        }
        assert "content_sha256" in columns
        assert connection.execute(
            "SELECT DISTINCT content_sha256 FROM schema_migrations WHERE version < 33"
        ).fetchall() == [(None,)]
        assert connection.execute(
            "SELECT content_sha256 FROM schema_migrations WHERE version = 33"
        ).fetchone() == (expected_sha256,)

    module_path.write_bytes(b"MIGRATION_CONTENT = 'changed-after-application'\n")
    with pytest.raises(RuntimeError, match="migration content changed.*version 33"):
        migration_runner.run_all(str(db_path))


def test_external_move_bam_migration_enforces_exact_producer_authority_on_v32_data(
    tmp_path: Path,
) -> None:
    from migrations.add_ont_external_move_bam_receipts import migrate as migrate_external_move_bam

    db_path = tmp_path / "external-move-producer-authority.db"
    _bootstrap_migration_parents(db_path)
    migrate(str(db_path))

    insert_source_sql = """
        INSERT INTO ont_move_table_sources(
            id, run_id, observed_generation, raw_representation_id, input_file_id,
            source_job_id, external_registration_receipt_id, artifact_sha256,
            artifact_size_bytes, molecule_type, source_runtime_identity,
            validation_state, reason_code, validation_receipt, created_at
        ) VALUES (?, 'run-authority', 1, 'raw-authority', ?, ?, ?, ?, 7, 'dna',
                  '{}', 'requested', 'move_source_validation_requested', '{}', '2026-08-21T00:00:00')
    """
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO ont_instrument_runs(id) VALUES ('run-authority')")
        connection.execute("INSERT INTO ont_raw_signal_representations(id) VALUES ('raw-authority')")
        connection.execute("INSERT INTO jobs(id) VALUES ('job-authority')")
        for input_id in (
            "input-managed", "input-external", "input-missing", "input-dual", "input-dangling",
        ):
            connection.execute("INSERT INTO input_files(id) VALUES (?)", (input_id,))
        connection.execute(
            insert_source_sql,
            ("source-managed", "input-managed", "job-authority", None, "1" * 64),
        )
        connection.commit()

    migrate_external_move_bam(str(db_path))

    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute(
            "SELECT source_job_id, external_registration_receipt_id FROM ont_move_table_sources WHERE id='source-managed'"
        ).fetchone() == ("job-authority", None)
        connection.execute(
            """
            INSERT INTO ont_external_move_bam_registration_receipts(
                id, candidate_id, run_id, observed_generation, raw_representation_id,
                server_relative_path, root_device, root_inode, file_device, file_inode,
                file_mtime_ns, file_ctime_ns, artifact_sha256, artifact_size_bytes,
                molecule_type, created_at
            ) VALUES ('receipt-authority', ?, 'run-authority', 1, 'raw-authority',
                      'nested/moves.bam', 1, 2, 3, 4, 5, 6, ?, 7, 'dna', '2026-08-21T00:00:00')
            """,
            ("a" * 64, "2" * 64),
        )
        connection.execute(
            insert_source_sql,
            ("source-external", "input-external", None, "receipt-authority", "2" * 64),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="exactly one producer authority"):
            connection.execute(
                insert_source_sql,
                ("source-missing", "input-missing", None, None, "3" * 64),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="exactly one producer authority"):
            connection.execute(
                insert_source_sql,
                ("source-dual", "input-dual", "job-authority", "receipt-authority", "4" * 64),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="external receipt authority does not exist"):
            connection.execute(
                insert_source_sql,
                ("source-dangling", "input-dangling", None, "receipt-missing", "5" * 64),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError, match="exactly one producer authority"):
            connection.execute(
                """
                UPDATE ont_move_table_sources
                SET source_job_id = NULL, external_registration_receipt_id = NULL
                WHERE id = 'source-managed'
                """
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="exactly one producer authority"):
            connection.execute(
                """
                UPDATE ont_move_table_sources
                SET external_registration_receipt_id = 'receipt-authority'
                WHERE id = 'source-managed'
                """
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="external receipt authority does not exist"):
            connection.execute(
                """
                UPDATE ont_move_table_sources
                SET external_registration_receipt_id = 'receipt-missing'
                WHERE id = 'source-external'
                """
            )
        connection.rollback()


def test_external_move_bam_migration_rejects_invalid_v32_rows_without_partial_ddl(
    tmp_path: Path,
) -> None:
    from migrations.add_ont_external_move_bam_receipts import migrate as migrate_external_move_bam

    db_path = tmp_path / "invalid-v32-producer-authority.db"
    _bootstrap_migration_parents(db_path)
    migrate(str(db_path))
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT INTO ont_instrument_runs(id) VALUES ('run-invalid')")
        connection.execute("INSERT INTO ont_raw_signal_representations(id) VALUES ('raw-invalid')")
        connection.execute("INSERT INTO input_files(id) VALUES ('input-invalid')")
        connection.execute(
            """
            INSERT INTO ont_move_table_sources(
                id, run_id, observed_generation, raw_representation_id, input_file_id,
                source_job_id, external_registration_receipt_id, artifact_sha256,
                artifact_size_bytes, molecule_type, source_runtime_identity,
                validation_state, reason_code, validation_receipt, created_at
            ) VALUES (
                'source-invalid', 'run-invalid', 1, 'raw-invalid', 'input-invalid',
                NULL, NULL, ?, 7, 'dna', '{}', 'requested',
                'move_source_validation_requested', '{}', '2026-08-21T00:00:00'
            )
            """,
            ("f" * 64,),
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="exactly one producer authority"):
        migrate_external_move_bam(str(db_path))

    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ont_external_move_bam_registration_receipts'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_ont_move_source_%producer%'"
        ).fetchall() == []


async def _seed_ready_authority(
    session: AsyncSession,
    root: Path,
    *,
    source_id: str = "ont-moves-ready",
) -> tuple[Path, Path]:
    now = datetime.utcnow()
    blow5 = root / "reads.blow5"
    blow5.write_bytes(b"governed-blow5")
    Path(f"{blow5}.idx").write_bytes(b"governed-index")
    move_root = root / "seed-move-source"
    move_root.mkdir()
    move_bam = move_root / "moves.bam"
    move_bam.write_bytes(b"governed-move-bam")
    filtered_bam = root / "filtered-moves.bam"
    filtered_bam.write_bytes(b"governed-filtered-move-bam")
    inventory = root / "read-inventory.txt"
    inventory.write_text("read-1\nread-2\n", encoding="utf-8")
    raw_artifact_manifest = {
        "artifacts": [
            {
                "kind": "blow5",
                "path": str(blow5),
                "sha256": hashlib.sha256(blow5.read_bytes()).hexdigest(),
                "bytes": blow5.stat().st_size,
            },
            {
                "kind": "blow5_index",
                "path": f"{blow5}.idx",
                "sha256": hashlib.sha256(Path(f"{blow5}.idx").read_bytes()).hexdigest(),
                "bytes": Path(f"{blow5}.idx").stat().st_size,
            },
        ]
    }
    raw_manifest_sha256 = hashlib.sha256(
        json.dumps(
            raw_artifact_manifest,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()

    session.add_all(
        [
            OntInstrumentRun(
                id="run-1",
                position_id="position-1",
                minknow_run_id="minknow-run-1",
                state="completed",
                observed_at=now,
                observed_generation=1,
                output_directories={"reads": str(root)},
                output_files={},
            ),
            InputFile(
                id="move-input-1",
                filename=move_bam.name,
                file_type="bam",
                directory=str(move_root),
                size_bytes=move_bam.stat().st_size,
            ),
            Job(
                id="seed-move-source-job",
                name="seed move source",
                status="completed",
                model_id="dorado",
                mode="basecall",
                params={},
                output_dir=str(move_root),
            ),
        ]
    )
    await session.flush()
    session.add_all(
        [
            OntInstrumentRunEvent(
                id="run-event-1",
                run_id="run-1",
                event_type="completed",
                state="completed",
                observed_at=now,
                observed_generation=1,
                output_files={},
            ),
            OntRawSignalRepresentation(
                id="raw-blow5-1",
                run_id="run-1",
                observed_generation=1,
                role="derived",
                source_kind="pod5_conversion",
                format="blow5",
                source_fidelity="lossless_signal",
                state="ready",
                reason_code="validated_indexed_blow5_ready",
                artifact_manifest=raw_artifact_manifest,
                manifest_sha256=raw_manifest_sha256,
                parent_representation_ids=[],
                parent_manifest_sha256s=[],
                compression={},
                runtime_identity={"tool": "slow5tools"},
                validation_receipts={"adjacent_index": True},
                read_count=2,
                published_at=now,
                retention_pinned_at=now,
            ),
        ]
    )
    await session.flush()
    session.add(
        OntMoveTableSource(
            id=source_id,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            input_file_id="move-input-1",
            source_job_id="seed-move-source-job",
            external_registration_receipt_id=None,
            artifact_sha256=hashlib.sha256(move_bam.read_bytes()).hexdigest(),
            artifact_size_bytes=move_bam.stat().st_size,
            bam_header_sha256="b" * 64,
            record_count=2,
            unique_read_count=2,
            mv_tag_count=2,
            ts_tag_count=2,
            ns_tag_count=2,
            basecall_model_id=MODEL_ID,
            molecule_type="dna",
            source_runtime_identity={"tool": "dorado"},
            read_inventory_sha256=READ_INVENTORY_SHA256,
            validation_state="ready",
            reason_code="move_source_exact_read_set_ready",
            validation_receipt={
                "managed_outputs": {
                    "filtered_move_bam": str(filtered_bam),
                    "read_inventory": str(inventory),
                },
                "managed_output_sha256s": {
                    "filtered_move_bam_sha256": hashlib.sha256(filtered_bam.read_bytes()).hexdigest(),
                    "filtered_move_bam_size_bytes": filtered_bam.stat().st_size,
                    "read_inventory_sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
                    "read_inventory_size_bytes": inventory.stat().st_size,
                },
            },
            created_at=now,
            validated_at=now,
        )
    )
    await session.commit()
    return filtered_bam, inventory


@pytest_asyncio.fixture
async def workbench_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(worker_service, "get_allowed_roots", lambda: {"test": tmp_path})
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'workbench.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        filtered_bam, inventory = await _seed_ready_authority(session, tmp_path)
    try:
        yield WorkbenchStore(
            factory=factory,
            root=tmp_path,
            filtered_bam=filtered_bam,
            inventory=inventory,
        )
    finally:
        await engine.dispose()


def test_retained_parent_pin_rejects_intermediate_symlinks_beneath_governed_roots(
    tmp_path: Path,
) -> None:
    governed = tmp_path / "governed"
    outside = tmp_path / "outside"
    governed.mkdir()
    outside.mkdir()
    regular = governed / "regular" / "parent.bin"
    regular.parent.mkdir()
    regular.write_bytes(b"regular-parent")
    escaped = outside / "escaped.bin"
    escaped.write_bytes(b"escaped-parent")
    (governed / "linked-parent").symlink_to(outside, target_is_directory=True)
    with RetainedParentSet((governed,)) as parents:
        retained = parents.pin(
            regular,
            alias="regular.bin",
            expected_sha256=hashlib.sha256(regular.read_bytes()).hexdigest(),
            expected_size=regular.stat().st_size,
        )
        assert retained.sha256 == hashlib.sha256(b"regular-parent").hexdigest()

    with RetainedParentSet((governed,)) as parents:
        with pytest.raises(RuntimeError, match="governed root|symbolic links"):
            parents.pin(
                governed / "linked-parent" / escaped.name,
                alias="escaped.bin",
                expected_sha256=hashlib.sha256(escaped.read_bytes()).hexdigest(),
                expected_size=escaped.stat().st_size,
            )


def _configure_external_move_bam_candidate_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    key_file = tmp_path / "external-move-bam-candidate.key"
    key_file.write_bytes(os.urandom(32))
    key_file.chmod(0o600)
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE_ENV, str(key_file))
    return key_file


def test_external_move_bam_candidate_ids_are_keyed_and_key_specific(tmp_path: Path) -> None:
    root = tmp_path / "candidate-id-root"
    root.mkdir()
    bam = root / "moves.bam"
    bam.write_bytes(b"candidate-metadata-only")
    root_info = root.stat()
    file_info = bam.stat()
    first_key = os.urandom(32)
    second_key = os.urandom(32)

    first_id = service._external_move_bam_candidate_id(
        bam.name, root_info, file_info, first_key
    )
    second_id = service._external_move_bam_candidate_id(
        bam.name, root_info, file_info, second_key
    )
    unkeyed_id = hashlib.sha256(
        service._canonical(
            service._external_move_bam_candidate_body(bam.name, root_info, file_info)
        )
    ).hexdigest()

    assert re.fullmatch(r"[0-9a-f]{64}", first_id)
    assert first_id != second_id
    assert first_id != unkeyed_id


@pytest.mark.parametrize("invalid_key", ["missing", "symlink", "world-readable", "short", "long"])
def test_external_move_bam_candidate_key_file_contract_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_key: str,
) -> None:
    root = tmp_path / "invalid-key-root"
    root.mkdir()
    (root / "moves.bam").write_bytes(b"moves")
    key_file = tmp_path / "configured-candidate.key"
    if invalid_key == "missing":
        monkeypatch.delenv(service.EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE_ENV, raising=False)
    elif invalid_key == "symlink":
        target = tmp_path / "private-key-target"
        target.write_bytes(os.urandom(32))
        target.chmod(0o600)
        key_file.symlink_to(target)
        monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE_ENV, str(key_file))
    else:
        sizes = {"world-readable": 32, "short": 31, "long": 33}
        key_file.write_bytes(os.urandom(sizes[invalid_key]))
        key_file.chmod(0o644 if invalid_key == "world-readable" else 0o600)
        monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE_ENV, str(key_file))
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(root))

    with pytest.raises(
        service.OntSignalError,
        match=r"^external move-BAM source is unavailable$",
    ) as raised:
        service.list_external_move_bam_candidates()

    assert str(key_file) not in str(raised.value)
    assert str(root) not in str(raised.value)


def test_external_move_bam_candidate_key_is_read_once_per_catalog_and_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "single-key-read-root"
    root.mkdir()
    (root / "one.bam").write_bytes(b"one")
    (root / "two.bam").write_bytes(b"two")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(root))
    _configure_external_move_bam_candidate_key(tmp_path, monkeypatch)
    original_read = service._read_external_move_bam_candidate_key
    reads = 0

    def observed_read() -> bytes:
        nonlocal reads
        reads += 1
        return original_read()

    monkeypatch.setattr(service, "_read_external_move_bam_candidate_key", observed_read)
    candidates = service.list_external_move_bam_candidates()
    assert reads == 1

    reads = 0
    _sealed, retained_descriptors = service._seal_external_move_bam_candidate(
        candidates[0]["candidate_id"]
    )
    try:
        assert reads == 1
    finally:
        for descriptor in retained_descriptors:
            os.close(descriptor)


def test_external_move_bam_candidate_catalog_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "external"
    root.mkdir()
    (root / "one.bam").write_bytes(b"one")
    (root / "two.bam").write_bytes(b"two")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(root))
    _configure_external_move_bam_candidate_key(tmp_path, monkeypatch)
    monkeypatch.setattr(service, "MAX_EXTERNAL_MOVE_BAM_CANDIDATES", 1)

    with pytest.raises(service.OntSignalError, match="exceeds bounded policy"):
        service.list_external_move_bam_candidates()


def test_external_move_bam_candidate_traversal_bounds_all_visited_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "external-entry-flood"
    root.mkdir()
    for ordinal in range(4):
        (root / f"unrelated-{ordinal}").mkdir()
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(root))
    _configure_external_move_bam_candidate_key(tmp_path, monkeypatch)
    monkeypatch.setattr(
        service,
        "MAX_EXTERNAL_MOVE_BAM_VISITED_ENTRIES",
        3,
        raising=False,
    )

    with pytest.raises(service.OntSignalError, match="visited-entry policy"):
        service.list_external_move_bam_candidates()


def _api(factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[router.get_session] = override_session
    app.include_router(router.router, prefix="/api/ont/signal-workbench")
    return app


async def _seed_failed_external_move_source(
    factory: async_sessionmaker[AsyncSession],
    *,
    source_id: str = "ont-moves-external-failed",
    artifact_sha256: str = "9" * 64,
    state: str = "failed",
    legacy_retry_exhausted: bool = False,
    null_validated_at: bool = False,
) -> tuple[str, str]:
    async with factory() as session:
        representation = await session.get(OntRawSignalRepresentation, "raw-blow5-1")
        assert representation is not None
        receipt_body = {
            "candidate_id": hashlib.sha256(source_id.encode()).hexdigest(),
            "server_relative_path": f"retained/{source_id}.bam",
            "root_device": 101,
            "root_inode": 102,
            "file_device": 103,
            "file_inode": 104,
            "file_mtime_ns": 105,
            "file_ctime_ns": 106,
            "artifact_sha256": artifact_sha256,
            "artifact_size_bytes": 107,
            "run_id": "run-1",
            "observed_generation": 1,
            "raw_representation_id": "raw-blow5-1",
            "molecule_type": "dna",
        }
        receipt_id = f"ont-external-move-{service._digest(receipt_body)}"
        input_file_id = f"ont-ext-bam-{artifact_sha256[:24]}"
        session.add(
            InputFile(
                id=input_file_id,
                filename=f"external-{artifact_sha256}.bam",
                file_type="bam",
                directory="",
                size_bytes=receipt_body["artifact_size_bytes"],
            )
        )
        session.add(
            OntExternalMoveBamRegistrationReceipt(
                id=receipt_id,
                **receipt_body,
                created_at=datetime.utcnow(),
            )
        )
        validation_receipt: dict[str, Any] = {
            "raw_manifest_sha256": representation.manifest_sha256,
            "external_registration_receipt_id": receipt_id,
            "failure": {"code": "SourceRepairRequired"},
        }
        if legacy_retry_exhausted:
            validation_receipt = {
                "raw_manifest_sha256": representation.manifest_sha256,
                "external_registration_receipt_id": receipt_id,
                "retry": {
                    "max_attempts": 3,
                    "failures": [
                        {
                            "attempt": attempt,
                            "failed_at": f"2026-08-21T12:00:0{attempt}",
                            "failure_code": "RuntimeError",
                            "message_sha256": f"{attempt}" * 64,
                        }
                        for attempt in (1, 2, 3)
                    ],
                },
            }
        session.add(
            OntMoveTableSource(
                id=source_id,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id=input_file_id,
                source_job_id=None,
                external_registration_receipt_id=receipt_id,
                artifact_sha256=artifact_sha256,
                artifact_size_bytes=receipt_body["artifact_size_bytes"],
                molecule_type="dna",
                source_runtime_identity=service._external_move_bam_runtime_identity(
                    artifact_sha256
                ),
                validation_state=state,
                reason_code=(
                    "runtime_validation_failed_retry_exhausted"
                    if legacy_retry_exhausted
                    else "SourceRepairRequired"
                    if state == "failed"
                    else "move_source_validation_requested"
                ),
                validation_receipt=validation_receipt,
                claim_token=None,
                lease_expires_at=None,
                created_at=datetime.utcnow(),
                validated_at=(
                    None
                    if legacy_retry_exhausted or null_validated_at
                    else datetime.utcnow()
                    if state in {"failed", "ready"}
                    else None
                ),
                attempt_number=1,
                predecessor_move_source_id=None,
            )
        )
        await session.commit()
    return receipt_id, str(receipt_body["candidate_id"])


@pytest.mark.asyncio
async def test_fresh_external_move_source_attempt_is_atomic_and_registration_replays_original(
    workbench_store: WorkbenchStore,
) -> None:
    source_id = "ont-moves-external-failed"
    receipt_id, candidate_id = await _seed_failed_external_move_source(
        workbench_store.factory,
        source_id=source_id,
    )
    async with workbench_store.factory() as session:
        before_result = await session.execute(
            text("SELECT * FROM ont_move_table_sources WHERE id=:id"),
            {"id": source_id},
        )
        before = tuple(before_result.one())

    route = (
        "/api/ont/signal-workbench/move-sources/"
        f"{source_id}/fresh-attempt"
    )
    app = _api(workbench_store.factory)

    async def request_fresh_attempt() -> Any:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.post(route, json={})

    first, concurrent_replay = await asyncio.gather(
        request_fresh_attempt(),
        request_fresh_attempt(),
    )
    assert first.status_code == 202
    assert concurrent_replay.status_code == 202
    first_body = first.json()
    assert concurrent_replay.json() == first_body
    assert first_body["move_source_id"] != source_id
    assert OPAQUE_ID.fullmatch(first_body["move_source_id"])
    assert first_body["attempt_number"] == 2
    assert first_body["predecessor_move_source_id"] == source_id
    assert first_body["state"] == "requested"
    assert first_body["reason_code"] == "fresh_move_source_attempt_requested"
    assert first_body["external_registration_receipt_id"] == receipt_id
    assert first_body["source_job_id"] is None
    assert first_body["validation_receipt"] == {
        "schema": "bms.ont-move-source-fresh-attempt.v1",
        "predecessor_move_source_id": source_id,
        "raw_manifest_sha256": first_body["validation_receipt"][
            "raw_manifest_sha256"
        ],
        "external_registration_receipt_id": receipt_id,
    }

    async with workbench_store.factory() as session:
        after_result = await session.execute(
            text("SELECT * FROM ont_move_table_sources WHERE id=:id"),
            {"id": source_id},
        )
        assert tuple(after_result.one()) == before
        attempts = (
            await session.execute(
                select(OntMoveTableSource)
                .where(
                    OntMoveTableSource.run_id == "run-1",
                    OntMoveTableSource.artifact_sha256 == "9" * 64,
                )
                .order_by(OntMoveTableSource.attempt_number)
            )
        ).scalars().all()
        assert [row.attempt_number for row in attempts] == [1, 2]
        assert [row.predecessor_move_source_id for row in attempts] == [None, source_id]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        sequential_replay = await client.post(route, json={})
        registration_replay = await client.post(
            "/api/ont/signal-workbench/runs/run-1/generations/1/"
            "external-move-bam-candidates/register",
            json={
                "candidate_id": candidate_id,
                "raw_representation_id": "raw-blow5-1",
                "molecule_type": "dna",
            },
        )
        rejected_field = await client.post(
            route,
            json={"server_path": "/forbidden/host/path"},
        )
    assert sequential_replay.status_code == 202
    assert sequential_replay.json() == first_body
    assert registration_replay.status_code == 202
    assert registration_replay.json()["move_source_id"] == source_id
    assert registration_replay.json()["attempt_number"] == 1
    assert registration_replay.json()["predecessor_move_source_id"] is None
    assert rejected_field.status_code == 422


@pytest.mark.asyncio
async def test_fresh_external_move_source_attempt_rejects_attempt_four(
    workbench_store: WorkbenchStore,
) -> None:
    original_id = "ont-moves-external-attempt-1-failed"
    await _seed_failed_external_move_source(
        workbench_store.factory,
        source_id=original_id,
    )
    app = _api(workbench_store.factory)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        attempt_two = await client.post(
            f"/api/ont/signal-workbench/move-sources/{original_id}/fresh-attempt",
            json={},
        )
    assert attempt_two.status_code == 202
    attempt_two_id = attempt_two.json()["move_source_id"]
    async with workbench_store.factory() as session:
        await session.execute(
            text(
                "UPDATE ont_move_table_sources SET validation_state='failed', "
                "reason_code='test_failed', validated_at=:validated_at "
                "WHERE id=:id"
            ),
            {"id": attempt_two_id, "validated_at": datetime.utcnow()},
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        attempt_three = await client.post(
            f"/api/ont/signal-workbench/move-sources/{attempt_two_id}/fresh-attempt",
            json={},
        )
    assert attempt_three.status_code == 202
    predecessor_id = attempt_three.json()["move_source_id"]
    async with workbench_store.factory() as session:
        await session.execute(
            text(
                "UPDATE ont_move_table_sources SET validation_state='failed', "
                "reason_code='test_failed', validated_at=:validated_at "
                "WHERE id=:id"
            ),
            {"id": predecessor_id, "validated_at": datetime.utcnow()},
        )
        await session.commit()
        before = tuple(
            (
                await session.execute(
                    text("SELECT * FROM ont_move_table_sources WHERE id=:id"),
                    {"id": predecessor_id},
                )
            ).one()
        )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/ont/signal-workbench/move-sources/{predecessor_id}/fresh-attempt",
            json={},
        )

    assert response.status_code == 409
    async with workbench_store.factory() as session:
        after = tuple(
            (
                await session.execute(
                    text("SELECT * FROM ont_move_table_sources WHERE id=:id"),
                    {"id": predecessor_id},
                )
            ).one()
        )
        assert after == before
        assert await session.scalar(
            select(func.count())
            .select_from(OntMoveTableSource)
            .where(OntMoveTableSource.predecessor_move_source_id == predecessor_id)
        ) == 0


@pytest.mark.asyncio
async def test_fresh_attempt_accepts_only_exact_preserved_null_validated_retry_exhaustion(
    workbench_store: WorkbenchStore,
) -> None:
    predecessor_id = "ont-moves-710c42e97bcc47709da2cb62f67f3746"
    await _seed_failed_external_move_source(
        workbench_store.factory,
        source_id=predecessor_id,
        artifact_sha256="8" * 64,
        legacy_retry_exhausted=True,
    )
    arbitrary_id = "ont-moves-failed-null-arbitrary"
    await _seed_failed_external_move_source(
        workbench_store.factory,
        source_id=arbitrary_id,
        artifact_sha256="7" * 64,
        null_validated_at=True,
    )
    async with workbench_store.factory() as session:
        before_result = await session.execute(
            text("SELECT * FROM ont_move_table_sources WHERE id=:id"),
            {"id": predecessor_id},
        )
        predecessor_before = tuple(before_result.one())

    app = _api(workbench_store.factory)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        accepted = await client.post(
            f"/api/ont/signal-workbench/move-sources/{predecessor_id}/fresh-attempt",
            json={},
        )
        rejected = await client.post(
            f"/api/ont/signal-workbench/move-sources/{arbitrary_id}/fresh-attempt",
            json={},
        )

    assert accepted.status_code == 202
    assert accepted.json()["predecessor_move_source_id"] == predecessor_id
    assert rejected.status_code == 409
    async with workbench_store.factory() as session:
        after_result = await session.execute(
            text("SELECT * FROM ont_move_table_sources WHERE id=:id"),
            {"id": predecessor_id},
        )
        assert tuple(after_result.one()) == predecessor_before
        assert await session.scalar(
            select(func.count())
            .select_from(OntMoveTableSource)
            .where(OntMoveTableSource.predecessor_move_source_id == arbitrary_id)
        ) == 0


@pytest.mark.asyncio
async def test_fresh_external_move_source_attempt_rejects_invalid_state_and_authority(
    workbench_store: WorkbenchStore,
) -> None:
    invalid_ids: list[str] = []
    for ordinal, state in enumerate(("requested", "running", "ready"), start=1):
        source_id = f"ont-moves-invalid-{state}"
        await _seed_failed_external_move_source(
            workbench_store.factory,
            source_id=source_id,
            artifact_sha256=f"{ordinal}" * 64,
            state=state,
        )
        invalid_ids.append(source_id)

    claimed_id = "ont-moves-invalid-claimed"
    await _seed_failed_external_move_source(
        workbench_store.factory,
        source_id=claimed_id,
        artifact_sha256="4" * 64,
    )
    leased_id = "ont-moves-invalid-leased"
    await _seed_failed_external_move_source(
        workbench_store.factory,
        source_id=leased_id,
        artifact_sha256="5" * 64,
    )
    dangling_id = "ont-moves-invalid-dangling"
    dangling_receipt_id, _ = await _seed_failed_external_move_source(
        workbench_store.factory,
        source_id=dangling_id,
        artifact_sha256="6" * 64,
    )
    divergent_id = "ont-moves-invalid-divergent"
    divergent_receipt_id, _ = await _seed_failed_external_move_source(
        workbench_store.factory,
        source_id=divergent_id,
        artifact_sha256="7" * 64,
    )
    async with workbench_store.factory() as session:
        claimed = await session.get(OntMoveTableSource, claimed_id)
        leased = await session.get(OntMoveTableSource, leased_id)
        divergent_receipt = await session.get(
            OntExternalMoveBamRegistrationReceipt,
            divergent_receipt_id,
        )
        dangling_receipt = await session.get(
            OntExternalMoveBamRegistrationReceipt,
            dangling_receipt_id,
        )
        assert claimed is not None and leased is not None
        assert divergent_receipt is not None and dangling_receipt is not None
        claimed.claim_token = "claim-must-block"
        leased.lease_expires_at = datetime.utcnow() + timedelta(minutes=5)
        divergent_receipt.artifact_size_bytes += 1
        await session.delete(dangling_receipt)
        managed = await session.get(OntMoveTableSource, "ont-moves-ready")
        assert managed is not None
        managed.validation_state = "failed"
        managed.reason_code = "SourceRepairRequired"
        managed.claim_token = None
        managed.lease_expires_at = None
        await session.commit()
    invalid_ids.extend(
        [claimed_id, leased_id, dangling_id, divergent_id, "ont-moves-ready"]
    )

    app = _api(workbench_store.factory)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        responses = [
            await client.post(
                f"/api/ont/signal-workbench/move-sources/{source_id}/fresh-attempt",
                json={},
            )
            for source_id in invalid_ids
        ]
    assert [response.status_code for response in responses] == [409] * len(invalid_ids)
    assert all("path" not in response.text.lower() for response in responses)

    async with workbench_store.factory() as session:
        assert await session.scalar(
            select(func.count())
            .select_from(OntMoveTableSource)
            .where(OntMoveTableSource.predecessor_move_source_id.in_(invalid_ids))
        ) == 0


@pytest.mark.asyncio
async def test_external_move_bam_catalog_unavailability_is_safe_503(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)
    monkeypatch.delenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, raising=False)
    async with AsyncClient(
        transport=ASGITransport(app=_api(workbench_store.factory)),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/ont/signal-workbench/external-move-bam-candidates"
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "external move-BAM source is unavailable"}


def test_public_json_omits_path_keyed_receipt_entries() -> None:
    public = service._public_json({
        "blow5_parents": {
            "/tmp/internal-parent/raw-0.blow5": "a" * 64,
            "/mnt/private/raw-1.blow5": "b" * 64,
        },
        "safe_digest": "c" * 64,
    })
    assert public == {"blow5_parents": {}, "safe_digest": "c" * 64}
    assert "/tmp/" not in json.dumps(public)
    assert "/mnt/" not in json.dumps(public)


@pytest.mark.asyncio
async def test_external_move_bam_catalog_and_registration_key_failures_are_path_opaque(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "public-error-external-root"
    external_root.mkdir()
    (external_root / "moves.bam").write_bytes(b"moves")
    rejected_key = workbench_store.root / "public-error-key"
    rejected_key.write_bytes(os.urandom(32))
    rejected_key.chmod(0o644)
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    monkeypatch.setenv(
        service.EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE_ENV,
        str(rejected_key),
    )

    async with AsyncClient(
        transport=ASGITransport(app=_api(workbench_store.factory)),
        base_url="http://test",
    ) as client:
        catalog = await client.get(
            "/api/ont/signal-workbench/external-move-bam-candidates"
        )
        registration = await client.post(
            "/api/ont/signal-workbench/runs/run-1/generations/1/external-move-bam-candidates/register",
            json={
                "candidate_id": "0" * 64,
                "raw_representation_id": "raw-blow5-1",
                "molecule_type": "dna",
            },
        )

    assert catalog.status_code == 503
    assert catalog.json() == {"detail": "external move-BAM source is unavailable"}
    assert registration.status_code == 409
    assert registration.json() == {"detail": "external move-BAM source is unavailable"}
    assert str(external_root) not in registration.text
    assert str(rejected_key) not in registration.text


@pytest.mark.asyncio
async def test_external_move_bam_candidate_id_survives_api_restart_with_same_key(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "restart-stable-external-root"
    external_root.mkdir()
    (external_root / "moves.bam").write_bytes(b"restart-stable")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)

    observed_ids: list[str] = []
    for app in (_api(workbench_store.factory), _api(workbench_store.factory)):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get(
                "/api/ont/signal-workbench/external-move-bam-candidates"
            )
        assert response.status_code == 200
        observed_ids.append(response.json()["items"][0]["candidate_id"])

    assert observed_ids[0] == observed_ids[1]


@pytest.mark.asyncio
async def test_external_move_bam_catalog_listing_runs_off_the_async_event_loop(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_loop_thread = threading.get_ident()
    listing_threads: list[int] = []

    def list_candidates() -> list[dict[str, Any]]:
        listing_threads.append(threading.get_ident())
        return []

    monkeypatch.setattr(service, "list_external_move_bam_candidates", list_candidates)
    async with AsyncClient(
        transport=ASGITransport(app=_api(workbench_store.factory)),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/ont/signal-workbench/external-move-bam-candidates"
        )

    assert response.status_code == 200
    assert listing_threads and listing_threads[0] != event_loop_thread


def test_router_openapi_closes_every_json_response_and_keeps_artifact_binary() -> None:
    app = FastAPI()
    app.include_router(router.router, prefix="/api/ont/signal-workbench")
    root = "/api/ont/signal-workbench"
    expected = {
        ("get", f"{root}/runs/{{run_id}}/generations/{{observed_generation}}/capabilities"): ("200", "WorkbenchCapabilitiesResponse"),
        ("get", f"{root}/runs/{{run_id}}/generations/{{observed_generation}}/move-sources"): ("200", "MoveSourceListResponse"),
        ("post", f"{root}/runs/{{run_id}}/generations/{{observed_generation}}/move-sources"): ("202", "MoveSourceResponse"),
        ("get", f"{root}/external-move-bam-candidates"): ("200", "ExternalMoveBamCandidateListResponse"),
        ("post", f"{root}/runs/{{run_id}}/generations/{{observed_generation}}/external-move-bam-candidates/register"): ("202", "MoveSourceResponse"),
        ("post", f"{root}/move-sources/{{move_source_id}}/fresh-attempt"): ("202", "MoveSourceResponse"),
        ("get", f"{root}/mapping-profiles"): ("200", "MappingProfileListResponse"),
        ("post", f"{root}/mapping-profiles"): ("201", "MappingProfileResponse"),
        ("get", f"{root}/calibrations"): ("200", "CalibrationArtifactListResponse"),
        ("post", f"{root}/runs/{{run_id}}/generations/{{observed_generation}}/calibrations"): ("202", "CalibrationJobResponse"),
        ("get", f"{root}/calibrations/{{calibration_job_id}}"): ("200", "CalibrationJobResponse"),
        ("post", f"{root}/calibrations/{{calibration_job_id}}/cancel"): ("202", "CalibrationJobResponse"),
        ("post", f"{root}/runs/{{run_id}}/generations/{{observed_generation}}/mappings"): ("202", "MappingJobResponse"),
        ("get", f"{root}/mappings/{{mapping_job_id}}"): ("200", "MappingJobResponse"),
        ("post", f"{root}/mappings/{{mapping_job_id}}/cancel"): ("202", "MappingJobResponse"),
        ("post", f"{root}/views"): ("202", "ViewJobResponse"),
        ("get", f"{root}/views/{{view_job_id}}"): ("200", "ViewJobResponse"),
        ("post", f"{root}/views/{{view_job_id}}/cancel"): ("202", "ViewJobResponse"),
        ("post", f"{root}/viewer-sessions"): ("201", "ViewerSessionResponse"),
        ("get", f"{root}/viewer-sessions/{{viewer_session_id}}"): ("200", "ViewerSessionResponse"),
        ("patch", f"{root}/viewer-sessions/{{viewer_session_id}}"): ("200", "ViewerSessionResponse"),
    }
    specification = app.openapi()
    routes = {
        (method.lower(), f"{root}{route.path}"): route
        for base_route in router.router.routes
        for route in [cast(APIRoute, base_route)]
        for method in route.methods
    }

    for (method, path), (status, model_name) in expected.items():
        operation = specification["paths"][path][method]
        assert operation["responses"][status]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{model_name}"
        }
        route = routes[(method, path)]
        assert route.response_model is getattr(router, model_name)
        assert route.endpoint.__annotations__.get("return") == model_name

    closed_components = {
        "WorkbenchCapabilitiesResponse",
        "WorkbenchResolvedResponse",
        "WorkbenchModesResponse",
        "CapabilityModeResponse",
        "MoveSourceResponse",
        "MoveSourceListResponse",
        "MoveTagCountsResponse",
        "FreshMoveSourceAttemptCreate",
        "MappingProfileResponse",
        "MappingProfileListResponse",
        "CalibrationSampleSelectionResponse",
        "CalibrationArtifactResponse",
        "CalibrationArtifactListResponse",
        "CalibrationJobResponse",
        "MappingArtifactResponse",
        "MappingJobResponse",
        "ReferenceRegionResponse",
        "RenderParamsResponse",
        "ViewArtifactDescriptorResponse",
        "ViewOutputManifestResponse",
        "ViewJobResponse",
        "ViewerSessionResponse",
    }
    schemas = specification["components"]["schemas"]
    for component in closed_components:
        assert schemas[component]["additionalProperties"] is False

    artifact_path = f"{root}/views/{{view_job_id}}/artifacts/{{artifact_id}}"
    artifact_operation = specification["paths"][artifact_path]["get"]
    assert "content" not in artifact_operation["responses"]["200"]
    artifact_route = routes[("get", artifact_path)]
    assert artifact_route.response_model is None
    assert artifact_route.endpoint.__annotations__.get("return") == "Response"


def test_mapping_profile_request_requires_non_null_calibration_artifact() -> None:
    schema = router.MappingProfileCreate.model_json_schema()
    assert "calibration_artifact_id" in schema["required"]
    assert schema["properties"]["calibration_artifact_id"] == {"title": "Calibration Artifact Id", "type": "string"}
    with pytest.raises(ValueError):
        router.MappingProfileCreate.model_validate({
            "name": "profile",
            "molecule_type": "dna",
            "basecall_model_id": MODEL_ID,
            "kmer_length": 5,
            "signal_move_offset": 4,
            "parameter_source": "approved_calibration",
            "calibration_artifact_id": None,
            "approval_receipt": {"approved": True},
        })


@pytest.mark.asyncio
async def test_closed_calibration_service_and_api_are_opaque_idempotent_and_cancellable(
    workbench_store: WorkbenchStore,
) -> None:
    app = _api(workbench_store.factory)
    route = "/api/ont/signal-workbench/runs/run-1/generations/1/calibrations"
    request = {
        "raw_representation_id": "raw-blow5-1",
        "move_source_id": "ont-moves-ready",
        "sample_count": 2,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        extra = await client.post(route, json={**request, "runtime_path": "/host/runtime"})
        path_parent = await client.post(
            route,
            json={**request, "raw_representation_id": "../reads.blow5"},
        )
        created = await client.post(route, json=request)
        repeated = await client.post(route, json=request)

        assert extra.status_code == 422
        assert path_parent.status_code == 422
        assert created.status_code == repeated.status_code == 202
        created_body = created.json()
        assert created_body == repeated.json()
        calibration_id = created_body["calibration_job_id"]
        assert OPAQUE_ID.fullmatch(calibration_id)
        assert "raw-blow5-1" not in calibration_id
        assert created_body["state"] == "requested"
        assert created_body["sample_count"] == 2
        assert re.fullmatch(r"[0-9a-f]{64}", created_body["request_fingerprint"])

        fetched = await client.get(
            f"/api/ont/signal-workbench/calibrations/{calibration_id}"
        )
        cancelled = await client.post(
            f"/api/ont/signal-workbench/calibrations/{calibration_id}/cancel"
        )
        cancelled_again = await client.post(
            f"/api/ont/signal-workbench/calibrations/{calibration_id}/cancel"
        )

    assert fetched.status_code == 200
    assert fetched.json() == created_body
    assert cancelled.status_code == cancelled_again.status_code == 202
    assert cancelled.json() == cancelled_again.json()
    assert cancelled.json()["state"] == "cancelled"
    assert cancelled.json()["reason_code"] == "cancelled_before_claim"

    async with workbench_store.factory() as session:
        assert await session.scalar(select(func.count()).select_from(OntSignalCalibrationJob)) == 1
        assert await service.get_calibration_job(session, calibration_id) == cancelled.json()
        with pytest.raises(KeyError, match="calibration job not found"):
            await service.get_calibration_job(session, "ont-signal-calibration-missing")


async def _publish_calibration_evidence(
    session: AsyncSession,
    *,
    sample_count: int = 2,
) -> OntSignalCalibrationArtifact:
    job_public = await service.create_calibration_job(
        session,
        run_id="run-1",
        observed_generation=1,
        raw_representation_id="raw-blow5-1",
        move_source_id="ont-moves-ready",
        sample_count=sample_count,
    )
    job = await session.get(OntSignalCalibrationJob, job_public["calibration_job_id"])
    assert job is not None
    artifact = OntSignalCalibrationArtifact(
        id=f"ont-signal-calibration-artifact-{sample_count}",
        raw_representation_id=job.raw_representation_id,
        move_source_id=job.move_source_id,
        basecall_model_id=MODEL_ID,
        sample_selection={
            "method": "sha256_read_id_rank_v1",
            "requested_count": sample_count,
            "selected_count": sample_count,
            "intersection_count": len(READ_IDS),
            "read_ids": READ_IDS[:sample_count],
            "selection_sha256": hashlib.sha256(
                json.dumps(READ_IDS[:sample_count], separators=(",", ":")).encode()
            ).hexdigest(),
        },
        recommended_kmer_length=5,
        recommended_signal_move_offset=4,
        score_evidence=[{"candidate_signal_move_offset": value} for value in range(9)],
        runtime_identity={"image_digest": "c" * 64},
        parent_sha256s={
            "raw_manifest_sha256": job.resource_snapshot["parents"]["raw_manifest_sha256"],
            "move_bam_sha256": (await session.get(OntMoveTableSource, job.move_source_id)).artifact_sha256,
            "move_read_inventory_sha256": READ_INVENTORY_SHA256,
        },
        artifact_sha256=hashlib.sha256(f"calibration-{sample_count}".encode()).hexdigest(),
        created_at=datetime.utcnow(),
    )
    session.add(artifact)
    await session.flush()
    job.calibration_artifact_id = artifact.id
    job.state = "ready"
    job.reason_code = "validated_calibration_ready"
    job.completed_at = job.updated_at = datetime.utcnow()
    await session.commit()
    return artifact


@pytest.mark.asyncio
async def test_model_exact_profile_requires_matching_immutable_calibration_evidence(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        artifact = await _publish_calibration_evidence(session)
        base = {
            "name": "Approved Dorado v4.3.0 calibration",
            "molecule_type": "dna",
            "basecall_model_id": MODEL_ID,
            "kmer_length": 5,
            "signal_move_offset": 4,
            "parameter_source": "approved_calibration",
            "calibration_artifact_id": artifact.id,
            "minimum_mapq": 0,
            "read_set_selection": "immutable_full_set",
            "approval_receipt": {
                "approved": True,
                "calibration_artifact_sha256": artifact.artifact_sha256,
            },
            "approved_by": "operator@example.test",
        }

        with pytest.raises(service.OntSignalError, match="calibration artifact is required"):
            await service.create_mapping_profile(
                session, **{**base, "calibration_artifact_id": None}
            )
        with pytest.raises(
            service.OntSignalError,
            match="parameters do not equal approved calibration evidence",
        ):
            await service.create_mapping_profile(
                session, **{**base, "basecall_model_id": "different-model"}
            )
        with pytest.raises(
            service.OntSignalError,
            match="parameters do not equal approved calibration evidence",
        ):
            await service.create_mapping_profile(session, **{**base, "kmer_length": 6})

        created = await service.create_mapping_profile(session, **base)
        repeated = await service.create_mapping_profile(session, **base)
        await session.commit()

        assert created == repeated
        assert OPAQUE_ID.fullmatch(created["mapping_profile_id"])
        assert created["basecall_model_id"] == MODEL_ID
        assert created["calibration_artifact_id"] == artifact.id
        assert created["kmer_length"] == artifact.recommended_kmer_length
        assert created["signal_move_offset"] == artifact.recommended_signal_move_offset
        assert created["primary_alignment_policy"] == "primary_only"
        assert created["minimum_mapq"] == 0
        assert created["include_supplementary"] is False
        assert created["read_set_selection"] == "immutable_full_set"
        assert await session.scalar(select(func.count()).select_from(OntSignalMappingProfile)) == 1


def _calibration_report(
    *,
    job: OntSignalCalibrationJob,
    source: OntMoveTableSource,
    mutation: str | None = None,
) -> dict[str, Any]:
    selected_ids = READ_IDS[: job.sample_count]
    managed_hashes = source.validation_receipt["managed_output_sha256s"]
    raw_artifacts = job.resource_snapshot["parents"]["raw_artifacts"]["artifacts"]
    blow5_items = [item for item in raw_artifacts if item.get("kind") == "blow5"]
    blow5_evidence = []
    for blow5_item in blow5_items:
        index_item = next(
            item for item in raw_artifacts if item.get("path") == f"{blow5_item['path']}.idx"
        )
        blow5_evidence.append(
            {
                "sha256": blow5_item["sha256"],
                "index_sha256": index_item["sha256"],
            }
        )
    report: dict[str, Any] = {
        "schema": "bms.ont-signal-calibration.v1",
        "basecall_model_id": source.basecall_model_id,
        "sample_selection": {
            "method": "sha256_read_id_rank_v1",
            "requested_count": job.sample_count,
            "selected_count": job.sample_count,
            "intersection_count": 2,
            "read_ids": selected_ids,
            "selection_sha256": hashlib.sha256(
                json.dumps(selected_ids, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "parent_sha256s": {
            "raw_manifest_sha256": job.resource_snapshot["parents"]["raw_manifest_sha256"],
            "move_bam_sha256": source.artifact_sha256,
            "filtered_move_bam_sha256": managed_hashes["filtered_move_bam_sha256"],
            "move_inventory_actual_sha256": READ_INVENTORY_SHA256,
            "blow5": blow5_evidence,
        },
        "tool_identity": {
            "name": "squigualiser",
            "version": "0.7.0",
            "commit": "5a2404f1f43bc3227a85475c59b2b77970078b2e",
            "candidate_kmer_bound": 9,
        },
        "recommendation": {"kmer_length": 5, "signal_move_offset": 4},
        "score_evidence": [
            {
                "candidate_signal_move_offset": value,
                "candidate_kmer_bound": 9,
                "score": float(value + 1),
                "read_count": job.sample_count,
            }
            for value in range(9)
        ],
        "validation": {
            "exact_intersection": True,
            "independent_recommendation_equal": True,
            "assumption_unambiguous": True,
        },
    }
    if mutation == "parent":
        report["parent_sha256s"]["raw_manifest_sha256"] = "f" * 64
    elif mutation == "read":
        duplicate_ids = ["read-1"] * job.sample_count
        report["sample_selection"]["read_ids"] = duplicate_ids
        report["sample_selection"]["selection_sha256"] = hashlib.sha256(
            json.dumps(duplicate_ids, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    elif mutation == "profile":
        report["basecall_model_id"] = "different-model"
    return report


def _patch_fake_calibration_runtime(
    monkeypatch: pytest.MonkeyPatch,
    worker: OntSignalWorker,
    output_root: Path,
    report: dict[str, Any],
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(worker, "_output_root", lambda: output_root)
    monkeypatch.setattr(
        worker,
        "_runtime_identity",
        lambda: {
            "image": f"sha256:{'d' * 64}",
            "image_digest": "d" * 64,
            "upstream_version": "0.7.0",
            "upstream_commit": "5a2404f1f43bc3227a85475c59b2b77970078b2e",
            "network": "none",
        },
    )

    async def fake_invoke(
        parents: Any,
        _arguments: list[str],
        kind: str,
        item_id: str,
        _claim_token: str,
        _output: Path,
        _allowed_output_names: set[str] | None = None,
    ) -> dict[str, Any]:
        parents.assert_unbroken()
        assert len(parents.parents) >= 5
        calls.append((kind, item_id))
        output = output_root / "calibrations" / item_id
        (output / "calibration.json").write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )
        return {
            "argv_sha256": "1" * 64,
            "returncode": 0,
            "stdout_sha256": "2" * 64,
            "stderr_sha256": "3" * 64,
            "stderr_tail": "",
        }

    monkeypatch.setattr(worker, "_invoke", fake_invoke)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["parent", "read", "profile"])
async def test_calibration_runtime_report_rejects_mismatched_parent_read_or_profile_evidence(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    worker = OntSignalWorker(workbench_store.factory, workbench_store.factory)
    async with workbench_store.factory() as session:
        created = await service.create_calibration_job(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            sample_count=2,
        )
        await session.commit()
        job = await session.get(OntSignalCalibrationJob, created["calibration_job_id"])
        source = await session.get(OntMoveTableSource, "ont-moves-ready")
        assert job is not None and source is not None
        report = _calibration_report(job=job, source=source, mutation=mutation)

    claimed = await worker._claim(OntSignalCalibrationJob, "state")
    assert claimed is not None
    job_id, claim_token = claimed
    _patch_fake_calibration_runtime(
        monkeypatch,
        worker,
        workbench_store.root / "fake-runtime",
        report,
    )

    with pytest.raises(
        RuntimeError, match="calibration report failed governed validation"
    ) as rejected:
        await worker._process_calibration(job_id, claim_token)
    await worker._fail(
        OntSignalCalibrationJob,
        "state",
        job_id,
        claim_token,
        rejected.value,
    )

    async with workbench_store.factory() as session:
        job = await session.get(OntSignalCalibrationJob, job_id)
        assert job is not None
        assert job.state == "failed"
        assert job.calibration_artifact_id is None
        assert await session.scalar(
            select(func.count()).select_from(OntSignalCalibrationArtifact)
        ) == 0


@pytest.mark.asyncio
async def test_leased_calibration_worker_recovers_cancels_and_publishes_only_once(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        service.EXTERNAL_MOVE_BAM_ROOT_ENV,
        str(workbench_store.root / "missing-optional-external-root"),
    )
    worker = OntSignalWorker(workbench_store.factory, workbench_store.factory)

    async with workbench_store.factory() as session:
        created = await service.create_calibration_job(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            sample_count=2,
        )
        await session.commit()
        job = await session.get(OntSignalCalibrationJob, created["calibration_job_id"])
        source = await session.get(OntMoveTableSource, "ont-moves-ready")
        assert job is not None and source is not None
        report = _calibration_report(job=job, source=source)

    claimed = await worker._claim(OntSignalCalibrationJob, "state")
    assert claimed is not None
    job_id, claim_token = claimed
    calls = _patch_fake_calibration_runtime(
        monkeypatch,
        worker,
        workbench_store.root / "fake-runtime",
        report,
    )
    await worker._process_calibration(job_id, claim_token)
    await worker._process_calibration(job_id, claim_token)

    async with workbench_store.factory() as session:
        published = await session.get(OntSignalCalibrationJob, job_id)
        assert published is not None
        assert published.state == "ready"
        assert published.claim_token is None
        assert published.calibration_artifact_id is not None
        assert await session.scalar(
            select(func.count()).select_from(OntSignalCalibrationArtifact)
        ) == 1
    assert calls == [("calibration", job_id)]

    async with workbench_store.factory() as session:
        cancel_created = await service.create_calibration_job(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            sample_count=1,
        )
        await session.commit()
    cancel_claim = await worker._claim(OntSignalCalibrationJob, "state")
    assert cancel_claim is not None
    cancel_id, cancel_token = cancel_claim
    assert cancel_id == cancel_created["calibration_job_id"]
    async with workbench_store.factory() as session:
        cancellation = await service.cancel_calibration_job(session, cancel_id)
        await session.commit()
        assert cancellation["state"] == "running"
        assert cancellation["reason_code"] == "cancellation_requested"
    with pytest.raises(asyncio.CancelledError):
        await worker._process_calibration(cancel_id, cancel_token)
    await worker._cancel_claim(
        OntSignalCalibrationJob, "state", cancel_id, cancel_token
    )
    async with workbench_store.factory() as session:
        cancelled = await session.get(OntSignalCalibrationJob, cancel_id)
        assert cancelled is not None
        assert cancelled.state == "cancelled"
        assert cancelled.claim_token is None
        assert cancelled.calibration_artifact_id is None
        assert await session.scalar(
            select(func.count()).select_from(OntSignalCalibrationArtifact)
        ) == 1

    async with workbench_store.factory() as session:
        recovery_created = await service.create_calibration_job(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            sample_count=3,
        )
        await session.commit()
    recovery_claim = await worker._claim(OntSignalCalibrationJob, "state")
    assert recovery_claim is not None
    recovery_id, _recovery_token = recovery_claim
    assert recovery_id == recovery_created["calibration_job_id"]
    async with workbench_store.factory() as session:
        recovering = await session.get(OntSignalCalibrationJob, recovery_id)
        assert recovering is not None
        recovering.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()
    await worker._recover_expired()
    async with workbench_store.factory() as session:
        recovered = await session.get(OntSignalCalibrationJob, recovery_id)
        assert recovered is not None
        assert recovered.state == "requested"
        assert recovered.reason_code == "expired_lease_recovered"
        assert recovered.claim_token is None
        assert recovered.lease_expires_at is None
        assert recovered.attempt == 1
        assert recovered.stage_receipts["lease_recoveries"][-1]["expired_attempt"] == 1


@pytest.mark.asyncio
async def test_expired_move_source_leases_consume_attempt_budget_to_terminal_failure(
    workbench_store: WorkbenchStore,
) -> None:
    source_id = "ont-moves-expired-budget"
    now = datetime.utcnow()
    async with workbench_store.factory() as session:
        session.add(
            OntMoveTableSource(
                id=source_id,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="move-input-1",
                source_job_id="source-job-1",
                artifact_sha256="8" * 64,
                artifact_size_bytes=10,
                molecule_type="dna",
                source_runtime_identity={"authority_state": "legacy_unknown"},
                validation_state="requested",
                reason_code="move_source_validation_requested",
                validation_receipt={"raw_manifest_sha256": "9" * 64},
                created_at=now,
            )
        )
        await session.commit()
    worker = OntSignalWorker(workbench_store.factory, workbench_store.factory)

    for attempt in range(1, worker_service.MOVE_SOURCE_MAX_ATTEMPTS + 1):
        claim = await worker._claim(OntMoveTableSource, "validation_state")
        assert claim is not None
        item_id, token = claim
        assert item_id == source_id
        async with workbench_store.factory() as session:
            running = await session.get(OntMoveTableSource, source_id)
            assert running is not None and running.claim_token == token
            running.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
            await session.commit()
        await worker._recover_expired()
        async with workbench_store.factory() as session:
            recovered = await session.get(OntMoveTableSource, source_id)
            assert recovered is not None
            failures = recovered.validation_receipt["retry"]["failures"]
            assert len(failures) == attempt
            assert failures[-1]["failure_code"] == "ExpiredLease"
            if attempt < worker_service.MOVE_SOURCE_MAX_ATTEMPTS:
                assert recovered.validation_state == "requested"
                assert recovered.reason_code == "move_source_retry_requested_after_expired_lease"
            else:
                assert recovered.validation_state == "failed"
                assert recovered.reason_code == "expired_lease_retry_exhausted"
                assert recovered.claim_token is None
                assert recovered.lease_expires_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", ["basecall_model_id", "read_inventory_sha256"])
async def test_capabilities_fail_closed_when_ready_move_source_lacks_required_evidence(
    workbench_store: WorkbenchStore,
    missing_field: str,
) -> None:
    async with workbench_store.factory() as session:
        source = await session.get(OntMoveTableSource, "ont-moves-ready")
        assert source is not None
        setattr(source, missing_field, None)
        await session.commit()

    async with workbench_store.factory() as session:
        capabilities = await service.workbench_capabilities(
            session, run_id="run-1", observed_generation=1
        )

    assert capabilities["resolved"]["raw_representation_id"] == "raw-blow5-1"
    assert capabilities["resolved"]["move_source_id"] is None
    assert capabilities["resolved"]["mapping_profile_id"] is None
    assert capabilities["modes"]["raw_waveform"]["state"] == "ready"
    assert capabilities["modes"]["signal_to_read"] == {
        "state": "unavailable",
        "reason_code": "compatible_move_table_source_missing",
    }
    assert capabilities["modes"]["signal_to_reference"]["state"] == "unavailable"
    assert capabilities["modes"]["signal_pileup"]["state"] == "unavailable"


@pytest.mark.asyncio
async def test_capabilities_route_forwards_complete_exact_alignment_authority(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = service.workbench_capabilities
    calls: list[dict[str, Any]] = []

    async def recording_capabilities(session: AsyncSession, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return await original(session, **kwargs)

    monkeypatch.setattr(service, "workbench_capabilities", recording_capabilities)
    app = _api(workbench_store.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/ont/signal-workbench/runs/run-1/generations/1/capabilities",
            params={
                "alignment_job_id": "alignment-job-exact",
                "alignment_session_id": "alignment-session-exact",
                "reference_revision_id": "reference-revision-exact",
            },
        )

    assert response.status_code == 200, response.text
    assert calls == [{
        "run_id": "run-1",
        "observed_generation": 1,
        "alignment_job_id": "alignment-job-exact",
        "alignment_session_id": "alignment-session-exact",
        "reference_revision_id": "reference-revision-exact",
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", [
    "alignment_job_id",
    "alignment_session_id",
    "reference_revision_id",
])
async def test_capabilities_route_rejects_partial_exact_alignment_authority(
    workbench_store: WorkbenchStore,
    missing_field: str,
) -> None:
    params = {
        "alignment_job_id": "alignment-job-exact",
        "alignment_session_id": "alignment-session-exact",
        "reference_revision_id": "reference-revision-exact",
    }
    params.pop(missing_field)
    app = _api(workbench_store.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/ont/signal-workbench/runs/run-1/generations/1/capabilities",
            params=params,
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "exact reference capability authority is incomplete"}


@pytest.mark.asyncio
async def test_viewer_session_api_rejects_stale_revision_without_overwriting_current_state(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.ngs_alignment_sessions,
        "resolve_alignment_session",
        lambda *_args, **_kwargs: {"ready": True, "artifacts": []},
    )
    async with workbench_store.factory() as session:
        session.add(
            Job(
                id="viewer-alignment-job",
                name="viewer alignment authority",
                status="completed",
                model_id="ont_alignment",
                mode="alignment",
                params={
                    "dataset_id": "dataset-1",
                    "source_instrument_run_id": "run-1",
                    "source_instrument_observed_generation": 1,
                    "ngs_reference_revision_id": "reference-revision-1",
                    "reference_sequence_sha256": "a" * 64,
                    "ont_workflow_id": "ont_alignment",
                    "ont_input_mode": "fastq",
                },
                output_dir=str(workbench_store.root),
            )
        )
        await session.commit()
    app = _api(workbench_store.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/ont/signal-workbench/viewer-sessions",
            json={
                "dataset_id": "dataset-1",
                "run_id": "run-1",
                "observed_generation": 1,
                "alignment_job_id": "viewer-alignment-job",
                "alignment_session_id": None,
                "reference_revision_id": None,
                "contig": "chr1",
                "locus_start": 100,
                "locus_end": 200,
                "selected_read_id": "read-1",
                **_viewer_create_states(),
            },
        )
        assert created.status_code == 201, created.text
        viewer = created.json()
        assert viewer["revision"] == 1
        assert OPAQUE_ID.fullmatch(viewer["viewer_session_id"])
        assert viewer["igv_state"] == _viewer_create_states()["igv_state"]
        assert viewer["signal_state"]["mode"] == "read"
        assert viewer["signal_state"]["view_job_id"] is None

        update = {
            "expected_revision": 1,
            "contig": "chr1",
            "locus_start": 120,
            "locus_end": 180,
            "selected_read_id": "read-2",
            "igv_state": {
                "alignment_display_mode": "EXPANDED",
                "alignment_color_by": "strand",
                "alignment_group_by": "none",
                "reads_track_loaded": True,
            },
            "signal_state": {
                "mode": "raw_waveform",
                "render_params": {},
                "view_job_id": None,
                "read_mapping_job_id": None,
                "reference_mapping_job_id": None,
            },
        }
        current = await client.patch(
            f"/api/ont/signal-workbench/viewer-sessions/{viewer['viewer_session_id']}",
            json=update,
        )
        stale = await client.patch(
            f"/api/ont/signal-workbench/viewer-sessions/{viewer['viewer_session_id']}",
            json={
                **update,
                "locus_start": 1,
                "locus_end": 10,
                "selected_read_id": "stale-read",
            },
        )
        fetched = await client.get(
            f"/api/ont/signal-workbench/viewer-sessions/{viewer['viewer_session_id']}"
        )

    assert current.status_code == 200
    assert current.json()["revision"] == 2
    assert stale.status_code == 409
    assert stale.json()["detail"] == "viewer session changed concurrently"
    assert fetched.status_code == 200
    assert fetched.json()["revision"] == 2
    assert fetched.json()["locus_start"] == 120
    assert fetched.json()["locus_end"] == 180
    assert fetched.json()["selected_read_id"] == "read-2"
    assert fetched.json()["igv_state"] == update["igv_state"]
    assert fetched.json()["signal_state"] == {
        **update["signal_state"],
        "render_params": router.RenderParams().model_dump(),
    }


async def _create_calibrated_profile(session: AsyncSession) -> dict[str, Any]:
    artifact = await _publish_calibration_evidence(session)
    return await service.create_mapping_profile(
        session,
        name="Approved calibrated profile",
        molecule_type="dna",
        basecall_model_id=MODEL_ID,
        kmer_length=artifact.recommended_kmer_length,
        signal_move_offset=artifact.recommended_signal_move_offset,
        parameter_source="approved_calibration",
        calibration_artifact_id=artifact.id,
        minimum_mapq=0,
        read_set_selection="immutable_full_set",
        approval_receipt={
            "approved": True,
            "calibration_artifact_sha256": artifact.artifact_sha256,
        },
        approved_by="operator@example.test",
    )


@pytest.mark.asyncio
async def test_move_source_registration_is_nofollow_job_owned_and_external_receipts_fail_closed(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = workbench_store.root / "approved"
    owned = approved / "job-output"
    other = approved / "other"
    outside = workbench_store.root / "outside"
    for directory in (owned, other, outside):
        directory.mkdir(parents=True)
    owned_bam = owned / "owned.bam"
    owned_bam.write_bytes(b"owned-move-table")
    other_bam = other / "other.bam"
    other_bam.write_bytes(b"other-move-table")
    outside_bam = outside / "linked.bam"
    outside_bam.write_bytes(b"linked-move-table")
    (approved / "linked-output").symlink_to(outside, target_is_directory=True)
    owned_sha256 = hashlib.sha256(owned_bam.read_bytes()).hexdigest()
    runtime_receipt = {
        "schema": "biomodstack.dorado_runtime_provenance.v1",
        "mode": "simplex",
        "model_id": MODEL_ID,
        "runtime_sha256": "9" * 64,
        "emit_moves": True,
        "calls_bam": {
            "sha256": owned_sha256,
            "read_count": 2,
            "read_inventory_sha256": READ_INVENTORY_SHA256,
            "move_tags": {"mv": 2, "ts": 2, "ns": 2},
        },
    }
    runtime_path = owned / "dorado_runtime_provenance.json"
    runtime_path.write_text(json.dumps(runtime_receipt), encoding="utf-8")
    runtime_sha256 = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
    monkeypatch.setattr(service, "get_allowed_roots", lambda: {"results": approved})

    async with workbench_store.factory() as session:
        session.add_all(
            [
                Job(
                    id="move-job-owned",
                    name="owned move producer",
                    status="completed",
                    model_id="dorado",
                    mode="basecall",
                    params={
                        "dorado_resolved_model_id": MODEL_ID,
                        "emit_moves": True,
                        "source_instrument_run_id": "run-1",
                        "source_instrument_observed_generation": 1,
                    },
                    provenance={
                        "ont_dorado_terminal_products": {
                            "schema": "biomodstack.ont_dorado_terminal_products.v1",
                            "stage": "dorado_demux",
                            "identities": {
                                "model_id": MODEL_ID,
                                "mode": "simplex",
                                "runtime_sha256": "9" * 64,
                                "calls_bam_sha256": owned_sha256,
                                "read_count": 2,
                            },
                            "products": {
                                "dorado_runtime_provenance": {
                                    "path": runtime_path.name,
                                    "sha256": runtime_sha256,
                                }
                            },
                        }
                    },
                    output_dir=str(owned),
                ),
                Job(
                    id="move-job-wrong-generation",
                    name="wrong generation producer",
                    status="completed",
                    model_id="dorado",
                    mode="basecall",
                    params={
                        "source_instrument_run_id": "other-run",
                        "source_instrument_observed_generation": 99,
                    },
                    output_dir=str(owned),
                ),
                Job(
                    id="move-job-wrong-root",
                    name="wrong move producer",
                    status="completed",
                    model_id="dorado",
                    mode="basecall",
                    params={
                        "source_instrument_run_id": "run-1",
                        "source_instrument_observed_generation": 1,
                    },
                    output_dir=str(owned),
                ),
                Job(
                    id="move-job-symlink",
                    name="symlink move producer",
                    status="completed",
                    model_id="dorado",
                    mode="basecall",
                    params={
                        "source_instrument_run_id": "run-1",
                        "source_instrument_observed_generation": 1,
                    },
                    output_dir=str(approved / "linked-output"),
                ),
                InputFile(
                    id="move-input-owned",
                    filename=owned_bam.name,
                    file_type="bam",
                    directory=str(owned),
                    size_bytes=owned_bam.stat().st_size,
                ),
                InputFile(
                    id="move-input-other",
                    filename=other_bam.name,
                    file_type="bam",
                    directory=str(other),
                    size_bytes=other_bam.stat().st_size,
                ),
                InputFile(
                    id="move-input-symlink",
                    filename=outside_bam.name,
                    file_type="bam",
                    directory=str(approved / "linked-output"),
                    size_bytes=outside_bam.stat().st_size,
                ),
            ]
        )
        await session.commit()

        owned_job = await session.get(Job, "move-job-owned")
        assert owned_job is not None
        session.add(
            Job(
                id="move-job-legacy-anchor",
                name="legacy anchor producer",
                status="completed",
                model_id="dorado",
                mode="basecall",
                params={
                    "dorado_resolved_model_id": MODEL_ID,
                    "emit_moves": True,
                },
                provenance=owned_job.provenance,
                output_dir=str(owned),
            )
        )
        await session.commit()
        legacy_job = await session.get(Job, "move-job-legacy-anchor")
        assert legacy_job is not None
        legacy_identity = service._derive_source_runtime_identity(legacy_job, owned_sha256)
        assert legacy_identity["authority_state"] == "legacy_unknown"

        with pytest.raises(service.OntSignalError, match="run generation"):
            await service.register_move_source(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="move-input-owned",
                molecule_type="dna",
                source_job_id="move-job-wrong-generation",
                external_registration_receipt_id=None,
                source_runtime_identity=None,
            )

        created = await service.register_move_source(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            input_file_id="move-input-owned",
            molecule_type="dna",
            source_job_id="move-job-owned",
            external_registration_receipt_id=None,
            source_runtime_identity=None,
        )
        assert created["artifact_sha256"] == hashlib.sha256(owned_bam.read_bytes()).hexdigest()
        replayed = await service.register_move_source(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            input_file_id="move-input-owned",
            molecule_type="dna",
            source_job_id="move-job-owned",
            external_registration_receipt_id=None,
            source_runtime_identity=None,
        )
        assert replayed["move_source_id"] == created["move_source_id"]
        with pytest.raises(service.OntSignalError, match="replay authority diverged"):
            await service.register_move_source(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="move-input-owned",
                molecule_type="rna",
                source_job_id="move-job-owned",
                external_registration_receipt_id=None,
                source_runtime_identity=None,
            )

        with pytest.raises(service.OntSignalError, match="does not own"):
            await service.register_move_source(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="move-input-other",
                molecule_type="dna",
                source_job_id="move-job-wrong-root",
                external_registration_receipt_id=None,
                source_runtime_identity=None,
            )
        with pytest.raises(service.OntSignalError, match="without following symbolic links"):
            await service.register_move_source(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="move-input-symlink",
                molecule_type="dna",
                source_job_id="move-job-symlink",
                external_registration_receipt_id=None,
                source_runtime_identity=None,
            )
        with pytest.raises(service.OntSignalError, match="external registration receipts are not supported"):
            await service.register_move_source(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="move-input-owned",
                molecule_type="dna",
                source_job_id=None,
                external_registration_receipt_id="caller-supplied",
                source_runtime_identity=None,
            )

    app = _api(workbench_store.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/ont/signal-workbench/runs/run-1/generations/1/move-sources",
            json={
                "raw_representation_id": "raw-blow5-1",
                "input_file_id": "move-input-owned",
                "molecule_type": "dna",
                "external_registration_receipt_id": "caller-supplied",
            },
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_external_move_bam_candidate_registration_is_path_opaque_durable_and_worker_revalidates_identity(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "external-move-bams"
    nested = external_root / "BFX6NB" / "bam"
    nested.mkdir(parents=True)
    bam = nested / "BFX6NB_1_JAN26-EL-Q2-01.bam"
    bam_bytes = b"external-move-bam-with-mv-ts-ns"
    bam.write_bytes(bam_bytes)
    escaped = workbench_store.root / "escaped.bam"
    escaped.write_bytes(b"escaped")
    (external_root / "escaped-link.bam").symlink_to(escaped)
    (external_root / "not-a-supported-candidate.ubam").write_bytes(b"ubam")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)
    event_loop_thread = threading.get_ident()
    sealing_threads: list[int] = []
    original_seal = service._seal_external_move_bam_candidate

    def observed_seal(candidate_id: str) -> dict[str, Any]:
        sealing_threads.append(threading.get_ident())
        return original_seal(candidate_id)

    monkeypatch.setattr(service, "_seal_external_move_bam_candidate", observed_seal)

    app = _api(workbench_store.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with monkeypatch.context() as listing_patch:
            listing_patch.setattr(
                service,
                "_stable_descriptor_identity",
                lambda _descriptor: (_ for _ in ()).throw(
                    AssertionError("candidate listing must not hash BAM bytes")
                ),
            )
            listed = await client.get(
                "/api/ont/signal-workbench/external-move-bam-candidates"
            )
        assert listed.status_code == 200
        candidates = listed.json()["items"]
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate == {
            "candidate_id": candidate["candidate_id"],
            "display_name": bam.name,
            "size_bytes": len(bam_bytes),
            "modified_at_ns": bam.stat().st_mtime_ns,
        }
        assert re.fullmatch(r"[0-9a-f]{64}", candidate["candidate_id"])
        assert all("path" not in key and "root" not in key for key in candidate)

        registered = await client.post(
            "/api/ont/signal-workbench/runs/run-1/generations/1/external-move-bam-candidates/register",
            json={
                "candidate_id": candidate["candidate_id"],
                "raw_representation_id": "raw-blow5-1",
                "molecule_type": "dna",
            },
        )
        assert registered.status_code == 202, registered.text
        assert sealing_threads and sealing_threads[0] != event_loop_thread
        source_payload = registered.json()
        assert source_payload["source_job_id"] is None
        assert OPAQUE_ID.fullmatch(source_payload["external_registration_receipt_id"])
        assert source_payload["source_runtime_identity"] == {
            "schema": "bms.ont-move-source-producer-runtime.v1",
            "authority_state": "legacy_unknown",
            "source_job_id": None,
            "source_bam_sha256": hashlib.sha256(bam_bytes).hexdigest(),
            "reason_code": "producer_runtime_provenance_unavailable",
            "requires_independent_move_validation": True,
        }
        assert not any("path" in key or "root" in key for key in source_payload)

    receipt_type = getattr(database_models, "OntExternalMoveBamRegistrationReceipt")
    async with workbench_store.factory() as session:
        receipt = await session.get(
            receipt_type, source_payload["external_registration_receipt_id"]
        )
        assert receipt is not None
        assert receipt.server_relative_path == "BFX6NB/bam/BFX6NB_1_JAN26-EL-Q2-01.bam"
        assert receipt.run_id == "run-1"
        assert receipt.observed_generation == 1
        assert receipt.raw_representation_id == "raw-blow5-1"
        assert receipt.artifact_sha256 == hashlib.sha256(bam_bytes).hexdigest()
        assert receipt.artifact_size_bytes == len(bam_bytes)
        root_info = external_root.stat()
        registered_info = bam.stat()
        assert (receipt.root_device, receipt.root_inode) == (root_info.st_dev, root_info.st_ino)
        assert (
            receipt.file_device,
            receipt.file_inode,
            receipt.file_mtime_ns,
            receipt.file_ctime_ns,
        ) == (
            registered_info.st_dev,
            registered_info.st_ino,
            registered_info.st_mtime_ns,
            registered_info.st_ctime_ns,
        )
        assert await session.scalar(select(func.count(Job.id))) == 1
        tracked = await session.get(InputFile, source_payload["artifact_id"])
        assert tracked is not None
        assert tracked.directory == ""
        assert str(external_root) not in tracked.filename

    retired_root = workbench_store.root / "retired-external-move-bams"
    replacement_root = workbench_store.root / "replacement-external-move-bams"
    replacement_bam = replacement_root / "BFX6NB" / "bam" / bam.name
    replacement_bam.parent.mkdir(parents=True)
    replacement_bam.write_bytes(bam_bytes)
    os.replace(external_root, retired_root)
    os.replace(replacement_root, external_root)
    worker = OntSignalWorker(workbench_store.factory, workbench_store.factory)
    claimed = await worker._claim(OntMoveTableSource, "validation_state")
    assert claimed is not None
    invoked = False

    async def forbidden_invoke(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal invoked
        invoked = True
        raise AssertionError("runtime must not run after external receipt identity divergence")

    monkeypatch.setattr(worker, "_invoke", forbidden_invoke)
    with pytest.raises(RuntimeError, match="external move BAM identity diverged"):
        await worker._process_move(*claimed)
    assert invoked is False


@pytest.mark.asyncio
async def test_external_move_bam_replay_uses_retained_authority_before_current_sources(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "durable-replay-external-root"
    external_root.mkdir()
    bam = external_root / "durable-replay.bam"
    bam.write_bytes(b"durable-replay-move-bam")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    key_file = _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)
    app = _api(workbench_store.factory)
    route = "/api/ont/signal-workbench/runs/run-1/generations/1/external-move-bam-candidates/register"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        catalog = await client.get(
            "/api/ont/signal-workbench/external-move-bam-candidates"
        )
        candidate_id = catalog.json()["items"][0]["candidate_id"]
        request = {
            "candidate_id": candidate_id,
            "raw_representation_id": "raw-blow5-1",
            "molecule_type": "dna",
        }
        created = await client.post(route, json=request)
        assert created.status_code == 202, created.text

        async with workbench_store.factory() as session:
            representation = await session.get(
                OntRawSignalRepresentation, "raw-blow5-1"
            )
            assert representation is not None
            representation.state = "unavailable"
            representation.reason_code = "current_raw_context_retired"
            representation.validation_receipts = {}
            await session.commit()

        retired_root = workbench_store.root / "durable-replay-retired-root"
        os.replace(external_root, retired_root)
        key_file.unlink()
        monkeypatch.delenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV)
        monkeypatch.delenv(service.EXTERNAL_MOVE_BAM_CANDIDATE_KEY_FILE_ENV)

        replayed = await client.post(route, json=request)
        mismatched = await client.post(
            route,
            json={**request, "molecule_type": "rna"},
        )

    assert replayed.status_code == 202, replayed.text
    assert replayed.json() == created.json()
    assert mismatched.status_code == 409
    assert mismatched.json() == {"detail": "ready indexed BLOW5 authority is required"}

    receipt_type = getattr(database_models, "OntExternalMoveBamRegistrationReceipt")
    async with workbench_store.factory() as session:
        assert await session.scalar(select(func.count()).select_from(receipt_type)) == 1
        assert await session.scalar(
            select(func.count()).select_from(OntMoveTableSource).where(
                OntMoveTableSource.external_registration_receipt_id.is_not(None)
            )
        ) == 1


def test_worker_governed_roots_include_raw_signal_publication_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging_root = tmp_path / "ont-raw"
    publication_root = tmp_path / "ont-raw-signal"
    staging_root.mkdir()
    publication_root.mkdir()
    monkeypatch.setenv(worker_service.ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(staging_root))

    roots = OntSignalWorker._governed_parent_roots()

    assert publication_root in roots


@pytest.mark.asyncio
async def test_worker_pins_external_move_bam_beneath_same_validated_root_generation(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "worker-root-generation"
    nested = external_root / "nested"
    nested.mkdir(parents=True)
    original_bytes = b"original-root-generation"
    (nested / "moves.bam").write_bytes(original_bytes)
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)
    candidate_id = service.list_external_move_bam_candidates()[0]["candidate_id"]

    async with workbench_store.factory() as session:
        async with session.begin():
            created = await service.register_external_move_bam_candidate(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                candidate_id=candidate_id,
                molecule_type="dna",
            )

    worker = OntSignalWorker(workbench_store.factory, workbench_store.factory)
    async with workbench_store.factory() as session:
        source = await session.get(OntMoveTableSource, created["move_source_id"])
        assert source is not None
        authority = await worker._resolve_move_bam_authority(session, source)

    retired_root = workbench_store.root / "worker-root-generation-retired"
    replacement_root = workbench_store.root / "worker-root-generation-replacement"
    replacement_nested = replacement_root / "nested"
    replacement_nested.mkdir(parents=True)
    (replacement_nested / "moves.bam").write_bytes(b"swapped-root-generation")
    os.replace(external_root, retired_root)
    os.replace(replacement_root, external_root)

    with RetainedParentSet(worker._governed_parent_roots()) as parents:
        retained = await worker._pin_move_bam_authority(
            parents,
            authority,
            alias="moves.bam",
            expected_sha256=created["artifact_sha256"],
            expected_size=created["artifact_size_bytes"],
        )
        assert retained.sha256 == hashlib.sha256(original_bytes).hexdigest()
        assert os.pread(retained.fd, len(original_bytes), 0) == original_bytes


def test_external_move_bam_seal_closes_both_descriptors_on_prebind_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = tmp_path / "seal-error-root"
    external_root.mkdir()
    (external_root / "seal-error.bam").write_bytes(b"seal-error")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    _configure_external_move_bam_candidate_key(tmp_path, monkeypatch)
    candidate_id = service.list_external_move_bam_candidates()[0]["candidate_id"]
    captured: list[int] = []
    original_open = service._open_external_move_bam_candidate

    def observed_open(*args: Any, **kwargs: Any) -> Any:
        result = original_open(*args, **kwargs)
        captured.extend(value for value in result[:2] if isinstance(value, int))
        return result

    monkeypatch.setattr(service, "_open_external_move_bam_candidate", observed_open)
    monkeypatch.setattr(
        service,
        "_stable_descriptor_identity",
        lambda _descriptor: (_ for _ in ()).throw(RuntimeError("hash failed")),
    )

    with pytest.raises(RuntimeError, match="hash failed"):
        service._seal_external_move_bam_candidate(candidate_id)

    assert len(captured) == 2
    for descriptor in captured:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.asyncio
async def test_external_move_bam_registration_holds_descriptors_through_outer_commit_and_savepoint(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "descriptor-commit-root"
    external_root.mkdir()
    (external_root / "descriptor-commit.bam").write_bytes(b"descriptor-commit")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)
    candidate_id = service.list_external_move_bam_candidates()[0]["candidate_id"]
    captured: list[int] = []
    original_seal = service._seal_external_move_bam_candidate

    def observed_seal(selected_candidate_id: str) -> Any:
        sealed, retained = cast(
            tuple[dict[str, Any], list[int]],
            original_seal(selected_candidate_id),
        )
        captured.extend(retained)
        return sealed, retained

    monkeypatch.setattr(service, "_seal_external_move_bam_candidate", observed_seal)
    async with workbench_store.factory() as session:
        async with session.begin():
            await service.register_external_move_bam_candidate(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                candidate_id=candidate_id,
                molecule_type="dna",
            )
            assert len(captured) == 2
            async with session.begin_nested():
                for descriptor in captured:
                    os.fstat(descriptor)
            for descriptor in captured:
                os.fstat(descriptor)
        for descriptor in captured:
            with pytest.raises(OSError):
                os.fstat(descriptor)


@pytest.mark.asyncio
async def test_external_move_bam_registration_closes_descriptors_after_outer_rollback(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "descriptor-rollback-root"
    external_root.mkdir()
    (external_root / "descriptor-rollback.bam").write_bytes(b"descriptor-rollback")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)
    candidate_id = service.list_external_move_bam_candidates()[0]["candidate_id"]
    captured: list[int] = []
    original_seal = service._seal_external_move_bam_candidate

    def observed_seal(selected_candidate_id: str) -> Any:
        sealed, retained = cast(
            tuple[dict[str, Any], list[int]],
            original_seal(selected_candidate_id),
        )
        captured.extend(retained)
        return sealed, retained

    monkeypatch.setattr(service, "_seal_external_move_bam_candidate", observed_seal)
    async with workbench_store.factory() as session:
        with pytest.raises(RuntimeError, match="force outer rollback"):
            async with session.begin():
                await service.register_external_move_bam_candidate(
                    session,
                    run_id="run-1",
                    observed_generation=1,
                    raw_representation_id="raw-blow5-1",
                    candidate_id=candidate_id,
                    molecule_type="dna",
                )
                assert len(captured) == 2
                raise RuntimeError("force outer rollback")
        for descriptor in captured:
            with pytest.raises(OSError):
                os.fstat(descriptor)


@pytest.mark.asyncio
async def test_external_move_bam_registration_cancellation_waits_for_seal_and_closes_handoff(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "descriptor-cancel-root"
    external_root.mkdir()
    (external_root / "descriptor-cancel.bam").write_bytes(b"descriptor-cancel")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)
    candidate_id = service.list_external_move_bam_candidates()[0]["candidate_id"]
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    returned_descriptors: list[int] = []

    def blocked_seal(_candidate_id: str) -> tuple[dict[str, Any], list[int]]:
        entered.set()
        release.wait(timeout=5)
        returned_descriptors.extend(os.pipe())
        finished.set()
        return {}, list(returned_descriptors)

    monkeypatch.setattr(service, "_seal_external_move_bam_candidate", blocked_seal)
    async with workbench_store.factory() as session:
        task = asyncio.create_task(
            service.register_external_move_bam_candidate(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                candidate_id=candidate_id,
                molecule_type="dna",
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        completed_before_release = task.done()
        release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        with pytest.raises(asyncio.CancelledError):
            await task

    assert completed_before_release is False
    assert len(returned_descriptors) == 2
    for descriptor in returned_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.asyncio
async def test_external_move_bam_router_rolls_back_unexpected_registration_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSession:
        rollback_calls = 0

        async def rollback(self) -> None:
            self.rollback_calls += 1

    async def fail_registration(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("unexpected registration failure")

    session = FailingSession()
    monkeypatch.setattr(service, "register_external_move_bam_candidate", fail_registration)
    request = router.ExternalMoveBamRegistrationCreate(
        candidate_id="d" * 64,
        raw_representation_id="raw-blow5-1",
        molecule_type="dna",
    )

    with pytest.raises(RuntimeError, match="unexpected registration failure"):
        await router.register_external_move_bam_candidate(
            "run-1",
            1,
            request,
            cast(AsyncSession, session),
        )
    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_equivalent_move_source_registration_race_returns_winner_and_unrelated_integrity_raises(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = workbench_store.root / "registration-race"
    approved.mkdir()
    bam = approved / "race.bam"
    bam.write_bytes(b"equivalent-registration-race")
    unrelated_bam = approved / "unrelated.bam"
    unrelated_bam.write_bytes(b"unrelated-integrity")
    monkeypatch.setattr(service, "get_allowed_roots", lambda: {"results": workbench_store.root})
    async with workbench_store.factory() as session:
        session.add_all([
            Job(
                id="race-source-job",
                name="race source",
                status="completed",
                model_id="legacy",
                mode="basecall",
                params={},
                output_dir=str(approved),
            ),
            InputFile(
                id="race-source-input",
                filename=bam.name,
                file_type="bam",
                directory=str(approved),
                size_bytes=bam.stat().st_size,
            ),
            Job(
                id="unrelated-source-job",
                name="unrelated source",
                status="completed",
                model_id="legacy",
                mode="basecall",
                params={},
                output_dir=str(approved),
            ),
            InputFile(
                id="unrelated-source-input",
                filename=unrelated_bam.name,
                file_type="bam",
                directory=str(approved),
                size_bytes=unrelated_bam.stat().st_size,
            ),
        ])
        await session.commit()

    arrivals = 0
    release = asyncio.Event()
    original_hash = service._hash_job_owned_input

    async def synchronized_hash(item: InputFile, source_job: Job) -> tuple[str, int]:
        nonlocal arrivals
        result = original_hash(item, source_job)
        arrivals += 1
        if arrivals == 2:
            release.set()
        await release.wait()
        return result

    monkeypatch.setattr(service, "_hash_job_owned_input_async", synchronized_hash)

    async def contender() -> str:
        async with workbench_store.factory() as session:
            result = await service.register_move_source(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="race-source-input",
                molecule_type="dna",
                source_job_id="race-source-job",
                external_registration_receipt_id=None,
                source_runtime_identity=None,
            )
            await session.commit()
            return str(result["move_source_id"])

    winners = await asyncio.gather(contender(), contender())
    assert winners[0] == winners[1]
    async with workbench_store.factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(OntMoveTableSource).where(
                OntMoveTableSource.artifact_sha256 == hashlib.sha256(bam.read_bytes()).hexdigest()
            )
        )
        assert count == 1

    async with workbench_store.factory() as session:
        original_execute = session.execute

        async def unrelated_integrity(statement: Any, *args: Any, **kwargs: Any) -> Any:
            if getattr(statement, "is_insert", False):
                raise IntegrityError("unrelated constraint", {}, RuntimeError("foreign key"))
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", unrelated_integrity)
        with pytest.raises(IntegrityError, match="unrelated constraint"):
            await service.register_move_source(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="unrelated-source-input",
                molecule_type="dna",
                source_job_id="unrelated-source-job",
                external_registration_receipt_id=None,
                source_runtime_identity=None,
            )


@pytest.mark.asyncio
async def test_concurrent_exact_external_move_bam_registrations_replay_one_winner(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_root = workbench_store.root / "external-registration-race"
    external_root.mkdir()
    bam = external_root / "race.bam"
    bam.write_bytes(b"external-registration-race")
    monkeypatch.setenv(service.EXTERNAL_MOVE_BAM_ROOT_ENV, str(external_root))
    _configure_external_move_bam_candidate_key(workbench_store.root, monkeypatch)
    async with workbench_store.factory() as session:
        assert await session.scalar(text("PRAGMA journal_mode=WAL")) == "wal"
        await session.commit()
    candidate_id = service.list_external_move_bam_candidates()[0]["candidate_id"]
    barrier = threading.Barrier(2)
    seal_count = 0
    seal_lock = threading.Lock()
    original_seal = service._seal_external_move_bam_candidate

    def synchronized_seal(selected_candidate_id: str) -> tuple[dict[str, Any], list[int]]:
        nonlocal seal_count
        result = original_seal(selected_candidate_id)
        with seal_lock:
            seal_count += 1
            should_wait = seal_count <= 2
        if should_wait:
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(service, "_seal_external_move_bam_candidate", synchronized_seal)
    connection_ids: set[int] = set()

    async def contender() -> dict[str, Any]:
        async with workbench_store.factory() as session:
            connection = await session.connection()
            connection_ids.add(id(connection.sync_connection.connection.dbapi_connection))
            await session.execute(text("BEGIN"))
            result = await service.register_external_move_bam_candidate(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                candidate_id=candidate_id,
                molecule_type="dna",
            )
            await session.commit()
            return result

    winners = await asyncio.gather(contender(), contender())
    assert len(connection_ids) == 2
    assert winners[0] == winners[1]
    receipt_type = getattr(database_models, "OntExternalMoveBamRegistrationReceipt")
    async with workbench_store.factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(InputFile).where(
                InputFile.id == winners[0]["artifact_id"]
            )
        ) == 1
        assert await session.scalar(select(func.count()).select_from(receipt_type)) == 1
        assert await session.scalar(
            select(func.count()).select_from(OntMoveTableSource).where(
                OntMoveTableSource.artifact_sha256 == winners[0]["artifact_sha256"]
            )
        ) == 1

    async with workbench_store.factory() as session:
        with pytest.raises(service.OntSignalError, match="replay authority diverged"):
            async with session.begin():
                await service.register_external_move_bam_candidate(
                    session,
                    run_id="run-1",
                    observed_generation=1,
                    raw_representation_id="raw-blow5-1",
                    candidate_id=candidate_id,
                    molecule_type="rna",
                )


@pytest.mark.asyncio
async def test_move_source_registration_seals_server_runtime_authority_or_explicit_legacy_unknown(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = workbench_store.root / "producer-authority"
    known_root = approved / "known"
    legacy_root = approved / "legacy"
    known_root.mkdir(parents=True)
    legacy_root.mkdir(parents=True)
    known_bam = known_root / "calls.bam"
    legacy_bam = legacy_root / "legacy.bam"
    known_bam.write_bytes(b"known-move-table")
    legacy_bam.write_bytes(b"legacy-qualified-move-table")
    known_sha256 = hashlib.sha256(known_bam.read_bytes()).hexdigest()
    runtime_receipt = {
        "schema": "biomodstack.dorado_runtime_provenance.v1",
        "mode": "simplex",
        "model_id": MODEL_ID,
        "runtime_sha256": "4" * 64,
        "emit_moves": True,
        "calls_bam": {
            "sha256": known_sha256,
            "read_count": 2,
            "read_inventory_sha256": READ_INVENTORY_SHA256,
            "move_tags": {"mv": 2, "ts": 2, "ns": 2},
            "duplex_dx1": 0,
        },
        "network": "denied_by_namespace",
        "model_download": "denied_by_namespace_and_sealed_models",
    }
    runtime_path = known_root / "dorado_runtime_provenance.json"
    runtime_path.write_text(json.dumps(runtime_receipt), encoding="utf-8")
    runtime_receipt_sha256 = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
    monkeypatch.setattr(service, "get_allowed_roots", lambda: {"results": approved})

    async with workbench_store.factory() as session:
        session.add_all(
            [
                Job(
                    id="known-producer-job",
                    name="known Dorado producer",
                    status="completed",
                    model_id="nanopore",
                    mode="basecall_dna",
                    params={
                        "dorado_resolved_model_id": MODEL_ID,
                        "emit_moves": True,
                        "source_instrument_run_id": "run-1",
                        "source_instrument_observed_generation": 1,
                    },
                    provenance={
                        "ont_dorado_terminal_products": {
                            "schema": "biomodstack.ont_dorado_terminal_products.v1",
                            "stage": "dorado_demux",
                            "identities": {
                                "model_id": MODEL_ID,
                                "mode": "simplex",
                                "runtime_sha256": "4" * 64,
                                "calls_bam_sha256": known_sha256,
                                "read_count": 2,
                            },
                            "products": {
                                "dorado_runtime_provenance": {
                                    "path": "dorado_runtime_provenance.json",
                                    "sha256": runtime_receipt_sha256,
                                }
                            },
                        }
                    },
                    output_dir=str(known_root),
                ),
                Job(
                    id="legacy-producer-job",
                    name="legacy qualified producer",
                    status="completed",
                    model_id="legacy-basecaller",
                    mode="basecall",
                    params={
                        "source_instrument_run_id": "run-1",
                        "source_instrument_observed_generation": 1,
                    },
                    provenance={},
                    output_dir=str(legacy_root),
                ),
                InputFile(
                    id="known-producer-bam",
                    filename=known_bam.name,
                    file_type="bam",
                    directory=str(known_root),
                    size_bytes=known_bam.stat().st_size,
                ),
                InputFile(
                    id="legacy-producer-bam",
                    filename=legacy_bam.name,
                    file_type="bam",
                    directory=str(legacy_root),
                    size_bytes=legacy_bam.stat().st_size,
                ),
            ]
        )
        await session.flush()

        known = await service.register_move_source(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            input_file_id="known-producer-bam",
            molecule_type="dna",
            source_job_id="known-producer-job",
            external_registration_receipt_id=None,
            source_runtime_identity=None,
        )
        legacy = await service.register_move_source(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            input_file_id="legacy-producer-bam",
            molecule_type="dna",
            source_job_id="legacy-producer-job",
            external_registration_receipt_id=None,
            source_runtime_identity=None,
        )

        assert known["source_runtime_identity"] == {
            "schema": "bms.ont-move-source-producer-runtime.v1",
            "authority_state": "known",
            "source_job_id": "known-producer-job",
            "source_bam_sha256": known_sha256,
            "runtime_provenance_sha256": runtime_receipt_sha256,
            "runtime_sha256": "4" * 64,
            "basecall_model_id": MODEL_ID,
            "emit_moves": True,
            "read_count": 2,
            "read_inventory_sha256": READ_INVENTORY_SHA256,
            "move_tag_counts": {"mv": 2, "ts": 2, "ns": 2},
        }
        assert legacy["source_runtime_identity"] == {
            "schema": "bms.ont-move-source-producer-runtime.v1",
            "authority_state": "legacy_unknown",
            "source_job_id": "legacy-producer-job",
            "source_bam_sha256": hashlib.sha256(legacy_bam.read_bytes()).hexdigest(),
            "reason_code": "producer_runtime_provenance_unavailable",
            "requires_independent_move_validation": True,
        }
        runtime_path.write_text("[]", encoding="utf-8")
        known_job = await session.get(Job, "known-producer-job")
        assert known_job is not None
        malformed_sha256 = hashlib.sha256(runtime_path.read_bytes()).hexdigest()
        anchor = dict(known_job.provenance["ont_dorado_terminal_products"])
        products = dict(anchor["products"])
        products["dorado_runtime_provenance"] = {
            **products["dorado_runtime_provenance"],
            "sha256": malformed_sha256,
        }
        known_job.provenance = {
            **known_job.provenance,
            "ont_dorado_terminal_products": {**anchor, "products": products},
        }
        with pytest.raises(service.OntSignalError, match="producer runtime provenance is malformed"):
            service._derive_source_runtime_identity(known_job, known_sha256)

        with pytest.raises(service.OntSignalError, match="caller-supplied runtime identity"):
            await service.register_move_source(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                input_file_id="known-producer-bam",
                molecule_type="dna",
                source_job_id="known-producer-job",
                external_registration_receipt_id=None,
                source_runtime_identity={"tool": "self-attested"},
            )


def test_move_worker_publication_requires_exact_known_producer_authority() -> None:
    validator = getattr(OntSignalWorker, "_validate_move_source_producer_authority", None)
    assert callable(validator), "move publication lacks producer-runtime authority validation"
    report = {
        "basecall_model_id": MODEL_ID,
        "record_count": 2,
        "unique_read_count": 2,
        "tag_counts": {"mv": 2, "ts": 2, "ns": 2},
        "read_inventory_sha256": READ_INVENTORY_SHA256,
    }
    known = OntMoveTableSource(
        id="known-worker-source",
        run_id="run-1",
        observed_generation=1,
        raw_representation_id="raw-blow5-1",
        input_file_id="known-producer-bam",
        source_job_id="known-producer-job",
        artifact_sha256="5" * 64,
        artifact_size_bytes=10,
        molecule_type="dna",
        source_runtime_identity={
            "schema": "bms.ont-move-source-producer-runtime.v1",
            "authority_state": "known",
            "source_job_id": "known-producer-job",
            "source_bam_sha256": "5" * 64,
            "runtime_provenance_sha256": "6" * 64,
            "runtime_sha256": "7" * 64,
            "basecall_model_id": MODEL_ID,
            "emit_moves": True,
            "read_count": 2,
            "read_inventory_sha256": READ_INVENTORY_SHA256,
            "move_tag_counts": {"mv": 2, "ts": 2, "ns": 2},
        },
        validation_state="running",
        reason_code="worker_claimed",
        validation_receipt={},
    )
    assert validator(known, report) == {
        "authority_state": "known",
        "basecall_model_id": MODEL_ID,
        "emit_moves": True,
        "independent_move_validation": True,
    }

    known.source_runtime_identity = {**known.source_runtime_identity, "emit_moves": False}
    with pytest.raises(RuntimeError, match="emit-moves"):
        validator(known, report)
    known.source_runtime_identity = {
        **known.source_runtime_identity,
        "emit_moves": True,
        "basecall_model_id": "different-model",
    }
    with pytest.raises(RuntimeError, match="basecall model"):
        validator(known, report)

    missing = OntMoveTableSource(
        id="missing-worker-source",
        run_id="run-1",
        observed_generation=1,
        raw_representation_id="raw-blow5-1",
        input_file_id="missing-producer-bam",
        source_job_id="missing-producer-job",
        artifact_sha256="8" * 64,
        artifact_size_bytes=10,
        molecule_type="dna",
        source_runtime_identity={
            "schema": "bms.ont-move-source-producer-runtime.v1",
            "authority_state": "legacy_unknown",
            "source_job_id": "missing-producer-job",
            "source_bam_sha256": "8" * 64,
            "reason_code": "producer_runtime_provenance_unavailable",
            "requires_independent_move_validation": True,
        },
        validation_state="running",
        reason_code="worker_claimed",
        validation_receipt={},
    )
    assert validator(missing, report) == {
        "authority_state": "legacy_unknown",
        "basecall_model_id": MODEL_ID,
        "emit_moves": "validated_from_bam_tags",
        "independent_move_validation": True,
    }


@pytest.mark.asyncio
async def test_move_source_runtime_failures_are_boundedly_rerequested_before_terminal_failure(
    workbench_store: WorkbenchStore,
) -> None:
    worker = OntSignalWorker(workbench_store.factory, workbench_store.factory)
    async with workbench_store.factory() as session:
        source = await session.get(OntMoveTableSource, "ont-moves-ready")
        assert source is not None
        source.validation_state = "running"
        source.reason_code = "worker_claimed"
        source.claim_token = "move-attempt-1"
        source.lease_expires_at = datetime.utcnow() + timedelta(minutes=5)
        source.validated_at = None
        source.validation_receipt = {
            "raw_manifest_sha256": "a" * 64,
            "retry": {"max_attempts": 3, "failures": []},
        }
        await session.commit()

    for attempt in (1, 2):
        token = f"move-attempt-{attempt}"
        await worker._fail(
            OntMoveTableSource,
            "validation_state",
            "ont-moves-ready",
            token,
            RuntimeError(f"transient move validation failure {attempt}"),
        )
        async with workbench_store.factory() as session:
            source = await session.get(OntMoveTableSource, "ont-moves-ready")
            assert source is not None
            assert source.validation_state == "requested"
            assert source.reason_code == "move_source_retry_requested"
            retry = source.validation_receipt["retry"]
            assert retry["max_attempts"] == 3
            assert len(retry["failures"]) == attempt
        claimed = await worker._claim(OntMoveTableSource, "validation_state")
        assert claimed is not None
        item_id, next_token = claimed
        assert item_id == "ont-moves-ready"
        async with workbench_store.factory() as session:
            source = await session.get(OntMoveTableSource, item_id)
            assert source is not None
            source.claim_token = f"move-attempt-{attempt + 1}"
            await session.commit()

    await worker._fail(
        OntMoveTableSource,
        "validation_state",
        "ont-moves-ready",
        "move-attempt-3",
        RuntimeError("terminal move validation failure"),
    )
    async with workbench_store.factory() as session:
        source = await session.get(OntMoveTableSource, "ont-moves-ready")
        assert source is not None
        assert source.validation_state == "failed"
        assert source.reason_code == "runtime_validation_failed_retry_exhausted"
        assert len(source.validation_receipt["retry"]["failures"]) == 3
        assert source.validated_at is not None


def _assert_public_value_has_no_paths(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = key.lower()
            assert "path" not in normalized
            assert "directory" not in normalized
            assert "filename" not in normalized
            assert normalized != "managed_outputs"
            _assert_public_value_has_no_paths(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_public_value_has_no_paths(nested)
    elif isinstance(value, str):
        if value.startswith(("/api/", "/ngs?")):
            return
        assert not value.startswith("/")
        assert not re.match(r"^[A-Za-z]:[\\/]", value)
        assert not value.startswith("file://")
        assert not re.fullmatch(
            r"[^/\\\s]+\.(?:bam|ubam|bai|blow5|slow5|pod5|fast5|fastq|fq|fasta|fa|paf|gz|tbi|txt|json|html|svg|bed|csv|tsv|log)",
            value,
            re.IGNORECASE,
        )


def test_recursive_public_sanitization_redacts_paths_embedded_in_prose() -> None:
    value = service._public_json(
        {
            "reason_code": "runtime_validation_failed",
            "failure": (
                "runtime produced unexpected output files: secret.txt, nested/reads.bam "
                "under /srv/private/run while preserving count 12 and ID job-123"
            ),
            "nested": [
                "opened C:\\private\\reads.blow5",
                "hash 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            ],
        }
    )

    rendered = json.dumps(value, sort_keys=True)
    assert value["reason_code"] == "runtime_validation_failed"
    assert "runtime produced unexpected output files" in value["failure"]
    assert "count 12" in value["failure"]
    assert "job-123" in value["failure"]
    assert "[redacted-path]" in rendered
    for secret in (
        "secret.txt",
        "nested/reads.bam",
        "/srv/private/run",
        "C:\\private\\reads.blow5",
    ):
        assert secret not in rendered


@pytest.mark.asyncio
async def test_all_public_workbench_responses_recursively_remove_host_paths(
    workbench_store: WorkbenchStore,
) -> None:
    host_path = str(workbench_store.root / "secret-host-directory" / "secret.file")
    async with workbench_store.factory() as session:
        profile_public = await _create_calibrated_profile(session)
        profile = await session.get(OntSignalMappingProfile, profile_public["mapping_profile_id"])
        calibration = await session.get(OntSignalCalibrationArtifact, profile.calibration_artifact_id)
        source = await session.get(OntMoveTableSource, "ont-moves-ready")
        assert profile is not None and calibration is not None and source is not None
        calibration.runtime_identity = {
            "tool": "squigualiser",
            "nested": {"output_path": host_path, "display_name": "secret.bam"},
        }
        calibration.score_evidence = [{"score": 1.0, "filename": "secret.file"}]
        profile.approval_receipt = {**profile.approval_receipt, "review_directory": host_path}
        source.source_runtime_identity = {"tool": "dorado", "host": {"working_directory": host_path}}
        source.validation_receipt = {
            **source.validation_receipt,
            "reason_evidence": {"count": 2, "message": "validated"},
            "managed_outputs": {"filtered_move_bam": host_path},
        }
        mapping_public = await service.create_mapping_job(
            session,
            session,
            mode="signal_to_read",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=profile.id,
            reference_revision_id=None,
            alignment_job_id=None,
            alignment_session_id=None,
        )
        mapping = await session.get(OntSignalMappingJob, mapping_public["mapping_job_id"])
        assert mapping is not None
        mapping.resource_snapshot = {**mapping.resource_snapshot, "nested": {"host_path": host_path}}
        mapping.stage_receipts = {**mapping.stage_receipts, "runtime": {"output_directory": host_path, "count": 3}}
        mapping.state = "ready"
        mapping.reason_code = "validated_signal_to_read_mapping_ready"
        mapping.completed_at = datetime.utcnow()
        mapping_artifact = OntSignalMappingArtifact(
            id="mapping-artifact-public-path-test",
            mapping_job_id=mapping.id,
            kind="reform_paf",
            managed_relative_path="ont_signal_workbench/mappings/internal/reform.paf.gz",
            media_type="application/gzip",
            sha256="d" * 64,
            size_bytes=123,
            parent_identities={"input_path": host_path, "read_count": 2},
            runtime_identity={"work_directory": host_path, "tool": "squigualiser"},
            validation_receipt={"managed_output_path": host_path, "record_count": 2},
            created_at=datetime.utcnow(),
        )
        session.add(mapping_artifact)
        view = OntSquigualiserViewJob(
            id="ont-squig-view-public-path-test",
            mapping_artifact_id=mapping_artifact.id,
            mode="read",
            read_id="read-1",
            render_params={"strand": "forward"},
            request_fingerprint="e" * 64,
            state="ready",
            reason_code="bounded_squigualiser_view_ready",
            output_manifest={
                "artifacts": [{
                    "artifact_id": "view-html",
                    "managed_relative_path": "ont_signal_workbench/views/internal/view.html",
                    "filename": "view.html",
                    "sha256": "f" * 64,
                    "size_bytes": 12,
                    "media_type": "text/html",
                    "host_path": host_path,
                }],
                "output_directory": host_path,
            },
            render_receipt={"request_identity_sha256": "e" * 64, "runtime": {"path": host_path}},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        viewer = OntSignalViewerSession(
            id="ont-viewer-public-path-test",
            dataset_id="dataset-1",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id=source.id,
            mapping_profile_id=profile.id,
            igv_state={
                "alignment_display_mode": "FULL",
                "alignment_color_by": "strand",
                "alignment_group_by": "none",
                "reads_track_loaded": True,
                "locus": "chr1:1-2",
            },
            signal_state={
                "mode": "raw_waveform",
                "render_params": router.RenderParams().model_dump(),
                "view_job_id": None,
                "read_mapping_job_id": None,
                "reference_mapping_job_id": None,
            },
            revision=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add_all([view, viewer])
        await session.commit()

    app = _api(workbench_store.factory)
    urls = [
        "/api/ont/signal-workbench/runs/run-1/generations/1/move-sources",
        "/api/ont/signal-workbench/calibrations",
        "/api/ont/signal-workbench/mapping-profiles",
        f"/api/ont/signal-workbench/mappings/{mapping.id}",
        f"/api/ont/signal-workbench/views/{view.id}",
        f"/api/ont/signal-workbench/viewer-sessions/{viewer.id}",
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        responses = [await client.get(url) for url in urls]
    for response in responses:
        assert response.status_code == 200, response.text
        assert str(workbench_store.root) not in response.text
        _assert_public_value_has_no_paths(response.json())
    mapping_body = responses[3].json()
    assert mapping_body["reason_code"] == "validated_signal_to_read_mapping_ready"
    assert mapping_body["artifacts"][0]["sha256"] == "d" * 64
    assert mapping_body["artifacts"][0]["validation_receipt"]["record_count"] == 2


@pytest.mark.asyncio
async def test_v1_profiles_and_capabilities_are_calibrated_only(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        artifact = await _publish_calibration_evidence(session)
        with pytest.raises(service.OntSignalError, match="approved calibration"):
            await service.create_mapping_profile(
                session,
                name="unproven upstream profile",
                molecule_type="dna",
                basecall_model_id=MODEL_ID,
                kmer_length=5,
                signal_move_offset=4,
                parameter_source="exact_upstream_profile",
                calibration_artifact_id=None,
                minimum_mapq=0,
                read_set_selection="immutable_full_set",
                approval_receipt={"approved": True},
                approved_by="operator@example.test",
            )
        profile = await service.create_mapping_profile(
            session,
            name="governed calibrated profile",
            molecule_type="dna",
            basecall_model_id=MODEL_ID,
            kmer_length=artifact.recommended_kmer_length,
            signal_move_offset=artifact.recommended_signal_move_offset,
            parameter_source="approved_calibration",
            calibration_artifact_id=artifact.id,
            minimum_mapq=0,
            read_set_selection="immutable_full_set",
            approval_receipt={
                "approved": True,
                "calibration_artifact_sha256": artifact.artifact_sha256,
            },
            approved_by=None,
        )
        await session.commit()
        capabilities = await service.workbench_capabilities(session, run_id="run-1", observed_generation=1)
    assert profile["parameter_source"] == "approved_calibration"
    assert capabilities["resolved"]["mapping_profile_id"] == profile["mapping_profile_id"]

    app = _api(workbench_store.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(
            "/api/ont/signal-workbench/mapping-profiles",
            json={
                "name": "unproven upstream profile",
                "molecule_type": "dna",
                "basecall_model_id": MODEL_ID,
                "kmer_length": 5,
                "signal_move_offset": 4,
                "parameter_source": "exact_upstream_profile",
                "calibration_artifact_id": None,
                "approval_receipt": {"approved": True},
            },
        )
    assert rejected.status_code == 422


async def _seed_ready_mapping_artifact(
    session: AsyncSession,
    *,
    artifact_id: str,
    mode: str,
) -> OntSignalMappingArtifact:
    profile = await _create_calibrated_profile(session)
    mapping = OntSignalMappingJob(
        id=f"mapping-{artifact_id}",
        mode=mode,
        run_id="run-1",
        observed_generation=1,
        raw_representation_id="raw-blow5-1",
        move_source_id="ont-moves-ready",
        mapping_profile_id=profile["mapping_profile_id"],
        reference_revision_id=(
            "reference-revision-1" if mode == "signal_to_reference" else None
        ),
        request_fingerprint=hashlib.sha256(artifact_id.encode()).hexdigest(),
        state="ready",
        reason_code=f"validated_{mode}_mapping_ready",
        resource_snapshot={},
        stage_receipts={},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
    )
    payload = f"bytes-{artifact_id}".encode()
    artifact = OntSignalMappingArtifact(
        id=artifact_id,
        mapping_job_id=mapping.id,
        kind="realign_paf" if mode == "signal_to_reference" else "reform_paf",
        managed_relative_path=f"ont_signal_workbench/mappings/{artifact_id}/mapping.paf",
        media_type="text/plain",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        parent_identities={},
        runtime_identity={},
        validation_receipt={"record_count": 2},
        created_at=datetime.utcnow(),
    )
    session.add_all([mapping, artifact])
    await session.flush()
    return artifact


@pytest.mark.asyncio
async def test_view_creation_rejects_pinned_runtime_incompatible_mode_flags(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        artifact = await _seed_ready_mapping_artifact(
            session,
            artifact_id="mapping-artifact-pileup-policy",
            mode="signal_to_reference",
        )
        with pytest.raises(service.OntSignalError, match="pileup.*loose"):
            await service.create_view_job(
                session,
                mapping_artifact_id=artifact.id,
                mode="pileup",
                read_id=None,
                reference_contig="chr1",
                reference_start=1,
                reference_end=100,
                render_params={"loose_bound": True},
            )


def test_signal_to_reference_admission_accepts_only_primary_alignment_sessions() -> None:
    validator = getattr(service, "_require_primary_alignment_session", None)
    assert callable(validator), "signal-to-reference admission lacks alignment-mode validation"
    validated = validator({"ready": True, "mode": "primary", "artifacts": {}})
    assert isinstance(validated, dict)
    assert validated["mode"] == "primary"
    with pytest.raises(service.OntSignalError, match="primary alignment session"):
        validator({"ready": True, "mode": "dimer_candidates", "artifacts": {}})


@pytest.mark.asyncio
async def test_read_view_admission_requires_signal_to_read_reform_artifact(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        reference_artifact = await _seed_ready_mapping_artifact(
            session,
            artifact_id="mapping-artifact-read-reject-reference",
            mode="signal_to_reference",
        )
        with pytest.raises(service.OntSignalError, match="signal-to-read reform"):
            await service.create_view_job(
                session,
                mapping_artifact_id=reference_artifact.id,
                mode="read",
                read_id="read-1",
                reference_contig=None,
                reference_start=None,
                reference_end=None,
                render_params={},
            )

        reference_mapping = await session.get(
            OntSignalMappingJob, reference_artifact.mapping_job_id
        )
        assert reference_mapping is not None
        reference_mapping.mode = "signal_to_read"
        await session.flush()
        with pytest.raises(service.OntSignalError, match="signal-to-read reform"):
            await service.create_view_job(
                session,
                mapping_artifact_id=reference_artifact.id,
                mode="read",
                read_id="read-1",
                reference_contig=None,
                reference_start=None,
                reference_end=None,
                render_params={},
            )


@pytest.mark.asyncio
async def test_registration_viewer_and_worker_full_file_work_are_offloaded(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_calls: list[str] = []

    async def service_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        service_calls.append(function.__name__)
        return function(*args, **kwargs)

    monkeypatch.setattr(service.asyncio, "to_thread", service_to_thread)
    owned = workbench_store.root / "offloaded-registration"
    owned.mkdir()
    bam = owned / "moves.bam"
    bam.write_bytes(b"offloaded-registration-bam")
    monkeypatch.setattr(service, "get_allowed_roots", lambda: {"results": workbench_store.root})
    monkeypatch.setattr(
        service.ngs_alignment_sessions,
        "resolve_alignment_session",
        lambda *_args, **_kwargs: {"ready": True, "mode": "primary", "artifacts": []},
    )
    async with workbench_store.factory() as session:
        session.add_all([
            Job(
                id="offloaded-source-job",
                name="offloaded source",
                status="completed",
                model_id="legacy",
                mode="basecall",
                params={},
                output_dir=str(owned),
            ),
            InputFile(
                id="offloaded-source-input",
                filename=bam.name,
                file_type="bam",
                directory=str(owned),
                size_bytes=bam.stat().st_size,
            ),
            Job(
                id="offloaded-viewer-alignment",
                name="offloaded viewer alignment",
                status="completed",
                model_id="ont_alignment",
                mode="alignment",
                params={
                    "dataset_id": "dataset-offloaded",
                    "source_instrument_run_id": "run-1",
                    "source_instrument_observed_generation": 1,
                    "ngs_reference_revision_id": "reference-offloaded",
                    "reference_sequence_sha256": "a" * 64,
                    "ont_workflow_id": "ont_alignment",
                    "ont_input_mode": "fastq",
                },
                output_dir=str(workbench_store.root),
            ),
        ])
        await session.commit()
        await service.register_move_source(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            input_file_id="offloaded-source-input",
            molecule_type="dna",
            source_job_id="offloaded-source-job",
            external_registration_receipt_id=None,
            source_runtime_identity=None,
        )
        await service.create_viewer_session(
            session,
            dataset_id="dataset-offloaded",
            run_id="run-1",
            observed_generation=1,
            alignment_job_id="offloaded-viewer-alignment",
            alignment_session_id="primary",
            reference_revision_id="reference-offloaded",
            contig="chr1",
            locus_start=1,
            locus_end=2,
            selected_read_id="read-1",
            **_viewer_create_states(),
        )
    assert "_hash_job_owned_input" in service_calls
    assert "<lambda>" in service_calls

    worker_calls: list[str] = []

    async def worker_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        worker_calls.append(function.__name__)
        return function(*args, **kwargs)

    monkeypatch.setattr(worker_service.asyncio, "to_thread", worker_to_thread)
    monkeypatch.setattr(
        worker_service.ngs_alignment_sessions,
        "resolve_session_alignment_bundle",
        lambda *_args, **_kwargs: (Path("alignment.bam"), {}, Path("alignment.bam.bai"), {}),
    )
    worker = OntSignalWorker(None, None)
    hashed = tmp_path / "hashed.bin"
    hashed.write_bytes(b"hash-me-off-thread")
    digest, size = await worker._stable_file_identity_async(hashed)
    assert digest == hashlib.sha256(hashed.read_bytes()).hexdigest()
    assert size == hashed.stat().st_size
    await worker._resolve_session_alignment_bundle_async(
        "alignment-job",
        "primary",
        {"source_reference_sha256": "a" * 64, "workflow_id": "ont_alignment", "input_mode": "fastq"},
        str(tmp_path),
    )
    with RetainedParentSet((tmp_path,)) as parents:
        retained = await worker._pin_parent_async(
            parents,
            hashed,
            alias="hashed.bin",
            expected_sha256=digest,
            expected_size=size,
        )
        assert retained.sha256 == digest
    assert worker_calls == ["_stable_file_identity", "<lambda>", "_identity_from_descriptor"]


@pytest.mark.asyncio
async def test_alignment_resolution_and_view_artifact_read_hash_are_offloaded(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = getattr(service, "_resolve_primary_alignment_session_async", None)
    assert callable(resolver), "mapping admission lacks an async alignment-resolution offload"
    calls: list[str] = []

    async def fake_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(function.__name__)
        return function(*args, **kwargs)

    monkeypatch.setattr(service.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        service.ngs_alignment_sessions,
        "resolve_alignment_session",
        lambda *_args, **_kwargs: {"ready": True, "mode": "primary", "artifacts": {}},
    )
    alignment = await cast(Any, resolver)(
        "alignment-job",
        "alignment-session",
        {
            "source_reference_sha256": "a" * 64,
            "workflow_id": "ont_alignment",
            "input_mode": "fastq",
        },
        str(workbench_store.root),
    )
    assert alignment["mode"] == "primary"

    async with workbench_store.factory() as session:
        mapping_artifact = await _seed_ready_mapping_artifact(
            session,
            artifact_id="mapping-artifact-offloaded-serve",
            mode="signal_to_read",
        )
        session.add(
            OntSquigualiserViewJob(
                id="view-offloaded-serve",
                mapping_artifact_id=mapping_artifact.id,
                mode="read",
                read_id="read-1",
                render_params={},
                request_fingerprint="4" * 64,
                state="ready",
                reason_code="bounded_squigualiser_view_ready",
                output_manifest={
                    "artifacts": [{
                        "artifact_id": "artifact-offloaded-serve",
                        "managed_relative_path": "views/offloaded/view.html",
                        "sha256": "5" * 64,
                        "size_bytes": 7,
                        "media_type": "text/html",
                    }]
                },
                render_receipt={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
        )
        await session.commit()

        monkeypatch.setattr(service, "_read_verified_view_artifact", lambda _item: b"offload")
        payload, _metadata = await service.resolve_view_artifact(
            session, "view-offloaded-serve", "artifact-offloaded-serve"
        )

    assert payload == b"offload"
    assert calls == ["<lambda>", "<lambda>"]


@pytest.mark.asyncio
async def test_managed_bed_is_completed_job_owned_and_byte_bound_to_view_identity(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = workbench_store.root / "approved"
    job_root = approved / "bed-job"
    job_root.mkdir(parents=True)
    bed_path = job_root / "regions.bed"
    bed_path.write_text("chr1\t1\t2\n", encoding="utf-8")
    monkeypatch.setattr(service, "get_allowed_roots", lambda: {"results": approved})

    async with workbench_store.factory() as session:
        artifact = await _seed_ready_mapping_artifact(
            session,
            artifact_id="mapping-artifact-bed-parent",
            mode="signal_to_read",
        )
        session.add_all(
            [
                Job(
                    id="completed-bed-job",
                    name="completed BED producer",
                    status="completed",
                    model_id="bed-producer",
                    mode="analysis",
                    params={},
                    output_dir=str(job_root),
                ),
                InputFile(
                    id="managed-bed-1",
                    filename=bed_path.name,
                    file_type="bed",
                    directory=str(job_root),
                    size_bytes=bed_path.stat().st_size,
                ),
            ]
        )
        await session.flush()
        first = await service.create_view_job(
            session,
            mapping_artifact_id=artifact.id,
            mode="read",
            read_id="read-1",
            reference_contig=None,
            reference_start=None,
            reference_end=None,
            render_params={"managed_bed_artifact_id": "managed-bed-1"},
        )
        first_row = await session.get(OntSquigualiserViewJob, first["view_job_id"])
        assert first_row is not None
        expected = hashlib.sha256(bed_path.read_bytes()).hexdigest()
        assert first_row.render_params["managed_bed_sha256"] == expected
        assert first_row.render_params["managed_bed_size_bytes"] == bed_path.stat().st_size
        assert first_row.render_params["managed_bed_source_job_id"] == "completed-bed-job"
        await OntSignalWorker._verify_managed_bed_parent(session, first_row.render_params)

        bed_path.write_text("chr1\t3\t4\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="managed BED.*immutable parent"):
            await OntSignalWorker._verify_managed_bed_parent(session, first_row.render_params)

        second = await service.create_view_job(
            session,
            mapping_artifact_id=artifact.id,
            mode="read",
            read_id="read-1",
            reference_contig=None,
            reference_start=None,
            reference_end=None,
            render_params={"managed_bed_artifact_id": "managed-bed-1"},
        )
        assert second["request_fingerprint"] != first["request_fingerprint"]


@pytest.mark.asyncio
async def test_viewer_session_rejects_incoherent_dataset_run_alignment_authority(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service.ngs_alignment_sessions,
        "resolve_alignment_session",
        lambda *_args, **_kwargs: {"ready": True, "artifacts": []},
    )
    async with workbench_store.factory() as session:
        session.add(
            Job(
                id="alignment-authority-job",
                name="governed alignment",
                status="completed",
                model_id="ont_alignment",
                mode="alignment",
                params={
                    "dataset_id": "dataset-authoritative",
                    "source_instrument_run_id": "run-1",
                    "source_instrument_observed_generation": 1,
                    "ngs_reference_revision_id": "reference-revision-1",
                    "reference_sequence_sha256": "a" * 64,
                    "ont_workflow_id": "ont_alignment",
                    "ont_input_mode": "fastq",
                },
                output_dir=str(workbench_store.root),
            )
        )
        await session.flush()

        with pytest.raises(service.OntSignalError, match="dataset.*alignment authority"):
            await service.create_viewer_session(
                session,
                dataset_id="dataset-forged",
                run_id="run-1",
                observed_generation=1,
                alignment_job_id="alignment-authority-job",
                alignment_session_id="primary",
                reference_revision_id="reference-revision-1",
                contig="chr1",
                locus_start=1,
                locus_end=10,
                selected_read_id="read-1",
                **_viewer_create_states(),
            )


@pytest.mark.asyncio
async def test_html_artifact_serving_accepts_the_48_mib_render_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results_root = tmp_path / "results"
    artifact_path = (
        results_root / "ont_signal_workbench" / "views" / "large" / "view.html"
    )
    artifact_path.parent.mkdir(parents=True)
    payload = b"x" * (9 * 1024 * 1024)
    artifact_path.write_bytes(payload)
    monkeypatch.setattr(service, "get_results_dir", lambda: results_root)

    assert service._read_verified_view_artifact(
        {
            "managed_relative_path": "views/large/view.html",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "media_type": "text/html",
        }
    ) == payload


@pytest.mark.asyncio
async def test_artifact_route_returns_the_exact_bounded_verified_descriptor_bytes(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results_root = workbench_store.root / "served-results"
    artifact_path = results_root / "ont_signal_workbench" / "views" / "served" / "view.html"
    artifact_path.parent.mkdir(parents=True)
    payload = b"<html><head></head><body>governed</body></html>"
    artifact_path.write_bytes(payload)
    monkeypatch.setattr(service, "get_results_dir", lambda: results_root)

    async with workbench_store.factory() as session:
        mapping_artifact = await _seed_ready_mapping_artifact(
            session,
            artifact_id="mapping-artifact-served-bytes",
            mode="signal_to_read",
        )
        session.add(
            OntSquigualiserViewJob(
                id="view-served-bytes",
                mapping_artifact_id=mapping_artifact.id,
                mode="read",
                read_id="read-1",
                render_params={},
                request_fingerprint="f" * 64,
                state="ready",
                reason_code="bounded_squigualiser_view_ready",
                output_manifest={
                    "artifacts": [
                        {
                            "artifact_id": "artifact-served-bytes",
                            "managed_relative_path": "views/served/view.html",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                            "size_bytes": len(payload),
                            "media_type": "text/html",
                        }
                    ]
                },
                render_receipt={},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )
        )
        await session.commit()

    def forbidden_reopen(_path: Path) -> bytes:
        raise AssertionError("artifact route reopened a verified path")

    monkeypatch.setattr(Path, "read_bytes", forbidden_reopen)
    app = _api(workbench_store.factory)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/ont/signal-workbench/views/view-served-bytes/artifacts/artifact-served-bytes"
        )

    assert response.status_code == 200
    assert response.content == payload
    assert "default-src 'none'" in response.headers["content-security-policy"]


@pytest.mark.asyncio
async def test_concurrent_mapping_and_view_creation_replay_one_fingerprint(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        profile = await _create_calibrated_profile(session)
        await session.commit()

    async def create_mapping() -> dict[str, Any]:
        async with workbench_store.factory() as session:
            result = await service.create_mapping_job(
                session,
                session,
                mode="signal_to_read",
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                move_source_id="ont-moves-ready",
                mapping_profile_id=profile["mapping_profile_id"],
                reference_revision_id=None,
                alignment_job_id=None,
                alignment_session_id=None,
            )
            await session.commit()
            return result

    mapping_a, mapping_b = await asyncio.gather(create_mapping(), create_mapping())
    assert mapping_a["mapping_job_id"] == mapping_b["mapping_job_id"]
    assert mapping_a["request_fingerprint"] == mapping_b["request_fingerprint"]
    assert re.fullmatch(r"[0-9a-f]{64}", mapping_a["request_fingerprint"])

    async with workbench_store.factory() as session:
        assert await session.scalar(select(func.count()).select_from(OntSignalMappingJob)) == 1
        assert await session.scalar(select(func.count()).select_from(OntSignalMappingEvent)) == 1
        mapping = await session.get(OntSignalMappingJob, mapping_a["mapping_job_id"])
        assert mapping is not None
        mapping.state = "ready"
        mapping.reason_code = "validated_signal_to_read_mapping_ready"
        mapping.completed_at = datetime.utcnow()
        artifact = OntSignalMappingArtifact(
            id="mapping-artifact-concurrent-view",
            mapping_job_id=mapping.id,
            kind="reform_paf",
            managed_relative_path="ont_signal_workbench/mappings/concurrent/reform.paf.gz",
            media_type="application/gzip",
            sha256="9" * 64,
            size_bytes=10,
            parent_identities={},
            runtime_identity={},
            validation_receipt={"record_count": 2},
            created_at=datetime.utcnow(),
        )
        session.add(artifact)
        await session.commit()

    async def create_view() -> dict[str, Any]:
        async with workbench_store.factory() as session:
            result = await service.create_view_job(
                session,
                mapping_artifact_id="mapping-artifact-concurrent-view",
                mode="read",
                read_id="read-1",
                reference_contig=None,
                reference_start=None,
                reference_end=None,
                render_params={},
            )
            await session.commit()
            return result

    view_a, view_b = await asyncio.gather(create_view(), create_view())
    assert view_a["view_job_id"] == view_b["view_job_id"]
    assert view_a["request_fingerprint"] == view_b["request_fingerprint"]
    async with workbench_store.factory() as session:
        assert await session.scalar(select(func.count()).select_from(OntSquigualiserViewJob)) == 1


@pytest.mark.asyncio
async def test_mapping_and_view_cancellation_are_guarded_explicit_and_idempotent(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        profile = await _create_calibrated_profile(session)
        mapping_public = await service.create_mapping_job(
            session,
            session,
            mode="signal_to_read",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=profile["mapping_profile_id"],
            reference_revision_id=None,
            alignment_job_id=None,
            alignment_session_id=None,
        )
        mapping_id = mapping_public["mapping_job_id"]
        first = await service.cancel_mapping_job(session, mapping_id)
        second = await service.cancel_mapping_job(session, mapping_id)
        await session.commit()
        assert first == second
        assert first["state"] == "cancelled"
        assert first["reason_code"] == "cancelled_before_claim"
        assert first["completed_at"] is not None
        assert first["stage_receipts"]["cancellation"]["disposition"] == "cancelled_before_claim"

        running_mapping = OntSignalMappingJob(
            id="ont-signal-map-running-cancel",
            mode="signal_to_read",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=profile["mapping_profile_id"],
            request_fingerprint="7" * 64,
            state="running",
            reason_code="claimed",
            claim_token="mapping-lease-token",
            lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
            resource_snapshot={},
            stage_receipts={"request_identity_sha256": "7" * 64},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(running_mapping)
        await session.commit()
        running_first = await service.cancel_mapping_job(session, running_mapping.id)
        running_second = await service.cancel_mapping_job(session, running_mapping.id)
        await session.commit()
        assert running_first == running_second
        assert running_first["state"] == "running"
        assert running_first["reason_code"] == "cancellation_requested"
        persisted_mapping = await session.get(OntSignalMappingJob, running_mapping.id)
        assert persisted_mapping.claim_token == "mapping-lease-token"
        assert persisted_mapping.lease_expires_at is not None

        mapping_ready = await session.get(OntSignalMappingJob, mapping_id)
        mapping_ready.state = "ready"
        mapping_ready.reason_code = "validated_signal_to_read_mapping_ready"
        mapping_ready.completed_at = datetime.utcnow()
        artifact = OntSignalMappingArtifact(
            id="mapping-artifact-cancellation-view",
            mapping_job_id=mapping_ready.id,
            kind="reform_paf",
            managed_relative_path="ont_signal_workbench/mappings/cancel/reform.paf.gz",
            media_type="application/gzip",
            sha256="8" * 64,
            size_bytes=10,
            parent_identities={},
            runtime_identity={},
            validation_receipt={"record_count": 2},
            created_at=datetime.utcnow(),
        )
        session.add(artifact)
        await session.commit()
        view = await service.create_view_job(
            session,
            mapping_artifact_id=artifact.id,
            mode="read",
            read_id="read-1",
            reference_contig=None,
            reference_start=None,
            reference_end=None,
            render_params={},
        )
        view_first = await service.cancel_view_job(session, view["view_job_id"])
        view_second = await service.cancel_view_job(session, view["view_job_id"])
        await session.commit()
        assert view_first == view_second
        assert view_first["state"] == "cancelled"
        assert view_first["reason_code"] == "cancelled_before_claim"

        running_view = OntSquigualiserViewJob(
            id="ont-squig-view-running-cancel",
            mapping_artifact_id=artifact.id,
            mode="read",
            read_id="read-2",
            render_params={},
            request_fingerprint="6" * 64,
            state="running",
            reason_code="claimed",
            claim_token="view-lease-token",
            lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
            output_manifest={},
            render_receipt={"request_identity_sha256": "6" * 64},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(running_view)
        await session.commit()
        running_view_first = await service.cancel_view_job(session, running_view.id)
        running_view_second = await service.cancel_view_job(session, running_view.id)
        await session.commit()
        assert running_view_first == running_view_second
        assert running_view_first["state"] == "running"
        persisted_view = await session.get(OntSquigualiserViewJob, running_view.id)
        assert persisted_view.claim_token == "view-lease-token"
        assert persisted_view.lease_expires_at is not None


def test_migration_triggers_enforce_immutable_profiles_requests_outputs_and_receipts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "immutability.db"
    _bootstrap_migration_parents(db_path)
    migrate(str(db_path))
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO ont_instrument_runs(id) VALUES ('run-1')")
        connection.execute("INSERT INTO ont_raw_signal_representations(id) VALUES ('raw-1')")
        connection.execute("INSERT INTO input_files(id) VALUES ('input-1')")
        connection.execute(
            """INSERT INTO ont_move_table_sources(
                id,run_id,observed_generation,raw_representation_id,input_file_id,
                artifact_sha256,artifact_size_bytes,molecule_type,source_runtime_identity,
                validation_state,reason_code,validation_receipt,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("move-1", "run-1", 1, "raw-1", "input-1", "a" * 64, 10, "dna", "{}", "ready", "ready", "{}", now),
        )
        connection.execute(
            """INSERT INTO ont_signal_calibration_artifacts(
                id,raw_representation_id,move_source_id,basecall_model_id,sample_selection,
                recommended_kmer_length,recommended_signal_move_offset,score_evidence,
                runtime_identity,parent_sha256s,artifact_sha256,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("cal-1", "raw-1", "move-1", MODEL_ID, "{}", 5, 4, "[]", "{}", "{}", "b" * 64, now),
        )
        connection.execute(
            """INSERT INTO ont_signal_mapping_profiles(
                id,name,molecule_type,basecall_model_id,kmer_length,signal_move_offset,
                parameter_source,calibration_artifact_id,primary_alignment_policy,minimum_mapq,
                include_supplementary,read_set_selection,approval_receipt,approved_at,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("profile-1", "profile", "dna", MODEL_ID, 5, 4, "approved_calibration", "cal-1", "primary_only", 0, 0, "immutable_full_set", '{"approved":true}', now, now),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE ont_signal_mapping_profiles SET name='changed' WHERE id='profile-1'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM ont_signal_mapping_profiles WHERE id='profile-1'")

        connection.execute(
            """INSERT INTO ont_signal_mapping_jobs(
                id,mode,run_id,observed_generation,raw_representation_id,move_source_id,
                mapping_profile_id,request_fingerprint,state,reason_code,attempt,
                resource_snapshot,stage_receipts,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("map-1", "signal_to_read", "run-1", 1, "raw-1", "move-1", "profile-1", "c" * 64, "requested", "requested", 0, "{}", '{"request_identity_sha256":"c"}', now, now),
        )
        connection.execute(
            "UPDATE ont_signal_mapping_jobs SET stage_receipts=? WHERE id='map-1'",
            ('{"request_identity_sha256":"c","runtime":{"attempt":1}}',),
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE ont_signal_mapping_jobs SET stage_receipts=? WHERE id='map-1'",
                ('{"request_identity_sha256":"d","runtime":{"attempt":1}}',),
            )
        with pytest.raises(sqlite3.IntegrityError, match="identity"):
            connection.execute("UPDATE ont_signal_mapping_jobs SET request_fingerprint=? WHERE id='map-1'", ("d" * 64,))
        connection.execute(
            """INSERT INTO ont_signal_mapping_artifacts(
                id,mapping_job_id,kind,managed_relative_path,media_type,sha256,size_bytes,
                parent_identities,runtime_identity,validation_receipt,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("map-artifact-1", "map-1", "reform_paf", "mapping/reform.paf.gz", "application/gzip", "e" * 64, 10, "{}", "{}", '{"record_count":1}', now),
        )
        connection.execute(
            "UPDATE ont_signal_mapping_jobs SET state='ready',reason_code='ready',completed_at=?,stage_receipts=? WHERE id='map-1'",
            (now, '{"request_identity_sha256":"c","runtime":{"attempt":1},"validation":{"record_count":1}}'),
        )
        with pytest.raises(sqlite3.IntegrityError, match="terminal"):
            connection.execute("UPDATE ont_signal_mapping_jobs SET stage_receipts='{}' WHERE id='map-1'")
        with pytest.raises(sqlite3.IntegrityError, match="retained"):
            connection.execute("DELETE FROM ont_signal_mapping_jobs WHERE id='map-1'")

        connection.execute(
            """INSERT INTO ont_squigualiser_view_jobs(
                id,mapping_artifact_id,mode,read_id,render_params,request_fingerprint,state,
                reason_code,attempt,output_manifest,render_receipt,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("view-1", "map-artifact-1", "read", "read-1", "{}", "f" * 64, "requested", "requested", 0, "{}", '{"request_identity_sha256":"f"}', now, now),
        )
        with pytest.raises(sqlite3.IntegrityError, match="identity"):
            connection.execute("UPDATE ont_squigualiser_view_jobs SET read_id='read-2' WHERE id='view-1'")
        connection.execute(
            "UPDATE ont_squigualiser_view_jobs SET state='ready',reason_code='ready',completed_at=?,output_manifest=?,render_receipt=? WHERE id='view-1'",
            (now, '{"artifacts":[{"artifact_id":"html","sha256":"1","size_bytes":1}]}', '{"request_identity_sha256":"f","runtime":{"tool":"squigualiser"}}'),
        )
        with pytest.raises(sqlite3.IntegrityError, match="terminal"):
            connection.execute("UPDATE ont_squigualiser_view_jobs SET output_manifest='{}' WHERE id='view-1'")
        with pytest.raises(sqlite3.IntegrityError, match="retained"):
            connection.execute("DELETE FROM ont_squigualiser_view_jobs WHERE id='view-1'")


def test_move_source_registration_authority_is_immutable_before_terminal_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "move-registration-immutability.db"
    _bootstrap_migration_parents(db_path)
    migrate(str(db_path))
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO ont_instrument_runs(id) VALUES ('run-1')")
        connection.execute("INSERT INTO ont_raw_signal_representations(id) VALUES ('raw-1')")
        connection.execute("INSERT INTO input_files(id) VALUES ('input-1')")
        connection.execute("INSERT INTO jobs(id) VALUES ('source-job-1')")
        connection.execute("INSERT INTO jobs(id) VALUES ('source-job-2')")
        connection.execute(
            """INSERT INTO ont_move_table_sources(
                id,run_id,observed_generation,raw_representation_id,input_file_id,
                source_job_id,external_registration_receipt_id,artifact_sha256,
                artifact_size_bytes,molecule_type,source_runtime_identity,
                validation_state,reason_code,validation_receipt,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "move-requested", "run-1", 1, "raw-1", "input-1", "source-job-1",
                "receipt-1", "a" * 64, 10, "dna", '{"authority_state":"known"}',
                "requested", "requested", "{}", now,
            ),
        )
        for state in ("requested", "running"):
            if state == "running":
                connection.execute(
                    "UPDATE ont_move_table_sources SET validation_state='running',claim_token='token' WHERE id='move-requested'"
                )
            for column, value in (
                ("source_job_id", "'source-job-2'"),
                ("external_registration_receipt_id", "'receipt-2'"),
                ("source_runtime_identity", "'{\"authority_state\":\"legacy_unknown\"}'"),
            ):
                with pytest.raises(sqlite3.IntegrityError, match="identity"):
                    connection.execute(
                        f"UPDATE ont_move_table_sources SET {column}={value} WHERE id='move-requested'"
                    )


@pytest.mark.asyncio
async def test_move_source_publication_fence_retains_registration_authority(
    workbench_store: WorkbenchStore,
) -> None:
    source_id = "move-publication-fence"
    now = datetime.utcnow()
    async with workbench_store.factory() as session:
        source = OntMoveTableSource(
            id=source_id,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            input_file_id="move-input-1",
            source_job_id="source-job-original",
            external_registration_receipt_id=None,
            artifact_sha256="7" * 64,
            artifact_size_bytes=10,
            molecule_type="dna",
            source_runtime_identity={"authority_state": "known", "source_job_id": "source-job-original"},
            validation_state="running",
            reason_code="worker_claimed",
            claim_token="publication-token",
            lease_expires_at=now + timedelta(minutes=5),
            validation_receipt={},
            created_at=now,
        )
        session.add(source)
        await session.commit()
        authority = OntSignalWorker._move_registration_authority(source)
    async with workbench_store.factory() as session:
        source = await session.get(OntMoveTableSource, source_id)
        assert source is not None
        source.source_runtime_identity = {"authority_state": "legacy_unknown"}
        await session.commit()
    async with workbench_store.factory() as session:
        result = await session.execute(
            update(OntMoveTableSource)
            .where(*OntSignalWorker._move_publication_fence(source_id, "publication-token", authority))
            .values(validation_state="ready")
        )
        assert result.rowcount == 0
        await session.rollback()


def test_terminal_move_source_trigger_freezes_all_authority_and_evidence(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "move-terminal-immutability.db"
    _bootstrap_migration_parents(db_path)
    migrate(str(db_path))
    now = datetime.utcnow().isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("INSERT INTO ont_instrument_runs(id) VALUES ('run-1')")
        connection.execute("INSERT INTO ont_raw_signal_representations(id) VALUES ('raw-1')")
        connection.execute("INSERT INTO input_files(id) VALUES ('input-1')")
        connection.execute("INSERT INTO jobs(id) VALUES ('source-job-1')")
        connection.execute(
            """INSERT INTO ont_move_table_sources(
                id,run_id,observed_generation,raw_representation_id,input_file_id,
                source_job_id,external_registration_receipt_id,artifact_sha256,
                artifact_size_bytes,bam_header_sha256,record_count,unique_read_count,
                mv_tag_count,ts_tag_count,ns_tag_count,basecall_model_id,molecule_type,
                source_runtime_identity,read_inventory_sha256,validation_state,reason_code,
                validation_receipt,created_at,validated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "move-1", "run-1", 1, "raw-1", "input-1", "source-job-1",
                "receipt-1", "a" * 64, 10, "b" * 64, 2, 2, 2, 2, 2,
                MODEL_ID, "dna", '{"runtime":"one"}', "c" * 64, "ready",
                "move_source_exact_read_set_ready", '{"validated":true}', now, now,
            ),
        )
        mutations = {
            "validation_state": "'failed'",
            "reason_code": "'changed_reason'",
            "validation_receipt": "'{\"validated\":false}'",
            "source_runtime_identity": "'{\"runtime\":\"two\"}'",
            "source_job_id": "NULL",
            "external_registration_receipt_id": "'receipt-2'",
            "bam_header_sha256": f"'{('d' * 64)}'",
            "record_count": "3",
            "unique_read_count": "3",
            "mv_tag_count": "3",
            "ts_tag_count": "3",
            "ns_tag_count": "3",
            "basecall_model_id": "'different-model'",
            "read_inventory_sha256": f"'{('e' * 64)}'",
            "validated_at": "'2099-01-01T00:00:00'",
        }
        for column, value in mutations.items():
            with pytest.raises(sqlite3.IntegrityError, match="terminal evidence"):
                connection.execute(
                    f"UPDATE ont_move_table_sources SET {column}={value} WHERE id='move-1'"
                )
        with pytest.raises(sqlite3.IntegrityError, match="retained evidence"):
            connection.execute("DELETE FROM ont_move_table_sources WHERE id='move-1'")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "incompatible"),
    [("molecule_type", "rna"), ("basecall_model_id", "different-basecall-model")],
)
async def test_mapping_admission_requires_profile_science_exactly_equal_selected_move_source(
    workbench_store: WorkbenchStore,
    field: str,
    incompatible: str,
) -> None:
    async with workbench_store.factory() as session:
        profile_public = await _create_calibrated_profile(session)
        profile = await session.get(
            OntSignalMappingProfile, profile_public["mapping_profile_id"]
        )
        assert profile is not None
        setattr(profile, field, incompatible)

        with pytest.raises(service.OntSignalError, match="model.*incompatible"):
            await service.create_mapping_job(
                session,
                session,
                mode="signal_to_read",
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                move_source_id="ont-moves-ready",
                mapping_profile_id=profile.id,
                reference_revision_id=None,
                alignment_job_id=None,
                alignment_session_id=None,
            )


@pytest.mark.asyncio
async def test_viewer_session_concurrent_update_is_rowcount_fenced_cas(
    workbench_store: WorkbenchStore,
) -> None:
    now = datetime.utcnow()
    async with workbench_store.factory() as session:
        session.add(
            OntSignalViewerSession(
                id="viewer-cas",
                dataset_id="dataset-1",
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                move_source_id="ont-moves-ready",
                contig="chr1",
                locus_start=1,
                locus_end=10,
                selected_read_id="read-1",
                igv_state={},
                signal_state={"selected_read_id": "read-1"},
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()

    async def contender(start: int, read_id: str) -> str:
        async with workbench_store.factory() as session:
            try:
                await service.update_viewer_session(
                    session,
                    "viewer-cas",
                    expected_revision=1,
                    contig="chr1",
                    locus_start=start,
                    locus_end=start + 9,
                    selected_read_id=read_id,
                    igv_state={"locus": f"chr1:{start}-{start + 9}"},
                    signal_state={"selected_read_id": read_id},
                )
                await session.commit()
                return "won"
            except service.OntSignalError:
                await session.rollback()
                return "lost"

    outcomes = await asyncio.gather(contender(20, "read-1"), contender(40, "read-2"))
    assert sorted(outcomes) == ["lost", "won"]
    async with workbench_store.factory() as session:
        persisted = await session.get(OntSignalViewerSession, "viewer-cas")
        assert persisted is not None
        assert persisted.revision == 2
        assert (persisted.locus_start, persisted.selected_read_id) in {
            (20, "read-1"),
            (40, "read-2"),
        }


@pytest.mark.asyncio
async def test_viewer_update_uses_revision_in_rowcount_fenced_update() -> None:
    row = OntSignalViewerSession(
        id="viewer-fenced-update",
        dataset_id="dataset-1",
        run_id="run-1",
        observed_generation=1,
        contig="chr1",
        locus_start=1,
        locus_end=2,
        selected_read_id="read-1",
        igv_state={},
        signal_state={},
        revision=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    class LostCasSession:
        update_seen = False

        async def get(self, model: Any, identity: str) -> Any:
            if model is OntSignalViewerSession and identity == row.id:
                return row
            return None

        async def execute(self, statement: Any) -> Any:
            assert getattr(statement, "is_update", False)
            self.update_seen = True
            return type("Result", (), {"rowcount": 0})()

    session = LostCasSession()
    with pytest.raises(service.OntSignalError, match="changed concurrently"):
        await service.update_viewer_session(
            session,  # type: ignore[arg-type]
            row.id,
            expected_revision=1,
            contig="chr1",
            locus_start=10,
            locus_end=20,
            selected_read_id="read-1",
            igv_state={},
            signal_state={"selected_read_id": "read-1"},
        )
    assert session.update_seen is True


@pytest.mark.asyncio
async def test_reference_capability_filters_exact_viewer_alignment_reference_authority(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        profile = await _create_calibrated_profile(session)
        read_mapping = OntSignalMappingJob(
            id="mapping-read-capability",
            mode="signal_to_read",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=profile["mapping_profile_id"],
            request_fingerprint="1" * 64,
            state="ready",
            reason_code="ready",
            resource_snapshot={},
            stage_receipts={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        exact = OntSignalMappingJob(
            id="mapping-reference-exact",
            mode="signal_to_reference",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=profile["mapping_profile_id"],
            reference_revision_id="reference-exact",
            alignment_job_id="alignment-exact",
            alignment_session_id="session-exact",
            parent_mapping_job_id=read_mapping.id,
            request_fingerprint="2" * 64,
            state="ready",
            reason_code="ready",
            resource_snapshot={},
            stage_receipts={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        unrelated = OntSignalMappingJob(
            id="mapping-reference-unrelated",
            mode="signal_to_reference",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=profile["mapping_profile_id"],
            reference_revision_id="reference-other",
            alignment_job_id="alignment-other",
            alignment_session_id="session-other",
            parent_mapping_job_id=read_mapping.id,
            request_fingerprint="3" * 64,
            state="ready",
            reason_code="ready",
            resource_snapshot={},
            stage_receipts={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed_at=datetime.utcnow() + timedelta(seconds=1),
        )
        session.add_all([
            Job(id="alignment-exact", name="exact", status="completed", model_id="ont", mode="alignment", params={}, output_dir=str(workbench_store.root)),
            Job(id="alignment-other", name="other", status="completed", model_id="ont", mode="alignment", params={}, output_dir=str(workbench_store.root)),
            read_mapping,
            exact,
            unrelated,
        ])
        await session.commit()

        capabilities = await service.workbench_capabilities(
            session,
            run_id="run-1",
            observed_generation=1,
            alignment_job_id="alignment-exact",
            alignment_session_id="session-exact",
            reference_revision_id="reference-exact",
        )
        unscoped = await service.workbench_capabilities(
            session,
            run_id="run-1",
            observed_generation=1,
        )

    assert capabilities["resolved"]["signal_to_read_mapping_job_id"] == read_mapping.id
    assert capabilities["resolved"]["signal_to_reference_mapping_job_id"] == exact.id
    assert capabilities["modes"]["raw_waveform"]["state"] == "ready"
    assert unscoped["resolved"]["signal_to_read_mapping_job_id"] == read_mapping.id
    assert unscoped["resolved"]["signal_to_reference_mapping_job_id"] is None
    assert unscoped["modes"]["raw_waveform"]["state"] == "ready"
    assert unscoped["modes"]["signal_to_reference"]["state"] != "ready"
    assert unscoped["modes"]["signal_pileup"]["state"] != "ready"


def test_profile_sourced_render_params_reject_an_explicit_shift() -> None:
    with pytest.raises(service.OntSignalError, match="profile-sourced base shift"):
        service.normalize_render_params({
            "base_shift_source": "profile",
            "base_shift_value": 7,
        })


@pytest.mark.asyncio
async def test_viewer_update_rejects_reference_mapping_from_another_read_mapping(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        profile = await _create_calibrated_profile(session)
        common = {
            "run_id": "run-1",
            "observed_generation": 1,
            "raw_representation_id": "raw-blow5-1",
            "move_source_id": "ont-moves-ready",
            "mapping_profile_id": profile["mapping_profile_id"],
            "state": "ready",
            "reason_code": "ready",
            "resource_snapshot": {},
            "stage_receipts": {},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "completed_at": datetime.utcnow(),
        }
        saved_read = OntSignalMappingJob(
            id="mapping-viewer-saved-read",
            mode="signal_to_read",
            request_fingerprint="a" * 64,
            **common,
        )
        other_read = OntSignalMappingJob(
            id="mapping-viewer-other-read",
            mode="signal_to_read",
            request_fingerprint="b" * 64,
            **common,
        )
        reference = OntSignalMappingJob(
            id="mapping-viewer-reference",
            mode="signal_to_reference",
            reference_revision_id="reference-revision-1",
            alignment_job_id="alignment-job-1",
            alignment_session_id="alignment-session-1",
            parent_mapping_job_id=other_read.id,
            request_fingerprint="c" * 64,
            **common,
        )
        viewer = OntSignalViewerSession(
            id="viewer-reject-mixed-mapping-chain",
            dataset_id="dataset-1",
            run_id="run-1",
            observed_generation=1,
            alignment_job_id="alignment-job-1",
            alignment_session_id="alignment-session-1",
            reference_revision_id="reference-revision-1",
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=profile["mapping_profile_id"],
            contig="chr1",
            locus_start=1,
            locus_end=10,
            selected_read_id="read-1",
            igv_state={},
            signal_state={},
            revision=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add_all([saved_read, other_read, reference, viewer])
        await session.flush()

        with pytest.raises(service.OntSignalError, match="mapping chain"):
            await service.update_viewer_session(
                session,
                viewer.id,
                expected_revision=1,
                contig="chr1",
                locus_start=1,
                locus_end=10,
                selected_read_id="read-1",
                igv_state={},
                signal_state={
                    "read_mapping_job_id": saved_read.id,
                    "reference_mapping_job_id": reference.id,
                },
            )


@pytest.mark.asyncio
async def test_viewer_update_rejects_unrelated_saved_view_job(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        artifact = await _seed_ready_mapping_artifact(
            session,
            artifact_id="mapping-artifact-unrelated-view",
            mode="signal_to_read",
        )
        mapping = await session.get(OntSignalMappingJob, artifact.mapping_job_id)
        assert mapping is not None
        mapping.observed_generation = 2
        unrelated_view = OntSquigualiserViewJob(
            id="view-unrelated-generation",
            mapping_artifact_id=artifact.id,
            mode="read",
            read_id="read-1",
            render_params={},
            request_fingerprint="4" * 64,
            state="ready",
            reason_code="ready",
            output_manifest={},
            render_receipt={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        viewer = OntSignalViewerSession(
            id="viewer-reject-unrelated-view",
            dataset_id="dataset-1",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=mapping.mapping_profile_id,
            contig="chr1",
            locus_start=1,
            locus_end=10,
            selected_read_id="read-1",
            igv_state={},
            signal_state={},
            revision=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add_all([unrelated_view, viewer])
        await session.commit()

        with pytest.raises(service.OntSignalError, match="saved view.*authority"):
            await service.update_viewer_session(
                session,
                viewer.id,
                expected_revision=1,
                contig="chr1",
                locus_start=1,
                locus_end=10,
                selected_read_id="read-1",
                igv_state={},
                signal_state={
                    "selected_read_id": "read-1",
                    "view_job_id": unrelated_view.id,
                },
            )


@pytest.mark.asyncio
async def test_profile_sourced_view_binds_approved_effective_shift(
    workbench_store: WorkbenchStore,
) -> None:
    async with workbench_store.factory() as session:
        mapping_artifact = await _seed_ready_mapping_artifact(
            session,
            artifact_id="mapping-artifact-profile-shift",
            mode="signal_to_read",
        )
        mapping = await session.get(OntSignalMappingJob, mapping_artifact.mapping_job_id)
        assert mapping is not None
        profile = await session.get(OntSignalMappingProfile, mapping.mapping_profile_id)
        assert profile is not None
        created = await service.create_view_job(
            session,
            mapping_artifact_id=mapping_artifact.id,
            mode="read",
            read_id="read-1",
            reference_contig=None,
            reference_start=None,
            reference_end=None,
            render_params={"base_shift_source": "profile"},
        )
        row = await session.get(OntSquigualiserViewJob, created["view_job_id"])

    assert row is not None
    assert row.render_params["base_shift_profile_id"] == profile.id
    assert re.fullmatch(r"[0-9a-f]{64}", row.render_params["base_shift_profile_sha256"])
    assert row.render_params["base_shift_effective_value"] == 0
    assert worker_service._effective_base_shift(row.render_params) == 0


@pytest.mark.asyncio
async def test_expired_calibration_mapping_and_view_leases_exhaust_attempt_budget(
    workbench_store: WorkbenchStore,
) -> None:
    now = datetime.utcnow()
    worker = OntSignalWorker(workbench_store.factory, workbench_store.factory)
    async with workbench_store.factory() as session:
        calibration = await service.create_calibration_job(
            session,
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            sample_count=7,
        )
        calibration_row = await session.get(
            OntSignalCalibrationJob, calibration["calibration_job_id"]
        )
        mapping_artifact = await _seed_ready_mapping_artifact(
            session,
            artifact_id="mapping-artifact-expired-view",
            mode="signal_to_read",
        )
        mapping_row = await session.get(
            OntSignalMappingJob, mapping_artifact.mapping_job_id
        )
        assert mapping_row is not None
        mapping_row.state = "running"
        mapping_row.reason_code = "worker_claimed"
        mapping_row.attempt = worker_service.SIGNAL_JOB_MAX_ATTEMPTS
        mapping_row.claim_token = "mapping-expired-token"
        mapping_row.lease_expires_at = now - timedelta(seconds=1)
        view_row = OntSquigualiserViewJob(
            id="view-expired-budget",
            mapping_artifact_id=mapping_artifact.id,
            mode="read",
            read_id="read-1",
            render_params={},
            request_fingerprint="8" * 64,
            state="running",
            reason_code="worker_claimed",
            attempt=worker_service.SIGNAL_JOB_MAX_ATTEMPTS,
            claim_token="view-expired-token",
            lease_expires_at=now - timedelta(seconds=1),
            output_manifest={},
            render_receipt={},
            created_at=now,
            updated_at=now,
        )
        assert calibration_row is not None
        calibration_row.state = "running"
        calibration_row.reason_code = "worker_claimed"
        calibration_row.attempt = worker_service.SIGNAL_JOB_MAX_ATTEMPTS
        calibration_row.claim_token = "calibration-expired-token"
        calibration_row.lease_expires_at = now - timedelta(seconds=1)
        session.add(view_row)
        await session.commit()

    await worker._recover_expired()

    async with workbench_store.factory() as session:
        recovered = [
            await session.get(OntSignalCalibrationJob, calibration["calibration_job_id"]),
            await session.get(OntSignalMappingJob, mapping_row.id),
            await session.get(OntSquigualiserViewJob, view_row.id),
        ]
        assert all(row is not None and row.state == "failed" for row in recovered)
        assert all(row.reason_code == "expired_lease_retry_exhausted" for row in recovered)
        assert all(row.claim_token is None and row.lease_expires_at is None for row in recovered)
        assert all(row.completed_at is not None for row in recovered)


@pytest.mark.asyncio
async def test_managed_bed_hashing_is_offloaded_after_descriptor_admission(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_root = workbench_store.root / "bed-offload"
    job_root.mkdir()
    bed = job_root / "regions.bed"
    bed.write_text("chr1\t1\t2\n", encoding="utf-8")
    monkeypatch.setattr(service, "get_allowed_roots", lambda: {"results": workbench_store.root})
    calls: list[tuple[str, bool]] = []

    async def recording_to_thread(function: Any, *args: Any, **kwargs: Any) -> Any:
        descriptor = int(args[0])
        calls.append((function.__name__, stat.S_ISREG(os.fstat(descriptor).st_mode)))
        return function(*args, **kwargs)

    monkeypatch.setattr(service.asyncio, "to_thread", recording_to_thread)
    async with workbench_store.factory() as session:
        session.add_all([
            Job(
                id="bed-offload-job",
                name="BED offload",
                status="completed",
                model_id="bed",
                mode="analysis",
                params={},
                output_dir=str(job_root),
            ),
            InputFile(
                id="bed-offload-input",
                filename=bed.name,
                file_type="bed",
                directory=str(job_root),
                size_bytes=bed.stat().st_size,
            ),
        ])
        await session.flush()
        _path, identity = await service.resolve_managed_bed_authority(
            session, "bed-offload-input"
        )

    assert identity["sha256"] == hashlib.sha256(bed.read_bytes()).hexdigest()
    assert calls == [("_stable_descriptor_identity", True)]


@pytest.mark.asyncio
async def test_signal_to_reference_requires_exact_run_and_read_inventory_binding(
    workbench_store: WorkbenchStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with workbench_store.factory() as session:
        profile = await _create_calibrated_profile(session)
        parent = OntSignalMappingJob(
            id="mapping-parent-for-alignment-binding",
            mode="signal_to_read",
            run_id="run-1",
            observed_generation=1,
            raw_representation_id="raw-blow5-1",
            move_source_id="ont-moves-ready",
            mapping_profile_id=profile["mapping_profile_id"],
            request_fingerprint="6" * 64,
            state="ready",
            reason_code="ready",
            resource_snapshot={},
            stage_receipts={},
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        session.add_all([
            parent,
            Job(
                id="alignment-wrong-run-read-set",
                name="wrong alignment authority",
                status="completed",
                model_id="ont_alignment",
                mode="alignment",
                params={
                    "dataset_id": "dataset-other",
                    "source_instrument_run_id": "run-other",
                    "source_instrument_observed_generation": 9,
                    "source_read_inventory_sha256": "f" * 64,
                    "reference_sequence_sha256": "a" * 64,
                    "ont_workflow_id": "ont_alignment",
                    "ont_input_mode": "fastq",
                },
                output_dir=str(workbench_store.root),
            ),
        ])
        await session.flush()
        monkeypatch.setattr(
            service,
            "_resolve_reference_authority",
            lambda *_args, **_kwargs: None,
        )
        async def reference_authority(*_args: Any, **_kwargs: Any) -> tuple[Any, Any]:
            return (
                SimpleNamespace(
                    id="reference-revision",
                    normalized_sequence_sha256="a" * 64,
                    contig_manifest_sha256="b" * 64,
                ),
                SimpleNamespace(id="reference-artifact", sha256="c" * 64),
            )
        monkeypatch.setattr(service, "_resolve_reference_authority", reference_authority)

        async def alignment_session(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"ready": True, "mode": "primary", "artifacts": {}}

        monkeypatch.setattr(
            service, "_resolve_primary_alignment_session_async", alignment_session
        )

        with pytest.raises(service.OntSignalError, match="run generation|read inventory"):
            await service.create_mapping_job(
                session,
                session,
                mode="signal_to_reference",
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                move_source_id="ont-moves-ready",
                mapping_profile_id=profile["mapping_profile_id"],
                reference_revision_id="reference-revision",
                alignment_job_id="alignment-wrong-run-read-set",
                alignment_session_id="primary",
            )


@pytest.mark.asyncio
async def test_calibration_and_profile_creation_races_reload_one_durable_winner(
    workbench_store: WorkbenchStore,
) -> None:
    async def create_calibration() -> dict[str, Any]:
        async with workbench_store.factory() as session:
            result = await service.create_calibration_job(
                session,
                run_id="run-1",
                observed_generation=1,
                raw_representation_id="raw-blow5-1",
                move_source_id="ont-moves-ready",
                sample_count=11,
            )
            await session.commit()
            return result

    calibration_a, calibration_b = await asyncio.gather(
        create_calibration(), create_calibration()
    )
    assert calibration_a["calibration_job_id"] == calibration_b["calibration_job_id"]

    async with workbench_store.factory() as session:
        artifact = await _publish_calibration_evidence(session)
        await session.commit()

    async def create_profile() -> dict[str, Any]:
        async with workbench_store.factory() as session:
            result = await service.create_mapping_profile(
                session,
                name="Concurrent approved profile",
                molecule_type="dna",
                basecall_model_id=MODEL_ID,
                kmer_length=artifact.recommended_kmer_length,
                signal_move_offset=artifact.recommended_signal_move_offset,
                parameter_source="approved_calibration",
                calibration_artifact_id=artifact.id,
                minimum_mapq=0,
                read_set_selection="immutable_full_set",
                approval_receipt={
                    "approved": True,
                    "calibration_artifact_sha256": artifact.artifact_sha256,
                },
                approved_by="operator@example.test",
            )
            await session.commit()
            return result

    profile_a, profile_b = await asyncio.gather(create_profile(), create_profile())
    assert profile_a["mapping_profile_id"] == profile_b["mapping_profile_id"]
    async with workbench_store.factory() as session:
        assert await session.scalar(
            select(func.count()).select_from(OntSignalCalibrationJob).where(
                OntSignalCalibrationJob.sample_count == 11
            )
        ) == 1
        assert await session.scalar(
            select(func.count()).select_from(OntSignalMappingProfile).where(
                OntSignalMappingProfile.calibration_artifact_id == artifact.id
            )
        ) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection",
    [
        {"contig": "chr1", "locus_start": None, "locus_end": None, "selected_read_id": None},
        {"contig": "chr1", "locus_start": 20, "locus_end": 10, "selected_read_id": None},
        {"contig": None, "locus_start": None, "locus_end": None, "selected_read_id": "../read"},
    ],
)
async def test_create_viewer_session_matches_update_selection_validation(
    workbench_store: WorkbenchStore,
    selection: dict[str, Any],
) -> None:
    async with workbench_store.factory() as session:
        session.add(Job(
            id="viewer-create-validation-job",
            name="viewer create validation",
            status="completed",
            model_id="ont_alignment",
            mode="alignment",
            params={
                "dataset_id": "dataset-1",
                "source_instrument_run_id": "run-1",
                "source_instrument_observed_generation": 1,
            },
            output_dir=str(workbench_store.root),
        ))
        await session.flush()
        with pytest.raises(service.OntSignalError, match="viewer (locus|selected read) authority"):
            await service.create_viewer_session(
                session,
                dataset_id="dataset-1",
                run_id="run-1",
                observed_generation=1,
                alignment_job_id="viewer-create-validation-job",
                alignment_session_id=None,
                reference_revision_id=None,
                **_viewer_create_states(),
                **selection,
            )


def test_viewer_session_request_and_response_state_schemas_share_closed_literals() -> None:
    create_schema = router.ViewerSessionCreate.model_json_schema()
    assert create_schema["required"][-2:] == ["igv_state", "signal_state"]
    create_igv_name = create_schema["properties"]["igv_state"]["$ref"].rsplit("/", 1)[-1]
    create_signal_name = create_schema["properties"]["signal_state"]["$ref"].rsplit("/", 1)[-1]
    assert create_schema["$defs"][create_igv_name]["additionalProperties"] is False
    assert create_schema["$defs"][create_signal_name]["additionalProperties"] is False

    update_schema = router.ViewerSessionUpdate.model_json_schema()
    update_definitions = update_schema["$defs"]
    update_igv_name = update_schema["properties"]["igv_state"]["$ref"].rsplit("/", 1)[-1]
    update_signal_name = update_schema["properties"]["signal_state"]["$ref"].rsplit("/", 1)[-1]
    assert update_definitions[update_igv_name]["additionalProperties"] is False
    assert update_definitions[update_signal_name]["additionalProperties"] is False
    valid = {
        "expected_revision": 1,
        "contig": "chr1",
        "locus_start": 1,
        "locus_end": 2,
        "selected_read_id": "read-1",
        "igv_state": {
            "alignment_display_mode": "FULL",
            "alignment_color_by": "strand",
            "alignment_group_by": "none",
            "reads_track_loaded": True,
        },
        "signal_state": {
            "mode": "read",
            "render_params": {},
            "view_job_id": None,
            "read_mapping_job_id": None,
            "reference_mapping_job_id": None,
        },
    }
    create_valid = {
        "dataset_id": "dataset-1",
        "run_id": "run-1",
        "observed_generation": 1,
        "alignment_job_id": "alignment-job-1",
        "alignment_session_id": "primary",
        "reference_revision_id": "reference-1",
        "contig": valid["contig"],
        "locus_start": valid["locus_start"],
        "locus_end": valid["locus_end"],
        "selected_read_id": valid["selected_read_id"],
        "igv_state": valid["igv_state"],
        "signal_state": valid["signal_state"],
    }
    assert router.ViewerSessionCreate.model_validate(create_valid)
    assert router.ViewerSessionUpdate.model_validate(valid)
    for field, unsupported in (
        ("alignment_display_mode", "COLLAPSED"),
        ("alignment_color_by", "arbitrary-color"),
        ("alignment_group_by", "arbitrary-group"),
    ):
        with pytest.raises(ValueError):
            router.ViewerSessionUpdate.model_validate({
                **valid,
                "igv_state": {**valid["igv_state"], field: unsupported},
            })

    response_schema = router.ViewerSessionResponse.model_json_schema()
    response_definitions = response_schema["$defs"]
    response_igv_name = response_schema["properties"]["igv_state"]["$ref"].rsplit("/", 1)[-1]
    response_signal_name = response_schema["properties"]["signal_state"]["$ref"].rsplit("/", 1)[-1]
    assert response_definitions[response_igv_name]["additionalProperties"] is False
    assert response_definitions[response_signal_name]["additionalProperties"] is False
    with pytest.raises(ValueError):
        router.ViewerIgvStateResponse.model_validate({"host_path": "/tmp/escape"})
    with pytest.raises(ValueError):
        router.ViewerSignalStateResponse.model_validate({"command": "squigualiser plot"})
