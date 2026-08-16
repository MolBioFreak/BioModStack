#!/usr/bin/env python3
"""Bind explicit upstream ConforNets coordinates to normalized output bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from confornets_source_closure import validate_source_evidence


class LedgerError(ValueError):
    pass


_UPSTREAM_LEDGER_FIELDS = {
    "coordinates", "runtime_coordinates", "source_relative_path", "bytes", "sha256",
    "request_sha256", "coordinate_plan_sha256", "runtime_identity",
    "container_digest", "checkpoint_sha256",
}
_RUNTIME_COORDINATE_FIELDS = {
    "target_id", "task", "test_case_id", "reference_id", "run_index",
    "confornet_index", "saved_step", "sample_index",
}


def _validate_upstream_entry_shape(entry: object) -> None:
    if not isinstance(entry, dict) or set(entry) != _UPSTREAM_LEDGER_FIELDS:
        raise LedgerError("upstream coordinate ledger row is malformed")
    runtime_coordinates = entry.get("runtime_coordinates")
    if not isinstance(runtime_coordinates, dict) or set(runtime_coordinates) != _RUNTIME_COORDINATE_FIELDS:
        raise LedgerError("upstream runtime coordinates are malformed")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(value: object) -> str:
    if not isinstance(value, str):
        raise LedgerError("ledger path must be a string")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {"", ".", ".."} for part in path.parts) or "\\" in value:
        raise LedgerError(f"non-canonical ledger path: {value!r}")
    return value


def _canonicalize_mse_run_coordinate(
    entry: dict[str, Any], plan_coordinates: list[dict[str, Any]]
) -> dict[str, Any]:
    """Map upstream MSE job ordinals onto the requested ConforNet axis.

    Upstream MSE numbers each independently trained run as ``confornet_index=job_idx``.
    The public request contract represents those independent executions with
    ``run_index`` and reserves ``confornet_index`` for the requested ConforNet axis
    (which is one for MSE).  Bind by every authoritative coordinate except that
    redundant upstream job ordinal and require a unique canonical match.
    """

    observed = entry.get("coordinates")
    runtime = entry.get("runtime_coordinates")
    if not isinstance(observed, dict) or not isinstance(runtime, dict):
        raise LedgerError("upstream coordinate mapping is malformed")
    if observed.get("task") != "mse":
        return observed
    observed_without_net = {
        key: value for key, value in observed.items() if key != "confornet_index"
    }
    matches = [
        coordinate
        for coordinate in plan_coordinates
        if {
            key: value for key, value in coordinate.items() if key != "confornet_index"
        }
        == observed_without_net
    ]
    if len(matches) != 1:
        raise LedgerError("upstream MSE run does not map uniquely to the canonical plan")
    for key in _RUNTIME_COORDINATE_FIELDS - {"target_id", "confornet_index"}:
        if runtime.get(key) != observed.get(key):
            raise LedgerError("upstream MSE runtime coordinates contradict mapped coordinates")
    runtime_index = runtime.get("confornet_index")
    if (
        isinstance(runtime_index, bool)
        or not isinstance(runtime_index, int)
        or runtime_index < 0
        or runtime_index != observed.get("confornet_index")
    ):
        raise LedgerError("upstream MSE job ordinal is invalid")
    return matches[0]


def bind(request_path: Path, plan_path: Path, native_root: Path, output: Path) -> None:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    samples = json.loads((native_root / "samples.json").read_text(encoding="utf-8"))
    if not isinstance(request, dict) or request.get("request_sha256") != _canonical_sha256(
        {key: value for key, value in request.items() if key != "request_sha256"}
    ):
        raise LedgerError("canonical request hash is invalid")
    if not isinstance(plan, dict) or plan.get("coordinate_plan_sha256") != _canonical_sha256(
        {key: value for key, value in plan.items() if key != "coordinate_plan_sha256"}
    ):
        raise LedgerError("canonical coordinate-plan hash is invalid")
    if (
        plan.get("request_id") != request.get("request_id")
        or plan.get("request_sha256") != request.get("request_sha256")
        or plan.get("backend") != "confornets"
        or not isinstance(plan.get("coordinates"), list)
        or plan.get("expected_cardinality") != len(plan["coordinates"])
    ):
        raise LedgerError("canonical request/coordinate-plan binding is invalid")
    if not isinstance(samples, list) or len(samples) != len(plan["coordinates"]):
        raise LedgerError("normalized sample cardinality does not equal the coordinate plan")
    upstream_path = native_root / "raw" / "cm_upstream_coordinate_ledger_v1.jsonl"
    lines = upstream_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise LedgerError("upstream coordinate ledger is empty")
    upstream_entries = [json.loads(line) for line in lines]
    if len(upstream_entries) != len(plan["coordinates"]):
        raise LedgerError("upstream coordinate ledger cardinality mismatch")

    identity = request["confornets"]["backend_identity"]
    attestation_path = native_root / "raw" / "cm_confornets_runtime_attestation_v1.json"
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    expected_attestation_fields = {
        "schema_name", "schema_version", "status", "request_sha256",
        "coordinate_plan_sha256", "backend_commit", "runtime_identity",
        "container_digest", "feature_identity_sha256", "checkpoint_sha256",
        "executed_sources", "commands",
    }
    if not isinstance(attestation, dict) or set(attestation) != expected_attestation_fields:
        raise LedgerError("runtime attestation is malformed")
    if (
        attestation["schema_name"] != "cm_confornets_runtime_attestation"
        or attestation["schema_version"] != 2
        or attestation["status"] != "container_executed"
        or attestation["request_sha256"] != request["request_sha256"]
        or attestation["coordinate_plan_sha256"] != plan["coordinate_plan_sha256"]
        or attestation["backend_commit"] != identity["backend_commit"]
        or attestation["runtime_identity"] != identity["runtime_identity"]
        or attestation["container_digest"] != identity["container_digest"]
        or attestation["feature_identity_sha256"] != identity["feature_identity_sha256"]
        or attestation["checkpoint_sha256"] != request["confornets"]["checkpoint"]["sha256"]
    ):
        raise LedgerError("runtime attestation identity is not authoritative")
    sources = attestation["executed_sources"]
    if not isinstance(sources, list) or not sources or not isinstance(attestation["commands"], list):
        raise LedgerError("runtime attestation source or command evidence is missing")
    source_paths: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != {"relative_path", "bytes", "sha256"}:
            raise LedgerError("runtime attestation source record is malformed")
        relative = _relative(source["relative_path"])
        if relative in source_paths or not isinstance(source["bytes"], int) or source["bytes"] < 1:
            raise LedgerError("runtime attestation source identity is duplicated or invalid")
        source_paths.add(relative)
        if not isinstance(source["sha256"], str) or len(source["sha256"]) != 64:
            raise LedgerError("runtime attestation source hash is invalid")
    try:
        validate_source_evidence(
            request["confornets"]["task"],
            sources,
            attestation["commands"],
        )
    except ValueError as exc:
        raise LedgerError(str(exc)) from exc

    plan_coordinates = plan["coordinates"]
    by_source: dict[str, dict[str, Any]] = {}
    for entry in upstream_entries:
        _validate_upstream_entry_shape(entry)
        if entry.get("request_sha256") != request["request_sha256"]:
            raise LedgerError("upstream ledger request binding mismatch")
        if entry.get("coordinate_plan_sha256") != plan["coordinate_plan_sha256"]:
            raise LedgerError("upstream ledger coordinate-plan binding mismatch")
        if (
            entry["runtime_identity"] != identity["runtime_identity"]
            or entry["container_digest"] != identity["container_digest"]
            or entry["checkpoint_sha256"] != request["confornets"]["checkpoint"]["sha256"]
        ):
            raise LedgerError("upstream ledger runtime identity mismatch")
        source_path = _relative(entry.get("source_relative_path"))
        if source_path in by_source:
            raise LedgerError("duplicate upstream source path")
        entry = dict(entry)
        entry["coordinates"] = _canonicalize_mse_run_coordinate(entry, plan_coordinates)
        by_source[source_path] = entry
    expected_coordinates = {_canonical_bytes(value) for value in plan_coordinates}
    observed_coordinates = [_canonical_bytes(entry.get("coordinates")) for entry in by_source.values()]
    if len(observed_coordinates) != len(set(observed_coordinates)) or set(observed_coordinates) != expected_coordinates:
        raise LedgerError("upstream ledger coordinates do not equal canonical plan")

    normalized_by_coordinate: dict[bytes, dict[str, Any]] = {}
    normalized_paths: set[str] = set()
    for sample in samples:
        source_path = _relative(sample.get("source_relative_path"))
        relative_path = _relative(sample.get("relative_path"))
        if source_path not in by_source:
            raise LedgerError(f"sample has no explicit upstream coordinate: {source_path}")
        if relative_path in normalized_paths:
            raise LedgerError("shared normalized output path")
        normalized_paths.add(relative_path)
        artifact = native_root / relative_path
        if not artifact.is_file() or artifact.is_symlink():
            raise LedgerError(f"normalized output is missing or unsafe: {relative_path}")
        if sample.get("sha256") != _sha256(artifact) or sample.get("bytes") != artifact.stat().st_size:
            raise LedgerError(f"normalized sample byte identity mismatch: {relative_path}")
        source_artifact = native_root / "raw" / source_path
        if not source_artifact.is_file() or source_artifact.is_symlink():
            raise LedgerError(f"explicit upstream coordinate output is missing: {source_path}")
        upstream = by_source[source_path]
        if upstream["sha256"] != _sha256(source_artifact) or upstream["bytes"] != source_artifact.stat().st_size:
            raise LedgerError("write-time upstream coordinate byte identity changed")
        coordinate_key = _canonical_bytes(by_source[source_path]["coordinates"])
        if coordinate_key in normalized_by_coordinate:
            raise LedgerError("duplicate normalized coordinate")
        normalized_by_coordinate[coordinate_key] = {
                "coordinates": by_source[source_path]["coordinates"],
                "relative_path": relative_path,
                "bytes": artifact.stat().st_size,
                "sha256": _sha256(artifact),
                "source_relative_path": f"raw/{source_path}",
                "source_bytes": source_artifact.stat().st_size,
                "source_sha256": _sha256(source_artifact),
            }
    normalized_entries = [
        normalized_by_coordinate[_canonical_bytes(coordinate)]
        for coordinate in plan_coordinates
    ]
    if len(normalized_entries) != len(plan["coordinates"]):
        raise LedgerError("output ledger cardinality mismatch")

    if output.exists():
        raise LedgerError(f"output already exists: {output}")
    shutil.copytree(native_root, output)
    native_request = output / "request.json"
    ledger = {
        "schema_name": "cm_output_coordinate_ledger",
        "schema_version": 1,
        "request_sha256": request["request_sha256"],
        "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
        "native_request_sha256": _sha256(native_request),
        "entries": normalized_entries,
    }
    (output / "cm_output_coordinate_ledger_v1.json").write_bytes(_canonical_bytes(ledger))
    output_attestation = output / "raw" / "cm_confornets_runtime_attestation_v1.json"
    receipt = {
        "schema_name": "cm_confornets_execution_receipt", "schema_version": 1,
        "status": "container_executed", "request_sha256": request["request_sha256"],
        "request_file_sha256": _sha256(request_path),
        "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
        "coordinate_plan_file_sha256": _sha256(plan_path),
        "native_request_sha256": _sha256(output / "request.json"),
        "output_ledger_sha256": _sha256(output / "cm_output_coordinate_ledger_v1.json"),
        "runtime_attestation_path": "raw/cm_confornets_runtime_attestation_v1.json",
        "runtime_attestation_sha256": _sha256(output_attestation),
        "checkpoint_sha256": attestation["checkpoint_sha256"],
        "container_digest": attestation["container_digest"],
        "backend_commit": attestation["backend_commit"],
        "runtime_identity": attestation["runtime_identity"],
        "feature_identity_sha256": attestation["feature_identity_sha256"],
    }
    (output / "execution_receipt.json").write_bytes(_canonical_bytes(receipt))
    native_files = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            native_files.append(
                {
                    "relative_path": path.relative_to(output).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    file_ledger = {
        "schema_name": "cm_native_file_ledger",
        "schema_version": 1,
        "request_sha256": request["request_sha256"],
        "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
        "files": native_files,
    }
    (output / "cm_native_file_ledger_v1.json").write_bytes(_canonical_bytes(file_ledger))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--coordinate-plan", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        bind(args.request, args.coordinate_plan, args.native_root, args.out)
    except (LedgerError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"ConforNets output ledger binding failed: {exc}\n")
    print(f"Wrote request-bound ConforNets output ledger under {args.out}")


if __name__ == "__main__":
    main()
