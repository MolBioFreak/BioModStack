"""Lean shared API for Shape Blueprint geometry and job submission."""

from __future__ import annotations

import os
from pathlib import Path
import hashlib
import struct
from typing import cast
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, ShapeDesignGeometry, ShapeDesignRequest, get_session
from paths import get_data_root
from routers import jobs as jobs_router
from schemas import JobCreate
from services.shape_geometry import MAX_MESH_BYTES, ShapeGeometryError
from services.shape_requests import ShapeRequestError, SubmittedShapeRequest, materialize_shape_request
from services.shape_resources import AdmittedGeometry, admit_mesh_geometry


router = APIRouter()
_SHAPE_JOB_NAMESPACE = uuid.UUID("dd81e5ad-bac4-50df-b858-1ce033ec42d7")

_UNIT_TO_ANGSTROM = {
    "angstrom": 1.0,
    "nanometer": 10.0,
    "micrometer": 10_000.0,
    "millimeter": 10_000_000.0,
    "centimeter": 100_000_000.0,
    "meter": 10_000_000_000.0,
    "inch": 254_000_000.0,
    "foot": 3_048_000_000.0,
}


def _feature_enabled() -> bool:
    return os.getenv("BMS_SHAPE_BLUEPRINT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _summary(row: ShapeDesignGeometry | AdmittedGeometry) -> dict[str, object]:
    manifest = dict(row.manifest)
    bounds = [float(value) for value in cast(list[float], manifest["bounds_angstrom"])]
    scale = float(str(manifest["angstrom_per_unit"]))
    source_unit = str(
        manifest.get(
            "source_unit",
            next((label for label, value in _UNIT_TO_ANGSTROM.items() if value == scale), "custom"),
        )
    )
    return {
        "geometry_id": row.geometry_id,
        "source_id": row.source_id,
        "geometry_sha256": row.geometry_sha256,
        "source_sha256": manifest["source_sha256"],
        "point_pool_sha256": manifest["point_pool_sha256"],
        "sdf_sha256": manifest["sdf_sha256"],
        "preview_obj_sha256": manifest.get("preview_obj_sha256"),
        "sdf_sign": manifest["sdf_sign"],
        "sdf_grid_shape": manifest["sdf_grid_shape"],
        "vertex_count": int(manifest["vertex_count"]),
        "face_count": int(manifest["face_count"]),
        "point_count": int(manifest["point_count"]),
        "bounds_angstrom": bounds,
        "dimensions_angstrom": [bounds[index + 3] - bounds[index] for index in range(3)],
        "source_format": str(manifest.get("source_format", "obj")),
        "source_parser": str(manifest.get("source_parser", "obj_strict_v1")),
        "source_unit": source_unit,
        "angstrom_per_unit": scale,
    }


@router.post("/geometries", status_code=status.HTTP_201_CREATED)
async def upload_geometry(
    file: UploadFile = File(...),
    unit: str = Form("angstrom"),
    session: AsyncSession = Depends(get_session),
):
    suffix = Path(file.filename or "").suffix.lower()
    source_format = {".obj": "obj", ".stl": "stl"}.get(suffix)
    if source_format is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "unsupported_format", "message": "accepted mesh formats are OBJ and STL"},
        )
    scale = _UNIT_TO_ANGSTROM.get(unit)
    if scale is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_unit", "message": "unsupported source unit"})
    payload = await file.read(MAX_MESH_BYTES + 1)
    if len(payload) > MAX_MESH_BYTES:
        raise HTTPException(status_code=413, detail="mesh exceeds the 16 MiB limit")
    try:
        result = await admit_mesh_geometry(
            session,
            data_root=get_data_root(),
            payload=payload,
            filename=file.filename or f"source.{source_format}",
            source_format=source_format,
            source_unit=unit,
            angstrom_per_unit=scale,
        )
    except ShapeGeometryError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc
    return _summary(result)


@router.get("/geometries")
async def list_geometries(session: AsyncSession = Depends(get_session)):
    rows = (
        await session.execute(
            select(ShapeDesignGeometry).order_by(
                ShapeDesignGeometry.created_at.desc(), ShapeDesignGeometry.geometry_id
            ).limit(100)
        )
    ).scalars().all()
    return {"geometries": [_summary(row) for row in rows]}


