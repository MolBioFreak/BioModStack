"""Domain prepare/replay through scratch SQL, HTTP and the real native compiler.

Retained source products are fixtures, not a claim of scientific execution.
"""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, func
from database import Job, ExecutionTarget, MolBioNgsReceipt, NgsReferenceSetManifest, NgsReferenceSetMapping
from routers import jobs, ont_runs
from services import ont_barcode_batches as batches, ont_pooled_reference_assignment as pooled
from tests.test_ont_prepared_launch import launch
from tests.test_ont_barcode_batches import _source_tree, _add_receipts


@pytest_asyncio.fixture
async def domain(launch, monkeypatch):
    launch.app.include_router(ont_runs.barcode_router, prefix="/api/jobs")
    for owner in (batches, pooled):
        monkeypatch.setattr(owner, "get_inputs_dir", lambda: launch.inputs)
    monkeypatch.setattr(pooled, "get_results_dir", lambda: launch.results)
    launch.reads.write_text("@read-a note\nACGT\n+\nIIII\n@read-b\nACGT\n+\nIIII\n")
    root = launch.results / "source-job"
    digest, source = _source_tree(root, ["barcode01", "barcode02"])
    source["job"].execution_target_id = "deleted-source-rental"
    async with launch.factory() as session:
        session.add(source["job"])
        ids = _add_receipts(session, launch.inputs, 2)
        session.add(ExecutionTarget(id="chosen-worker", provider="vast", provider_instance_id="inert", active=True,
            state="ready", provider_metadata={"inventory": {"checked_at": datetime.utcnow().isoformat(),
                "status": "complete", "present": True, "running": True}}))
        await session.commit()
    # Only the source cookie is fixture-authorized. No compiler, creation or result
    # projection is replaced; no scientific jobs are executed by this scratch app.
    monkeypatch.setattr(ont_runs.alignment_access, "request_is_authorized", lambda *_: True)
    return SimpleNamespace(**vars(launch), receipt_ids=ids, source_root=root)


def barcode_request(ctx, **extra):
    return {"idempotency_key": "batch", "target_workflow": "ont_plasmid_qc",
            "mappings": [{"unit_id": f"barcode{i+1:02d}", "molbio_ngs_receipt_id": receipt}
                         for i, receipt in enumerate(ctx.receipt_ids)], **extra}


def pooled_request(ctx, **extra):
    return {"idempotency_key": "pooled", "fastq_path": str(ctx.reads),
            "targets": [{"target_id": f"target{i+1}", "label": f"Target {i+1}", "molbio_ngs_receipt_id": receipt}
                        for i, receipt in enumerate(ctx.receipt_ids)], **extra}


async def assert_unclaimed(ctx):
    async with ctx.factory() as session:
        assert await session.scalar(select(func.count()).select_from(NgsReferenceSetManifest)) == 0
        assert await session.scalar(select(func.count()).select_from(NgsReferenceSetMapping)) == 0
        assert await session.scalar(select(func.count()).select_from(Job)) == 1
        for rid in ctx.receipt_ids:
            assert (await session.get(MolBioNgsReceipt, rid)).consumed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["barcode", "pooled"])
