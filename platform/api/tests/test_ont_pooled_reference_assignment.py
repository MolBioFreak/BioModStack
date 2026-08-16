from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.requests import Request

from database import (
    Base,
    Job,
    MolBioNgsReceipt,
    NgsPooledAssignmentRelease,
    NgsPooledAssignmentReleaseTarget,
    NgsPooledReferenceTarget,
    NgsReferenceSetManifest,
)
from routers import jobs, ont_runs
from schemas import JobStatus
from services import molbio_ngs_receipts
from services import ont_pooled_reference_assignment as pooled


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _http_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "headers": [],
            "query_string": b"",
        }
    )


def _add_receipts(
    session: AsyncSession,
    inputs_root: Path,
    sequences: tuple[str, ...],
) -> list[str]:
    receipt_ids: list[str] = []
    for index, sequence in enumerate(sequences, start=1):
        receipt_id = f"receipt-{index:02d}"
        fasta = f">receipt_{index}\n{sequence}\n".encode("ascii")
        path = inputs_root / "molbio_ngs_receipts" / receipt_id / "expected_reference.fasta"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fasta)
        session.add(
            MolBioNgsReceipt(
                id=receipt_id,
                sequence_id=f"sequence-{index:02d}",
                revision_id=f"revision-{index:02d}",
                revision_sha256=_sha256_bytes(sequence.encode("ascii")),
                reference_snapshot_path=str(path.resolve()),
                reference_snapshot_sha256=_sha256_bytes(fasta),
                expires_at=datetime.utcnow() + timedelta(minutes=15),
                created_at=datetime.utcnow(),
            )
        )
        receipt_ids.append(receipt_id)
    return receipt_ids


@pytest_asyncio.fixture
async def pooled_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    inputs_root = tmp_path / "inputs"
    results_root = tmp_path / "results"
    inputs_root.mkdir()
    results_root.mkdir()
    fastq = inputs_root / "uploads" / "pooled.fastq"
    fastq.parent.mkdir()
    fastq.write_text("@read-a note\nACGT\n+\nIIII\n@read-b\nTGCA\n+\nJJJJ\n", encoding="ascii")

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'main.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    receipt_ids = _add_receipts(session, inputs_root, ("ACGTACGT", "TGCATGCA", "GGGGAAAA"))
    await session.commit()

    monkeypatch.setattr(pooled, "get_inputs_dir", lambda: inputs_root)
    monkeypatch.setattr(pooled, "get_results_dir", lambda: results_root)
    monkeypatch.setattr(molbio_ngs_receipts, "get_inputs_dir", lambda: inputs_root)
    monkeypatch.setattr(
        ont_runs,
        "get_allowed_roots",
        lambda: {"inputs": inputs_root, "results": results_root},
    )

    calls: list[dict[str, Any]] = []
    failure = {"at": None}

    async def create_job(
        job_data,
        _background_tasks,
        db: AsyncSession,
        _preallocated_job_id=None,
        _commit=True,
        **_kwargs,
    ):
        assert _commit is False
        assert _preallocated_job_id is not None
        calls.append({"job": job_data, "id": _preallocated_job_id})
        output_dir = results_root / _preallocated_job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        job = Job(
            id=_preallocated_job_id,
            name=job_data.name,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=dict(job_data.params),
            status=JobStatus.QUEUED.value,
            queue_status=JobStatus.QUEUED.value,
            output_dir=str(output_dir),
            parent_job_id=job_data.parent_job_id,
            child_stage=job_data.child_stage,
            batch_id=job_data.batch_id,
            batch_name=job_data.batch_name,
            lineage_root_job_id=job_data.params.get("lineage_root_job_id"),
            stage_family=job_data.params.get("stage_family", "ont_ngs"),
            stage_mode=job_data.params.get("stage_mode", job_data.mode),
            source_stage_job_id=job_data.params.get("source_stage_job_id"),
            source_stage_family=job_data.params.get("source_stage_family"),
            source_stage_mode=job_data.params.get("source_stage_mode"),
            source_selection_count=job_data.params.get("source_selection_count"),
            selection_source_type=job_data.params.get("selection_source_type"),
            selection_source_job_id=job_data.params.get("selection_source_job_id"),
        )
        db.add(job)
        await db.flush()
        if failure["at"] is not None and len(calls) == failure["at"]:
            raise RuntimeError("forced canonical job creation failure")
        return SimpleNamespace(id=_preallocated_job_id)

    monkeypatch.setattr(jobs, "create_job", create_job)
    yield SimpleNamespace(
        engine=engine,
        session=session,
        inputs_root=inputs_root,
        results_root=results_root,
        fastq=fastq,
        receipt_ids=receipt_ids,
        calls=calls,
        failure=failure,
    )
    await session.close()
    await engine.dispose()


