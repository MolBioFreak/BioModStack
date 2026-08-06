from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from experiment_database import ExperimentBase, create_experiment_engine, create_experiment_session_factory
from experiment_migrations import run_all as run_experiment_migrations
from migrations.sqlite_sha256 import register_sqlite_sha256
from experiment_models import (
    ExperimentAuditEvent,
    ExperimentDispatchOutbox,
    ExperimentLineageEdge,
    ExperimentRevision,
    ExperimentValidation,
    ExperimentWorkflowPreparation,
    ExperimentWorkflowRun,
)
from experiment_services import (
    DispatchFailure,
    ExistingJobMaterializer,
    IdempotencyConflict,
    RevisionConflict,
    create_dataset,
    create_experiment_workspace,
    create_workflow,
    create_run_group,
    clone_workflow,
    prepare_workflow,
    save_dataset_revision,
    save_workflow_draft,
    save_workflow_revision,
)
from routers.experiment_workspaces import router


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@pytest_asyncio.fixture
async def experiment_store(tmp_path: Path):
    db_path = tmp_path / "experiments.db"
    run_experiment_migrations(db_path)
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_experiment_session_factory(engine)
    try:
        yield db_path, engine, factory
    finally:
        await engine.dispose()


def _workflow_payload() -> dict:
    return {
        "schema": "bms.workflow.generic.v1",
        "workflow_family": "generic_test",
        "contract_version": "1",
        "adapter_id": "generic.test.adapter.v1",
        "nodes": [{"id": "main", "kind": "scheduler_job", "required": True}],
        "edges": [],
        "parameters": {"seed": 101},
        "scheduler": {
            "name": "generic-test",
            "model_id": "generic_test",
            "mode": "predict",
            "params": {"seed": 101},
        },
    }


