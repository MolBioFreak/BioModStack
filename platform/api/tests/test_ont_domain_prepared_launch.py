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
