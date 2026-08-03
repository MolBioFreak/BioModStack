"""Typed Shape request normalization and server-owned staging."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from database import ShapeDesignGeometry, ShapeDesignRequest
from services.shape_resources import _publish


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "shape_blueprint" / "rfd3_profiles.json"
_ACTIVE_PROFILE_IDS = ("rfd3_unguided_control_v1", "rfd3_ca_shape_transfer_control_v1")
_BATCH_POLICY_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "shape_blueprint" / "rfd3_batch_policies.json"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _load_profile_registry() -> tuple[dict[str, dict[str, object]], str]:
    if _PROFILE_REGISTRY_PATH.is_symlink() or not _PROFILE_REGISTRY_PATH.is_file():
        raise RuntimeError(f"RFD3 profile registry is unavailable: {_PROFILE_REGISTRY_PATH}")
    registry = json.loads(_PROFILE_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or registry.get("schema") != "bms_rfd3_profile_registry_v1":
        raise RuntimeError("RFD3 profile registry schema is invalid")
    profiles = registry.get("profiles")
    if not isinstance(profiles, dict):
        raise RuntimeError("RFD3 profile registry has no profiles")
    normalized = {str(profile_id): dict(profile) for profile_id, profile in profiles.items() if isinstance(profile, dict)}
    if set(_ACTIVE_PROFILE_IDS) - set(normalized):
        raise RuntimeError("RFD3 profile registry is missing an active profile")
    registry_sha256 = hashlib.sha256(_canonical_json(registry)).hexdigest()
    return normalized, registry_sha256


RFD3_PROFILE_REGISTRY, RFD3_PROFILE_REGISTRY_SHA256 = _load_profile_registry()


def _allocation_policy_sha256(policy_id: str) -> str:
    if _BATCH_POLICY_REGISTRY_PATH.is_symlink() or not _BATCH_POLICY_REGISTRY_PATH.is_file():
        raise RuntimeError("RFD3 batch policy registry is unavailable")
    registry = json.loads(_BATCH_POLICY_REGISTRY_PATH.read_text(encoding="utf-8"))
    policies = registry.get("allocation_policies") if isinstance(registry, dict) else None
    policy = policies.get(policy_id) if isinstance(policies, dict) else None
    if not isinstance(policy, dict):
        raise ShapeRequestError("allocation_policy_unavailable", f"RFD3 allocation policy is unavailable: {policy_id}")
    return hashlib.sha256(_canonical_json(policy)).hexdigest()


def _profile_for_request(profile_id: str) -> dict[str, object]:
    profile = RFD3_PROFILE_REGISTRY.get(profile_id)
    if profile is None or profile.get("status") != "active_control":
        raise ShapeRequestError("guidance_profile_unavailable", f"RFD3 guidance profile is unavailable: {profile_id}")
    return dict(profile)


class ShapeLengthPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["fixed", "uniform_integer_range", "deterministic_range"] = "fixed"
    min: int = Field(ge=40, le=600)
    max: int = Field(ge=40, le=600)
    allocation_policy_id: Literal["fixed_length_v1", "balanced_bucket_v1"] | None = None
    allocation_policy_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_range(self) -> "ShapeLengthPolicy":
        if self.mode == "fixed" and self.min != self.max:
            raise ValueError("fixed Shape length policy requires min == max")
        if self.mode != "fixed" and self.min > self.max:
            raise ValueError("Shape length policy requires min <= max")
        expected_policy = "fixed_length_v1" if self.mode == "fixed" else "balanced_bucket_v1"
        if self.allocation_policy_id is None:
            self.allocation_policy_id = expected_policy
        elif self.allocation_policy_id != expected_policy:
            raise ValueError("Shape length policy allocation policy does not match its mode")
        return self


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
    target_length: int | None = Field(default=None, ge=40, le=600)
    length_policy: ShapeLengthPolicy | None = None
    num_backbones: int = Field(default=4, ge=1, le=200)
    sequences_per_backbone: int = Field(default=0, ge=0, le=8)
    seed: int = Field(default=0, ge=0, le=2_147_483_647)
    generator: Literal["rfd3"] = "rfd3"
    sequence_policy: Literal["auto", "skip", "external"] = "auto"
    sequence_engine: Literal["proteinmpnn", "fampnn"] | None = None
    sequence_engines: tuple[Literal["proteinmpnn", "fampnn"], ...] = ()
    predictor: Literal["esmfold2"] = "esmfold2"
    validator_suite: tuple[Literal["boltz2", "esmfold2", "protenix_v2"], ...] = (
        "boltz2",
        "esmfold2",
        "protenix_v2",
    )
    guidance_profile: Literal["rfd3_unguided_control_v1", "rfd3_ca_shape_transfer_control_v1"] = (
        "rfd3_ca_shape_transfer_control_v1"
    )

    @model_validator(mode="after")
    def normalize_contract(self) -> "SubmittedShapeRequest":
        if self.length_policy is None:
            if self.target_length is None:
                raise ValueError("target_length or length_policy is required")
            self.length_policy = ShapeLengthPolicy(mode="fixed", min=self.target_length, max=self.target_length)
        elif self.target_length is not None and (
            self.length_policy.mode != "fixed"
            or self.length_policy.min != self.target_length
            or self.length_policy.max != self.target_length
        ):
            raise ValueError("target_length conflicts with length_policy")
        if self.sequence_policy == "external" and self.sequence_engine is None:
            raise ValueError("sequence_engine is required when sequence_policy is external")
        if self.sequence_policy != "external" and self.sequence_engine is not None:
            raise ValueError("sequence_engine is only valid with sequence_policy=external")
        if self.sequence_policy == "skip" and self.sequences_per_backbone != 0:
            raise ValueError("sequence_policy=skip requires sequences_per_backbone=0")
        if self.sequence_policy == "external" and self.sequences_per_backbone == 0:
            raise ValueError("sequence_policy=external requires sequences_per_backbone > 0")
        if any(engine not in {"proteinmpnn", "fampnn"} for engine in self.sequence_engines):
            raise ValueError("only ProteinMPNN and FAMPNN are supported for Shape sequence design")
        return self


@dataclass(frozen=True)
class StagedShapeRequest:
    request_id: str
    request_sha256: str
    model_id: str
    mode: str
    name: str
    stage_dir: str
    launch_params: dict[str, object]


def _checked_artifact(root: Path, relative: str, expected_sha256: str) -> tuple[Path, bytes]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1:
        raise ShapeRequestError("geometry_artifact_invalid", "canonical geometry artifact is unavailable")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ShapeRequestError("geometry_artifact_hash_mismatch", "canonical geometry artifact hash mismatch")
    return path, payload


def _validate_sequence_engines(engines: tuple[str, ...]) -> None:
    if len(set(engines)) != len(engines):
        raise ShapeRequestError("sequence_engines_duplicate", "sequence engines must be unique")


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

    profile = _profile_for_request(submitted.guidance_profile)
    if submitted.length_policy is None:
        raise ShapeRequestError("length_policy_missing", "normalized Shape length policy is missing")
    length_policy = submitted.length_policy.model_dump(mode="json")
    allocation_policy_id = str(length_policy["allocation_policy_id"])
    allocation_policy_sha256 = _allocation_policy_sha256(allocation_policy_id)
    if length_policy.get("allocation_policy_sha256") not in {None, allocation_policy_sha256}:
        raise ShapeRequestError("allocation_policy_hash_mismatch", "Shape allocation policy hash does not match the canonical registry")
    length_policy["allocation_policy_sha256"] = allocation_policy_sha256
    spec = {
        "schema": "bms_shape_design_request_v2",
        "request_id": f"shape_{submitted.client_request_id}",
        "geometry_id": geometry.geometry_id,
        "geometry_sha256": geometry.geometry_sha256,
        "point_pool_sha256": point_pool_sha256,
        "geometry_manifest_sha256": manifest_sha256,
        "sdf_sha256": sdf_sha256,
        "sdf_sign": sdf_sign,
        "sdf_grid_shape": sdf_grid_shape,
        "length_policy": length_policy,
        "target_length": length_policy["min"] if length_policy["mode"] == "fixed" else None,
        "candidate_count_total": submitted.num_backbones,
        "child_batch_limit": 32,
        "seed_root": submitted.seed,
        "num_backbones": submitted.num_backbones,
        "candidate_batch_size": min(32, submitted.num_backbones),
        "sequences_per_backbone": submitted.sequences_per_backbone,
        "seed": submitted.seed,
        "generator": submitted.generator,
        "sequence_policy": submitted.sequence_policy,
        "sequence_engine": submitted.sequence_engine,
        "sequence_engines": list(submitted.sequence_engines),
        "predictor": submitted.predictor,
        "validator_suite": list(submitted.validator_suite),
        "guidance_profile": profile,
        "guidance_profile_registry_sha256": RFD3_PROFILE_REGISTRY_SHA256,
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
        "shape_length_policy": json.dumps(row.request_spec["length_policy"], sort_keys=True, separators=(",", ":")),
        "shape_target_length": row.request_spec.get("target_length") or row.request_spec["length_policy"]["min"],
        "shape_num_backbones": row.request_spec["num_backbones"],
        "shape_candidate_batch_size": row.request_spec["candidate_batch_size"],
        "shape_sequences_per_backbone": row.request_spec["sequences_per_backbone"],
        "shape_sequence_policy": row.request_spec["sequence_policy"],
        "shape_sequence_engine": row.request_spec.get("sequence_engine"),
        "shape_validator_suite": ",".join(row.request_spec["validator_suite"]),
        "shape_seed": row.request_spec["seed"],
        "shape_generator": "rfd3",
        "shape_sequence_engines": ",".join(row.request_spec.get("sequence_engines", [])),
        "shape_predictor": "esmfold2",
        "shape_guidance_profile": row.request_spec["guidance_profile"]["id"],
        "shape_guidance_profile_registry_sha256": row.request_spec["guidance_profile_registry_sha256"],
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
