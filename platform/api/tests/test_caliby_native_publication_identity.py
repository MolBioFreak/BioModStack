"""Initial association tests; real serializers/finalizer, inert science only."""
import copy
import hashlib
import json
import shutil

import pytest
from sqlalchemy import func, select

from database import Job, JobArtifact
from services.caliby_native_publication import read_published_native_results
from services.result_state_integrity import finalize_successful_job
from tests import test_sequence_native_publication as fixtures
from tests.test_sequence_native_publication import store, seed  # noqa: F401

MODES = ["ensemble_design", "sidechain_pack"]


def emitted_with_custody(tmp_path, monkeypatch, mode, empty):
    """Retain controller preparation independently, before worker deletion."""
    normalize = fixtures.normalize_request
    materialize = fixtures.materialize_request
    retained = tmp_path / "controller-prepared"

    def two_sources(task, params):
        params = copy.deepcopy(params)
        states = params["ensembles"][0]["states"] if task == "ensemble_design" else params["structures"]
        second = {**states[0], "state_id": "secondary"}
        states.append(second)
        return normalize(task, params)

    def capture(task, params, directory):
        result = materialize(task, params, directory)
        shutil.copytree(directory, retained)
        return result

    monkeypatch.setattr(fixtures, "normalize_request", two_sources)
    monkeypatch.setattr(fixtures, "materialize_request", capture)
    root, folder, primary, document, params = fixtures.emitted_result(
        tmp_path, monkeypatch, "caliby_experimental", mode, empty=empty)
    params["caliby_request_dir"] = str(retained)
    assert not (tmp_path / "worker").exists()
    return root, root / folder / primary, document, params, retained


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("mutation", ["selected_setting", "effective_setting", "selected_source", "source_order", "coherent_hash"])
async def test_first_association_rejects_contradiction(tmp_path, monkeypatch, store, mode, empty, mutation):
    root, path, document, params, retained = emitted_with_custody(tmp_path, monkeypatch, mode, empty)
    original_params = copy.deepcopy(params)
    if mutation == "selected_setting":
        params["temperature" if mode == "ensemble_design" else "scn_step_scale"] = 0.73
    elif mutation == "effective_setting":
        document["request"]["effective"]["scn_num_steps"] += 1
    elif mutation == "selected_source":
        states = params["ensembles"][0]["states"] if mode == "ensemble_design" else params["structures"]
        states[0]["path"] = str(tmp_path / "another-source.cif")
    elif mutation == "source_order":
        document["request"]["sources"].reverse()
    else:
        # Foreign worker bytes and their self-consistent declared hash, with
        # unchanged selected params, cannot replace independent controller custody.
        foreign = tmp_path / "foreign-worker"
        shutil.copytree(retained, foreign)
        request = copy.deepcopy(document["request"])
        for source in request["sources"]:
            structure = foreign / source["path"]
            structure.write_text(fixtures.CIF + "# foreign source\n")
            source["sha256"] = hashlib.sha256(structure.read_bytes()).hexdigest()
        (foreign / "request.json").write_text(json.dumps(request))
        document["request"] = request
        shutil.rmtree(foreign)
        assert params == original_params
    path.write_text(json.dumps(document))
    # No input bytes are needed to compare retained hashes.
    shutil.rmtree(retained / "structures")
    await seed(store, root, "caliby_experimental", mode, params, remote=True)
    async with store() as session:
        job = await session.get(Job, "native")
        with pytest.raises(ValueError, match="Caliby"):
            await finalize_successful_job(job, str(root), session)
        await session.refresh(job)
        assert job.status != "completed"
        assert await session.scalar(select(func.count(JobArtifact.id))) == 0
    async with store() as session:
        job = await session.get(Job, "native")
        assert job.status != "completed"
        assert job.params == params
        assert await session.scalar(select(func.count(JobArtifact.id))) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("custody", ["request_only", "removed", "no_hashes", "unreadable"])
async def test_source_offline_completion_and_post_cleanup_reopen(tmp_path, monkeypatch, store, mode, empty, custody):
    root, path, document, params, retained = emitted_with_custody(tmp_path, monkeypatch, mode, empty)
    shutil.rmtree(retained / "structures")
    if custody == "removed":
        shutil.rmtree(retained)
    elif custody == "no_hashes":
        request = json.loads((retained / "request.json").read_text())
        for source in request["sources"]:
            source.pop("sha256")
        (retained / "request.json").write_text(json.dumps(request))
    elif custody == "unreadable":
        (retained / "request.json").write_text("{interrupted historical file")
    await seed(store, root, "caliby_experimental", mode, params)
    async with store() as session:
        job = await session.get(Job, "native")
        assert (await finalize_successful_job(job, str(root), session)).completed
        result = await read_published_native_results(job, session)
        assert result["request"] == document["request"]
        assert len(result["records"]) == (0 if empty else 1)
        ids = {row.id for row in (await session.scalars(select(JobArtifact))).all()}
    if retained.exists():
        shutil.rmtree(retained)
    async with store() as session:
        job = await session.get(Job, "native")
        assert (await read_published_native_results(job, session))["request"] == document["request"]
        job.status = job.queue_status = "running"
        await session.commit()
        assert (await finalize_successful_job(job, str(root), session)).completed
        assert (await read_published_native_results(job, session))["request"] == document["request"]
        assert {row.id for row in (await session.scalars(select(JobArtifact))).all()} == ids
        assert job.params == params


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", MODES)
async def test_absent_independent_hash_is_unavailable_not_a_gate(tmp_path, monkeypatch, store, mode):
    root, path, document, params, retained = emitted_with_custody(tmp_path, monkeypatch, mode, True)
    shutil.rmtree(retained)
    # A hash claimed only by a returned result cannot establish historical
    # source-byte identity. Do not manufacture proof or a new refusal.
    document["request"]["sources"][0]["sha256"] = "f" * 64
    path.write_text(json.dumps(document))
    await seed(store, root, "caliby_experimental", mode, params)
    async with store() as session:
        job = await session.get(Job, "native")
        assert (await finalize_successful_job(job, str(root), session)).completed
        assert (await read_published_native_results(job, session))["request"] == document["request"]
    # Once attached, existing JobArtifact custody still protects those bytes.
    document["request"]["sources"][0]["sha256"] = "e" * 64
    path.write_text(json.dumps(document))
    async with store() as session:
        job = await session.get(Job, "native")
        with pytest.raises(ValueError, match="artifact bytes"):
            await read_published_native_results(job, session)
