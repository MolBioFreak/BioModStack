"""Typed Shape request normalization and server-owned staging."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from database import ShapeDesignGeometry, ShapeDesignRequest
from services.shape_resources import _publish


_SHA256 = re.compile(r"^[0-9a-f]{64}$")

PAPER_LIKE_RFD3_PROFILE = {
    "id": "paper_like_rfd3_v1",
    "paper_doi": "10.64898/2026.07.22.740177",
    "shape_ctrl_commit": "e1a518b61e216d3c597a46e5a151b9e24756e33e",
    "source_shape_weight": 0.75,
    "source_guide_scale": 2.0,
    "source_sdf_weight": 1.0,
    "source_chamfer_weight": 1.0,
    "source_target_point_count": 800,
    "target_sampling": "seeded_subset_of_immutable_uniform_interior_pool_v1",
    "rfd3_transfer_coefficient": 0.13333333333333333,
    "effective_step_size": 0.2,
    "max_update_angstrom": 0.5,
    "guidance_decay": "constant",
    "gradient_scaling": "raw",
    "outside_reduction": "sum",
    "connectivity_weight": 0.0,
}


class ShapeRequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SubmittedShapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    name: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
    geometry_id: str = Field(pattern=r"^geom_[0-9a-f]{32}$")
    expected_geometry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_geometry_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_point_pool_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_length: int = Field(ge=40, le=600)
    num_backbones: int = Field(default=4, ge=1, le=32)
    sequences_per_backbone: int = Field(default=2, ge=1, le=8)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    generator: Literal["rfd3"] = "rfd3"
    sequence_engines: tuple[Literal["proteinmpnn", "fampnn"], ...] = ("proteinmpnn", "fampnn")
    predictor: Literal["esmfold2"] = "esmfold2"
    guidance_profile: Literal["paper_like_rfd3_v1"] = "paper_like_rfd3_v1"


@dataclass(frozen=True)
class StagedShapeRequest:
    request_id: str
    request_sha256: str
    model_id: str
    mode: str
    name: str
    stage_dir: str
    launch_params: dict[str, object]


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _checked_artifact(root: Path, relative: str, expected_sha256: str) -> tuple[Path, bytes]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise ShapeRequestError("geometry_artifact_invalid", "canonical geometry artifact is unavailable")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ShapeRequestError("geometry_artifact_hash_mismatch", "canonical geometry artifact hash mismatch")
    return path, payload


def _validate_sequence_engines(engines: tuple[str, ...]) -> None:
    if tuple(engines) != ("proteinmpnn", "fampnn"):
        raise ShapeRequestError(
            "sequence_lanes_required",
            "V1 requires exactly ProteinMPNN and FAMPNN in that order",
        )


def _cleanup_new_stage(stage: Path, created: list[Path]) -> None:
    if stage.exists() and not stage.is_symlink():
        os.chmod(stage, 0o750)
    for path in reversed(created):
        path.unlink(missing_ok=True)
    try:
        stage.rmdir()
    except OSError:
        pass


async def materialize_shape_request(
    session: AsyncSession,
    *,
    data_root: Path,
    submitted: SubmittedShapeRequest,
) -> StagedShapeRequest:
    _validate_sequence_engines(tuple(submitted.sequence_engines))
    geometry = await session.get(ShapeDesignGeometry, submitted.geometry_id)
    if geometry is None:
        raise ShapeRequestError("geometry_not_found", "Shape geometry does not exist")
    if geometry.geometry_sha256 != submitted.expected_geometry_sha256:
        raise ShapeRequestError("geometry_hash_mismatch", "Shape geometry hash does not match the request")
    point_pool_sha256 = str(geometry.manifest.get("point_pool_sha256") or "")
    if point_pool_sha256 != submitted.expected_point_pool_sha256:
        raise ShapeRequestError("point_pool_hash_mismatch", "Shape point pool hash does not match the request")
    manifest_sha256 = str(geometry.manifest.get("manifest_sha256") or "")
    if manifest_sha256 != submitted.expected_geometry_manifest_sha256:
        raise ShapeRequestError(
            "geometry_manifest_hash_mismatch",
            "Shape geometry manifest hash does not match the reviewed request",
        )
    unhashed_manifest = dict(geometry.manifest)
    unhashed_manifest.pop("manifest_sha256", None)
    if not _SHA256.fullmatch(manifest_sha256) or hashlib.sha256(_canonical_json(unhashed_manifest)).hexdigest() != manifest_sha256:
        raise ShapeRequestError("geometry_manifest_hash_mismatch", "Shape geometry manifest is not hash-bound")
    sdf_sha256 = str(geometry.manifest.get("sdf_sha256") or "")
    sdf_sign = str(geometry.manifest.get("sdf_sign") or "")
    sdf_grid_shape = geometry.manifest.get("sdf_grid_shape")
    if not _SHA256.fullmatch(sdf_sha256) or sdf_sign != "positive_inside" or sdf_grid_shape != [48, 48, 48]:
        raise ShapeRequestError(
            "sdf_contract_invalid",
            "Shape geometry lacks the canonical positive-inside 48^3 SDF",
        )

    spec = {
        "schema": "bms_shape_design_request_v1",
        "request_id": f"shape_{submitted.client_request_id}",
        "geometry_id": geometry.geometry_id,
        "geometry_sha256": geometry.geometry_sha256,
        "point_pool_sha256": point_pool_sha256,
        "geometry_manifest_sha256": manifest_sha256,
        "sdf_sha256": sdf_sha256,
        "sdf_sign": sdf_sign,
        "sdf_grid_shape": sdf_grid_shape,
        "target_length": submitted.target_length,
        "num_backbones": submitted.num_backbones,
        "sequences_per_backbone": submitted.sequences_per_backbone,
        "seed": submitted.seed,
        "generator": submitted.generator,
        "sequence_engines": list(submitted.sequence_engines),
        "predictor": submitted.predictor,
        "guidance_profile": dict(PAPER_LIKE_RFD3_PROFILE),
    }
    request_sha256 = hashlib.sha256(_canonical_json(spec)).hexdigest()
    request_id = str(spec["request_id"])
    spec = {**spec, "request_sha256": request_sha256}

    existing = await session.get(ShapeDesignRequest, request_id)
    if existing is not None:
        if existing.request_sha256 != request_sha256:
            raise ShapeRequestError("request_id_conflict", "client request ID is already bound to different scientific intent")
        return _staged(existing, data_root=data_root, name=submitted.name)

    shape_root = (data_root / "shape_blueprint").resolve()
    geometry_root = shape_root
    artifact_map = dict(geometry.artifacts)
    vertices_path, vertices = _checked_artifact(
        geometry_root, str(artifact_map["vertices_f64"]), str(geometry.manifest["vertices_sha256"])
    )
    faces_path, faces = _checked_artifact(
        geometry_root, str(artifact_map["faces_u32"]), str(geometry.manifest["faces_sha256"])
    )
    points_path, points = _checked_artifact(
        geometry_root, str(artifact_map["points_f32"]), point_pool_sha256
    )
    sdf_path, sdf = _checked_artifact(geometry_root, str(artifact_map["sdf_f32"]), sdf_sha256)
    del vertices_path, faces_path, points_path, sdf_path

    stage_relative = f"requests/{request_id}"
    stage = (shape_root / stage_relative).resolve()
    if not stage.is_relative_to(shape_root):
        raise ShapeRequestError("stage_path_invalid", "request staging escaped the Shape data root")
    geometry_manifest = _canonical_json(geometry.manifest)
    staged_payloads = {
        "request.json": _canonical_json(spec),
        "geometry-manifest.json": geometry_manifest,
        "vertices.f64le": vertices,
        "faces.u32le": faces,
        "points.f32le": points,
        "sdf.f32le": sdf,
    }
    row = ShapeDesignRequest(
        request_id=request_id,
        geometry_id=geometry.geometry_id,
        request_sha256=request_sha256,
        request_spec=spec,
        stage_relative_path=stage_relative,
        job_id=None,
    )
    session.add(row)
    created: list[Path] = []
    try:
        await session.flush()
        for filename, payload in staged_payloads.items():
            try:
                path = stage / filename
                if _publish(path, payload):
                    created.append(path)
            except RuntimeError as exc:
                if "immutable Shape artifact conflict" not in str(exc):
                    raise
                raise ShapeRequestError(
                    "request_id_conflict",
                    "client request ID is already bound to different staged bytes",
                ) from exc
        os.chmod(stage, 0o550)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        _cleanup_new_stage(stage, created)
        existing = await session.get(ShapeDesignRequest, request_id)
        if existing is None or existing.request_sha256 != request_sha256:
            raise ShapeRequestError(
                "request_id_conflict",
                "client request ID was concurrently bound to different scientific intent",
            )
        return _staged(existing, data_root=data_root, name=submitted.name)
    except BaseException:
        await session.rollback()
        _cleanup_new_stage(stage, created)
        raise
    return _staged(row, data_root=data_root, name=submitted.name)


def _staged(row: ShapeDesignRequest, *, data_root: Path, name: str) -> StagedShapeRequest:
    stage = (data_root / "shape_blueprint" / row.stage_relative_path).resolve()
    params: dict[str, object] = {
        "modification_mode": "shape_blueprint",
        "shape_request_id": row.request_id,
        "shape_request_sha256": row.request_sha256,
        "shape_request_path": str(stage / "request.json"),
        "shape_geometry_manifest_path": str(stage / "geometry-manifest.json"),
        "shape_vertices_path": str(stage / "vertices.f64le"),
        "shape_faces_path": str(stage / "faces.u32le"),
        "shape_points_path": str(stage / "points.f32le"),
        "shape_sdf_path": str(stage / "sdf.f32le"),
        "shape_geometry_id": row.geometry_id,
        "shape_geometry_sha256": row.request_spec["geometry_sha256"],
        "shape_point_pool_sha256": row.request_spec["point_pool_sha256"],
        "shape_target_length": row.request_spec["target_length"],
        "shape_num_backbones": row.request_spec["num_backbones"],
        "shape_sequences_per_backbone": row.request_spec["sequences_per_backbone"],
        "shape_seed": row.request_spec["seed"],
        "shape_generator": "rfd3",
        "shape_sequence_engines": "proteinmpnn,fampnn",
        "shape_predictor": "esmfold2",
        "shape_guidance_profile": row.request_spec.get("guidance_profile", {}).get("id", "rfd3_transfer_v1"),
        "msa_provider": "local",
    }
    return StagedShapeRequest(
        request_id=row.request_id,
        request_sha256=row.request_sha256,
        model_id="protein_modification_experimental",
        mode="shape_blueprint",
        name=name,
        stage_dir=str(stage),
        launch_params=params,
    )
