"""Real publication/finalizer in scratch SQLite; all science below is inert.

Fixtures run production leaf serializers, never mock successful result APIs.
They are transport evidence only, not Caliby or LigandMPNN inference.
"""
import copy
import importlib.metadata
import json
from pathlib import Path
import runpy
import shutil
import sys
import types

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, ExecutionTarget, Job, JobArtifact, get_session
from services.caliby_native import materialize_request, normalize_request
from services.ligandmpnn_design import prepare_design_request
from services.result_ingester import ingest_job_results
from services.result_state_integrity import finalize_successful_job, job_expects_design_results
from routers import sequence_native, files

REPO = Path(__file__).resolve().parents[3]
# Deliberately inert single-atom transport structure, no scientific claim.
CIF = "data_inert_transport\n#\nloop_\n_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n_atom_site.label_atom_id\n_atom_site.label_comp_id\n_atom_site.label_asym_id\n_atom_site.label_seq_id\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\nATOM 1 C CA ALA A 1 0 0 0\n#\n"
MODES = [("caliby_experimental", mode) for mode in ("ensemble_design", "sidechain_pack")] + [
    ("ligandmpnn", mode) for mode in ("ligand_aware", "ntp_aware", "metal_aware", "dna_aware")]


@pytest_asyncio.fixture
async def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BMS_DATA_ROOT", str(tmp_path))
    # Governed files and publication resolve to the same scratch-only root.
    monkeypatch.setattr("paths.get_allowed_roots", lambda: {"bms_results": tmp_path})
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'native.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def emitted_result(tmp_path, monkeypatch, model, mode, *, empty=False, write_outputs=True):
    worker = tmp_path / "worker"
    worker.mkdir()
    source = worker / "source.cif"
    source.write_text(CIF)
    output = worker / "output"
    output.mkdir()
    if model == "caliby_experimental":
        params = ({"ensembles": [{"ensemble_id": "ensemble-one", "states": [
            {"state_id": "primary", "path": str(source)}]}], "omit_aas": [],
            "verbose": False, "potts_rejection_step": False, "gaussian_noise_std": 0.0}
            if mode == "ensemble_design" else {"structures": [{"state_id": "primary", "path": str(source)}]})
        params = normalize_request(mode, {**params, "num_workers": 0, "scn_step_scale": 0.0})
        prepared = worker / "prepared"
        materialize_request(mode, params, prepared)
        monkeypatch.syspath_prepend(str(REPO / "scripts"))
        runtime = runpy.run_path(str(REPO / "scripts/run_caliby_experimental.py"))
        # Only scientific model/preflight/config merge are inert. The real
        # source ledger, row serializer and portable native output writer run.
        class InertModel:
            sampling_cfg = {"inert_transport": True}
            def emit(self, out_dir):
                folder = Path(out_dir) / "packed_samples"
                folder.mkdir(parents=True)
                paths = []
                if not empty:
                    path = folder / "sample.cif"
                    path.write_text(CIF)
                    paths.append(str(path))
                result = {"example_id": [] if empty else ["state_000000"], "out_pdb": paths}
                if mode == "ensemble_design":
                    result.update(seq=[] if empty else ["A"], U=[] if empty else [-1.25],
                                  input_seq=[] if empty else ["A"])
                return result
            def ensemble_sample(self, mapping, *, out_dir, **kwargs):
                return self.emit(out_dir)
            def sidechain_pack(self, paths, *, out_dir, **kwargs):
                return self.emit(out_dir)
        api = types.ModuleType("caliby.api")
        api._merge_sampling_cfg = lambda cfg, **kwargs: {**cfg, **kwargs}
        package = types.ModuleType("caliby")
        package.api = api
        monkeypatch.setitem(sys.modules, "caliby", package)
        monkeypatch.setitem(sys.modules, "caliby.api", api)
        omega = types.ModuleType("omegaconf")
        omega.OmegaConf = types.SimpleNamespace(to_container=lambda cfg, **kw: cfg)
        monkeypatch.setitem(sys.modules, "omegaconf", omega)
        runtime["run"].__globals__.update(
            preflight_caliby_runtime=lambda **kw: {"model_name": "inert-transport", "task": mode},
            load_caliby_model=lambda _: InertModel())
        document = runtime["run"](prepared, output / "caliby_native")
        folder, primary = "caliby_native", "caliby_results.json"
    else:
        params = {"target_pdb": str(source), "design_seed": 0, "remove_ccds": [],
                  "remove_waters": None, "structure_noise": 0.0,
                  "write_fasta": write_outputs, "write_structures": write_outputs}
        request = prepare_design_request(mode, params)
        runtime = runpy.run_path(str(REPO / "scripts/run_ligandmpnn_design.py"))
        class InertResult:
            def __init__(self, inp):
                self.input_dict = copy.deepcopy(inp)
                self.output_dict = {"model_type": "ligand_mpnn", "batch_idx": 3,
                                    "design_idx": 7, "sequence": "A", "recovery": float("nan")}
            def write_structure(self, base_path):
                base_path.with_suffix(".cif").write_text(CIF)
            def write_fasta(self, base_path):
                base_path.with_suffix(".fa").write_text(">inert_transport\nA\n")
        class InertEngine:
            def __init__(self, **kwargs):
                pass
            def run(self, input_dicts, atom_arrays):
                return [] if empty else [InertResult(input_dicts[0])]
        original_version = importlib.metadata.version
        monkeypatch.setattr(importlib.metadata, "version", lambda name:
                            "inert-transport" if name == "rc-foundry" else original_version(name))
        document = runtime["execute"](request, source, output / "ligandmpnn_design", engine_class=InertEngine)
        folder, primary = "ligandmpnn_design", "manifest.json"
    # Simulate existing bridge's materialized returned root, then remove all
    # worker originals, source and prepared inputs before actual ingestion.
    returned = tmp_path / "returned"
    shutil.copytree(output, returned)
    shutil.rmtree(worker)
    return returned, folder, primary, json.loads((returned / folder / primary).read_text()), params


