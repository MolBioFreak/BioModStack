from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import routers.conformational_mapping as cm_router
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingRecord, ConformationalMappingRequest, Job
from routers.conformational_mapping import (
    SubmitRequest,
    _mutation_principal,
    _principal,
    _registered_source_format,
    request_status,
    retry_request,
)
from services.conformational_mapping.persistence import (
    ConformationalPersistenceError,
    capability_matches,
    issue_request_capability,
    register_prepared_request,
    transition_request,
)


def _body() -> dict:
    return {
        "name": "map", "backend": "external_import", "registered_snapshot_id": "snapshot",
        "registered_artifact_ids": ["structure"], "ordered_seeds": [0], "samples_per_seed": 1,
        "feature_policy": {"mode": "features_disabled_control_v1", "protein_msa_enabled": False, "templates_enabled": False, "rna_msa_enabled": False},
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {"sign_zero_epsilon": 1e-6, "clash_detector_id": "bms_sidechain_clash_v1", "clash_detector_version": "1", "outer_support_minimum": 1.0, "inner_support_minimum": 1.0, "sign_consistency_minimum": 1.0, "clash_free_minimum": 1.0, "rank_stability_minimum": 1.0, "minimum_common_ranked_universe_size": 3},
    }


def _http_request(
    *,
    client_host: str,
    headers: dict[str, str] | None = None,
) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/api/conformational-mapping/sources",
        "query_string": b"",
        "headers": [
            (name.lower().encode("ascii"), value.encode("ascii"))
            for name, value in (headers or {}).items()
        ],
        "client": (client_host, 42000),
        "server": ("127.0.0.1", 8000),
    })


def test_registered_source_format_is_server_normalized() -> None:
    assert _registered_source_format("source/content.cif") == "mmcif"
    assert _registered_source_format("source/content.MMCIF") == "mmcif"
    assert _registered_source_format("legacy/content.pdb") == "pdb"


def test_cm_personal_workflow_principal_is_available_without_proxy_or_operator_credentials() -> None:
    assert _principal(_http_request(client_host="127.0.0.1")) == "local-personal-workflow"
    assert _principal(_http_request(client_host="100.64.0.12", headers={"Authorization": "Bearer ignored"})) == (
        "local-personal-workflow"
    )


def test_cm_personal_workflow_mutations_need_no_browser_origin_or_operator_credential() -> None:
    assert _mutation_principal(_http_request(client_host="127.0.0.1")) == "local-personal-workflow"


@pytest.mark.asyncio
async def test_request_status_projects_terminal_job_without_mutating_on_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = SimpleNamespace(
        request_id="request-1", job_id="job-1", backend="external_import",
        status="queued", progress_json={"phase": "queued"}, failure_receipt_json=None,
        result_contract_id=None,
    )
    job = SimpleNamespace(
        id="job-1", status="failed", queue_status="failed", current_stage="analysis",
        error_message="bounded failure",
    )

    async def authorized(*_args, **_kwargs):
        return record

    class ReadOnlySession:
        async def get(self, *_args, **_kwargs):
            return job

        async def commit(self):
            raise AssertionError("GET must not commit")

    monkeypatch.setattr(cm_router, "_authorized_record", authorized)
    result = await request_status(
        "request-1", _http_request(client_host="127.0.0.1"), ReadOnlySession()
    )
    assert result["status"] == "failed"
    assert result["retry_eligible"] is True
    assert result["failure_receipt"]["terminal_state"] == "failed"
    assert result["failure_receipt"]["message"] == "bounded failure"
    assert record.status == "queued"


@pytest.mark.asyncio
async def test_retry_never_downgrades_completed_request_from_stale_failed_job(monkeypatch) -> None:
    record = SimpleNamespace(status="completed", job_id="job-1")

    async def authorized(*_args, **_kwargs):
        return record

    class UntouchedSession:
        async def get(self, *_args, **_kwargs):
            raise AssertionError("completed guard must run before reading stale job state")

    monkeypatch.setattr(cm_router, "_authorized_record", authorized)
    with pytest.raises(HTTPException, match="completed request authority") as exc:
        await retry_request(
            "request-1", _http_request(client_host="127.0.0.1"), UntouchedSession()
        )
    assert exc.value.status_code == 409
    assert record.status == "completed"


@pytest.mark.asyncio
async def test_retry_uses_authoritative_nextflow_work_dir_for_resume(monkeypatch, tmp_path) -> None:
    record = SimpleNamespace(status="failed", job_id="job-1")
    job = SimpleNamespace(
        id="job-1", status="failed", queue_status="failed", error_message="failed",
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(), nextflow_run_id="123",
        retry_count=1, params={"cm_request_path": "/results/request.json"},
    )

    async def authorized(*_args, **_kwargs):
        return record

    async def transition(_session, target, *, status, progress, **_kwargs):
        target.status = status
        target.progress_json = progress

    class Session:
        async def get(self, *_args, **_kwargs):
            return job

        async def commit(self):
            return None

    work_dir = tmp_path / "work"
    monkeypatch.setattr(cm_router, "_authorized_record", authorized)
    monkeypatch.setattr(cm_router, "transition_request", transition)
    monkeypatch.setattr(cm_router, "get_work_dir", lambda: work_dir)

    result = await retry_request(
        "request-1", _http_request(client_host="127.0.0.1"), Session()
    )

    assert result == {
        "request_id": "request-1", "job_id": "job-1", "status": "queued", "retry_count": 2,
    }
    assert job.params == {
        "cm_request_path": "/results/request.json", "resume_work_dir": str(work_dir),
    }
    assert job.nextflow_run_id is None
    assert record.status == "queued"


