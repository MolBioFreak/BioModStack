from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import database
from database import Base, Job, RFD3LocalRedesignRequest
from services import job_control, nextflow
from services.execution_ownership import deterministic_unit_name


async def _create_rfd3_job_and_request(
    factory: sessionmaker,
    *,
    job_id: str,
    request_id: str,
    awaiting_input: bool = False,
) -> None:
    async with factory() as session:
        session.add_all(
            [
                Job(
                    id=job_id,
                    name="RFD3 launch failure",
                    model_id="protein_local_redesign",
                    mode="local_redesign",
                    status="running",
                    queue_status="running",
                    params={},
                    awaiting_input=awaiting_input,
                    started_at=datetime.utcnow(),
                    created_at=datetime.utcnow(),
                ),
                RFD3LocalRedesignRequest(
                    request_id=request_id,
                    job_id=job_id,
                    schema_version=1,
                    request_sha256="1" * 64,
                    profile_id="generic_local_redesign_v1",
                    profile_registry_sha256="2" * 64,
                    redesign_mode="partial_diffusion",
                    sequence_policy="skip",
                    status="running",
                    request_json={"schema": "bms.rfd3.local-redesign.request.v1"},
                ),
            ]
        )
        await session.commit()


def _configure_transient_launch(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker,
    tmp_path: Path,
    *,
    job_id: str,
) -> None:
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)
    monkeypatch.setenv("BMS_TRANSIENT_WORKFLOW_UNIT", "1")
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_LANE", "development")
    monkeypatch.setenv(
        "BMS_TRANSIENT_WORKFLOW_UNIT_NAME",
        deterministic_unit_name("development", job_id, 1),
    )
    monkeypatch.setenv("BMS_TRANSIENT_WORKFLOW_OWNER_NONCE", "owner-1")
    lane_root = tmp_path / "development-lane"
    monkeypatch.setenv("BMS_STATE_DIR", str(lane_root / "state"))
    monkeypatch.setenv("BMS_DB_PATH", str(lane_root / "state" / "bms.db"))
    monkeypatch.setenv("BMS_WORK", str(lane_root / "work"))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(lane_root / "results"))
    monkeypatch.setattr(database, "async_session", factory)
    monkeypatch.setattr(nextflow, "assert_workflow_launch_allowed", lambda _action: None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("lifecycle_override", "failure_published"),
    [
        ({}, True),
        ({"awaiting_input": True}, False),
        ({"paused": True}, False),
        ({"awaiting_stage": "review"}, False),
        ({"awaiting_payload": {"gate": "review"}}, False),
    ],
)
async def test_rfd3_missing_gpu_failure_updates_typed_request_only_after_job_cas(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lifecycle_override: dict,
    failure_published: bool,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rfd3-launch-failure.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    job_id = "rfd3-gpu-authority-failure"
    request_id = "rfd3-gpu-authority-request"
    try:
        await _create_rfd3_job_and_request(
            factory,
            job_id=job_id,
            request_id=request_id,
        )
        if lifecycle_override:
            async with factory() as session:
                guarded_job = await session.get(Job, job_id)
                assert guarded_job is not None
                for field, value in lifecycle_override.items():
                    setattr(guarded_job, field, value)
                await session.commit()
        _configure_transient_launch(monkeypatch, factory, tmp_path, job_id=job_id)

        await nextflow.launch_nextflow_job(
            job_id=job_id,
            model_id="protein_local_redesign",
            mode="local_redesign",
            params={},
            output_dir=str(tmp_path / "output"),
            allow_running_job=True,
        )

        async with factory() as session:
            job = await session.get(Job, job_id)
            request = await session.get(RFD3LocalRedesignRequest, request_id)
            assert job is not None and request is not None
            if not failure_published:
                assert job.status == "running"
                assert request.status == "running"
                assert request.failure_receipt_json is None
                assert request.terminal_at is None
            else:
                assert job.status == "failed"
                assert job.queue_status == "failed"
                assert job.error_message == "GPU-required workflow has no authoritative scheduler GPU assignment"
                assert request.status == "failed"
                assert request.failure_receipt_json == {
                    "schema": "bms.rfd3.local-redesign.failure-receipt.v1",
                    "job_id": job_id,
                    "status": "failed",
                    "error_message": job.error_message,
                }
                assert request.terminal_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rfd3_cancellation_terminalizes_typed_request_with_completed_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rfd3-cancel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    job_id = "rfd3-cancel"
    request_id = "rfd3-cancel-request"
    try:
        await _create_rfd3_job_and_request(factory, job_id=job_id, request_id=request_id)

        async def stopped_and_empty(_run_id: str) -> bool:
            return True

        monkeypatch.setattr(job_control, "cancel_nextflow_job", stopped_and_empty)
        async with factory() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            job.nextflow_run_id = "biomodstack-development-job-rfd3-cancel-attempt-1.service"
            await session.commit()
            await job_control.cancel_job_lineage(job_id, session)

        async with factory() as session:
            job = await session.get(Job, job_id)
            request = await session.get(RFD3LocalRedesignRequest, request_id)
            assert job is not None and request is not None
            assert job.status == "cancelled"
            assert request.status == "cancelled"
            assert request.terminal_at is not None
            receipt = request.failure_receipt_json
            assert receipt is not None
            assert receipt["status"] == "cancelled"
            assert receipt["cancellation_receipt"] == job.params["cancellation_receipt"]
            assert receipt["cancellation_receipt"]["state"] == "completed"
    finally:
        await engine.dispose()
