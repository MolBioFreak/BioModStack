from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingRecord, ConformationalMappingRequest, Job
from routers.conformational_mapping import SubmitRequest
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
