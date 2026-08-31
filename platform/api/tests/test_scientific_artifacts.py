from __future__ import annotations

import importlib
import hashlib
import json
import multiprocessing
import os
import sqlite3

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from database import Base, Design
from migrations.add_scientific_artifact_receipts import migrate
import scripts.migrate_json_payloads_to_artifacts as migration
from services.scientific_artifacts import (
    artifact_row_reference,
    canonical_json_bytes,
    envelope_rows,
    install_parquet_rows,
    publish_table_rows,
    query_json_envelope_page,
    query_rows,
    reconstruct_envelope,
    resolve_json_envelope_fields,
    resolve_json_value,
    verify_artifact,
)
from services.scientific_artifacts.writer import (
    ScientificArtifactError,
)


def _publish_same_artifact(root, barrier, results) -> None:
    """Start exact publishers together across independent processes."""
    import services.scientific_artifacts.writer as writer

    barrier.wait(timeout=10)
    artifact = writer.install_parquet_rows(
        root=root,
        owner_kind="concurrent-test",
        owner_id="same-owner",
        role="rows",
        schema_id="bms.concurrent-test.v1",
        schema_version=1,
        source_sha256="a" * 64,
        rows=[{"value": 1}],
        schema=pa.schema([("value", pa.int64())]),
    )
    results.put(artifact)


def _install_transactional_artifact(root, transaction_id, *, value=1):
    import services.scientific_artifacts.writer as writer

    return writer.install_parquet_rows(
        root=root,
        owner_kind="transaction-test",
        owner_id="shared-owner",
        role="rows",
        schema_id="bms.transaction-test.v1",
        schema_version=1,
        source_sha256="f" * 64,
        rows=[{"value": value}],
        schema=pa.schema([("value", pa.int64())]),
        transaction_id=transaction_id,
    )


def test_small_design_scientific_array_is_externalized_before_flush(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BMS_DATA", str(tmp_path / "bms-data"))
    engine = create_engine(f"sqlite:///{tmp_path / 'design.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Design(
                id="design-small-dense",
                job_id="job-small-dense",
                name="small dense",
                pdb_path="small.pdb",
                residue_plddt=[0.5],
            )
        )
        session.commit()
    with engine.connect() as connection:
        raw = connection.execute(
            text("SELECT residue_plddt FROM designs WHERE id = :id"),
            {"id": "design-small-dense"},
        ).scalar_one()
        receipts = connection.execute(
            text(
                "SELECT owner_kind, owner_id, role, row_count "
                "FROM scientific_artifact_receipts"
            )
        ).all()
    reference = json.loads(raw)
    assert reference["schema"] == "bms.scientific-artifact-row-reference.v1"
    assert receipts == [
        (
            "design_field",
            "design-small-dense:residue_plddt",
            "payload",
            1,
        )
    ]


def test_unrelated_design_update_keeps_existing_dense_artifact_receipt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("BMS_DATA", str(tmp_path / "bms-data"))
    engine = create_engine(f"sqlite:///{tmp_path / 'design-update.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Design(
                id="design-update",
                job_id="job-update",
                name="before",
                pdb_path="update.pdb",
                residue_plddt=[0.5],
            )
        )
        session.commit()
        design = session.get(Design, "design-update")
        assert design is not None
        design.name = "after"
        session.commit()
    with engine.connect() as connection:
        receipt_count = connection.execute(
            text(
                "SELECT COUNT(*) FROM scientific_artifact_receipts "
                "WHERE owner_kind = 'design_field' AND owner_id = "
                "'design-update:residue_plddt'"
            )
        ).scalar_one()
    assert receipt_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installer_commits", "reuser_commits"),
    [(True, False), (False, True), (False, False)],
    ids=[
        "installer-commit-reuser-rollback",
        "installer-rollback-reuser-commit",
        "both-rollback",
    ],
)
async def test_transaction_interleavings_preserve_only_receipted_artifact_bytes(
    tmp_path, monkeypatch, installer_commits, reuser_commits
):
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("BMS_SCIENTIFIC_ARTIFACT_ROOT", str(artifact_root))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'interleaving.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    installer_session = sessions()
    reuser_session = sessions()
    publication = {
        "owner_kind": "transaction-test",
        "owner_id": "shared-owner",
        "role": "rows",
        "schema_id": "bms.transaction-test.v1",
        "source_sha256": "f" * 64,
        "rows": [{"value": 1}],
        "schema": pa.schema([("value", pa.int64())]),
    }
    try:
        installer = await publish_table_rows(installer_session, **publication)
        reuser = await publish_table_rows(reuser_session, **publication)
        if installer_commits:
            await installer_session.commit()
        else:
            await installer_session.rollback()
        if reuser_commits:
            await reuser_session.commit()
        else:
            await reuser_session.rollback()

        async with sessions() as verification_session:
            receipt_count = (
                await verification_session.execute(
                    text("SELECT COUNT(*) FROM scientific_artifact_receipts")
                )
            ).scalar_one()
        if installer_commits or reuser_commits:
            assert receipt_count == 1
            assert verify_artifact(reuser, root=artifact_root).is_file()
        else:
            assert receipt_count == 0
            assert not installer.storage_path.exists()
        assert not list(artifact_root.rglob("*.reused"))
        assert not list(artifact_root.rglob(".publication-ownership.json"))
    finally:
        await installer_session.close()
        await reuser_session.close()
        await engine.dispose()


