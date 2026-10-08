from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from experiment_database import create_experiment_engine, create_experiment_session_factory, get_experiment_session
from experiment_migrations import (
    LEGACY_MIGRATION_NAME,
    LEGACY_MIGRATION_VERSION,
    MIGRATION_NAME,
    MIGRATION_SQL,
    MIGRATION_V2_SQL,
    MIGRATION_VERSION,
    attest_schema,
    migration_checksum,
    run_all,
)
from experiment_models import (
    ExperimentAggregateHead,
    ExperimentAuditEvent,
    ExperimentExternalEntityReceipt,
    ExperimentResource,
    ExperimentResearchRecord,
)
from experiment_operations import (
    build_workspace_export,
    create_online_backup,
    register_external_entity_receipt,
    verify_backup,
    verify_workspace_export,
)
from routers.experiment_workspaces import router as compatibility_router
from routers.projects import router as projects_router


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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


def _app(factory) -> FastAPI:
    app = FastAPI()
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
        assert ledger == [(2, "global_experiment_workspace_receipts_and_projections"), (MIGRATION_VERSION, MIGRATION_NAME)]
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
            (2, "global_experiment_workspace_receipts_and_projections"),
            (MIGRATION_VERSION, MIGRATION_NAME),
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

        assert (await client.get("/api/projects")).json()[0]["id"] == project["id"]
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
        )).json()} == {first_domain["id"], second_domain["id"]}

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
