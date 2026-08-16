#!/usr/bin/env python3
"""Evaluate an independently refolded RFD3 candidate with typed validator-native evidence."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np


INITIAL_SCRIPT = Path(__file__).with_name("evaluate_rfd3_initial_candidate.py")
DEFAULT_VALIDATORS = ("boltz2", "esmfold2", "protenix_v2")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _initial_module():
    spec = importlib.util.spec_from_file_location("shape_initial_admission_for_post_refold", INITIAL_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("initial admission module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return payload


def _ca_coordinates(path: Path) -> np.ndarray:
    initial = _initial_module()
    if path.name.endswith(".cif.gz"):
        atoms = initial._parse_atoms(initial._read_cif_text(path))
        residues = initial._residues(atoms)
        coordinates = []
        for residue in residues:
            atom_map = initial._atom_map(residue)
            if "CA" not in atom_map:
                raise ValueError("structure is missing a Cα atom")
            coordinates.append(atom_map["CA"].coord)
        return np.asarray(coordinates, dtype=np.float64)
    residues: dict[tuple[str, str, str], tuple[float, float, float]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith(("ATOM", "HETATM")) or line[12:16].strip().upper() != "CA":
            continue
        if len(line) < 54:
            raise ValueError("PDB structure contains a truncated Cα record")
        key = (line[21:22].strip() or "A", line[22:26].strip(), line[26:27].strip())
        coords = tuple(float(line[start:end]) for start, end in ((30, 38), (38, 46), (46, 54)))
        if not all(math.isfinite(value) for value in coords):
            raise ValueError("PDB structure contains non-finite Cα coordinates")
        residues.setdefault(key, coords)
    if not residues:
        raise ValueError("structure contains no Cα atoms")
    return np.asarray(list(residues.values()), dtype=np.float64)


def _kabsch_rmsd(mobile: np.ndarray, fixed: np.ndarray) -> tuple[float, list[list[float]], list[float]]:
    if mobile.shape != fixed.shape or mobile.ndim != 2 or mobile.shape[1] != 3 or len(mobile) == 0:
        raise ValueError("post-refold Cα arrays are incompatible")
    mobile_center = mobile.mean(axis=0)
    fixed_center = fixed.mean(axis=0)
    centered_mobile = mobile - mobile_center
    centered_fixed = fixed - fixed_center
    u, _, vt = np.linalg.svd(centered_mobile.T @ centered_fixed)
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:, -1] *= -1
        rotation = u @ vt
    translation = fixed_center - mobile_center @ rotation
    aligned = mobile @ rotation + translation
    rmsd = float(np.sqrt(np.mean(np.sum((aligned - fixed) ** 2, axis=1))))
    return rmsd, rotation.tolist(), translation.tolist()


def _finite_native_metric(metrics: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return True
    return False


def _validator_gate(expected: list[str], records: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    normalized: dict[str, Any] = {}
    missing: list[str] = []
    invalid: list[str] = []
    required_keys = {
        "boltz2": ("confidence_score", "ptm", "plddt", "plddt_mean"),
        "esmfold2": ("plddt_mean", "plddt", "ptm"),
        "protenix_v2": ("confidence_score", "plddt", "plddt_mean", "ptm"),
    }
    for validator in expected:
        record = records.get(validator)
        if not isinstance(record, dict) or record.get("status") != "completed":
            missing.append(validator)
            continue
        task_type = str(record.get("task_type") or "monomer")
        native_metrics = record.get("native_metrics") or record.get("metrics")
        if not isinstance(native_metrics, dict) or not _finite_native_metric(native_metrics, required_keys.get(validator, ())):
            invalid.append(validator)
            continue
        normalized[validator] = {
            "status": "completed",
            "task_type": task_type,
            "model_id": record.get("model_id"),
            "checkpoint_sha256": record.get("checkpoint_sha256"),
            "native_metrics": dict(native_metrics),
            "metric_namespace": f"{validator}.native",
        }
        if validator == "boltz2" and task_type == "complex":
            ipsae = next((native_metrics.get(key) for key in ("ipSAE", "ipsae", "ip_sae") if key in native_metrics), None)
            if not isinstance(ipsae, (int, float)) or not math.isfinite(float(ipsae)):
                invalid.append(validator)
            else:
                normalized[validator]["interface_metric"] = {"name": "ipSAE", "value": float(ipsae)}
        elif validator == "boltz2" and task_type == "monomer":
            normalized[validator]["interface_metric"] = {"name": None, "value": None, "applicable": False}
    return normalized, missing, invalid


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical(payload) + b"\n")
    os.replace(temporary, path)


def evaluate_post_refold(
    *,
    candidate_path: Path,
    source_structure_path: Path,
    request_path: Path,
    manifest_path: Path,
    output_path: Path,
    validator_records: dict[str, Any],
    points_path: Path | None = None,
    sdf_path: Path | None = None,
) -> dict[str, Any]:
    request = _json(request_path)
    manifest = _json(manifest_path)
    initial = _initial_module()
    initial_path = output_path.with_name(f".{output_path.stem}.initial_admission.json")
    initial_result = initial.admit_initial_candidate(
        candidate_path=candidate_path,
        request_path=request_path,
        manifest_path=manifest_path,
        points_path=points_path,
        sdf_path=sdf_path,
        output_path=initial_path,
    )
    expected = list(request.get("validator_suite") or DEFAULT_VALIDATORS)
    normalized_validators, missing_validators, invalid_validators = _validator_gate(expected, validator_records)
    result: dict[str, Any] = {
        "schema": "bms_rfd3_post_refold_evaluation_v1",
        "status": "rejected",
        "request_sha256": request.get("request_sha256"),
        "geometry_sha256": request.get("geometry_sha256"),
        "candidate_id": initial_result.get("candidate_id"),
        "candidate_sha256": _sha(candidate_path),
        "source_structure_sha256": _sha(source_structure_path),
        "initial_admission": initial_result,
        "validators": normalized_validators,
        "validator_policy": {"expected_validators": expected, "native_metrics_only": True, "cross_validator_score": None},
    }
    if initial_result.get("status") != "accepted":
        result["reason"] = {"code": "initial_admission_failed", "initial_reason": initial_result.get("reason")}
        _write(output_path, result)
        return result
    try:
        candidate_ca = _ca_coordinates(candidate_path)
        source_ca = _ca_coordinates(source_structure_path)
        ca_rmsd, rotation, translation = _kabsch_rmsd(candidate_ca, source_ca)
        result["post_refold"] = {
            "ca_rmsd_angstrom": ca_rmsd,
            "ca_count": int(len(candidate_ca)),
            "alignment_transform": {
                "convention": "candidate_row @ rotation_row + translation_row = source_frame",
                "rotation_row_major": rotation,
                "translation_angstrom": translation,
            },
            "cad_metrics": dict(initial_result.get("metrics") or {}),
            "continuity_metrics": {
                key: initial_result["metrics"].get(key)
                for key in ("chainbreak_count", "covalent_geometry_violation_count", "backbone_clash_count", "sidechain_clash_count")
            },
        }
    except (OSError, ValueError, FloatingPointError) as exc:
        result["reason"] = {"code": "post_refold_geometry_invalid", "message": str(exc)}
        _write(output_path, result)
        return result
    if missing_validators or invalid_validators:
        result["reason"] = {
            "code": "validator_evidence_incomplete",
            "missing_validators": sorted(set(missing_validators)),
            "invalid_validators": sorted(set(invalid_validators)),
        }
    else:
        result["status"] = "accepted"
        result["acceptance"] = {
            "status": "accepted",
            "basis": "initial_admission_plus_independent_post_refold_ca_rmsd_and_validator_native_metric_completeness",
            "ca_rmsd_angstrom": result["post_refold"]["ca_rmsd_angstrom"],
        }
        result["reason"] = None
    _write(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--source-structure", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--validator-json", action="append", default=[], metavar="VALIDATOR=PATH")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    records = {}
    for binding in args.validator_json:
        validator, separator, path = binding.partition("=")
        if not separator or validator not in DEFAULT_VALIDATORS:
            raise SystemExit("--validator-json must be VALIDATOR=PATH for boltz2, esmfold2, or protenix_v2")
        records[validator] = _json(Path(path))
    result = evaluate_post_refold(
        candidate_path=args.candidate,
        source_structure_path=args.source_structure,
        request_path=args.request,
        manifest_path=args.manifest,
        output_path=args.output,
        validator_records=records,
    )
    return 0 if result["status"] == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
