#!/usr/bin/env python3
"""Build a typed manifest from declared native RFD3 outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any

from contract import canonical_json, request_sha256


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _size(path: Path) -> int:
    return path.stat().st_size


def _media_type(path: Path) -> str:
    if path.name.endswith(".cif.gz"):
        return "chemical/x-mmcif+gzip"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _candidate_id(path: Path) -> str:
    name = path.name
    if name.endswith(".cif.gz"):
        return name[:-7]
    if name.endswith(".json"):
        return name[:-5]
    return path.stem


def _metrics(metadata: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "summary_confidences",
        "ca_rmsd_to_input",
        "backbone_rmsd",
        "insertion_rmsd",
        "diffused_index_map",
        "hbond_metrics",
        "inference_metadata",
    )
    return {key: metadata[key] for key in keys if key in metadata}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True)
    parser.add_argument("--cif-file", action="append", default=[])
    parser.add_argument("--json-file", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--request-storage-path", required=True)
    parser.add_argument("--artifact-relative-prefix", default="run/rfd3")
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--source-storage-path", required=True)
    args = parser.parse_args()

    request_path = Path(args.request).expanduser().resolve()
    storage_root = Path(args.storage_root).expanduser().resolve()
    request_storage_path = Path(args.request_storage_path).expanduser().resolve()
    request: dict[str, Any] = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema") != "bms.rfd3.local-redesign.request.v1":
        raise SystemExit("unsupported local-redesign request schema")
    input_binding = request.get("input")
    if not isinstance(input_binding, dict) or not isinstance(input_binding.get("path"), str):
        raise SystemExit("canonical request has no source input binding")
    expected_source_sha = input_binding.get("sha256")
    source_file = Path(args.source_file).expanduser().resolve()
    source_storage_path = Path(args.source_storage_path).expanduser().resolve()
    if not source_file.is_file():
        raise SystemExit(f"source structure does not exist: {source_file}")
    if expected_source_sha and _sha256(source_file) != expected_source_sha:
        raise SystemExit("source structure hash does not match the canonical request")
    request_digest = request_sha256(request)

    cif_paths = [Path(value).resolve() for value in args.cif_file]
    json_paths = [Path(value).resolve() for value in args.json_file]
    if not cif_paths:
        raise SystemExit("at least one native RFD3 CIF artifact is required")
    if any(not path.is_file() for path in [*cif_paths, *json_paths]):
        raise SystemExit("declared RFD3 artifacts must exist")

    metadata_by_candidate: dict[str, dict[str, Any]] = {}
    for path in json_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid native RFD3 metadata: {path}: {exc}") from exc
        metadata_by_candidate[_candidate_id(path)] = payload

    artifacts: list[dict[str, Any]] = [
        {
            "role": "source_structure",
            "relative_path": f"external_inputs/{source_storage_path.name}",
            "storage_path": str(source_storage_path),
            "sha256": _sha256(source_file),
            "bytes": _size(source_file),
            "media_type": _media_type(source_file),
        },
        {
            "role": "native_request",
            "relative_path": f"requests/{request_storage_path.name}",
            "storage_path": str(request_storage_path),
            "sha256": _sha256(request_path),
            "bytes": _size(request_path),
            "media_type": "application/json",
        }
    ]
    candidates: list[dict[str, Any]] = []
    for cif_path in sorted(cif_paths):
        candidate_id = _candidate_id(cif_path)
        candidate_artifacts = [
            {
                "role": "structure",
                "relative_path": f"{args.artifact_relative_prefix}/{cif_path.name}",
                "storage_path": str(storage_root / cif_path.name),
                "sha256": _sha256(cif_path),
                "bytes": _size(cif_path),
                "media_type": _media_type(cif_path),
            }
        ]
        metadata_path = next((path for path in json_paths if _candidate_id(path) == candidate_id), None)
        metadata = metadata_by_candidate.get(candidate_id, {})
        if metadata_path is not None:
            candidate_artifacts.append(
                {
                    "role": "native_prediction_metadata",
                    "relative_path": f"{args.artifact_relative_prefix}/{metadata_path.name}",
                    "storage_path": str(storage_root / metadata_path.name),
                    "sha256": _sha256(metadata_path),
                    "bytes": _size(metadata_path),
                    "media_type": _media_type(metadata_path),
                }
            )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "status": "generated",
                "result_set": "rfd3_local_redesign_candidates",
                "artifact_manifest_sha256": hashlib.sha256(
                    canonical_json(candidate_artifacts).encode("utf-8")
                ).hexdigest(),
                "metrics": _metrics(metadata),
                "artifacts": candidate_artifacts,
            }
        )
        artifacts.extend(candidate_artifacts)

    manifest = {
        "schema": "bms.rfd3.local-redesign.result.v1",
        "result_contract_id": "rfd3_local_redesign_v1",
        "request_sha256": request_digest,
        "request_schema": request.get("schema"),
        "profile_id": request.get("profile_id"),
        "profile_registry_sha256": request.get("profile_registry_sha256"),
        "profile": request.get("profile"),
        "redesign_mode": request.get("redesign_mode"),
        "contig_dialect": request.get("contig_dialect"),
        "sequence_policy": request.get("sequence_policy"),
        "input": request.get("input"),
        "rfd3": request.get("rfd3"),
        "execution": request.get("execution"),
        "evaluation": request.get("evaluation"),
        "artifacts": artifacts,
        "candidates": candidates,
    }
    manifest["manifest_sha256"] = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    Path(args.output).write_text(canonical_json(manifest) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