async def seed(store, root, model, mode, params, *, remote=False, status="running", awaiting=False):
    async with store() as session:
        if remote:
            session.add(ExecutionTarget(id="target", provider="vast", provider_instance_id="inert",
                                        name="inert target", state="ready"))
            await session.flush()
        job = Job(id="native", name="inert native transport", model_id=model, mode=mode,
                  output_dir=str(root), params=params, status=status, queue_status="running",
                  awaiting_input=awaiting, execution_target_id="target" if remote else None,
                  remote_state="returning" if remote else None,
                  remote_attempt_id="inert-return-attempt" if remote else None)
        session.add(job)
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("model,mode", MODES)
@pytest.mark.parametrize("empty,remote", [(False, False), (False, True), (True, False), (True, True)])
async def test_actual_finalizer_relocated_reopen_and_native_downloads(tmp_path, monkeypatch, store, model, mode, empty, remote):
    root, folder, primary, document, params = emitted_result(tmp_path, monkeypatch, model, mode, empty=empty)
    await seed(store, root, model, mode, params, remote=remote)
    async with store() as session:
        job = await session.get(Job, "native")
        assert not job_expects_design_results(job)
        result = await finalize_successful_job(job, str(root), session)
        assert result.completed, job.error_message
        assert result.design_count == 0
        assert job.status == "completed"
        if remote:
            assert job.remote_state == "ingested"
        assert job.provenance["result_integrity"]["idempotent_prior_results"] is False
        ids = {row.logical_path: row.id for row in (await session.scalars(select(JobArtifact))).all()}
        assert folder + "/" + primary in ids
        assert await session.scalar(select(func.count(Design.id))) == 0
    async with store() as session:
        # Real public ingester replay in a new transaction, without source files.
        assert await ingest_job_results("native", str(root), session) == 0
        assert {row.logical_path: row.id for row in (await session.scalars(select(JobArtifact))).all()} == ids
        job = await session.get(Job, "native")
        job.status = job.queue_status = "running"
        if remote:
            job.remote_state = "returning"
        await session.commit()
        result = await finalize_successful_job(job, str(root), session)
        assert result.completed
        assert job.provenance["result_integrity"]["idempotent_prior_results"] is True
    app = FastAPI()
    app.include_router(sequence_native.router, prefix="/api")
    app.include_router(files.router, prefix="/api/files")
    async def session_override():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[files.get_governed_ngs_result_roots] = lambda: ()
    endpoint = "caliby-native-results" if model == "caliby_experimental" else "ligandmpnn-design-results"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/jobs/native/{endpoint}")
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["mode"] == mode and result["request"] == document["request"]
        for key, value in document.items():
            if key != "records":
                assert result[key] == value
        for row, native in zip(result["records"], document["records"]):
            for key, value in native.items():
                if key != "artifacts":
                    assert row[key] == value
        assert len(result["records"]) == len(document["records"])
        for artifact in result["artifacts"]:
            assert artifact["relative_path"] == artifact["path"]
            streamed = await client.get(artifact["stream_url"])
            assert streamed.status_code == 200
            assert streamed.content == (root / folder / artifact["path"]).read_bytes()
            download = await client.get(artifact["download_url"])
            assert download.status_code == 200, download.text
            assert download.content == (root / folder / artifact["path"]).read_bytes()
            if artifact["path"].endswith(".cif"):
                assert download.content == CIF.encode()
                assert artifact["media_type"] == "chemical/x-mmcif"
        wrong = await client.get("/api/jobs/native/" + ("ligandmpnn-design-results" if model == "caliby_experimental" else "caliby-native-results"))
        assert wrong.status_code == 404


