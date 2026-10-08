from __future__ import annotations

import json
import inspect
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import routers.conformational_mapping as cm_router
from services.conformational_mapping import global_adapter as cm_global_adapter
from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingRecord, ConformationalMappingRequest, ConformationalMappingSource, Job
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
    principal: str | None = None,
) -> Request:
    request = Request({
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
    if principal:
        request.state.authenticated_principal = {
            "subject": principal,
            "roles": ["scientist"],
        }
    return request


def test_registered_source_format_is_server_normalized() -> None:
    assert _registered_source_format("source/content.cif") == "mmcif"
    assert _registered_source_format("source/content.MMCIF") == "mmcif"
    assert _registered_source_format("legacy/content.pdb") == "pdb"


def test_cm_principal_requires_authentication_or_trusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_CM_TRUSTED_PROXY_SECRET", raising=False)
    with pytest.raises(HTTPException, match="authenticated conformational-mapping principal"):
        _principal(_http_request(client_host="127.0.0.1"))
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "trusted")
    assert _principal(
        _http_request(
            client_host="100.64.0.12",
            headers={"X-BMS-CM-Proxy-Secret": "trusted"},
        )
    ) == "local-application-operator"
    assert _principal(_http_request(client_host="127.0.0.1", principal="alice")) == "alice"


def test_cm_mutations_require_an_authenticated_principal() -> None:
    with pytest.raises(HTTPException, match="authenticated conformational-mapping principal"):
        _mutation_principal(_http_request(client_host="127.0.0.1"))
    assert _mutation_principal(_http_request(client_host="127.0.0.1", principal="alice")) == "alice"


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
async def test_retry_rejects_missing_authority_without_mutating_terminal_job(monkeypatch) -> None:
    record = SimpleNamespace(
        request_id="request-1", status="failed", job_id="job-1", backend="external_import",
        request_json={}, coordinate_plan_json={}, request_sha256="a" * 64,
        coordinate_plan_sha256="b" * 64,
    )
    job = SimpleNamespace(
        id="job-1", status="failed", queue_status="failed", error_message="failed",
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(), nextflow_run_id="123",
        retry_count=1, max_retries=2, params={"cm_request_path": "/results/request.json"},
    )

    async def authorized(*_args, **_kwargs):
        return record

    class Session:
        async def get(self, *_args, **_kwargs):
            return job

        async def commit(self):
            raise AssertionError("invalid retry authority must not commit")

    monkeypatch.setattr(cm_router, "_authorized_record", authorized)
    with pytest.raises(HTTPException, match="persisted CM retry authority") as raised:
        await retry_request("request-1", _http_request(client_host="127.0.0.1"), Session())
    assert raised.value.status_code == 409
    assert job.status == "failed"
    assert job.nextflow_run_id == "123"
    assert record.status == "failed"


@pytest.mark.asyncio
async def test_completed_request_does_not_project_historical_failure_as_current(monkeypatch) -> None:
    record = SimpleNamespace(
        request_id="request-1", job_id="job-1", backend="confornets",
        status="completed", progress_json={"phase": "completed"},
        failure_receipt_json={"terminal_state": "failed", "message": "historical"},
        result_contract_id="conformational_mapping_confornets_v1",
        request_json={"run_record": {"name": "Map", "notes": "Keep context", "selected_input": {}}},
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
    assert result["run_record"]["notes"] == "Keep context"


def test_cm11_api_typed_submission_rejects_unknown_fields() -> None:
    SubmitRequest.model_validate(_body())
    with pytest.raises(ValidationError):
        SubmitRequest.model_validate({**_body(), "server_path": "/tmp/input.pdb"})


def test_cm_run_notes_are_typed_and_bounded() -> None:
    body = SubmitRequest.model_validate({**_body(), "notes": "Compare open and closed states."})
    assert body.notes == "Compare open and closed states."

    with pytest.raises(ValidationError):
        SubmitRequest.model_validate({**_body(), "notes": "x" * 4001})


def test_cm_run_selection_rejects_client_owned_authority() -> None:
    with pytest.raises(ValidationError):
        SubmitRequest.model_validate({**_body(), "selected_model_id": "1"})
    with pytest.raises(ValidationError):
        SubmitRequest.model_validate({**_body(), "selected_chain_ids": ["A"]})
    assert cm_router._confornets_submission_policy() == {
        "chain_id": "A",
        "test_case_id": "bms-canonical-monomer",
        "benchmark_name": "biomodstack",
    }
    assert cm_router._bind_confornets_submission_policy({"task": "diversity"}) == {
        "task": "diversity",
        **cm_router._confornets_submission_policy(),
    }
    for field in ("chain_id", "test_case_id", "benchmark_name"):
        with pytest.raises(HTTPException, match="server-owned ConforNets fields"):
            cm_router._bind_confornets_submission_policy({field: "caller-value"})
    assert cm_router._bind_runtime_policy(
        "protenix_v2_ensemble", {"use_default_params": False, "n_cycle": 12, "n_step": 240}
    ) == {"use_default_params": False, "n_cycle": 12, "n_step": 240}
    for backend in ("confornets", "external_import"):
        assert cm_router._bind_runtime_policy(backend, {"use_default_params": True}) == {
            "use_default_params": True
        }
        with pytest.raises(HTTPException, match="supported only by Protenix"):
            cm_router._bind_runtime_policy(
                backend, {"use_default_params": False, "n_cycle": 12, "n_step": 240}
            )


def test_global_cm_adapter_uses_canonical_server_policy_binders() -> None:
    adapter_source = inspect.getsource(cm_global_adapter.materialize_preallocated_cm_job)
    assert "_bind_runtime_policy" in adapter_source
    assert "_bind_confornets_submission_policy" in adapter_source
    assert "_bind_analysis_policy" in adapter_source
    assert "_managed_checkpoint_for_submission" in adapter_source


def test_cm_analysis_policy_is_server_owned() -> None:
    canonical = cm_router._canonical_analysis_policy()
    assert cm_router._bind_analysis_policy(canonical) == canonical
    caller_override = {**canonical, "inner_support_minimum": 0.7}
    with pytest.raises(HTTPException, match="server-owned analysis policy"):
        cm_router._bind_analysis_policy(caller_override)


def test_cm_source_registration_reserves_server_receipt_metadata() -> None:
    for metadata in (
        {"resolved_chain_ids": ["A"]},
        {"normalization_receipt": {}},
        {"provider_receipt": {}},
    ):
        with pytest.raises(HTTPException, match="server source receipts are server-owned"):
            cm_router._reject_reserved_source_metadata(metadata)


def test_cm_checkpoint_upload_is_rejected() -> None:
    with pytest.raises(HTTPException, match="server-managed"):
        cm_router._validate_upload_source_kind("confornets_checkpoint")


@pytest.mark.asyncio
async def test_cm_submission_accepts_only_the_installed_managed_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = SimpleNamespace(source_id="cm_src_server_confornets_checkpoint_approved")

    async def ensure(_session):
        return managed

    monkeypatch.setattr(cm_router, "_ensure_managed_confornets_checkpoint", ensure)
    assert await cm_router._managed_checkpoint_for_submission(
        SimpleNamespace(), managed.source_id
    ) is managed
    with pytest.raises(HTTPException, match="installed managed checkpoint"):
        await cm_router._managed_checkpoint_for_submission(
            SimpleNamespace(), "cm_src_caller_checkpoint"
        )


def test_cm_run_record_trusts_only_content_bound_provider_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cm_router, "get_data_root", lambda: tmp_path)
    source = SimpleNamespace(
        source_id="source",
        source_kind="structure_upload",
        content_sha256="a" * 64,
        metadata_json={
            "name": "RCSB 1UBQ",
            "provider_receipt": {
                "provider": "RCSB",
                "accession": "FAKE",
                "content_sha256": "a" * 64,
            },
        },
    )
    selected = cm_router._run_record_selected_input(source, model_id=None, sample_id=None, chain_ids=[])  # type: ignore[arg-type]
    assert "provider" not in selected
    assert "accession" not in selected

    receipt = cm_router._publish_source_authority(
        source,  # type: ignore[arg-type]
        authority_kind="rcsb_download",
        payload={"provider": "RCSB", "accession": "1UBQ"},
    )
    selected = cm_router._run_record_selected_input(source, model_id=None, sample_id=None, chain_ids=[])  # type: ignore[arg-type]
    assert selected["provider"] == "RCSB"
    assert selected["accession"] == "1UBQ"

    receipt_path = cm_router._source_authority_path("source")
    tampered = {**receipt, "payload": {"provider": "RCSB", "accession": "2XYZ"}}
    receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
    selected = cm_router._run_record_selected_input(source, model_id=None, sample_id=None, chain_ids=[])  # type: ignore[arg-type]
    assert "provider" not in selected
    assert "accession" not in selected


@pytest.mark.asyncio
async def test_cm_source_lookup_rejects_cross_principal_source() -> None:
    source = SimpleNamespace(
        principal_id="other-principal",
        source_kind="structure_upload",
        immutable=True,
    )
    session = SimpleNamespace(get=lambda *_args: None)

    async def get(*_args):
        return source

    session.get = get
    with pytest.raises(HTTPException, match="registered source is unavailable"):
        await cm_router._source(session, "foreign", "owner", {"structure_upload"})


@pytest.mark.asyncio
async def test_cm_source_preview_returns_verified_registered_bytes(tmp_path: Path) -> None:
    payload = b"data_preview\n#\n"
    source_path = tmp_path / "source.cif"
    source_path.write_bytes(payload)
    source = SimpleNamespace(
        source_id="preview-source",
        principal_id=cm_router._PERSONAL_WORKFLOW_PRINCIPAL,
        source_kind="structure_upload",
        immutable=True,
        storage_root=str(tmp_path),
        relative_path="source.cif",
        content_sha256=__import__("hashlib").sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )

    class SourceSession:
        async def get(self, model, source_id):
            assert model is ConformationalMappingSource
            assert source_id == source.source_id
            return source

    response = await cm_router.source_content(
        source.source_id,
        _http_request(
            client_host="127.0.0.1",
            principal=cm_router._PERSONAL_WORKFLOW_PRINCIPAL,
        ),
        SourceSession(),  # type: ignore[arg-type]
    )
    assert response.body == payload
    assert response.media_type == "chemical/x-mmcif"
    assert response.headers["etag"] == f'"sha256:{source.content_sha256}"'


@pytest.mark.asyncio
async def test_cm_request_lookup_rejects_cross_principal_record(monkeypatch) -> None:
    async def get_foreign(*_args):
        return SimpleNamespace(principal_id="other-principal", progress_json={})

    monkeypatch.setattr(cm_router, "get_request", get_foreign)
    with pytest.raises(HTTPException, match="request not found"):
        await cm_router._authorized_record(
            "foreign-request",
            _http_request(client_host="127.0.0.1", principal="alice"),
            SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_cm_source_listing_is_principal_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cm_router, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(cm_router, "get_weights_root", lambda: tmp_path / "missing-weights")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sources.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    request = _http_request(client_host="127.0.0.1", principal="alice")
    expected_principal = _principal(request)
    async with factory() as session:
        session.add_all([
            ConformationalMappingSource(
                source_id="owned", principal_id=expected_principal, source_kind="structure_upload",
                storage_root=str(tmp_path), relative_path="owned.cif", content_sha256="a" * 64,
                size_bytes=1, metadata_json={
                    "name": "owned",
                    "managed": True,
                    "asset_id": "confornets.of3p2.checkpoint",
                    "provider_receipt": {"provider": "RCSB", "accession": "1UBQ", "content_sha256": "a" * 64},
                }, immutable=True,
            ),
            ConformationalMappingSource(
                source_id="foreign", principal_id="other-principal", source_kind="structure_upload",
                storage_root=str(tmp_path), relative_path="foreign.cif", content_sha256="b" * 64,
                size_bytes=1, metadata_json={"name": "foreign"}, immutable=True,
            ),
        ])
        await session.commit()
        result = await cm_router.list_sources(request, session)
    assert [source["source_id"] for source in result["sources"]] == ["owned"]
    assert result["sources"][0]["authority_receipt"] is None
    assert result["sources"][0]["managed_checkpoint"] is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_cm_source_listing_provisions_installed_managed_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weights_root = tmp_path / "weights"
    checkpoint = weights_root / "openfold3" / "of3-p2-155k.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"managed-checkpoint")
    monkeypatch.setattr(cm_router, "get_weights_root", lambda: weights_root)
    monkeypatch.setattr(cm_router, "get_data_root", lambda: tmp_path / "data")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'managed.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        result = await cm_router.list_sources(
            _http_request(client_host="127.0.0.1", principal="alice"), session
        )
    checkpoint_sources = [
        source for source in result["sources"] if source["source_kind"] == "confornets_checkpoint"
    ]
    assert len(checkpoint_sources) == 1
    assert checkpoint_sources[0]["metadata"]["managed"] is True
    assert checkpoint_sources[0]["managed_checkpoint"] is True
    await engine.dispose()


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
