from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, ExternalResultImport, Job
from services import boltz_api_jobs
from services.boltz_api_jobs import (
    BOLTZ_API_MODEL,
    BOLTZ_API_POLL_INTERVAL_SECONDS,
    BoltzApiJobError,
    BoltzApiJobWorker,
    build_boltz_api_input,
    estimate_fingerprint,
    process_boltz_api_job,
    queue_boltz_api_job,
)


def test_build_provider_input_preserves_complex_entities_and_chain_ids() -> None:
    provider_input = build_boltz_api_input(
        sequence="ACDE",
        primary_chain_id="A",
        complex_components=[
            {"type": "protein", "id": "A", "sequence": "ACDE"},
            {"type": "dna", "id": "B", "sequence": "ACGT"},
            {"type": "ligand", "id": "C", "ccd": "ATP"},
        ],
        num_samples=3,
        use_msa=True,
    )

    assert provider_input == {
        "entities": [
            {"type": "protein", "value": "ACDE", "chain_ids": ["A"]},
            {"type": "dna", "value": "ACGT", "chain_ids": ["B"]},
            {"type": "ligand_ccd", "value": "ATP", "chain_ids": ["C"]},
        ],
        "num_samples": 3,
    }


def test_build_provider_input_uses_documented_ligand_and_empty_msa_shapes() -> None:
    provider_input = build_boltz_api_input(
        sequence="ACDE",
        primary_chain_id="A",
        complex_components=[
            {"type": "protein", "id": "A", "sequence": "ACDE"},
            {"type": "ligand", "id": "B", "smiles": "CCO"},
            {"type": "ion", "id": "C", "ccd": "MG"},
        ],
        num_samples=1,
        use_msa=False,
    )

    assert provider_input["entities"] == [
        {"type": "protein", "value": "ACDE", "chain_ids": ["A"], "msa": {"type": "empty"}},
        {"type": "ligand_smiles", "value": "CCO", "chain_ids": ["B"]},
        {"type": "ligand_ccd", "value": "MG", "chain_ids": ["C"]},
    ]


def test_worker_uses_fifteen_second_provider_poll_interval() -> None:
    worker = BoltzApiJobWorker(None)  # type: ignore[arg-type]
    assert BOLTZ_API_POLL_INTERVAL_SECONDS == 15.0
    assert worker._poll_interval == 15.0


