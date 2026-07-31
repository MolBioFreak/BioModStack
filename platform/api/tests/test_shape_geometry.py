from __future__ import annotations

import importlib
from pathlib import Path
import struct

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

TETRA_TRIANGLES = (
    ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
    ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    ((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
)


def _ascii_stl(triangles=TETRA_TRIANGLES) -> bytes:
    lines = ["solid tetra"]
    for triangle in triangles:
        lines.extend(("facet normal 0 0 0", "outer loop"))
        lines.extend(f"vertex {x} {y} {z}" for x, y, z in triangle)
        lines.extend(("endloop", "endfacet"))
    lines.append("endsolid tetra")
    return ("\n".join(lines) + "\n").encode("ascii")


def _binary_stl(triangles=TETRA_TRIANGLES) -> bytes:
    payload = bytearray(b"BioModStack binary STL".ljust(80, b"\0"))
    payload.extend(struct.pack("<I", len(triangles)))
    for triangle in triangles:
        coordinates = tuple(value for vertex in triangle for value in vertex)
        payload.extend(struct.pack("<12fH", 0.0, 0.0, 0.0, *coordinates, 0))
    return bytes(payload)


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
    ("payload", "encoding"),
    [
        (_ascii_stl(), "ascii"),
        (_binary_stl(), "binary"),
    ],
)
def test_closed_stl_canonicalization_is_deterministic(payload: bytes, encoding: str) -> None:
    geometry = _geometry()

    first = geometry.canonicalize_mesh(payload, source_format="stl", angstrom_per_unit=10.0)
    second = geometry.canonicalize_mesh(payload, source_format="stl", angstrom_per_unit=10.0)

    assert first.geometry_sha256 == second.geometry_sha256
    assert first.vertices_f64 == second.vertices_f64
    assert first.faces_u32 == second.faces_u32
    assert first.manifest["source_format"] == "stl"
    assert first.manifest["source_parser"] == f"stl_{encoding}_v1"
    assert first.vertex_count == 4
    assert first.face_count == 4
    assert first.bounds_angstrom == [-2.5, -2.5, -2.5, 7.5, 7.5, 7.5]


def test_stl_and_obj_sources_have_distinct_source_bound_geometry_identity() -> None:
    geometry = _geometry()
    tetra_obj = b"v 0 0 0\nv 0 1 0\nv 1 0 0\nv 0 0 1\nf 1 2 3\nf 1 3 4\nf 1 4 2\nf 3 2 4\n"

    obj = geometry.canonicalize_obj(tetra_obj, angstrom_per_unit=10.0)
    stl = geometry.canonicalize_mesh(_ascii_stl(), source_format="stl", angstrom_per_unit=10.0)

    assert obj.vertices_f64 == stl.vertices_f64
    assert obj.faces_u32 == stl.faces_u32
    assert obj.geometry_sha256 != stl.geometry_sha256
    assert "source_format" not in obj.manifest
    assert stl.manifest["source_format"] == "stl"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (_binary_stl() + b"trailing", "invalid_stl"),
        (_binary_stl()[:100], "invalid_stl"),
        (_ascii_stl(TETRA_TRIANGLES[:1]), "open_mesh"),
        (_ascii_stl().replace(b"vertex 0.0 0.0 0.0", b"vertex nan 0.0 0.0", 1), "non_finite_vertex"),
        (
            _ascii_stl(
                TETRA_TRIANGLES
                + tuple(tuple((x + 3.0, y, z) for x, y, z in triangle) for triangle in TETRA_TRIANGLES)
            ),
            "disconnected_mesh",
        ),
    ],
)
def test_invalid_stl_is_rejected(payload: bytes, code: str) -> None:
    geometry = _geometry()

    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry.canonicalize_mesh(payload, source_format="stl", angstrom_per_unit=1.0)

    assert exc.value.code == code


def test_binary_stl_rejects_non_finite_and_attribute_payloads() -> None:
    geometry = _geometry()

    non_finite = bytearray(_binary_stl())
    struct.pack_into("<f", non_finite, 84, float("nan"))
    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry.canonicalize_mesh(bytes(non_finite), source_format="stl", angstrom_per_unit=1.0)
    assert exc.value.code == "non_finite_vertex"

    attributed = bytearray(_binary_stl())
    struct.pack_into("<H", attributed, 84 + 48, 1)
    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry.canonicalize_mesh(bytes(attributed), source_format="stl", angstrom_per_unit=1.0)
    assert exc.value.code == "unsupported_stl_attribute"


def test_unknown_mesh_format_is_rejected() -> None:
    geometry = _geometry()

    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry.canonicalize_mesh(CUBE_OBJ, source_format="step", angstrom_per_unit=1.0)

    assert exc.value.code == "unsupported_format"


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


def test_self_intersection_gate_rejects_coplanar_overlap() -> None:
    geometry = _geometry()
    vertices = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [3.0, 0.0, 0.0],
            [0.0, 3.0, 0.0],
            [0.5, 0.5, 0.0],
            [2.5, 0.5, 0.0],
            [0.5, 2.5, 0.0],
        ],
        dtype=np.float64,
    )
    faces = np.asarray([[0, 1, 2], [3, 4, 5]], dtype=np.int64)

    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry._reject_self_intersections(vertices, faces)

    assert exc.value.code == "self_intersection"