def _submit_request(
    context: SimpleNamespace,
    *,
    key: str = "pooled-submit-key",
    sequences: tuple[int, ...] = (0, 1, 2),
    group: str | None = None,
) -> pooled.PooledReferenceAssignmentRequest:
    return pooled.PooledReferenceAssignmentRequest(
        idempotency_key=key,
        fastq_path=str(context.fastq),
        min_mapq=20,
        min_alignment_score_margin=10,
        name="Pooled review",
        pinned_gpu=2,
        targets=[
            {
                "target_id": f"target-{chr(ord('a') + ordinal)}",
                "label": f"Target {ordinal + 1}",
                "indistinguishable_group": group,
                "molbio_ngs_receipt_id": context.receipt_ids[receipt_index],
            }
            for ordinal, receipt_index in enumerate(sequences)
        ],
    )


async def _submit(context: SimpleNamespace, request: pooled.PooledReferenceAssignmentRequest):
    response = Response()
    result = await pooled.submit_pooled_reference_assignment(
        session=context.session,
        request=request,
        background_tasks=BackgroundTasks(),
        http_request=_http_request("/api/ont/ngs/pooled-reference-assignment/submit"),
        response=response,
    )
    context.last_response = response
    return result


def _write_assignment_summary(
    context: SimpleNamespace,
    submit_result: dict[str, Any],
    *,
    target_counts: dict[str, int] | None = None,
    break_count_closure: bool = False,
) -> Path:
    assignment_job_id = submit_result["assignment_job_id"]
    manifest = submit_result["manifest"]
    output = context.results_root / assignment_job_id / "pooled_reference_assignment"
    output.mkdir(parents=True, exist_ok=True)
    targets = [entry["target_id"] for entry in manifest["entries"]]
    if target_counts is None:
        target_counts = {target_id: (1 if index < 2 else 0) for index, target_id in enumerate(targets)}
    occurrences: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    ordinal = 0
    for target_id in targets:
        records: list[str] = []
        ids: list[str] = []
        for _ in range(target_counts[target_id]):
            ordinal += 1
            occurrence_id = f"occurrence_{ordinal}"
            source_header = ("read-a note", "read-b")[ordinal - 1]
            source_id = source_header.split()[0]
            occurrences.append(
                {
                    "occurrence_id": occurrence_id,
                    "source_read_id": source_id,
                    "source_header": source_header,
                    "input_ordinal": ordinal,
                }
            )
            assignments.append(
                {
                    "occurrence_id": occurrence_id,
                    "source_read_id": source_id,
                    "source_header": source_header,
                    "input_ordinal": ordinal,
                    "disposition": f"target:{target_id}",
                    "target_id": target_id,
                    "reason": "unique_competitive_alignment",
                }
            )
            ids.append(occurrence_id)
            records.append(f"@{occurrence_id}\nACGT\n+\nIIII\n")
        (output / f"target_{target_id}.read_ids.txt").write_text(
            "".join(f"{value}\n" for value in ids), encoding="utf-8"
        )
        (output / f"target_{target_id}.fastq").write_text("".join(records), encoding="ascii")

    occurrence_payload = {
        "schema": "bms.ngs.fastq-occurrence-map.v1",
        "input_fastq_filename": context.fastq.name,
        "input_fastq_sha256": _sha256_bytes(context.fastq.read_bytes()),
        "count": len(occurrences),
        "records": occurrences,
    }
    occurrence_path = output / "occurrence_map.json"
    occurrence_path.write_text(json.dumps(occurrence_payload, sort_keys=True), encoding="utf-8")
    occurrence_sha256 = _sha256_bytes(occurrence_path.read_bytes())

    per_read_path = output / "per_read_assignment.tsv"
    with per_read_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(
            [
                "occurrence_id",
                "source_read_id",
                "source_header",
                "input_ordinal",
                "disposition",
                "target_id",
                "best_alignment_score",
                "second_alignment_score",
                "alignment_score_delta",
                "best_mapq",
                "reason",
            ]
        )
        for row in assignments:
            writer.writerow(
                [
                    row["occurrence_id"],
                    row["source_read_id"],
                    row["source_header"],
                    row["input_ordinal"],
                    row["disposition"],
                    row["target_id"],
                    100,
                    "",
                    "",
                    60,
                    row["reason"],
                ]
            )

    valid = len(assignments)
    disposition_counts = {
        f"target:{target_id}": count for target_id, count in target_counts.items() if count
    }
    assigned = sum(target_counts.values())
    targets_summary = []
    entry_by_id = {entry["target_id"]: entry for entry in manifest["entries"]}
    for target_id in targets:
        entry = entry_by_id[target_id]
        targets_summary.append(
            {
                "target_id": target_id,
                "label": entry["label"],
                "molbio_sequence_id": entry["molbio_sequence_id"],
                "molbio_revision_id": entry["molbio_revision_id"],
                "revision_sha256": entry["revision_sha256"],
                "indistinguishable_group": entry.get("indistinguishable_group"),
                "read_count": target_counts[target_id],
                "read_ids_path": f"target_{target_id}.read_ids.txt",
                "fastq_path": f"target_{target_id}.fastq",
            }
        )
    summary = {
        "schema": "bms.ngs.pooled-reference-assignment-summary.v1",
        "workflow_id": "ont_pooled_reference_assignment",
        "mode": "pooled",
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": manifest["manifest_sha256"],
        "scientific_status": "REVIEW",
        "release_state": "awaiting_operator_release",
        "policy": {
            "fastq_input_policy": "strict",
            "min_mapq": 20,
            "min_alignment_score_margin": 10,
            "minimap2_preset": "map-ont",
            "secondary_alignments": "retained",
            "identical_targets": "ambiguous_at_individual_target_level",
            "occurrence_id_policy": "synthetic_occurrence_id_from_one_based_input_ordinal",
        },
        "counts": {
            "input_fastq_records": valid,
            "valid_fastq_reads": valid,
            "occurrence_map_count": valid,
            "rejected_by_input_policy": 0,
            "target_assigned_reads": assigned + (1 if break_count_closure else 0),
            "ambiguous_reads": 0,
            "unclassified_reads": 0,
        },
        "disposition_counts": disposition_counts,
        "accounting": {
            "valid_fastq_reads": valid,
            "occurrence_map_count": valid,
            "sum_of_dispositions": assigned,
            "input_fastq_records": valid,
            "valid_plus_rejected": valid,
            "occurrence_map_matches_valid_fastq_reads": True,
            "closure": True,
        },
        "occurrence_map_path": "occurrence_map.json",
        "occurrence_map_sha256": occurrence_sha256,
        "occurrence_map_count": valid,
        "read_assignments": assignments,
        "targets": targets_summary,
        "artifacts": {
            "per_read_assignment": "per_read_assignment.tsv",
            "fastq_preflight": "fastq_preflight.json",
            "occurrence_map": "occurrence_map.json",
            "combined_reference": "combined_intended_reference.fasta",
            "combined_reference_index": "combined_intended_reference.fasta.fai",
            "alignment_bam": "pooled_assignment.bam",
            "alignment_bai": "pooled_assignment.bam.bai",
            "alignment_log": "pooled_reference_assignment.minimap2.log",
            "ambiguous_read_ids": "ambiguous.read_ids.txt",
            "ambiguous_fastq": "ambiguous.fastq",
            "unclassified_read_ids": "unclassified.read_ids.txt",
            "unclassified_fastq": "unclassified.fastq",
            "igv_session": "intended_pool.igv_session.json",
        },
    }
    summary_path = output / "assignment_summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")
    return summary_path


