from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from migrations.sqlite_sha256 import register_sqlite_sha256
from sqlalchemy import select

import experiment_migrations as migration_module
from experiment_database import create_experiment_engine, create_experiment_session_factory, get_experiment_session
from experiment_migrations import (
    LEGACY_MIGRATION_NAME,
    LEGACY_MIGRATION_VERSION,
    MIGRATION_NAME,
    MIGRATION_SQL,
    MIGRATION_V2_SQL,
    MIGRATION_V3_NAME,
    MIGRATION_V3_VERSION,
    MIGRATION_V4_NAME,
    MIGRATION_V4_VERSION,
    MIGRATION_V5_NAME,
    MIGRATION_V5_VERSION,
    MIGRATION_V6_NAME,
    MIGRATION_V6_VERSION,
    MIGRATION_V7_NAME,
    MIGRATION_V7_VERSION,
    MIGRATION_V8_NAME,
    MIGRATION_V8_VERSION,
    MIGRATION_V9_NAME,
    MIGRATION_V9_VERSION,
    MIGRATION_VERSION,
    attest_schema,
    migration_checksum,
    run_all,
)
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentExternalEntityReceipt,
    ExperimentLineageEdge,
    ExperimentResource,
    ExperimentResearchRecord,
    ExperimentRevisionEdge,
    ExperimentRevision,
    ExperimentWorkflowRun,
)
from experiment_operations import (
    build_workspace_export,
    create_online_backup,
    register_external_entity_receipt,
    verify_backup,
    verify_workspace_export,
)
from experiment_services import (
    DispatchFailure,
    ExistingJobMaterializer,
    LIVE_CORE_JOB_STATE_MAP,
    _validate_workflow_payload,
    ValidationFailure,
    create_dataset,
    create_domain_experiment,
    create_global_experiment,
    create_project,
    create_workflow,
    save_dataset_revision,
    save_hierarchy_revision,
)
from routers.experiment_workspaces import router as compatibility_router
from routers.projects import router as projects_router
from routers.project_manager import _project_bound_job
from database import Job
from services.global_experiments.launch_contexts import (
    LaunchContextError,
    claim_launch_context,
    consume_launch_context,
    create_launch_context,
    resolve_launch_context,
    resolve_launch_context_for_display,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_live_core_states_project_into_persisted_state_vocabulary():
    assert LIVE_CORE_JOB_STATE_MAP["queued"] == "dispatched"
    assert LIVE_CORE_JOB_STATE_MAP["pending"] == "dispatched"
    assert LIVE_CORE_JOB_STATE_MAP["processing"] == "running"
    assert set(LIVE_CORE_JOB_STATE_MAP.values()) <= {"dispatched", "running"}


def test_workflow_contract_rejects_unknown_and_execution_alias_keys():
    base = {
        "schema": "bms.workflow.generic.v1",
        "workflow_family": "generic_test",
        "contract_version": "1",
        "adapter_id": "generic.test.adapter.v1",
        "nodes": [{"id": "main", "kind": "scheduler_job", "required": True}],
        "edges": [],
        "parameters": {"seed": 101},
        "scheduler": {"name": "generic", "model_id": "generic_test", "mode": "predict", "params": {"seed": 101}},
    }
    invalid_payloads = [
        {**base, "unknown": True},
        {**base, "nodes": [{**base["nodes"][0], "loader": "module:callable"}]},
        {**base, "edges": [{"source": "main", "target": "main", "plugin": "unsafe"}]},
        {**base, "scheduler": {**base["scheduler"], "callable": "unsafe"}},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationFailure):
            _validate_workflow_payload(payload)


@pytest.mark.asyncio
async def test_dispatch_rejects_unregistered_generic_materializer():
    materializer = ExistingJobMaterializer(None)  # type: ignore[arg-type]
    with pytest.raises(DispatchFailure, match="no registered typed materializer"):
        await materializer.materialize(
            "attempt-generic",
            {
                "scheduler": {
                    "params": {"workflow_adapter": "generic_test"},
                },
                "workflow_run_id": "run-generic",
                "attempt_id": "attempt-generic",
            },
        )


@pytest_asyncio.fixture
async def project_store(tmp_path: Path):
    db_path = tmp_path / "experiments.db"
    run_all(db_path)
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_experiment_session_factory(engine)
    try:
        yield db_path, factory
    finally:
        await engine.dispose()


def _remove_parent_revision_authority_migration(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("DELETE FROM experiment_schema_migrations WHERE version >= 20")
        connection.execute("DROP TRIGGER IF EXISTS trg_experiment_workflow_setup_contract_immutable")
        connection.execute("DROP TRIGGER IF EXISTS trg_experiment_workflow_setup_terminal_immutable")
        connection.execute("DROP INDEX IF EXISTS ix_experiment_workflow_setups_project_updated")
        connection.execute("DROP INDEX IF EXISTS ix_experiment_workflow_setups_experiment")
        connection.execute("DROP TABLE IF EXISTS workflow_setup_contexts")
        connection.execute("DROP TRIGGER IF EXISTS trg_experiment_domain_parent_global_revision_insert")
        connection.execute("DROP TRIGGER IF EXISTS trg_experiment_revision_edge_immutable_delete")
        connection.execute("DROP INDEX IF EXISTS ux_experiment_domain_parent_global_revision")
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_parent_global_revision_migration_backfills_historical_domain_intervals_idempotently(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "historical-parent-revisions.db"
    run_all(db_path)
    _remove_parent_revision_authority_migration(db_path)
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_experiment_session_factory(engine)
    async with factory() as session:
        project = await create_project(session, _project_payload())
        global_experiment = await create_global_experiment(
            session, project.id, _experiment_payload()
        )
        domain = await create_domain_experiment(
            session, project.id, global_experiment.id, _domain_payload()
        )
        global_one_id = global_experiment.current_revision_id
        domain_one_id = domain.current_revision_id
        global_one = await session.get(ExperimentRevision, global_one_id)
        assert global_one is not None
        global_payload = json.loads(global_one.canonical_payload)
        global_payload["change_summary"] = "second Global interval"
        global_two = await save_hierarchy_revision(
            session,
            global_experiment.id,
            "experiment",
            global_payload,
            expected_head_generation=global_experiment.head_generation,
        )
        domain_one = await session.get(ExperimentRevision, domain_one_id)
        assert domain_one is not None
        domain_payload = json.loads(domain_one.canonical_payload)
        domain_payload["change_summary"] = "second Domain interval"
        domain_two = await save_hierarchy_revision(
            session,
            domain.id,
            "domain_experiment",
            domain_payload,
            expected_head_generation=domain.head_generation,
        )
        await session.commit()
        await session.execute(
            ExperimentRevisionEdge.__table__.delete().where(
                ExperimentRevisionEdge.role == "parent_global_revision"
            )
        )
        await session.commit()
        global_one = await session.get(ExperimentRevision, global_one_id)
        assert global_one is not None
        expected = {
            domain_one_id: (global_one_id, global_one.payload_sha256),
            domain_two.resource_id: (global_two.resource_id, global_two.payload_sha256),
        }
    await engine.dispose()
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER trg_experiment_revision_edge_immutable_delete
            BEFORE DELETE ON revision_edges
            BEGIN SELECT RAISE(ABORT, 'immutable revision edge'); END
            """
        )
        connection.commit()
    finally:
        connection.close()

    run_all(db_path)
    run_all(db_path)

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT revision_id, target_resource_id, expected_sha256 "
            "FROM revision_edges WHERE role = 'parent_global_revision' ORDER BY revision_id"
        ).fetchall()
        assert {row[0]: (row[1], row[2]) for row in rows} == expected
        assert connection.execute(
            "SELECT count(*) FROM experiment_schema_migrations WHERE version = 20"
        ).fetchone() == (1,)
    finally:
        connection.close()

    _remove_parent_revision_authority_migration(db_path)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO revision_edges(
                revision_id, target_resource_id, role, ordinal,
                expected_sha256, metadata_json
            ) VALUES (?, ?, 'parent_global_revision', 1, ?, '{"authority":"server_resolved"}')
            """,
            (domain_one_id, global_two.resource_id, global_two.payload_sha256),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="conflicting parent Global revision authority"):
        run_all(db_path)


def _app(factory) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _authenticated_test_operator(request, call_next):
        request.state.authenticated_principal = {
            "id": "operator",
            "roles": ["operator"],
        }
        return await call_next(request)

    app.include_router(compatibility_router)
    app.include_router(projects_router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_experiment_session] = override_session
    return app


def _project_payload(name: str = "Project A") -> dict:
    return {
        "schema": "bms.project.v1",
        "name": name,
        "description": "Project description",
        "research_objective": "Research objective",
        "owner": "operator",
        "contributors": ["scientist"],
        "tags": ["protein"],
        "status": "draft",
        "start_date": "2026-08-09",
        "target_end_date": None,
        "external_references": [],
        "created_by": "operator",
        "change_summary": "created",
    }


def _experiment_payload(name: str = "Experiment A") -> dict:
    return {
        "schema": "bms.global-experiment.v1",
        "name": name,
        "objective": "Compare candidates",
        "scientific_question": "Which candidate is stable?",
        "hypothesis": None,
        "description": "Experiment description",
        "status": "draft",
        "priority": "normal",
        "tags": [],
        "shared_source_receipt_ids": [],
        "shared_dataset_ids": [],
        "comparison_plan": None,
        "success_criteria": ["Review evidence"],
        "review_summary": None,
        "conclusion": None,
        "created_by": "operator",
        "change_summary": "created",
    }


def _domain_payload(kind: str = "protein_in_silico", name: str = "Domain A") -> dict:
    return {
        "schema": "bms.domain-experiment.v1",
        "domain_kind": kind,
        "domain_contract_version": "1",
        "name": name,
        "objective": "Generate candidates",
        "status": "draft",
        "tags": [],
        "source_receipt_ids": [],
        "dataset_ids": [],
        "created_by": "operator",
        "change_summary": "created",
        "domain_payload": (
            {
                "schema": "bms.protein-in-silico-experiment.v1",
                "experiment_mode": "design",
                "targets": [
                    {
                        "target_id": "target-1",
                        "label": "Target 1",
                        "entity_receipt_ids": [],
                        "role": "target",
                    }
                ],
                "scientific_objective": "Generate candidates",
                "design_constraints": [],
                "planned_capabilities": ["rfd3_local_redesign"],
                "comparison_groups": [],
                "validation_strategy": ["boltz2"],
            }
            if kind == "protein_in_silico"
            else {"schema": "bms.ngs-molbio-experiment.v1"}
        ),
    }


@pytest.mark.asyncio
async def test_fresh_and_v2_to_new_migrations_preserve_rows_and_attest(project_store, tmp_path: Path):
    db_path, _factory = project_store
    connection = sqlite3.connect(db_path)
    try:
        ledger = connection.execute(
            "SELECT version, name FROM experiment_schema_migrations ORDER BY version"
        ).fetchall()
        assert ledger == [
            (version, name)
            for version, name, _checksum in migration_module._accepted_migration_ledgers()[0]
        ]
        assert attest_schema(connection)["ok"] is True
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'research_records'"
        ).fetchone() == ("research_records",)
    finally:
        connection.close()

    legacy_path = tmp_path / "v2.db"
    legacy = sqlite3.connect(legacy_path)
    try:
        legacy.execute("PRAGMA foreign_keys=ON")
        legacy.executescript(MIGRATION_SQL)
        legacy.execute(
            """
            CREATE TABLE experiment_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        legacy.execute(
            "INSERT INTO experiment_schema_migrations VALUES (?, ?, ?, '', 'legacy')",
            (2, "global_experiment_workspace_receipts_and_projections", _sha(MIGRATION_V2_SQL)),
        )
        legacy.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) VALUES (?, 'workspace', NULL, NULL, ?)",
            ("project-retained", "2026-08-09T00:00:00Z"),
        )
        legacy.execute(
            "INSERT INTO aggregate_heads(aggregate_id, aggregate_kind, workspace_id, parent_id, lifecycle_state, display_name, description, created_at, updated_at) VALUES (?, 'workspace', ?, NULL, 'draft', 'Retained', '', ?, ?)",
            ("project-retained", "project-retained", "2026-08-09T00:00:00Z", "2026-08-09T00:00:00Z"),
        )
        legacy.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) VALUES (?, 'experiment', ?, ?, ?)",
            ("experiment-retained", "project-retained", "project-retained", "2026-08-09T00:00:00Z"),
        )
        legacy.execute(
            "INSERT INTO aggregate_heads(aggregate_id, aggregate_kind, workspace_id, parent_id, lifecycle_state, display_name, description, created_at, updated_at) VALUES (?, 'experiment', ?, ?, 'completed', 'Retained experiment', '', ?, ?)",
            ("experiment-retained", "project-retained", "project-retained", "2026-08-09T00:00:00Z", "2026-08-09T00:00:00Z"),
        )
        legacy.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) VALUES (?, 'workflow', ?, ?, ?)",
            ("workflow-needs-domain", "project-retained", "project-retained", "2026-08-09T00:00:00Z"),
        )
        legacy.execute(
            "INSERT INTO aggregate_heads(aggregate_id, aggregate_kind, workspace_id, parent_id, lifecycle_state, display_name, description, created_at, updated_at) VALUES (?, 'workflow', ?, ?, 'draft', 'Needs domain', '', ?, ?)",
            ("workflow-needs-domain", "project-retained", "project-retained", "2026-08-09T00:00:00Z", "2026-08-09T00:00:00Z"),
        )
        legacy.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) VALUES (?, 'workflow', ?, ?, ?)",
            ("workflow-cm-retained", "project-retained", "experiment-retained", "2026-08-09T00:00:00Z"),
        )
        legacy.execute(
            "INSERT INTO aggregate_heads(aggregate_id, aggregate_kind, workspace_id, parent_id, lifecycle_state, display_name, description, created_at, updated_at) VALUES (?, 'workflow', ?, ?, 'validated', 'Retained CM', 'bms.cm.protenix_v2.adapter.v1', ?, ?)",
            ("workflow-cm-retained", "project-retained", "experiment-retained", "2026-08-09T00:00:00Z", "2026-08-09T00:00:00Z"),
        )
        legacy.commit()
    finally:
        legacy.close()

    run_all(legacy_path)
    migrated = sqlite3.connect(legacy_path)
    try:
        assert migrated.execute(
            "SELECT id, kind FROM resources WHERE id IN ('project-retained', 'experiment-retained') ORDER BY id"
        ).fetchall() == [("experiment-retained", "experiment"), ("project-retained", "workspace")]
        assert migrated.execute(
            "SELECT aggregate_id, aggregate_kind, parent_id, lifecycle_state, head_generation FROM aggregate_heads WHERE aggregate_id IN ('project-retained', 'experiment-retained') ORDER BY aggregate_id"
        ).fetchall() == [
            ("experiment-retained", "experiment", "project-retained", "review", 1),
            ("project-retained", "workspace", None, "draft", 1),
        ]
        assert migrated.execute(
            "SELECT lifecycle_state, parent_id FROM aggregate_heads WHERE aggregate_id = 'workflow-needs-domain'"
        ).fetchone() == ("needs_domain_assignment", "project-retained")
        cm_binding = migrated.execute(
            """
            SELECT workflow.parent_id, owner.lifecycle_owner_id, domain.aggregate_kind, domain.parent_id
            FROM aggregate_heads AS workflow
            JOIN resources AS owner ON owner.id = workflow.aggregate_id
            JOIN aggregate_heads AS domain ON domain.aggregate_id = workflow.parent_id
            WHERE workflow.aggregate_id = 'workflow-cm-retained'
            """
        ).fetchone()
        assert cm_binding is not None
        assert cm_binding[0] == cm_binding[1]
        assert cm_binding[2:] == ("domain_experiment", "experiment-retained")
        assert migrated.execute(
            """
            SELECT count(*) FROM lineage_edges
            WHERE source_resource_id = ? AND target_resource_id = 'workflow-cm-retained'
              AND edge_mode = 'owns' AND edge_key = 'migration:v7:cm-workflow'
            """,
            (cm_binding[0],),
        ).fetchone() == (1,)
        assert migrated.execute(
            "SELECT count(*) FROM audit_events WHERE resource_id = 'workflow-cm-retained' AND event_type = 'legacy_cm_workflow_bound'"
        ).fetchone() == (1,)
        migrated_payloads = [
            json.loads(row[0])
            for row in migrated.execute(
                "SELECT canonical_payload FROM revisions WHERE subject_id IN ('project-retained', 'experiment-retained') ORDER BY subject_id"
            ).fetchall()
        ]
        assert [payload["schema"] for payload in migrated_payloads] == [
            "bms.global-experiment.v1",
            "bms.project.v1",
        ]
        assert all(payload["needs_metadata_review"] is True for payload in migrated_payloads)
        assert migrated_payloads[0]["status"] == "review"
        migrated_provenance = json.loads(
            migrated.execute(
                "SELECT provenance_json FROM revisions WHERE subject_id = 'experiment-retained'"
            ).fetchone()[0]
        )
        assert migrated_provenance["legacy_lifecycle_state"] == "completed"
        assert migrated.execute(
            "SELECT version, name FROM experiment_schema_migrations ORDER BY version"
        ).fetchall() == [
            (version, name)
            for version, name, _checksum in migration_module._accepted_migration_ledgers()[0]
        ]
        assert attest_schema(migrated)["ok"] is True
        migrated.execute("DROP TRIGGER trg_experiment_research_record_immutable_update")
        migrated.execute(
            "CREATE TRIGGER trg_experiment_research_record_immutable_update BEFORE UPDATE ON research_records BEGIN SELECT 1; END"
        )
        malformed_attestation = attest_schema(migrated)
        assert malformed_attestation["ok"] is False
        assert malformed_attestation["definition_errors"]
    finally:
        migrated.close()


def test_genuine_v2_receipt_schema_migrates_legacy_rows_with_unverified_authority(
    tmp_path: Path,
):
    db_path = tmp_path / "genuine-v2-receipts.db"
    previous_v2_sql = MIGRATION_SQL.replace(
        "    verification_authority TEXT NOT NULL DEFAULT 'legacy_unverified' CHECK (length(verification_authority) > 0),\n",
        "",
    )
    assert previous_v2_sql != MIGRATION_SQL

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(previous_v2_sql)
        connection.execute(
            """
            CREATE TABLE experiment_schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                description TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO experiment_schema_migrations VALUES (?, ?, ?, '', 'legacy')",
            (2, "global_experiment_workspace_receipts_and_projections", _sha(MIGRATION_V2_SQL)),
        )
        connection.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES ('project-v2', 'workspace', NULL, NULL, '2026-08-09T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES ('receipt-v2', 'external_entity_receipt', 'project-v2', 'project-v2', '2026-08-09T00:00:00Z')"
        )
        connection.execute(
            """
            INSERT INTO external_entity_receipts(
                id, workspace_id, resource_id, store_id, entity_kind, entity_id,
                generation_or_revision, content_digest, availability,
                acknowledgement_json, created_at
            ) VALUES (
                'receipt-v2', 'project-v2', 'receipt-v2', 'core', 'job', 'job-v2',
                '1', ?, 'available', '{}', '2026-08-09T00:00:00Z'
            )
            """,
            ("a" * 64,),
        )
        connection.commit()
        assert "verification_authority" not in {
            row[1] for row in connection.execute("PRAGMA table_info(external_entity_receipts)")
        }
    finally:
        connection.close()

    run_all(db_path)

    migrated = sqlite3.connect(db_path)
    try:
        assert migrated.execute(
            "SELECT verification_authority FROM external_entity_receipts WHERE id = 'receipt-v2'"
        ).fetchone() == ("legacy_unverified",)
        assert migrated.execute(
            "SELECT version, name FROM experiment_schema_migrations ORDER BY version"
        ).fetchall() == [
            (version, name)
            for version, name, _checksum in migration_module._accepted_migration_ledgers()[0]
        ]
        assert attest_schema(migrated)["ok"] is True
    finally:
        migrated.close()


def test_schema_attestation_preserves_quoted_literal_case(tmp_path: Path):
    db_path = tmp_path / "literal-case.db"
    run_all(db_path)
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            ("trg_experiment_research_record_immutable_update",),
        ).fetchone()
        assert row is not None
        original_sql = str(row[0])
        altered_sql = original_sql.replace(
            "research record is append-only",
            "RESEARCH RECORD IS APPEND-ONLY",
        )
        assert altered_sql != original_sql
        connection.execute("DROP TRIGGER trg_experiment_research_record_immutable_update")
        connection.execute(altered_sql)
        attestation = attest_schema(connection)
        assert attestation["ok"] is False
        assert attestation["definition_errors"]
    finally:
        connection.close()


