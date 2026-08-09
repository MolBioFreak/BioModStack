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


def test_global_cm_outer_adapter_rejects_nested_adapter_substitution_without_mutation() -> None:
    submission: dict[str, object] = {
        "backend": "protenix_v2_ensemble",
        "registered_snapshot_id": "snapshot",
    }
    payload = _global_cm_payload(submission, ["snapshot"])
    scheduler = payload["scheduler"]
    assert isinstance(scheduler, dict)
    params = scheduler["params"]
    assert isinstance(params, dict)
    params["workflow_adapter"] = "bms.cm.confornets.adapter.v1"
    submitted_payload = json.loads(json.dumps(payload))

    with pytest.raises(ValidationFailure, match="nested workflow adapter"):
        _validate_workflow_payload(payload)

    assert payload == submitted_payload


def test_global_cm_outer_backend_rejects_nested_submission_substitution_without_mutation() -> None:
    submission: dict[str, object] = {
        "backend": "protenix_v2_ensemble",
        "registered_snapshot_id": "snapshot",
    }
    payload = _global_cm_payload(submission, ["snapshot"])
    scheduler = payload["scheduler"]
    assert isinstance(scheduler, dict)
    params = scheduler["params"]
    assert isinstance(params, dict)
    nested_submission = params["cm_submission"]
    assert isinstance(nested_submission, dict)
    nested_submission["backend"] = "confornets"
    submitted_payload = json.loads(json.dumps(payload))

    with pytest.raises(ValidationFailure, match="nested submission backend"):
        _validate_workflow_payload(payload)

    assert payload == submitted_payload


def _recovery_scheduler() -> dict[str, object]:
    return {
        "name": "global CM",
        "model_id": "conformational_mapping",
        "mode": "map",
        "params": {
            "workflow_adapter": "bms.cm.protenix_v2.adapter.v1",
            "cm_source_receipt_ids": ["snapshot"],
            "cm_submission": {
                "name": "authoritative submission",
                "backend": "protenix_v2_ensemble",
                "registered_snapshot_id": "snapshot",
                "ordered_seeds": [101],
            },
        },
    }


_RECOVERY_ATTEMPT_ID = "00000000-0000-4000-8000-00000000c001"


def _recovery_request_params() -> dict[str, object]:
    return {
        "backend": "protenix_v2_ensemble",
        "targets": [{"target_id": "target-a", "target_order": 0}],
        "ordered_seeds": [101],
        "samples_per_seed": 1,
        "feature_policy": {
            "mode": "regenerate_mutated_protein_v1",
            "protein_msa_enabled": True,
            "templates_enabled": True,
            "rna_msa_enabled": True,
        },
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {
            "sign_zero_epsilon": 0.000001,
            "clash_detector_id": "bms_clash",
            "clash_detector_version": "1",
            "outer_support_minimum": 0.8,
            "inner_support_minimum": 0.6,
            "sign_consistency_minimum": 0.8,
            "clash_free_minimum": 0.9,
            "rank_stability_minimum": 0.6,
            "minimum_common_ranked_universe_size": 3,
        },
        "protenix_snapshot_id": "snapshot",
    }


async def _persist_recoverable_cm_attempt(
    session: AsyncSession,
    *,
    scheduler: dict[str, object],
    results_root: Path,
    attempt_id: str = _RECOVERY_ATTEMPT_ID,
    run_group_id: str = "group",
) -> tuple[Job, ConformationalMappingRequest]:
    params = scheduler["params"]
    assert isinstance(params, dict)
    submission = params["cm_submission"]
    assert isinstance(submission, dict)
    output_root = results_root / f"conformational_mapping_{attempt_id}"
    materialized = global_adapter.materialize_trusted_internal_request(
        _recovery_request_params(),
        output_dir=output_root,
        request_id=attempt_id,
        principal_id=global_adapter._PERSONAL_WORKFLOW_PRINCIPAL,
    )
    request_json = json.loads(materialized.request_path.read_text(encoding="utf-8"))
    coordinate_plan_json = json.loads(
        materialized.coordinate_plan_path.read_text(encoding="utf-8")
    )
    request_sha256 = request_json["request_sha256"]
    coordinate_plan_sha256 = coordinate_plan_json["coordinate_plan_sha256"]
    job = Job(
        id=attempt_id,
        name=str(scheduler["name"]),
        status="queued",
        model_id="conformational_mapping",
        mode="map",
        params={"cm_request_path": str(materialized.request_path)},
        output_dir=str(output_root),
        batch_id=run_group_id,
        lineage_root_job_id=attempt_id,
        stage_family="conformational_mapping",
        stage_mode="protenix_v2_ensemble",
        provenance={
            "cm_request_sha256": request_sha256,
            "cm_coordinate_plan_sha256": coordinate_plan_sha256,
            "cm_principal_id": global_adapter._PERSONAL_WORKFLOW_PRINCIPAL,
            "cm_workflow_adapter": params["workflow_adapter"],
            "cm_scheduler_sha256": global_adapter.sha256_text(global_adapter.canonical_json(scheduler)),
            "cm_submission_sha256": global_adapter.sha256_text(global_adapter.canonical_json(submission)),
            "global_run_group_id": run_group_id,
            "global_attempt_id": attempt_id,
        },
    )
    session.add(job)
    await session.flush()
    request = ConformationalMappingRequest(
        request_id=attempt_id,
        job_id=attempt_id,
        principal_id=global_adapter._PERSONAL_WORKFLOW_PRINCIPAL,
        backend="protenix_v2_ensemble",
        status="queued",
        request_sha256=request_sha256,
        coordinate_plan_sha256=coordinate_plan_sha256,
        resume_key="0" * 64,
        result_contract_id="conformational_mapping_ensemble_v1",
        request_json=request_json,
        coordinate_plan_json=coordinate_plan_json,
        progress_json={},
    )
    session.add(request)
    await session.commit()
    return job, request


