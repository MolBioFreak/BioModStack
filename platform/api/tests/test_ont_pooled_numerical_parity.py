"""Pooled numerical observations through real HTTP/read/release owners."""
import csv
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Job, NgsPooledAssignmentRelease, NgsReferenceSetManifest
from services import ont_pooled_reference_assignment as pooled
from test_ont_policy_regression import policy_context, pooled_context
from test_ont_pooled_reference_assignment import _submit_request, _write_assignment_summary

NUMERICAL = ("best_alignment_score", "second_alignment_score", "alignment_score_delta", "best_mapq")


@pytest.mark.asyncio
@pytest.mark.parametrize("observations", ["legacy", "current", "partial", "low_confidence", "writer"])
async def test_numerical_http_reader_and_explicit_release(policy_context, observations, record_property, monkeypatch):
    context = policy_context
    response = await context.client.post("/api/ont/ngs/pooled-reference-assignment/submit", json=_submit_request(context).model_dump(mode="json"))
    assert response.status_code == 201, response.text
    submitted = response.json()
    job_id = submitted["assignment_job_id"]
    path = f"/api/jobs/{job_id}/pooled-assignment/targets?read_limit=100"
    pending = await context.client.get(path)
    assert pending.status_code == 200
    assert pending.json()["read_assignments"] is None
    job = await context.session.get(Job, job_id)
    job.status = "completed"
    await context.session.commit()
    summary_path = _write_assignment_summary(context, submitted)
    summary = json.loads(summary_path.read_text())
    values = (100, 70, 30, 60) if observations != "low_confidence" else (-10, -10, 0, 0)
    if observations != "legacy":
        for row in summary["read_assignments"]:
            row.update(dict(zip(NUMERICAL, values)))
            if observations == "partial":
                del row["second_alignment_score"]
                row["alignment_score_delta"] = None
        summary_path.write_text(json.dumps(summary))
        tsv_path = summary_path.with_name("per_read_assignment.tsv")
        with tsv_path.open() as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            fields = reader.fieldnames
            rows = list(reader)
        for row in rows:
            row.update(dict(zip(NUMERICAL, values)))
            if observations == "partial":
                row["second_alignment_score"] = ""
                row["alignment_score_delta"] = ""
        with tsv_path.open("w") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
    if observations == "writer":
        from pathlib import Path
        from scripts import pooled_ont_reference_assignment as writer
        manifest = await context.session.get(NgsReferenceSetManifest, submitted["reference_set_id"])
        manifest_path = Path(manifest.manifest_path)
        output = summary_path.parent
        writer.run_preflight(manifest_path, manifest_path.parent, context.fastq, output)
        bam = output / "pooled_assignment.bam"
        bam.write_bytes(b"inert BAM; SAM transport replaced below")
        bam.with_suffix(".bam.bai").write_bytes(b"inert index")
        (output / "combined_intended_reference.fasta.fai").write_text("inert index")
        (output / "pooled_reference_assignment.minimap2.log").write_text("inert transport")
        sam = ["@HD\tVN:1.6\tSO:coordinate"] + [
            f"@SQ\tSN:target-{target}\tLN:8" for target in "abc"
        ]
        for ordinal, target, sequence in ((1, "a", "ACGT"), (2, "b", "TGCA")):
            sam.extend([
                f"occurrence_{ordinal}\t0\ttarget-{target}\t1\t60\t4M\t*\t0\t0\t{sequence}\tIIII\tAS:i:100",
                f"occurrence_{ordinal}\t256\ttarget-c\t1\t0\t4M\t*\t0\t0\t{sequence}\tIIII\tAS:i:70",
            ])
        monkeypatch.setattr(writer, "_samtools_view", lambda *_: iter(sam))
        writer.run_classify(manifest_path, manifest_path.parent, context.fastq,
                            output / "valid_reads.fastq", output / "fastq_preflight.json",
                            bam, ["inert-samtools"], output / "combined_intended_reference.fasta",
                            output, 20, 10)
        summary = json.loads(summary_path.read_text())
        with (output / "per_read_assignment.tsv").open() as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        for tsv, row in zip(rows, summary["read_assignments"], strict=True):
            assert tuple(row[key] for key in NUMERICAL) == (100, 70, 30, 60)
            assert tuple(int(tsv[key]) for key in NUMERICAL) == (100, 70, 30, 60)
    loaded = await pooled._load_release_context(context.session, job_id)
    assert loaded["summary"]["read_assignments"] == summary["read_assignments"]
    async def no_evidence_scan(*_args, **_kwargs):
        raise AssertionError("ordinary target polling must not load release/read evidence")
    with monkeypatch.context() as guard:
        guard.setattr(pooled, "_load_release_context", no_evidence_scan)
        ordinary = await context.client.get(path.split("?")[0])
        assert ordinary.status_code == 200
        assert "read_assignments" not in ordinary.json()
    for offset in (0, 1, 2):
        page = await context.client.get(path.split("?")[0], params={"read_limit": 1, "read_offset": offset})
        assert page.status_code == 200
        assert page.json()["read_assignments"] == summary["read_assignments"][offset:offset + 1]
        assert page.json()["read_assignments_total"] == len(summary["read_assignments"])
        assert page.json()["read_assignments_offset"] == offset
    read = await context.client.get(path)
    assert read.status_code == 200, read.text
    assert read.json()["read_assignments"] == summary["read_assignments"]
    record_property("api_path", path)
    record_property("api_read_assignments", json.dumps(read.json()["read_assignments"], sort_keys=True))
    async with async_sessionmaker(context.engine)() as reader:
        assert not (await reader.execute(select(Job).where(Job.parent_job_id == job_id))).scalars().all()
        assert not (await reader.execute(select(NgsPooledAssignmentRelease))).scalars().all()
    release_path = f"/api/jobs/{job_id}/pooled-assignment/release"
    payload = {"idempotency_key": "numeric-release", "target_workflow": "ont_plasmid_qc", "target_ids": ["target-a", "target-b"]}
    release = await context.client.post(release_path, json=payload)
    assert release.status_code == 201, release.text
    replay = await context.client.post(release_path, json=payload)
    assert replay.status_code == 201 and replay.json() == release.json()
    record_property("release_response", json.dumps(release.json(), sort_keys=True))
    async with async_sessionmaker(context.engine)() as reader:
        children = (await reader.execute(select(Job).where(Job.parent_job_id == job_id))).scalars().all()
        assert len(children) == 2
        assert all(child.params["remote_result_policy"] == "manual" for child in children)
        assert len((await reader.execute(select(NgsPooledAssignmentRelease))).scalars().all()) == 1
    after = await context.client.get(path)
    assert after.json()["read_assignments"] == summary["read_assignments"]


@pytest.mark.asyncio
async def test_unknown_assignment_fields_still_rejected_and_targets_remain_readable(policy_context):
    context = policy_context
    response = await context.client.post("/api/ont/ngs/pooled-reference-assignment/submit", json=_submit_request(context).model_dump(mode="json"))
    assert response.status_code == 201
    submitted = response.json()
    job_id = submitted["assignment_job_id"]
    job = await context.session.get(Job, job_id)
    job.status = "completed"
    await context.session.commit()
    summary_path = _write_assignment_summary(context, submitted)
    summary = json.loads(summary_path.read_text())
    summary["read_assignments"][0]["unknown_field"] = 1
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(pooled.PooledAssignmentError, match="keys are not exact"):
        await pooled._load_release_context(context.session, job_id)
    response = await context.client.get(f"/api/jobs/{job_id}/pooled-assignment/targets?read_limit=100")
    assert response.status_code == 200
    assert len(response.json()["targets"]) == 3
    assert response.json()["read_assignments"] is None