def test_historical_migration_authority_is_frozen_against_runtime_sql_mutation(
    tmp_path: Path,
    monkeypatch,
):
    frozen_checksum = migration_module._migration_v3_checksum()
    monkeypatch.setattr(
        migration_module,
        "MIGRATION_V3_SQL",
        migration_module.MIGRATION_V3_SQL + "\nCREATE TABLE migration_drift_probe(id TEXT);\n",
    )

    assert migration_module._migration_v3_checksum() == frozen_checksum

    db_path = tmp_path / "mutated-v3.db"
    migration_module._expected_schema_definition_manifest.cache_clear()
    with pytest.raises(RuntimeError, match="frozen migration v3 checksum mismatch"):
        migration_module.run_all(db_path)


def test_external_entity_receipts_are_immutable_and_attested_at_sqlite_boundary(tmp_path: Path):
    db_path = tmp_path / "immutable-receipts.db"
    run_all(db_path)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        assert connection.execute(
            "SELECT version, name FROM experiment_schema_migrations WHERE version = ?",
            (MIGRATION_V4_VERSION,),
        ).fetchone() == (MIGRATION_V4_VERSION, MIGRATION_V4_NAME)
        connection.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES ('project-1', 'workspace', NULL, NULL, '2026-08-09T00:00:00Z')"
        )
        connection.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES ('receipt-1', 'external_entity_receipt', 'project-1', 'project-1', '2026-08-09T00:00:00Z')"
        )
        connection.execute(
            """
            INSERT INTO external_entity_receipts(
                id, workspace_id, resource_id, store_id, entity_kind, entity_id,
                generation_or_revision, content_digest, availability,
                verification_authority, acknowledgement_json, created_at
            ) VALUES (
                'receipt-1', 'project-1', 'receipt-1', 'core', 'job', 'job-1',
                '1', ?, 'available', 'adapter.v1', '{}', '2026-08-09T00:00:00Z'
            )
            """,
            ("a" * 64,),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="external entity receipt is immutable"):
            connection.execute(
                "UPDATE external_entity_receipts SET availability = 'unavailable' WHERE id = 'receipt-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="external entity receipt is immutable"):
            connection.execute("DELETE FROM external_entity_receipts WHERE id = 'receipt-1'")

        assert attest_schema(connection)["ok"] is True
        connection.execute("DROP TRIGGER trg_experiment_external_entity_receipt_immutable_update")
        connection.execute(
            "CREATE TRIGGER trg_experiment_external_entity_receipt_immutable_update "
            "BEFORE UPDATE ON research_records BEGIN SELECT 1; END"
        )
        malformed = attest_schema(connection)
        assert malformed["ok"] is False
        assert any("external_entity_receipt_immutable_update" in error for error in malformed["definition_errors"])
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_project_global_domain_hierarchy_is_typed_and_isolated(project_store):
    _db_path, factory = project_store
    app = _app(factory)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        project_response = await client.post("/api/projects", json=_project_payload())
        assert project_response.status_code == 201, project_response.text
        project = project_response.json()
        assert project["kind"] == "project"
        assert project["storage_kind"] == "workspace"

        revised_project = await client.patch(
            f"/api/projects/{project['id']}",
            json={
                "expected_head_generation": project["head_generation"],
                "description": "Revised project description",
            },
        )
        assert revised_project.status_code == 200, revised_project.text
        assert revised_project.json()["description"] == "Revised project description"
        stale_project = await client.patch(
            f"/api/projects/{project['id']}",
            json={"expected_head_generation": project["head_generation"], "description": "stale"},
        )
        assert stale_project.status_code == 409

        assert (await client.get("/api/projects")).json()["items"][0]["id"] == project["id"]
        assert (await client.get(f"/api/projects/{project['id']}")).json()["id"] == project["id"]

        experiment_response = await client.post(
            f"/api/projects/{project['id']}/experiments", json=_experiment_payload()
        )
        assert experiment_response.status_code == 201, experiment_response.text
        experiment = experiment_response.json()
        assert experiment["kind"] == "global_experiment"
        assert experiment["parent_id"] == project["id"]

        first_domain_response = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains",
            json=_domain_payload(),
        )
        second_domain_response = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains",
            json=_domain_payload(name="Domain B"),
        )
        assert first_domain_response.status_code == 201, first_domain_response.text
        assert second_domain_response.status_code == 201, second_domain_response.text
        first_domain = first_domain_response.json()
        second_domain = second_domain_response.json()
        assert first_domain["kind"] == second_domain["kind"] == "domain_experiment"
        assert first_domain["domain_kind"] == "protein_in_silico"
        assert first_domain["parent_id"] == experiment["id"]
        assert {row["id"] for row in (await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains"
        )).json()["items"]} == {first_domain["id"], second_domain["id"]}

        other_project = (await client.post("/api/projects", json=_project_payload("Project B"))).json()
        other_experiment = (await client.post(
            f"/api/projects/{other_project['id']}/experiments", json=_experiment_payload("Experiment B")
        )).json()
        foreign_get = await client.get(
            f"/api/projects/{other_project['id']}/experiments/{experiment['id']}"
        )
        foreign_domain = await client.post(
            f"/api/projects/{other_project['id']}/experiments/{other_experiment['id']}/domains",
            json={**_domain_payload(), "parent_id": experiment["id"]},
        )
        assert foreign_get.status_code == 404
        assert foreign_domain.status_code == 422

        invalid_kind = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains",
            json=_domain_payload("liquid_handler"),
        )
        extra_field = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains",
            json={**_domain_payload(), "unexpected": True},
        )
        assert invalid_kind.status_code == 422
        assert extra_field.status_code == 422

        wrong_domain_schema = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains",
            json={**_domain_payload(), "domain_payload": {"schema": "bms.ngs-molbio-experiment.v1"}},
        )
        mutable_domain_kind = await client.patch(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains/{first_domain['id']}",
            json={
                "expected_head_generation": first_domain["head_generation"],
                "domain_kind": "ngs_molbio",
            },
        )
        archived_project_create = await client.post(
            "/api/projects",
            json={**_project_payload("Invalid archived project"), "status": "archived"},
        )
        archived_experiment_create = await client.post(
            f"/api/projects/{project['id']}/experiments",
            json={**_experiment_payload("Invalid archived experiment"), "status": "archived"},
        )
        incomplete_terminal_review = await client.post(
            f"/api/projects/{project['id']}/experiments",
            json={**_experiment_payload("Invalid completed experiment"), "status": "completed"},
        )
        assert wrong_domain_schema.status_code == 422
        assert mutable_domain_kind.status_code == 422
        assert archived_project_create.status_code == 422
        assert archived_experiment_create.status_code == 422
        assert incomplete_terminal_review.status_code == 422

    async with factory() as session:
        domain_resource = await session.get(ExperimentResource, first_domain["id"])
        assert domain_resource is not None
        assert domain_resource.lifecycle_owner_id == experiment["id"]
        owns_edge = (
            await session.execute(
                select(ExperimentLineageEdge).where(
                    ExperimentLineageEdge.source_resource_id == experiment["id"],
                    ExperimentLineageEdge.target_resource_id == first_domain["id"],
                    ExperimentLineageEdge.edge_mode == "owns",
                )
            )
        ).scalar_one()
        assert owns_edge.workspace_id == project["id"]