@pytest.mark.asyncio
async def test_completed_request_does_not_project_historical_failure_as_current(monkeypatch) -> None:
    record = SimpleNamespace(
        request_id="request-1", job_id="job-1", backend="confornets",
        status="completed", progress_json={"phase": "completed"},
        failure_receipt_json={"terminal_state": "failed", "message": "historical"},
        result_contract_id="conformational_mapping_confornets_v1",
    )
    job = SimpleNamespace(status="completed")

    async def authorized(*_args, **_kwargs):
        return record

    class ReadOnlySession:
        async def get(self, *_args, **_kwargs):
            return job

    monkeypatch.setattr(cm_router, "_authorized_record", authorized)
    result = await request_status(
        "request-1", _http_request(client_host="127.0.0.1"), ReadOnlySession()
    )
    assert result["status"] == "completed"
    assert result["failure_receipt"] is None


def test_cm11_api_typed_submission_rejects_unknown_fields() -> None:
    SubmitRequest.model_validate(_body())
    with pytest.raises(ValidationError):
        SubmitRequest.model_validate({**_body(), "server_path": "/tmp/input.pdb"})


def test_cm11_api_request_capability_is_secret_bound() -> None:
    token, digest = issue_request_capability()
    assert capability_matches(token, digest)
    assert not capability_matches(token + "x", digest)
    assert not capability_matches(None, digest)


@pytest.mark.asyncio
async def test_register_prepared_request_inserts_job_before_cm_request_with_sqlite_foreign_keys(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'foreign_keys.db'}")
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await register_prepared_request(
            session,
            job=Job(
                id="job", name="map", model_id="conformational_mapping", mode="map",
                status="queued", params={},
            ),
            principal_id="alice",
            request={"request_id": "request", "request_sha256": "a" * 64, "backend": "external_import"},
            coordinate_plan={"coordinate_plan_sha256": "b" * 64, "expected_cardinality": 1, "coordinates": [{}]},
            resume_key="0" * 64,
            capability_sha256="c" * 64,
        )
        await session.commit()
        assert await session.get(Job, "job") is not None
        assert await session.get(ConformationalMappingRequest, "request") is not None
    await engine.dispose()


@pytest.mark.asyncio
async def test_cm11_api_lifecycle_survives_session_restart(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        request = {"request_id": "r", "request_sha256": "a" * 64, "backend": "external_import"}
        plan = {"coordinate_plan_sha256": "b" * 64, "expected_cardinality": 1, "coordinates": [{}]}
        record = await register_prepared_request(
            session, job=Job(id="j", name="map", model_id="conformational_mapping", mode="map", status="queued", params={}, created_at=datetime.utcnow()),
            principal_id="alice", request=request, coordinate_plan=plan, resume_key="0" * 64, capability_sha256="c" * 64,
        )
        await transition_request(session, record, status="queued", progress={"phase": "queued"})
        await transition_request(session, record, status="running", progress={"phase": "running"})
        await session.commit()
    async with factory() as session:
        restored = await session.scalar(select(ConformationalMappingRequest).where(ConformationalMappingRequest.request_id == "r"))
        assert restored is not None and restored.status == "running" and restored.progress_json["phase"] == "running"
        receipt = {"schema_name": "cm_failure_receipt", "schema_version": 1, "request_id": "r", "message": "bounded failure"}
        await transition_request(session, restored, status="failed", failure_receipt=receipt)
        await session.commit()
        assert await session.scalar(select(ConformationalMappingRecord).where(ConformationalMappingRecord.record_type == "failure_receipt"))
        await transition_request(session, restored, status="queued", progress={"phase": "queued"})
        assert restored.failure_receipt_json is None
        assert await session.scalar(select(ConformationalMappingRecord).where(ConformationalMappingRecord.record_type == "failure_receipt"))
    await engine.dispose()


@pytest.mark.asyncio
async def test_cm11_api_invalid_lifecycle_transition_fails_closed(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'invalid.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        record = ConformationalMappingRequest(
            request_id="r", job_id="j", principal_id="alice", backend="external_import", status="completed",
            request_sha256="a" * 64, coordinate_plan_sha256="b" * 64, resume_key="0" * 64,
            result_contract_id="conformational_mapping_import_v1", request_json={}, coordinate_plan_json={}, progress_json={},
        )
        with pytest.raises(ConformationalPersistenceError, match="transition"):
            await transition_request(session, record, status="queued")
    await engine.dispose()
