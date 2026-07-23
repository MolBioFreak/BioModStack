from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from database import (
    ConformationalMappingLandscapeRow,
    ConformationalMappingRecord,
)
from services.conformational_mapping.contracts import canonical_sha256
from services.conformational_mapping.persistence import (
    ConformationalPersistenceError,
    ingest_result_bundle,
)
from test_conformational_mapping_ingester_state_compatibility import (
    _coherent_state_bundle,
    _no_authority_bundle,
    _register,
    _session,
)


async def _projection_rows(session, table: str):
    return (await session.execute(text(f"SELECT * FROM {table}"))).mappings().all()


@pytest.mark.asyncio
async def test_state_analysis_projection_persists_exact_validated_header_pairs_and_rows(tmp_path: Path) -> None:
    root = tmp_path / "result"
    root.mkdir()
    request, bundle, artifact = _coherent_state_bundle(root)
    bundle["cm_state_landscape_analyses"] = [artifact]
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        await session.commit()

        headers = await _projection_rows(session, "conformational_mapping_state_landscape_analysis_headers")
        pairs = await _projection_rows(session, "conformational_mapping_state_landscape_analysis_pairs")
        rows = await _projection_rows(session, "conformational_mapping_state_landscape_analysis_rows")

        assert len(headers) == 1
        header = headers[0]
        assert {
            key: header[key]
            for key in (
                "request_id", "analysis_id", "content_sha256", "source_ensemble_sha256",
                "source_landscape_sha256", "source_structure_map_sha256", "comparison_sha256",
                "formula_version", "formula_sha256", "policy_sha256", "comparison_mode",
                "comparison_target_id", "comparison_scope", "reference_candidate_id",
                "pair_count", "row_count", "exclusion_count",
            )
        } == {
            "request_id": record.request_id,
            "analysis_id": artifact["analysis_id"],
            "content_sha256": canonical_sha256(artifact),
            "source_ensemble_sha256": artifact["source_ensemble_sha256"],
            "source_landscape_sha256": artifact["source_landscape_sha256"],
            "source_structure_map_sha256": artifact["source_structure_map_sha256"],
            "comparison_sha256": artifact["comparison_sha256"],
            "formula_version": artifact["formula_version"],
            "formula_sha256": artifact["formula_sha256"],
            "policy_sha256": artifact["policy_sha256"],
            "comparison_mode": artifact["comparison_mode"],
            "comparison_target_id": artifact["comparison_target_id"],
            "comparison_scope": artifact["comparison_scope"],
            "reference_candidate_id": artifact["reference_candidate_id"],
            "pair_count": len(artifact["resolved_pairs"]),
            "row_count": len(artifact["rows"]),
            "exclusion_count": len(artifact["exclusion_ledger"]),
        }
        assert json.loads(header["reference_backend_coordinates_json"]) == artifact["reference_backend_coordinates"]
        assert [
            {key: pair[key] for key in ("pair_id", "candidate_a_id", "candidate_b_id")}
            for pair in pairs
        ] == artifact["resolved_pairs"]
        assert [
            {
                "pair_id": row["pair_id"],
                "candidate_a_id": row["candidate_a_id"],
                "candidate_b_id": row["candidate_b_id"],
                "identity": {
                    "target_id": row["target_id"],
                    "entity_instance_id": row["entity_instance_id"],
                    "auth_asym_id": row["auth_asym_id"],
                    "auth_seq_id": row["auth_seq_id"],
                    "insertion_code": row["insertion_code"],
                    "sequence_index": row["sequence_index"],
                    "validated_wt": row["validated_wt"],
                },
                "metrics": json.loads(row["metrics_json"]),
                "availability": json.loads(row["availability_json"]),
            }
            for row in rows
        ] == [
            {
                **artifact_row,
                "availability": {
                    name: {"status": metric["status"], "reason": metric["reason"]}
                    for name, metric in artifact_row["metrics"].items()
                },
            }
            for artifact_row in artifact["rows"]
        ]
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_state_analysis_projection_replay_is_idempotent_without_duplicate_rows(tmp_path: Path) -> None:
    root = tmp_path / "result"
    root.mkdir()
    request, bundle, artifact = _coherent_state_bundle(root)
    bundle["cm_state_landscape_analyses"] = [artifact]
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        await session.commit()
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        await session.commit()

        assert len(await _projection_rows(session, "conformational_mapping_state_landscape_analysis_headers")) == 1
        assert len(await _projection_rows(session, "conformational_mapping_state_landscape_analysis_pairs")) == len(artifact["resolved_pairs"])
        assert len(await _projection_rows(session, "conformational_mapping_state_landscape_analysis_rows")) == len(artifact["rows"])
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_forged_state_analysis_binding_commits_no_canonical_or_projection_rows(tmp_path: Path) -> None:
    root = tmp_path / "result"
    root.mkdir()
    request, bundle, artifact = _coherent_state_bundle(root)
    forged = copy.deepcopy(artifact)
    forged["analysis_id"] = "cm_state_landscape_analysis_" + "0" * 32
    bundle["cm_state_landscape_analyses"] = [forged]
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        with pytest.raises(ConformationalPersistenceError, match="binding validation failed"):
            await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        await session.commit()

        assert await session.scalar(select(func.count()).select_from(ConformationalMappingRecord).where(
            ConformationalMappingRecord.request_id == record.request_id
        )) == 0
        assert await session.scalar(select(func.count()).select_from(ConformationalMappingLandscapeRow).where(
            ConformationalMappingLandscapeRow.request_id == record.request_id
        )) == 0
        assert await _projection_rows(session, "conformational_mapping_state_landscape_analysis_headers") == []
        assert await _projection_rows(session, "conformational_mapping_state_landscape_analysis_pairs") == []
        assert await _projection_rows(session, "conformational_mapping_state_landscape_analysis_rows") == []
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_bundle_without_state_analysis_authority_creates_no_projection(tmp_path: Path) -> None:
    root = tmp_path / "result"
    root.mkdir()
    request, bundle = _no_authority_bundle(root)
    session, engine = await _session(tmp_path)
    try:
        record = await _register(session, request, bundle)
        await ingest_result_bundle(session, record, bundle=bundle, result_root=root)
        await session.commit()
        assert await _projection_rows(session, "conformational_mapping_state_landscape_analysis_headers") == []
    finally:
        await session.close()
        await engine.dispose()


