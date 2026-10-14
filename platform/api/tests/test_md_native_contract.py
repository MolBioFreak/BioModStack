from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts.bms_md.contract import normalize_job_config, prepare_verified_worker_inputs, verify_input_snapshots
from scripts.bms_md.native_config import parse_mdp, render_mdp
from services.md import launch_contract as contract
from services.md.starting_structures import MdNativeLaunchIntent, compile_native_job_intent
from test_md_job_v2_contract import _catalog, ONE_AKI_FIXTURE


def _files(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, data in {
        "system.gro": b"inert non-protein coordinates with existing box\n",
        "system.top": b'#include "parts/surface.itp"\n[ system ]\nmixture\n',
        "parts/surface.itp": b"[ moleculetype ]\nSURF 3\n",
        "system.ndx": b"[ SURF ]\n1 2\n[ DNA ]\n3 4\n",
        "reference.gro": b"inert restraint reference\n",
        "system.tpr": b"inert compiled TPR bytes\x00\xff",
        "state.cpt": b"inert checkpoint bytes\x00\xff",
    }.items():
        path = root / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    return root


def _intent(root, *, compiled=False):
    return {
        "schema_version": "bms.md.launch-intent.v2", "name": "Native-system",
        "input": ({"kind": "compiled", "tpr": str(root / "system.tpr"), "checkpoint": str(root / "state.cpt")}
                  if compiled else {"kind": "prepared", "coordinates": str(root / "system.gro"),
                                    "topology": str(root / "system.top"), "index": str(root / "system.ndx"),
                                    "restraint_reference": str(root / "reference.gro")}),
        "replicas": 12,
        "stages": [] if compiled else [{"name": "adsorption", "mdp": {
            "integrator": "md", "dt": 0.001, "nsteps": 500_000_001,
            "tcoupl": "v-rescale", "ref-t": "310 295", "tc-grps": "DNA SOL",
            "pcoupl": "no", "pbc": "xy", "constraints": "h-bonds",
            "define": "-DPOSRES", "freezegrps": "SURF", "freezedim": "Y Y Y",
            "pull": "yes", "pull-ncoords": 2, "pull-coord1-type": "umbrella",
            "pull-coord1-geometry": "distance", "pull-coord1-k": 1000,
            "pull-coord2-type": "constraint", "pull-coord2-k": 0,
            "userint1": 41, "nstxout-compressed": 0,
        }}],
        "analysis": {"selections": {"surface": "resname SURF"}},
    }


def _spec(root, **kwargs):
    return compile_native_job_intent(MdNativeLaunchIntent.model_validate(_intent(root, **kwargs)))


@pytest.fixture
def native_files(tmp_path, monkeypatch):
    root = _files(tmp_path / "inputs")
    monkeypatch.setenv("BMS_FEATURE_MOLECULAR_DYNAMICS", "1")
    monkeypatch.setattr(contract, "_bound_engine_runtime_identity", lambda engine: {"image_name": "gromacs.sif", "sif_sha256": "a" * 64})
    return root


@pytest.mark.parametrize("compiled", [False, True])
def test_native_materialization_and_worker_relocation_preserve_bytes(native_files, tmp_path, compiled):
    spec = _spec(native_files, compiled=compiled)
    identity = contract.native_input_identity(spec)
    params = contract.materialize_md_job_spec(
        params={"md_job_spec": spec, "md_source_provenance": {"native_input_identity": identity}},
        job_id="native-test", output_dir=tmp_path / "results", resolve_runtime_path=lambda value: value,
    )
    normalized = params["md_job_spec"]
    assert normalized["stages"] == spec["stages"]
    assert normalized["replicas"] == 12
    assert "preparation" not in normalized and "chemistry" not in normalized
    assert normalize_job_config(normalized) == normalized
    verify_input_snapshots(normalized)
    worker = prepare_verified_worker_inputs(Path(params["md_job_config"]), tmp_path / "worker")
    verify_input_snapshots(worker)
    for field, value in spec["input"].items():
        assert Path(worker["input"][field]).read_bytes() == Path(value).read_bytes()
        assert Path(worker["input"][field]).is_relative_to(tmp_path / "worker")
    if not compiled:
        assert (Path(worker["input"]["topology"]).parent / "parts/surface.itp").read_bytes() == (native_files / "parts/surface.itp").read_bytes()
        assert worker["analysis"] == spec["analysis"]
    else:
        assert worker["stages"] == []
    Path(normalized["input"]["tpr" if compiled else "index"]).chmod(0o644)
    Path(normalized["input"]["tpr" if compiled else "index"]).write_bytes(b"changed")
    with pytest.raises(Exception, match="SNAPSHOT_MISMATCH"):
        verify_input_snapshots(normalized)


def test_mdp_import_has_one_settings_document_and_native_render(native_files):
    mdp = native_files / "import.mdp"
    mdp.write_text("; native import\nintegrator = md\nnsteps = -1 ; native indefinite\nref-t = 310 290\npull-coord2-k = 0\nuserreal1 = 1e-07\n")
    spec = _spec(native_files)
    spec["stages"] = [{"name": "run", "mdp_file": str(mdp)}]
    normalized = contract.normalize_md_job_spec(params={"md_job_spec": spec}, job_id="native-import", resolve_runtime_path=lambda value: value)
    entries = normalized["stages"][0]["mdp"]
    assert entries == parse_mdp(mdp.read_text())
    assert parse_mdp(render_mdp(entries)) == entries
    assert "mdp_file" not in normalized["stages"][0]
    assert entries["nsteps"] == "-1" and entries["userreal1"] == "1e-07"


def _guided_intent():
    catalog = _catalog()
    view = catalog.view()
    profile = view.get_profile("gmx_amber99sb_ildn_tip3p_smoke_v1")
    intent = _intent(ONE_AKI_FIXTURE.parent)
    intent["input"] = {
        "kind": "guided", "source_ref": {"kind": "managed_fixture", "id": "1aki-admitted-v1"},
        "expected_source_sha256": hashlib.sha256(ONE_AKI_FIXTURE.read_bytes()).hexdigest(),
        "chemistry_profile_id": profile["id"], "chemistry_profile_sha256": profile["profile_sha256"],
        "catalog_digest": view.catalog_digest, "padding_nm": 2.2, "salt_molar": 0.2, "neutralize": False,
    }
    return intent, catalog, profile


def test_guided_preparation_uses_native_protocol_without_smoke_caps():
    intent, catalog, profile = _guided_intent()
    spec = compile_native_job_intent(MdNativeLaunchIntent.model_validate(intent), profile=profile, source_token=str(ONE_AKI_FIXTURE))
    normalized = contract.normalize_md_job_spec(params={"md_job_spec": spec}, job_id="guided-native", resolve_runtime_path=lambda value: value, chemistry_catalog=catalog)
    assert normalized["stages"] == spec["stages"]
    assert normalized["replicas"] == 12
    assert normalized["preparation"]["neutralize"] is False
    assert normalized["chemistry"]["resolved_preparation"] == profile["v1_preparation"]
    assert normalize_job_config(normalized) == normalized


@pytest.mark.parametrize("change", ["compiled_mdp", "mixed_inputs", "unknown_outer", "forged_digest"])
def test_native_envelope_and_existing_ownership_rules(native_files, change):
    spec = _spec(native_files, compiled=change == "compiled_mdp")
    if change == "compiled_mdp":
        spec["stages"] = [{"name": "cannot_change_tpr", "mdp": {"nsteps": 1}}]
    elif change == "mixed_inputs":
        spec["input"]["structure"] = str(ONE_AKI_FIXTURE)
    elif change == "unknown_outer":
        spec["silently_ignored"] = True
    else:
        spec["input"]["coordinates_sha256"] = "f" * 64
    with pytest.raises((ValueError, contract.MDLaunchError)):
        contract.normalize_md_job_spec(params={"md_job_spec": spec}, job_id="invalid", resolve_runtime_path=lambda value: value)


@pytest.fixture
def native_client(native_files, tmp_path, monkeypatch):
    from database import get_session
    from routers import molecular_dynamics, jobs
    import paths
    roots = {"inputs": native_files, "bms_results": tmp_path / "results"}
    monkeypatch.setattr(paths, "get_allowed_roots", lambda: roots)
    monkeypatch.setattr(jobs, "get_allowed_roots", lambda: roots, raising=False)
    app = FastAPI()
    app.include_router(molecular_dynamics.router)
    async def unused_session():
        yield None
    app.dependency_overrides[get_session] = unused_session
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("compiled", [False, True])
def test_http_preview_launch_materializes_exact_native_document(native_client, native_files, tmp_path, monkeypatch, compiled):
    from routers import jobs
    intent = _intent(native_files, compiled=compiled)
    response = native_client.post("/api/molecular-dynamics/launch-preview", json={"schema_version": "bms.md.launch-preview-request.v1", "intent": intent})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["schema_version"] == "bms.md.launch-preview.v2"
    assert preview["effective_request"]["stages"] == intent["stages"]
    assert not (tmp_path / "results").exists()
    materialized = []
    class ReachedMaterializer(Exception):
        pass
    async def receiving_create(job_data, background_tasks, session, **kwargs):
        materialized.append(contract.materialize_md_job_spec(
            params=job_data.params, job_id="http-native", output_dir=tmp_path / "results",
            resolve_runtime_path=kwargs["_md_input_resolver"],
        ))
        raise ReachedMaterializer
    monkeypatch.setattr(jobs, "create_job", receiving_create)
    request = {"schema_version": "bms.md.launch-request.v1", "intent": intent, "preview_digest": preview["preview_digest"]}
    stale = copy.deepcopy(request)
    stale["intent"]["replicas"] += 1
    assert native_client.post("/api/molecular-dynamics/launch", json=stale).status_code == 409
    assert not materialized
    with pytest.raises(ReachedMaterializer):
        native_client.post("/api/molecular-dynamics/launch", json=request)
    saved = json.loads(Path(materialized[0]["md_job_config"]).read_text())
    assert saved["stages"] == intent["stages"]
    assert saved["analysis"] == intent["analysis"]
    verify_input_snapshots(saved)


def test_preview_binds_relative_includes_and_preserves_path_authority(native_client, native_files):
    intent = _intent(native_files)
    def preview():
        return native_client.post("/api/molecular-dynamics/launch-preview", json={"schema_version": "bms.md.launch-preview-request.v1", "intent": intent})
    first = preview()
    assert first.status_code == 200, first.text
    (native_files / "parts/surface.itp").write_text("changed include\n")
    second = preview()
    assert second.status_code == 200
    assert first.json()["preview_digest"] != second.json()["preview_digest"]
    intent["input"]["coordinates"] = "/etc/hosts"
    assert preview().status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["prepared", "compiled", "guided"])