@pytest.mark.asyncio
async def test_workflow_can_be_owned_by_a_domain_experiment(project_store):
    _db_path, factory = project_store
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        project_response = await client.post("/api/projects", json=_project_payload())
        assert project_response.status_code == 201, project_response.text
        project = project_response.json()
        global_response = await client.post(
            f"/api/projects/{project['id']}/experiments", json=_experiment_payload()
        )
        assert global_response.status_code == 201, global_response.text
        global_experiment = global_response.json()
        domain_response = await client.post(
            f"/api/projects/{project['id']}/experiments/{global_experiment['id']}/domains",
            json=_domain_payload(),
        )
        assert domain_response.status_code == 201, domain_response.text
        domain = domain_response.json()

    async with factory() as session:
        workflow = await create_workflow(
            session,
            project["id"],
            "Canonical Domain workflow",
            "rfd3",
            experiment_id=domain["id"],
        )
        await session.commit()
        resource = await session.get(ExperimentResource, workflow.aggregate_id)
        assert workflow.workspace_id == project["id"]
        assert workflow.parent_id == domain["id"]
        assert resource is not None
        assert resource.lifecycle_owner_id == domain["id"]


@pytest.mark.asyncio
async def test_launch_context_is_one_time_and_keeps_verified_return_binding(project_store):
    _, factory = project_store
    async with factory() as session:
        project = await create_project(session, _project_payload("Launch context"))
        global_experiment = await create_global_experiment(
            session,
            project.aggregate_id,
            _experiment_payload("Launch global"),
        )
        domain = await create_domain_experiment(
            session,
            project.aggregate_id,
            global_experiment.aggregate_id,
            _domain_payload("protein_in_silico"),
        )
        return_uri = (
            f"/projects/{project.aggregate_id}?focus={global_experiment.aggregate_id}"
            f"&selected=domain_experiment:{domain.aggregate_id}"
        )
        context = await create_launch_context(
            session,
            project_id=project.aggregate_id,
            global_experiment_id=global_experiment.aggregate_id,
            domain_experiment_id=domain.aggregate_id,
            workflow_id=None,
            workflow_revision_id=None,
            return_uri=return_uri,
        )
        await session.commit()

        assert (await resolve_launch_context(session, context.launch_context_id)).return_uri == return_uri
        claimed, token = await claim_launch_context(session, context.launch_context_id)
        await session.commit()
        consumed, binding = await consume_launch_context(
            session,
            launch_context_id=claimed.launch_context_id,
            claim_token=token,
            canonical_job_id="job-bound",
            canonical_batch_id=None,
        )
        await session.commit()

        assert consumed.state == "consumed"
        assert binding["verified"] is True
        assert binding["return_uri"] == return_uri
        assert (
            await resolve_launch_context_for_display(session, context.launch_context_id)
        ).canonical_job_id == "job-bound"
        display_context = await resolve_launch_context_for_display(session, context.launch_context_id)
        projected = await _project_bound_job(
            session,
            display_context,
            Job(id="job-bound", name="Bound", status="queued", model_id="protenix", mode="predict", params={}),
            binding,
        )
        assert await _project_bound_job(
            session,
            display_context,
            Job(id="job-bound", name="Bound", status="queued", model_id="protenix", mode="predict", params={}),
            binding,
        ) == projected
        assert await session.get(ExperimentWorkflowRun, projected["workflow_run_id"]) is not None
        with pytest.raises(LaunchContextError) as caught:
            await resolve_launch_context(session, context.launch_context_id)
        assert caught.value.code == "launch_context_consumed"