def test_state_analysis_projection_migration_is_ordered_and_idempotent(tmp_path: Path) -> None:
    migration = importlib.import_module("migrations.add_state_landscape_analysis_projection")
    database = tmp_path / "projection.db"
    migration.migrate(str(database))
    migration.migrate(str(database))

    import sqlite3
    with sqlite3.connect(database) as connection:
        for table, expected_columns in {
            "conformational_mapping_state_landscape_analysis_headers": {
                "request_id", "analysis_id", "content_sha256", "source_ensemble_sha256",
                "source_landscape_sha256", "source_structure_map_sha256", "comparison_sha256",
                "formula_version", "formula_sha256", "policy_sha256", "comparison_mode",
                "comparison_target_id", "comparison_scope", "reference_backend_coordinates_json",
                "reference_candidate_id", "pair_count", "row_count", "exclusion_count", "created_at",
            },
            "conformational_mapping_state_landscape_analysis_pairs": {
                "request_id", "analysis_id", "pair_id", "candidate_a_id", "candidate_b_id",
            },
            "conformational_mapping_state_landscape_analysis_rows": {
                "id", "request_id", "analysis_id", "pair_id", "candidate_a_id", "candidate_b_id",
                "target_id", "entity_instance_id", "auth_asym_id", "auth_seq_id", "insertion_code",
                "sequence_index", "validated_wt", "metrics_json", "availability_json",
            },
        }.items():
            assert {row[1] for row in connection.execute(f"PRAGMA table_info({table})")} == expected_columns

    runner = importlib.import_module("migrations.runner")
    assert [(migration.version, migration.name) for migration in runner.MIGRATIONS][-1] == (
        13, "enforce_state_landscape_analysis_pair_row_integrity",
    )


def test_state_analysis_projection_pair_row_migration_rejects_orphan_and_candidate_mismatch(
    tmp_path: Path,
) -> None:
    import sqlite3

    database = tmp_path / "projection-pair-row-integrity.db"
    initial_migration = importlib.import_module("migrations.add_state_landscape_analysis_projection")
    integrity_migration = importlib.import_module(
        "migrations.enforce_state_landscape_analysis_pair_row_integrity"
    )
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "CREATE TABLE conformational_mapping_requests (request_id VARCHAR(36) PRIMARY KEY NOT NULL)"
        )
        connection.execute(
            "INSERT INTO conformational_mapping_requests(request_id) VALUES ('request-1')"
        )
    initial_migration.migrate(str(database))
    integrity_migration.migrate(str(database))

    header_values = (
        "request-1", "analysis-1", "a" * 64, "b" * 64, "c" * 64, "d" * 64,
        "e" * 64, "formula-v1", "f" * 64, "0" * 64, "all_pairs", "target-1",
        "all", None, None, 1, 1, 0,
    )
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO conformational_mapping_state_landscape_analysis_headers (
                request_id, analysis_id, content_sha256, source_ensemble_sha256,
                source_landscape_sha256, source_structure_map_sha256, comparison_sha256,
                formula_version, formula_sha256, policy_sha256, comparison_mode,
                comparison_target_id, comparison_scope, reference_backend_coordinates_json,
                reference_candidate_id, pair_count, row_count, exclusion_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            header_values,
        )
        connection.execute(
            """
            INSERT INTO conformational_mapping_state_landscape_analysis_pairs (
                request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id
            ) VALUES ('request-1', 'analysis-1', 'pair-1', 'candidate-a', 'candidate-b')
            """
        )

        def insert_row(row_id: str, pair_id: str, candidate_a_id: str, candidate_b_id: str) -> None:
            connection.execute(
                """
                INSERT INTO conformational_mapping_state_landscape_analysis_rows (
                    id, request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id,
                    target_id, entity_instance_id, auth_asym_id, auth_seq_id, insertion_code,
                    sequence_index, validated_wt, metrics_json, availability_json
                ) VALUES (?, 'request-1', 'analysis-1', ?, ?, ?, 'target-1', 'entity-1', 'A',
                          1, '', 1, 'A', '{}', '{}')
                """,
                (row_id, pair_id, candidate_a_id, candidate_b_id),
            )

        insert_row("row-valid", "pair-1", "candidate-a", "candidate-b")
        with pytest.raises(sqlite3.IntegrityError):
            insert_row("row-orphan", "pair-not-in-pairs", "candidate-a", "candidate-b")
        with pytest.raises(sqlite3.IntegrityError):
            insert_row("row-mismatch", "pair-1", "candidate-a", "candidate-other")
        assert connection.execute(
            "SELECT id FROM conformational_mapping_state_landscape_analysis_rows"
        ).fetchall() == [("row-valid",)]


