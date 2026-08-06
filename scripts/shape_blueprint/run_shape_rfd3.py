#!/usr/bin/env python3
"""Run the pinned Shape-guided RFD3 sampler from one validated request bundle."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import importlib.util as importlib_util
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping


BASE_SAMPLER_SHA256 = "e64fd42242422fa40ee0112a031911f462b13403b3f8d46ae49879d569b9314f"
PATCHED_SAMPLER_SHA256 = "bea19f55bc545963dd8834b6d7b22d5f7b6fd3ad9425e4cd3900cd7aa040a5ab"
FOUNDRY_VERSION = "0.1.9"
FOUNDRY_COMMIT = "a36d29c5c0d196a1c1c23349878683b6643da67d"
SHAPE_CTRL_COMMIT = "e1a518b61e216d3c597a46e5a151b9e24756e33e"
DEFAULT_CHECKPOINT = Path("/foundry/checkpoints/rfd3_latest.ckpt")
INSTALLED_SAMPLER = Path("/usr/local/lib/python3.12/dist-packages/rfd3/model/inference_sampler.py")
INSTALLED_SHAPE_SAMPLER = Path("/usr/local/lib/python3.12/dist-packages/bms_shape_sampler.py")
GUIDANCE_SOURCE = Path(__file__).with_name("shape_guidance.py")
SAMPLER_SOURCE = Path(__file__).with_name("rfd3_shape_sampler.py")
PROFILE_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "shape_blueprint" / "rfd3_profiles.json"


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _rfd3_runtime_identity() -> dict[str, object]:
    spec = importlib_util.find_spec("rfd3")
    if spec is None or spec.origin is None:
        raise ValueError("imported RFD3 package is unavailable")
    package_root = Path(spec.origin).resolve().parent
    if not package_root.is_dir():
        raise ValueError("imported RFD3 package root is unavailable")
    tree_sha256 = hashlib.sha256()
    files = sorted(path for path in package_root.rglob("*") if path.is_file() and not path.is_symlink())
    for path in files:
        tree_sha256.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        tree_sha256.update(b"\0")
        tree_sha256.update(path.read_bytes())
    try:
        rfd3_version = metadata.version("rfd3")
    except metadata.PackageNotFoundError:
        rfd3_version = "unpackaged"
    try:
        atomworks_version = metadata.version("atomworks")
    except metadata.PackageNotFoundError:
        atomworks_version = "unpackaged"
    try:
        rc_foundry_version = metadata.version("rc-foundry")
    except metadata.PackageNotFoundError:
        rc_foundry_version = FOUNDRY_VERSION
    return {
        "rfd3_imported_root": str(package_root),
        "rfd3_imported_version": rfd3_version,
        "rfd3_imported_tree_sha256": tree_sha256.hexdigest(),
        "rc_foundry_version": rc_foundry_version,
        "atomworks_version": atomworks_version,
    }


def _load_profile_registry() -> tuple[dict[str, dict[str, object]], str]:
    if PROFILE_REGISTRY_PATH.is_symlink() or not PROFILE_REGISTRY_PATH.is_file():
        raise ValueError(f"RFD3 profile registry is unavailable: {PROFILE_REGISTRY_PATH}")
    registry = json.loads(PROFILE_REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or registry.get("schema") != "bms_rfd3_profile_registry_v1":
        raise ValueError("RFD3 profile registry schema is invalid")
    raw_profiles = registry.get("profiles")
    if not isinstance(raw_profiles, dict):
        raise ValueError("RFD3 profile registry has no profiles")
    profiles = {str(profile_id): dict(profile) for profile_id, profile in raw_profiles.items() if isinstance(profile, dict)}
    registry_sha256 = hashlib.sha256(_canonical(registry)).hexdigest()
    return profiles, registry_sha256


RFD3_PROFILE_REGISTRY, PROFILE_REGISTRY_SHA256 = _load_profile_registry()


def _load(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"{path.name} must be a regular, single-link staged input")
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _profile_for_request(request: dict) -> dict[str, object]:
    raw = request.get("guidance_profile")
    profile_id = raw.get("id") if isinstance(raw, dict) else raw
    if not isinstance(profile_id, str):
        raise ValueError("Shape request lacks a guidance profile id")
    profile = RFD3_PROFILE_REGISTRY.get(profile_id)
    if profile is None or profile.get("status") != "active_control":
        raise ValueError(f"Shape guidance profile is unrecognized or unavailable: {profile_id}")
    registry_sha256 = request.get("guidance_profile_registry_sha256")
    if registry_sha256 != PROFILE_REGISTRY_SHA256:
        raise ValueError("Shape guidance profile registry hash mismatch")
    if isinstance(raw, dict) and raw != profile:
        raise ValueError("Shape guidance profile payload does not match the canonical registry")
    return dict(profile)


def validate_request_v2(request: dict, manifest: dict) -> tuple[dict, dict]:
    if request.get("schema") != "bms_shape_design_request_v2":
        raise ValueError("unsupported Shape request schema")
    _profile_for_request(request)
    if request.get("geometry_sha256") != manifest.get("geometry_sha256"):
        raise ValueError("request and manifest geometry identity mismatch")
    if request.get("point_pool_sha256") != manifest.get("point_pool_sha256"):
        raise ValueError("request and manifest point-pool identity mismatch")
    claimed = str(request.get("request_sha256") or "")
    unhashed = dict(request)
    unhashed.pop("request_sha256", None)
    if hashlib.sha256(_canonical(unhashed)).hexdigest() != claimed:
        raise ValueError("Shape request hash mismatch")
    if request.get("generator") != "rfd3":
        raise ValueError("Shape request does not select RFD3")
    length_policy = request.get("length_policy")
    if not isinstance(length_policy, dict) or length_policy.get("mode") not in {"fixed", "uniform_integer_range", "deterministic_range"}:
        raise ValueError("Shape length policy is invalid")
    minimum = length_policy.get("min")
    maximum = length_policy.get("max")
    if not isinstance(minimum, int) or not isinstance(maximum, int) or not 40 <= minimum <= maximum <= 600:
        raise ValueError("Shape length policy is outside [40, 600]")
    if length_policy["mode"] == "fixed" and minimum != maximum:
        raise ValueError("fixed Shape length policy requires min == max")
    count = request.get("num_backbones")
    seed = request.get("seed")
    if not isinstance(count, int) or not 1 <= count <= 200:
        raise ValueError("Shape backbone count is outside [1, 200]")
    if not isinstance(seed, int) or not 0 <= seed <= 2_147_483_647:
        raise ValueError("Shape seed is outside [0, 2147483647]")
    sequence_policy = request.get("sequence_policy", "auto")
    if sequence_policy not in {"auto", "skip", "external"}:
        raise ValueError("Shape sequence policy is invalid")
    if sequence_policy == "external" and request.get("sequence_engine") not in {"proteinmpnn", "fampnn"}:
        raise ValueError("Shape external sequence policy requires ProteinMPNN or FAMPNN")
    if sequence_policy != "external" and request.get("sequence_engine") is not None:
        raise ValueError("Shape sequence engine is only valid with sequence_policy=external")
    candidate_ids = request.get("candidate_ids")
    if candidate_ids is not None:
        if not isinstance(candidate_ids, list) or len(candidate_ids) != count or len(set(candidate_ids)) != count or any(
            not isinstance(candidate_id, str) or len(candidate_id) != 64 for candidate_id in candidate_ids
        ):
            raise ValueError("Shape child request candidate IDs are invalid")
    effective_seeds = request.get("candidate_effective_seeds")
    if effective_seeds is not None and (
        not isinstance(effective_seeds, list)
        or len(effective_seeds) != count
        or any(not isinstance(seed_value, int) or not 0 <= seed_value <= 2_147_483_647 for seed_value in effective_seeds)
    ):
        raise ValueError("Shape child request effective seeds are invalid")
    return request, manifest


def validate_request(request_path: Path, manifest_path: Path) -> tuple[dict, dict]:
    return validate_request_v2(_load(request_path), _load(manifest_path))


def _effective_profile(request: dict) -> dict:
    return _profile_for_request(request)


def _guidance_trace_summary(
    path: Path,
    *,
    expected_profile: dict,
    expected_registry_sha256: str,
    expected_active_count: int,
) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise RuntimeError("RFD3 emitted no Shape guidance step receipt")
    for row in rows:
        if row.get("schema") != "bms_rfd3_shape_guidance_step_v4":
            raise RuntimeError("RFD3 Shape guidance receipt schema mismatch")
        if row.get("guidance_profile") != expected_profile["id"]:
            raise RuntimeError("RFD3 Shape guidance profile receipt mismatch")
        if row.get("profile_registry_sha256") != expected_registry_sha256:
            raise RuntimeError("RFD3 Shape profile registry receipt mismatch")
        if row.get("shape_outside_weight") != expected_profile["outside_weight"]:
            raise RuntimeError("RFD3 Shape outside-weight receipt mismatch")
        if row.get("shape_chamfer_weight") != expected_profile["chamfer_weight"]:
            raise RuntimeError("RFD3 Shape Chamfer-weight receipt mismatch")
        if row.get("shape_connectivity_weight") != expected_profile["connectivity_weight"]:
            raise RuntimeError("RFD3 Shape connectivity-weight receipt mismatch")
        if row.get("shape_max_update_angstrom") is None:
            raise RuntimeError("RFD3 Shape update bound receipt is missing")
        if row.get("active_target_point_count") != expected_active_count:
            raise RuntimeError("RFD3 Shape active target count mismatch")
        digest = str(row.get("active_point_pool_sha256") or "")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise RuntimeError("RFD3 Shape active point-pool digest is invalid")
    digests = {row["active_point_pool_sha256"] for row in rows}
    if len(digests) != 1:
        raise RuntimeError("RFD3 Shape active point pool changed within one run")
    return {
        "guidance_trace_rows": len(rows),
        "active_target_point_count": expected_active_count,
        "active_point_pool_sha256": digests.pop(),
    }


def _verify_installed_sampler() -> str:
    installed_sampler_sha256 = _sha256(INSTALLED_SAMPLER)
    if installed_sampler_sha256 != PATCHED_SAMPLER_SHA256:
        raise ValueError(
            "installed RFD3 sampler hash mismatch: "
            f"expected {PATCHED_SAMPLER_SHA256}, got {installed_sampler_sha256}"
        )
    return installed_sampler_sha256


def _verify_installed_shape_sampler() -> str:
    if not INSTALLED_SHAPE_SAMPLER.is_file():
        raise ValueError(f"installed Shape sampler is unavailable: {INSTALLED_SHAPE_SAMPLER}")
    actual = _sha256(INSTALLED_SHAPE_SAMPLER)
    expected = _sha256(SAMPLER_SOURCE)
    if actual != expected:
        raise ValueError(f"installed Shape sampler hash mismatch: expected {expected}, got {actual}")
    return actual


def _bind_candidate_output_names(output_dir: Path, request: dict[str, Any]) -> None:
    candidate_ids = request.get("candidate_ids")
    if candidate_ids is None:
        return
    structures = sorted(output_dir.glob("*.cif.gz"))
    if len(structures) != len(candidate_ids):
        raise RuntimeError("RFD3 candidate output count does not match the immutable child candidate list")
    for source, candidate_id in zip(structures, candidate_ids, strict=True):
        target = output_dir / f"{candidate_id}.cif.gz"
        if source != target:
            if target.exists():
                raise RuntimeError("RFD3 candidate output identity collision")
            source.rename(target)
            source_json = output_dir / f"{source.name[:-7]}.json"
            target_json = output_dir / f"{candidate_id}.json"
            if source_json.exists():
                if target_json.exists():
                    raise RuntimeError("RFD3 candidate metadata identity collision")
                source_json.rename(target_json)


def run_shape_rfd3(
    *,
    request_path: Path,
    manifest_path: Path,
    points_path: Path,
    sdf_path: Path,
    output_dir: Path,
    receipt_path: Path,
    executable: str = "rfd3",
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    environment: Mapping[str, str] | None = None,
    num_timesteps: int = 200,
    guidance_step_size: float | None = None,
) -> dict:
    request, manifest = validate_request(request_path, manifest_path)
    profile = _effective_profile(request)
    runtime_identity = _rfd3_runtime_identity()
    if guidance_step_size is not None and guidance_step_size != profile["effective_step_size"]:
        raise ValueError("CLI guidance step size disagrees with immutable Shape guidance profile")
    guidance_step_size = float(profile["effective_step_size"])
    if _sha256(points_path) != manifest.get("point_pool_sha256"):
        raise ValueError("staged point-pool hash mismatch")
    if _sha256(sdf_path) != manifest.get("sdf_sha256"):
        raise ValueError("staged SDF hash mismatch")
    if checkpoint_path.is_symlink() or not checkpoint_path.is_file():
        raise ValueError("RFD3 checkpoint is unavailable or indirect")
    installed_sampler_sha256 = _verify_installed_sampler()
    installed_shape_sampler_sha256 = _verify_installed_shape_sampler() if bool(profile["guidance_enabled"]) else None
    if not 10 <= num_timesteps <= 500:
        raise ValueError("RFD3 timestep count is outside [10, 500]")
    if not 0.0 <= guidance_step_size <= 1.0:
        raise ValueError("Shape guidance step size is outside [0, 1]")

    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir.parent / "shape_rfd3_input.json"
    length_policy = request["length_policy"]
    input_spec = {
        "shape_blueprint": {
            "dialect": 2,
            "length": f"{length_policy['min']}-{length_policy['max']}",
        }
    }
    input_path.write_bytes(_canonical(input_spec) + b"\n")
    guidance_enabled = bool(profile["guidance_enabled"])
    guidance_receipt = output_dir / "shape_guidance_steps.jsonl"
    effective_seeds = request.get("candidate_effective_seeds") or [request["seed"]]
    command = [
        executable,
        f"inputs={input_path.resolve()}",
        f"out_dir={output_dir.resolve()}",
        f"n_batches={request['num_backbones']}",
        "diffusion_batch_size=1",
        f"seed={effective_seeds[0]}",
        f"ckpt_path={checkpoint_path.resolve()}",
        f"inference_sampler.num_timesteps={num_timesteps}",
    ]
    if guidance_enabled:
        command.extend(
            [
                "inference_sampler.kind=shape",
                f"+inference_sampler.shape_step_size={guidance_step_size}",
                f"+inference_sampler.shape_max_update={profile['max_update_angstrom']}",
                f"+inference_sampler.shape_target_point_count={profile['target_point_count']}",
                f"+inference_sampler.shape_target_point_seed={request['seed']}",
                f"+inference_sampler.shape_guidance_profile={profile['id']}",
                f"+inference_sampler.shape_profile_registry_sha256={PROFILE_REGISTRY_SHA256}",
                f"+inference_sampler.shape_source_shape_weight={profile['source_shape_weight']}",
                f"+inference_sampler.shape_source_guide_scale={profile['source_guide_scale']}",
                f"+inference_sampler.shape_rfd3_transfer_coefficient={profile['rfd3_transfer_coefficient']}",
                f"+inference_sampler.shape_outside_weight={profile['outside_weight']}",
                f"+inference_sampler.shape_chamfer_weight={profile['chamfer_weight']}",
                f"+inference_sampler.shape_connectivity_weight={profile['connectivity_weight']}",
                f"+inference_sampler.shape_manifest_path={manifest_path.resolve()}",
                f"+inference_sampler.shape_points_path={points_path.resolve()}",
                f"+inference_sampler.shape_sdf_path={sdf_path.resolve()}",
                f"+inference_sampler.shape_expected_geometry_sha256={request['geometry_sha256']}",
                f"+inference_sampler.shape_expected_point_pool_sha256={request['point_pool_sha256']}",
                f"+inference_sampler.shape_receipt_path={guidance_receipt.resolve()}",
            ]
        )
    run_environment = os.environ.copy()
    if environment:
        run_environment.update(environment)
    try:
        subprocess.run(command, check=True, env=run_environment)
    except subprocess.CalledProcessError as exc:
        failure = {
            "schema": "bms_shape_rfd3_runtime_receipt_v1",
            "status": "failed",
            "failure_kind": "rfd3_process_failure",
            "exit_code": exc.returncode,
            "request_id": request.get("request_id"),
            "request_sha256": request.get("request_sha256"),
            "parent_request_sha256": request.get("parent_request_sha256"),
            "batch_plan_sha256": request.get("batch_plan_sha256"),
            "batch_id": request.get("batch_id"),
            "batch_index": request.get("batch_index"),
            "candidate_ids": request.get("candidate_ids"),
            "candidate_effective_seeds": request.get("candidate_effective_seeds"),
            "guidance_profile_id": profile["id"],
            "guidance_enabled": guidance_enabled,
            "guidance_profile_registry_sha256": request.get("guidance_profile_registry_sha256"),
            "length_policy": request.get("length_policy"),
            "sequence_policy": request.get("sequence_policy", "auto"),
            "sequence_engine": request.get("sequence_engine"),
            "validator_suite": request.get("validator_suite", []),
            "geometry_sha256": request.get("geometry_sha256"),
            "point_pool_sha256": request.get("point_pool_sha256"),
            "sdf_sha256": manifest.get("sdf_sha256"),
            "base_sampler_sha256": BASE_SAMPLER_SHA256,
            "patched_sampler_sha256": installed_sampler_sha256,
            "sampler_hash_verified": True,
            "installed_shape_sampler_sha256": installed_shape_sampler_sha256,
            "shape_sampler_hash_verified": guidance_enabled,
            "rfd3_version": runtime_identity["rfd3_imported_version"],
            "rfd3_imported_root": runtime_identity["rfd3_imported_root"],
            "rfd3_imported_tree_sha256": runtime_identity["rfd3_imported_tree_sha256"],
            "rc_foundry_version": runtime_identity["rc_foundry_version"],
            "atomworks_version": runtime_identity["atomworks_version"],
            "checkpoint_path": str(checkpoint_path),
            "checkpoint_sha256": _sha256(checkpoint_path),
            "seed": request["seed"],
            "num_timesteps": num_timesteps,
            "guidance_step_size": guidance_step_size,
            "effective_step_size": guidance_step_size,
            "shape_outside_weight": profile["outside_weight"],
            "shape_chamfer_weight": profile["chamfer_weight"],
            "shape_connectivity_weight": profile["connectivity_weight"],
            "shape_max_update_angstrom": profile["max_update_angstrom"],
            "guidance_profile": profile,
            "target_sampling": profile["target_sampling"],
            "guidance_decay": profile["guidance_decay"],
            "gradient_scaling": profile["gradient_scaling"],
            "outside_reduction": profile["outside_reduction"],
            "connectivity_weight": profile["connectivity_weight"],
            "integration_state": "delta_L",
            "guidance_reference": "X_denoised_L",
            "native_update_equation": "X_next=X_noisy+step_scale*d_t*delta_L_guided",
            "foundry_version_pin": FOUNDRY_VERSION,
            "foundry_commit": FOUNDRY_COMMIT,
            "shape_ctrl_commit": SHAPE_CTRL_COMMIT,
            "shape_guidance_sha256": _sha256(GUIDANCE_SOURCE),
            "shape_sampler_sha256": _sha256(SAMPLER_SOURCE),
        }
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_bytes(_canonical(failure) + b"\n")
        raise
    _bind_candidate_output_names(output_dir, request)
    structures = sorted(output_dir.glob("*.cif.gz"))
    if len(structures) != request["num_backbones"]:
        raise RuntimeError(
            f"RFD3 emitted {len(structures)} backbones; expected {request['num_backbones']}"
        )
    if guidance_enabled:
        if not guidance_receipt.is_file() or guidance_receipt.stat().st_size == 0:
            raise RuntimeError("RFD3 emitted no Shape guidance step receipt")
        expected_active_count = int(profile["target_point_count"])
        trace_summary = _guidance_trace_summary(
            guidance_receipt,
            expected_profile=profile,
            expected_registry_sha256=PROFILE_REGISTRY_SHA256,
            expected_active_count=expected_active_count,
        )
    else:
        guidance_receipt.touch()
        trace_summary = {
            "guidance_trace_rows": 0,
            "active_target_point_count": 0,
            "active_point_pool_sha256": None,
        }
    receipt = {
        "schema": "bms_shape_rfd3_runtime_receipt_v1",
        "status": "completed",
        "request_id": request.get("request_id"),
        "request_sha256": request.get("request_sha256"),
        "parent_request_sha256": request.get("parent_request_sha256"),
        "batch_plan_sha256": request.get("batch_plan_sha256"),
        "batch_id": request.get("batch_id"),
        "batch_index": request.get("batch_index"),
        "candidate_ids": request.get("candidate_ids"),
        "candidate_effective_seeds": request.get("candidate_effective_seeds"),
        "guidance_profile_id": profile["id"],
        "guidance_enabled": guidance_enabled,
        "guidance_profile_registry_sha256": request.get("guidance_profile_registry_sha256"),
        "sequence_policy": request.get("sequence_policy", "auto"),
        "sequence_engine": request.get("sequence_engine"),
        "validator_suite": request.get("validator_suite", []),
        "geometry_sha256": request.get("geometry_sha256"),
        "point_pool_sha256": request.get("point_pool_sha256"),
        "sdf_sha256": manifest.get("sdf_sha256"),
        "base_sampler_sha256": BASE_SAMPLER_SHA256,
        "patched_sampler_sha256": installed_sampler_sha256,
        "sampler_hash_verified": True,
        "installed_shape_sampler_sha256": installed_shape_sampler_sha256,
        "shape_sampler_hash_verified": guidance_enabled,
        "rfd3_version": runtime_identity["rfd3_imported_version"],
        "rfd3_imported_root": runtime_identity["rfd3_imported_root"],
        "rfd3_imported_tree_sha256": runtime_identity["rfd3_imported_tree_sha256"],
        "rc_foundry_version": runtime_identity["rc_foundry_version"],
        "atomworks_version": runtime_identity["atomworks_version"],
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": _sha256(checkpoint_path),
        "length_policy": request["length_policy"],
        "target_length": request.get("target_length"),
        "requested_backbone_count": request["num_backbones"],
        "output_backbone_count": len(structures),
        "seed": request["seed"],
        "num_timesteps": num_timesteps,
        "guidance_step_size": guidance_step_size,
        "effective_step_size": guidance_step_size,
        "shape_outside_weight": profile["outside_weight"],
        "shape_chamfer_weight": profile["chamfer_weight"],
        "shape_connectivity_weight": profile["connectivity_weight"],
        "shape_max_update_angstrom": profile["max_update_angstrom"],
        "guidance_profile": profile,
        "target_sampling": profile["target_sampling"],
        **trace_summary,
        "guidance_decay": profile["guidance_decay"],
        "gradient_scaling": profile["gradient_scaling"],
        "outside_reduction": profile["outside_reduction"],
        "connectivity_weight": profile["connectivity_weight"],
        "integration_state": "delta_L",
        "guidance_reference": "X_denoised_L",
        "native_update_equation": "X_next=X_noisy+step_scale*d_t*delta_L_guided",
        "foundry_version_pin": FOUNDRY_VERSION,
        "foundry_commit": FOUNDRY_COMMIT,
        "shape_ctrl_commit": SHAPE_CTRL_COMMIT,
        "shape_guidance_sha256": _sha256(GUIDANCE_SOURCE),
        "shape_sampler_sha256": _sha256(SAMPLER_SOURCE),
        "output_backbones": [path.name for path in structures],
        "guidance_steps_receipt": guidance_receipt.name,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(_canonical(receipt) + b"\n")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--sdf", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--executable", default="rfd3")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--num-timesteps", type=int, default=200)
    parser.add_argument("--guidance-step-size", type=float)
    args = parser.parse_args()
    run_shape_rfd3(
        request_path=args.request,
        manifest_path=args.manifest,
        points_path=args.points,
        sdf_path=args.sdf,
        output_dir=args.output_dir,
        receipt_path=args.receipt,
        executable=args.executable,
        checkpoint_path=args.checkpoint,
        num_timesteps=args.num_timesteps,
        guidance_step_size=args.guidance_step_size,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
