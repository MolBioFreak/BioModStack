"""Ordinary ONT retained launch controls; real HTTP, compiler and scratch SQL owners."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Job, MolBioNgsReceipt, NgsComparisonPanelReceipt
from routers import ont_runs, jobs
from services import molbio_ngs_receipts as receipts, ngs_comparison_panels as panels
from services import ont_submission_trust as custody
from services.ont_ngs_contract import ONT_WORKFLOW_ALIASES, CANONICAL_ONT_WORKFLOWS


@pytest_asyncio.fixture
async def launch(tmp_path, monkeypatch):
    from fastapi import FastAPI
    import paths
    import services.job_result_roots as result_roots

    data = tmp_path / "data"
    inputs = data / "inputs"
    results = data / "results"
    inputs.mkdir(parents=True)
    results.mkdir()
    monkeypatch.setenv("BMS_DATA", str(data))
    monkeypatch.setenv("BMS_WORK", str(data / "work"))
    monkeypatch.setenv("BMS_RESULTS_DIR", str(results))
    monkeypatch.setenv("BMS_MOLBIO_NGS_REFERENCE_ROOT", str(data / "references"))
    from services import molbio_ngs_references as references
    for owner in (paths, jobs, receipts, panels, custody, references):
        monkeypatch.setattr(owner, "get_inputs_dir", lambda: inputs)
    monkeypatch.setattr(paths, "get_data_root", lambda: data)
    monkeypatch.setattr(jobs, "get_data_root", lambda: data)
    monkeypatch.setattr(jobs, "get_results_dir", lambda: results)
    monkeypatch.setattr(result_roots, "get_results_dir", lambda: results)
    monkeypatch.setattr(ont_runs, "get_allowed_roots", lambda: {"inputs": inputs, "results": results})
    monkeypatch.setattr(jobs, "get_allowed_roots", lambda: {"inputs": inputs, "results": results})
    monkeypatch.setattr(paths, "get_allowed_roots", lambda: {"inputs": inputs, "results": results})
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'core.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def session_dependency():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(ont_runs.router, prefix="/api/ont")
    app.include_router(jobs.router, prefix="/api/jobs")
    app.dependency_overrides[ont_runs.get_session] = session_dependency
    app.dependency_overrides[ont_runs.get_experiment_session] = session_dependency
    app.dependency_overrides[ont_runs.get_molbio_ngs_session] = session_dependency
    reads = inputs / "reads.fastq"
    reads.write_text("@r\nACGT\n+\nIIII\n")
    pod5 = inputs / "pod5"
    pod5.mkdir()
    (pod5 / "reads.pod5").write_bytes(b"inert input selector; no science executed")
    revision = SimpleNamespace(id="revision-1", content_sha256=hashlib.sha256(b"ACGT").hexdigest(),
                               snapshot={"sequence": "ACGT", "sequence_type": "dna"})
    async with factory() as session:
        receipt = await receipts.issue_molbio_ngs_receipt(session, sequence_id="sequence-1", revision=revision)
        panel = await panels.seed_approved_panel(session, entries=[{
            "sequence_id": "sequence-1", "revision": revision, "role": "host",
        }], actor="test")
        panel_receipt = await panels.issue_comparison_panel_receipt(session, panel_id=panel.id, expected_receipt_id=receipt.id)
        await session.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        yield SimpleNamespace(client=client, app=app, inputs=inputs, results=results, factory=factory,
                              reads=reads, pod5=pod5, receipt=receipt, panel_receipt=panel_receipt)
    await engine.dispose()


def payload(launch, *, panel=False):
    params = {"fastq_path": str(launch.reads), "molbio_ngs_receipt_id": launch.receipt.id}
    if panel:
        params["ngs_comparison_panel_receipt_id"] = launch.panel_receipt.id
    return {"params": params}


async def prepare(launch, body, workflow="ont_fastq_qc"):
    response = await launch.client.post(f"/api/ont/ngs/{workflow}/prepare", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def no_claims(launch):
    async with launch.factory() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 0
        assert (await session.get(MolBioNgsReceipt, launch.receipt.id)).consumed_at is None
        assert (await session.get(NgsComparisonPanelReceipt, launch.panel_receipt.id)).consumed_at is None


@pytest.mark.asyncio
async def test_fastq_prepare_replay_survives_upload_deletion_and_commits(launch):
    first = await prepare(launch, payload(launch))
    await no_claims(launch)
    snapshot = launch.inputs / "ont_fastq_launch_snapshots" / first["request"]["fastq_snapshot"]["relative_path"]
    assert snapshot.read_bytes() == launch.reads.read_bytes()
    assert "execution_target_id" not in first["request"]
    assert first["request"]["execution_plan_approval"] is None
    launch.reads.unlink()
    replay = await prepare(launch, first["request"])
    assert replay == first
    assert list((launch.inputs / "ont_fastq_launch_snapshots").rglob("*.fastq")) == [snapshot]
    response = await launch.client.post("/api/ont/ngs/ont_fastq_qc/submit", json=first["request"])
    assert response.status_code == 201, response.text
    async with launch.factory() as session:
        job = await session.get(Job, response.json()["id"])
        assert job.params["fastq_path"] == str(snapshot)
        assert job.params["ont_input_provenance"]["submitted_path"] == str(launch.reads)
        assert (await session.get(MolBioNgsReceipt, launch.receipt.id)).consumed_job_id == job.id
    used = await launch.client.post("/api/ont/ngs/ont_fastq_qc/prepare", json=first["request"])
    assert used.status_code == 422 and "already used" in used.text


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["expected_reference.fasta", "comparison_panel_snapshot.json", "revision-1.fasta"])
async def test_panel_replay_exact_bytes_no_publication_and_tamper(launch, tamper):
    first = await prepare(launch, payload(launch, panel=True), "ont_plasmid_qc")
    before = {p: p.stat().st_mtime_ns for p in launch.inputs.rglob("*") if p.is_file()}
    assert await prepare(launch, first["request"], "ont_plasmid_qc") == first
    assert before == {p: p.stat().st_mtime_ns for p in launch.inputs.rglob("*") if p.is_file()}
    await no_claims(launch)
    root = launch.inputs / "ngs_comparison_task_inputs" / first["request"]["comparison_launch_id"]
    (root / "unrelated.txt").write_text("ignored, not declared")
    assert await prepare(launch, first["request"], "ont_plasmid_qc") == first
    child = root / tamper
    child.write_bytes(b"changed")
    bad = await launch.client.post("/api/ont/ngs/ont_plasmid_qc/submit", json=first["request"])
    assert bad.status_code == 422 and "bytes changed" in bad.text
    await no_claims(launch)
    assert root.is_dir()


@pytest.mark.parametrize("value", ["../x", "/x", "x//y", "x/./y", "x\\y", "x\0y", "A" * 64 + "/" + "a" * 64 + "/launch-" + "b" * 32 + ".fastq"])
def test_closed_fastq_selector(value):
    with pytest.raises(ValidationError):
        ont_runs.OntFastqLaunchSnapshot(relative_path=value, sha256="a" * 64, size_bytes=1)


@pytest.mark.parametrize("size", [True, 1.5, "1", 0, -1])
def test_fastq_size_is_strict(size):
    with pytest.raises(ValidationError):
        ont_runs.OntFastqLaunchSnapshot(relative_path="b" * 64 + "/" + "a" * 64 + "/launch-" + "c" * 32 + ".fastq", sha256="a" * 64, size_bytes=size)


@pytest.mark.parametrize("value", ["AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", " aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "../x"])
def test_snapshot_uuids_are_not_repaired(value):
    with pytest.raises(ValidationError):
        ont_runs.OntManagedReferenceRequest(global_domain_experiment_id="d", molbio_ngs_state_revision_id="s", ngs_reference_revision_id="r", launch_snapshot_id=value)


@pytest.mark.asyncio
async def test_snapshot_symlink_rejected_and_retained(launch):
    first = await prepare(launch, payload(launch))
    path = launch.inputs / "ont_fastq_launch_snapshots" / first["request"]["fastq_snapshot"]["relative_path"]
    path.unlink()
    path.symlink_to(launch.reads)
    response = await launch.client.post("/api/ont/ngs/ont_fastq_qc/submit", json=first["request"])
    assert response.status_code == 422
    await no_claims(launch)
    assert path.is_symlink()


@pytest.mark.asyncio
async def test_shared_approval_transport_and_rollback(launch, monkeypatch):
    from fastapi import HTTPException
    first = await prepare(launch, payload(launch, panel=True), "ont_plasmid_qc")
    seen = []
    async def fail_after_claim(job, tasks, session, *_args, **kwargs):
        assert kwargs["commit"] is False
        assert (await session.get(MolBioNgsReceipt, launch.receipt.id)).consumed_at is not None
        assert (await session.get(NgsComparisonPanelReceipt, launch.panel_receipt.id)).consumed_at is not None
        assert job.execution_plan_approval == "f" * 64
        seen.append(job)
        session.add(Job(id="rolled-back", name=job.name, model_id=job.model_id, mode=job.mode, params=job.params))
        await session.flush()
        raise HTTPException(409, detail={"message": "controlled insertion failure"})
    monkeypatch.setattr(ont_runs, "_create_pipeline_job", fail_after_claim)
    request = {**first["request"], "execution_plan_approval": "f" * 64}
    failed = await launch.client.post("/api/ont/ngs/ont_plasmid_qc/submit", json=request)
    assert failed.status_code == 409 and failed.json()["detail"] == {"message": "controlled insertion failure"}
    assert len(seen) == 1
    await no_claims(launch)
    assert await prepare(launch, first["request"], "ont_plasmid_qc") == first


@pytest_asyncio.fixture
async def managed(launch, tmp_path):
    from molbio_ngs_database import create_molbio_ngs_engine, create_molbio_ngs_session_factory
    from molbio_ngs_migrations import run_all
    from molbio_ngs_services import StateMember, save_state_revision
    from services.molbio_ngs_references import create_reference, resolve_ngs_reference_revision_receipt
    from services.molbio_ngs_member_receipts import persist_member_receipt
    from tests.molbio_ngs_managed_fixture import initialize_managed_domain
    from tests.test_molbio_ngs_cross_plane_lineage import _domain_binding, _state_payload

    path = tmp_path / "domain.db"
    run_all(path)
    engine = create_molbio_ngs_engine(f"sqlite+aiosqlite:///{path}")
    factory = create_molbio_ngs_session_factory(engine)
    async with factory() as session:
        await initialize_managed_domain(session, _domain_binding("domain", "global-rev"), idempotency_key="init")
        reference, revision = await create_reference(
            session, global_domain_experiment_id="domain", name="reference", raw_fasta=b">reference\nACGT\n",
            molecule_type="dna", topology="circular", coordinate_contract="fasta-1-based-inclusive",
            source_provenance={"source": "test"}, idempotency_key="reference",
        )
        member = await persist_member_receipt(session, await resolve_ngs_reference_revision_receipt(
            session, global_domain_experiment_id="domain", reference_id=reference.id, revision_id=revision.id,
        ))
        state = _state_payload()
        state["design"]["expected_molecule_roles"] = ["ngs_reference"]
        state["reference_policy"]["required_roles"] = ["ngs_reference"]
        state["analysis_policy"]["allowed_workflow_ids"] = ["ont_fastq_qc", "ont_plasmid_qc"]
        state_revision = await save_state_revision(
            session, global_domain_experiment_id="domain", global_domain_experiment_revision_id="global-rev",
            payload=state, members=[StateMember(receipt_id=member.receipt_id, role="ngs_reference", ordinal=0)],
            expected_head_generation=0, parent_revision_id=None, idempotency_key="state",
        )
        await session.commit()
    async def dependency():
        async with factory() as session:
            yield session
    launch.app.dependency_overrides[ont_runs.get_molbio_ngs_session] = dependency
    yield SimpleNamespace(factory=factory, reference=reference, revision=revision, request={
        "params": {"fastq_path": str(launch.reads)},
        "managed_reference": {"global_domain_experiment_id": "domain",
                              "molbio_ngs_state_revision_id": state_revision.id,
                              "ngs_reference_revision_id": revision.id},
    })
    await engine.dispose()


@pytest.mark.asyncio
async def test_managed_http_replay_current_authority_and_duplicate_local_jobs(launch, managed):
    first = await prepare(launch, managed.request)
    assert "reference_fasta" not in first["request"]["params"]
    launch_id = first["request"]["managed_reference"]["launch_snapshot_id"]
    root = launch.inputs / "molbio_ngs_managed_launch_snapshots"
    snapshot = root / launch_id / "reference.fasta"
    before = snapshot.stat().st_mtime_ns
    assert await prepare(launch, first["request"]) == first
    await no_claims(launch)
    assert list(root.iterdir()) == [snapshot.parent]
    assert snapshot.stat().st_mtime_ns == before
    ids = []
    for _ in range(2):
        response = await launch.client.post("/api/ont/ngs/ont_fastq_qc/submit", json=first["request"])
        assert response.status_code == 201, response.text
        assert "reference_fasta" not in response.json()["params"]
        ids.append(response.json()["id"])
    assert ids[0] != ids[1]
    async with launch.factory() as session:
        for identifier in ids:
            job = await session.get(Job, identifier)
            assert job.params["reference_fasta"] == str(snapshot)
            assert job.params["ngs_reference_revision_id"] == managed.revision.id
    from services.molbio_ngs_references import archive_reference
    async with managed.factory() as session:
        await archive_reference(session, reference_id=managed.reference.id, expected_head_generation=1, idempotency_key="archive")
        await session.commit()
    rejected = await launch.client.post("/api/ont/ngs/ont_fastq_qc/prepare", json=first["request"])
    assert rejected.status_code == 422 and "archived" in rejected.text
    assert snapshot.is_file()


def test_managed_partial_publication_cleanup(tmp_path, monkeypatch):
    from services import molbio_ngs_references as references
    from molbio_ngs_services import StateIntegrityError
    monkeypatch.setattr(references, "get_inputs_dir", lambda: tmp_path)
    source = tmp_path / "reference.fasta"
    source.write_bytes(b">r\nACGT\n")
    with pytest.raises(StateIntegrityError, match="drifted"):
        references._snapshot_managed_reference(source, expected_sha256="f" * 64, expected_size_bytes=8)
    assert list((tmp_path / "molbio_ngs_managed_launch_snapshots").iterdir()) == []


@pytest.mark.asyncio
async def test_remote_shared_review_missing_approval_rolls_back_claims(launch):
    from database import ExecutionTarget
    async with launch.factory() as session:
        session.add(ExecutionTarget(id="fixture-worker", provider="vast", provider_instance_id="fixture", active=True,
            state="ready", provider_metadata={"inventory": {"checked_at": datetime.utcnow().isoformat(),
                "status": "complete", "present": True, "running": True}}))
        await session.commit()
    body = {**payload(launch, panel=True), "execution_target_id": "fixture-worker"}
    first = await prepare(launch, body, "ont_plasmid_qc")
    print("REMOTE_COMPILER_REVIEW", first["preview"]["admissible"], first["preview"]["blockers"])
    response = await launch.client.post("/api/ont/ngs/ont_plasmid_qc/submit", json=first["request"])
    assert response.status_code == 409, response.text
    assert "requires explicit execution-plan preview approval" in response.text
    await no_claims(launch)
    assert await prepare(launch, first["request"], "ont_plasmid_qc") == first


@pytest.mark.asyncio
async def test_review_digest_binds_edits_and_context_not_historical_source_label(launch):
    first = await prepare(launch, payload(launch))
    for changes in ({"name": "edited"}, {"execution_target_id": "other-worker"},
                    {"params": {**first["request"]["params"], "min_read_length": 500}},
                    {"params": {**first["request"]["params"], "fastq_path": str(launch.inputs / "deleted-source.fastq")}}):
        changed = await prepare(launch, {**first["request"], **changes})
        assert changed["preview"]["approval_digest"] != first["preview"]["approval_digest"]
        assert changed["request"]["fastq_snapshot"] == first["request"]["fastq_snapshot"]
    token = ont_runs.current_launch_context_id.set("server-project-context")
    try:
        contextual = await prepare(launch, first["request"])
        assert contextual["preview"]["request"]["launch_context_id"] == "server-project-context"
        assert contextual["preview"]["approval_digest"] != first["preview"]["approval_digest"]
    finally:
        ont_runs.current_launch_context_id.reset(token)
    await no_claims(launch)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["ont_input_provenance", "comparison_panel_binding", "molbio_revision_binding"])
async def test_prepare_never_accepts_browser_provenance(launch, field):
    body = payload(launch)
    body["params"][field] = {"source": "caller"}
    response = await launch.client.post("/api/ont/ngs/ont_fastq_qc/prepare", json=body)
    assert response.status_code == 422 and "server-controlled" in response.text
    await no_claims(launch)


@pytest.mark.asyncio
async def test_expired_receipt_cannot_be_replaced_under_retained_selection(launch):
    first = await prepare(launch, payload(launch))
    async with launch.factory() as session:
        receipt = await session.get(MolBioNgsReceipt, launch.receipt.id)
        receipt.expires_at = datetime.utcnow() - timedelta(seconds=1)
        await session.commit()
    response = await launch.client.post("/api/ont/ngs/ont_fastq_qc/submit", json=first["request"])
    assert response.status_code == 422 and "expired" in response.text
    await no_claims(launch)


@pytest.mark.asyncio
async def test_generic_job_route_cannot_repost_prepared_authority(launch):
    first = await prepare(launch, payload(launch))
    response = await launch.client.post("/api/jobs", json=first["preview"]["request"])
    assert response.status_code == 422 and "typed /api/ont/ngs" in response.text
    await no_claims(launch)


@pytest.mark.asyncio
async def test_panel_commit_consumes_both_receipts_and_retains_staging(launch):
    first = await prepare(launch, payload(launch, panel=True), "ont_plasmid_qc")
    response = await launch.client.post("/api/ont/ngs/ont_plasmid_qc/submit", json=first["request"])
    assert response.status_code == 201, response.text
    async with launch.factory() as session:
        job = await session.get(Job, response.json()["id"])
        assert (await session.get(MolBioNgsReceipt, launch.receipt.id)).consumed_job_id == job.id
        assert (await session.get(NgsComparisonPanelReceipt, launch.panel_receipt.id)).consumed_job_id == job.id
        assert Path(job.params["comparison_panel_snapshot"]).is_file()
        assert job.params["comparison_panel_binding"]["receipt_id"] == launch.panel_receipt.id


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["fastq", "molbio", "managed", "panel"])
async def test_shared_remote_digest_and_transaction_with_result_projection_seam(launch, managed, monkeypatch, family):
    """Test only this lane's handoff; native result projection has a separate owner.

    The compiler, canonical digest, target validation, approval comparator and SQL
    insertion remain real. On the pre-integration base, the one known result-
    contract blocker is isolated at the preview admissibility seam, not replaced
    with a fabricated native result contract. Integrated acceptance must remove
    that blocker in the real projection owner; this control is NOT that evidence.
    """
    from database import ExecutionTarget
    original = jobs._execution_plan_preview
    def preview(job, *args, **kwargs):
        result = original(job, *args, **kwargs)
        if not result["admissible"]:
            assert len(result["blockers"]) == 1, result["blockers"]
            blocker = result["blockers"][0]
            assert blocker["field"] == "result_contract" and blocker["component_or_dependency_id"] == "nanopore"
            result = {**result, "admissible": True}
        return result
    monkeypatch.setattr(jobs, "_execution_plan_preview", preview)
    async with launch.factory() as session:
        session.add(ExecutionTarget(id="fixture-worker", provider="vast", provider_instance_id="fixture", active=True,
            state="ready", provider_metadata={"inventory": {"checked_at": datetime.utcnow().isoformat(),
                "status": "complete", "present": True, "running": True}}))
        await session.commit()
    body = managed.request if family == "managed" else payload(launch, panel=family == "panel")
    workflow = "ont_fastq_qc" if family in {"fastq", "managed"} else "ont_plasmid_qc"
    first = await prepare(launch, {**body, "execution_target_id": "fixture-worker"}, workflow)
    approved = {**first["request"], "execution_plan_approval": first["preview"]["approval_digest"]}
    stale = await launch.client.post(f"/api/ont/ngs/{workflow}/submit", json={**approved, "name": "edited"})
    assert stale.status_code == 409 and "stale" in stale.text, stale.text
    await no_claims(launch)
    assert await prepare(launch, first["request"], workflow) == first
    response = await launch.client.post(f"/api/ont/ngs/{workflow}/submit", json=approved)
    assert response.status_code == 201, response.text
    async with launch.factory() as session:
        job = await session.get(Job, response.json()["id"])
        assert job.execution_target_id == "fixture-worker"
        assert job.provenance["execution_plan_approval"]["approval_digest"] == first["preview"]["approval_digest"]
        assert job.params["fastq_path"] == first["preview"]["request"]["params"]["fastq_path"]
        if family != "managed":
            assert (await session.get(MolBioNgsReceipt, launch.receipt.id)).consumed_job_id == job.id
        if family == "panel":
            assert (await session.get(NgsComparisonPanelReceipt, launch.panel_receipt.id)).consumed_job_id == job.id


@pytest.mark.asyncio
async def test_final_commit_failure_rolls_back_job_and_claims_not_snapshots(launch):
    first = await prepare(launch, payload(launch, panel=True), "ont_plasmid_qc")
    rolled_back = []
    async def dependency():
        async with launch.factory() as session:
            rollback = session.rollback
            async def fail_commit():
                raise RuntimeError("controlled final commit failure")
            async def track_rollback():
                rolled_back.append(True)
                await rollback()
            session.commit = fail_commit
            session.rollback = track_rollback
            yield session
    launch.app.dependency_overrides[ont_runs.get_session] = dependency
    with pytest.raises(RuntimeError, match="controlled final commit failure"):
        await launch.client.post("/api/ont/ngs/ont_plasmid_qc/submit", json=first["request"])
    assert rolled_back == [True]
    await no_claims(launch)
    assert await prepare(launch, first["request"], "ont_plasmid_qc") == first


@pytest.mark.asyncio
async def test_saved_placement_omission_and_explicit_local_are_distinct(launch):
    request = ont_runs.OntNgsSubmitRequest(params={"pod5_dir": str(launch.pod5)})
    omitted = ont_runs._job_create_for_ont_submit("basecall_dna", request)
    local = ont_runs._job_create_for_ont_submit("basecall_dna", ont_runs.OntNgsSubmitRequest.model_validate({
        **request.model_dump(exclude_unset=True), "execution_target_id": None,
    }))
    assert "execution_target_id" not in omitted.model_fields_set
    assert "execution_target_id" in local.model_fields_set
    assert omitted.execution_target_id is local.execution_target_id is None
    response = await launch.client.post("/api/ont/ngs/basecall_dna/submit", json=request.model_dump(exclude_unset=True))
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["fastq", "managed"])
async def test_retained_snapshot_digest_tamper_is_rejected_before_claims(launch, managed, family):
    first = await prepare(launch, managed.request if family == "managed" else payload(launch))
    if family == "managed":
        snapshot = launch.inputs / "molbio_ngs_managed_launch_snapshots" / first["request"]["managed_reference"]["launch_snapshot_id"] / "reference.fasta"
    else:
        snapshot = launch.inputs / "ont_fastq_launch_snapshots" / first["request"]["fastq_snapshot"]["relative_path"]
    snapshot.chmod(0o600)
    content = snapshot.read_bytes()
    snapshot.write_bytes(b"X" + content[1:])
    response = await launch.client.post("/api/ont/ngs/ont_fastq_qc/submit", json=first["request"])
    assert response.status_code == 422 and ("digest mismatch" in response.text or "bytes changed" in response.text)
    await no_claims(launch)
    assert snapshot.is_file()


@pytest.mark.asyncio
async def test_fastq_descriptor_does_not_expand_other_workflow_custody(launch):
    first = await prepare(launch, payload(launch))
    response = await launch.client.post("/api/ont/ngs/ont_plasmid_qc/prepare", json=first["request"])
    assert response.status_code == 422 and "only to ordinary FASTQ QC" in response.text
    await no_claims(launch)


ORDINARY_IDS = sorted(identity for identity in set(CANONICAL_ONT_WORKFLOWS) | set(ONT_WORKFLOW_ALIASES)
                      if ont_runs.resolve_ont_workflow_alias(identity) != "ont_pooled_reference_assignment")


@pytest.mark.asyncio
@pytest.mark.parametrize("workflow", ORDINARY_IDS)
async def test_all_ordinary_identities_stable_prepare_and_explicit_local(launch, workflow):
    canonical = ont_runs.resolve_ont_workflow_alias(workflow)
    if canonical == "ont_pooled_reference_assignment":
        pytest.fail("dedicated pooled workflow must not enter ordinary test roster")
    body = payload(launch)
    if canonical in {"ont_basecall_dna", "ont_basecall_rna", "ont_methylation_analysis"}:
        body["params"].pop("fastq_path")
        body["params"]["pod5_dir"] = str(launch.pod5)
    body["execution_target_id"] = None
    first = await prepare(launch, body, workflow)
    assert first["workflow_id"] == workflow
    assert first["request"]["execution_target_id"] is None
    assert first["preview"]["request"]["model_id"] == "nanopore"
    assert first["preview"]["request"]["params"]["ont_request_workflow_id"] == workflow
    assert first["preview"]["request"]["params"]["ont_workflow_id"] == canonical
    assert await prepare(launch, first["request"], workflow) == first
    await no_claims(launch)
    response = await launch.client.post(f"/api/ont/ngs/{workflow}/submit", json=first["request"])
    assert response.status_code == 201, response.text
    async with launch.factory() as session:
        job = await session.get(Job, response.json()["id"])
        assert job.params["ont_request_workflow_id"] == workflow
        assert job.params["ont_workflow_id"] == canonical
        assert job.execution_target_id is None