@pytest.mark.asyncio
async def test_submit_success_replay_conflict_and_manifest_targets(pooled_context) -> None:
    context = pooled_context
    request = _submit_request(context)
    first = await _submit(context, request)
    replay = await _submit(context, request)

    assert replay == first
    assert len(context.calls) == 1
    assert first["manifest"]["schema"] == "bms.ngs.reference-set.v1"
    assert first["manifest"]["mode"] == "pooled"
    assert first["manifest"]["manifest_id"] == first["reference_set_id"]
    assert first["manifest"]["manifest_sha256"] == first["manifest_sha256"]
    assert context.calls[0]["job"].mode == "pooled_reference_assignment"
    assert context.calls[0]["job"].params["fastq_sha256"] == _sha256_bytes(context.fastq.read_bytes())
    assert context.calls[0]["job"].params["scientific_status"] == "REVIEW"
    assert context.calls[0]["job"].params["release_state"] == "awaiting_operator_release"

    manifests = (await context.session.execute(select(NgsReferenceSetManifest))).scalars().all()
    targets = (await context.session.execute(select(NgsPooledReferenceTarget))).scalars().all()
    assert len(manifests) == 1
    assert len(targets) == 3
    assert all(
        (Path(manifests[0].manifest_path).parent / target.fasta_path).is_file()
        for target in targets
    )
    receipts = (await context.session.execute(select(MolBioNgsReceipt))).scalars().all()
    assert all(receipt.consumed_at is not None for receipt in receipts)
    assert [receipt.consumed_job_id for receipt in receipts].count(first["assignment_job_id"]) == 1

    manifest_result = await pooled.get_pooled_assignment_manifest(
        context.session, assignment_job_id=first["assignment_job_id"]
    )
    target_result = await pooled.get_pooled_assignment_targets(
        context.session, assignment_job_id=first["assignment_job_id"]
    )
    assert manifest_result["assignment_job_id"] == first["assignment_job_id"]
    assert manifest_result["reference_set_id"] == first["reference_set_id"]
    assert manifest_result["scientific_status"] == "REVIEW"
    assert manifest_result["execution_status"] == "queued"
    assert manifest_result["manifest"] == first["manifest"]
    assert [row["target_id"] for row in target_result["targets"]] == [
        "target-a",
        "target-b",
        "target-c",
    ]

    conflicting = request.model_copy(update={"min_mapq": 21})
    with pytest.raises(pooled.PooledAssignmentError) as raised:
        await _submit(context, conflicting)
    assert raised.value.status_code == 409
    assert raised.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_submit_rollback_removes_staging_job_and_receipt_consumption(pooled_context) -> None:
    context = pooled_context
    context.failure["at"] = 1
    with pytest.raises(pooled.PooledAssignmentError, match="rolled back"):
        await _submit(context, _submit_request(context))
    assert (await context.session.execute(select(Job))).scalars().all() == []
    assert (await context.session.execute(select(NgsReferenceSetManifest))).scalars().all() == []
    assert (await context.session.execute(select(NgsPooledReferenceTarget))).scalars().all() == []
    receipts = (await context.session.execute(select(MolBioNgsReceipt))).scalars().all()
    assert all(receipt.consumed_at is None and receipt.consumed_job_id is None for receipt in receipts)
    staged_root = context.inputs_root / pooled.REFERENCE_SET_ROOT_NAME
    assert not staged_root.exists() or not any(staged_root.iterdir())
    assert not any(context.results_root.iterdir())


