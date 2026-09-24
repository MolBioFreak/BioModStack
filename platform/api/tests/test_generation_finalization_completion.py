"""Real scratch-store terminal/selection owners; synthetic producer fixtures only."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from sqlalchemy import select

from database import Design, Job, JobArtifact
from test_project_manager_adapters import stores
from test_generation_publication_integration import pp_output
from test_boltzgen_candidate_accounting import published
from services import ppiflow_generation as pp
from services import boltzgen_candidate_publication as bg
from services.binder_diagnostic_selection import CandidateDocument, documents, selected_document
from services.result_state_integrity import finalize_successful_job, repair_result_state


CASES = [("ppiflow", mode) for mode in pp.MODES] + [
    ("boltzgen", "protein_binder"), ("boltzgen", "peptide_binder"),
    ("boltzgen", "nanobody_binder"),
]


def native_output(output, model, mode, zero):
    output.mkdir(parents=True)
    if model == "ppiflow":
        records = pp_output(output, 0 if zero else 2)
        directory = output / "ppiflow_generation"
        receipt_path = directory / "generation_receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["mode"] = mode
        receipt_path.write_text(json.dumps(receipt))
        for record in records:
            record["mode"] = mode
            (directory / (record["path"] + ".sample.json")).write_text(json.dumps(record))
        if records:
            (directory / "samples.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
    elif zero:
        from filter_boltzgen import run_strict_filter
        target = output / "collected/boltzgen_filtered"
        target.mkdir(parents=True)
        run_strict_filter(SimpleNamespace(
            pdbs=[], jsons=[], out_dir=str(target), filter_biased="false",
            metrics_override=None, additional_filters=None, size_buckets=None,
            boltzgen_min_plddt=None, boltzgen_min_conf_score=None,
            boltzgen_max_rmsd=None, budget=1, alpha=0,
        ))
    else:
        published(output)


def running_job(output, model, mode, *, remote=False):
    return Job(
        id="generation", name="fixture generation", model_id=model, mode=mode,
        params={}, output_dir=str(output), status="running", queue_status="running",
        awaiting_input=False, provenance={"core_protein_scientific_contract": 1},
        # Manual returned-result ingestion uses the actual existing CAS without
        # claiming a live lease, remote dispatch or native model execution.
        **({"execution_target_id": "fixture-worker", "remote_attempt_id": "fixture-attempt",
            "remote_state": "returning"} if remote else {}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model,mode", CASES)
@pytest.mark.parametrize("zero", [False, True])
@pytest.mark.parametrize("remote", [False, True])
async def test_native_finalizer_zero_yield_replay_reopen(stores, model, mode, zero, remote):
    root, _, core = stores
    output = root / "results/generation"
    native_output(output, model, mode, zero)
    read = pp.read_published_generation_results if model == "ppiflow" else bg.read_published_generation_results
    key = f"{model}_generation_publication"
    expected = 0 if zero else (2 if model == "ppiflow" else 1)
    async with core() as db:
        job = running_job(output, model, mode, remote=remote)
        db.add(job)
        await db.commit()
        result = await finalize_successful_job(job, str(output), db)
        assert result.completed, job.error_message
        assert result.design_count == expected
        assert job.status == job.queue_status == "completed"
        assert job.provenance["result_integrity"]["result_kind"] == key
        assert job.provenance["result_integrity"]["idempotent_prior_results"] is False
        if remote:
            assert job.remote_state == "ingested"
        first = await read(job, db)
        ids = set((await db.scalars(select(Design.id))).all())
        assert len(ids) == expected
        # Existing administrative repair must not reinterpret native zero yield
        # as a failed generic Design ingestion.
        report = await repair_result_state(db, apply=False)
        assert not [c for c in report.changes if c.record_id == job.id]
        job.status = job.queue_status = "running"
        if remote:
            job.remote_state = "returning"
        await db.commit()
        replay = await finalize_successful_job(job, str(output), db)
        assert replay.completed, job.error_message
        assert job.provenance["result_integrity"]["idempotent_prior_results"] is True
        assert (await read(job, db)) == first
        assert set((await db.scalars(select(Design.id))).all()) == ids
    # A new session, without a worker or original input files, reopens the
    # controller-owned publication and its exact native metrics/documents.
    async with core() as db:
        reopened = await db.get(Job, "generation")
        assert (await read(reopened, db)) == first
        assert reopened.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("model,mode", CASES)
async def test_initial_generation_explicit_document_selection(stores, model, mode):
    root, _, core = stores
    output = root / "results/generation"
    native_output(output, model, mode, False)
    publish = pp.publish_generation_results if model == "ppiflow" else bg.ingest
    async with core() as db:
        job = running_job(output, model, mode)
        db.add(job)
        await db.flush()
        await publish(job, output, db, commit=True)
        for design in list((await db.scalars(select(Design))).all()):
            before = (design.pdb_path, copy.deepcopy(design.provenance))
            declared = documents(job, design)
            assert len(declared) == 1
            doc = declared[0]
            assert doc["artifact_id"] == design.provenance["primary_artifact_id"]
            assert unquote(doc["download_url"]).endswith(doc["path"])
            exact, identity = await selected_document(
                job, design, CandidateDocument(artifact_id=doc["artifact_id"]), db,
            )
            artifact = await db.get(JobArtifact, doc["artifact_id"])
            assert exact == Path(artifact.storage_path) == Path(before[0])
            assert identity["artifact_sha256"] == doc["sha256"]
            assert identity["owner_job_id"] == job.id
            assert identity["producer_document"] == {k: v for k, v in doc.items() if k != "download_url"}
            assert (design.pdb_path, design.provenance) == before
            with pytest.raises(ValueError, match="producer-bound"):
                await selected_document(job, design, CandidateDocument(artifact_id="unjoined"), db)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["ppiflow", "boltzgen"])
@pytest.mark.parametrize("zero", [False, True])
async def test_native_publication_survives_existing_optional_failure(stores, monkeypatch, model, zero):
    from services import result_ingester
    root, _, core = stores
    output = root / "results/generation"
    native_output(output, model, "protein_binder", zero)
    read = pp.read_published_generation_results if model == "ppiflow" else bg.read_published_generation_results

    async def broken_attachment(*args, **kwargs):
        raise RuntimeError("fixture optional attachment failure")

    monkeypatch.setattr(result_ingester, "_ingest_explicit_frustrampnn_results", broken_attachment)
    async with core() as db:
        job = running_job(output, model, "protein_binder")
        job.stage_outputs = {"frustrampnn": ["fixture-invalid-manifest"]}
        db.add(job)
        await db.commit()
        result = await finalize_successful_job(job, str(output), db)
        assert not result.completed
        assert job.provenance["result_integrity"]["primary_validated"] is True
        assert job.provenance["result_integrity"]["failed_stage"] == "frustrampnn"
        await read(job, db)
        assert "fixture optional attachment failure" in job.error_message


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["ppiflow", "boltzgen"])
async def test_child_designs_do_not_change_native_generation_publication_count(stores, model):
    root, _, core = stores
    output = root / "results/generation"
    native_output(output, model, "protein_binder", True)
    async with core() as db:
        job = running_job(output, model, "protein_binder")
        child = Job(id="child", name="child", model_id="proteinmpnn", mode="design",
                    params={}, parent_job_id=job.id)
        db.add_all([job, child])
        await db.flush()
        db.add(Design(id="child-design", job_id=child.id, name="child", pdb_path="child.pdb"))
        await db.commit()
        result = await finalize_successful_job(job, str(output), db)
        assert result.completed, job.error_message
        assert result.design_count == 0
        assert await db.get(Design, "child-design") is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["ppiflow", "boltzgen"])
@pytest.mark.parametrize("state", ["cancelled", "awaiting_input"])
async def test_native_finalizer_preserves_existing_cancel_and_review(stores, model, state):
    root, _, core = stores
    output = root / "results/generation"
    # No output at all: the existing cancellation/review owner wins before ingestion.
    async with core() as db:
        job = running_job(output, model, "protein_binder")
        if state == "cancelled":
            job.status = job.queue_status = state
        else:
            job.awaiting_input = True
            job.awaiting_stage = "fixture-review"
            job.awaiting_payload = {"fixture": True}
        db.add(job)
        await db.commit()
        before = (job.status, job.queue_status, copy.deepcopy(job.provenance))
        result = await finalize_successful_job(job, str(output), db)
        assert not result.completed and result.integrity_state == state
        assert (job.status, job.queue_status, job.provenance) == before
        assert not list((await db.scalars(select(JobArtifact))).all())


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["ppiflow", "boltzgen"])
async def test_native_terminal_replay_rejects_changed_publication(stores, model):
    root, _, core = stores
    output = root / "results/generation"
    native_output(output, model, "protein_binder", True)
    async with core() as db:
        job = running_job(output, model, "protein_binder")
        db.add(job)
        await db.commit()
        assert (await finalize_successful_job(job, str(output), db)).completed
        receipt = output / ("ppiflow_generation/generation_receipt.json" if model == "ppiflow"
                            else "collected/boltzgen_filtered/filter_summary.json")
        receipt.write_bytes(receipt.read_bytes() + b" ")
        job.status = job.queue_status = "running"
        await db.commit()
        result = await finalize_successful_job(job, str(output), db)
        assert not result.completed and result.integrity_state == "ingestion_failed"
        assert job.status == "failed"
        assert f"{model}_generation_publication" in job.provenance
        assert not list((await db.scalars(select(Design))).all())


@pytest.mark.asyncio
@pytest.mark.parametrize("zero", [False, True])
async def test_historical_boltzgen_readback_stays_unmodified_and_paginated(stores, zero):
    root, _, core = stores
    output = root / "results/generation"
    native_output(output, "boltzgen", "protein_binder", zero)
    async with core() as db:
        job = running_job(output, "boltzgen", "protein_binder")
        # Existing child accounting path writes the original core-only contract;
        # this models historical rows without fabricating JobArtifact bindings.
        job.model_id = "boltzgen_child"
        db.add(job)
        await db.flush()
        await bg.ingest(job, output, db, commit=True)
        job.model_id = "boltzgen"
        await db.commit()
        before = copy.deepcopy(job.provenance)
        page = await bg.read_published_generation_results(job, db, offset=1, limit=1)
        assert page["offset"] == page["limit"] == 1
        assert page["total"] == (0 if zero else 3)
        assert len(page["records"]) == (0 if zero else 1)
        assert page["artifacts"] == []
        assert job.provenance == before
        assert not list((await db.scalars(select(JobArtifact))).all())
        result = await finalize_successful_job(job, str(output), db)
        assert result.completed, job.error_message
        assert job.provenance["result_integrity"]["idempotent_prior_results"] is True
        assert "boltzgen_generation_publication" not in job.provenance


def test_ppiflow_publication_reads_each_owned_file_once(tmp_path, monkeypatch):
    from collections import Counter
    output = tmp_path / "output"
    native_output(output, "ppiflow", "protein_binder", False)
    job = running_job(output, "ppiflow", "protein_binder")
    open_file = Path.open
    reads = Counter()

    def observed_open(path, mode="r", *args, **kwargs):
        if "r" in mode and path.is_relative_to(output):
            reads[path.relative_to(output / "ppiflow_generation").as_posix()] += 1
        return open_file(path, mode, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", observed_open)
        root, result, publication = pp._publication_input(job, output)
    assert result == pp.read_ppiflow_generation_result(root)
    assert reads == Counter({name: 1 for name in publication["files"]})
    for name, entry in publication["files"].items():
        import hashlib
        data = (root / name).read_bytes()
        assert entry == {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
