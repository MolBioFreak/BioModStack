#!/usr/bin/env python3
"""Build the sole terminal Shape Blueprint result manifest from evaluated bundles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is absent or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is malformed: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be an object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _closed_file(bundle: Path, descriptor: dict[str, Any], label: str) -> Path:
    filename = descriptor.get("filename")
    if not isinstance(filename, str) or Path(filename).name != filename or not filename:
        raise ValueError(f"{label} filename is unsafe")
    path = bundle / filename
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"{label} is absent, linked, or unsafe")
    if descriptor.get("bytes") != path.stat().st_size:
        raise ValueError(f"{label} byte count mismatch")
    if descriptor.get("sha256") != _sha256(path):
        raise ValueError(f"{label} SHA-256 mismatch")
    return path


def _artifact(path: Path, output_root: Path, fmt: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "relative_path": path.relative_to(output_root).as_posix(),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }
    if fmt:
        payload["format"] = fmt
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def build_result(
    *,
    job_id: str,
    request_path: Path,
    candidate_bundles: Iterable[Path],
    output_dir: Path,
) -> dict[str, Any]:
    request = _read_json(request_path, "Shape request")
    required = (
        "request_id", "request_sha256", "geometry_id", "geometry_sha256",
        "point_pool_sha256", "sdf_sha256", "sdf_sign",
    )
    for key in required:
        if not isinstance(request.get(key), str) or not request[key]:
            raise ValueError(f"Shape request lacks {key}")
    if request["sdf_sign"] != "positive_inside":
        raise ValueError("Shape request SDF convention must be positive_inside")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("Shape result output directory must be new or empty")
    candidate_root = output_dir / "results" / "shape_candidates"
    candidate_root.mkdir(parents=True, exist_ok=True)

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_bundle in candidate_bundles:
        bundle = Path(raw_bundle)
        if bundle.is_symlink() or not bundle.is_dir():
            raise ValueError("Shape candidate bundle is absent or unsafe")
        metadata = _read_json(bundle / "candidate_bundle.json", "Shape candidate bundle manifest")
        if metadata.get("schema") != "bms_shape_candidate_bundle_v1":
            raise ValueError("Shape candidate bundle schema is invalid")
        candidate_id = metadata.get("candidate_id")
        if not isinstance(candidate_id, str) or not SAFE_ID.fullmatch(candidate_id) or candidate_id in seen:
            raise ValueError("Shape candidate ID is invalid or duplicated")
        seen.add(candidate_id)
        status = metadata.get("status")
        if status == "rejected":
            reason = metadata.get("reason")
            if not isinstance(reason, dict) or not reason.get("code"):
                raise ValueError(f"Rejected Shape candidate lacks a reason: {candidate_id}")
            rejected.append({"candidate_id": candidate_id, "reason": reason, "provenance": metadata.get("provenance") or {}})
            continue
        if status != "accepted":
            raise ValueError(f"Shape candidate status is invalid: {candidate_id}")
        name = metadata.get("name")
        if not isinstance(name, str) or not SAFE_ID.fullmatch(name):
            raise ValueError(f"Shape candidate name is invalid: {candidate_id}")
        structure_descriptor = metadata.get("structure")
        source_descriptor = metadata.get("source_backbone")
        metrics_descriptor = metadata.get("metrics")
        if not isinstance(structure_descriptor, dict) or not isinstance(source_descriptor, dict) or not isinstance(metrics_descriptor, dict):
            raise ValueError(f"Shape candidate descriptors are absent: {candidate_id}")
        structure = _closed_file(bundle, structure_descriptor, f"{candidate_id} structure")
        source_backbone = _closed_file(bundle, source_descriptor, f"{candidate_id} source backbone")
        metrics = _closed_file(bundle, metrics_descriptor, f"{candidate_id} metrics")
        parsed_metrics = _read_json(metrics, f"{candidate_id} metrics")
        if parsed_metrics.get("schema") != "bms_shape_candidate_metrics_v1":
            raise ValueError(f"Shape candidate metrics schema is invalid: {candidate_id}")
        for key in ("candidate_id", "geometry_sha256", "point_pool_sha256", "sdf_sha256"):
            expected = candidate_id if key == "candidate_id" else request[key]
            if parsed_metrics.get(key) != expected:
                raise ValueError(f"Shape candidate metrics {key} binding mismatch: {candidate_id}")
        if parsed_metrics.get("source_backbone_sha256") != source_descriptor.get("sha256"):
            raise ValueError(f"Shape candidate metrics source-backbone binding mismatch: {candidate_id}")
        destination_structure = candidate_root / f"{candidate_id}{structure.suffix}"
        destination_source = candidate_root / f"{candidate_id}.source{source_backbone.suffix}"
        destination_metrics = candidate_root / f"{candidate_id}.metrics.json"
        shutil.copyfile(structure, destination_structure)
        shutil.copyfile(source_backbone, destination_source)
        shutil.copyfile(metrics, destination_metrics)
        accepted.append(
            {
                "candidate_id": candidate_id,
                "name": name,
                "structure": _artifact(destination_structure, output_dir, structure.suffix.lstrip(".")),
                "source_backbone": _artifact(destination_source, output_dir, source_backbone.suffix.lstrip(".")),
                "metrics": _artifact(destination_metrics, output_dir),
                "provenance": metadata.get("provenance") or {},
            }
        )

    accepted.sort(key=lambda item: item["candidate_id"])
    rejected.sort(key=lambda item: item["candidate_id"])
    outcome = "candidates" if accepted else "no_candidates"
    if accepted:
        reason = None
    elif rejected:
        reason = {
            "code": "all_candidates_rejected",
            "message": "all refolded candidates failed declared structural admission",
            "rejected_count": len(rejected),
        }
    else:
        reason = {
            "code": "no_refolded_candidates",
            "message": "all upstream scientific stages completed but produced no refolded candidates",
        }
    manifest = {
        "schema": "bms_shape_result_v1",
        "outcome": outcome,
        "job_id": job_id,
        **{key: request[key] for key in required},
        "candidate_count": len(accepted),
        "candidates": accepted,
        "rejected_count": len(rejected),
        "rejections": rejected,
        "reason": reason,
    }
    _atomic_json(output_dir / "results" / "shape_result_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--candidate-bundle", action="append", default=[], type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    build_result(
        job_id=args.job_id,
        request_path=args.request,
        candidate_bundles=args.candidate_bundle,
        output_dir=args.output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
