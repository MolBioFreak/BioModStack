"""ONT HTTP policy conversion with scratch SQLite, never a worker or lifespan."""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job, NgsPooledAssignmentRelease, NgsPooledAssignmentReleaseTarget
from routers import jobs, ont_runs
from schemas import ExecutionPolicy, JobCreate
from test_ont_pooled_reference_assignment import (
    pooled_context, _submit_request, _write_assignment_summary,
)

_REAL_CREATE = jobs.create_job


@pytest_asyncio.fixture
async def policy_context(pooled_context, monkeypatch):
    context = pooled_context
    monkeypatch.setattr(jobs, "create_job", _REAL_CREATE)
    monkeypatch.setattr(jobs, "get_results_dir", lambda: context.results_root)
    monkeypatch.setattr(jobs, "get_inputs_dir", lambda: context.inputs_root)
    monkeypatch.setattr(jobs, "get_data_root", lambda: context.inputs_root.parent)
    monkeypatch.setattr(jobs, "get_allowed_roots", lambda: {"inputs": context.inputs_root, "results": context.results_root})
    monkeypatch.setattr(ont_runs.ont_submission_trust, "get_inputs_dir", lambda: context.inputs_root)
    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    app.include_router(jobs.router, prefix="/api/jobs")
    app.include_router(ont_runs.barcode_router, prefix="/api/jobs")
    async def session():
        yield context.session
    for dependency in (ont_runs.get_session, ont_runs.get_experiment_session, ont_runs.get_molbio_ngs_session):
        app.dependency_overrides[dependency] = session
    pod5 = context.inputs_root / "pod5"
    pod5.mkdir()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as client:
        yield SimpleNamespace(**vars(context), client=client, app=app, pod5=pod5)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["omitted", None, {}, {"remote_result_policy": "automatic"}])
async def test_http_policy_is_persisted(policy_context, policy):
    context = policy_context
    payload = {"name": "policy regression", "params": {"pod5_dir": str(context.pod5)}}
    if policy != "omitted":
        payload["execution_policy"] = policy
    response = await context.client.post("/api/ont/ngs/ont_basecall_dna/submit", json=payload)
    assert response.status_code == 201, response.text
    expected = ExecutionPolicy.model_validate(policy or {}) if policy != "omitted" else JobCreate.model_fields["execution_policy"].default_factory()
    async with async_sessionmaker(context.engine)() as reader:
        row = await reader.get(Job, response.json()["id"])
        assert row is not None
        assert ExecutionPolicy.from_params(row.params).model_dump() == expected.model_dump()
        assert row.params["remote_result_policy"] == expected.remote_result_policy
        assert row.status == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_second_child", [False, True])