@pytest.mark.asyncio
async def test_workflow_and_dataset_reject_project_or_global_ownership(project_store):
    db_path, factory = project_store
    async with factory() as session:
        project = await create_project(session, _project_payload("Strict ownership"))
        global_experiment = await create_global_experiment(
            session,
            project.aggregate_id,
            _experiment_payload("Strict global"),
        )
        await session.commit()

        for parent_id in (None, project.aggregate_id, global_experiment.aggregate_id):
            with pytest.raises(ValidationFailure, match="Domain Experiment"):
                await create_workflow(
                    session,
                    project.aggregate_id,
                    "Invalid workflow",
                    "rfd3",
                    experiment_id=parent_id,
                )
            with pytest.raises(ValidationFailure, match="Domain Experiment"):
                await create_dataset(
                    session,
                    project.aggregate_id,
                    "Invalid dataset",
                    "structures",
                    experiment_id=parent_id,
                )

    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            """
            INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at)
            VALUES ('invalid-sql-workflow', 'workflow', ?, ?, ?)
            """,
            (
                project.aggregate_id,
                project.aggregate_id,
                "2026-08-09T00:00:00Z",
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="aggregate parent"):
            connection.execute(
                """
                INSERT INTO aggregate_heads(
                    aggregate_id, aggregate_kind, workspace_id, parent_id,
                    lifecycle_state, display_name, description, created_at, updated_at
                ) VALUES (?, 'workflow', ?, ?, 'draft', 'Invalid SQL workflow', '', ?, ?)
                """,
                (
                    "invalid-sql-workflow",
                    project.aggregate_id,
                    project.aggregate_id,
                    "2026-08-09T00:00:00Z",
                    "2026-08-09T00:00:00Z",
                ),
            )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_hierarchy_references_require_verified_project_local_sources_and_bind_digests(
    project_store,
):
    _db_path, factory = project_store
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        project = (await client.post("/api/projects", json=_project_payload())).json()
        other_project = (await client.post("/api/projects", json=_project_payload("Reference foreign"))).json()
        source_experiment = (
            await client.post(
                f"/api/projects/{project['id']}/experiments",
                json=_experiment_payload("Dataset source"),
            )
        ).json()
        source_domain = (
            await client.post(
                f"/api/projects/{project['id']}/experiments/{source_experiment['id']}/domains",
                json=_domain_payload(),
            )
        ).json()
        foreign_source_experiment = (
            await client.post(
                f"/api/projects/{other_project['id']}/experiments",
                json=_experiment_payload("Foreign dataset source"),
            )
        ).json()
        foreign_source_domain = (
            await client.post(
                f"/api/projects/{other_project['id']}/experiments/{foreign_source_experiment['id']}/domains",
                json=_domain_payload(),
            )
        ).json()

        def acknowledgement(
            entity_id: str,
            digest: str,
            *,
            availability: str = "available",
        ) -> dict:
            return {
                "schema": "bms.global.external-entity-receipt.v1",
                "store_id": "core",
                "entity_kind": "design",
                "entity_id": entity_id,
                "entity_revision_id": "1",
                "content_digest": digest,
                "contract_digest": digest,
                "source_build_revision": "test-build-sha",
                "verified_at": "2026-08-09T00:00:00Z",
                "verifier_id": "test.server-adapter.v1",
                "availability": availability,
                "reopen_uri": f"/designs/{entity_id}",
                "metadata": {},
            }

        async with factory() as session:
            valid_digest = "1" * 64
            valid_receipt = await register_external_entity_receipt(
                session,
                workspace_id=project["id"],
                store_id="core",
                entity_kind="design",
                entity_id="valid-design",
                generation_or_revision="1",
                content_digest=valid_digest,
                acknowledgement=acknowledgement("valid-design", valid_digest),
                verification_authority="test.server-adapter.v1",
            )
            foreign_receipt = await register_external_entity_receipt(
                session,
                workspace_id=other_project["id"],
                store_id="core",
                entity_kind="design",
                entity_id="foreign-design",
                generation_or_revision="1",
                content_digest="2" * 64,
                acknowledgement=acknowledgement("foreign-design", "2" * 64),
                verification_authority="test.server-adapter.v1",
            )
            unverified_receipt = await register_external_entity_receipt(
                session,
                workspace_id=project["id"],
                store_id="core",
                entity_kind="design",
                entity_id="caller-design",
                generation_or_revision="1",
                content_digest="3" * 64,
            )
            for receipt_id, entity_id, digest, stored_availability, acknowledged_digest in (
                ("receipt-unavailable", "unavailable-design", "4" * 64, "unavailable", "4" * 64),
                ("receipt-mismatch", "mismatch-design", "5" * 64, "available", "6" * 64),
            ):
                session.add(
                    ExperimentResource(
                        id=receipt_id,
                        kind="external_entity_receipt",
                        workspace_id=project["id"],
                        lifecycle_owner_id=project["id"],
                        created_at="2026-08-09T00:00:00Z",
                    )
                )
                await session.flush()
                session.add(
                    ExperimentExternalEntityReceipt(
                        id=receipt_id,
                        workspace_id=project["id"],
                        resource_id=receipt_id,
                        store_id="core",
                        entity_kind="design",
                        entity_id=entity_id,
                        generation_or_revision="1",
                        content_digest=digest,
                        availability=stored_availability,
                        verification_authority="test.server-adapter.v1",
                        acknowledgement_json=json.dumps(
                            acknowledgement(entity_id, acknowledged_digest, availability=stored_availability)
                        ),
                        created_at="2026-08-09T00:00:00Z",
                    )
                )
            cross_bound_digest = "7" * 64
            session.add(
                ExperimentResource(
                    id="receipt-cross-bound-resource",
                    kind="external_entity_receipt",
                    workspace_id=project["id"],
                    lifecycle_owner_id=project["id"],
                    created_at="2026-08-09T00:00:00Z",
                )
            )
            await session.flush()
            session.add(
                ExperimentExternalEntityReceipt(
                    id="receipt-cross-bound-resource",
                    workspace_id=project["id"],
                    resource_id=foreign_receipt.id,
                    store_id="core",
                    entity_kind="design",
                    entity_id="cross-bound-design",
                    generation_or_revision="1",
                    content_digest=cross_bound_digest,
                    availability="available",
                    verification_authority="test.server-adapter.v1",
                    acknowledgement_json=json.dumps(
                        acknowledgement("cross-bound-design", cross_bound_digest)
                    ),
                    created_at="2026-08-09T00:00:00Z",
                )
            )
            dataset = await create_dataset(
                session,
                project["id"],
                "Verified dataset",
                "reference",
                experiment_id=source_domain["id"],
            )
            dataset_revision = await save_dataset_revision(
                session,
                dataset.aggregate_id,
                {"schema": "bms.dataset.v1", "members": [{"identity": "member-1", "value": "A"}]},
                expected_head_generation=0,
            )
            foreign_dataset = await create_dataset(
                session,
                other_project["id"],
                "Foreign dataset",
                "reference",
                experiment_id=foreign_source_domain["id"],
            )
            await save_dataset_revision(
                session,
                foreign_dataset.aggregate_id,
                {"schema": "bms.dataset.v1", "members": []},
                expected_head_generation=0,
            )
            await session.commit()
            valid_receipt_id = valid_receipt.id
            invalid_receipt_ids = [
                "missing-receipt",
                foreign_receipt.id,
                unverified_receipt.id,
                "receipt-unavailable",
                "receipt-mismatch",
                "receipt-cross-bound-resource",
            ]
            dataset_id = dataset.aggregate_id
            dataset_digest = dataset_revision.payload_sha256
            foreign_dataset_id = foreign_dataset.aggregate_id

        for receipt_id in invalid_receipt_ids:
            response = await client.post(
                f"/api/projects/{project['id']}/experiments",
                json={**_experiment_payload(f"Invalid receipt {receipt_id}"), "shared_source_receipt_ids": [receipt_id]},
            )
            assert response.status_code == 422, response.text
        for invalid_dataset_id in ("missing-dataset", foreign_dataset_id):
            response = await client.post(
                f"/api/projects/{project['id']}/experiments",
                json={**_experiment_payload(f"Invalid dataset {invalid_dataset_id}"), "shared_dataset_ids": [invalid_dataset_id]},
            )
            assert response.status_code == 422, response.text

        global_response = await client.post(
            f"/api/projects/{project['id']}/experiments",
            json={
                **_experiment_payload("Bound hierarchy references"),
                "shared_source_receipt_ids": [valid_receipt_id],
                "shared_dataset_ids": [dataset_id],
            },
        )
        assert global_response.status_code == 201, global_response.text
        global_experiment = global_response.json()

        missing_domain_receipt = await client.post(
            f"/api/projects/{project['id']}/experiments/{global_experiment['id']}/domains",
            json={**_domain_payload(name="Missing top-level receipt"), "source_receipt_ids": ["missing-receipt"]},
        )
        missing_target_receipt_payload = _domain_payload(name="Missing target receipt")
        missing_target_receipt_payload["domain_payload"]["targets"][0]["entity_receipt_ids"] = [
            "missing-receipt"
        ]
        missing_target_receipt = await client.post(
            f"/api/projects/{project['id']}/experiments/{global_experiment['id']}/domains",
            json=missing_target_receipt_payload,
        )
        missing_domain_dataset = await client.post(
            f"/api/projects/{project['id']}/experiments/{global_experiment['id']}/domains",
            json={**_domain_payload(name="Missing dataset"), "dataset_ids": ["missing-dataset"]},
        )
        assert missing_domain_receipt.status_code == 422
        assert missing_target_receipt.status_code == 422
        assert missing_domain_dataset.status_code == 422

        valid_domain_payload = _domain_payload(name="Bound domain references")
        valid_domain_payload["source_receipt_ids"] = [valid_receipt_id]
        valid_domain_payload["dataset_ids"] = [dataset_id]
        valid_domain_payload["domain_payload"]["targets"][0]["entity_receipt_ids"] = [valid_receipt_id]
        domain_response = await client.post(
            f"/api/projects/{project['id']}/experiments/{global_experiment['id']}/domains",
            json=valid_domain_payload,
        )
        assert domain_response.status_code == 201, domain_response.text
        domain = domain_response.json()

    async with factory() as session:
        global_edges = (
            await session.execute(
                select(ExperimentRevisionEdge).where(
                    ExperimentRevisionEdge.revision_id == global_experiment["current_revision_id"]
                )
            )
        ).scalars().all()
        domain_edges = (
            await session.execute(
                select(ExperimentRevisionEdge).where(
                    ExperimentRevisionEdge.revision_id == domain["current_revision_id"]
                )
            )
        ).scalars().all()
        assert {(edge.target_resource_id, edge.expected_sha256) for edge in global_edges} == {
            (valid_receipt_id, valid_digest),
            (dataset_id, dataset_digest),
        }
        assert {(edge.target_resource_id, edge.expected_sha256) for edge in domain_edges} == {
            (valid_receipt_id, valid_digest),
            (dataset_id, dataset_digest),
        }


