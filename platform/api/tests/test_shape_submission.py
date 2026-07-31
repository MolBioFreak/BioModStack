from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


CUBE_OBJ = b"""\
v -1 -1 -1
v 1 -1 -1
v 1 1 -1
v -1 1 -1
v -1 -1 1
v 1 -1 1
v 1 1 1
v -1 1 1
f 1 3 2
f 1 4 3
f 5 6 7
f 5 7 8
f 1 2 6
f 1 6 5
f 2 3 7
f 2 7 6
f 3 4 8
f 3 8 7
f 4 1 5
f 4 5 8
"""


async def _database(tmp_path: Path):
    database = importlib.import_module("database")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shape-request.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
    return database, engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_shape_request_materializes_closed_hash_bound_bundle(tmp_path: Path) -> None:
    database, engine, factory = await _database(tmp_path)
    resources = importlib.import_module("services.shape_resources")
    requests = importlib.import_module("services.shape_requests")
    request_type = getattr(database, "ShapeDesignRequest")
    assert "principal_id" not in request_type.__table__.columns

    try:
        async with factory() as session:
            geometry = await resources.admit_obj_geometry(
                session,
                data_root=tmp_path / "data",
                payload=CUBE_OBJ,
                filename="cube.obj",
                angstrom_per_unit=10.0,
            )
            submitted = requests.SubmittedShapeRequest(
                client_request_id="7efcaea8-4d75-4ca0-8e60-e3c834740983",
                name="small-shape-run",
                geometry_id=geometry.geometry_id,
                expected_geometry_sha256=geometry.geometry_sha256,
                expected_point_pool_sha256=geometry.point_pool_sha256,
                target_length=120,
                num_backbones=2,
                sequences_per_backbone=2,
                seed=42,
            )
            staged = await requests.materialize_shape_request(
                session,
                data_root=tmp_path / "data",
                submitted=submitted,
            )
            assert staged.model_id == "protein_modification_experimental"
            assert staged.mode == "shape_blueprint"
            assert set(staged.launch_params) >= {
                "shape_request_path",
                "shape_points_path",
                "shape_geometry_manifest_path",
            }
            assert not any(key.endswith("client_path") for key in staged.launch_params)
            stage_root = Path(staged.stage_dir).resolve()
            for raw_path in staged.launch_params.values():
                if isinstance(raw_path, str) and raw_path.startswith(str(stage_root)):
                    path = Path(raw_path)
                    assert path.is_file() and path.stat().st_nlink == 1
                    assert path.stat().st_mode & 0o222 == 0
            persisted = await session.get(request_type, staged.request_id)
            assert persisted is not None
            assert persisted.request_sha256 == staged.request_sha256
            assert persisted.job_id is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_shape_request_rejects_geometry_hash_mismatch_before_staging(tmp_path: Path) -> None:
    _, engine, factory = await _database(tmp_path)
    resources = importlib.import_module("services.shape_resources")
    requests = importlib.import_module("services.shape_requests")
    try:
        async with factory() as session:
            geometry = await resources.admit_obj_geometry(
                session,
                data_root=tmp_path / "data",
                payload=CUBE_OBJ,
                filename="cube.obj",
                angstrom_per_unit=10.0,
            )
            submitted = requests.SubmittedShapeRequest(
                client_request_id="55015f9d-6d67-4dd0-83b3-ec454f95ef75",
                name="hash-mismatch",
                geometry_id=geometry.geometry_id,
                expected_geometry_sha256="0" * 64,
                expected_point_pool_sha256=geometry.point_pool_sha256,
                target_length=100,
                num_backbones=1,
                sequences_per_backbone=1,
                seed=1,
            )
            with pytest.raises(requests.ShapeRequestError) as error:
                await requests.materialize_shape_request(
                    session,
                    data_root=tmp_path / "data",
                    submitted=submitted,
                )
            assert error.value.code == "geometry_hash_mismatch"
            assert not (tmp_path / "data" / "shape_blueprint" / "requests").exists()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_shape_request_translates_immutable_publication_conflict(tmp_path: Path, monkeypatch) -> None:
    _, engine, factory = await _database(tmp_path)
    resources = importlib.import_module("services.shape_resources")
    requests = importlib.import_module("services.shape_requests")
    try:
        async with factory() as session:
            geometry = await resources.admit_obj_geometry(
                session,
                data_root=tmp_path / "data",
                payload=CUBE_OBJ,
                filename="cube.obj",
                angstrom_per_unit=10.0,
            )
            submitted = requests.SubmittedShapeRequest(
                client_request_id="54c602f8-a20a-4458-952d-ff54dd5bc832",
                name="publication-conflict",
                geometry_id=geometry.geometry_id,
                expected_geometry_sha256=geometry.geometry_sha256,
                expected_point_pool_sha256=geometry.point_pool_sha256,
                target_length=100,
                num_backbones=1,
                sequences_per_backbone=1,
                seed=0,
            )
            monkeypatch.setattr(
                requests,
                "_publish",
                lambda path, payload: (_ for _ in ()).throw(
                    RuntimeError(f"immutable Shape artifact conflict: {path.name}")
                ),
            )
            with pytest.raises(requests.ShapeRequestError) as error:
                await requests.materialize_shape_request(
                    session,
                    data_root=tmp_path / "data",
                    submitted=submitted,
                )
            assert error.value.code == "request_id_conflict"
    finally:
        await engine.dispose()


