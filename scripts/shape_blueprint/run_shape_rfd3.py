#!/usr/bin/env python3
"""Run the pinned Shape-guided RFD3 sampler from one validated request bundle."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
from typing import Mapping


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

PAPER_LIKE_RFD3_PROFILE = {
    "id": "paper_like_rfd3_v1",
    "paper_doi": "10.64898/2026.07.22.740177",
    "shape_ctrl_commit": SHAPE_CTRL_COMMIT,
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

LEGACY_RFD3_PROFILE = {
    "id": "rfd3_transfer_v1",
    "source_shape_weight": 1.0,
    "source_guide_scale": 1.0,
    "source_sdf_weight": 1.0,
    "source_chamfer_weight": 1.0,
    "source_target_point_count": 0,
    "target_sampling": "complete_immutable_uniform_interior_pool_v1",
    "rfd3_transfer_coefficient": 0.2,
    "effective_step_size": 0.2,
    "max_update_angstrom": 0.5,
    "guidance_decay": "constant",
    "gradient_scaling": "raw",
    "outside_reduction": "sum",
    "connectivity_weight": 0.0,
}


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


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


def validate_request(request_path: Path, manifest_path: Path) -> tuple[dict, dict]:
    request = _load(request_path)
    manifest = _load(manifest_path)
    if request.get("schema") != "bms_shape_design_request_v1":
        raise ValueError("unsupported Shape request schema")
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
    length = request.get("target_length")
    count = request.get("num_backbones")
    seed = request.get("seed")
    if not isinstance(length, int) or not 40 <= length <= 600:
        raise ValueError("Shape target length is outside [40, 600]")
    if not isinstance(count, int) or not 1 <= count <= 32:
        raise ValueError("Shape backbone count is outside [1, 32]")
    if not isinstance(seed, int) or not 0 <= seed <= 2_147_483_647:
        raise ValueError("Shape seed is outside [0, 2147483647]")
    profile = request.get("guidance_profile")
    if profile is not None and profile != PAPER_LIKE_RFD3_PROFILE:
        raise ValueError("Shape guidance profile is not a recognized immutable profile")
    return request, manifest


def _effective_profile(request: dict) -> dict:
    return dict(PAPER_LIKE_RFD3_PROFILE if request.get("guidance_profile") is not None else LEGACY_RFD3_PROFILE)


def _guidance_trace_summary(path: Path, *, expected_profile: dict, expected_active_count: int) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not rows:
        raise RuntimeError("RFD3 emitted no Shape guidance step receipt")
    for row in rows:
        if row.get("schema") != "bms_rfd3_shape_guidance_step_v4":
            raise RuntimeError("RFD3 Shape guidance receipt schema mismatch")
        if row.get("guidance_profile") != expected_profile["id"]:
            raise RuntimeError("RFD3 Shape guidance profile receipt mismatch")
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
    installed_shape_sampler_sha256 = _verify_installed_shape_sampler()
    if not 10 <= num_timesteps <= 500:
        raise ValueError("RFD3 timestep count is outside [10, 500]")
    if not 0.0 <= guidance_step_size <= 1.0:
        raise ValueError("Shape guidance step size is outside [0, 1]")

    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir.parent / "shape_rfd3_input.json"
    input_spec = {
        "shape_blueprint": {
            "dialect": 2,
            "length": f"{request['target_length']}-{request['target_length']}",
        }
    }
    input_path.write_bytes(_canonical(input_spec) + b"\n")
    guidance_receipt = output_dir / "shape_guidance_steps.jsonl"
    command = [
        executable,
        f"inputs={input_path.resolve()}",
        f"out_dir={output_dir.resolve()}",
        f"n_batches={request['num_backbones']}",
        "diffusion_batch_size=1",
        f"seed={request['seed']}",
        f"ckpt_path={checkpoint_path.resolve()}",
        "inference_sampler.kind=shape",
        f"inference_sampler.num_timesteps={num_timesteps}",
        f"+inference_sampler.shape_step_size={guidance_step_size}",
        f"+inference_sampler.shape_max_update={profile['max_update_angstrom']}",
        f"+inference_sampler.shape_target_point_count={profile['source_target_point_count']}",
        f"+inference_sampler.shape_target_point_seed={request['seed']}",
        f"+inference_sampler.shape_guidance_profile={profile['id']}",
        f"+inference_sampler.shape_source_shape_weight={profile['source_shape_weight']}",
        f"+inference_sampler.shape_source_guide_scale={profile['source_guide_scale']}",
        f"+inference_sampler.shape_rfd3_transfer_coefficient={profile['rfd3_transfer_coefficient']}",
        f"+inference_sampler.shape_sdf_weight={profile['source_sdf_weight']}",
        f"+inference_sampler.shape_chamfer_weight={profile['source_chamfer_weight']}",
        f"+inference_sampler.shape_manifest_path={manifest_path.resolve()}",
        f"+inference_sampler.shape_points_path={points_path.resolve()}",
        f"+inference_sampler.shape_sdf_path={sdf_path.resolve()}",
        f"+inference_sampler.shape_expected_geometry_sha256={request['geometry_sha256']}",
        f"+inference_sampler.shape_expected_point_pool_sha256={request['point_pool_sha256']}",
        f"+inference_sampler.shape_receipt_path={guidance_receipt.resolve()}",
    ]
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
            "geometry_sha256": request.get("geometry_sha256"),
            "point_pool_sha256": request.get("point_pool_sha256"),
            "sdf_sha256": manifest.get("sdf_sha256"),
            "base_sampler_sha256": BASE_SAMPLER_SHA256,
            "patched_sampler_sha256": installed_sampler_sha256,
            "sampler_hash_verified": True,
            "installed_shape_sampler_sha256": installed_shape_sampler_sha256,
            "shape_sampler_hash_verified": True,
            "checkpoint_path": str(checkpoint_path),
            "checkpoint_sha256": _sha256(checkpoint_path),
            "seed": request["seed"],
            "num_timesteps": num_timesteps,
            "guidance_step_size": guidance_step_size,
            "guidance_profile": profile,
            "target_sampling": profile["target_sampling"],
            "guidance_decay": "constant",
            "gradient_scaling": "raw",
            "outside_reduction": "sum",
            "connectivity_weight": 0.0,
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
    structures = sorted(output_dir.glob("*.cif.gz"))
    if len(structures) != request["num_backbones"]:
        raise RuntimeError(
            f"RFD3 emitted {len(structures)} backbones; expected {request['num_backbones']}"
        )
    if not guidance_receipt.is_file() or guidance_receipt.stat().st_size == 0:
        raise RuntimeError("RFD3 emitted no Shape guidance step receipt")
    expected_active_count = int(profile["source_target_point_count"] or manifest["point_count"])
    trace_summary = _guidance_trace_summary(
        guidance_receipt,
        expected_profile=profile,
        expected_active_count=expected_active_count,
    )
    try:
        rfd3_version = metadata.version("rfd3")
    except metadata.PackageNotFoundError:
        rfd3_version = "unknown"
    receipt = {
        "schema": "bms_shape_rfd3_runtime_receipt_v1",
        "status": "completed",
        "request_id": request.get("request_id"),
        "request_sha256": request.get("request_sha256"),
        "geometry_sha256": request.get("geometry_sha256"),
        "point_pool_sha256": request.get("point_pool_sha256"),
        "sdf_sha256": manifest.get("sdf_sha256"),
        "base_sampler_sha256": BASE_SAMPLER_SHA256,
        "patched_sampler_sha256": installed_sampler_sha256,
        "sampler_hash_verified": True,
        "installed_shape_sampler_sha256": installed_shape_sampler_sha256,
        "shape_sampler_hash_verified": True,
        "rfd3_version": rfd3_version,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": _sha256(checkpoint_path),
        "target_length": request["target_length"],
        "requested_backbone_count": request["num_backbones"],
        "output_backbone_count": len(structures),
        "seed": request["seed"],
        "num_timesteps": num_timesteps,
        "guidance_step_size": guidance_step_size,
        "guidance_profile": profile,
        "target_sampling": profile["target_sampling"],
        **trace_summary,
        "guidance_decay": "constant",
        "gradient_scaling": "raw",
        "outside_reduction": "sum",
        "connectivity_weight": 0.0,
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