@pytest.mark.asyncio
async def test_completed_experiment_rejects_transition_back_to_draft(project_store):
    _db_path, factory = project_store
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        project = (await client.post("/api/projects", json=_project_payload())).json()
        experiment = (await client.post(
            f"/api/projects/{project['id']}/experiments", json=_experiment_payload()
        )).json()
        for requested_status, extra_fields in (
            ("planned", {}),
            ("active", {}),
            ("analysis", {}),
            ("review", {}),
            ("completed", {"review_summary": "Reviewed", "conclusion": "Concluded"}),
        ):
            response = await client.patch(
                f"/api/projects/{project['id']}/experiments/{experiment['id']}",
                json={
                    "expected_head_generation": experiment["head_generation"],
                    "status": requested_status,
                    **extra_fields,
                },
            )
            assert response.status_code == 200, response.text
            experiment = response.json()

        revision_count = len((await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/revisions"
        )).json()["items"])
        invalid = await client.patch(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}",
            json={
                "expected_head_generation": experiment["head_generation"],
                "status": "draft",
            },
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["detail"]["code"] == "invalid_transition"
        unchanged = (await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}"
        )).json()
        assert unchanged["lifecycle_state"] == "completed"
        assert unchanged["head_generation"] == experiment["head_generation"]
        assert len((await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/revisions"
        )).json()["items"]) == revision_count

    async with factory() as session:
        transitions = (
            await session.execute(
                select(ExperimentAuditEvent)
                .where(
                    ExperimentAuditEvent.resource_id == experiment["id"],
                    ExperimentAuditEvent.event_type == "aggregate_lifecycle_transitioned",
                )
                .order_by(ExperimentAuditEvent.generation)
            )
        ).scalars().all()
        assert [json.loads(event.payload_json) for event in transitions] == [
            {"from": "draft", "to": "planned"},
            {"from": "planned", "to": "active"},
            {"from": "active", "to": "analysis"},
            {"from": "analysis", "to": "review"},
            {"from": "review", "to": "completed"},
        ]