def test_transactional_reuse_rejects_digest_mismatch_without_disarming_cleanup(tmp_path):
    import services.scientific_artifacts.writer as writer

    installer = _install_transactional_artifact(tmp_path, "installer", value=1)
    with pytest.raises(ScientificArtifactError, match="immutable artifact conflict"):
        _install_transactional_artifact(tmp_path, "mismatch", value=2)

    writer.finalize_artifact_publication(installer, committed=False)

    assert not installer.storage_path.exists()
    assert not list(installer.storage_path.parent.glob(f".{installer.storage_path.name}.*"))


def test_concurrent_exact_publication_remains_no_clobber(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(target=_publish_same_artifact, args=(tmp_path, barrier, results))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    artifacts = [results.get(timeout=2) for _ in processes]
    assert sum(artifact.newly_installed for artifact in artifacts) == 1
    assert {artifact.content_sha256 for artifact in artifacts} == {
        artifacts[0].content_sha256
    }
    assert verify_artifact(artifacts[0], root=tmp_path).is_file()
    assert not list(tmp_path.rglob("*.reused"))


def test_parquet_artifact_round_trips_exact_envelope_and_duckdb_page(tmp_path):
    payload = {
        "scalar": {"nested": [3, 2, 1]},
        "list": [{"b": 2, "a": 1}, {"value": 0}],
    }
    schema = pa.schema(
        [
            ("key", pa.string()),
            ("item_index", pa.int64()),
            ("payload_json", pa.string()),
        ]
    )
    installed = install_parquet_rows(
        root=tmp_path,
        owner_kind="cm_record",
        owner_id="request/one",
        role="payload",
        schema_id="bms.json-envelope.v1",
        schema_version=1,
        source_sha256="a" * 64,
        rows=envelope_rows(payload),
        schema=schema,
    )

    assert verify_artifact(installed, root=tmp_path) == installed.storage_path
    rows = query_rows(
        installed.reference(),
        columns=["key", "item_index", "payload_json"],
        limit=10,
        root=str(tmp_path),
    )
    assert reconstruct_envelope(rows) == payload


@pytest.mark.parametrize("symlink_kind", ["parent", "final"])
def test_parquet_queries_reject_symlink_components(tmp_path, symlink_kind):
    schema = pa.schema([("value", pa.int64())])
    installed = install_parquet_rows(
        root=tmp_path, owner_kind="symlink-test", owner_id="one", role="rows",
        schema_id="bms.test.v1", schema_version=1, source_sha256="1" * 64,
        rows=[{"value": 1}], schema=schema,
    )
    if symlink_kind == "parent":
        parent = installed.storage_path.parent
        real_parent = tmp_path / "real-by-id"
        parent.rename(real_parent)
        parent.symlink_to(real_parent, target_is_directory=True)
    else:
        real_file = tmp_path / "real.parquet"
        installed.storage_path.rename(real_file)
        installed.storage_path.symlink_to(real_file)

    with pytest.raises(ScientificArtifactError, match="symlink|regular|unsafe"):
        query_rows(installed.reference(), columns=["value"], limit=10, root=str(tmp_path))


def test_parquet_query_keeps_verified_descriptor_after_original_path_replacement(
    tmp_path, monkeypatch,
):
    import services.scientific_artifacts.query as query_service

    schema = pa.schema([("value", pa.int64())])
    installed = install_parquet_rows(
        root=tmp_path, owner_kind="race-test", owner_id="verified", role="rows",
        schema_id="bms.test.v1", schema_version=1, source_sha256="2" * 64,
        rows=[{"value": 1}], schema=schema,
    )
    replacement = install_parquet_rows(
        root=tmp_path, owner_kind="race-test", owner_id="replacement", role="rows",
        schema_id="bms.test.v1", schema_version=1, source_sha256="3" * 64,
        rows=[{"value": 999}], schema=schema,
    )
    original_read_schema = query_service.pq.read_schema
    replaced = False

    def replace_after_verification(path):
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(replacement.storage_path, installed.storage_path)
        return original_read_schema(path)

    monkeypatch.setattr(query_service.pq, "read_schema", replace_after_verification)

    rows = query_rows(installed.reference(), columns=["value"], limit=10, root=str(tmp_path))
    assert rows == [{"value": 1}]


def test_artifact_fingerprint_matches_persisted_parquet_schema(tmp_path):
    schema = pa.schema([("values", pa.list_(pa.float64()))])
    installed = install_parquet_rows(
        root=tmp_path,
        owner_kind="test",
        owner_id="list-schema",
        role="values",
        schema_id="bms.test-list.v1",
        schema_version=1,
        source_sha256="a" * 64,
        rows=[{"values": [1.0, 2.0]}],
        schema=schema,
    )
    persisted_schema = pq.read_schema(installed.storage_path)
    fields = [
        {"name": field.name, "type": str(field.type), "nullable": field.nullable}
        for field in persisted_schema
    ]
    expected = hashlib.sha256(canonical_json_bytes(fields)).hexdigest()
    assert installed.column_schema_sha256 == expected


def test_json_envelope_projection_and_collection_page_are_bounded(tmp_path):
    payload = {
        "schema_name": "cm_analysis",
        "schema_version": 1,
        "analysis_id": "analysis-1",
        "expected_strata": ["a", "b"],
        "results": [{"source_row_key": f"row-{index}"} for index in range(5)],
    }
    schema = pa.schema(
        [
            ("key", pa.string()),
            ("item_index", pa.int64()),
            ("payload_json", pa.string()),
        ]
    )
    installed = install_parquet_rows(
        root=tmp_path,
        owner_kind="cm_record",
        owner_id="request/one",
        role="payload",
        schema_id="bms.json-envelope.v1",
        schema_version=1,
        source_sha256="a" * 64,
        rows=envelope_rows(payload),
        schema=schema,
    )

    assert resolve_json_envelope_fields(
        installed.reference(),
        keys=["schema_name", "schema_version", "analysis_id", "expected_strata"],
        root=tmp_path,
    ) == {
        "schema_name": "cm_analysis",
        "schema_version": 1,
        "analysis_id": "analysis-1",
        "expected_strata": ["a", "b"],
    }
    first = query_json_envelope_page(
        installed.reference(), key="results", offset=0, limit=2, root=tmp_path,
    )
    assert first["total_count"] == 5
    assert first["next_offset"] == 2
    assert [row["source_row_key"] for row in first["rows"]] == ["row-0", "row-1"]
    last = query_json_envelope_page(
        installed.reference(), key="results", offset=4, limit=2, root=tmp_path,
    )
    assert last["next_offset"] is None
    assert [row["source_row_key"] for row in last["rows"]] == ["row-4"]


def test_row_reference_is_compact_and_resolves_value(tmp_path):
    payload = {"value": {"nested": [1, 2]}}
    schema = pa.schema(
        [
            ("key", pa.string()),
            ("item_index", pa.int64()),
            ("payload_json", pa.string()),
        ]
    )
    installed = install_parquet_rows(
        root=tmp_path,
        owner_kind="cm_record",
        owner_id="request/one",
        role="payload",
        schema_id="bms.json-envelope.v1",
        schema_version=1,
        source_sha256="a" * 64,
        rows=envelope_rows(payload),
        schema=schema,
    )
    reference = artifact_row_reference(installed.reference(), 0, value_field="payload_json")
    assert len(json.dumps(reference, separators=(",", ":"))) < 450
    assert resolve_json_value(reference, root=tmp_path) == {"nested": [1, 2]}


def test_redundant_frustra_indexes_are_removed():
    index_migration = importlib.import_module("migrations.add_frustrampnn_landscape_index_slimming")
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE frustrampnn_landscape_rows (id TEXT PRIMARY KEY, parent_job_id TEXT, invocation_id TEXT, target_id TEXT, entity_instance_id TEXT, status TEXT);
        CREATE INDEX ix_frustrampnn_landscape_rows_parent_job_id ON frustrampnn_landscape_rows(parent_job_id);
        CREATE INDEX ix_frustrampnn_landscape_rows_invocation_id ON frustrampnn_landscape_rows(invocation_id);
        CREATE INDEX ix_frustrampnn_landscape_rows_target_id ON frustrampnn_landscape_rows(target_id);
        CREATE INDEX ix_frustrampnn_landscape_rows_entity_instance_id ON frustrampnn_landscape_rows(entity_instance_id);
        CREATE INDEX ix_frustrampnn_landscape_rows_status ON frustrampnn_landscape_rows(status);
        """
    )
    index_migration.migrate(connection)
    remaining = {row[1] for row in connection.execute('PRAGMA index_list("frustrampnn_landscape_rows")')}
    assert remaining == {"ix_frustrampnn_landscape_rows_status", "sqlite_autoindex_frustrampnn_landscape_rows_1"}


def test_landscape_retirement_migration_requires_empty_tables_and_blocks_repopulation():
    retirement = importlib.import_module(
        "migrations.retire_scientific_landscape_projections"
    )
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE conformational_mapping_landscape_rows (
            id TEXT PRIMARY KEY, request_id TEXT
        );
        CREATE INDEX ix_cm_legacy_request
            ON conformational_mapping_landscape_rows(request_id);
        CREATE TABLE frustrampnn_landscape_rows (
            id TEXT PRIMARY KEY, parent_job_id TEXT
        );
        CREATE INDEX ix_fmpnn_legacy_parent
            ON frustrampnn_landscape_rows(parent_job_id);
        INSERT INTO conformational_mapping_landscape_rows VALUES ('cm-1', 'request-1');
        """
    )
    with pytest.raises(RuntimeError, match="still contain rows"):
        retirement.migrate(connection)
    connection.execute("DELETE FROM conformational_mapping_landscape_rows")
    connection.commit()
    retirement.migrate(connection)
    assert {
        row[1]
        for row in connection.execute(
            'PRAGMA index_list("conformational_mapping_landscape_rows")'
        )
    } == {"sqlite_autoindex_conformational_mapping_landscape_rows_1"}
    assert {
        row[1]
        for row in connection.execute('PRAGMA index_list("frustrampnn_landscape_rows")')
    } == {"sqlite_autoindex_frustrampnn_landscape_rows_1"}
    with pytest.raises(sqlite3.IntegrityError, match="retired"):
        connection.execute(
            "INSERT INTO frustrampnn_landscape_rows VALUES ('f-1', 'job-1')"
        )



    assert migration.design_field_rows("design-1", "confidence_metrics", {"b": 2, "a": 1}) == {
        "row_index": 0,
        "design_id": "design-1",
        "field_name": "confidence_metrics",
        "payload_json": "{\"a\":1,\"b\":2}",
    }