@pytest.mark.asyncio
async def test_global_cm_existing_attempt_recovery_accepts_exact_authoritative_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery-exact.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        scheduler = _recovery_scheduler()
        submitted_scheduler = json.loads(json.dumps(scheduler))
        results_root = tmp_path / "results"
        monkeypatch.setattr(global_adapter, "get_results_dir", lambda: results_root)
        async with factory() as session:
            await _persist_recoverable_cm_attempt(
                session, scheduler=scheduler, results_root=results_root,
            )
            receipt = await global_adapter.materialize_preallocated_cm_job(
                session,
                attempt_id=_RECOVERY_ATTEMPT_ID,
                scheduler=scheduler,
                run_group_id="group",
            )

        assert receipt["recovered_existing"] is True
        assert receipt["scheduler_job_id"] == _RECOVERY_ATTEMPT_ID
        assert scheduler == submitted_scheduler
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "substitution",
    ["scheduler_model", "scheduler_mode", "submission", "backend", "run_group"],
)
async def test_global_cm_existing_attempt_recovery_rejects_incoming_authority_substitution(
    tmp_path: Path,
    substitution: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'recovery-{substitution}.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scheduler = _recovery_scheduler()
    results_root = tmp_path / "results"
    monkeypatch.setattr(global_adapter, "get_results_dir", lambda: results_root)
    async with factory() as session:
        await _persist_recoverable_cm_attempt(
            session, scheduler=scheduler, results_root=results_root,
        )
        incoming = json.loads(json.dumps(scheduler))
        run_group_id = "group"
        params = incoming["params"]
        assert isinstance(params, dict)
        submission = params["cm_submission"]
        assert isinstance(submission, dict)
        if substitution == "scheduler_model":
            incoming["model_id"] = "other_model"
        elif substitution == "scheduler_mode":
            incoming["mode"] = "other_mode"
        elif substitution == "submission":
            submission["ordered_seeds"] = [202]
        elif substitution == "backend":
            params["workflow_adapter"] = "bms.cm.confornets.adapter.v1"
            params["cm_source_receipt_ids"] = ["sequence", "checkpoint"]
            params["cm_submission"] = {
                "name": "substituted submission",
                "backend": "confornets",
                "registered_sequence_id": "sequence",
                "registered_checkpoint_id": "checkpoint",
                "registered_reference_ids": [],
                "ordered_seeds": [101],
            }
        else:
            run_group_id = "other-group"

        with pytest.raises(DispatchFailure, match="recovery authority"):
            await global_adapter.materialize_preallocated_cm_job(
                session,
                attempt_id=_RECOVERY_ATTEMPT_ID,
                scheduler=incoming,
                run_group_id=run_group_id,
            )

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "persisted_substitution",
    ["attempt", "scheduler_digest", "submission_digest", "request_digest", "coordinate_digest"],
)
async def test_global_cm_existing_attempt_recovery_rejects_inconsistent_persisted_provenance(
    tmp_path: Path,
    persisted_substitution: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'provenance-{persisted_substitution}.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scheduler = _recovery_scheduler()
    results_root = tmp_path / "results"
    monkeypatch.setattr(global_adapter, "get_results_dir", lambda: results_root)
    async with factory() as session:
        job, request = await _persist_recoverable_cm_attempt(
            session, scheduler=scheduler, results_root=results_root,
        )
        provenance = dict(job.provenance)
        if persisted_substitution == "attempt":
            provenance["global_attempt_id"] = "other-attempt"
        elif persisted_substitution == "scheduler_digest":
            provenance["cm_scheduler_sha256"] = "1" * 64
        elif persisted_substitution == "submission_digest":
            provenance["cm_submission_sha256"] = "2" * 64
        elif persisted_substitution == "request_digest":
            provenance["cm_request_sha256"] = "3" * 64
        else:
            request.coordinate_plan_sha256 = "4" * 64
        job.provenance = provenance
        await session.commit()

        with pytest.raises(DispatchFailure, match="recovery authority"):
            await global_adapter.materialize_preallocated_cm_job(
                session,
                attempt_id=_RECOVERY_ATTEMPT_ID,
                scheduler=scheduler,
                run_group_id="group",
            )

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "substitution",
    [
        "missing_params",
        "extra_params",
        "traversal_request_path",
        "alternate_request_path",
        "nonexistent_request_path",
        "substituted_output_dir",
        "equivalent_output_dir",
        "missing_request_file",
        "missing_coordinate_plan_file",
    ],
)
async def test_global_cm_existing_attempt_recovery_rejects_nonexecutable_job_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    substitution: str,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'job-{substitution}.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scheduler = _recovery_scheduler()
    results_root = tmp_path / "results"
    output_root = results_root / f"conformational_mapping_{_RECOVERY_ATTEMPT_ID}"
    request_path = output_root / "cm_request_v1.json"
    plan_path = output_root / "cm_coordinate_plan_v1.json"
    monkeypatch.setattr(global_adapter, "get_results_dir", lambda: results_root)

    async with factory() as session:
        job, _ = await _persist_recoverable_cm_attempt(
            session, scheduler=scheduler, results_root=results_root,
        )
        if substitution == "missing_params":
            job.params = {}
        elif substitution == "extra_params":
            job.params = {"cm_request_path": str(request_path), "gpu_id": 0}
        elif substitution == "traversal_request_path":
            job.params = {"cm_request_path": f"{output_root}/nested/../cm_request_v1.json"}
        elif substitution == "alternate_request_path":
            arbitrary = tmp_path / "fixture" / "cm_request_v1.json"
            arbitrary.parent.mkdir()
            arbitrary.write_bytes(request_path.read_bytes())
            job.params = {"cm_request_path": str(arbitrary)}
        elif substitution == "nonexistent_request_path":
            job.params = {"cm_request_path": str(tmp_path / "missing" / "cm_request_v1.json")}
        elif substitution == "substituted_output_dir":
            job.output_dir = str(tmp_path / "other-output")
        elif substitution == "equivalent_output_dir":
            job.output_dir = f"{output_root}/."
        elif substitution == "missing_request_file":
            request_path.unlink()
        else:
            plan_path.unlink()
        await session.commit()

        with pytest.raises(DispatchFailure, match="executable authority"):
            await global_adapter.materialize_preallocated_cm_job(
                session,
                attempt_id=_RECOVERY_ATTEMPT_ID,
                scheduler=scheduler,
                run_group_id="group",
            )

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("document", ["request", "coordinate_plan"])
async def test_global_cm_existing_attempt_recovery_rejects_schema_invalid_resigned_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document: str,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'schema-{document}.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    scheduler = _recovery_scheduler()
    results_root = tmp_path / "results"
    output_root = results_root / f"conformational_mapping_{_RECOVERY_ATTEMPT_ID}"
    monkeypatch.setattr(global_adapter, "get_results_dir", lambda: results_root)

    async with factory() as session:
        job, record = await _persist_recoverable_cm_attempt(
            session, scheduler=scheduler, results_root=results_root,
        )
        request_json = dict(record.request_json)
        coordinate_plan_json = dict(record.coordinate_plan_json)
        if document == "request":
            request_json["unexpected"] = "fully-resigned"
            request_json["request_sha256"] = global_adapter.canonical_sha256({
                key: value for key, value in request_json.items() if key != "request_sha256"
            })
            coordinate_plan_json["request_sha256"] = request_json["request_sha256"]
        else:
            coordinate_plan_json["unexpected"] = "fully-resigned"
        coordinate_plan_json["coordinate_plan_sha256"] = global_adapter.canonical_sha256({
            key: value
            for key, value in coordinate_plan_json.items()
            if key != "coordinate_plan_sha256"
        })
        record.request_json = request_json
        record.coordinate_plan_json = coordinate_plan_json
        record.request_sha256 = request_json["request_sha256"]
        record.coordinate_plan_sha256 = coordinate_plan_json["coordinate_plan_sha256"]
        provenance = dict(job.provenance)
        provenance["cm_request_sha256"] = record.request_sha256
        provenance["cm_coordinate_plan_sha256"] = record.coordinate_plan_sha256
        job.provenance = provenance
        (output_root / "cm_request_v1.json").write_text(
            json.dumps(request_json, sort_keys=True, separators=(",", ":")), encoding="utf-8",
        )
        (output_root / "cm_coordinate_plan_v1.json").write_text(
            json.dumps(coordinate_plan_json, sort_keys=True, separators=(",", ":")), encoding="utf-8",
        )
        await session.commit()

        with pytest.raises(DispatchFailure, match="schema-valid"):
            await global_adapter.materialize_preallocated_cm_job(
                session,
                attempt_id=_RECOVERY_ATTEMPT_ID,
                scheduler=scheduler,
                run_group_id="group",
            )

    await engine.dispose()


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
