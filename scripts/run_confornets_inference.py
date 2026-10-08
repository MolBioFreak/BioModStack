#!/usr/bin/env python3
"""Run upstream ConforNets and normalize real artifacts for BioModStack.

This wrapper intentionally does not fabricate outputs. It invokes the pinned
upstream ConforNets scripts, then fails if no real CIF conformers were produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_TASKS = {"diversity", "mse", "transfer"}


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(cmd: list[str], cwd: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(cmd) + "\n")
        log.flush()
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log.write(proc.stdout)
        if proc.returncode != 0:
            log.write(f"\nCommand failed with exit code {proc.returncode}\n")
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout)


def _copytree_overwrite(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _load_request(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    task = str(payload.get("task") or "diversity").lower()
    if task not in VALID_TASKS:
        raise SystemExit(f"Unsupported ConforNets task {task!r}")
    payload["task"] = task
    return payload


def _common_upstream_args(request: dict[str, Any], assets_dir: Path, raw_dir: Path) -> list[str]:
    params = request.get("params") or {}
    args = [
        "--checkpoint",
        str(params.get("checkpoint_path") or ""),
        "--output-dir",
        str(raw_dir),
        "--num-recycles",
        str(params.get("num_recycles", 0)),
        "--benchmark",
        str(request["benchmark"]),
        "--assets-dir",
        str(assets_dir),
        "--test-case",
        str(request["test_case"]),
    ]
    config_yaml = str(params.get("config_yaml") or "").strip()
    if config_yaml:
        args.extend(["--config-yaml", config_yaml])
    if _bool(params.get("compute_confidence")):
        args.append("--compute-confidence")
    if _bool(params.get("save_full_confidence")):
        args.append("--save-full-confidence")
    return args


def _save_steps(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _build_run_command(request: dict[str, Any], assets_dir: Path, raw_dir: Path) -> list[str]:
    repo_path = Path(request.get("params", {}).get("confornets_repo_path") or "")
    if not repo_path.exists() or not repo_path.is_dir():
        raise SystemExit(f"ConforNets repo path does not exist: {repo_path}")
    task = request["task"]
    params = request.get("params") or {}
    common = _common_upstream_args(request, assets_dir, raw_dir)
    scripts = repo_path / "scripts"

    if task == "diversity":
        cmd = [sys.executable, str(scripts / "run_diversity.py"), *common]
        cmd.extend([
            "--k-confornets",
            str(params.get("k_confornets", 2)),
            "--max-steps",
            str(params.get("max_steps", 21)),
            "--num-runs",
            str(params.get("num_runs", 2)),
            "--num-samples",
            str(params.get("num_samples", 5)),
            "--num-steps",
            str(params.get("num_diffusion_steps", 200)),
            "--lr",
            str(params.get("lr", 0.001)),
            "--grad-clip",
            str(params.get("grad_clip", 10.0)),
            "--save-steps",
            *_save_steps(params.get("save_steps", "5,10,15,20")),
        ])
        return cmd

    if task == "mse":
        if not request.get("references"):
            raise SystemExit("ConforNets MSE task requires at least one staged reference")
        cmd = [sys.executable, str(scripts / "run_mse_training.py"), *common]
        cmd.extend([
            "--max-steps",
            str(params.get("max_steps", 300)),
            "--num-runs",
            str(params.get("num_runs", 2)),
            "--num-samples",
            str(params.get("num_samples", 5)),
            "--lr",
            str(params.get("lr", 0.002)),
        ])
        return cmd

    confornet_path = str(params.get("confornet_path") or "").strip()
    mse_dir = str(params.get("mse_dir") or "").strip()
    if bool(confornet_path) == bool(mse_dir):
        raise SystemExit("ConforNets transfer requires exactly one of confornet_path or mse_dir")
    cmd = [sys.executable, str(scripts / "run_transfer.py"), *common]
    if confornet_path:
        cmd.extend(["--confornet-path", confornet_path])
    else:
        cmd.extend(["--mse-dir", mse_dir])
        source = str(params.get("source_test_cases") or "").strip()
        if source:
            cmd.extend(["--source", source])
    cmd.extend(["--num-samples", str(params.get("num_samples", 5))])
    return cmd


def _run_preprocess(request: dict[str, Any], assets_dir: Path, log_path: Path) -> None:
    repo_path = Path(request.get("params", {}).get("confornets_repo_path") or "")
    if not repo_path.exists() or not repo_path.is_dir():
        raise SystemExit(f"ConforNets repo path does not exist: {repo_path}")
    cmd = [
        sys.executable,
        str(repo_path / "preprocess.py"),
        "--benchmark",
        str(request["benchmark"]),
        "--assets-dir",
        str(assets_dir),
    ]
    if _bool((request.get("params") or {}).get("skip_msa")):
        cmd.append("--skip-msa")
    _run(cmd, cwd=repo_path, log_path=log_path)


def _normalize_outputs(request: dict[str, Any], raw_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    conformer_dir = output_dir / "conformers"
    states_dir = output_dir / "states"
    confidence_dir = output_dir / "confidence"
    conformer_dir.mkdir(parents=True, exist_ok=True)
    states_dir.mkdir(parents=True, exist_ok=True)
    confidence_dir.mkdir(parents=True, exist_ok=True)

    samples: list[dict[str, Any]] = []
    cif_paths = sorted(path for path in raw_dir.rglob("*.cif") if path.is_file())
    if not cif_paths:
        raise SystemExit("Upstream ConforNets produced no CIF conformers; refusing to publish empty results")

    for frame_index, src in enumerate(cif_paths):
        sample_id = f"cn_{frame_index:05d}_{src.stem}"
        dst = conformer_dir / f"{sample_id}.cif"
        shutil.copy2(src, dst)
        samples.append({
            "sample_id": sample_id,
            "frame_index": frame_index,
            "relative_path": str(dst.relative_to(output_dir)),
            "source_relative_path": str(src.relative_to(raw_dir)),
            "sha256": _sha256_file(dst),
            "bytes": dst.stat().st_size,
            "format": "cif",
            "task": request["task"],
            "query_id": request["query_id"],
            "test_case": request["test_case"],
        })

    for src in sorted(raw_dir.rglob("*.pt")):
        if src.is_file():
            dst = states_dir / src.name
            if dst.exists():
                dst = states_dir / f"{src.parent.name}_{src.name}"
            shutil.copy2(src, dst)
    for src in sorted(raw_dir.rglob("*.csv")):
        if src.is_file():
            dst = confidence_dir / src.name
            if dst.exists():
                dst = confidence_dir / f"{src.parent.name}_{src.name}"
            shutil.copy2(src, dst)

    (output_dir / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    (output_dir / "ensemble_manifest.json").write_text(
        json.dumps({
            "schema_version": 1,
            "workflow": "confornets_experimental",
            "monomer_only": True,
            "query_id": request["query_id"],
            "sequence_sha256": request.get("input_hashes", {}).get("sequence_sha256"),
            "frame_count": len(samples),
            "samples_json": "samples.json",
            "conformer_dir": "conformers",
            "frame_index_base": 0,
            "references": request.get("references", []),
        }, indent=2),
        encoding="utf-8",
    )
    (output_dir / "landscape.json").write_text(
        json.dumps({
            "schema_version": 1,
            "workflow": "confornets_experimental",
            "status": "not_computed",
            "reason": "RMSD/free-energy landscape metrics require an explicit downstream analysis step; no synthetic landscape values are generated by the wrapper.",
            "sample_count": len(samples),
            "references": request.get("references", []),
        }, indent=2),
        encoding="utf-8",
    )
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description="Run upstream ConforNets and normalize BioModStack artifacts")
    parser.add_argument("--request", required=True)
    parser.add_argument("--assets-dir", default="")
    parser.add_argument("--output-dir", default="confornets_results")
    args = parser.parse_args()

    request_path = Path(args.request).resolve()
    request = _load_request(request_path)
    output_dir = Path(args.output_dir).resolve()
    raw_dir = output_dir / "raw"
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.assets_dir:
        assets_dir = Path(args.assets_dir).resolve()
    else:
        assets_dir = Path(request["assets_dir"]).resolve()
    if not assets_dir.exists():
        raise SystemExit(f"ConforNets assets directory not found: {assets_dir}")

    relocated_request = dict(request)
    relocated_request["assets_dir"] = str(assets_dir)
    (output_dir / "request.json").write_text(json.dumps(relocated_request, indent=2), encoding="utf-8")

    log_path = output_dir / "confornets_commands.log"
    started = datetime.now(timezone.utc).isoformat()
    _run_preprocess(relocated_request, assets_dir, log_path)
    run_cmd = _build_run_command(relocated_request, assets_dir, raw_dir)
    _run(run_cmd, cwd=Path(relocated_request["params"]["confornets_repo_path"]), log_path=log_path)

    samples = _normalize_outputs(relocated_request, raw_dir, output_dir)
    finished = datetime.now(timezone.utc).isoformat()
    provenance = {
        "schema_version": 1,
        "workflow": "confornets_experimental",
        "started_at": started,
        "finished_at": finished,
        "request": relocated_request,
        "upstream_repo_path": relocated_request["params"]["confornets_repo_path"],
        "commands_log": "confornets_commands.log",
        "raw_output_dir": "raw",
        "sample_count": len(samples),
        "monomer_only": True,
        "synthetic_outputs": False,
    }
    (output_dir / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    artifact_manifest = {
        "schema_version": 1,
        "workflow": "confornets_experimental",
        "samples_json": "samples.json",
        "landscape_json": "landscape.json",
        "ensemble_manifest_json": "ensemble_manifest.json",
        "provenance_json": "provenance.json",
        "commands_log": "confornets_commands.log",
        "conformer_dir": "conformers",
        "raw_output_dir": "raw",
        "sample_count": len(samples),
    }
    (output_dir / "artifact_manifest.json").write_text(json.dumps(artifact_manifest, indent=2), encoding="utf-8")
    print(f"Normalized {len(samples)} ConforNets CIF samples into {output_dir}")


if __name__ == "__main__":
    main()
