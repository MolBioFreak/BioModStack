from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingRecord, ConformationalMappingRequest, Job
from routers.conformational_mapping import SubmitRequest, _principal, _registered_source_format
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


def test_cm_application_principal_requires_server_authenticated_proxy_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BMS_CM_OPERATOR_TOKEN", raising=False)
    monkeypatch.delenv("BMS_CM_TRUSTED_PROXY_SECRET", raising=False)
    with pytest.raises(HTTPException) as denied:
        _principal(_http_request(client_host="127.0.0.1"))
    assert denied.value.status_code == 401

    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "server-only-proxy-secret")
    request = _http_request(
        client_host="127.0.0.1",
        headers={"X-BMS-CM-Proxy-Secret": "server-only-proxy-secret"},
    )
    assert _principal(request) == "local-application-operator"


def test_cm_application_principal_ignores_unverifiable_tailscale_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BMS_CM_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "server-only-proxy-secret")
    base_headers = {
        "X-BMS-CM-Proxy-Secret": "server-only-proxy-secret",
        "Tailscale-User-Login": "Christian@Example.COM",
    }
    request = _http_request(client_host="::1", headers=base_headers)
    assert _principal(request) == "local-application-operator"


def test_cm_application_principal_uses_server_proof_after_forwarded_client_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BMS_CM_OPERATOR_TOKEN", raising=False)
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "server-only-proxy-secret")
    forwarded = _http_request(
        client_host="100.64.0.12",
        headers={"X-BMS-CM-Proxy-Secret": "server-only-proxy-secret"},
    )
    assert _principal(forwarded) == "local-application-operator"
    for client_host, headers in (
        ("127.0.0.1", {"X-BMS-CM-Proxy-Secret": "wrong"}),
        ("127.0.0.1", {"Tailscale-User-Login": "forged@example.com"}),
    ):
        with pytest.raises(HTTPException) as denied:
            _principal(_http_request(client_host=client_host, headers=headers))
        assert denied.value.status_code == 401


def test_cm_proxy_contract_strips_browser_operator_token_and_injects_server_secret() -> None:
    root = Path(__file__).resolve().parents[3]
    nginx = (root / "docker/web/nginx.conf").read_text(encoding="utf-8")
    compose = (root / "compose.core-runtime.yml").read_text(encoding="utf-8")
    assert 'proxy_set_header X-BMS-CM-Operator-Token "";' in nginx
    assert 'proxy_set_header Tailscale-User-Login "";' in nginx
    assert 'proxy_set_header X-BMS-CM-Proxy-Secret "${BMS_CM_TRUSTED_PROXY_SECRET}";' in nginx
    assert compose.count("      BMS_CM_TRUSTED_PROXY_SECRET:") == 2


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
