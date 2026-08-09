from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, ConformationalMappingArtifact, ConformationalMappingRequest, Job
from experiment_services import (
    WORKFLOW_ADAPTER_REGISTRY,
    DispatchFailure,
    ValidationFailure,
    _cm_submission_source_ids,
    _validate_workflow_payload,
)
from routers import conformational_mapping as cm
from services import job_control
from services.conformational_mapping import global_adapter
from services.conformational_mapping.persistence import issue_request_capability


def _request(*, principal: str | None = None, token: str | None = None, proxy: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    if proxy:
        headers.append((b"x-bms-cm-proxy-secret", proxy.encode()))
    request = Request({
        "type": "http", "method": "GET", "scheme": "http", "path": "/api/conformational-mapping",
        "query_string": b"", "headers": headers, "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000),
    })
    if principal:
        request.state.authenticated_principal = {"subject": principal, "roles": ["scientist"]}
    return request


def test_cm_principal_requires_effective_authentication_and_valid_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_CM_TRUSTED_PROXY_SECRET", "trusted-secret")
    with pytest.raises(HTTPException) as missing:
        cm._principal(_request())
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as wrong:
        cm._principal(_request(proxy="wrong"))
    assert wrong.value.status_code == 401
    assert cm._principal(_request(proxy="trusted-secret")) == "local-application-operator"
    assert cm._principal(_request(principal="alice")) == "alice"


@pytest.mark.asyncio
async def test_request_capability_is_enforced_for_non_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    token, digest = issue_request_capability()
    record = SimpleNamespace(principal_id="alice", progress_json={"capability_sha256": digest})

    async def get_request(_session, _request_id):
        return record

    monkeypatch.setattr(cm, "get_request", get_request)
    with pytest.raises(HTTPException) as denied:
        await cm._authorized_record("r", _request(principal="bob"), SimpleNamespace())
    assert denied.value.status_code == 404
    assert await cm._authorized_record("r", _request(principal="bob", token=token), SimpleNamespace()) is record
    with pytest.raises(HTTPException) as anonymous_mutation:
        await cm._authorized_record("r", _request(token=token), SimpleNamespace(), mutation=True)
    assert anonymous_mutation.value.status_code == 401
    with pytest.raises(HTTPException) as foreign_mutation:
        await cm._authorized_record(
            "r", _request(principal="bob", token=token), SimpleNamespace(), mutation=True,
        )
    assert foreign_mutation.value.status_code == 404
    assert await cm._authorized_record(
        "r", _request(principal="alice"), SimpleNamespace(), mutation=True,
    ) is record


def test_upload_cannot_impersonate_a_prior_run_artifact() -> None:
    with pytest.raises(HTTPException, match="run artifact"):
        cm._validate_upload_source_kind("structure_artifact")


def test_external_import_contract_is_singular() -> None:
    assert cm.SubmitRequest.model_fields["registered_artifact_ids"].metadata
    with pytest.raises(Exception):
        cm.SubmitRequest.model_validate({
            "name": "plural import", "backend": "external_import",
            "registered_artifact_ids": ["a", "b"], "ordered_seeds": [0], "samples_per_seed": 1,
            "feature_policy": {"mode": "features_disabled_control_v1"},
            "runtime_policy": {"use_default_params": True},
            "analysis_policy": cm._canonical_analysis_policy(),
        })


def test_registered_global_cm_adapters_equal_executable_materializers() -> None:
    expected = {
        "bms.cm.protenix_v2.adapter.v1",
        "bms.cm.confornets.adapter.v1",
    }
    assert set(global_adapter.EXECUTABLE_CM_ADAPTERS) == expected
    assert WORKFLOW_ADAPTER_REGISTRY["conformational_mapping"] == expected


def _global_cm_payload(submission: dict[str, object], receipt_ids: list[str]) -> dict[str, object]:
    backend = str(submission["backend"])
    adapter = (
        "bms.cm.protenix_v2.adapter.v1"
        if backend == "protenix_v2_ensemble"
        else "bms.cm.confornets.adapter.v1"
    )
    return {
        "schema": "bms.experiment.workflow.v1",
        "workflow_family": "conformational_mapping",
        "contract_version": "1",
        "adapter_id": adapter,
        "stage": "protenix_v2_sampling" if backend == "protenix_v2_ensemble" else "confornets_sampling",
        "backend": backend,
        "source_receipt_ids": receipt_ids,
        "expected_cardinality": 1,
        "nodes": [{"id": "generate", "kind": "generator"}],
        "edges": [],
        "scheduler": {
            "name": "global CM", "model_id": "conformational_mapping", "mode": "map",
            "params": {"workflow_adapter": adapter, "cm_submission": submission},
        },
    }


def test_global_cm_source_receipts_exactly_bind_submitted_sources() -> None:
    submission: dict[str, object] = {
        "backend": "confornets",
        "registered_sequence_id": "sequence",
        "registered_checkpoint_id": "checkpoint",
        "registered_reference_ids": ["reference-a", "reference-b"],
        "registered_config_id": "config",
        "registered_transfer_id": "transfer",
    }
    expected = ["sequence", "checkpoint", "reference-a", "reference-b", "config", "transfer"]
    assert _cm_submission_source_ids(submission) == expected
    _validate_workflow_payload(_global_cm_payload(submission, expected))
    with pytest.raises(ValidationFailure, match="do not bind"):
        _validate_workflow_payload(_global_cm_payload(submission, ["sequence", "checkpoint"]))


@pytest.mark.asyncio
async def test_global_cm_materializer_rolls_back_core_state_and_removes_failed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(global_adapter, "get_results_dir", lambda: tmp_path)

    async def fail_after_flush(session: AsyncSession, **_: object) -> dict[str, object]:
        root = tmp_path / "conformational_mapping_attempt"
        root.mkdir(parents=True)
        (root / "partial").write_text("partial", encoding="utf-8")
        session.add(Job(id="attempt", name="partial", status="queued", model_id="conformational_mapping", mode="map", params={}))
        await session.flush()
        raise DispatchFailure("injected materialization failure")

    monkeypatch.setattr(global_adapter, "_materialize_preallocated_cm_job", fail_after_flush)
    async with factory() as session:
        with pytest.raises(DispatchFailure, match="injected"):
            await global_adapter.materialize_preallocated_cm_job(
                session, attempt_id="attempt", scheduler={}, run_group_id="group",
            )
        assert (await session.execute(select(Job).where(Job.id == "attempt"))).scalar_one_or_none() is None
    assert not (tmp_path / "conformational_mapping_attempt").exists()
    await engine.dispose()


def test_confornets_snapshot_identity_binds_full_normalized_snapshot() -> None:
    base = {
        "schema_name": "cm_complex_snapshot", "schema_version": 1, "target_id": "target", "target_order": 0,
        "original_source_path": "registered/sequence", "original_source_sha256": "a" * 64,
        "entities": [{"entity_type": "protein", "source_entity_id": "protein", "count": 1,
                      "ordered_instance_ids": ["A"], "sequence": "ACDE"}],
        "bonds": [], "instance_mappings": [],
        "admission": {"token_count": 4, "atom_count": 0, "token_limit": 10000, "conversion_omissions": []},
        "unsupported_fields": [],
    }
    first = global_adapter._seal_confornets_snapshot(base)
    changed = json.loads(json.dumps(base))
    changed["entities"][0]["sequence"] = "ACDF"
    second = global_adapter._seal_confornets_snapshot(changed)
    assert first["normalized_source_sha256"] != second["normalized_source_sha256"]


@pytest.mark.asyncio
async def test_your_runs_lists_only_owned_completed_verified_reusable_artifacts(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runs.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    good = tmp_path / "good.cif"
    good.write_bytes(b"data_good\n#\n")
    stale = tmp_path / "stale.cif"
    stale.write_bytes(b"different")
    async with factory() as session:
        for request_id, principal, request_status, job_status, path, digest in (
            ("owned", "alice", "completed", "completed", good, hashlib.sha256(good.read_bytes()).hexdigest()),
            ("foreign", "bob", "completed", "completed", good, hashlib.sha256(good.read_bytes()).hexdigest()),
            ("running", "alice", "running", "running", good, hashlib.sha256(good.read_bytes()).hexdigest()),
            ("stale", "alice", "completed", "completed", stale, "0" * 64),
        ):
            session.add(Job(id=f"job-{request_id}", name=request_id, status=job_status, model_id="conformational_mapping", mode="map", params={}, output_dir=str(tmp_path)))
            await session.flush()
            session.add(ConformationalMappingRequest(
                request_id=request_id, job_id=f"job-{request_id}", principal_id=principal, backend="external_import",
                status=request_status, request_sha256=hashlib.sha256(request_id.encode()).hexdigest(), coordinate_plan_sha256="b" * 64,
                resume_key="c" * 64, result_contract_id="conformational_mapping_import_v1", request_json={},
                coordinate_plan_json={}, progress_json={},
            ))
            await session.flush()
            session.add(ConformationalMappingArtifact(
                artifact_id=f"artifact-{request_id}", request_id=request_id, candidate_id="candidate", role="authoritative_cif",
                relative_path=path.name, storage_path=str(path), content_sha256=digest, size_bytes=path.stat().st_size,
                media_type="chemical/x-mmcif", metadata_json={"backend_coordinates": {
                    "backend": "external_import", "target_id": "target", "staged_index": 0,
                    "source_content_sha256": digest, "staged_receipt_sha256": "d" * 64,
                }},
            ))
        await session.commit()
        listed = await cm.list_reusable_runs(_request(principal="alice"), session)
        assert [item["request_id"] for item in listed["runs"]] == ["owned"]
        artifact = listed["runs"][0]["artifacts"][0]
        assert artifact["available"] is True
        assert artifact["role"] == "authoritative_cif"
        assert artifact["media_type"] == "chemical/x-mmcif"
        assert artifact["backend_coordinates"]["staged_index"] == 0
    await engine.dispose()


def test_retry_launch_params_never_reuse_resume_work_dir() -> None:
    params = {"cm_request_path": "/trusted/cm_request_v1.json", "resume_work_dir": "/untrusted/work", "gpu_id": 1}
    cleaned = cm._clean_retry_launch_params(params, attempt_root=Path("/fresh/attempt"))
    assert "resume_work_dir" not in cleaned
    assert cleaned["cm_request_path"] == "/fresh/attempt/cm_request_v1.json"


@pytest.mark.asyncio
async def test_typed_cancellation_persists_joint_intent_before_external_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = SimpleNamespace(
        id="job", parent_job_id=None, status="running", queue_status="running",
        params={}, nextflow_run_id="run", paused=False, assigned_gpu=0,
        awaiting_input=False, awaiting_stage=None, awaiting_payload={}, retry_count=0,
        current_stage="inference", stage_progress=0.5, completed_at=None, error_message=None,
    )
    typed = {"status": "running", "phase": "running"}
    commits: list[tuple[str, str, str]] = []

    class Session:
        async def flush(self) -> None:
            return None

        async def commit(self) -> None:
            commits.append((job.status, job.queue_status, typed["phase"]))

    async def load_lineage(_session: object, _job_id: str):
        return job, [job], {"job": 0}

    async def stop(_run_id: str) -> bool:
        assert commits == [("running", "cancelling", "cancellation_requested")]
        return True

    async def mark_intent() -> None:
        typed["phase"] = "cancellation_requested"

    async def mark_terminal() -> None:
        typed.update({"status": "cancelled", "phase": "cancelled"})

    monkeypatch.setattr(job_control, "_load_job_lineage", load_lineage)
    monkeypatch.setattr(job_control, "cancel_nextflow_job", stop)
    await job_control.cancel_job_lineage(
        "job", Session(),  # type: ignore[arg-type]
        commit=True,
        before_intent_commit=mark_intent,
        before_terminal_commit=mark_terminal,
    )
    assert commits == [
        ("running", "cancelling", "cancellation_requested"),
        ("cancelled", "cancelled", "cancelled"),
    ]