@pytest.mark.asyncio
async def test_downloader_uses_fifteen_second_poll_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*args, **_kwargs):
        captured.extend(str(arg) for arg in args)
        return FakeProcess()

    monkeypatch.setattr(boltz_api_jobs, "_cli_binary", lambda: "/tmp/boltz-api")
    monkeypatch.setattr(boltz_api_jobs.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    await boltz_api_jobs._download_results("provider-job", tmp_path / "run")

    index = captured.index("--poll-interval-seconds")
    assert captured[index + 1] == "15"


def test_build_provider_input_rejects_duplicate_chains() -> None:
    with pytest.raises(BoltzApiJobError, match="unique across components"):
        build_boltz_api_input(
            sequence="ACDE",
            primary_chain_id="A",
            complex_components=[
                {"type": "protein", "id": "A", "sequence": "ACDE"},
                {"type": "dna", "id": "A", "sequence": "ACGT"},
            ],
            num_samples=1,
            use_msa=True,
        )


def test_build_provider_input_rejects_more_than_ten_samples() -> None:
    with pytest.raises(BoltzApiJobError, match="between 1 and 10"):
        build_boltz_api_input(
            sequence="ACDE",
            primary_chain_id="A",
            complex_components=[],
            num_samples=11,
            use_msa=True,
        )


def test_estimate_fingerprint_ignores_volatile_provider_metadata_but_binds_cost() -> None:
    provider_input = {"entities": [{"type": "protein", "value": "ACDE", "chain_ids": ["A"]}], "num_samples": 1}
    first = estimate_fingerprint(
        model=BOLTZ_API_MODEL,
        provider_input=provider_input,
        estimate={"request_id": "one", "created_at": "now", "amount": 0.25, "currency": "USD"},
    )
    second = estimate_fingerprint(
        model=BOLTZ_API_MODEL,
        provider_input=provider_input,
        estimate={"request_id": "two", "created_at": "later", "amount": 0.25, "currency": "USD"},
    )
    changed = estimate_fingerprint(
        model=BOLTZ_API_MODEL,
        provider_input=provider_input,
        estimate={"request_id": "three", "created_at": "later", "amount": 0.30, "currency": "USD"},
    )
    assert first == second
    assert first != changed

    breakdown_changed = estimate_fingerprint(
        model=BOLTZ_API_MODEL,
        provider_input=provider_input,
        estimate={
            "request_id": "four",
            "amount": 0.25,
            "currency": "USD",
            "breakdown": {"cost_per_unit_usd": 0.25, "num_units": 2},
        },
    )
    assert first != breakdown_changed


@pytest.mark.asyncio
async def test_cost_approval_creates_durable_remote_queue_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    provider_input = {"entities": [{"type": "protein", "value": "ACDE", "chain_ids": ["A"]}], "num_samples": 1}
    estimate = {"amount": 0.25, "currency": "USD"}
    fingerprint = estimate_fingerprint(model=BOLTZ_API_MODEL, provider_input=provider_input, estimate=estimate)
    client_request_id = "11111111-1111-4111-8111-111111111111"
    estimate_calls = 0

    async def fake_estimate(*, model: str, provider_input: dict):
        nonlocal estimate_calls
        estimate_calls += 1
        return estimate, estimate_fingerprint(model=model, provider_input=provider_input, estimate=estimate)

    monkeypatch.setattr(boltz_api_jobs, "estimate_boltz_api_cost", fake_estimate)
    try:
        async with Session() as session:
            job = await queue_boltz_api_job(
                session,
                name="remote fold",
                client_request_id=client_request_id,
                model=BOLTZ_API_MODEL,
                provider_input=provider_input,
                approved_estimate_fingerprint=fingerprint,
            )
            assert job.model_id == "boltz_api"
            assert job.status == "queued"
            assert job.params["provider_state"] == "submitting"
            assert job.params["provider_idempotency_key"] == f"bms-{client_request_id}"
            replay = await queue_boltz_api_job(
                session,
                name="remote fold replay",
                client_request_id=client_request_id,
                model=BOLTZ_API_MODEL,
                provider_input=provider_input,
                approved_estimate_fingerprint=fingerprint,
            )
            assert replay.id == job.id
            assert estimate_calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_submits_queued_job_and_persists_provider_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    calls: list[tuple[str, str]] = []

    async def fake_payload(provider_input: dict, operation: str, *, model: str, idempotency_key: str | None = None):
        calls.append((operation, idempotency_key or ""))
        return {"id": "sab_pred_remote123", "status": "pending"}

    monkeypatch.setattr(boltz_api_jobs, "_with_payload_file", fake_payload)
    try:
        async with Session() as session:
            job = Job(
                id="job-remote-1",
                name="remote fold",
                status="queued",
                queue_status="queued",
                model_id="boltz_api",
                mode="external_api",
                params={
                    "provider_state": "submitting",
                    "provider_model": BOLTZ_API_MODEL,
                    "provider_input": {"entities": [{"type": "protein", "value": "ACDE", "chain_ids": ["A"]}], "num_samples": 1},
                    "provider_idempotency_key": "bms-job-remote-1",
                },
            )
            session.add(job)
            await session.commit()
            await process_boltz_api_job(session, job)
            await session.refresh(job)
            assert job.status == "running"
            assert job.params["provider_job_id"] == "sab_pred_remote123"
            assert job.params["provider_state"] == "submitted"
            assert calls == [("start", "bms-job-remote-1")]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_propagates_failed_ingestion_to_the_original_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_DATA_ROOT", str(tmp_path / "data"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            job = Job(
                id="job-import-failed",
                name="remote fold",
                status="running",
                queue_status="running",
                model_id="boltz_api",
                mode="external_api",
                params={
                    "provider_state": "importing",
                    "provider_model": BOLTZ_API_MODEL,
                    "provider_input": {"entities": [{"type": "protein", "value": "ACDE", "chain_ids": ["A"]}], "num_samples": 1},
                    "external_import_id": "import-failed",
                },
            )
            session.add(job)
            session.add(ExternalResultImport(
                id="import-failed",
                provider_id="boltz_api",
                resource_type="predictions:structure-and-binding",
                provider_job_id="sab_pred_failed",
                state="failed",
                source_path=str(tmp_path),
                source_fingerprint="a" * 64,
                run_metadata_sha256="b" * 64,
                archive_sha256="c" * 64,
                bms_job_id=job.id,
                dataset_name="remote",
                failure_code="PAE_INVALID",
                failure_message="canonical PAE validation failed",
                provider_metadata={},
            ))
            await session.commit()

        worker = BoltzApiJobWorker(Session)
        assert await worker.run_once() == 0
        async with Session() as session:
            failed_job = await session.get(Job, "job-import-failed")
            assert failed_job.status == "failed"
            assert failed_job.params["provider_last_error_code"] == "PAE_INVALID"
            assert failed_job.error_message == "canonical PAE validation failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_http_replay_returns_the_same_job_without_integrity_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    provider_input = {"entities": [{"type": "protein", "value": "ACDE", "chain_ids": ["A"]}], "num_samples": 1}
    estimate = {"amount": 0.25, "currency": "USD"}
    fingerprint = estimate_fingerprint(model=BOLTZ_API_MODEL, provider_input=provider_input, estimate=estimate)
    both_estimating = asyncio.Event()
    estimate_calls = 0

    async def fake_estimate(*, model: str, provider_input: dict):
        nonlocal estimate_calls
        estimate_calls += 1
        if estimate_calls == 2:
            both_estimating.set()
        await both_estimating.wait()
        return estimate, estimate_fingerprint(model=model, provider_input=provider_input, estimate=estimate)

    async def submit() -> Job:
        async with Session() as session:
            return await queue_boltz_api_job(
                session,
                name="concurrent replay",
                client_request_id="33333333-3333-4333-8333-333333333333",
                model=BOLTZ_API_MODEL,
                provider_input=provider_input,
                approved_estimate_fingerprint=fingerprint,
            )

    monkeypatch.setattr(boltz_api_jobs, "estimate_boltz_api_cost", fake_estimate)
    try:
        first, second = await asyncio.gather(submit(), submit())
        assert first.id == second.id
        async with Session() as session:
            assert await session.get(Job, first.id) is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancellation_committed_after_worker_refresh_cannot_be_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    BaseSession = async_sessionmaker(engine, expire_on_commit=False)
    interleave = False
    provider_input = {"entities": [{"type": "protein", "value": "ACDE", "chain_ids": ["A"]}], "num_samples": 1}
    original_transition = boltz_api_jobs._commit_active_job_transition

    async def cancel_then_transition(session, job_id: str, **values):
        nonlocal interleave
        if interleave:
            interleave = False
            async with BaseSession() as cancellation_session:
                cancelled = await cancellation_session.get(Job, "job-cancel-race")
                cancelled.status = "cancelled"
                cancelled.queue_status = "cancelled"
                await cancellation_session.commit()
        return await original_transition(session, job_id, **values)

    async def fake_payload(provider_input: dict, operation: str, *, model: str, idempotency_key: str | None = None):
        return {"id": "sab_pred_cancel_race", "status": "pending"}

    monkeypatch.setattr(boltz_api_jobs, "_with_payload_file", fake_payload)
    monkeypatch.setattr(boltz_api_jobs, "_commit_active_job_transition", cancel_then_transition)
    try:
        async with BaseSession() as session:
            session.add(Job(
                id="job-cancel-race",
                name="cancel race",
                status="queued",
                queue_status="queued",
                model_id="boltz_api",
                mode="external_api",
                params={
                    "provider_state": "submitting",
                    "provider_model": BOLTZ_API_MODEL,
                    "provider_input": provider_input,
                    "provider_idempotency_key": "bms-cancel-race",
                },
            ))
            await session.commit()

        async with BaseSession() as session:
            job = await session.get(Job, "job-cancel-race")
            interleave = True
            await process_boltz_api_job(session, job)

        async with BaseSession() as session:
            cancelled = await session.get(Job, "job-cancel-race")
            assert cancelled.status == "cancelled"
            assert cancelled.queue_status == "cancelled"
    finally:
        await engine.dispose()