def test_aggregate_head_rejects_lifecycle_mismatch_with_current_revision(tmp_path: Path):
    db_path = tmp_path / "head-revision-consistency.db"
    run_all(db_path)
    connection = sqlite3.connect(db_path)
    register_sqlite_sha256(connection)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        created_at = "2026-08-09T00:00:00Z"
        payload = json.dumps(
            {
                **_project_payload("Consistent project"),
                "needs_metadata_review": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES ('project-consistent', 'workspace', NULL, NULL, ?)",
            (created_at,),
        )
        connection.execute(
            "INSERT INTO resources(id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES ('revision-consistent', 'revision', 'project-consistent', 'project-consistent', ?)",
            (created_at,),
        )
        connection.execute(
            """
            INSERT INTO revisions(
                resource_id, subject_id, revision_number, parent_revision_id, schema_name,
                schema_version, canonical_payload, payload_sha256, dependency_graph_sha256,
                provenance_json, created_at
            ) VALUES (
                'revision-consistent', 'project-consistent', 1, NULL, 'bms.project.v1',
                '1', ?, ?, ?, '{}', ?
            )
            """,
            (payload, _sha(payload), _sha('{"edges":[],"nodes":[]}'), created_at),
        )
        connection.execute(
            """
            INSERT INTO aggregate_heads(
                aggregate_id, aggregate_kind, workspace_id, parent_id, current_revision_id,
                head_generation, lifecycle_state, display_name, description, created_at, updated_at
            ) VALUES (
                'project-consistent', 'workspace', 'project-consistent', NULL, 'revision-consistent',
                1, 'draft', 'Consistent project', '', ?, ?
            )
            """,
            (created_at, created_at),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="aggregate head lifecycle must match current revision status"):
            connection.execute(
                "UPDATE aggregate_heads SET lifecycle_state = 'active' "
                "WHERE aggregate_id = 'project-consistent'"
            )
        connection.rollback()
        assert connection.execute(
            "SELECT lifecycle_state FROM aggregate_heads WHERE aggregate_id = 'project-consistent'"
        ).fetchone() == ("draft",)
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_archive_restore_is_reversible_and_does_not_archive_children(project_store):
    _db_path, factory = project_store
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        project = (await client.post("/api/projects", json=_project_payload())).json()
        experiment = (await client.post(
            f"/api/projects/{project['id']}/experiments", json=_experiment_payload()
        )).json()
        domain = (await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains",
            json=_domain_payload(),
        )).json()

        archived = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/archive",
            json={"expected_head_generation": experiment["head_generation"]},
        )
        assert archived.status_code == 200, archived.text
        archived_payload = archived.json()
        assert archived_payload["lifecycle_state"] == "archived"
        assert archived_payload["head_generation"] == experiment["head_generation"] + 1
        archived_revisions = (await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/revisions"
        )).json()["items"]
        assert archived_revisions[0]["payload"]["status"] == "archived"
        assert [row["revision_number"] for row in archived_revisions] == [2, 1]
        stale_archive = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/archive",
            json={"expected_head_generation": experiment["head_generation"]},
        )
        assert stale_archive.status_code == 409
        blocked_child = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains",
            json=_domain_payload(name="Blocked under archive"),
        )
        assert blocked_child.status_code == 422
        child_after_parent_archive = await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains/{domain['id']}"
        )
        assert child_after_parent_archive.status_code == 200
        assert child_after_parent_archive.json()["lifecycle_state"] != "archived"

        restored = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/restore",
            json={"expected_head_generation": archived_payload["head_generation"]},
        )
        assert restored.status_code == 200, restored.text
        restored_payload = restored.json()
        assert restored_payload["lifecycle_state"] != "archived"
        assert restored_payload["head_generation"] == archived_payload["head_generation"] + 1
        restored_revisions = (await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/revisions"
        )).json()["items"]
        assert restored_revisions[0]["payload"]["status"] == "draft"
        assert [row["revision_number"] for row in restored_revisions] == [3, 2, 1]
        stale_restore = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/restore",
            json={"expected_head_generation": archived_payload["head_generation"]},
        )
        assert stale_restore.status_code == 409

        archived_domain = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains/{domain['id']}/archive",
            json={"expected_head_generation": domain["head_generation"]},
        )
        restored_domain = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains/{domain['id']}/restore",
            json={"expected_head_generation": archived_domain.json()["head_generation"]},
        )
        assert archived_domain.status_code == 200
        assert restored_domain.status_code == 200

        assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 405

    async with factory() as session:
        events = (await session.execute(select(ExperimentAuditEvent))).scalars().all()
        event_types = {event.event_type for event in events}
        assert "aggregate_created" in event_types
        assert "aggregate_archived" in event_types
        assert "aggregate_restored" in event_types


