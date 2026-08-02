"""Shared immutable storage for Shape Blueprint geometry resources."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import ShapeCadSource, ShapeDesignGeometry
from services.shape_geometry import canonicalize_mesh


@dataclass(frozen=True)
class AdmittedGeometry:
    source_id: str
    geometry_id: str
    source_sha256: str
    geometry_sha256: str
    point_pool_sha256: str
    manifest: dict[str, object]
    artifacts: dict[str, str]


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _safe_filename(filename: str) -> str:
    label = Path(filename or "source.obj").name.strip()
    return label[:255] or "source.obj"


def _publish(path: Path, payload: bytes) -> bool:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.stat().st_nlink != 1 or path.read_bytes() != payload:
            raise RuntimeError(f"immutable Shape artifact conflict: {path.name}")
        return False
    descriptor, temporary_name = tempfile.mkstemp(prefix=".shape-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o440)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or path.stat().st_nlink != 1 or path.read_bytes() != payload:
                raise RuntimeError(f"immutable Shape artifact conflict: {path.name}")
            return False
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _cleanup_publications(paths: list[Path], *, root: Path) -> None:
    for path in reversed(paths):
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != root and parent.is_relative_to(root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def _result(row: ShapeDesignGeometry) -> AdmittedGeometry:
    return AdmittedGeometry(
        source_id=row.source_id,
        geometry_id=row.geometry_id,
        source_sha256=str(row.manifest["source_sha256"]),
        geometry_sha256=row.geometry_sha256,
        point_pool_sha256=str(row.manifest["point_pool_sha256"]),
        manifest=dict(row.manifest),
        artifacts=dict(row.artifacts),
    )


async def admit_mesh_geometry(
    session: AsyncSession,
    *,
    data_root: Path,
    payload: bytes,
    filename: str,
    source_format: str,
    source_unit: str,
    angstrom_per_unit: float,
) -> AdmittedGeometry:
    normalized_format = source_format.strip().lower()
    canonical = await asyncio.to_thread(
        canonicalize_mesh,
        payload,
        source_format=normalized_format,
        angstrom_per_unit=angstrom_per_unit,
    )
    source_id = f"cad_{canonical.source_sha256[:32]}"
    conversion = {
        "schema": "bms_shape_geometry_conversion_v1",
        "angstrom_per_unit": float(angstrom_per_unit),
        "center_mode": "volume_centroid_v1",
        "source_format": normalized_format,
        "source_parser": canonical.manifest["source_parser"],
        "source_unit": source_unit,
    }
    conversion_sha256 = hashlib.sha256(_canonical_json(conversion)).hexdigest()
    publication_identity = {
        "schema": "bms_shape_geometry_publication_identity_v1",
        "source_sha256": canonical.source_sha256,
        "geometry_sha256": canonical.geometry_sha256,
        "conversion_sha256": conversion_sha256,
    }
    publication_sha256 = hashlib.sha256(_canonical_json(publication_identity)).hexdigest()
    geometry_id = f"geom_{publication_sha256[:32]}"

    existing = await session.scalar(
        select(ShapeDesignGeometry).where(
            ShapeDesignGeometry.source_id == source_id,
            ShapeDesignGeometry.conversion_sha256 == conversion_sha256,
        )
    )
    if existing is not None:
        if existing.geometry_sha256 != canonical.geometry_sha256:
            raise RuntimeError("stored Shape conversion identity conflicts with canonical output")
        return _result(existing)

    root = (data_root / "shape_blueprint").resolve()
    source_relative = f"sources/{canonical.source_sha256}/source.{normalized_format}"
    geometry_prefix = f"geometries/{publication_sha256}"
    artifacts = {
        "vertices_f64": f"{geometry_prefix}/vertices.f64le",
        "faces_u32": f"{geometry_prefix}/faces.u32le",
        "points_f32": f"{geometry_prefix}/points.f32le",
        "sdf_f32": f"{geometry_prefix}/sdf.f32le",
        "preview_obj": f"{geometry_prefix}/preview.obj",
        "manifest": f"{geometry_prefix}/manifest.json",
    }
    unhashed_manifest = {
        **canonical.manifest,
        "conversion_sha256": conversion_sha256,
        "publication_sha256": publication_sha256,
        "source_unit": source_unit,
        "conversion": conversion,
        "artifacts": artifacts,
    }
    manifest_sha256 = hashlib.sha256(_canonical_json(unhashed_manifest)).hexdigest()
    final_manifest = {**unhashed_manifest, "manifest_sha256": manifest_sha256}
    payloads = {
        source_relative: payload,
        artifacts["vertices_f64"]: canonical.vertices_f64,
        artifacts["faces_u32"]: canonical.faces_u32,
        artifacts["points_f32"]: canonical.points_f32,
        artifacts["sdf_f32"]: canonical.sdf_f32,
        artifacts["preview_obj"]: canonical.preview_obj,
        artifacts["manifest"]: _canonical_json(final_manifest),
    }
    source = await session.get(ShapeCadSource, source_id)
    if source is None:
        source = ShapeCadSource(
            source_id=source_id,
            source_sha256=canonical.source_sha256,
            size_bytes=len(payload),
            original_filename=_safe_filename(filename),
            relative_path=source_relative,
            created_at=datetime.utcnow(),
        )
        session.add(source)
    elif source.source_sha256 != canonical.source_sha256 or source.size_bytes != len(payload):
        raise RuntimeError("stored Shape source identity conflicts with uploaded bytes")

    row = ShapeDesignGeometry(
        geometry_id=geometry_id,
        source_id=source_id,
        geometry_sha256=canonical.geometry_sha256,
        conversion_sha256=conversion_sha256,
        angstrom_per_unit=float(angstrom_per_unit),
        vertex_count=canonical.vertex_count,
        face_count=canonical.face_count,
        point_count=canonical.point_count,
        manifest=final_manifest,
        artifacts=artifacts,
        created_at=datetime.utcnow(),
    )
    session.add(row)
    # Reserve database identities before publication. SQLite then holds the
    # writer transaction until commit, so rollback cleanup cannot race another
    # admission that has adopted these deterministic publication paths.
    created: list[Path] = []
    try:
        await session.flush()
        for relative, content in payloads.items():
            destination = (root / relative).resolve()
            if not destination.is_relative_to(root):
                raise RuntimeError("Shape publication escaped the data root")
            if _publish(destination, content):
                created.append(destination)
        await session.commit()
    except BaseException:
        await session.rollback()
        _cleanup_publications(created, root=root)
        raise
    return _result(row)


async def admit_obj_geometry(
    session: AsyncSession,
    *,
    data_root: Path,
    payload: bytes,
    filename: str,
    angstrom_per_unit: float,
) -> AdmittedGeometry:
    """Compatibility wrapper that preserves the original OBJ conversion identity."""
    return await admit_mesh_geometry(
        session,
        data_root=data_root,
        payload=payload,
        filename=filename,
        source_format="obj",
        source_unit="angstrom",
        angstrom_per_unit=angstrom_per_unit,
    )