def test_self_intersection_gate_rejects_crossing_faces_that_share_one_vertex() -> None:
    geometry = _geometry()
    vertices = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [2.0, 0.0, 0.0],
            [0.0, 2.0, 0.0],
            [1.0, -1.0, -1.0],
            [1.0, 1.0, 1.0],
        ],
        dtype=np.float64,
    )
    faces = np.asarray([[0, 1, 2], [0, 3, 4]], dtype=np.int64)

    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry._reject_self_intersections(vertices, faces)

    assert exc.value.code == "self_intersection"


def test_self_intersection_validation_is_translation_invariant() -> None:
    geometry = _geometry()

    def transformed_cube(offset: float) -> bytes:
        lines: list[str] = []
        for raw_line in CUBE_OBJ.decode("ascii").splitlines():
            if raw_line.startswith("v "):
                _, x, y, z = raw_line.split()
                coordinates = [offset + 100.0 * float(value) for value in (x, y, z)]
                lines.append("v " + " ".join(format(value, ".17g") for value in coordinates))
            else:
                lines.append(raw_line)
        return ("\n".join(lines) + "\n").encode("ascii")

    at_origin = geometry.canonicalize_obj(transformed_cube(0.0), angstrom_per_unit=1.0)
    translated = geometry.canonicalize_obj(transformed_cube(1e16), angstrom_per_unit=1.0)

    assert translated.vertices_f64 == at_origin.vertices_f64
    assert translated.faces_u32 == at_origin.faces_u32


def test_self_intersection_scan_limit_counts_axis_disjoint_active_faces(monkeypatch: pytest.MonkeyPatch) -> None:
    geometry = _geometry()
    monkeypatch.setattr(geometry, "MAX_SELF_INTERSECTION_SCANS", 2, raising=False)
    vertices = np.asarray(
        [[x, y, z] for y in (0.0, 10.0, 20.0, 30.0) for x, z in ((0.0, 0.0), (1.0, 0.0), (0.5, 1.0))],
        dtype=np.float64,
    )
    faces = np.asarray([[3 * index, 3 * index + 1, 3 * index + 2] for index in range(4)], dtype=np.uint32)

    with pytest.raises(geometry.ShapeGeometryError) as exc:
        geometry._reject_self_intersections(vertices, faces)

    assert exc.value.code == "mesh_too_complex"


def test_conversion_bounds_are_enforced() -> None:
    geometry = _geometry()

    meter_scaled = geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=10_000_000_000.0)
    assert meter_scaled.bounds_angstrom == [
        -10_000_000_000.0,
        -10_000_000_000.0,
        -10_000_000_000.0,
        10_000_000_000.0,
        10_000_000_000.0,
        10_000_000_000.0,
    ]

    for invalid_scale in (0.0, 10_000_000_001.0):
        with pytest.raises(geometry.ShapeGeometryError) as exc:
            geometry.canonicalize_obj(CUBE_OBJ, angstrom_per_unit=invalid_scale)
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
            assert len(body["preview_obj_sha256"]) == 64
            assert "principal" not in body and "owner" not in body

            listed = await client.get("/api/shape-blueprint/geometries")
            assert listed.status_code == 200
            assert [row["geometry_id"] for row in listed.json()["geometries"]] == [body["geometry_id"]]

            preview = await client.get(f"/api/shape-blueprint/geometries/{body['geometry_id']}/preview.obj")
            assert preview.status_code == 200
            assert preview.content.startswith(b"# bms_shape_canonical_obj_v1")

            preview_path = next(
                (tmp_path / "data" / "shape_blueprint" / "geometries").glob("*/preview.obj")
            )
            preview_path.chmod(0o640)
            preview_path.write_bytes(preview.content + b"# corrupted\n")
            corrupted = await client.get(
                f"/api/shape-blueprint/geometries/{body['geometry_id']}/preview.obj"
            )
            assert corrupted.status_code == 409
            assert corrupted.json()["detail"] == "Shape preview artifact hash mismatch"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_shape_geometry_http_accepts_binary_stl_with_reviewable_provenance(tmp_path: Path, monkeypatch) -> None:
    database = importlib.import_module("database")
    router = importlib.import_module("routers.shape_blueprint")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shape-stl-api.db'}")
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
    payload = _binary_stl()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            unsupported = await client.post(
                "/api/shape-blueprint/geometries",
                data={"unit": "millimeter"},
                files={"file": ("part.step", b"STEP", "application/octet-stream")},
            )
            assert unsupported.status_code == 422
            assert unsupported.json()["detail"]["code"] == "unsupported_format"

            uploaded = await client.post(
                "/api/shape-blueprint/geometries",
                data={"unit": "millimeter"},
                files={"file": ("printed-part.stl", payload, "model/stl")},
            )
            assert uploaded.status_code == 201, uploaded.text
            body = uploaded.json()
            assert body["source_format"] == "stl"
            assert body["source_parser"] == "stl_binary_v1"
            assert body["source_unit"] == "millimeter"
            assert body["angstrom_per_unit"] == 10_000_000.0
            assert body["dimensions_angstrom"] == [10_000_000.0, 10_000_000.0, 10_000_000.0]

        async with factory() as session:
            source = await session.get(database.ShapeCadSource, body["source_id"])
            geometry = await session.get(database.ShapeDesignGeometry, body["geometry_id"])
            assert source is not None and geometry is not None
            assert source.original_filename == "printed-part.stl"
            assert source.relative_path.endswith("/source.stl")
            assert (tmp_path / "data" / "shape_blueprint" / source.relative_path).read_bytes() == payload
            assert geometry.manifest["source_format"] == "stl"
            assert geometry.manifest["source_unit"] == "millimeter"
    finally:
        await engine.dispose()
