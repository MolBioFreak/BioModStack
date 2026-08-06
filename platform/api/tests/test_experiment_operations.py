from __future__ import annotations

import pytest
import pytest_asyncio
from pathlib import Path

from experiment_database import create_experiment_engine, create_experiment_session_factory
from experiment_migrations import run_all
from experiment_operations import (
    ExperimentOperationError,
    build_workspace_export,
    create_online_backup,
    register_external_entity_receipt,
    verify_backup,
    verify_workspace_export,
    workspace_analytics,
)
from experiment_services import create_experiment_workspace


@pytest_asyncio.fixture
async def operation_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "experiments.db"
    monkeypatch.setenv("BMS_EXPERIMENT_DB_PATH", str(db_path))
    monkeypatch.setenv("BMS_EXPERIMENT_BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("BMS_EXPERIMENT_EXPORT_ROOT", str(tmp_path / "exports"))
    monkeypatch.setenv("BMS_EXPERIMENT_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    run_all(db_path)
    engine = create_experiment_engine(f"sqlite+aiosqlite:///{db_path}")
    factory = create_experiment_session_factory(engine)
    try:
        yield db_path, factory
    finally:
        await engine.dispose()


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
    assert verify_workspace_export(exported["export_id"])["verified"] is True

    backup = create_online_backup()
    assert verify_backup(backup["backup_id"])["verified"] is True


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
