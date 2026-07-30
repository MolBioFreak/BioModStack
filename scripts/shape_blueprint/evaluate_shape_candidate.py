#!/usr/bin/env python3
"""Align one ESMFold2 refold to its source backbone and evaluate canonical Shape fit."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import torch
from biotite.structure.io import load_structure, save_structure

from shape_guidance import ShapeGuidanceField

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"{label} is absent or linked")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _regular(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"{label} is absent or linked")
    return path


def _ca_coordinates(array, expected_length: int, label: str) -> np.ndarray:
    ca = array[(array.atom_name == "CA") & (~array.hetero)]
    if len(ca) != expected_length:
        raise ValueError(f"{label} CA count does not match target length")
    if len(set(str(chain) for chain in ca.chain_id)) != 1:
        raise ValueError(f"{label} must contain exactly one protein chain")
    coordinates = np.asarray(ca.coord, dtype=np.float64)
    if not np.isfinite(coordinates).all():
        raise ValueError(f"{label} contains non-finite CA coordinates")
    return coordinates


def _rigid_transform(moving: np.ndarray, fixed: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    moving_center = moving.mean(axis=0)
    fixed_center = fixed.mean(axis=0)
    centered_moving = moving - moving_center
    centered_fixed = fixed - fixed_center
    u, _, vt = np.linalg.svd(centered_moving.T @ centered_fixed)
    rotation_row = u @ vt
    if np.linalg.det(rotation_row) < 0:
        u[:, -1] *= -1
        rotation_row = u @ vt
    translation_row = fixed_center - moving_center @ rotation_row
    aligned = moving @ rotation_row + translation_row
    rmsd = float(np.sqrt(np.mean(np.sum((aligned - fixed) ** 2, axis=1))))
    return rotation_row, translation_row, rmsd


def evaluate_candidate(
    *,
    sequence_name: str,
    source_backbone: Path,
    structure_path: Path,
    esm_metrics_path: Path,
    request_path: Path,
    geometry_manifest_path: Path,
    points_path: Path,
    sdf_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    request = _json(request_path, "Shape request")
    geometry = _json(geometry_manifest_path, "Shape geometry manifest")
    esm_metrics = _json(esm_metrics_path, "ESMFold2 metrics")
    source_backbone = _regular(source_backbone, "source backbone")
    structure_path = _regular(structure_path, "ESMFold2 structure")
    points_path = _regular(points_path, "canonical point pool")
    sdf_path = _regular(sdf_path, "canonical SDF")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("candidate output directory must be new or empty")
    output_dir.mkdir(parents=True, exist_ok=True)

    expected_length = int(request["target_length"])
    if request.get("geometry_sha256") != geometry.get("geometry_sha256"):
        raise ValueError("geometry SHA-256 binding mismatch")
    if request.get("point_pool_sha256") != _sha256(points_path):
        raise ValueError("point-pool SHA-256 binding mismatch")
    if geometry.get("sdf_sha256") != _sha256(sdf_path):
        raise ValueError("SDF SHA-256 binding mismatch")
    if esm_metrics.get("sequence_name") != sequence_name:
        raise ValueError("ESMFold2 sequence-name binding mismatch")
    plddt = esm_metrics.get("plddt_mean")
    if not isinstance(plddt, (int, float)) or not math.isfinite(float(plddt)):
        raise ValueError("ESMFold2 metrics lack finite plddt_mean")
    plddt = float(plddt)
    if 0.0 <= plddt <= 1.0:
        plddt *= 100.0
    if not 0.0 <= plddt <= 100.0:
        raise ValueError("ESMFold2 pLDDT is outside the supported 0..100 range")

    source = load_structure(str(source_backbone), model=1)
    candidate = load_structure(str(structure_path), model=1)
    source_ca = _ca_coordinates(source, expected_length, "source backbone")
    candidate_ca = _ca_coordinates(candidate, expected_length, "ESMFold2 structure")
    rotation_row, translation_row, alignment_rmsd = _rigid_transform(candidate_ca, source_ca)
    aligned_coordinates = np.asarray(candidate.coord, dtype=np.float64) @ rotation_row + translation_row
    if not np.isfinite(aligned_coordinates).all():
        raise ValueError("aligned candidate contains non-finite coordinates")
    candidate.coord = aligned_coordinates.astype(np.float32)
    aligned_ca = _ca_coordinates(candidate, expected_length, "aligned candidate")
    ca_distances = np.linalg.norm(np.diff(aligned_ca, axis=0), axis=1)
    ordinary_backbone = bool(np.all((ca_distances >= 2.8) & (ca_distances <= 4.5)))

    point_count = int(geometry["point_count"])
    points = np.frombuffer(points_path.read_bytes(), dtype="<f4")
    if points.size != point_count * 3:
        raise ValueError("canonical point pool byte shape mismatch")
    points_tensor = torch.from_numpy(points.reshape(point_count, 3).copy())
    grid_shape = tuple(int(value) for value in geometry["sdf_grid_shape"])
    sdf_values = np.frombuffer(sdf_path.read_bytes(), dtype="<f4")
    if sdf_values.size != int(np.prod(grid_shape)):
        raise ValueError("canonical SDF byte shape mismatch")
    field = ShapeGuidanceField(
        points=points_tensor,
        sdf=torch.from_numpy(sdf_values.reshape(grid_shape).copy()),
        origin=torch.tensor(geometry["sdf_origin_angstrom"], dtype=torch.float32),
        spacing=torch.tensor(geometry["sdf_spacing_angstrom"], dtype=torch.float32),
    )
    ca_tensor = torch.from_numpy(aligned_ca.astype(np.float32)).unsqueeze(0)
    signed_distance = field.signed_distance(ca_tensor)
    losses = field.loss(ca_tensor)

    normalized = SAFE_NAME.sub("_", sequence_name).strip("._-") or "shape_candidate"
    identity = hashlib.sha256(
        (sequence_name + "\0" + _sha256(source_backbone) + "\0" + _sha256(structure_path)).encode("utf-8")
    ).hexdigest()[:16]
    candidate_id = f"{normalized[:72]}__{identity}"
    output_structure = output_dir / f"{candidate_id}.cif"
    save_structure(str(output_structure), candidate)
    engine = "proteinmpnn" if "__proteinmpnn__" in sequence_name else "fampnn" if "__fampnn__" in sequence_name else "unknown"
    transform = {
        "convention": "aligned_candidate_row = candidate_row @ rotation_row + translation_row",
        "rotation_row_major": rotation_row.tolist(),
        "translation_angstrom": translation_row.tolist(),
        "source": "esmfold2_ca_to_generating_rfd3_ca",
        "rmsd_angstrom": alignment_rmsd,
    }
    metrics = {
        "schema": "bms_shape_candidate_metrics_v1",
        "candidate_id": candidate_id,
        "sequence_name": sequence_name,
        "target_length": expected_length,
        "finite_coordinates": True,
        "ordinary_backbone": ordinary_backbone,
        "ca_distance_min": float(ca_distances.min()),
        "ca_distance_max": float(ca_distances.max()),
        "ca_distance_mean": float(ca_distances.mean()),
        "ca_distance_valid_fraction_2_8_to_4_5": float(np.mean((ca_distances >= 2.8) & (ca_distances <= 4.5))),
        "shape_total": float(losses["total"]),
        "shape_chamfer": float(losses["chamfer"]),
        "shape_outside": float(losses["outside"]),
        "sdf_positive_inside_fraction": float((signed_distance >= 0).float().mean()),
        "plddt_overall": float(plddt),
        "ptm": esm_metrics.get("ptm"),
        "alignment_transform": transform,
        "source_backbone_sha256": _sha256(source_backbone),
        "untransformed_esmfold2_sha256": _sha256(structure_path),
        "geometry_sha256": request["geometry_sha256"],
        "point_pool_sha256": request["point_pool_sha256"],
        "sdf_sha256": geometry["sdf_sha256"],
    }
    output_metrics = output_dir / f"{candidate_id}.metrics.json"
    _atomic_json(output_metrics, metrics)
    status = "accepted" if ordinary_backbone else "rejected"
    bundle = {
        "schema": "bms_shape_candidate_bundle_v1",
        "status": status,
        "candidate_id": candidate_id,
        "name": candidate_id,
        "structure": {"filename": output_structure.name, "sha256": _sha256(output_structure), "bytes": output_structure.stat().st_size},
        "metrics": {"filename": output_metrics.name, "sha256": _sha256(output_metrics), "bytes": output_metrics.stat().st_size},
        "provenance": {
            "sequence_engine": engine,
            "predictor": "esmfold2",
            "sequence_name": sequence_name,
            "source_backbone_sha256": _sha256(source_backbone),
            "esmfold2_metrics_sha256": _sha256(esm_metrics_path),
            "alignment_transform": transform,
        },
        "reason": None if status == "accepted" else {"code": "invalid_backbone_geometry", "message": "refolded CA distances failed ordinary backbone bounds"},
    }
    _atomic_json(output_dir / "candidate_bundle.json", bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence-name", required=True)
    parser.add_argument("--source-backbone", required=True, type=Path)
    parser.add_argument("--structure", required=True, type=Path)
    parser.add_argument("--esm-metrics", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--geometry-manifest", required=True, type=Path)
    parser.add_argument("--points", required=True, type=Path)
    parser.add_argument("--sdf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    evaluate_candidate(
        sequence_name=args.sequence_name,
        source_backbone=args.source_backbone,
        structure_path=args.structure,
        esm_metrics_path=args.esm_metrics,
        request_path=args.request,
        geometry_manifest_path=args.geometry_manifest,
        points_path=args.points,
        sdf_path=args.sdf,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