@pytest.mark.asyncio
async def test_output_writing_off_preserves_native_only_rows(tmp_path, monkeypatch, store):
    root, folder, primary, native, params = emitted_result(tmp_path, monkeypatch, "ligandmpnn", "ligand_aware", write_outputs=False)
    await seed(store, root, "ligandmpnn", "ligand_aware", params, remote=True)
    async with store() as session:
        job = await session.get(Job, "native")
        assert (await finalize_successful_job(job, str(root), session)).completed
        result = await sequence_native.get_ligandmpnn_design_results(job.id, session)
        assert result["records"] == native["records"]
        assert result["records"][0]["native_output"]["recovery"] is None
        assert len(result["artifacts"]) == 1
        assert result["request"]["write_structures"] is False
        assert result["request"]["options"]["seed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("model,mode", [MODES[0], MODES[2]])
@pytest.mark.parametrize("mutation", ["document", "structure", "registry", "symlink"])
async def test_registered_custody_rejects_changed_bytes(tmp_path, monkeypatch, store, model, mode, mutation):
    root, folder, primary, native, params = emitted_result(tmp_path, monkeypatch, model, mode)
    await seed(store, root, model, mode, params)
    async with store() as session:
        assert await ingest_job_results("native", str(root), session) == 0
    async with store() as session:
        job = await session.get(Job, "native")
        if mutation == "document":
            path = root / folder / primary
            path.write_text(path.read_text() + " ")
        elif mutation == "structure":
            path = next((root / folder).rglob("*.cif"))
            path.write_text(CIF + "# changed\n")
        elif mutation == "symlink":
            path = next((root / folder).rglob("*.cif"))
            outside = tmp_path / "outside.cif"
            outside.write_bytes(path.read_bytes())
            path.unlink()
            path.symlink_to(outside)
        else:
            row = (await session.scalars(select(JobArtifact))).first()
            row.sha256 = "0" * 64
            await session.commit()
        reader = sequence_native.get_caliby_native_results if model == "caliby_experimental" else sequence_native.get_ligandmpnn_design_results
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await reader(job.id, session)
        assert error.value.status_code == 409
        with pytest.raises(ValueError):
            await ingest_job_results(job.id, str(root), session)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,awaiting,expected", [("cancelled", False, "cancelled"), ("running", True, "awaiting_input")])
async def test_cancellation_and_review_precede_native_ingestion(tmp_path, store, status, awaiting, expected):
    root = tmp_path / "absent-output"
    await seed(store, root, "caliby_experimental", "sidechain_pack", {}, status=status, awaiting=awaiting)
    async with store() as session:
        job = await session.get(Job, "native")
        result = await finalize_successful_job(job, str(root), session)
        assert not result.completed and result.integrity_state == expected
        assert await session.scalar(select(func.count(JobArtifact.id))) == 0
