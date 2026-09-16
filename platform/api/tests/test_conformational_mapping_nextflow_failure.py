from __future__ import annotations

import importlib.metadata
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import database
from database import Base, ConformationalMappingRecord, ConformationalMappingRequest, Job
from services import nextflow
from services.conformational_mapping.persistence import (
    register_prepared_request,
    terminalize_failed_request_for_job,
    transition_request,
)
from services.execution_ownership import UnitProperties


class _FailedNextflowProcess:
    pid = 4242
    returncode = 1

    async def wait(self) -> int:
        return 1


def _load_cm_router_without_pydna(monkeypatch: pytest.MonkeyPatch):
    """Load the CM retry route despite this checkout's unrelated missing optional dep."""

    real_version = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "5.5.16" if name == "pydna" else real_version(name),
    )
    pydna = types.ModuleType("pydna")
    pydna.__path__ = []
    monkeypatch.setitem(sys.modules, "pydna", pydna)
    for name, attributes in {
        "pydna.assembly2": {"Assembly": object},
        "pydna.design": {
            "assembly_fragments": lambda *_args, **_kwargs: None,
            "primer_design": lambda *_args, **_kwargs: None,
        },
        "pydna.dseqrecord": {"Dseqrecord": object},
        "pydna.tm": {"tm_default": lambda *_args, **_kwargs: 0.0},
    }.items():
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    from routers import conformational_mapping as cm_router

    return cm_router


async def _create_cm_job_and_request(
    factory: sessionmaker,
    *,
    job_id: str,
    request_id: str,
    awaiting_input: bool = False,
) -> None:
    async with factory() as session:
        job = Job(
            id=job_id,
            name="CM failure",
            model_id="conformational_mapping",
            mode="map",
            status="queued",
            queue_status="running",
            stage_family="conformational_mapping",
            params={},
            awaiting_input=awaiting_input,
            created_at=datetime.utcnow(),
        )
        record = await register_prepared_request(
            session,
            job=job,
            principal_id="alice",
            request={
                "request_id": request_id,
                "request_sha256": "a" * 64,
                "backend": "protenix_v2_ensemble",
            },
            coordinate_plan={
                "coordinate_plan_sha256": "b" * 64,
                "expected_cardinality": 1,
                "coordinates": [{}],
            },
            resume_key="0" * 64,
            capability_sha256="c" * 64,
        )
        await transition_request(session, record, status="queued", progress={"phase": "queued"})
        await session.commit()


async def _failure_receipt_for(
    session: AsyncSession,
    *,
    request_id: str,
) -> ConformationalMappingRecord | None:
    return await session.scalar(
        select(ConformationalMappingRecord).where(
            ConformationalMappingRecord.request_id == request_id,
            ConformationalMappingRecord.record_type == "failure_receipt",
        )
    )


async def _assert_terminalized_failure(
    factory: sessionmaker,
    *,
    job_id: str,
    request_id: str,
    message: str,
) -> ConformationalMappingRequest:
    async with factory() as session:
        job = await session.get(Job, job_id)
        request = await session.get(ConformationalMappingRequest, request_id)
        receipt = await _failure_receipt_for(session, request_id=request_id)
        assert job is not None and job.status == "failed"
        assert request is not None and request.status == "failed"
        assert receipt is not None
        assert request.failure_receipt_json == receipt.payload_json
        assert request.failure_receipt_json == {
            "schema_name": "cm_failure_receipt",
            "schema_version": 1,
            "request_id": request_id,
            "job_id": job_id,
            "terminal_state": "failed",
            "message": message,
            "recorded_at": request.failure_receipt_json["recorded_at"],
        }
        assert request.terminal_at is not None
        return request


def _configure_local_nextflow_launch(
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("BMS_WORKFLOW_ADAPTER_URL", raising=False)
    monkeypatch.setenv("BMS_WORKFLOW_ADAPTER_LANE", "development")
    lane_root = tmp_path / "development-lane"
    monkeypatch.setenv("BMS_STATE_DIR", str(lane_root / "state"))
    monkeypatch.setenv("BMS_DB_PATH", str(lane_root / "state" / "bms.db"))
    monkeypatch.setenv("BMS_WORK", str(lane_root / "work"))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(lane_root / "results"))
    monkeypatch.setattr(database, "async_session", factory)
    monkeypatch.setattr(nextflow, "assert_workflow_launch_allowed", lambda _action: None)
    monkeypatch.setattr(nextflow, "resolve_nextflow_java_env", lambda env: (env, []))


