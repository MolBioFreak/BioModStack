from __future__ import annotations

import importlib
import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job


def _service():
    return importlib.import_module("services.nextflow")


def _job() -> Job:
    return Job(
        id="terminal-cas-job",
        name="terminal CAS",
        model_id="nanopore",
        mode="fastq_qc",
        status="running",
        queue_status="running",
        params={"execution_generation": 4, "owner_nonce": "owner-a"},
        provenance={"execution": {"unit": "unit-a", "invocation_id": "invocation-a"}},
        completed_stages=["fastq_align"],
        stage_outputs={"fastq_align": ["bms_results/run/align/aligned.bam"]},
        current_stage="fastq_qc",
        stage_progress=50,
        awaiting_input=False,
        paused=False,
        assigned_gpu=None,
        output_dir="bms_results/run",
        created_at=datetime(2026, 8, 20, 18, 0, 0),
        started_at=datetime(2026, 8, 20, 18, 1, 0),
    )


@pytest.mark.asyncio
async def test_terminal_publication_snapshot_covers_every_job_column(tmp_path) -> None:
    service = _service()
    capture = getattr(service, "capture_terminal_job_publication_snapshot", None)
    assert callable(capture)

    snapshot = capture(_job())

    assert set(snapshot) == {column.name for column in Job.__table__.columns}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("params", {"execution_generation": 5, "owner_nonce": "owner-b"}),
        ("provenance", {"execution": {"unit": "unit-b", "invocation_id": "invocation-b"}}),
        ("completed_stages", ["fastq_align", "fastq_qc"]),
        ("stage_outputs", {"fastq_align": ["bms_results/run/align/newer.bam"]}),
        ("current_stage", "construct_verification"),
        ("assigned_gpu", 1),
    ],
)
async def test_terminal_publication_rejects_every_newer_execution_or_lifecycle_field(
    tmp_path,
    field: str,
    value: Any,
) -> None:
    service = _service()
    capture = getattr(service, "capture_terminal_job_publication_snapshot", None)
    publish = getattr(service, "publish_terminal_job_changes", None)
    assert callable(capture)
    assert callable(publish)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'terminal-cas.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as seed:
            seed.add(_job())
            await seed.commit()
        async with factory() as stale:
            job = await stale.get(Job, "terminal-cas-job")
            assert job is not None
            snapshot = capture(job)
            await stale.rollback()

            async with factory() as concurrent:
                current = await concurrent.get(Job, "terminal-cas-job")
                assert current is not None
                setattr(current, field, value)
                await concurrent.commit()

            rowcount = await publish(
                stale,
                job_id="terminal-cas-job",
                snapshot=snapshot,
                changes={
                    "status": "completed",
                    "queue_status": "completed",
                    "current_stage": "Complete",
                    "completed_at": datetime(2026, 8, 20, 18, 2, 0),
                },
            )
            assert rowcount == 0
            await stale.rollback()

        async with factory() as verify:
            persisted = await verify.scalar(select(Job).where(Job.id == "terminal-cas-job"))
            assert persisted is not None
            assert getattr(persisted, field) == value
            assert persisted.status == "running"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_publication_applies_once_when_the_full_snapshot_matches(tmp_path) -> None:
    service = _service()
    capture = getattr(service, "capture_terminal_job_publication_snapshot", None)
    publish = getattr(service, "publish_terminal_job_changes", None)
    assert callable(capture)
    assert callable(publish)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'terminal-cas-positive.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            job = _job()
            session.add(job)
            await session.commit()
            snapshot = capture(job)
            rowcount = await publish(
                session,
                job_id=job.id,
                snapshot=snapshot,
                changes={"status": "completed", "queue_status": "completed"},
            )
            assert rowcount == 1
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_publication_matches_semantically_equal_persisted_json(tmp_path) -> None:
    service = _service()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'terminal-cas-json.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add(_job())
            await session.commit()
            await session.execute(
                text(
                    "UPDATE jobs SET params=:params, provenance=:provenance, "
                    "stage_outputs=:stage_outputs, selected_loop_scope='null' WHERE id=:job_id"
                ),
                {
                    "job_id": "terminal-cas-job",
                    "params": '{ "owner_nonce": "owner-a", "execution_generation": 4 }',
                    "provenance": json.dumps(
                        {"execution": {"invocation_id": "invocation-a", "unit": "unit-a"}},
                        separators=(",", ":"),
                    ),
                    "stage_outputs": '{ "fastq_align": [ "bms_results/run/align/aligned.bam" ] }',
                },
            )
            await session.commit()
            session.expire_all()
            job = await session.get(Job, "terminal-cas-job")
            assert job is not None
            snapshot = service.capture_terminal_job_publication_snapshot(job)

            rowcount = await service.publish_terminal_job_changes(
                session,
                job_id=job.id,
                snapshot=snapshot,
                changes={"status": "completed", "queue_status": "completed"},
            )

            assert rowcount == 1
            await session.commit()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "failed", "queue_status": "failed"},
        {"status": "completed", "queue_status": "completed"},
        {"status": "cancelled", "queue_status": "cancelled"},
        {"paused": True},
        {"awaiting_input": True, "awaiting_stage": "review", "awaiting_payload": {"gate": "review"}},
        {"awaiting_stage": "review"},
        {"awaiting_payload": {"gate": "review"}},
    ],
)
async def test_terminal_publication_rejects_every_nonactive_or_gated_snapshot(
    tmp_path,
    overrides: dict[str, Any],
) -> None:
    service = _service()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'terminal-cas-state.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            job = _job()
            for field, value in overrides.items():
                setattr(job, field, value)
            session.add(job)
            await session.commit()
            snapshot = service.capture_terminal_job_publication_snapshot(job)
            rowcount = await service.publish_terminal_job_changes(
                session,
                job_id=job.id,
                snapshot=snapshot,
                changes={"status": "completed", "queue_status": "completed"},
            )
            assert rowcount == 0
            await session.rollback()
        async with factory() as verify:
            persisted = await verify.get(Job, "terminal-cas-job")
            assert persisted is not None
            for field, value in overrides.items():
                assert getattr(persisted, field) == value
    finally:
        await engine.dispose()