@pytest.mark.asyncio
async def test_experiment_migration_enforces_pragmas_digest_and_revision_immutability(
    experiment_store,
):
    db_path, _engine, _factory = experiment_store
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        register_sqlite_sha256(connection)
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        workspace_id = "workspace-1"
        connection.execute(
            "INSERT INTO resources (id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES (?, 'workspace', NULL, NULL, '2026-01-01T00:00:00Z')",
            (workspace_id,),
        )
        revision_id = "revision-1"
        payload = '{"a":1}'
        connection.execute(
            "INSERT INTO resources (id, kind, workspace_id, lifecycle_owner_id, created_at) "
            "VALUES (?, 'revision', ?, ?, '2026-01-01T00:00:00Z')",
            (revision_id, workspace_id, workspace_id),
        )
        connection.execute(
            "INSERT INTO revisions (resource_id, subject_id, revision_number, schema_name, schema_version, "
            "canonical_payload, payload_sha256, dependency_graph_sha256, created_at) "
            "VALUES (?, ?, 1, 'test', '1', ?, ?, ?, '2026-01-01T00:00:00Z')",
            (revision_id, workspace_id, payload, _sha(payload), _sha("[]")),
        )
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="revision payload digest"):
            connection.execute(
                "INSERT INTO revisions (resource_id, subject_id, revision_number, schema_name, schema_version, "
                "canonical_payload, payload_sha256, dependency_graph_sha256, created_at) "
                "VALUES ('revision-2', ?, 2, 'test', '1', ?, ?, ?, '2026-01-01T00:00:00Z')",
                (workspace_id, payload, "0" * 64, _sha("[]")),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable revision"):
            connection.execute(
                "UPDATE revisions SET canonical_payload = ? WHERE resource_id = ?",
                ('{"a":2}', revision_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable revision"):
            connection.execute("DELETE FROM revisions WHERE resource_id = ?", (revision_id,))
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_workflow_and_dataset_revision_save_load_prepare_is_server_backed(experiment_store):
    _db_path, _engine, factory = experiment_store
    async with factory() as session:
        workspace = await create_experiment_workspace(session, "CM test workspace")
        workflow = await create_workflow(session, workspace.id, "Generic workflow", "generic_test")
        dataset = await create_dataset(session, workspace.id, "Input dataset", "generic_inputs")
        await save_workflow_draft(session, workflow.id, _workflow_payload(), expected_generation=0)
        workflow_revision = await save_workflow_revision(
            session, workflow.id, expected_head_generation=0
        )
        dataset_revision = await save_dataset_revision(
            session,
            dataset.id,
            {"members": [{"role": "sequence", "value": "ACDE"}]},
            expected_head_generation=0,
        )
        preparation = await prepare_workflow(
            session,
            workflow_revision.id,
            {"input_dataset_revision_ids": [dataset_revision.id]},
        )
        await session.commit()

        assert workflow_revision.payload_sha256 == _sha(workflow_revision.canonical_payload)
        assert dataset_revision.payload_sha256 == _sha(dataset_revision.canonical_payload)
        assert preparation.validation_status == "valid"
        assert preparation.scheduler_payload_json

        persisted_revision = await session.get(ExperimentRevision, workflow_revision.id)
        persisted_preparation = await session.get(ExperimentWorkflowPreparation, preparation.id)
        assert persisted_revision is not None
        assert persisted_preparation is not None
        assert persisted_preparation.validation_resource_id
        assert (await session.execute(select(ExperimentValidation))).scalars().all()
        assert (await session.execute(select(ExperimentLineageEdge))).scalars().all()
        assert (await session.execute(select(ExperimentAuditEvent))).scalars().all()
        assert json.loads(persisted_revision.canonical_payload)["workflow_family"] == "generic_test"

        clone = await clone_workflow(session, workflow.id, source_revision_id=workflow_revision.id)
        await session.commit()
        assert clone.id != workflow.id
        fork_edges = (
            await session.execute(
                select(ExperimentLineageEdge).where(
                    ExperimentLineageEdge.source_resource_id == clone.id,
                    ExperimentLineageEdge.edge_mode == "forked_from",
                )
            )
        ).scalars().all()
        assert len(fork_edges) == 1


@pytest.mark.asyncio
async def test_revision_generation_conflict_and_run_group_idempotency(experiment_store):
    _db_path, _engine, factory = experiment_store
    async with factory() as session:
        workspace = await create_experiment_workspace(session, "run-group workspace")
        workflow = await create_workflow(session, workspace.id, "workflow", "generic_test")
        dataset = await create_dataset(session, workspace.id, "dataset", "generic_inputs")
        await save_workflow_draft(session, workflow.id, _workflow_payload(), expected_generation=0)
        revision = await save_workflow_revision(session, workflow.id, expected_head_generation=0)
        dataset_revision = await save_dataset_revision(
            session, dataset.id, {"members": []}, expected_head_generation=0
        )
        preparation = await prepare_workflow(
            session, revision.id, {"input_dataset_revision_ids": [dataset_revision.id]}
        )
        await session.commit()

        with pytest.raises(RevisionConflict):
            await save_workflow_revision(session, workflow.id, expected_head_generation=0)

        group = await create_run_group(
            session,
            workspace.id,
            [preparation.id],
            idempotency_key="launch-1",
        )
        await session.commit()
        replay = await create_run_group(
            session,
            workspace.id,
            [preparation.id],
            idempotency_key="launch-1",
        )
        assert replay.id == group.id
        with pytest.raises(IdempotencyConflict):
            await create_run_group(
                session,
                workspace.id,
                [],
                idempotency_key="launch-1",
            )

        outbox_rows = (
            await session.execute(select(ExperimentDispatchOutbox))
        ).scalars().all()
        runs = (await session.execute(select(ExperimentWorkflowRun))).scalars().all()
        assert len(outbox_rows) == 1
        assert len(runs) == 1
        assert outbox_rows[0].status == "pending"


class _FakeMaterializer:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def materialize(self, attempt_id: str, payload: dict) -> dict:
        self.calls.append((attempt_id, payload))
        return {
            "store_id": "fake-core",
            "entity_kind": "job",
            "entity_id": attempt_id,
            "generation": 1,
            "content_digest": _sha(json.dumps(payload, sort_keys=True)),
        }


@pytest.mark.asyncio
async def test_outbox_dispatch_is_idempotent_and_acknowledges_external_receipt(experiment_store):
    _db_path, _engine, factory = experiment_store
    from experiment_services import dispatch_pending_outbox

    async with factory() as session:
        workspace = await create_experiment_workspace(session, "dispatch workspace")
        workflow = await create_workflow(session, workspace.id, "workflow", "generic_test")
        dataset = await create_dataset(session, workspace.id, "dataset", "generic_inputs")
        await save_workflow_draft(session, workflow.id, _workflow_payload(), expected_generation=0)
        revision = await save_workflow_revision(session, workflow.id, expected_head_generation=0)
        dataset_revision = await save_dataset_revision(
            session, dataset.id, {"members": []}, expected_head_generation=0
        )
        preparation = await prepare_workflow(
            session, revision.id, {"input_dataset_revision_ids": [dataset_revision.id]}
        )
        await create_run_group(session, workspace.id, [preparation.id], idempotency_key="dispatch-1")
        await session.commit()

        materializer = _FakeMaterializer()
        first = await dispatch_pending_outbox(session, materializer)
        second = await dispatch_pending_outbox(session, materializer)

        assert first == 1
        assert second == 0
        assert len(materializer.calls) == 1
        outbox = (await session.execute(select(ExperimentDispatchOutbox))).scalar_one()
        assert outbox.status == "acknowledged"
        run = (await session.execute(select(ExperimentWorkflowRun))).scalar_one()
        assert run.state == "dispatched"

        await create_run_group(session, workspace.id, [preparation.id], idempotency_key="dispatch-2")
        await session.commit()
        stale = (
            await session.execute(
                select(ExperimentDispatchOutbox).where(ExperimentDispatchOutbox.status == "pending")
            )
        ).scalar_one()
        stale.status = "dispatching"
        stale.updated_at = "2000-01-01T00:00:00+00:00"
        await session.commit()
        recovered_materializer = _FakeMaterializer()
        assert await dispatch_pending_outbox(session, recovered_materializer) == 1
        assert len(recovered_materializer.calls) == 1


@pytest.mark.asyncio
async def test_workspace_router_exposes_server_backed_creation(experiment_store):
    _db_path, _engine, factory = experiment_store
    from experiment_database import get_experiment_session

    app = FastAPI()
    app.include_router(router)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_experiment_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/experiment-workspaces",
            json={"name": "HTTP workspace", "description": "saved on server"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "HTTP workspace"
        rejected = await client.post(
            "/api/experiment-workspaces",
            json={"name": "invalid", "unexpected": True},
        )
        assert rejected.status_code == 422
        loaded = await client.get(f"/api/experiment-workspaces/{body['id']}")
        assert loaded.status_code == 200
        assert loaded.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_existing_job_materializer_preallocates_and_reuses_core_job_identity(tmp_path: Path):
    from database import Job

    core_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    try:
        async with core_engine.begin() as connection:
            await connection.run_sync(Job.__table__.create)
        core_factory = async_sessionmaker(core_engine, expire_on_commit=False, class_=AsyncSession)
        payload = {
            "run_group_id": "run-group-id",
            "scheduler_job_id": "12345678-1234-1234-1234-123456789012",
            "scheduler": {
                "name": "materialized-job",
                "model_id": "generic_test",
                "mode": "predict",
                "params": {"seed": 101},
            },
        }
        async with core_factory() as session:
            materializer = ExistingJobMaterializer(session)
            first = await materializer.materialize("global-attempt-id", payload)
            second = await materializer.materialize("global-attempt-id", payload)
            assert first == second
            assert first["entity_id"] == payload["scheduler_job_id"]
            assert (await session.execute(select(Job))).scalars().all().__len__() == 1
            conflict = {**payload, "scheduler": {**payload["scheduler"], "params": {"seed": 202}}}
            with pytest.raises(DispatchFailure, match="conflicts"):
                await materializer.materialize("global-attempt-id", conflict)
    finally:
        await core_engine.dispose()
