#!/usr/bin/env python3
"""Bind validator-suite evidence to one evaluated Shape candidate bundle."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

from evaluate_rfd3_post_refold import evaluate_post_refold


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"{label} is absent or unsafe")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _write(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical(payload) + b"\n")
    os.replace(temporary, path)


def _descriptor(path: Path) -> dict[str, Any]:
    return {"filename": path.name, "sha256": _sha(path), "bytes": path.stat().st_size}


def _gzip_copy(source: Path, destination: Path) -> None:
    with source.open("rb") as source_handle, gzip.open(destination, "wb", compresslevel=6) as destination_handle:
        for block in iter(lambda: source_handle.read(1024 * 1024), b""):
            destination_handle.write(block)


def attach_post_refold(
    *,
    bundle_dir: Path,
    validator_records_path: Path,
    request_path: Path,
    geometry_manifest_path: Path,
    points_path: Path,
    sdf_path: Path,
) -> dict[str, Any]:
    bundle_path = bundle_dir / "candidate_bundle.json"
    bundle = _json(bundle_path, "candidate bundle")
    if bundle.get("schema") != "bms_shape_candidate_bundle_v1":
        raise ValueError("candidate bundle schema is invalid")
    metrics_descriptor = bundle.get("metrics")
    structure_descriptor = bundle.get("structure")
    source_descriptor = bundle.get("source_backbone")
    if not all(isinstance(value, dict) for value in (metrics_descriptor, structure_descriptor, source_descriptor)):
        raise ValueError("candidate bundle descriptors are incomplete")
    metrics_descriptor = cast(dict[str, Any], metrics_descriptor)
    structure_descriptor = cast(dict[str, Any], structure_descriptor)
    source_descriptor = cast(dict[str, Any], source_descriptor)
    structure = bundle_dir / str(structure_descriptor["filename"])
    source = bundle_dir / str(source_descriptor["filename"])
    metrics_path = bundle_dir / str(metrics_descriptor["filename"])
    metrics = _json(metrics_path, "candidate metrics")
    validator_envelope = _json(validator_records_path, "validator suite")
    if validator_envelope.get("schema") != "bms_shape_validator_suite_v1":
        raise ValueError("validator suite schema is invalid")
    records = validator_envelope.get("records")
    if not isinstance(records, dict):
        raise ValueError("validator suite records are absent")

    post_path = bundle_dir / "post_refold_evaluation.json"
    if bundle.get("status") == "accepted":
        compressed_candidate = bundle_dir / f"{bundle['candidate_id']}.post_refold.cif.gz"
        _gzip_copy(structure, compressed_candidate)
        post = evaluate_post_refold(
            candidate_path=compressed_candidate,
            source_structure_path=source,
            request_path=request_path,
            manifest_path=geometry_manifest_path,
            output_path=post_path,
            validator_records=records,
            points_path=points_path,
            sdf_path=sdf_path,
        )
    else:
        post = {
            "schema": "bms_rfd3_post_refold_evaluation_v1",
            "status": "rejected",
            "reason": bundle.get("reason") or {"code": "candidate_pre_refold_rejected"},
            "validators": records,
        }
        _write(post_path, post)

    metrics["validator_evidence"] = {
        "schema": validator_envelope["schema"],
        "status": validator_envelope.get("status"),
        "sequence_name": validator_envelope.get("sequence_name"),
        "records": post.get("validators") or records,
        "suite_sha256": _sha(validator_records_path),
    }
    metrics["post_refold"] = post
    metrics["validation"] = {
        "status": "accepted" if post.get("status") == "accepted" and bundle.get("status") == "accepted" else "rejected",
        "reason": post.get("reason"),
        "validator_suite": validator_envelope.get("validators", []),
    }
    _write(metrics_path, metrics)
    bundle["status"] = "accepted" if metrics["validation"]["status"] == "accepted" else "rejected"
    bundle["reason"] = None if bundle["status"] == "accepted" else post.get("reason") or {"code": "post_refold_rejected"}
    provenance = dict(bundle.get("provenance") or {})
    provenance.update({
        "validator_suite_sha256": _sha(validator_records_path),
        "post_refold_evaluation_sha256": _sha(post_path),
        "validator_status": metrics["validation"]["status"],
    })
    bundle["provenance"] = provenance
    bundle["metrics"] = _descriptor(metrics_path)
    _write(bundle_path, bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--validator-records", required=True, type=Path)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--geometry-manifest", required=True, type=Path)
    parser.add_argument("--points", required=True, type=Path)
    parser.add_argument("--sdf", required=True, type=Path)
    args = parser.parse_args()
    attach_post_refold(
        bundle_dir=args.bundle,
        validator_records_path=args.validator_records,
        request_path=args.request,
        geometry_manifest_path=args.geometry_manifest,
        points_path=args.points,
        sdf_path=args.sdf,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