async def test_native_launch_reaches_real_job_create_and_scratch_persistence(native_files, tmp_path, monkeypatch, mode):
    from database import Base, Job, MdRun
    from routers import jobs, molecular_dynamics as md
    from services import gpu_orchestrator
    from services.md.starting_structures import MdLaunchPreviewRequest, MdLaunchRequest
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from test_md_typed_launch import _AcceptingRegistry
    import paths

    results = tmp_path / "results"
    results.mkdir()
    roots = {"inputs": native_files, "bms_results": results}
    monkeypatch.setattr(paths, "get_allowed_roots", lambda: roots)
    monkeypatch.setattr(jobs, "get_allowed_roots", lambda: roots, raising=False)
    monkeypatch.setattr(jobs, "get_results_dir", lambda: results)
    monkeypatch.setattr(jobs, "get_registry", lambda: _AcceptingRegistry())
    monkeypatch.setattr(gpu_orchestrator, "estimate_vram", lambda *_args, **_kwargs: 0)
    for name, directory in {"BMS_DATA": "data", "BMS_RESULTS_DIR": "results", "BMS_WORK": "work", "BMS_STATE_DIR": "state", "BMS_SCHEDULER_STATE_DIR": "scheduler"}.items():
        monkeypatch.setenv(name, str(tmp_path / directory))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            intent = _intent(native_files, compiled=mode == "compiled")
            if mode == "guided":
                intent, catalog, _profile = _guided_intent()
                monkeypatch.setattr(md, "get_chemistry_catalog", lambda: catalog)
                monkeypatch.setattr(contract, "get_chemistry_catalog", lambda: catalog)
            preview = await md.preview_typed_md_launch(MdLaunchPreviewRequest.model_validate({
                "schema_version": "bms.md.launch-preview-request.v1", "intent": intent,
            }), session)
            response = await md.launch_typed_md_job(MdLaunchRequest.model_validate({
                "schema_version": "bms.md.launch-request.v1", "intent": intent,
                "preview_digest": preview.preview_digest,
            }), session)
            job_id = response.id
        async with sessions() as session:
            job = await session.get(Job, job_id)
            run = await session.get(MdRun, job_id)
            assert job.parent_job_id is None and run is not None
            assert job.params["md_job_spec"]["stages"] == intent["stages"]
            verify_input_snapshots(job.params["md_job_spec"])
    finally:
        await engine.dispose()