@pytest.mark.asyncio
async def test_identical_sequences_require_one_explicit_common_group(pooled_context) -> None:
    context = pooled_context
    receipt_two = await context.session.get(MolBioNgsReceipt, context.receipt_ids[1])
    receipt_one = await context.session.get(MolBioNgsReceipt, context.receipt_ids[0])
    source = Path(receipt_one.reference_snapshot_path)
    target = Path(receipt_two.reference_snapshot_path)
    target.write_bytes(source.read_bytes())
    receipt_two.revision_sha256 = receipt_one.revision_sha256
    receipt_two.reference_snapshot_sha256 = receipt_one.reference_snapshot_sha256
    await context.session.commit()

    with pytest.raises(pooled.PooledAssignmentError, match="indistinguishable_group"):
        await _submit(context, _submit_request(context, sequences=(0, 1)))
    grouped = await _submit(
        context,
        _submit_request(context, key="grouped-identical", sequences=(0, 1), group="same-sequence"),
    )
    assert {entry["indistinguishable_group"] for entry in grouped["manifest"]["entries"]} == {
        "same-sequence"
    }


@pytest.mark.asyncio
async def test_submit_rejects_fastq_escape_symlink_and_receipt_tamper(pooled_context, tmp_path: Path) -> None:
    context = pooled_context
    outside = tmp_path / "outside.fastq"
    outside.write_text("@x\nACGT\n+\nIIII\n", encoding="ascii")
    escaped = _submit_request(context).model_copy(update={"fastq_path": str(outside)})
    with pytest.raises(pooled.PooledAssignmentError, match="confined"):
        await _submit(context, escaped)

    link = context.inputs_root / "uploads" / "link.fastq"
    link.symlink_to(context.fastq)
    linked = _submit_request(context, key="symlink").model_copy(update={"fastq_path": str(link)})
    with pytest.raises(pooled.PooledAssignmentError, match="symlink"):
        await _submit(context, linked)

    receipt = await context.session.get(MolBioNgsReceipt, context.receipt_ids[0])
    Path(receipt.reference_snapshot_path).write_text(">tampered\nTTTT\n", encoding="ascii")
    with pytest.raises(pooled.PooledAssignmentError, match="digest"):
        await _submit(context, _submit_request(context, key="tamper"))


