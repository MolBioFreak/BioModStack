"""Lean shared API for Shape Blueprint geometry and job submission."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import ShapeDesignGeometry, get_session
from paths import get_data_root
from services.shape_geometry import MAX_OBJ_BYTES, ShapeGeometryError
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
