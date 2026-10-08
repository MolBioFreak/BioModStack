from __future__ import annotations

import hashlib
import json
import os

import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy import select

from experiment_database import create_experiment_engine, create_experiment_session_factory
from experiment_migrations import run_all
from experiment_models import (
    ExperimentLogChunk,
    ExperimentLogStream,
    ExperimentResource,
    ExperimentRunAttempt,
)
from experiment_operations import (
    ExperimentOperationError,
    build_workspace_export,
    create_online_backup,
    register_external_entity_receipt,
    verify_backup,
    verify_workspace_export,
    workspace_analytics,
)
from experiment_services import (
    create_dataset,
    create_experiment_workspace,
    create_run_group,
    create_workflow,
    prepare_workflow,
    save_dataset_revision,
    save_workflow_draft,
    save_workflow_revision,
)


@pytest_asyncio.fixture
async def operation_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "experiments.db"
    monkeypatch.setenv("BMS_EXPERIMENT_DB_PATH", str(db_path))
    monkeypatch.setenv("BMS_EXPERIMENT_BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("BMS_EXPERIMENT_EXPORT_ROOT", str(tmp_path / "exports"))
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("BMS_BUILD_SHA", "test-build-sha")
    run_all(db_path)
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_experiment_session_factory(engine)
    try:
        yield db_path, factory
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
async def test_backup_export_and_global_analytics_are_hash_verified(operation_store):
    _db_path, factory = operation_store
    async with factory() as session:
        workspace = await create_experiment_workspace(session, "operations", "test")
        await session.commit()
        summary = await workspace_analytics(session, workspace.aggregate_id)
        exported = await build_workspace_export(session, workspace.aggregate_id)

    assert summary["bounded"] is True
    assert any(point["dimension"] == "resource_kind" for point in summary["points"])
    export_verification = verify_workspace_export(exported["export_id"])
    assert export_verification["verified"] is True
    assert export_verification["provenance_valid"] is True

    backup = create_online_backup()
    assert backup["schema_version"] == 3
    assert backup["source_revision"] == "test-build-sha"
    assert backup["object_counts"]
    backup_verification = verify_backup(backup["backup_id"])
    assert backup_verification["verified"] is True
    assert backup_verification["provenance_valid"] is True


@pytest.mark.asyncio
async def test_workspace_export_never_includes_foreign_claims_or_log_chunks(operation_store, monkeypatch):
    _db_path, factory = operation_store
    async with factory() as session:
        first = await create_experiment_workspace(session, "first", "")
        second = await create_experiment_workspace(session, "second", "")
        workflow = await create_workflow(session, second.id, "workflow", "generic_test")
        dataset = await create_dataset(session, second.id, "dataset", "generic_inputs")
        await save_workflow_draft(session, workflow.id, _workflow_payload(), expected_generation=0)
        revision = await save_workflow_revision(session, workflow.id, expected_head_generation=0)
        dataset_revision = await save_dataset_revision(
            session, dataset.id, {"members": []}, expected_head_generation=0
        )
        preparation = await prepare_workflow(
            session, revision.id, {"input_dataset_revision_ids": [dataset_revision.id]}
        )
        await create_run_group(session, second.id, [preparation.id], idempotency_key="foreign-launch")
        await session.flush()
        attempt = (
            await session.execute(
                select(ExperimentRunAttempt).where(ExperimentRunAttempt.workspace_id == second.id)
            )
        ).scalar_one()
        stream_id = "foreign-log-stream"
        session.add(
            ExperimentResource(
                id=stream_id,
                kind="log_stream",
                workspace_id=second.id,
                lifecycle_owner_id=attempt.resource_id,
            )
        )
        await session.flush()
        session.add(
            ExperimentLogStream(
                resource_id=stream_id,
                attempt_id=attempt.resource_id,
                stream_name="stdout",
                state="closed",
            )
        )
        await session.flush()
        foreign_text = "foreign project log"
        session.add(
            ExperimentLogChunk(
                stream_id=stream_id,
                sequence_number=0,
                content_sha256=hashlib.sha256(foreign_text.encode()).hexdigest(),
                content_text=foreign_text,
            )
        )
        await session.commit()
        first_export = await build_workspace_export(session, first.id)
        second_export = await build_workspace_export(session, second.id)

    export_root = Path(os.environ["BMS_EXPERIMENT_EXPORT_ROOT"])
    first_manifest = json.loads((export_root / first_export["export_id"] / "manifest.json").read_text())
    second_manifest = json.loads((export_root / second_export["export_id"] / "manifest.json").read_text())
    assert first_manifest["tables"]["idempotency_claims"] == []
    assert first_manifest["tables"]["log_chunks"] == []
    assert len(second_manifest["tables"]["idempotency_claims"]) == 1
    assert len(second_manifest["tables"]["log_chunks"]) == 1


@pytest.mark.asyncio
async def test_external_receipt_is_idempotent_and_rejects_uppercase_digest(operation_store):
    _db_path, factory = operation_store
    async with factory() as session:
        workspace = await create_experiment_workspace(session, "receipts", "test")
        await session.commit()
        with pytest.raises(ExperimentOperationError):
            await register_external_entity_receipt(
                session,
                workspace_id=workspace.aggregate_id,
                store_id="core",
                entity_kind="job",
                entity_id="job-1",
                generation_or_revision="1",
                content_digest="A" * 64,
            )
        receipt = await register_external_entity_receipt(
            session,
            workspace_id=workspace.aggregate_id,
            store_id="core",
            entity_kind="job",
            entity_id="job-1",
            generation_or_revision="1",
            content_digest="a" * 64,
        )
        await session.commit()
        replay = await register_external_entity_receipt(
            session,
            workspace_id=workspace.aggregate_id,
            store_id="core",
            entity_kind="job",
            entity_id="job-1",
            generation_or_revision="1",
            content_digest="a" * 64,
        )
        assert replay.id == receipt.id
