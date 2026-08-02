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
FOUNDRY_VERSION = "0.1.9"
FOUNDRY_COMMIT = "a36d29c5c0d196a1c1c23349878683b6643da67d"
SHAPE_CTRL_COMMIT = "e1a518b61e216d3c597a46e5a151b9e24756e33e"
DEFAULT_CHECKPOINT = Path("/foundry/checkpoints/rfd3_latest.ckpt")
GUIDANCE_SOURCE = Path(__file__).with_name("shape_guidance.py")
SAMPLER_SOURCE = Path(__file__).with_name("rfd3_shape_sampler.py")


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
    return request, manifest


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
    guidance_step_size: float = 0.2,
) -> dict:
    request, manifest = validate_request(request_path, manifest_path)
    if _sha256(points_path) != manifest.get("point_pool_sha256"):
        raise ValueError("staged point-pool hash mismatch")
    if _sha256(sdf_path) != manifest.get("sdf_sha256"):
        raise ValueError("staged SDF hash mismatch")
    if checkpoint_path.is_symlink() or not checkpoint_path.is_file():
        raise ValueError("RFD3 checkpoint is unavailable or indirect")
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
        "+inference_sampler.shape_max_update=0.5",
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
            "checkpoint_path": str(checkpoint_path),
            "checkpoint_sha256": _sha256(checkpoint_path),
            "seed": request["seed"],
            "num_timesteps": num_timesteps,
            "guidance_step_size": guidance_step_size,
            "guidance_decay": "constant",
            "gradient_scaling": "raw",
            "outside_reduction": "sum",
            "connectivity_weight": 0.0,
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
        "rfd3_version": rfd3_version,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": _sha256(checkpoint_path),
        "target_length": request["target_length"],
        "requested_backbone_count": request["num_backbones"],
        "output_backbone_count": len(structures),
        "seed": request["seed"],
        "num_timesteps": num_timesteps,
        "guidance_step_size": guidance_step_size,
        "guidance_decay": "constant",
        "gradient_scaling": "raw",
        "outside_reduction": "sum",
        "connectivity_weight": 0.0,
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
    parser.add_argument("--guidance-step-size", type=float, default=0.2)
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
