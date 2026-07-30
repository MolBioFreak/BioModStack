"""Lean shared API for Shape Blueprint geometry and job submission."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, ShapeDesignGeometry, ShapeDesignRequest, get_session
from paths import get_data_root
from routers import jobs as jobs_router
from schemas import JobCreate
from services.shape_geometry import MAX_OBJ_BYTES, ShapeGeometryError
from services.shape_requests import ShapeRequestError, SubmittedShapeRequest, materialize_shape_request
from services.shape_resources import AdmittedGeometry, admit_obj_geometry


router = APIRouter()

_UNIT_TO_ANGSTROM = {
    "angstrom": 1.0,
    "nanometer": 10.0,
    "micrometer": 10_000.0,
    "millimeter": 10_000_000.0,
    "centimeter": 100_000_000.0,
    "meter": 10_000_000_000.0,
    "inch": 254_000_000.0,
}


def _feature_enabled() -> bool:
    return os.getenv("BMS_SHAPE_BLUEPRINT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _summary(row: ShapeDesignGeometry | AdmittedGeometry) -> dict[str, object]:
    manifest = dict(row.manifest)
    return {
        "geometry_id": row.geometry_id,
        "source_id": row.source_id,
        "geometry_sha256": row.geometry_sha256,
        "source_sha256": manifest["source_sha256"],
        "point_pool_sha256": manifest["point_pool_sha256"],
        "vertex_count": int(manifest["vertex_count"]),
        "face_count": int(manifest["face_count"]),
        "point_count": int(manifest["point_count"]),
        "bounds_angstrom": manifest["bounds_angstrom"],
    }


@router.post("/geometries", status_code=status.HTTP_201_CREATED)
async def upload_geometry(
    file: UploadFile = File(...),
    unit: str = Form("angstrom"),
    session: AsyncSession = Depends(get_session),
):
    if Path(file.filename or "").suffix.lower() != ".obj":
        raise HTTPException(status_code=422, detail={"code": "unsupported_format", "message": "V1 accepts OBJ only"})
    scale = _UNIT_TO_ANGSTROM.get(unit)
    if scale is None:
        raise HTTPException(status_code=422, detail={"code": "invalid_unit", "message": "unsupported source unit"})
    payload = await file.read(MAX_OBJ_BYTES + 1)
    if len(payload) > MAX_OBJ_BYTES:
        raise HTTPException(status_code=413, detail="OBJ exceeds the 16 MiB limit")
    try:
        result = await admit_obj_geometry(
            session,
            data_root=get_data_root(),
            payload=payload,
            filename=file.filename or "source.obj",
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
    return FileResponse(path, media_type="text/plain", filename=f"{geometry_id}.obj")


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
    )
    request_row = await session.get(ShapeDesignRequest, staged.request_id)
    if request_row is None:
        raise HTTPException(status_code=500, detail="Shape request disappeared during job creation")
    request_row.job_id = job_response.id
    await session.commit()
    return {
        "request_id": staged.request_id,
        "request_sha256": staged.request_sha256,
        "job_id": job_response.id,
        "job_status": str(job_response.status),
        "reused": False,
    }