async def test_pooled_release_real_job_persistence_and_replay(policy_context, monkeypatch, fail_second_child):
    context = policy_context
    response = await context.client.post("/api/ont/ngs/pooled-reference-assignment/submit", json=_submit_request(context).model_dump(mode="json"))
    assert response.status_code == 201, response.text
    submitted = response.json()
    job_id = submitted["assignment_job_id"]
    assignment = await context.session.get(Job, job_id)
    assignment.status = "completed"  # Inert scientific completion fixture; no worker executes.
    await context.session.commit()
    _write_assignment_summary(context, submitted)
    for suffix in ("manifest", "targets"):
        read = await context.client.get(f"/api/jobs/{job_id}/pooled-assignment/{suffix}")
        assert read.status_code == 200, read.text
    async with async_sessionmaker(context.engine)() as reader:
        assert (await reader.execute(select(Job).where(Job.parent_job_id == job_id))).scalars().all() == []
        assert (await reader.execute(select(NgsPooledAssignmentRelease))).scalars().all() == []

    if fail_second_child:
        count = 0
        async def fail_after_insert(*args, **kwargs):
            nonlocal count
            created = await _REAL_CREATE(*args, **kwargs)
            count += 1
            if count == 2:
                raise RuntimeError("injected failure after second real child insertion")
            return created
        monkeypatch.setattr(jobs, "create_job", fail_after_insert)
    payload = {"idempotency_key": "explicit-policy-release", "target_workflow": "ont_plasmid_qc", "target_ids": ["target-a", "target-b"]}
    path = f"/api/jobs/{job_id}/pooled-assignment/release"
    response = await context.client.post(path, json=payload)
    if fail_second_child:
        assert response.status_code == 422, response.text
        async with async_sessionmaker(context.engine)() as reader:
            assert (await reader.execute(select(Job).where(Job.parent_job_id == job_id))).scalars().all() == []
            assert (await reader.execute(select(NgsPooledAssignmentRelease))).scalars().all() == []
            assert (await reader.execute(select(NgsPooledAssignmentReleaseTarget))).scalars().all() == []
        monkeypatch.setattr(jobs, "create_job", _REAL_CREATE)
        response = await context.client.post(path, json=payload)
    assert response.status_code == 201, response.text
    replay = await context.client.post(path, json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json() == response.json()
    async with async_sessionmaker(context.engine)() as reader:
        children = (await reader.execute(select(Job).where(Job.parent_job_id == job_id))).scalars().all()
        assert len(children) == 2
        assert len((await reader.execute(select(NgsPooledAssignmentRelease))).scalars().all()) == 1
        assert len((await reader.execute(select(NgsPooledAssignmentReleaseTarget))).scalars().all()) == 2
        for child in children:
            assert child.params["remote_result_policy"] == ExecutionPolicy().remote_result_policy
            assert child.params["expected_plasmid_size"] == 8
            assert child.params["requested_expected_plasmid_size"] is None
            assert child.status == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after_insert", [False, True])
async def test_instrument_handoff_real_owners(policy_context, monkeypatch, tmp_path, fail_after_insert):
    from database import MolBioNgsReceipt
    from services import ont_run_control
    from tests.test_ont_run_control import _seed_server_observed_run
    from tests.test_molbio_ngs_cross_plane_lineage import _domain_binding, _state_payload
    from tests.molbio_ngs_managed_fixture import initialize_managed_domain
    from molbio_ngs_database import create_molbio_ngs_engine, create_molbio_ngs_session_factory
    from molbio_ngs_migrations import run_all
    from molbio_ngs_services import StateMember, save_state_revision
    from services.molbio_ngs_member_receipts import build_external_member_receipt, persist_member_receipt

    context = policy_context
    factory = async_sessionmaker(context.engine, expire_on_commit=False)
    monkeypatch.setattr(ont_run_control, "async_session", factory)
    monkeypatch.setattr(ont_run_control, "get_inputs_dir", lambda: context.inputs_root)
    from services import molbio_ngs_member_receipts
    monkeypatch.setattr(molbio_ngs_member_receipts, "get_inputs_dir", lambda: context.inputs_root)
    monkeypatch.setattr(molbio_ngs_member_receipts, "get_results_dir", lambda: context.results_root)
    run_id = await _seed_server_observed_run(state="starting", output_directories={"reads": str(context.fastq.parent)})
    # Only host observation is inert: the actual manifest, snapshot, receipt,
    # conversion and canonical Job insertion owners all execute.
    monkeypatch.setattr(ont_run_control, "request_host_agent", lambda *args, **kwargs: {
        "status": "completed", "minknow_run_id": "PRIVATE-MINKNOW-RUN",
        "output_files": {"fastq": [str(context.fastq)], "pod5": [], "bam": []},
    })
    await ont_run_control.reconcile_instrument_run(run_id)
    domain_path = tmp_path / "handoff-domain.db"
    run_all(domain_path)
    engine = create_molbio_ngs_engine(f"sqlite+aiosqlite:///{domain_path}")
    try:
        async with create_molbio_ngs_session_factory(engine)() as domain:
            await initialize_managed_domain(domain, _domain_binding("domain-1", "domain-rev-1"), idempotency_key="init-policy-domain")
            external = await persist_member_receipt(domain, build_external_member_receipt(
                source_store_id="molbio", entity_kind="molecular_revision", entity_id="external-policy-revision",
                source_generation_or_revision="1", content_digest="e" * 64,
                source_schema="bms.molbio.molecular-revision.v1", availability="available",
                reopen_destination={"surface": "molbio-sequence-revision", "params": {"sequence_id": "sequence-01", "revision_id": "revision-01"}},
            ))
            state = await save_state_revision(domain, global_domain_experiment_id="domain-1",
                global_domain_experiment_revision_id="domain-rev-1", payload=_state_payload(),
                members=[StateMember(receipt_id=external.receipt_id, role="molecular_expected_construct", ordinal=0)],
                expected_head_generation=0, parent_revision_id=None, idempotency_key="policy-state")
            state_id = state.id
            await domain.commit()
            async def domain_dependency():
                yield domain
            context.app.dependency_overrides[ont_runs.get_molbio_ngs_session] = domain_dependency
            payload = {"name": "instrument policy", "molbio_ngs_receipt_id": context.receipt_ids[0],
                       "global_domain_experiment_id": "domain-1", "molbio_ngs_state_revision_id": state_id}
            path = f"/api/ont/runs/{run_id}/handoff/plasmid-qc/submit"
            for policy in (None, {}, {"remote_result_policy": "automatic"}):
                rejected = await context.client.post(path, json={**payload, "execution_policy": policy})
                assert rejected.status_code == 422
                assert "accepts only" in rejected.text
            if fail_after_insert:
                async def failing_create(*args, **kwargs):
                    await _REAL_CREATE(*args, **kwargs)
                    raise RuntimeError("injected handoff failure after real insertion")
                monkeypatch.setattr(jobs, "create_job", failing_create)
                with pytest.raises(RuntimeError, match="injected handoff failure"):
                    await context.client.post(path, json=payload)
                async with factory() as reader:
                    assert (await reader.execute(select(Job))).scalars().all() == []
                    receipt = await reader.get(MolBioNgsReceipt, context.receipt_ids[0])
                    assert receipt.consumed_at is None and receipt.consumed_job_id is None
                monkeypatch.setattr(jobs, "create_job", _REAL_CREATE)
            response = await context.client.post(path, json=payload)
            assert response.status_code == 201, response.text
            async with factory() as reader:
                row = await reader.get(Job, response.json()["id"])
                assert row.params["remote_result_policy"] == ExecutionPolicy().remote_result_policy
                assert row.params["source_instrument_run_id"] == run_id
                assert row.params["expected_plasmid_size"] == 8
                assert row.params["requested_expected_plasmid_size"] is None
                assert row.params["ont_instrument_run_binding"]["observed_generation"] == 2
                receipt = await reader.get(MolBioNgsReceipt, context.receipt_ids[0])
                assert receipt.consumed_job_id == row.id
            # This endpoint uses a one-time receipt, not pooled idempotency:
            # replay is refused and must never create another Job.
            replay = await context.client.post(path, json=payload)
            assert replay.status_code == 422, replay.text
            async with factory() as reader:
                assert len((await reader.execute(select(Job))).scalars().all()) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["placement", "escaped-input", "managed-reference", "nested-policy"])