def test_shape_mode_routes_only_to_shape_workflow() -> None:
    nextflow = importlib.import_module("services.nextflow")
    command = nextflow.build_nextflow_command(
        "protein_modification_experimental",
        "shape_blueprint",
        {
            "shape_request_path": "/server/stage/request.json",
            "shape_points_path": "/server/stage/points.f32le",
            "shape_request_sha256": "a" * 64,
            "msa_provider": "local",
        },
        "/server/results/shape",
        job_id="shape-job",
    )
    assert command[2] == "workflows/shape_blueprint_design.nf"
    joined = " ".join(command)
    assert "shape_blueprint,workstation_ryzen7960x" in joined
    assert "--shape_request_path /server/stage/request.json" in joined


def test_preallocated_shape_job_output_path_ignores_presentation_name(tmp_path: Path, monkeypatch) -> None:
    jobs = importlib.import_module("routers.jobs")
    monkeypatch.setattr(jobs, "get_results_dir", lambda: tmp_path / "results")
    job_id = "7eb4ccb6-4514-5fcb-a5c6-dcde3f7eb394"
    first = jobs._standard_job_output_dir("first-name", "20260730_235959", job_id)
    second = jobs._standard_job_output_dir("different-name", "20260731_000000", job_id)
    assert first == second == tmp_path / "results" / job_id