@pytest.mark.asyncio
async def test_release_rejects_summary_count_closure_and_zero_read_target(pooled_context) -> None:
    context = pooled_context
    submitted = await _submit(context, _submit_request(context))
    assignment = await context.session.get(Job, submitted["assignment_job_id"])
    assignment.status = JobStatus.COMPLETED.value
    await context.session.commit()

    summary = _write_assignment_summary(context, submitted, break_count_closure=True)
    request = pooled.PooledAssignmentReleaseRequest(
        idempotency_key="release-count-bad",
        target_workflow="ont_plasmid_qc",
        target_ids=["target-a"],
    )
    with pytest.raises(pooled.PooledAssignmentError, match="arithmet"):
        await pooled.release_pooled_assignment(
            session=context.session,
            assignment_job_id=submitted["assignment_job_id"],
            request=request,
            background_tasks=BackgroundTasks(),
            http_request=_http_request(f"/api/jobs/{submitted['assignment_job_id']}/pooled-assignment/release"),
            response=Response(),
        )

    _write_assignment_summary(context, submitted, target_counts={"target-a": 1, "target-b": 0, "target-c": 1})
    zero = request.model_copy(update={"idempotency_key": "release-zero", "target_ids": ["target-b"]})
    with pytest.raises(pooled.PooledAssignmentError, match="non-empty"):
        await pooled.release_pooled_assignment(
            session=context.session,
            assignment_job_id=submitted["assignment_job_id"],
            request=zero,
            background_tasks=BackgroundTasks(),
            http_request=_http_request(f"/api/jobs/{submitted['assignment_job_id']}/pooled-assignment/release"),
            response=Response(),
        )


@pytest.mark.asyncio
async def test_release_rejects_summary_symlink_and_occurrence_tamper(pooled_context) -> None:
    context = pooled_context
    submitted = await _submit(context, _submit_request(context))
    assignment = await context.session.get(Job, submitted["assignment_job_id"])
    assignment.status = JobStatus.COMPLETED.value
    await context.session.commit()
    summary = _write_assignment_summary(context, submitted)
    real_summary = summary.with_name("real-summary.json")
    summary.replace(real_summary)
    summary.symlink_to(real_summary)
    request = pooled.PooledAssignmentReleaseRequest(
        idempotency_key="release-symlink",
        target_workflow="ont_plasmid_qc",
        target_ids=["target-a"],
    )
    with pytest.raises(pooled.PooledAssignmentError, match="symlink"):
        await pooled.release_pooled_assignment(
            session=context.session,
            assignment_job_id=submitted["assignment_job_id"],
            request=request,
            background_tasks=BackgroundTasks(),
            http_request=_http_request("/api/jobs/assignment/pooled-assignment/release"),
            response=Response(),
        )

    summary.unlink()
    real_summary.replace(summary)
    occurrence = summary.parent / "occurrence_map.json"
    payload = json.loads(occurrence.read_text(encoding="utf-8"))
    payload["records"][0]["source_read_id"] = "forged"
    occurrence.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(pooled.PooledAssignmentError, match="occurrence-map digest"):
        await pooled.release_pooled_assignment(
            session=context.session,
            assignment_job_id=submitted["assignment_job_id"],
            request=request.model_copy(update={"idempotency_key": "release-occurrence-tamper"}),
            background_tasks=BackgroundTasks(),
            http_request=_http_request("/api/jobs/assignment/pooled-assignment/release"),
            response=Response(),
        )


