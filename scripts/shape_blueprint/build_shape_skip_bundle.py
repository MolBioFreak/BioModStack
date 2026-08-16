#!/usr/bin/env python3
"""Create a truthful terminal candidate bundle for sequence-policy=skip."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is absent or unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _descriptor(path: Path, fmt: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "filename": path.name,
        "sha256": _sha(path),
        "bytes": path.stat().st_size,
    }
    if fmt:
        result["format"] = fmt
    return result


def build_skip_bundle(
    *,
    backbone_dir: Path,
    request_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    request = _json(request_path, "Shape request")
    sequence_policy = str(request.get("sequence_policy") or "auto")
    sequence_count = int(request.get("sequences_per_backbone") or 0)
    if sequence_count != 0:
        raise ValueError("initial-only Shape bundle requires sequences_per_backbone=0")
    manifest = _json(backbone_dir / "shape_backbone_manifest.json", "backbone manifest")
    admission = _json(backbone_dir / "initial_admission.json", "initial admission")
    if manifest.get("status") != "accepted" or admission.get("status") != "accepted":
        raise ValueError("skip bundle requires an accepted initial backbone")
    candidate_id = manifest.get("candidate_id")
    if not isinstance(candidate_id, str) or len(candidate_id) != 64:
        raise ValueError("backbone manifest candidate ID is invalid")
    for key in ("request_sha256", "geometry_sha256", "point_pool_sha256", "sdf_sha256"):
        expected = request.get(key)
        observed = manifest.get(key) or admission.get(key)
        if expected != observed:
            raise ValueError(f"skip backbone {key} binding mismatch")
    backbone = backbone_dir / "shape_backbone.pdb"
    if not backbone.is_file() or _sha(backbone) != manifest["backbone"]["sha256"]:
        raise ValueError("skip backbone bytes do not match its manifest")
    if output_dir.exists():
        raise ValueError("skip bundle output directory must be new")
    output_dir.mkdir(parents=True)
    structure = output_dir / f"{candidate_id}.pdb"
    source = output_dir / f"{candidate_id}.source.pdb"
    shutil.copyfile(backbone, structure)
    shutil.copyfile(backbone, source)
    admission_metrics = dict(admission.get("metrics") or {})
    metrics = {
        "schema": "bms_shape_candidate_metrics_v1",
        "candidate_id": candidate_id,
        "geometry_sha256": request["geometry_sha256"],
        "point_pool_sha256": request["point_pool_sha256"],
        "sdf_sha256": request["sdf_sha256"],
        "source_backbone_sha256": _sha(source),
        "sequence_policy": sequence_policy,
        "sequence_design": {"status": "not_requested", "engine": None},
        "initial_admission": admission,
        "shape_metrics": admission_metrics,
        "validation": {
            "status": "not_applicable",
            "reason": {
                "code": "sequence_design_not_requested",
                "message": "post-refold validators are not applicable without a designed sequence",
            },
            "validator_suite": list(request.get("validator_suite") or []),
        },
        "plddt_overall": None,
    }
    metrics_path = output_dir / f"{candidate_id}.metrics.json"
    metrics_path.write_bytes(_canonical(metrics) + b"\n")
    bundle = {
        "schema": "bms_shape_candidate_bundle_v1",
        "status": "accepted",
        "candidate_id": candidate_id,
        "name": candidate_id,
        "structure": _descriptor(structure, "pdb"),
        "source_backbone": _descriptor(source, "pdb"),
        "metrics": _descriptor(metrics_path),
        "provenance": {
            "request_sha256": request["request_sha256"],
            "geometry_sha256": request["geometry_sha256"],
            "initial_admission_sha256": _sha(backbone_dir / "initial_admission.json"),
            "backbone_manifest_sha256": _sha(backbone_dir / "shape_backbone_manifest.json"),
            "sequence_policy": sequence_policy,
            "sequence_engine": None,
            "predictor": None,
            "validator_status": "not_applicable",
            "validator_suite": list(request.get("validator_suite") or []),
        },
    }
    (output_dir / "candidate_bundle.json").write_bytes(_canonical(bundle) + b"\n")
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backbone-dir", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    build_skip_bundle(backbone_dir=args.backbone_dir, request_path=args.request, output_dir=args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