@pytest.mark.asyncio
async def test_eln_lite_records_are_append_only_at_all_scopes_and_exported(project_store, monkeypatch):
    db_path, factory = project_store
    monkeypatch.setenv("BMS_EXPERIMENT_DB_PATH", str(db_path))
    monkeypatch.setenv("BMS_EXPERIMENT_BACKUP_ROOT", str(db_path.parent / "backups"))
    monkeypatch.setenv("BMS_EXPERIMENT_EXPORT_ROOT", str(db_path.parent / "exports"))
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(db_path.parent / "artifacts"))
    monkeypatch.setenv("BMS_BUILD_SHA", "test-build-sha")
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        project = (await client.post("/api/projects", json=_project_payload())).json()
        experiment = (await client.post(
            f"/api/projects/{project['id']}/experiments", json=_experiment_payload()
        )).json()
        domain = (await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains",
            json=_domain_payload(),
        )).json()

        project_record = await client.post(
            f"/api/projects/{project['id']}/records",
            json={"record_kind": "note", "body": "Project note", "author": "operator"},
        )
        global_record = await client.post(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/records",
            json={"record_kind": "observation", "body": "Global observation"},
        )
        domain_records = []
        for kind in ("note", "observation", "decision", "conclusion"):
            response = await client.post(
                f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains/{domain['id']}/records",
                json={"record_kind": kind, "body": f"{kind} body"},
            )
            assert response.status_code == 201, response.text
            domain_records.append(response.json())
        assert project_record.status_code == global_record.status_code == 201
        assert (await client.get(f"/api/projects/{project['id']}/records")).json()["items"][0]["subject_resource_id"] == project["id"]
        assert (await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/records"
        )).json()["items"][0]["subject_resource_id"] == experiment["id"]
        listed_domain = (await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains/{domain['id']}/records"
        )).json()
        assert {record["record_kind"] for record in listed_domain["items"]} == {
            "note", "observation", "decision", "conclusion"
        }
        first_record_page = (await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains/{domain['id']}/records?limit=2"
        )).json()
        assert len(first_record_page["items"]) == 2
        assert first_record_page["next_cursor"] is not None
        second_record_page = (await client.get(
            f"/api/projects/{project['id']}/experiments/{experiment['id']}/domains/{domain['id']}/records",
            params={"limit": 2, "cursor": first_record_page["next_cursor"]},
        )).json()
        assert len(second_record_page["items"]) == 2
        assert {row["id"] for row in first_record_page["items"]}.isdisjoint(
            {row["id"] for row in second_record_page["items"]}
        )

        other_project = (await client.post("/api/projects", json=_project_payload("ELN foreign"))).json()
        receipt_payload = {
            "store_id": "core",
            "entity_kind": "job",
            "generation_or_revision": "1",
            "content_digest": "a" * 64,
        }
        available_receipt = (await client.post(
            f"/api/experiment-workspaces/{project['id']}/external-receipts",
            json={**receipt_payload, "entity_id": "available-job", "availability": "available"},
        )).json()
        unavailable_receipt = (await client.post(
            f"/api/experiment-workspaces/{project['id']}/external-receipts",
            json={**receipt_payload, "entity_id": "unavailable-job", "availability": "unavailable"},
        )).json()
        foreign_receipt = (await client.post(
            f"/api/experiment-workspaces/{other_project['id']}/external-receipts",
            json={**receipt_payload, "entity_id": "foreign-job", "availability": "available"},
        )).json()
        assert available_receipt["availability"] == "unavailable"
        assert available_receipt["store_id"] == "unverified:core"
        async with factory() as verifier_session:
            server_acknowledgement = {
                "schema": "bms.global.external-entity-receipt.v1",
                "store_id": "core",
                "entity_kind": "job",
                "entity_id": "verified-job",
                "entity_revision_id": "1",
                "content_digest": "b" * 64,
                "contract_digest": "b" * 64,
                "source_build_revision": "test-build-sha",
                "verified_at": "2026-08-09T00:00:00Z",
                "verifier_id": "test.server-adapter.v1",
                "availability": "available",
                "reopen_uri": "/jobs/verified-job",
                "metadata": {},
            }
            server_receipt = await register_external_entity_receipt(
                verifier_session,
                workspace_id=project["id"],
                store_id="core",
                entity_kind="job",
                entity_id="verified-job",
                generation_or_revision="1",
                content_digest="b" * 64,
                acknowledgement=server_acknowledgement,
                verification_authority="test.server-adapter.v1",
            )
            await verifier_session.commit()
            server_receipt_id = server_receipt.id
            forged_receipt_id = "external-receipt-legacy-forged"
            forged_acknowledgement = {
                **server_acknowledgement,
                "entity_id": "legacy-forged-job",
                "content_digest": "c" * 64,
                "contract_digest": "c" * 64,
            }
            verifier_session.add(
                ExperimentResource(
                    id=forged_receipt_id,
                    kind="external_entity_receipt",
                    workspace_id=project["id"],
                    lifecycle_owner_id=project["id"],
                    created_at="2026-08-09T00:00:00Z",
                )
            )
            await verifier_session.flush()
            verifier_session.add(
                ExperimentExternalEntityReceipt(
                    id=forged_receipt_id,
                    workspace_id=project["id"],
                    resource_id=forged_receipt_id,
                    store_id="core",
                    entity_kind="job",
                    entity_id="legacy-forged-job",
                    generation_or_revision="1",
                    content_digest="c" * 64,
                    availability="available",
                    acknowledgement_json=json.dumps(forged_acknowledgement),
                    created_at="2026-08-09T00:00:00Z",
                )
            )
            await verifier_session.commit()
        unknown_reference = await client.post(
            f"/api/projects/{project['id']}/records",
            json={"record_kind": "note", "body": "unknown", "source_receipt_ids": ["missing"]},
        )
        unavailable_reference = await client.post(
            f"/api/projects/{project['id']}/records",
            json={"record_kind": "note", "body": "unavailable", "source_receipt_ids": [unavailable_receipt["id"]]},
        )
        self_asserted_reference = await client.post(
            f"/api/projects/{project['id']}/records",
            json={"record_kind": "note", "body": "self asserted", "source_receipt_ids": [available_receipt["id"]]},
        )
        historical_forgery_reference = await client.post(
            f"/api/projects/{project['id']}/records",
            json={"record_kind": "note", "body": "historical forgery", "source_receipt_ids": [forged_receipt_id]},
        )
        foreign_reference = await client.post(
            f"/api/projects/{project['id']}/records",
            json={"record_kind": "note", "body": "foreign", "source_receipt_ids": [foreign_receipt["id"]]},
        )
        verified_reference = await client.post(
            f"/api/projects/{project['id']}/records",
            json={"record_kind": "note", "body": "verified", "source_receipt_ids": [server_receipt_id]},
        )
        assert (
            unknown_reference.status_code
            == unavailable_reference.status_code
            == self_asserted_reference.status_code
            == historical_forgery_reference.status_code
            == foreign_reference.status_code
            == 422
        )
        assert verified_reference.status_code == 201

        replacement = await client.post(
            f"/api/projects/{project['id']}/records",
            json={
                "record_kind": "note",
                "body": "Replacement note",
                "supersedes_record_id": project_record.json()["id"],
            },
        )
        assert replacement.status_code == 201, replacement.text
        assert replacement.json()["supersedes_record_id"] == project_record.json()["id"]

        invalid = await client.post(
            f"/api/projects/{project['id']}/records",
            json={"record_kind": "invalid", "body": "bad"},
        )
        assert invalid.status_code == 422

    async with factory() as session:
        rows = (await session.execute(select(ExperimentResearchRecord))).scalars().all()
        assert len(rows) == 8
        assert all(row.resource_id == row.resource_id for row in rows)
        events = (await session.execute(select(ExperimentAuditEvent))).scalars().all()
        assert any(event.event_type == "research_record_appended" for event in events)

        exported = await build_workspace_export(session, project["id"])
    manifest = json.loads((db_path.parent / "exports" / exported["export_id"] / "manifest.json").read_text())
    assert len(manifest["tables"]["research_records"]) == 8
    assert "domain_adapter_receipts" in manifest["tables"]
    assert verify_workspace_export(exported["export_id"])["verified"] is True
    backup = create_online_backup()
    assert verify_backup(backup["backup_id"])["verified"] is True