async def test_policy_fix_preserves_receiving_negatives(policy_context, tmp_path, case):
    context = policy_context
    payload = {"name": "negative control", "params": {"pod5_dir": str(context.pod5)}}
    if case == "placement":
        payload["execution_target_id"] = "missing-target"
        expected = "not an active ready execution target"
    elif case == "escaped-input":
        outside = tmp_path / "outside"
        outside.mkdir()
        payload["params"]["pod5_dir"] = str(outside)
        expected = "confined"
    elif case == "managed-reference":
        payload["params"]["managed_reference_snapshot_sha256"] = "a" * 64
        expected = "server-controlled"
    else:
        payload["params"]["execution_policy"] = {"remote_result_policy": "automatic"}
        expected = "execution_policy"
    response = await context.client.post("/api/ont/ngs/ont_basecall_dna/submit", json=payload)
    assert response.status_code == 422, response.text
    assert expected in response.text
    async with async_sessionmaker(context.engine)() as reader:
        assert (await reader.execute(select(Job))).scalars().all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [{"remote_result_policy": "invalid"}, {"unexpected": True}, "manual"])
async def test_invalid_policy_still_rejected(policy_context, policy):
    context = policy_context
    response = await context.client.post("/api/ont/ngs/ont_basecall_dna/submit", json={
        "name": "invalid policy", "params": {"pod5_dir": str(context.pod5)}, "execution_policy": policy,
    })
    assert response.status_code == 422
    assert "execution_policy" in response.text
    assert (await context.session.execute(select(Job))).scalars().all() == []