@pytest.mark.asyncio
async def test_typed_shape_endpoint_uses_existing_job_lifecycle(tmp_path: Path, monkeypatch) -> None:
    database, engine, factory = await _database(tmp_path)
    resources = importlib.import_module("services.shape_resources")
    router = importlib.import_module("routers.shape_blueprint")
    captured = {}

    async def fake_create_job(
        job_data,
        background_tasks,
        session,
        _preallocated_job_id=None,
        _commit=True,
    ):
        captured["job_data"] = job_data
        captured["preallocated_job_id"] = _preallocated_job_id
        captured["commit"] = _commit
        job = database.Job(
            id=_preallocated_job_id,
            name=job_data.name,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=job_data.params,
            status="queued",
            queue_status="queued",
            output_dir=str(tmp_path / "results"),
        )
        session.add(job)
        await session.flush()
        return SimpleNamespace(id=job.id, status="queued", name=job.name)

    async def session_override():
        async with factory() as session:
            yield session

    monkeypatch.setattr(router, "get_data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(router.jobs_router, "create_job", fake_create_job)
    monkeypatch.setenv("BMS_SHAPE_BLUEPRINT_ENABLED", "true")
    async with factory() as session:
        geometry = await resources.admit_obj_geometry(
            session,
            data_root=tmp_path / "data",
            payload=CUBE_OBJ,
            filename="cube.obj",
            angstrom_per_unit=10.0,
        )

    app = FastAPI()
    app.include_router(router.router, prefix="/api/shape-blueprint")
    app.dependency_overrides[router.get_session] = session_override
    payload = {
        "client_request_id": "efbfe67e-f4fe-43d8-a856-78705ca82937",
        "name": "shape-owner-path",
        "geometry_id": geometry.geometry_id,
        "expected_geometry_sha256": geometry.geometry_sha256,
        "expected_point_pool_sha256": geometry.point_pool_sha256,
        "target_length": 120,
        "num_backbones": 2,
        "sequences_per_backbone": 2,
        "seed": 42,
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/shape-blueprint/requests", json=payload)
        assert response.status_code == 201, response.text
        expected_job_id = str(router.uuid.uuid5(router._SHAPE_JOB_NAMESPACE, "shape_efbfe67e-f4fe-43d8-a856-78705ca82937"))
        assert response.json()["job_id"] == expected_job_id
        assert captured["preallocated_job_id"] == expected_job_id
        assert captured["commit"] is False
        job_data = captured["job_data"]
        assert job_data.model_id == "protein_modification_experimental"
        assert job_data.mode == "shape_blueprint"
        assert job_data.params["shape_generator"] == "rfd3"
        assert all("client" not in key for key in job_data.params)
        async with factory() as session:
            persisted = await session.get(database.ShapeDesignRequest, "shape_efbfe67e-f4fe-43d8-a856-78705ca82937")
            assert persisted.job_id == expected_job_id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_duplicate_shape_submissions_create_one_job(tmp_path: Path, monkeypatch) -> None:
    database, engine, factory = await _database(tmp_path)
    resources = importlib.import_module("services.shape_resources")
    router = importlib.import_module("routers.shape_blueprint")

    async def fake_create_job(
        job_data,
        background_tasks,
        session,
        _preallocated_job_id=None,
        _commit=True,
    ):
        job = database.Job(
            id=_preallocated_job_id,
            name=job_data.name,
            model_id=job_data.model_id,
            mode=job_data.mode,
            params=job_data.params,
            status="queued",
            queue_status="queued",
            output_dir=str(tmp_path / "results" / str(_preallocated_job_id)),
        )
        session.add(job)
        await session.flush()
        await asyncio.sleep(0.05)
        return SimpleNamespace(id=job.id, status="queued", name=job.name)

    async def session_override():
        async with factory() as session:
            yield session

    monkeypatch.setattr(router, "get_data_root", lambda: tmp_path / "data")
    monkeypatch.setattr(router.jobs_router, "create_job", fake_create_job)
    monkeypatch.setenv("BMS_SHAPE_BLUEPRINT_ENABLED", "true")
    async with factory() as session:
        geometry = await resources.admit_obj_geometry(
            session,
            data_root=tmp_path / "data",
            payload=CUBE_OBJ,
            filename="cube.obj",
            angstrom_per_unit=10.0,
        )

    app = FastAPI()
    app.include_router(router.router, prefix="/api/shape-blueprint")
    app.dependency_overrides[router.get_session] = session_override
    payload = {
        "client_request_id": "cf353e75-271e-46c9-b2fe-67a655c2571d",
        "name": "shape-concurrent-owner-path",
        "geometry_id": geometry.geometry_id,
        "expected_geometry_sha256": geometry.geometry_sha256,
        "expected_point_pool_sha256": geometry.point_pool_sha256,
        "target_length": 120,
        "num_backbones": 1,
        "sequences_per_backbone": 1,
        "seed": 42,
    }
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first, second = await asyncio.gather(
                client.post("/api/shape-blueprint/requests", json=payload),
                client.post("/api/shape-blueprint/requests", json=payload),
            )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["job_id"] == second.json()["job_id"]
        assert sorted((first.json()["reused"], second.json()["reused"])) == [False, True]
        async with factory() as session:
            jobs = (await session.execute(select(database.Job))).scalars().all()
            assert len(jobs) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_staged_bundle_validator_emits_hash_bound_receipt(tmp_path: Path) -> None:
    _, engine, factory = await _database(tmp_path)
    resources = importlib.import_module("services.shape_resources")
    requests = importlib.import_module("services.shape_requests")
    try:
        async with factory() as session:
            geometry = await resources.admit_obj_geometry(
                session,
                data_root=tmp_path / "data",
                payload=CUBE_OBJ,
                filename="cube.obj",
                angstrom_per_unit=10.0,
            )
            staged = await requests.materialize_shape_request(
                session,
                data_root=tmp_path / "data",
                submitted=requests.SubmittedShapeRequest(
                    client_request_id="60a1ba51-0aa2-4ddc-979b-9a4e5f983744",
                    name="validator-proof",
                    geometry_id=geometry.geometry_id,
                    expected_geometry_sha256=geometry.geometry_sha256,
                    expected_point_pool_sha256=geometry.point_pool_sha256,
                    target_length=100,
                    num_backbones=1,
                    sequences_per_backbone=1,
                    seed=9,
                ),
            )
        stage = Path(staged.stage_dir)
        receipt = tmp_path / "shape_input_receipt.json"
        script = Path(__file__).parents[3] / "scripts" / "shape_blueprint" / "validate_bundle.py"
        command = [
                sys.executable,
                str(script),
                "--request", str(stage / "request.json"),
                "--manifest", str(stage / "geometry-manifest.json"),
                "--vertices", str(stage / "vertices.f64le"),
                "--faces", str(stage / "faces.u32le"),
                "--points", str(stage / "points.f32le"),
                "--sdf", str(stage / "sdf.f32le"),
                "--output", str(receipt),
            ]
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(receipt.read_text())
        assert payload["status"] == "validated"
        assert payload["request_sha256"] == staged.request_sha256
        assert payload["geometry_sha256"] == geometry.geometry_sha256
        assert payload["geometry_manifest_sha256"] == geometry.manifest["manifest_sha256"]
        assert payload["point_pool_sha256"] == geometry.point_pool_sha256
        assert payload["sdf_sha256"] == geometry.manifest["sdf_sha256"]
        assert payload["sdf_grid_shape"] == geometry.manifest["sdf_grid_shape"]

        manifest_path = stage / "geometry-manifest.json"
        manifest_path.chmod(0o640)
        tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
        tampered["source_unit"] = "millimeter"
        tampered["conversion"]["source_unit"] = "millimeter"
        unhashed = dict(tampered)
        unhashed.pop("manifest_sha256")
        tampered["manifest_sha256"] = hashlib.sha256(
            json.dumps(unhashed, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
        manifest_path.write_text(json.dumps(tampered, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        rejected = subprocess.run(command, text=True, capture_output=True)
        assert rejected.returncode != 0
        assert "request and geometry manifest hash disagree" in rejected.stderr
    finally:
        await engine.dispose()
