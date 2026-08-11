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


def _trajectory_binding(path: Path) -> tuple[str, str]:
    for marker, role in (
        ("_denoised_model_", "denoised_trajectory"),
        ("_noisy_model_", "noisy_trajectory"),
    ):
        if marker in path.name:
            candidate_name = path.name.replace(marker, "_model_", 1)
            return _candidate_id(Path(candidate_name)), role
    raise SystemExit(f"unsupported native RFD3 trajectory filename: {path.name}")


def _descriptor(
    *,
    role: str,
    source_path: Path,
    storage_path: Path,
    relative_path: str,
) -> dict[str, Any]:
    return {
        "role": role,
        "relative_path": relative_path,
        "storage_path": str(storage_path),
        "sha256": _sha256(source_path),
        "bytes": source_path.stat().st_size,
        "media_type": _media_type(source_path),
    }


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
    parser.add_argument("--native-input", required=True)
    parser.add_argument("--native-input-storage-path", required=True)
    parser.add_argument("--trajectory-file", action="append", default=[])
    parser.add_argument("--trajectory-dir")
    parser.add_argument("--preparation-receipt", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--metadata-jsonl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--request-storage-path", required=True)
    parser.add_argument("--artifact-relative-prefix", default="run/rfd3")
    parser.add_argument("--source-file", required=True)
    parser.add_argument("--source-storage-path", required=True)
    parser.add_argument("--preparation-receipt-storage-path", required=True)
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

    cif_paths = [Path(value).resolve() for value in args.cif_file]
    json_paths = [Path(value).resolve() for value in args.json_file]
    native_input = Path(args.native_input).resolve()
    native_input_storage_path = Path(args.native_input_storage_path).resolve()
    trajectory_paths = [Path(value).resolve() for value in args.trajectory_file]
    if args.trajectory_dir:
        trajectory_dir = Path(args.trajectory_dir).resolve()
        if not trajectory_dir.is_dir():
            raise SystemExit(f"native RFD3 trajectory directory does not exist: {trajectory_dir}")
        trajectory_paths.extend(sorted(trajectory_dir.glob("*.cif.gz")))
    preparation_receipt = Path(args.preparation_receipt).resolve()
    preparation_receipt_storage_path = Path(args.preparation_receipt_storage_path).resolve()
    log_file = Path(args.log_file).resolve()
    metadata_jsonl = Path(args.metadata_jsonl).resolve()
    declared_paths = [
        *cif_paths,
        *json_paths,
        *trajectory_paths,
        native_input,
        preparation_receipt,
        log_file,
        metadata_jsonl,
    ]
    if not cif_paths:
        raise SystemExit("at least one native RFD3 candidate CIF artifact is required")
    if any(not path.is_file() for path in declared_paths):
        raise SystemExit("declared RFD3 artifacts must exist")

    execution = request.get("execution")
    if not isinstance(execution, dict):
        raise SystemExit("canonical request has no execution contract")
    requested_num_designs = execution.get("num_designs")
    if not isinstance(requested_num_designs, int) or requested_num_designs < 1:
        raise SystemExit("canonical request has an invalid requested design count")
    if len(cif_paths) != requested_num_designs:
        raise SystemExit(
            f"native RFD3 candidate count mismatch: requested {requested_num_designs}, observed {len(cif_paths)}"
        )

    metadata_by_candidate: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in json_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"invalid native RFD3 metadata: {path}: {exc}") from exc
        metadata_by_candidate[_candidate_id(path)] = (path, payload)

    trajectory_by_candidate: dict[str, dict[str, Path]] = {}
    for path in trajectory_paths:
        candidate_id, role = _trajectory_binding(path)
        bound = trajectory_by_candidate.setdefault(candidate_id, {})
        if role in bound:
            raise SystemExit(f"duplicate {role} for native RFD3 candidate '{candidate_id}'")
        bound[role] = path

    candidate_ids = {_candidate_id(path) for path in cif_paths}
    if set(metadata_by_candidate) != candidate_ids:
        raise SystemExit("native RFD3 metadata must match every candidate structure exactly")
    if set(trajectory_by_candidate) - candidate_ids:
        raise SystemExit("native RFD3 trajectories reference unknown candidates")
    trajectories_requested = execution.get("dump_trajectories") is True
    if trajectories_requested:
        for candidate_id in candidate_ids:
            if set(trajectory_by_candidate.get(candidate_id, {})) != {
                "denoised_trajectory",
                "noisy_trajectory",
            }:
                raise SystemExit(
                    f"requested native RFD3 trajectories are incomplete for candidate '{candidate_id}'"
                )
    elif trajectory_paths:
        raise SystemExit("native RFD3 produced trajectories when they were not requested")

    try:
        receipt_payload = json.loads(preparation_receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid native RFD3 preparation receipt: {exc}") from exc
    sequence_design = receipt_payload.get("sequence_design")
    sequence_state = sequence_design.get("state") if isinstance(sequence_design, dict) else None
    design_id = receipt_payload.get("design_id")
    receipt_native = receipt_payload.get("native_rfd3")
    native_runtime_input = receipt_native.get("input") if isinstance(receipt_native, dict) else None
    expected_native = dict(request.get("rfd3") or {})
    expected_native["input"] = native_runtime_input
    runtime_input = receipt_payload.get("runtime_input")
    try:
        native_input_payload = json.loads(native_input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid native RFD3 producer input: {exc}") from exc
    if (
        receipt_payload.get("schema") != "bms.rfd3.local-redesign.preparation-receipt.v1"
        or receipt_payload.get("request_sha256") != request_sha256(request)
        or not isinstance(design_id, str)
        or not design_id
        or not isinstance(native_runtime_input, str)
        or Path(native_runtime_input).name != source_file.name
        or receipt_native != expected_native
        or native_input_payload != {design_id: expected_native}
        or receipt_payload.get("native_input_sha256")
        != hashlib.sha256(canonical_json(native_input_payload).encode("utf-8")).hexdigest()
        or not isinstance(runtime_input, dict)
        or runtime_input.get("path") != source_file.name
        or runtime_input.get("sha256") != expected_source_sha
        or receipt_payload.get("redesign_mode") != request.get("redesign_mode")
        or receipt_payload.get("sequence_policy") != request.get("sequence_policy")
    ):
        raise SystemExit("native RFD3 preparation receipt does not match the canonical request")
    if request.get("sequence_policy") == "skip" and sequence_state != "not_requested":
        raise SystemExit("skip sequence policy requires sequence_design_not_requested evidence")

    artifacts: list[dict[str, Any]] = [
        _descriptor(
            role="source_structure",
            source_path=source_file,
            storage_path=source_storage_path,
            relative_path=f"external_inputs/{source_storage_path.name}",
        ),
        _descriptor(
            role="native_request",
            source_path=request_path,
            storage_path=request_storage_path,
            relative_path=f"requests/{request_storage_path.name}",
        ),
        _descriptor(
            role="preparation_receipt",
            source_path=preparation_receipt,
            storage_path=preparation_receipt_storage_path,
            relative_path=f"collected/protein_local_redesign/{preparation_receipt_storage_path.name}",
        ),
        _descriptor(
            role="native_producer_input",
            source_path=native_input,
            storage_path=native_input_storage_path,
            relative_path=f"collected/protein_local_redesign/{native_input_storage_path.name}",
        ),
        _descriptor(
            role="producer_log",
            source_path=log_file,
            storage_path=storage_root / log_file.name,
            relative_path=f"{args.artifact_relative_prefix}/{log_file.name}",
        ),
        _descriptor(
            role="producer_metadata_index",
            source_path=metadata_jsonl,
            storage_path=storage_root / metadata_jsonl.name,
            relative_path=f"{args.artifact_relative_prefix}/{metadata_jsonl.name}",
        ),
    ]
    candidates: list[dict[str, Any]] = []
    for cif_path in sorted(cif_paths):
        candidate_id = _candidate_id(cif_path)
        metadata_path, metadata = metadata_by_candidate[candidate_id]
        candidate_artifacts = [
            _descriptor(
                role="structure",
                source_path=cif_path,
                storage_path=storage_root / cif_path.name,
                relative_path=f"{args.artifact_relative_prefix}/{cif_path.name}",
            ),
            _descriptor(
                role="native_prediction_metadata",
                source_path=metadata_path,
                storage_path=storage_root / metadata_path.name,
                relative_path=f"{args.artifact_relative_prefix}/{metadata_path.name}",
            ),
        ]
        for role, trajectory_path in sorted(trajectory_by_candidate.get(candidate_id, {}).items()):
            candidate_artifacts.append(
                _descriptor(
                    role=role,
                    source_path=trajectory_path,
                    storage_path=storage_root / "trajectories" / trajectory_path.name,
                    relative_path=f"{args.artifact_relative_prefix}/trajectories/{trajectory_path.name}",
                )
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
        "request_sha256": request_sha256(request),
        "request_schema": request.get("schema"),
        "profile_id": request.get("profile_id"),
        "profile_registry_sha256": request.get("profile_registry_sha256"),
        "profile": request.get("profile"),
        "redesign_mode": request.get("redesign_mode"),
        "contig_dialect": request.get("contig_dialect"),
        "sequence_policy": request.get("sequence_policy"),
        "input": request.get("input"),
        "rfd3": request.get("rfd3"),
        "execution": execution,
        "execution_evidence": {
            "requested_num_designs": requested_num_designs,
            "observed_num_designs": len(candidates),
            "candidate_count_integrity": "exact",
            "trajectories": "produced" if trajectory_paths else "not_requested",
            "sequence_design": sequence_state,
        },
        "evaluation": request.get("evaluation"),
        "artifacts": artifacts,
        "candidates": candidates,
    }
    manifest["manifest_sha256"] = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    Path(args.output).write_text(canonical_json(manifest) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
