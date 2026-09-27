"""Initial native association, using real inert emitters and scratch finalization.

No inference: the shared fixture owns transport-only science doubles. Retained
controller identity is distinct from the returned manifest's own declaration.
"""
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from sqlalchemy import func, select

from database import Job, JobArtifact
from services.ligandmpnn_design import MODES, prepare_design_request, prepare_for_job
from services.ligandmpnn_design_publication import read_published_native_results
from services.result_ingester import ingest_job_results
from services.result_state_integrity import finalize_successful_job
from tests import test_sequence_native_publication as fixtures
from tests.test_sequence_native_publication import store, seed


MODES = sorted(MODES)


def emitted(tmp_path, monkeypatch, mode, *, empty=False, prepared=True, controls=None):
    captured = {}

    def compile_request(selected_mode, params):
        params = {**params, **(controls or {})}
        if prepared:
            params.update(prepare_for_job(selected_mode, params, tmp_path / "controller",
                                          allowed_roots=[tmp_path]))
            request = json.loads(Path(params["ligandmpnn_design_request"]).read_text())
        else:
            request = prepare_design_request(selected_mode, params)
        captured.update(params)
        return request

    monkeypatch.setattr(fixtures, "prepare_design_request", compile_request)
    root, folder, primary, document, _ = fixtures.emitted_result(
        tmp_path, monkeypatch, "ligandmpnn", mode, empty=empty)
    return root, root / folder / primary, document, captured


async def assert_refused(store, root, *, match):
    # Exercise first association through the finalizer before an ingestion error
    # can already mark the Job failed and short-circuit finalization.
    async with store() as session:
        job = await session.get(Job, "native")
        result = await finalize_successful_job(job, str(root), session)
        assert not result.completed
        await session.refresh(job)
        assert match in job.error_message
        assert job.status != "completed"
        assert await session.scalar(select(func.count(JobArtifact.id))) == 0
    async with store() as session:
        with pytest.raises(ValueError, match=match):
            await ingest_job_results("native", str(root), session)
        assert await session.scalar(select(func.count(JobArtifact.id))) == 0
    async with store() as session:
        job = await session.get(Job, "native")
        result = await finalize_successful_job(job, str(root), session)
        assert not result.completed
        assert job.status != "completed"
        assert await session.scalar(select(func.count(JobArtifact.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("empty", [False, True], ids=["rows", "zero-yield"])
async def test_initial_settings_mismatch(tmp_path, monkeypatch, store, mode, empty):
    root, _, _, params = emitted(tmp_path, monkeypatch, mode, empty=empty)
    params["temperature"] = 0.73
    await seed(store, root, "ligandmpnn", mode, params)
    await assert_refused(store, root, match="scientific request")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("empty", [False, True], ids=["rows", "zero-yield"])
async def test_initial_different_requested_source(tmp_path, monkeypatch, store, mode, empty):
    root, _, _, params = emitted(tmp_path, monkeypatch, mode, empty=empty)
    params["target_pdb"] = str(tmp_path / "another-source.cif")
    await seed(store, root, "ligandmpnn", mode, params)
    await assert_refused(store, root, match="requested source")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("empty", [False, True], ids=["rows", "zero-yield"])
@pytest.mark.parametrize("authority", ["request", "input", "both"])
async def test_coherent_foreign_hash_against_controller_identity(
        tmp_path, monkeypatch, store, mode, empty, authority):
    root, _, document, params = emitted(tmp_path, monkeypatch, mode, empty=empty)
    # The return is a coherent, actually emitted native result. Only controller
    # inputs differ: same settings and claimed path, independently bound bytes.
    source = Path(params["ligandmpnn_design_input"])
    source.write_text(fixtures.CIF + "# independently retained controller input\n")
    request_path = Path(params["ligandmpnn_design_request"])
    retained = json.loads(request_path.read_text())
    retained["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    request_path.write_text(json.dumps(retained))
    assert retained["source"]["sha256"] != document["source_sha256"]
    if authority == "request":
        source.unlink()
    elif authority == "input":
        request_path.unlink()
    await seed(store, root, "ligandmpnn", mode, params)
    await assert_refused(store, root, match="source digest")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("empty", [False, True], ids=["rows", "zero-yield"])
@pytest.mark.parametrize("retention", ["prepared", "all-offline", "older-direct"])
async def test_correct_publication_and_offline_replay(
        tmp_path, monkeypatch, store, mode, empty, retention):
    controls = {"temperature": None, "design_seed": 0, "omit": [],
                "remove_waters": False, "bias": {}, "structure_noise": 0.0,
                "write_fasta": False, "write_structures": False, "ligand_smiles": None}
    root, _, document, params = emitted(tmp_path, monkeypatch, mode, empty=empty,
                                       prepared=retention != "older-direct", controls=controls)
    # Alias-only scientific source and framework fields must not introduce a
    # second interpretation of native defaults/null/false/empty containers.
    params["ligand_pdb"] = params.pop("target_pdb")
    params["framework_only"] = "not a scientific setting"
    assert not Path(params["ligand_pdb"]).exists()
    if retention == "all-offline":
        shutil.rmtree(tmp_path / "controller")
    await seed(store, root, "ligandmpnn", mode, params, remote=True)
    async with store() as session:
        job = await session.get(Job, "native")
        assert (await finalize_successful_job(job, str(root), session)).completed
        result = await read_published_native_results(job, session)
        assert result["request"] == document["request"]
        assert result["records"] == document["records"]
        ids = {a.logical_path: a.id for a in (await session.scalars(select(JobArtifact))).all()}
        assert ids
    if (tmp_path / "controller").exists():
        shutil.rmtree(tmp_path / "controller")
    async with store() as session:
        job = await session.get(Job, "native")
        assert await ingest_job_results(job.id, str(root), session) == 0
        job.status = job.queue_status = "running"
        job.remote_state = "returning"
        await session.commit()
        assert (await finalize_successful_job(job, str(root), session)).completed
        assert (await read_published_native_results(job, session))["request"] == document["request"]
        assert {a.logical_path: a.id for a in (await session.scalars(select(JobArtifact))).all()} == ids


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["declared-hash", "row-hash", "retained-settings"])
async def test_declared_contradictions_before_association(tmp_path, monkeypatch, store, mutation):
    root, manifest, document, params = emitted(tmp_path, monkeypatch, "ligand_aware")
    if mutation == "declared-hash":
        document["request"]["source"]["sha256"] = "0" * 64
    elif mutation == "row-hash":
        document["records"][0]["source_sha256"] = "0" * 64
    else:
        path = Path(params["ligandmpnn_design_request"])
        retained = json.loads(path.read_text())
        retained["options"]["temperature"] = 0.73
        path.write_text(json.dumps(retained))
    manifest.write_text(json.dumps(document))
    await seed(store, root, "ligandmpnn", "ligand_aware", params)
    await assert_refused(store, root, match="differs")