@pytest.mark.asyncio
async def test_production_sqlite_session_enforces_state_analysis_pair_row_foreign_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh production database module must enable SQLite FKs on its own connections."""

    database_path = tmp_path / "production-pair-row-integrity.db"
    import sqlite3

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE conformational_mapping_requests (request_id VARCHAR(36) PRIMARY KEY NOT NULL)"
        )
        connection.execute(
            "INSERT INTO conformational_mapping_requests(request_id) VALUES ('request-1')"
        )

    importlib.import_module("migrations.add_state_landscape_analysis_projection").migrate(
        str(database_path)
    )
    importlib.import_module("migrations.enforce_state_landscape_analysis_pair_row_integrity").migrate(
        str(database_path)
    )

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    module_name = "production_database_pair_row_integrity_test"
    database_module_path = Path(__file__).parents[1] / "database.py"
    spec = importlib.util.spec_from_file_location(module_name, database_module_path)
    assert spec is not None and spec.loader is not None
    production_database = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = production_database
    spec.loader.exec_module(production_database)

    try:
        async with production_database.async_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO conformational_mapping_state_landscape_analysis_headers (
                        request_id, analysis_id, content_sha256, source_ensemble_sha256,
                        source_landscape_sha256, source_structure_map_sha256, comparison_sha256,
                        formula_version, formula_sha256, policy_sha256, comparison_mode,
                        comparison_target_id, comparison_scope, reference_backend_coordinates_json,
                        reference_candidate_id, pair_count, row_count, exclusion_count
                    ) VALUES (
                        'request-1', 'analysis-1', :content_sha256, :source_ensemble_sha256,
                        :source_landscape_sha256, :source_structure_map_sha256, :comparison_sha256,
                        'formula-v1', :formula_sha256, :policy_sha256, 'all_pairs', 'target-1',
                        'all', NULL, NULL, 1, 1, 0
                    )
                    """
                ),
                {
                    "content_sha256": "a" * 64,
                    "source_ensemble_sha256": "b" * 64,
                    "source_landscape_sha256": "c" * 64,
                    "source_structure_map_sha256": "d" * 64,
                    "comparison_sha256": "e" * 64,
                    "formula_sha256": "f" * 64,
                    "policy_sha256": "0" * 64,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO conformational_mapping_state_landscape_analysis_pairs (
                        request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id
                    ) VALUES ('request-1', 'analysis-1', 'pair-1', 'candidate-a', 'candidate-b')
                    """
                )
            )
            await session.commit()

            row_insert = text(
                """
                INSERT INTO conformational_mapping_state_landscape_analysis_rows (
                    id, request_id, analysis_id, pair_id, candidate_a_id, candidate_b_id,
                    target_id, entity_instance_id, auth_asym_id, auth_seq_id, insertion_code,
                    sequence_index, validated_wt, metrics_json, availability_json
                ) VALUES (
                    :id, 'request-1', 'analysis-1', :pair_id, :candidate_a_id, :candidate_b_id,
                    'target-1', 'entity-1', 'A', 1, '', 1, 'A', '{}', '{}'
                )
                """
            )
            with pytest.raises(IntegrityError):
                await session.execute(
                    row_insert,
                    {
                        "id": "row-orphan",
                        "pair_id": "pair-not-in-pairs",
                        "candidate_a_id": "candidate-a",
                        "candidate_b_id": "candidate-b",
                    },
                )
            await session.rollback()

            with pytest.raises(IntegrityError):
                await session.execute(
                    row_insert,
                    {
                        "id": "row-mismatch",
                        "pair_id": "pair-1",
                        "candidate_a_id": "candidate-a",
                        "candidate_b_id": "candidate-other",
                    },
                )
    finally:
        await production_database.engine.dispose()
        sys.modules.pop(module_name, None)