def test_design_field_rows_preserve_nan_values():
    row = migration.design_field_rows("design-1", "confidence_metrics", {"score": float("nan")})
    assert row["payload_json"] == "{\"score\":NaN}"


def test_statistics_source_sha_excludes_self_digest() -> None:
    payload = {"value": 3, "statistics_sha256": ""}
    payload["statistics_sha256"] = migration.statistics_source_sha(payload, None)
    assert migration.statistics_source_sha(payload, payload["statistics_sha256"]) == payload["statistics_sha256"]


def test_cm_landscape_backfill_publishes_complete_rows(tmp_path):
    ddl = """
        CREATE TABLE conformational_mapping_landscape_rows (
            id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            entity_instance_id TEXT NOT NULL,
            auth_asym_id TEXT NOT NULL,
            auth_seq_id TEXT NOT NULL,
            insertion_code TEXT NOT NULL,
            sequence_index INTEGER NOT NULL,
            wt TEXT NOT NULL,
            mutation_aa TEXT NOT NULL,
            score REAL,
            score_class TEXT,
            scoreable INTEGER NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            provenance_json TEXT NOT NULL
        );
    """
    source = sqlite3.connect(":memory:")
    target = sqlite3.connect(":memory:")
    source.executescript(ddl)
    target.executescript(ddl)
    migration.ensure_receipt_tables(target)
    values = (
        "row-1", "request-1", "candidate-1", "copy-1", "A", "3", "",
        3, "A", "V", -1.25, "high", 1, "ok", None,
        json.dumps({"container_sha256": "a" * 64}),
    )
    insert = (
        "INSERT INTO conformational_mapping_landscape_rows VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    source.execute(insert, values)
    target.execute(insert, values)
    source.commit()
    target.commit()

    assert migration.backfill_cm_landscape_provenance(
        source, target, tmp_path
    ) == 1
    receipt = target.execute(
        "SELECT role, schema_id, row_count FROM scientific_artifact_receipts"
    ).fetchone()
    assert receipt == ("rows", "bms.cm-landscape.v1", 1)
    artifact_path = next(tmp_path.rglob("*.parquet"))
    artifact_rows = migration.pq.read_table(artifact_path).to_pylist()
    assert artifact_rows == [{
        "row_index": 0,
        "id": "row-1",
        "candidate_id": "candidate-1",
        "entity_instance_id": "copy-1",
        "auth_asym_id": "A",
        "auth_seq_id": "3",
        "insertion_code": "",
        "sequence_index": 3,
        "wt": "A",
        "mutation_aa": "V",
        "score": -1.25,
        "score_class": "high",
        "scoreable": True,
        "status": "ok",
        "reason": None,
        "provenance_json": json.dumps(
            {"container_sha256": "a" * 64}, separators=(",", ":"), sort_keys=True
        ),
    }]


def test_frustrampnn_comparison_backfill_replaces_inline_payload(tmp_path):
    source = sqlite3.connect(":memory:")
    target = sqlite3.connect(":memory:")
    ddl = """
        CREATE TABLE frustrampnn_comparisons (
            comparison_id TEXT PRIMARY KEY,
            comparison_sha256 TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
    """
    source.executescript(ddl)
    target.executescript(ddl)
    migration.ensure_receipt_tables(target)
    payload = {
        "schema_name": "frustrampnn_comparison",
        "schema_version": 1,
        "comparison_id": "comparison-1",
        "rows": [{"residue": index} for index in range(3)],
    }
    digest = migration.canonical_sha256(payload)
    source.execute(
        "INSERT INTO frustrampnn_comparisons VALUES (?, ?, ?)",
        ("comparison-1", digest, json.dumps(payload)),
    )
    target.execute(
        "INSERT INTO frustrampnn_comparisons VALUES (?, ?, ?)",
        ("comparison-1", digest, json.dumps(payload)),
    )
    source.commit()
    target.commit()

    assert migration.backfill_frustrampnn_comparisons(
        source, target, tmp_path
    ) == 1
    raw_reference = json.loads(
        target.execute(
            "SELECT payload_json FROM frustrampnn_comparisons"
        ).fetchone()[0]
    )
    assert raw_reference["schema"] == "bms.scientific-artifact-reference.v1"
    assert resolve_json_value(raw_reference, root=tmp_path) == payload


def test_envelope_reconstructs_empty_lists():
    payload = {"empty": [], "scalar": "ok"}
    assert reconstruct_envelope(envelope_rows(payload)) == payload


def test_artifact_verification_rejects_tampered_bytes(tmp_path):
    schema = pa.schema([("value", pa.int64())])
    installed = install_parquet_rows(
        root=tmp_path,
        owner_kind="test",
        owner_id="one",
        role="values",
        schema_id="bms.test.v1",
        schema_version=1,
        source_sha256="b" * 64,
        rows=[{"value": 1}],
        schema=schema,
    )
    installed.storage_path.write_bytes(installed.storage_path.read_bytes() + b"tamper")

    with pytest.raises(ScientificArtifactError, match="do not match"):
        verify_artifact(installed, root=tmp_path)


def test_scientific_receipt_migration_is_idempotent_and_foreign_key_bound(tmp_path):
    db_path = tmp_path / "core.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()

    migrate(db_path)
    migrate(db_path)

    connection = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "scientific_artifact_receipts",
        "scientific_payload_migrations",
    } <= tables
    connection.execute(
        "INSERT INTO scientific_artifact_receipts "
        "(artifact_id, owner_kind, owner_id, role, schema_id, artifact_schema_version, "
        "content_sha256, size_bytes, row_count, column_schema_sha256, storage_root, "
        "relative_path, media_type, availability, source_receipts_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            "artifact-1", "test", "one", "values", "bms.test.v1", 1,
            "c" * 64, 1, 1, "d" * 64, "test-root", "test/one.parquet",
            "application/vnd.apache.parquet", "available", json.dumps({}),
        ),
    )
    connection.execute(
        "INSERT INTO scientific_payload_migrations "
        "(migration_id, source_store, source_table, source_column, source_key, source_sha256, "
        "artifact_id, state, attempt_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (
            "migration-1", "core", "test", "payload_json", "one", "e" * 64,
            "artifact-1", "completed", 1,
        ),
    )
    connection.commit()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_migration_44_seals_only_literature_backed_ont_metric_receipts(tmp_path):
    from migrations.runner import MIGRATIONS

    db_path = tmp_path / "metric-receipts.sqlite3"
    migrate(db_path)
    migration = next((item for item in MIGRATIONS if item.version == 44), None)
    assert migration is not None
    assert migration.name == "seal_ont_read_metric_receipt_immutability"
    migration.fn(db_path)

    connection = sqlite3.connect(db_path)
    insert_sql = (
        "INSERT INTO scientific_artifact_receipts "
        "(artifact_id, owner_kind, owner_id, role, schema_id, artifact_schema_version, "
        "content_sha256, size_bytes, row_count, column_schema_sha256, storage_root, "
        "relative_path, media_type, availability, source_receipts_json, created_at) "
        "VALUES (?, ?, ?, ?, 'bms.test.v1', 1, ?, 1, 1, ?, 'test-root', ?, "
        "'application/vnd.apache.parquet', 'available', '{}', datetime('now'))"
    )
    protected = (
        "protected", "ont_raw_signal_representation", "rep-1",
        "literature_backed_read_metrics", "a" * 64, "b" * 64, "protected.parquet",
    )
    ordinary = (
        "ordinary", "other-owner", "owner-1", "other-role",
        "c" * 64, "d" * 64, "ordinary.parquet",
    )
    connection.execute(insert_sql, protected)
    connection.execute(insert_sql, ordinary)
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE scientific_artifact_receipts SET availability='missing' WHERE artifact_id='protected'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("DELETE FROM scientific_artifact_receipts WHERE artifact_id='protected'")

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE scientific_artifact_receipts "
            "SET owner_kind='ont_raw_signal_representation', "
            "role='literature_backed_read_metrics' "
            "WHERE artifact_id='ordinary'"
        )

    connection.execute(
        "UPDATE scientific_artifact_receipts SET availability='unavailable' WHERE artifact_id='ordinary'"
    )
    connection.execute("DELETE FROM scientific_artifact_receipts WHERE artifact_id='ordinary'")
    connection.commit()
    assert connection.execute(
        "SELECT availability FROM scientific_artifact_receipts WHERE artifact_id='protected'"
    ).fetchone() == ("available",)
    connection.close()
