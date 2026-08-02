#!/usr/bin/env python3
"""Run upstream ConforNets and normalize real artifacts for BioModStack.

This wrapper intentionally does not fabricate outputs. It invokes the pinned
upstream ConforNets scripts, then fails if no real CIF conformers were produced.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shlex
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


def _runtime_source_records(repo_path: Path, task: str) -> list[dict[str, Any]]:
    task_sources = {
        "diversity": ("scripts/run_diversity.py", "confornet/inference/diversity.py"),
        "mse": ("scripts/run_mse_training.py", "confornet/inference/mse_training.py"),
        "transfer": ("scripts/run_transfer.py",),
    }
    relatives = (
        "preprocess.py",
        "confornet/utils/io.py",
        "confornet/utils/cm_coordinate_ledger.py",
        *task_sources[task],
    )
    records: list[dict[str, Any]] = []
    for relative in relatives:
        path = (repo_path / relative).resolve(strict=True)
        path.relative_to(repo_path.resolve(strict=True))
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"canonical executed source is unavailable: {relative}")
        records.append({
            "relative_path": relative, "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        })
    return records


def _run(cmd: list[str], cwd: Path, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(cmd) + "\n")
        log.flush()
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(cwd) + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
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
    # Upstream full-confidence tensors are only meaningful when confidence is computed.
    # Treat save_full_confidence as an implicit confidence request, but never invent
    # confidence values later if upstream does not emit them.
    if _bool(params.get("compute_confidence")) or _bool(params.get("save_full_confidence")):
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


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _copy_unique(src: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    if dst.exists():
        dst = dst_dir / f"{src.parent.name}_{src.name}"
    shutil.copy2(src, dst)
    return dst


def _parse_confidence_rows(csv_paths: list[Path]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for csv_path in csv_paths:
        try:
            with csv_path.open("r", newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                fieldnames = set(reader.fieldnames or [])
                if not fieldnames.intersection({"plddt", "gplddt", "gpde", "ptm", "iptm"}):
                    continue
                for row_index, row in enumerate(reader):
                    metrics: dict[str, Any] = {}
                    for key, value in row.items():
                        if key is None or key in {"sample", "sample_id", "id", "name", "path", "file", "filename"}:
                            continue
                        numeric = _safe_float(value)
                        if numeric is not None:
                            metrics[key.strip()] = numeric
                    if not metrics:
                        continue
                    sample_key = str(
                        row.get("sample")
                        or row.get("sample_id")
                        or row.get("id")
                        or row.get("name")
                        or row.get("path")
                        or row.get("file")
                        or row.get("filename")
                        or row_index
                    ).strip()
                    keys = {sample_key, Path(sample_key).stem, str(row_index), f"sample_{row_index}"}
                    if sample_key.isdigit():
                        keys.add(f"sample_{int(sample_key)}")
                        keys.add(f"cn_{int(sample_key):05d}_sample_{int(sample_key)}")
                    for key in keys:
                        if key:
                            by_key[key] = dict(metrics)
        except Exception:
            continue
    return by_key


def _sample_lookup_keys(sample: dict[str, Any]) -> set[str]:
    keys = {
        str(sample.get("sample_id") or ""),
        str(sample.get("frame_index") or ""),
        f"sample_{sample.get('frame_index')}",
        Path(str(sample.get("relative_path") or "")).stem,
        Path(str(sample.get("source_relative_path") or "")).stem,
    }
    sample_id = str(sample.get("sample_id") or "")
    match = re.search(r"sample[_-]?(\d+)$", sample_id)
    if match:
        keys.add(match.group(1))
        keys.add(f"sample_{int(match.group(1))}")
    return {key for key in keys if key and key != "sample_None"}


def _parse_ca_coordinates(path: Path) -> list[tuple[float, float, float]]:
    coords: list[tuple[float, float, float]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.startswith(("ATOM", "HETATM")):
                continue
            # PDB fixed-column format.
            if len(line) >= 54 and line[12:16].strip() == "CA":
                try:
                    coords.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
                    continue
                except ValueError:
                    pass
            # Minimal mmCIF rows in tests/upstream text exports.
            tokens = shlex.split(line)
            if "CA" not in tokens[:8]:
                continue
            for offset in ((-4, -3, -2), (-3, -2, -1), (6, 7, 8), (10, 11, 12)):
                try:
                    coords.append((float(tokens[offset[0]]), float(tokens[offset[1]]), float(tokens[offset[2]])))
                    break
                except (IndexError, ValueError):
                    continue
    except Exception:
        return []
    return coords


def _ca_rmsd(path_a: Path, path_b: Path) -> float | None:
    coords_a = _parse_ca_coordinates(path_a)
    coords_b = _parse_ca_coordinates(path_b)
    n = min(len(coords_a), len(coords_b))
    if n <= 0:
        return None
    coords_a = coords_a[:n]
    coords_b = coords_b[:n]
    try:
        import numpy as np  # type: ignore

        p = np.asarray(coords_a, dtype=float)
        q = np.asarray(coords_b, dtype=float)
        p_centered = p - p.mean(axis=0)
        q_centered = q - q.mean(axis=0)
        covariance = p_centered.T @ q_centered
        v, _s, wt = np.linalg.svd(covariance)
        handedness = np.sign(np.linalg.det(v @ wt))
        correction = np.diag([1.0, 1.0, float(handedness) if handedness else 1.0])
        aligned = p_centered @ v @ correction @ wt
        rmsd = float(np.sqrt(np.mean(np.sum((aligned - q_centered) ** 2, axis=1))))
        return round(rmsd, 6)
    except Exception:
        centroid_a = tuple(sum(axis) / n for axis in zip(*coords_a))
        centroid_b = tuple(sum(axis) / n for axis in zip(*coords_b))
        total = 0.0
        for ca, cb in zip(coords_a, coords_b):
            centered_a = tuple(ca[idx] - centroid_a[idx] for idx in range(3))
            centered_b = tuple(cb[idx] - centroid_b[idx] for idx in range(3))
            total += sum((centered_a[idx] - centered_b[idx]) ** 2 for idx in range(3))
        return round(math.sqrt(total / n), 6)


def _mds_coordinates(distance_matrix: list[list[float]]) -> list[dict[str, float]]:
    n = len(distance_matrix)
    if n == 0:
        return []
    if n == 1:
        return [{"x": 0.0, "y": 0.0}]
    if n == 2:
        distance = float(distance_matrix[0][1])
        return [{"x": round(-distance / 2.0, 6), "y": 0.0}, {"x": round(distance / 2.0, 6), "y": 0.0}]
    try:
        import numpy as np  # type: ignore

        d = np.asarray(distance_matrix, dtype=float)
        j = np.eye(n) - np.ones((n, n)) / n
        b = -0.5 * j @ (d ** 2) @ j
        values, vectors = np.linalg.eigh(b)
        order = np.argsort(values)[::-1]
        coords = np.zeros((n, 2), dtype=float)
        for out_idx, eig_idx in enumerate(order[:2]):
            eig_value = max(float(values[eig_idx]), 0.0)
            coords[:, out_idx] = vectors[:, eig_idx] * math.sqrt(eig_value)
        return [{"x": round(float(x), 6), "y": round(float(y), 6)} for x, y in coords]
    except Exception:
        midpoint = (n - 1) / 2.0
        return [{"x": round(idx - midpoint, 6), "y": 0.0} for idx in range(n)]


def _normalize_outputs(request: dict[str, Any], raw_dir: Path, output_dir: Path) -> list[dict[str, Any]]:
    conformer_dir = output_dir / "conformers"
    states_dir = output_dir / "states"
    confidence_dir = output_dir / "confidence"
    evaluation_dir = output_dir / "evaluation"
    conformer_dir.mkdir(parents=True, exist_ok=True)
    states_dir.mkdir(parents=True, exist_ok=True)
    confidence_dir.mkdir(parents=True, exist_ok=True)
    evaluation_dir.mkdir(parents=True, exist_ok=True)

    samples: list[dict[str, Any]] = []
    sample_paths: list[Path] = []
    cif_paths = sorted(path for path in raw_dir.rglob("*.cif") if path.is_file())
    if not cif_paths:
        raise SystemExit("Upstream ConforNets produced no CIF conformers; refusing to publish empty results")

    copied_confidence_csvs: list[Path] = []
    confidence_tensors_by_key: dict[str, str] = {}
    for src in sorted(raw_dir.rglob("*.pt")):
        if not src.is_file():
            continue
        if "confidence" in src.name.lower() or "confidence" in str(src.parent).lower():
            dst = _copy_unique(src, confidence_dir)
            rel = str(dst.relative_to(output_dir))
            stem = dst.stem.replace("_confidence", "").replace("-confidence", "")
            confidence_tensors_by_key[stem] = rel
            confidence_tensors_by_key[dst.stem] = rel
        else:
            _copy_unique(src, states_dir)
    for src in sorted(raw_dir.rglob("*.csv")):
        if src.is_file():
            dst = _copy_unique(src, confidence_dir)
            copied_confidence_csvs.append(dst)

    confidence_by_key = _parse_confidence_rows(copied_confidence_csvs)

    for frame_index, src in enumerate(cif_paths):
        sample_id = f"cn_{frame_index:05d}_{src.stem}"
        dst = conformer_dir / f"{sample_id}.cif"
        shutil.copy2(src, dst)
        sample = {
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
        }
        lookup_keys = _sample_lookup_keys(sample)
        confidence = next((confidence_by_key[key] for key in lookup_keys if key in confidence_by_key), None)
        tensor_path = next((confidence_tensors_by_key[key] for key in lookup_keys if key in confidence_tensors_by_key), None)
        if confidence or tensor_path:
            sample_confidence = dict(confidence or {})
            if tensor_path:
                sample_confidence["full_confidence_tensor"] = tensor_path
            sample["confidence"] = sample_confidence
        samples.append(sample)
        sample_paths.append(dst)

    confidence_samples = [
        {
            "sample_id": sample["sample_id"],
            "frame_index": sample["frame_index"],
            **sample.get("confidence", {}),
        }
        for sample in samples
        if isinstance(sample.get("confidence"), dict)
    ]
    confidence_summary_path = confidence_dir / "confidence_summary.json"
    if confidence_samples:
        numeric_fields = sorted({key for sample in confidence_samples for key, value in sample.items() if isinstance(value, (int, float)) and key != "frame_index"})
        summary = {
            f"{field}_mean": round(sum(float(sample[field]) for sample in confidence_samples if isinstance(sample.get(field), (int, float))) / max(1, sum(1 for sample in confidence_samples if isinstance(sample.get(field), (int, float)))), 6)
            for field in numeric_fields
        }
        confidence_summary_path.write_text(
            json.dumps({"schema_version": 1, "status": "computed", "samples": confidence_samples, "summary": summary}, indent=2),
            encoding="utf-8",
        )

    references = [ref for ref in request.get("references", []) if isinstance(ref, dict) and ref.get("staged_path")]
    rmsd_threshold = _safe_float((request.get("params") or {}).get("rmsd_threshold")) or 3.0
    compute_evaluation = _bool((request.get("params") or {}).get("compute_evaluation", True))
    evaluation_summary_path = evaluation_dir / "evaluation_summary.json"
    reference_rmsd_path = evaluation_dir / "reference_rmsd.csv"
    pairwise_rmsd_path = evaluation_dir / "pairwise_rmsd_matrix.csv"
    landscape_payload: dict[str, Any]
    evaluation_samples: list[dict[str, Any]] = []
    pairwise_values: list[float] = []

    pairwise_matrix = [[0.0 for _ in samples] for _ in samples]
    for i, path_i in enumerate(sample_paths):
        for j in range(i + 1, len(sample_paths)):
            rmsd = _ca_rmsd(path_i, sample_paths[j])
            value = float(rmsd) if rmsd is not None else float("nan")
            pairwise_matrix[i][j] = value
            pairwise_matrix[j][i] = value
            if math.isfinite(value):
                pairwise_values.append(value)

    if compute_evaluation and references:
        reference_names = [str(ref.get("name") or Path(str(ref.get("staged_path"))).stem) for ref in references]
        landscape_points = _mds_coordinates([[0.0 if not math.isfinite(value) else value for value in row] for row in pairwise_matrix])
        with reference_rmsd_path.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = ["sample_id", "frame_index", *reference_names, "nearest_reference", "min_reference_rmsd", "success_at_1"]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for index, (sample, sample_path) in enumerate(zip(samples, sample_paths)):
                rmsd_to_references: dict[str, float] = {}
                for ref, ref_name in zip(references, reference_names):
                    rmsd = _ca_rmsd(sample_path, Path(str(ref["staged_path"])))
                    if rmsd is not None:
                        rmsd_to_references[ref_name] = float(rmsd)
                nearest_reference = min(rmsd_to_references, key=rmsd_to_references.get) if rmsd_to_references else None
                min_reference_rmsd = rmsd_to_references[nearest_reference] if nearest_reference else None
                success_at_1 = bool(min_reference_rmsd is not None and min_reference_rmsd <= rmsd_threshold)
                finite_pairwise = [value for value in pairwise_matrix[index] if math.isfinite(value) and value > 0.0]
                pairwise_diversity = {
                    "min_pairwise_rmsd": min(finite_pairwise) if finite_pairwise else 0.0,
                    "mean_pairwise_rmsd": round(sum(finite_pairwise) / len(finite_pairwise), 6) if finite_pairwise else 0.0,
                    "max_pairwise_rmsd": max(finite_pairwise) if finite_pairwise else 0.0,
                }
                landscape_point = landscape_points[index] if index < len(landscape_points) else {"x": 0.0, "y": 0.0}
                sample_eval = {
                    "sample_id": sample["sample_id"],
                    "frame_index": sample["frame_index"],
                    "nearest_reference": nearest_reference,
                    "min_reference_rmsd": min_reference_rmsd,
                    "success_at_1": success_at_1,
                    "rmsd_to_references": rmsd_to_references,
                    "pairwise_diversity": pairwise_diversity,
                    "landscape": landscape_point,
                }
                sample["reference_evaluation"] = {
                    "nearest_reference": nearest_reference,
                    "min_reference_rmsd": min_reference_rmsd,
                    "success_at_1": success_at_1,
                    "rmsd_to_references": rmsd_to_references,
                }
                sample["pairwise_diversity"] = pairwise_diversity
                sample["landscape"] = landscape_point
                evaluation_samples.append(sample_eval)
                writer.writerow({
                    "sample_id": sample["sample_id"],
                    "frame_index": sample["frame_index"],
                    **rmsd_to_references,
                    "nearest_reference": nearest_reference or "",
                    "min_reference_rmsd": min_reference_rmsd if min_reference_rmsd is not None else "",
                    "success_at_1": str(success_at_1).lower(),
                })
        with pairwise_rmsd_path.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = ["sample_id", *[sample["sample_id"] for sample in samples]]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for sample, row in zip(samples, pairwise_matrix):
                writer.writerow({"sample_id": sample["sample_id"], **{other["sample_id"]: (value if math.isfinite(value) else "") for other, value in zip(samples, row)}})
        success_count = sum(1 for sample in evaluation_samples if sample.get("success_at_1"))
        evaluation_payload = {
            "schema_version": 1,
            "status": "computed",
            "method": "ca_kabsch_rmsd_mds",
            "rmsd_threshold": rmsd_threshold,
            "sample_count": len(samples),
            "reference_count": len(references),
            "success_at_1_rate": round(success_count / len(samples), 6) if samples else 0.0,
            "pairwise_rmsd": {
                "min": min(pairwise_values) if pairwise_values else 0.0,
                "max": max(pairwise_values) if pairwise_values else 0.0,
                "mean": round(sum(pairwise_values) / len(pairwise_values), 6) if pairwise_values else 0.0,
                "count": len(pairwise_values),
            },
            "samples": evaluation_samples,
        }
        evaluation_summary_path.write_text(json.dumps(evaluation_payload, indent=2), encoding="utf-8")
        landscape_payload = {
            "schema_version": 1,
            "workflow": "confornets_experimental",
            "status": "computed",
            "method": "ca_kabsch_rmsd_mds",
            "sample_count": len(samples),
            "reference_count": len(references),
            "coordinates": evaluation_samples,
        }
    else:
        landscape_payload = {
            "schema_version": 1,
            "workflow": "confornets_experimental",
            "status": "not_computed",
            "reason": "Reference RMSD/landscape metrics require compute_evaluation=true and at least one staged reference; the wrapper does not substitute missing landscape values.",
            "sample_count": len(samples),
            "references": request.get("references", []),
        }

    (output_dir / "landscape.json").write_text(json.dumps(landscape_payload, indent=2), encoding="utf-8")
    (output_dir / "samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
    (output_dir / "ensemble_manifest.json").write_text(
        json.dumps({
            "schema_version": 2,
            "workflow": "confornets_experimental",
            "monomer_only": True,
            "query_id": request["query_id"],
            "sequence_sha256": request.get("input_hashes", {}).get("sequence_sha256"),
            "frame_count": len(samples),
            "samples_json": "samples.json",
            "conformer_dir": "conformers",
            "frame_index_base": 0,
            "references": request.get("references", []),
            "conformers": samples,
        }, indent=2),
        encoding="utf-8",
    )
    artifact_manifest = {
        "schema_version": 2,
        "workflow": "confornets_experimental",
        "samples_json": "samples.json",
        "landscape_json": "landscape.json",
        "ensemble_manifest_json": "ensemble_manifest.json",
        "conformer_dir": "conformers",
        "raw_output_dir": "raw",
        "sample_count": len(samples),
        "full_confidence_tensor_count": len(set(confidence_tensors_by_key.values())),
    }
    if confidence_summary_path.exists():
        artifact_manifest["confidence_summary_json"] = "confidence/confidence_summary.json"
    if evaluation_summary_path.exists():
        artifact_manifest["evaluation_summary_json"] = "evaluation/evaluation_summary.json"
    if reference_rmsd_path.exists():
        artifact_manifest["reference_rmsd_csv"] = "evaluation/reference_rmsd.csv"
    if pairwise_rmsd_path.exists():
        artifact_manifest["pairwise_rmsd_csv"] = "evaluation/pairwise_rmsd_matrix.csv"
    (output_dir / "artifact_manifest.json").write_text(json.dumps(artifact_manifest, indent=2), encoding="utf-8")
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

    canonical_binding = relocated_request.get("canonical_binding")
    if canonical_binding is not None:
        if not isinstance(canonical_binding, dict):
            raise SystemExit("canonical_binding must be an object")
        identity = relocated_request.get("backend_identity")
        if not isinstance(identity, dict):
            raise SystemExit("canonical execution requires backend_identity")
        context_path = raw_dir / "cm_confornets_coordinate_context_v1.json"
        ledger_path = raw_dir / "cm_upstream_coordinate_ledger_v1.jsonl"
        context = {
            "schema_name": "cm_confornets_coordinate_context",
            "schema_version": 1,
            "request_sha256": canonical_binding["request_sha256"],
            "coordinate_plan_sha256": canonical_binding["coordinate_plan_sha256"],
            "target_id": canonical_binding["target_id"],
            "coordinate_mapping": canonical_binding["coordinate_mapping"],
            "native_root": str(raw_dir),
            "runtime_identity": identity["runtime_identity"],
            "container_digest": identity["container_digest"],
            "checkpoint_sha256": relocated_request["input_hashes"]["checkpoint_sha256"],
        }
        context_path.write_text(
            json.dumps(context, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        os.environ["BMS_CM_COORDINATE_CONTEXT"] = str(context_path)
        os.environ["BMS_CM_COORDINATE_LEDGER"] = str(ledger_path)

    log_path = output_dir / "confornets_commands.log"
    started = datetime.now(timezone.utc).isoformat()
    _run_preprocess(relocated_request, assets_dir, log_path)
    run_cmd = _build_run_command(relocated_request, assets_dir, raw_dir)
    _run(run_cmd, cwd=Path(relocated_request["params"]["confornets_repo_path"]), log_path=log_path)

    if canonical_binding is not None:
        ledger_path.chmod(0o440)
        repo_path = Path(relocated_request["params"]["confornets_repo_path"]).resolve(strict=True)
        commit = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        identity = relocated_request["backend_identity"]
        if commit != identity["backend_commit"]:
            raise RuntimeError("executed ConforNets commit differs from registered identity")
        checkpoint = Path(relocated_request["params"]["checkpoint_path"]).resolve(strict=True)
        if _sha256_file(checkpoint) != relocated_request["input_hashes"]["checkpoint_sha256"]:
            raise RuntimeError("executed ConforNets checkpoint differs from registered identity")
        attestation = {
            "schema_name": "cm_confornets_runtime_attestation", "schema_version": 1,
            "status": "container_executed", "request_sha256": canonical_binding["request_sha256"],
            "coordinate_plan_sha256": canonical_binding["coordinate_plan_sha256"],
            "backend_commit": commit, "runtime_identity": identity["runtime_identity"],
            "container_digest": identity["container_digest"],
            "feature_identity_sha256": identity["feature_identity_sha256"],
            "checkpoint_sha256": relocated_request["input_hashes"]["checkpoint_sha256"],
            "executed_sources": _runtime_source_records(repo_path, relocated_request["task"]),
            "command": run_cmd,
        }
        (raw_dir / "cm_confornets_runtime_attestation_v1.json").write_text(
            json.dumps(attestation, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )

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
    }
    (output_dir / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    manifest_path = output_dir / "artifact_manifest.json"
    if manifest_path.exists():
        try:
            artifact_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            artifact_manifest = {}
    else:
        artifact_manifest = {}
    artifact_manifest.update({
        "schema_version": max(int(artifact_manifest.get("schema_version") or 1), 2),
        "workflow": "confornets_experimental",
        "samples_json": "samples.json",
        "landscape_json": "landscape.json",
        "ensemble_manifest_json": "ensemble_manifest.json",
        "provenance_json": "provenance.json",
        "commands_log": "confornets_commands.log",
        "conformer_dir": "conformers",
        "raw_output_dir": "raw",
        "sample_count": len(samples),
    })
    manifest_path.write_text(json.dumps(artifact_manifest, indent=2), encoding="utf-8")
    print(f"Normalized {len(samples)} ConforNets CIF samples into {output_dir}")


if __name__ == "__main__":
    main()
