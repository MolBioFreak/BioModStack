from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
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


def _geometry():
    return importlib.import_module("services.shape_geometry")


def test_closed_obj_canonicalization_is_deterministic() -> None:
    geometry = _geometry()

    first = geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=10.0)
    second = geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=10.0)

    assert first.geometry_sha256 == second.geometry_sha256
    assert first.point_pool_sha256 == second.point_pool_sha256
    assert first.vertices_f64 == second.vertices_f64
    assert first.faces_u32 == second.faces_u32
    assert first.points_f32 == second.points_f32
    assert first.vertex_count == 8
    assert first.face_count == 12
    assert first.point_count == 4096
    assert first.bounds_angstrom == [-10.0, -10.0, -10.0, 10.0, 10.0, 10.0]
    points = np.frombuffer(first.points_f32, dtype="<f4").reshape((-1, 3))
    assert np.all(points >= -10.0) and np.all(points <= 10.0)


def test_canonical_sdf_grid_is_deterministic_and_signed() -> None:
    geometry = _geometry()
    first = geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=10.0)
    second = geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=10.0)

    assert first.sdf_f32 == second.sdf_f32
    assert first.sdf_sha256 == second.sdf_sha256
    assert first.sdf_shape == [48, 48, 48]
    sdf = np.frombuffer(first.sdf_f32, dtype="<f4").reshape(first.sdf_shape)
    center = tuple(d // 2 for d in first.sdf_shape)
    assert sdf[center] > 0.0
    assert sdf[0, 0, 0] < 0.0
    assert all(np.isfinite(sdf.flat))


def test_same_source_bytes_with_different_units_produce_different_geometry() -> None:
    geometry = _geometry()

    angstrom = geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=1.0)
    millimeter = geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=10_000_000.0)

    assert angstrom.source_sha256 == millimeter.source_sha256
    assert angstrom.geometry_sha256 != millimeter.geometry_sha256
    assert angstrom.bounds_angstrom != millimeter.bounds_angstrom


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", "open_mesh"),
        (b"v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n", "non_triangular_face"),
        (b"v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", "non_finite_vertex"),
        (CUBE_OBJ.replace(b"v 1 1 1", b"v 1 1 -3"), "self_intersection"),
        (CUBE_OBJ + CUBE_OBJ.replace(b"v ", b"v 20 "), "invalid_obj"),
    ],
)
def test_invalid_obj_is_rejected(payload: bytes, code: str) -> None:
    geometry = _geometry()

    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry.canonicalize_obj(payload, angstrom_per_unit=1.0)

    assert exc.value.code == code


def test_conversion_bounds_are_enforced() -> None:
    geometry = _geometry()

    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=0.0)
    assert exc.value.code == "invalid_scale"


def test_builtin_box_preset_is_deterministic_and_uses_obj_admission() -> None:
    presets = importlib.import_module("services.shape_presets")
    first = presets.build_preset_obj("box", {"x": 40.0, "y": 30.0, "z": 20.0})
    second = presets.build_preset_obj("box", {"z": 20.0, "x": 40.0, "y": 30.0})
    assert first == second
    canonical = _geometry().canonicalize_obj(first, angstrom_per_unit=1.0)
    assert canonical.bounds_angstrom == [-20.0, -15.0, -10.0, 20.0, 15.0, 10.0]


@pytest.mark.asyncio
async def test_geometry_admission_persists_shared_immutable_resource(tmp_path: Path) -> None:
    database = importlib.import_module("database")
    resources = importlib.import_module("services.shape_resources")
    source_type = getattr(database, "ShapeCadSource")
    geometry_type = getattr(database, "ShapeDesignGeometry")
    assert "principal_id" not in source_type.__table__.columns
    assert "principal_id" not in geometry_type.__table__.columns

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shape.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            first = await resources.admit_obj_geometry(
                session,
                data_root=tmp_path / "data",
                payload=CUBE_OBJ,
                filename="cube.obj",
                angstrom_per_unit=10.0,
            )
            second = await resources.admit_obj_geometry(
                session,
                data_root=tmp_path / "data",
                payload=CUBE_OBJ,
                filename="renamed-cube.obj",
                angstrom_per_unit=10.0,
            )
            assert first.geometry_id == second.geometry_id
            assert await session.scalar(select(func.count()).select_from(source_type)) == 1
            assert await session.scalar(select(func.count()).select_from(geometry_type)) == 1
            stored = await session.get(geometry_type, first.geometry_id)
            assert stored is not None
            assert stored.geometry_sha256 == first.geometry_sha256
            root = (tmp_path / "data" / "shape_blueprint").resolve()
            for relative in stored.artifacts.values():
                path = (root / relative).resolve()
                assert path.is_relative_to(root)
                assert path.is_file()
                assert path.stat().st_nlink == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_shape_geometry_http_upload_list_and_preview(tmp_path: Path, monkeypatch) -> None:
    database = importlib.import_module("database")
    router = importlib.import_module("routers.shape_blueprint")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shape-api.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(database.Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def session_override():
        async with factory() as session:
            yield session

    monkeypatch.setattr(router, "get_data_root", lambda: tmp_path / "data")
    app = FastAPI()
    app.include_router(router.router, prefix="/api/shape-blueprint")
    app.dependency_overrides[router.get_session] = session_override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            uploaded = await client.post(
                "/api/shape-blueprint/geometries",
                data={"unit": "angstrom"},
                files={"file": ("cube.obj", CUBE_OBJ, "text/plain")},
            )
            assert uploaded.status_code == 201, uploaded.text
            body = uploaded.json()
            assert body["vertex_count"] == 8 and body["point_count"] == 4096
            assert "principal" not in body and "owner" not in body

            listed = await client.get("/api/shape-blueprint/geometries")
            assert listed.status_code == 200
            assert [row["geometry_id"] for row in listed.json()["geometries"]] == [body["geometry_id"]]

            preview = await client.get(f"/api/shape-blueprint/geometries/{body['geometry_id']}/preview.obj")
            assert preview.status_code == 200
            assert preview.content.startswith(b"# bms_shape_canonical_obj_v1")
    finally:
        await engine.dispose()
