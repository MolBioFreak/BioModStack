"""Native completion fixtures: no scientific runs, network, or real database."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pysam
import pytest

from services import ont_ngs_completion as completion
from services.ont_ngs_contract import DORADO_LOCK_PATH


@pytest.fixture(autouse=True)
def isolated_result_root(monkeypatch, tmp_path):
    from services import job_result_roots
    monkeypatch.setattr(job_result_roots, "get_results_dir", lambda: tmp_path)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path, molecule="dna"):
    root = tmp_path / "result"
    base = root / "basecall"
    base.mkdir(parents=True)
    source = tmp_path / "inputs"
    source.mkdir()
    # Input identity is checked, not scientific POD5 decoding in the API.
    (source / "reads.pod5").write_bytes(b"fixture-input-identity")
    lock = json.loads(DORADO_LOCK_PATH.read_bytes())
    model = lock["models"][molecule]["sup"]
    params = {
        "ont_workflow_id": f"ont_basecall_{molecule}",
        "ont_request_workflow_id": f"ont_basecall_{molecule}",
        "ont_input_mode": "pod5", "pod5_dir": str(source),
        "ont_molecule_type": molecule, "dorado_quality_mode": "sup",
        "dorado_basecall_mode": "simplex", "modified_bases": "none",
        "dorado_resolved_model_id": model["id"],
        "dorado_lock_sha256": _sha(DORADO_LOCK_PATH),
        "dorado_batch_size": 64, "dorado_device": "cuda:0", "min_qscore": 10,
        "emit_moves": False, "emit_summary": False, "trim_adapters": True,
    }
    with pysam.AlignmentFile(str(base / "calls.bam"), "wb", header={"HD": {"VN": "1.6"}}) as bam:
        read = pysam.AlignedSegment()
        read.query_name = "read-1"
        read.query_sequence = "ACGT"
        read.query_qualities = pysam.qualitystring_to_array("IIII")
        read.flag = 4
        bam.write(read)
    preflight = {
        "schema": "biomodstack.dorado_preflight.v1",
        "lock": {"sha256": params["dorado_lock_sha256"]},
        "runtime": {"version": lock["dorado"]["version"], "sif_sha256": lock["dorado"]["sif_sha256"],
                    "assets": {"verified": True, "runtime_sif": {"sha256": lock["dorado"]["sif_sha256"]}, "models": {"base": model}}},
        "selection": {"molecule": molecule, "quality": "sup", "mode": "simplex", "model_id": model["id"],
                      "model_aggregate_sha256": model["aggregate_sha256"], "modified_bases": "none",
                      "modified_bases_model_id": None, "stereo_model_id": None},
        "inputs": {"root": str(source), "read_count": 1, "files": [
            {"relative_path": "reads.pod5", "sha256": _sha(source / "reads.pod5"), "bytes": (source / "reads.pod5").stat().st_size}]},
        "pairs": None, "barcoding": {"kit": None, "sample_sheet": None},
        "execution_policy": {"device": "cuda:0", "batch_size": 64, "min_qscore": 10},
    }
    (base / "dorado_preflight.json").write_text(json.dumps(preflight))
    runtime = {
        "schema": "biomodstack.dorado_runtime_provenance.v1",
        "mode": "simplex", "model_id": model["id"],
        "preflight_sha256": _sha(base / "dorado_preflight.json"),
        "runtime_sha256": lock["dorado"]["sif_sha256"], "emit_moves": False,
        "calls_bam": {"sha256": _sha(base / "calls.bam"), "read_count": 1,
                      "read_inventory_sha256": hashlib.sha256(b"read-1\n").hexdigest(),
                      "move_tags": {"mv": 0, "ts": 0, "ns": 0}, "duplex_dx1": 0},
        "network": "denied_by_namespace", "model_download": "denied_by_namespace_and_sealed_models",
    }
    (base / "dorado_runtime_provenance.json").write_text(json.dumps(runtime))
    (base / "basecall.log").write_text("")  # Native stderr may legitimately be empty.
    outputs = [str(base / name) for name in ("calls.bam", "basecall.log", "dorado_preflight.json", "dorado_runtime_provenance.json")]
    job = SimpleNamespace(id="native-job", model_id="nanopore", mode=f"basecall_{molecule}", params=params,
                          output_dir=str(root), child_output_dir=None, status="running", queue_status="running",
                          provenance={"prior": {"keep": True}, "stage_terminal_states": {"dorado_basecall": {"status": "complete", "outputs": outputs}}})
    return job, root


def _validate(job):
    validator = getattr(completion, "validate_and_prepare_ont_native_basecall_completion", None)
    assert callable(validator), "native basecalling completion entrypoint is missing"
    return asyncio.run(validator(job))


@pytest.mark.parametrize("molecule", ["dna", "rna"])
def test_native_unreferenced_basecall_accepts_real_bam_and_binds_authority(tmp_path, molecule):
    job, root = _fixture(tmp_path, molecule)
    result = _validate(job)
    assert job.status == "completed"
    assert result["state"] == "validated"
    assert result["workflow_id"] == f"ont_basecall_{molecule}"
    assert result["read_count"] == 1
    assert result["summary_state"] == "not_requested"
    assert result["calls_bam_sha256"] == _sha(root / "basecall/calls.bam")
    assert result["effective_params_sha256"] == hashlib.sha256(__import__("rfc8785").dumps(job.params)).hexdigest()
    assert job.provenance["prior"] == {"keep": True}
    assert job.provenance["result_integrity"] == result
    assert "alignment_presentations" not in result


@pytest.mark.parametrize("damage", ["missing_bam", "corrupt_bam", "missing_log", "bad_preflight", "bad_runtime", "wrong_reads", "wrong_inventory", "wrong_model", "wrong_molecule", "wrong_input", "input_changed", "extra_input", "wrong_settings", "missing_stage", "wrong_stage_paths", "symlink_bam", "requested_moves_missing"])
def test_native_basecall_rejects_invalid_products_without_publication(tmp_path, damage):
    job, root = _fixture(tmp_path)
    base = root / "basecall"
    runtime_path = base / "dorado_runtime_provenance.json"
    preflight_path = base / "dorado_preflight.json"
    runtime = json.loads(runtime_path.read_text())
    preflight = json.loads(preflight_path.read_text())
    if damage == "missing_bam": (base / "calls.bam").unlink()
    if damage == "corrupt_bam":
        (base / "calls.bam").write_bytes(b"not BAM")
        runtime["calls_bam"]["sha256"] = _sha(base / "calls.bam")
    if damage == "missing_log": (base / "basecall.log").unlink()
    if damage == "bad_preflight": preflight["schema"] = "wrong"
    if damage == "bad_runtime": runtime["schema"] = "wrong"
    if damage == "wrong_reads": runtime["calls_bam"]["read_count"] = 2
    if damage == "wrong_inventory": runtime["calls_bam"]["read_inventory_sha256"] = "f" * 64
    if damage == "wrong_model": runtime["model_id"] = "wrong"
    if damage == "wrong_molecule": preflight["selection"]["molecule"] = "rna"
    if damage == "wrong_input": preflight["inputs"]["root"] = str(tmp_path / "other")
    if damage == "input_changed": (Path(job.params["pod5_dir"]) / "reads.pod5").write_bytes(b"changed")
    if damage == "extra_input": (Path(job.params["pod5_dir"]) / "extra.pod5").write_bytes(b"extra")
    if damage == "wrong_settings": preflight["execution_policy"]["min_qscore"] = 11
    if damage == "missing_stage": job.provenance["stage_terminal_states"] = {}
    if damage == "wrong_stage_paths": job.provenance["stage_terminal_states"]["dorado_basecall"]["outputs"].pop()
    if damage == "symlink_bam":
        (base / "calls.bam").rename(tmp_path / "elsewhere.bam")
        (base / "calls.bam").symlink_to(tmp_path / "elsewhere.bam")
    if damage == "requested_moves_missing":
        job.params["emit_moves"] = True
        runtime["emit_moves"] = True
    preflight_path.write_text(json.dumps(preflight))
    runtime["preflight_sha256"] = _sha(preflight_path)
    runtime_path.write_text(json.dumps(runtime))
    before = copy.deepcopy(job.__dict__)
    with pytest.raises(completion.OntNgsCompletionError):
        _validate(job)
    assert job.__dict__ == before


@pytest.mark.parametrize("molecule", ["dna", "rna"])
@pytest.mark.parametrize("suffix", [".pod5", ".POD5", ".Pod5"])
def test_native_basecall_accepts_producer_pod5_suffix_and_preserves_identity(tmp_path, molecule, suffix):
    job, root = _fixture(tmp_path, molecule)
    source = Path(job.params["pod5_dir"])
    original_sha = _sha(source / "reads.pod5")
    renamed = source / f"reads{suffix}"
    (source / "reads.pod5").rename(renamed)
    # Exercise actual producer discovery; API fixtures do not decode scientific POD5.
    producer = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/dorado_p4_preflight.py"))
    files = producer["_pod5_files"](source, set())
    assert files == [renamed]
    preflight_path = root / "basecall/dorado_preflight.json"
    preflight = json.loads(preflight_path.read_text())
    preflight["inputs"]["files"] = [
        {"relative_path": path.relative_to(source).as_posix(),
         "sha256": producer["_sha256"](path), "bytes": path.stat().st_size}
        for path in files
    ]
    record = preflight["inputs"]["files"][0]
    assert record["relative_path"] == f"reads{suffix}"
    assert record["sha256"] == original_sha
    preflight_path.write_text(json.dumps(preflight))
    runtime_path = root / "basecall/dorado_runtime_provenance.json"
    runtime = json.loads(runtime_path.read_text())
    runtime["preflight_sha256"] = _sha(preflight_path)
    runtime_path.write_text(json.dumps(runtime))

    result = _validate(job)
    assert job.status == "completed"
    assert result["input_inventory_sha256"] == hashlib.sha256(__import__("rfc8785").dumps(preflight["inputs"])).hexdigest()
    assert result["preflight_sha256"] == _sha(preflight_path)
    assert renamed.name == record["relative_path"] and _sha(renamed) == original_sha


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("suffix", [".FAST5", ".txt", ".POD5.bak"])
def test_native_basecall_refuses_non_pod5_suffix(tmp_path, declared, suffix):
    from services.ont_ngs_native_completion import _validate_inputs
    job, root = _fixture(tmp_path)
    source = Path(job.params["pod5_dir"])
    other = source / f"other{suffix}"
    other.write_bytes(b"not-pod5")
    producer = runpy.run_path(str(Path(__file__).resolve().parents[3] / "scripts/dorado_p4_preflight.py"))
    with pytest.raises(ValueError, match="legacy FAST5|unsupported mixed file"):
        producer["_pod5_files"](source, set())
    preflight = json.loads((root / "basecall/dorado_preflight.json").read_text())
    if declared:
        preflight["inputs"]["files"].append(
            {"relative_path": other.name, "sha256": _sha(other), "bytes": other.stat().st_size})
    with pytest.raises(completion.OntNgsCompletionError, match="unsafe or duplicate path|unexpected file"):
        _validate_inputs(job.params, preflight)


def test_dispatch_rejects_conflicting_workflow_authority(tmp_path):
    job, _ = _fixture(tmp_path)
    job.params["workflow_id"] = "ont_methylation"
    dispatch = getattr(completion, "ont_completion_lane", None)
    assert callable(dispatch), "shared ONT completion dispatch is missing"
    with pytest.raises(completion.OntNgsCompletionError, match="identit"):
        dispatch(job)


@pytest.mark.parametrize("molecule", ["dna", "rna"])
def test_dispatch_routes_bounded_native_lane_and_leaves_pending_branches_explicit(tmp_path, molecule):
    job, _ = _fixture(tmp_path, molecule)
    dispatch = getattr(completion, "ont_completion_lane", None)
    assert callable(dispatch), "shared ONT completion dispatch is missing"
    assert dispatch(job) == "native_basecall"
    for key, value in (("barcode_kit", "SQK-RBK114-96"), ("dorado_basecall_mode", "duplex"), ("modified_bases", "6mA")):
        original = dict(job.params)
        job.params[key] = value
        assert dispatch(job) == ("native_basecall" if key in {"barcode_kit", "dorado_basecall_mode"} and molecule == "dna" else None)
        job.params = original


async def _nextflow_native_entry(job):
    from services import nextflow
    validator = getattr(nextflow, "_validate_ont_terminal_completion", None)
    assert callable(validator), "Nextflow shared ONT completion dispatch is missing"
    return await validator(job, None, None)


def test_nextflow_dispatch_exercises_native_barrier_without_derived_stores(tmp_path, monkeypatch):
    from services import ngs_alignment_sessions
    job, _ = _fixture(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("unreferenced scientific completion must not use presentation stores")
    monkeypatch.setattr(ngs_alignment_sessions, "build_alignment_sessions", forbidden)
    assert asyncio.run(_nextflow_native_entry(job)) is True
    assert job.status == "completed"
    job2, root2 = _fixture(tmp_path / "second")
    (root2 / "basecall/calls.bam").write_bytes(b"broken")
    with pytest.raises(completion.OntNgsCompletionError):
        asyncio.run(_nextflow_native_entry(job2))
    assert job2.status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("summary_support", [None, True, False])
async def test_native_completion_losing_cas_publishes_no_result(tmp_path, summary_support):
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Base, Job
    from services import nextflow
    native, root = _fixture(tmp_path)
    if summary_support is not None:
        from test_dorado_summary_emitter import emit_receipt
        native.params["emit_summary"] = True
        emitted = emit_receipt(root / "basecall", supported=summary_support)
        assert emitted.returncode == 0, emitted.stderr
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'native-cas.db'}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as seed:
            seed.add(Job(id=native.id, name="native CAS", mode=native.mode,
                         model_id=native.model_id, params=native.params, provenance=native.provenance,
                         output_dir=native.output_dir, status="running", queue_status="running",
                         paused=False, awaiting_input=False, awaiting_payload={}))
            await seed.commit()
        async with sessions() as worker:
            job = await worker.get(Job, native.id)
            snapshot = nextflow.capture_terminal_job_publication_snapshot(job)
            async with sessions() as winner:
                await winner.execute(update(Job).where(Job.id == native.id).values(status="cancelled", queue_status="cancelled"))
                await winner.commit()
            assert await _nextflow_native_entry(job) is True
            changes = {"status": job.status, "queue_status": job.queue_status, "provenance": job.provenance}
            worker.expunge(job)
            assert await nextflow.publish_terminal_job_changes_atomically(worker, job_id=native.id, snapshot=snapshot, changes=changes) == 0
        async with sessions() as verify:
            job = await verify.get(Job, native.id)
            assert job.status == "cancelled"
            assert job.provenance == native.provenance
            assert "result_integrity" not in job.provenance
    finally:
        await engine.dispose()