@pytest.mark.parametrize("target", [None, "chosen-worker"])
async def test_domain_prepare_replay_commit_and_fresh_read(domain, family, target):
    ctx = domain
    url = "/api/jobs/source-job/barcode-batches" if family == "barcode" else "/api/ont/ngs/pooled-reference-assignment"
    submit = url if family == "barcode" else url + "/submit"
    body = (barcode_request if family == "barcode" else pooled_request)(ctx, execution_target_id=target)
    first = await ctx.client.post(url + "/prepare", json=body)
    assert first.status_code == 200, first.text
    first = first.json()
    await assert_unclaimed(ctx)
    root = ctx.inputs / "ngs_reference_sets" / first["request"]["reference_set_id"]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    replay = await ctx.client.post(url + "/prepare", json=first["request"])
    assert replay.status_code == 200, replay.text
    assert replay.json() == first
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    if family == "barcode":
        approved = {**first["request"], "execution_plan_approvals": {key: value["approval_digest"] for key, value in first["previews"].items()}}
    else:
        approved = {**first["request"], "execution_plan_approval": first["preview"]["approval_digest"]}
    if target:
        stale = {**approved, "name_prefix" if family == "barcode" else "name": "changed"}
        failed = await ctx.client.post(submit, json=stale)
        assert failed.status_code == 409 and "stale" in failed.text, failed.text
        await assert_unclaimed(ctx)
        assert root.is_dir()
    done = await ctx.client.post(submit, json=approved)
    assert done.status_code == 201, done.text
    done = done.json()
    child_ids = done["child_job_ids"] if family == "barcode" else [done["assignment_job_id"]]
    async with ctx.factory() as session:
        for cid in child_ids:
            job = await session.get(Job, cid)
            assert job.execution_target_id == target
            assert job.parent_job_id is None
            if family == "barcode":
                assert job.source_stage_job_id == "source-job"
                assert job.lineage_root_job_id == "source-job"
            else:
                assert job.params["scientific_status"] == "REVIEW"
        for rid in ctx.receipt_ids:
            assert (await session.get(MolBioNgsReceipt, rid)).consumed_at
    repeated = await ctx.client.post(submit, json=approved)
    assert repeated.status_code == 201 and repeated.json() == done


@pytest.mark.asyncio
async def test_second_barcode_child_failure_rolls_back_sql_but_retains_review(domain, monkeypatch):
    from fastapi import HTTPException
    ctx = domain
    url = "/api/jobs/source-job/barcode-batches"
    prepared = await ctx.client.post(url + "/prepare", json=barcode_request(ctx))
    assert prepared.status_code == 200, prepared.text
    prepared = prepared.json()
    real = batches._create_one_child
    count = 0
    async def fail_second(**kwargs):
        nonlocal count
        count += 1
        child = await real(**kwargs)
        if count == 2:
            raise HTTPException(409, {"code": "CONTROLLED_SECOND_CHILD_FAILURE"})
        return child
    monkeypatch.setattr(batches, "_create_one_child", fail_second)
    response = await ctx.client.post(url, json=prepared["request"])
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "CONTROLLED_SECOND_CHILD_FAILURE"
    await assert_unclaimed(ctx)
    assert (ctx.inputs / "ngs_reference_sets" / prepared["request"]["reference_set_id"]).is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [None, "chosen-worker"])
async def test_pooled_explicit_release_retained_review_and_independent_target(domain, target):
    from tests.test_ont_pooled_reference_assignment import _write_assignment_summary
    from database import NgsPooledAssignmentRelease
    ctx = domain
    submitted = await ctx.client.post("/api/ont/ngs/pooled-reference-assignment/submit", json=pooled_request(ctx))
    assert submitted.status_code == 201, submitted.text
    submitted = submitted.json()
    assignment_id = submitted["assignment_job_id"]
    _write_assignment_summary(SimpleNamespace(results_root=ctx.results, fastq=ctx.reads), submitted)
    async with ctx.factory() as session:
        job = await session.get(Job, assignment_id)
        job.status = "completed"
        job.execution_target_id = "deleted-source-rental"
        await session.commit()
    path = f"/api/jobs/{assignment_id}/pooled-assignment/release"
    body = {"idempotency_key": "release", "target_workflow": "ont_plasmid_qc",
            "target_ids": ["target1", "target2"], "execution_target_id": target}
    prepared = await ctx.client.post(path + "/prepare", json=body)
    assert prepared.status_code == 200, prepared.text
    prepared = prepared.json()
    again = await ctx.client.post(path + "/prepare", json=prepared["request"])
    assert again.status_code == 200 and again.json() == prepared, again.text
    async with ctx.factory() as session:
        assert await session.scalar(select(func.count()).select_from(NgsPooledAssignmentRelease)) == 0
        assert await session.scalar(select(func.count()).select_from(Job)) == 2
    approved = {**prepared["request"], "execution_plan_approvals": {
        key: value["approval_digest"] for key, value in prepared["previews"].items()}}
    if target:
        stale = await ctx.client.post(path, json={**approved, "name_prefix": "changed"})
        assert stale.status_code == 409 and "stale" in stale.text, stale.text
    released = await ctx.client.post(path, json=approved)
    assert released.status_code == 201, released.text
    async with ctx.factory() as session:
        for cid in released.json()["child_job_ids"]:
            child = await session.get(Job, cid)
            assert child.parent_job_id is None and child.execution_target_id == target
            assert child.source_stage_job_id == assignment_id
            assert child.params["pooled_assignment_release_binding"]["assignment_summary_sha256"]
    repeated = await ctx.client.post(path, json=approved)
    assert repeated.status_code == 201 and repeated.json() == released.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,target", [("resubmit", None), ("resubmit", "chosen-worker"),
                                              ("resume", None), ("resume", "chosen-worker")])