@pytest.mark.asyncio
@pytest.mark.parametrize("spawn_route", ["direct", "systemd"])
@pytest.mark.parametrize("retry", [False, True])
async def test_failed_cm_nextflow_run_terminalizes_linked_request_with_immutable_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, spawn_route: str, retry: bool,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cm-nextflow-failure.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    job_id = "cm-job-failed"
    request_id = "cm-request-failed"
    try:
        await _create_cm_job_and_request(factory, job_id=job_id, request_id=request_id)

        _configure_local_nextflow_launch(monkeypatch, factory, tmp_path)
        monkeypatch.setattr(nextflow, "preflight_nextflow_java", lambda _env: (True, "test java"))
        from component_runtime import NativeInvocation
        # Failure-publication/consumer test: compiler and image resolver are
        # isolated; the actual launch loop and both spawn consumers run below.
        monkeypatch.setattr(nextflow, "compile_job_nextflow_invocation", lambda *_args, **_kwargs:
            NativeInvocation.capture(model_id="conformational_mapping", mode="map",
                command=("nextflow",), requested={}, effective={}, native_parameters={},
                entrypoint="workflows/conformational_mapping.nf"))
        monkeypatch.setattr(nextflow, "transient_workflow_runner_mode", lambda: spawn_route == "direct")
        monkeypatch.setenv("BMS_TRANSIENT_WORKFLOW_UNIT_NAME", "biomodstack-development-job-cm-job-failed-attempt-1.service")
        monkeypatch.setenv("BMS_TRANSIENT_WORKFLOW_OWNER_NONCE", "test-owner")
        monkeypatch.setenv("BMS_SELECTED_IMAGE_STALE", "must-not-reach-spawn")
        image_environments = []
        async def pin_images(_session, _job, invocation):
            assert invocation.model_id == "conformational_mapping"
            index = len(image_environments) + 1
            selected = {"BMS_SELECTED_IMAGE_TEST": str(tmp_path / f"image-{index}.sif")}
            if index == 1:
                selected["BMS_SELECTED_IMAGE_FIRST_ONLY"] = "first-only"
            image_environments.append(selected)
            return selected
        monkeypatch.setattr(nextflow, "_pin_local_invocation_images", pin_images)
        spawned_environments = []
        async def no_sleep(_seconds):
            pass
        monkeypatch.setattr(nextflow.asyncio, "sleep", no_sleep)
        async with factory() as session:
            job = await session.get(Job, job_id)
            job.nextflow_run_id = "biomodstack-development-job-cm-job-failed-attempt-1.service"
            await session.commit()

        def record_spawn(env):
            selected = {key: value for key, value in env.items() if key.startswith("BMS_SELECTED_IMAGE_")}
            spawned_environments.append(selected)
            assert selected == image_environments[-1]
            if retry and len(spawned_environments) == 1:
                with (tmp_path / "output" / "nextflow.log").open("a") as log:
                    log.write("Unable to acquire lock on session with ID fixture\n")

        async def fake_create_subprocess_exec(*_args: object, **_kwargs: object) -> _FailedNextflowProcess:
            assert spawn_route == "direct"
            record_spawn(_kwargs["env"])
            return _FailedNextflowProcess()

        monkeypatch.setattr(nextflow.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        def create_unit(command):
            assert spawn_route == "systemd"
            env = dict(arg.removeprefix("--setenv=").split("=", 1)
                       for arg in command if arg.startswith("--setenv="))
            record_spawn(env)
            return f"biomodstack-development-job-cm-job-failed-attempt-{len(spawned_environments)}.service"
        monkeypatch.setattr(nextflow, "create_systemd_workflow_unit", create_unit)
        monkeypatch.setattr(
            nextflow,
            "show_unit_properties",
            lambda *_args: UnitProperties(
                active_state="inactive",
                sub_state="failed",
                control_group="",
                main_pid="0",
                exec_main_status="1",
                result="exit-code",
                slice_name="biomodstack-development-workflows.slice",
            ),
        )
        await nextflow.launch_nextflow_job(
            job_id=job_id,
            model_id="conformational_mapping",
            mode="map",
            params={"allow_retries": retry, "resume_work_dir": str(tmp_path / "resume"),
                    "resume_lock_retry_attempts": 1},
            output_dir=str(tmp_path / "output"),
        )
        assert spawned_environments == image_environments
        assert len(spawned_environments) == (2 if retry else 1)
        if retry:
            assert "BMS_SELECTED_IMAGE_FIRST_ONLY" not in spawned_environments[1]

        request = await _assert_terminalized_failure(
            factory,
            job_id=job_id,
            request_id=request_id,
            message=("Nextflow resume lock contention after retries: Unable to acquire lock on session with ID fixture"
                     if retry else "Nextflow exited with code 1"),
        )
        async with factory() as session:
            cm_router = _load_cm_router_without_pydna(monkeypatch)

            async def authorize_failed_request(*_args: object, **_kwargs: object) -> ConformationalMappingRequest:
                return request

            monkeypatch.setattr(cm_router, "_authorized_record", authorize_failed_request)
            fake_request: Any = None
            with pytest.raises(HTTPException) as retry_error:
                await cm_router.retry_request(request_id, request=fake_request, session=session)
            assert retry_error.value.status_code == 409
            job = await session.get(Job, job_id)
            assert job is not None and job.status == "failed" and job.queue_status == "failed"
            persisted = await session.get(ConformationalMappingRequest, request_id)
            assert persisted is not None and persisted.status == "failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_java_preflight_failure_terminalizes_linked_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cm-java-preflight-failure.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    job_id = "cm-job-java-preflight-failed"
    request_id = "cm-request-java-preflight-failed"
    try:
        await _create_cm_job_and_request(factory, job_id=job_id, request_id=request_id)
        _configure_local_nextflow_launch(monkeypatch, factory, tmp_path)
        monkeypatch.setattr(nextflow, "preflight_nextflow_java", lambda _env: (False, "Java 17 unavailable"))

        await nextflow.launch_nextflow_job(
            job_id=job_id,
            model_id="conformational_mapping",
            mode="map",
            params={},
            output_dir=str(tmp_path / "output"),
        )

        await _assert_terminalized_failure(
            factory,
            job_id=job_id,
            request_id=request_id,
            message="Nextflow Java preflight failed: Java 17 unavailable",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_nextflow_command_build_exception_terminalizes_linked_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cm-command-build-failure.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    job_id = "cm-job-command-build-failed"
    request_id = "cm-request-command-build-failed"
    try:
        await _create_cm_job_and_request(factory, job_id=job_id, request_id=request_id)
        _configure_local_nextflow_launch(monkeypatch, factory, tmp_path)
        monkeypatch.setattr(nextflow, "preflight_nextflow_java", lambda _env: (True, "test java"))

        def fail_to_build_command(*_args: object, **_kwargs: object) -> tuple[str, ...]:
            raise RuntimeError("command construction exploded")

        monkeypatch.setattr(nextflow, "compile_job_nextflow_invocation", fail_to_build_command)

        await nextflow.launch_nextflow_job(
            job_id=job_id,
            model_id="conformational_mapping",
            mode="map",
            params={},
            output_dir=str(tmp_path / "output"),
        )

        await _assert_terminalized_failure(
            factory,
            job_id=job_id,
            request_id=request_id,
            message="command construction exploded",
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_java_preflight_failure_does_not_terminalize_when_guarded_job_publish_loses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cm-java-preflight-cas-loss.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    job_id = "cm-job-java-preflight-cas-loss"
    request_id = "cm-request-java-preflight-cas-loss"
    try:
        await _create_cm_job_and_request(
            factory,
            job_id=job_id,
            request_id=request_id,
            awaiting_input=True,
        )
        _configure_local_nextflow_launch(monkeypatch, factory, tmp_path)
        monkeypatch.setattr(nextflow, "preflight_nextflow_java", lambda _env: (False, "Java 17 unavailable"))

        await nextflow.launch_nextflow_job(
            job_id=job_id,
            model_id="conformational_mapping",
            mode="map",
            params={},
            output_dir=str(tmp_path / "output"),
        )

        async with factory() as session:
            job = await session.get(Job, job_id)
            request = await session.get(ConformationalMappingRequest, request_id)
            assert job is not None and job.status == "running"
            assert request is not None and request.status == "queued"
            assert await _failure_receipt_for(session, request_id=request_id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cm_failure_terminalization_ignores_unrelated_and_completed_requests(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cm-failure-safety.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            unrelated_job = Job(
                id="unrelated-job",
                name="unrelated",
                model_id="boltz2",
                mode="predict",
                status="failed",
                queue_status="failed",
                stage_family="structure_prediction",
                error_message="unrelated failure",
                params={},
                created_at=datetime.utcnow(),
            )
            unrelated = await register_prepared_request(
                session,
                job=unrelated_job,
                principal_id="alice",
                request={
                    "request_id": "unrelated-request",
                    "request_sha256": "d" * 64,
                    "backend": "protenix_v2_ensemble",
                },
                coordinate_plan={
                    "coordinate_plan_sha256": "e" * 64,
                    "expected_cardinality": 1,
                    "coordinates": [{}],
                },
                resume_key="1" * 64,
                capability_sha256="2" * 64,
            )
            completed_job = Job(
                id="completed-job",
                name="completed",
                model_id="conformational_mapping",
                mode="map",
                status="failed",
                queue_status="failed",
                stage_family="conformational_mapping",
                error_message="late failure",
                params={},
                created_at=datetime.utcnow(),
            )
            completed = await register_prepared_request(
                session,
                job=completed_job,
                principal_id="alice",
                request={
                    "request_id": "completed-request",
                    "request_sha256": "f" * 64,
                    "backend": "protenix_v2_ensemble",
                },
                coordinate_plan={
                    "coordinate_plan_sha256": "a" * 64,
                    "expected_cardinality": 1,
                    "coordinates": [{}],
                },
                resume_key="3" * 64,
                capability_sha256="4" * 64,
            )
            await transition_request(session, completed, status="queued")
            await transition_request(session, completed, status="running")
            await transition_request(session, completed, status="completed")

            assert await terminalize_failed_request_for_job(session, job_id=unrelated_job.id) is False
            assert await terminalize_failed_request_for_job(session, job_id=completed_job.id) is False
            await session.commit()

        async with factory() as session:
            assert (await session.get(ConformationalMappingRequest, unrelated.request_id)).status == "prepared"
            assert (await session.get(ConformationalMappingRequest, completed.request_id)).status == "completed"
            assert await session.scalar(
                select(ConformationalMappingRecord).where(
                    ConformationalMappingRecord.record_type == "failure_receipt"
                )
            ) is None
    finally:
        await engine.dispose()