async def _geometry_or_404(session: AsyncSession, geometry_id: str) -> ShapeDesignGeometry:
    row = await session.get(ShapeDesignGeometry, geometry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Shape geometry not found")
    return row


@router.get("/geometries/{geometry_id}")
async def get_geometry(geometry_id: str, session: AsyncSession = Depends(get_session)):
    return _summary(await _geometry_or_404(session, geometry_id))


@router.get("/geometries/{geometry_id}/preview.obj")
async def get_geometry_preview(geometry_id: str, session: AsyncSession = Depends(get_session)):
    row = await _geometry_or_404(session, geometry_id)
    root = (get_data_root() / "shape_blueprint").resolve()
    relative = str(row.artifacts["preview_obj"])
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise HTTPException(status_code=409, detail="Shape preview artifact is unavailable")
    expected_sha256 = row.manifest.get("preview_obj_sha256")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise HTTPException(status_code=409, detail="Shape preview artifact is not hash-bound")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise HTTPException(status_code=409, detail="Shape preview artifact hash mismatch")
    return Response(
        content=payload,
        media_type="text/plain",
        headers={
            "Content-Disposition": f'inline; filename="{geometry_id}.obj"',
            "X-BMS-Preview-OBJ-SHA256": expected_sha256,
        },
    )


@router.get("/geometries/{geometry_id}/points.cif")
async def get_geometry_points(geometry_id: str, session: AsyncSession = Depends(get_session)):
    """Serve the exact canonical point pool as a Mol*-readable point-cloud structure."""
    row = await _geometry_or_404(session, geometry_id)
    root = (get_data_root() / "shape_blueprint").resolve()
    path = (root / str(row.artifacts["points_f32"])).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise HTTPException(status_code=409, detail="Shape point-pool artifact is unavailable")
    payload = path.read_bytes()
    manifest = dict(row.manifest)
    if hashlib.sha256(payload).hexdigest() != manifest["point_pool_sha256"]:
        raise HTTPException(status_code=409, detail="Shape point-pool artifact hash mismatch")
    point_count = int(manifest["point_count"])
    if len(payload) != point_count * 3 * 4:
        raise HTTPException(status_code=409, detail="Shape point-pool artifact length mismatch")
    points = struct.iter_unpack("<fff", payload)
    lines = [
        f"data_{geometry_id}",
        "#", "loop_", "_atom_site.group_PDB", "_atom_site.id", "_atom_site.type_symbol",
        "_atom_site.label_atom_id", "_atom_site.label_comp_id", "_atom_site.label_asym_id",
        "_atom_site.label_seq_id", "_atom_site.Cartn_x", "_atom_site.Cartn_y", "_atom_site.Cartn_z",
        "_atom_site.occupancy", "_atom_site.B_iso_or_equiv", "_atom_site.pdbx_PDB_model_num",
    ]
    lines.extend(
        f"HETATM {index} C C PNT S {index} {x:.6f} {y:.6f} {z:.6f} 1.00 0.00 1"
        for index, (x, y, z) in enumerate(points, start=1)
    )
    lines.append("#")
    return Response(
        content="\n".join(lines) + "\n",
        media_type="chemical/x-mmcif",
        headers={
            "Content-Disposition": f'inline; filename="{geometry_id}.points.cif"',
            "X-BMS-Point-Pool-SHA256": str(manifest["point_pool_sha256"]),
        },
    )


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def submit_shape_request(
    submitted: SubmittedShapeRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    if not _feature_enabled():
        raise HTTPException(status_code=404, detail="Shape Blueprint is disabled")
    try:
        staged = await materialize_shape_request(
            session,
            data_root=get_data_root(),
            submitted=submitted,
        )
    except ShapeRequestError as exc:
        if exc.code == "geometry_not_found":
            status_code = 404
        elif "mismatch" in exc.code or "conflict" in exc.code:
            status_code = 409
        else:
            status_code = 422
        raise HTTPException(status_code=status_code, detail={"code": exc.code, "message": str(exc)}) from exc

    request_row = await session.get(ShapeDesignRequest, staged.request_id)
    if request_row is None:
        raise HTTPException(status_code=500, detail="Shape request staging was not persisted")
    if request_row.job_id:
        existing_job = await session.get(Job, request_row.job_id)
        if existing_job is None:
            raise HTTPException(status_code=409, detail="Shape request references a missing job")
        return {
            "request_id": staged.request_id,
            "request_sha256": staged.request_sha256,
            "job_id": existing_job.id,
            "job_status": existing_job.status,
            "reused": True,
        }

    deterministic_job_id = str(uuid.uuid5(_SHAPE_JOB_NAMESPACE, staged.request_id))
    try:
        job_response = await jobs_router.create_job(
            JobCreate(
                name=staged.name,
                model_id=staged.model_id,
                mode=staged.mode,
                params=staged.launch_params,
                pinned_gpu=None,
                parent_job_id=None,
                child_stage=None,
                batch_id=None,
                batch_name=None,
                sequence_length=int(str(staged.launch_params["shape_target_length"])),
            ),
            background_tasks,
            session,
            _preallocated_job_id=deterministic_job_id,
            _commit=False,
        )
        request_row = await session.get(ShapeDesignRequest, staged.request_id)
        if request_row is None:
            raise HTTPException(status_code=500, detail="Shape request disappeared during job creation")
        request_row.job_id = deterministic_job_id
        await session.commit()
    except IntegrityError:
        await session.rollback()
        request_row = await session.get(ShapeDesignRequest, staged.request_id)
        existing_job = await session.get(Job, deterministic_job_id)
        if request_row is None or request_row.job_id != deterministic_job_id or existing_job is None:
            raise HTTPException(status_code=409, detail="Shape request creation is still in progress")
        return {
            "request_id": staged.request_id,
            "request_sha256": staged.request_sha256,
            "job_id": existing_job.id,
            "job_status": existing_job.status,
            "reused": True,
        }
    return {
        "request_id": staged.request_id,
        "request_sha256": staged.request_sha256,
        "job_id": job_response.id,
        "job_status": str(job_response.status),
        "reused": False,
    }