async def test_replay_sql_preserves_science_ancestry_without_active_old_approval(domain, operation, target):
    ctx = domain
    source_id = "replay-source"
    source_params = {"ont_workflow_id": "ont_plasmid_qc", "ont_input_mode": "fastq",
                     "fastq_path": str(ctx.reads), "reference_fasta": str(ctx.inputs / "molbio_ngs_receipts" / ctx.receipt_ids[0] / "expected_reference.fasta"),
                     "dorado_quality_mode": "sup", "dorado_model": "old-model", "dorado_resolved_model_id": "old-model",
                     "dorado_lock_sha256": "a" * 64, "dorado_runtime_sif": "/old/runtime.sif",
                     "run_assembly": False, "min_qscore": 17, "expected_plasmid_size": 4811,
                     "barcode_mapping_binding": {"mapping_id": "historical"},
                     "lineage_root_job_id": "source-job", "source_stage_job_id": "source-job",
                     "source_stage_family": "ont_ngs", "source_stage_mode": "basecall_dna",
                     "selection_source_type": "ont_dorado_demux", "selection_source_job_id": "source-job",
                     "source_selection_count": 2}
    output = ctx.results / source_id
    output.mkdir()
    async with ctx.factory() as session:
        session.add(Job(id=source_id, name="replay", model_id="nanopore", mode="plasmid_qc",
            status="failed", params=source_params, output_dir=str(output),
            execution_target_id="chosen-worker", execution_source_revision="b" * 40,
            execution_source_tree="c" * 40, provenance={"execution_plan_approval": {"old": True}},
            parent_job_id="source-job", child_stage="old_data_parent"))
        await session.commit()
    # An omitted cached-resume target retains the actual prior execution owner.
    body = {} if operation == "resume" and target == "chosen-worker" else {"execution_target_id": target}
    response = await ctx.client.post(f"/api/jobs/{source_id}/{operation}", json=body)
    assert response.status_code == 200, response.text
    async with ctx.factory() as session:
        replay = await session.get(Job, response.json()["new_job_id"])
        source = await session.get(Job, source_id)
        assert replay.execution_target_id == target
        assert replay.parent_job_id is None
        assert replay.lineage_root_job_id == replay.params["lineage_root_job_id"] == "source-job"
        assert replay.source_stage_job_id == "source-job"
        assert replay.source_selection_count == 2
        assert replay.params["min_qscore"] == 17 and replay.params["expected_plasmid_size"] == 4811
        assert "execution_plan_approval" not in replay.provenance
        assert source.provenance["execution_plan_approval"] == {"old": True}
        cached = operation == "resume" and target == "chosen-worker"
        if cached:
            assert replay.execution_source_revision == "b" * 40
            assert replay.execution_source_tree == "c" * 40
            assert replay.params["dorado_runtime_sif"] == "/old/runtime.sif"
            assert replay.params["dorado_resolved_model_id"] == "old-model"
            assert replay.output_dir == source.output_dir
        else:
            assert "dorado_runtime_sif" not in replay.params
            assert "dorado_resolved_model_id" not in replay.params
            assert replay.params["dorado_model"] == "sup"
            assert not any(key.startswith("resume_") for key in replay.params) or operation == "resume"