@pytest.mark.asyncio
async def test_compatibility_workspace_and_experiment_routes_share_project_authority(project_store):
    _db_path, factory = project_store
    app = _app(factory)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        legacy_project_response = await client.post(
            "/api/experiment-workspaces", json={"name": "Legacy project", "description": "legacy"}
        )
        assert legacy_project_response.status_code == 201, legacy_project_response.text
        legacy_project = legacy_project_response.json()
        assert legacy_project["deprecation"]["replacement"] == "/api/projects"
        projected = await client.get(f"/api/projects/{legacy_project['id']}")
        assert projected.status_code == 200
        assert projected.json()["id"] == legacy_project["id"]
        assert projected.json()["kind"] == "project"
        assert projected.json()["current_revision_id"] is not None
        project_revisions = (await client.get(
            f"/api/projects/{legacy_project['id']}/revisions"
        )).json()["items"]
        assert project_revisions[0]["payload"]["needs_metadata_review"] is False

        legacy_experiment_response = await client.post(
            f"/api/experiment-workspaces/{legacy_project['id']}/experiments",
            json={"name": "Legacy experiment", "question": "legacy question"},
        )
        assert legacy_experiment_response.status_code == 201, legacy_experiment_response.text
        legacy_experiment = legacy_experiment_response.json()
        projected_experiment = await client.get(
            f"/api/projects/{legacy_project['id']}/experiments/{legacy_experiment['id']}"
        )
        assert projected_experiment.status_code == 200
        assert projected_experiment.json()["id"] == legacy_experiment["id"]
        assert projected_experiment.json()["kind"] == "global_experiment"
        assert projected_experiment.json()["current_revision_id"] is not None
        experiment_revisions = (await client.get(
            f"/api/projects/{legacy_project['id']}/experiments/{legacy_experiment['id']}/revisions"
        )).json()["items"]
        assert experiment_revisions[0]["payload"]["needs_metadata_review"] is False