@pytest.mark.asyncio
async def test_atomic_child_release_preserves_bindings_and_replays(pooled_context) -> None:
    context = pooled_context
    submitted = await _submit(context, _submit_request(context))
    assignment = await context.session.get(Job, submitted["assignment_job_id"])
    assignment.status = JobStatus.COMPLETED.value
    await context.session.commit()
    _write_assignment_summary(context, submitted, target_counts={"target-a": 1, "target-b": 1, "target-c": 0})
    request = pooled.PooledAssignmentReleaseRequest(
        idempotency_key="release-success",
        target_workflow="ont_plasmid_qc",
        target_ids=["target-a", "target-b"],
        name_prefix="Released pooled target",
        pinned_gpu=1,
    )
    response = Response()
    first = await pooled.release_pooled_assignment(
        session=context.session,
        assignment_job_id=submitted["assignment_job_id"],
        request=request,
        background_tasks=BackgroundTasks(),
        http_request=_http_request(f"/api/jobs/{submitted['assignment_job_id']}/pooled-assignment/release"),
        response=response,
    )
    replay = await pooled.release_pooled_assignment(
        session=context.session,
        assignment_job_id=submitted["assignment_job_id"],
        request=request,
        background_tasks=BackgroundTasks(),
        http_request=_http_request(f"/api/jobs/{submitted['assignment_job_id']}/pooled-assignment/release"),
        response=Response(),
    )
    assert replay == first
    assert len(first["child_job_ids"]) == 2
    assert len(context.calls) == 3
    releases = (await context.session.execute(select(NgsPooledAssignmentRelease))).scalars().all()
    rows = (await context.session.execute(select(NgsPooledAssignmentReleaseTarget))).scalars().all()
    assert len(releases) == 1
    assert len(rows) == 2
    children = (await context.session.execute(select(Job).where(Job.id.in_(first["child_job_ids"])))).scalars().all()
    assert all(child.parent_job_id == submitted["assignment_job_id"] for child in children)
    assert all(child.params["run_fastq_qc"] is True for child in children)
    assert all(child.params["pooled_assignment_target_binding"]["release_id"] == first["release_id"] for child in children)
    assert all(child.params["molbio_revision_binding"]["revision_sha256"] for child in children)
    assert all(child.params["reference_set_binding"]["manifest_sha256"] == submitted["manifest_sha256"] for child in children)

    with pytest.raises(pooled.PooledAssignmentError) as conflict:
        await pooled.release_pooled_assignment(
            session=context.session,
            assignment_job_id=submitted["assignment_job_id"],
            request=request.model_copy(update={"target_ids": ["target-a"]}),
            background_tasks=BackgroundTasks(),
            http_request=_http_request("/api/jobs/assignment/pooled-assignment/release"),
            response=Response(),
        )
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_release_child_failure_rolls_back_all_children_and_release_rows(pooled_context) -> None:
    context = pooled_context
    submitted = await _submit(context, _submit_request(context))
    assignment = await context.session.get(Job, submitted["assignment_job_id"])
    assignment.status = JobStatus.COMPLETED.value
    await context.session.commit()
    _write_assignment_summary(context, submitted, target_counts={"target-a": 1, "target-b": 1, "target-c": 0})
    context.failure["at"] = 3
    request = pooled.PooledAssignmentReleaseRequest(
        idempotency_key="release-rollback",
        target_workflow="ont_plasmid_qc",
        target_ids=["target-a", "target-b"],
    )
    with pytest.raises(pooled.PooledAssignmentError, match="rolled back"):
        await pooled.release_pooled_assignment(
            session=context.session,
            assignment_job_id=submitted["assignment_job_id"],
            request=request,
            background_tasks=BackgroundTasks(),
            http_request=_http_request("/api/jobs/assignment/pooled-assignment/release"),
            response=Response(),
        )
    assert (await context.session.execute(select(NgsPooledAssignmentRelease))).scalars().all() == []
    assert (await context.session.execute(select(NgsPooledAssignmentReleaseTarget))).scalars().all() == []
    children = (await context.session.execute(select(Job).where(Job.id != submitted["assignment_job_id"]))).scalars().all()
    assert children == []


def test_pooled_routes_are_wired_on_strict_router_surfaces() -> None:
    ont_paths = {route.path for route in ont_runs.router.routes}
    job_paths = {route.path for route in ont_runs.barcode_router.routes}
    assert "/ngs/pooled-reference-assignment/submit" in ont_paths
    assert "/{assignment_job_id}/pooled-assignment/manifest" in job_paths
    assert "/{assignment_job_id}/pooled-assignment/targets" in job_paths
    assert "/{assignment_job_id}/pooled-assignment/release" in job_paths

    with pytest.raises(Exception):
        pooled.PooledReferenceAssignmentRequest.model_validate(
            {
                "idempotency_key": "strict",
                "fastq_path": "/inputs/reads.fastq",
                "targets": [],
                "min_mapq": 61,
                "min_alignment_score_margin": -1,
                "unknown": True,
            }
        )
