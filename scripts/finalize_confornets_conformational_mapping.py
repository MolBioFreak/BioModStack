#!/usr/bin/env python3
"""Finalize legacy ConforNets outputs into canonical CM Phase 4 manifests.

The sibling cm_coordinate_plan_v1.json and an explicit request-bound native
output ledger jointly authorize coordinates. Sample order and filenames never
supply coordinate identity.
"""

from __future__ import annotations

import argparse
import hashlib
import mimetypes
import os
import shutil
import stat
import sys
import tempfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from confornets_source_closure import validate_source_evidence


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "platform" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.conformational_mapping.contracts import (  # noqa: E402
    ContractValidationError,
    ResumeDescriptor,
    candidate_id,
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
    ensure_candidate_id_uniqueness,
    parse_backend_coordinates,
    validate_schema,
)
from services.conformational_mapping.request_builder import (  # noqa: E402
    build_confornets_coordinate_plan,
)
from services.conformational_mapping.structure_normalizer import (  # noqa: E402
    StructureMapError,
    validate_coordinate_mmcif,
)


class FinalizationError(ValueError):
    """The native result cannot be finalized without guessing or data loss."""


_PLAN_FIELDS = {
    "schema_name",
    "schema_version",
    "request_id",
    "backend",
    "request_sha256",
    "expected_cardinality",
    "coordinates",
    "coordinate_plan_sha256",
}
_LEGACY_REQUIRED = {
    "artifact_manifest.json",
    "samples.json",
    "request.json",
    "cm_output_coordinate_ledger_v1.json",
}
_ANALYTIC_STATUSES = {"computed", "not_computed", "requested_missing"}
_EXECUTION_RECEIPT_FIELDS = {
    "schema_name",
    "schema_version",
    "status",
    "request_sha256",
    "request_file_sha256",
    "coordinate_plan_sha256",
    "coordinate_plan_file_sha256",
    "native_request_sha256",
    "output_ledger_sha256",
    "runtime_attestation_path",
    "runtime_attestation_sha256",
    "checkpoint_sha256",
    "container_digest",
    "backend_commit",
    "runtime_identity",
    "feature_identity_sha256",
}


def _load_json(path: Path, *, label: str) -> Any:
    try:
        return canonical_json_loads(path.read_bytes())
    except (OSError, ContractValidationError) as exc:
        raise FinalizationError(f"cannot read {label} {path}: {exc}") from exc


def _safe_relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise FinalizationError(f"{label} must be a nonempty relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or value.startswith("./")
        or "\\" in value
        or any(part in {"", "."} for part in path.parts)
    ):
        raise FinalizationError(f"{label} contains a path escape: {value!r}")
    return value


def _contained_file(root: Path, relative_path: str, *, label: str) -> Path:
    safe = _safe_relative_path(relative_path, label=label)
    candidate = root.joinpath(*PurePosixPath(safe).parts)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FinalizationError(f"{label} escapes the native root: {safe!r}") from exc
    if candidate.is_symlink():
        raise FinalizationError(f"{label} may not be a symlink: {safe!r}")
    if not candidate.is_file():
        raise FinalizationError(f"missing {label}: {safe}")
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request_owned_path(
    request_root: Path,
    relative_value: object,
    *,
    label: str,
    expect_directory: bool = False,
) -> Path:
    relative = _safe_relative_path(relative_value, label=label)
    root_metadata = request_root.lstat()
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise FinalizationError("canonical request root must be a real directory")
    candidate = request_root
    metadata = root_metadata
    for index, component in enumerate(PurePosixPath(relative).parts):
        candidate = candidate / component
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise FinalizationError(f"missing {label}: {relative}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise FinalizationError(f"{label} may not contain a symlink: {relative}")
        if metadata.st_uid != root_metadata.st_uid:
            raise FinalizationError(f"{label} is not request-owned: {relative}")
        if index < len(PurePosixPath(relative).parts) - 1 and not stat.S_ISDIR(
            metadata.st_mode
        ):
            raise FinalizationError(f"{label} has a non-directory component: {relative}")
    if expect_directory:
        if not stat.S_ISDIR(metadata.st_mode):
            raise FinalizationError(f"{label} is not a directory: {relative}")
    elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise FinalizationError(f"{label} is not an unaliased regular file: {relative}")
    return candidate


def _validate_execution_receipt(
    request_path: Path,
    native_root: Path,
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> bool:
    receipt_path = native_root / "execution_receipt.json"
    if not receipt_path.exists():
        return False
    receipt = _load_json(receipt_path, label="execution receipt")
    if not isinstance(receipt, Mapping) or set(receipt) != _EXECUTION_RECEIPT_FIELDS:
        raise FinalizationError("execution receipt is malformed")
    if (
        receipt["schema_name"] != "cm_confornets_execution_receipt"
        or receipt["schema_version"] != 1
        or receipt["status"] != "container_executed"
    ):
        raise FinalizationError("execution receipt status/schema is not authoritative")
    plan_path = request_path.parent / "cm_coordinate_plan_v1.json"
    output_ledger_path = native_root / "cm_output_coordinate_ledger_v1.json"
    expected_bindings = {
        "request_sha256": request["request_sha256"],
        "request_file_sha256": _sha256_file(request_path),
        "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
        "coordinate_plan_file_sha256": _sha256_file(plan_path),
        "native_request_sha256": _sha256_file(native_root / "request.json"),
        "output_ledger_sha256": _sha256_file(output_ledger_path),
    }
    if any(receipt.get(key) != value for key, value in expected_bindings.items()):
        raise FinalizationError("execution receipt request/plan/native/output binding mismatch")

    settings = request["confornets"]
    identity = settings["backend_identity"]
    expected_identity = {
        "checkpoint_sha256": settings["checkpoint"]["sha256"],
        "container_digest": identity["container_digest"],
        "backend_commit": identity["backend_commit"],
        "runtime_identity": identity["runtime_identity"],
        "feature_identity_sha256": identity["feature_identity_sha256"],
    }
    if any(receipt.get(key) != value for key, value in expected_identity.items()):
        raise FinalizationError("execution receipt runtime identity mismatch")
    attestation_relative = _safe_relative_path(
        receipt["runtime_attestation_path"], label="runtime attestation"
    )
    attestation_path = _contained_file(native_root, attestation_relative, label="runtime attestation")
    if _sha256_file(attestation_path) != receipt["runtime_attestation_sha256"]:
        raise FinalizationError("runtime attestation hash mismatch")
    attestation = _load_json(attestation_path, label="runtime attestation")
    if not isinstance(attestation, Mapping):
        raise FinalizationError("runtime attestation is malformed")
    if (
        attestation.get("schema_name") != "cm_confornets_runtime_attestation"
        or attestation.get("schema_version") != 2
    ):
        raise FinalizationError("runtime attestation schema is not authoritative")
    for key, value in expected_identity.items():
        if attestation.get(key) != value:
            raise FinalizationError("runtime attestation identity mismatch")
    if (
        attestation.get("request_sha256") != request["request_sha256"]
        or attestation.get("coordinate_plan_sha256") != plan["coordinate_plan_sha256"]
        or attestation.get("status") != "container_executed"
        or not isinstance(attestation.get("executed_sources"), list)
        or not attestation["executed_sources"]
        or not isinstance(attestation.get("commands"), list)
        or not attestation["commands"]
    ):
        raise FinalizationError("runtime attestation execution binding is incomplete")
    try:
        validate_source_evidence(
            request["confornets"]["task"],
            attestation["executed_sources"],
            attestation["commands"],
        )
    except ValueError as exc:
        raise FinalizationError(str(exc)) from exc
    return True


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(dict(value)))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _copy_file_bytes(source: Path, destination: Path) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _validate_snapshots(
    snapshot_path: Path, request: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if snapshot_path.is_symlink():
        raise FinalizationError("canonical complex snapshot authority must not be a symlink")
    resolved = snapshot_path.resolve()
    if not resolved.is_file():
        raise FinalizationError("canonical complex snapshot authority is absent")
    try:
        raw = canonical_json_loads(resolved.read_bytes())
    except (OSError, ContractValidationError) as exc:
        raise FinalizationError(f"canonical complex snapshot authority is malformed: {exc}") from exc
    snapshots = raw if isinstance(raw, list) else [raw]
    if not snapshots:
        raise FinalizationError("canonical complex snapshot authority is empty")
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            raise FinalizationError("canonical complex snapshot entry must be an object")
        try:
            validate_schema("cm_complex_snapshot_v1", snapshot)
        except ContractValidationError as exc:
            raise FinalizationError(str(exc)) from exc
    by_target = {str(snapshot.get("target_id")): snapshot for snapshot in snapshots}
    for target in request.get("targets", []):
        target_id = str(target.get("target_id"))
        if target_id not in by_target:
            raise FinalizationError(f"CM target has no complex snapshot: {target_id}")
    return snapshots


def _validate_request(request_path: Path) -> dict[str, Any]:
    request = _load_json(request_path, label="canonical request")
    if not isinstance(request, dict):
        raise FinalizationError("canonical request must be an object")
    try:
        validate_schema("cm_request_v1", request)
    except ContractValidationError as exc:
        raise FinalizationError(str(exc)) from exc
    if request["backend"] != "confornets":
        raise FinalizationError("Phase 4 finalizer accepts only backend=confornets")
    if len(request["targets"]) != 1:
        raise FinalizationError("ConforNets requires exactly one single-chain target")
    without_hash = {key: value for key, value in request.items() if key != "request_sha256"}
    if canonical_sha256(without_hash) != request["request_sha256"]:
        raise FinalizationError("canonical request_sha256 mismatch")
    return request


def _validate_coordinate_plan(
    request_path: Path, request: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan_path = request_path.parent / "cm_coordinate_plan_v1.json"
    plan = _load_json(plan_path, label="coordinate plan")
    if not isinstance(plan, dict) or set(plan) != _PLAN_FIELDS:
        raise FinalizationError("coordinate plan fields are incomplete or unknown")
    if plan["schema_name"] != "cm_coordinate_plan" or plan["schema_version"] != 1:
        raise FinalizationError("unsupported coordinate plan schema")
    if plan["request_id"] != request["request_id"] or plan["backend"] != "confornets":
        raise FinalizationError("coordinate plan identity does not match request")
    if plan["request_sha256"] != request["request_sha256"]:
        raise FinalizationError("coordinate plan request hash binding mismatch")
    plan_without_hash = {
        key: value for key, value in plan.items() if key != "coordinate_plan_sha256"
    }
    if canonical_sha256(plan_without_hash) != plan["coordinate_plan_sha256"]:
        raise FinalizationError("coordinate plan SHA-256 mismatch")
    coordinates = plan["coordinates"]
    if not isinstance(coordinates, list) or not coordinates:
        raise FinalizationError("coordinate plan must be nonempty")
    normalized: list[dict[str, Any]] = []
    for index, coordinate in enumerate(coordinates):
        try:
            parsed = parse_backend_coordinates(coordinate)
        except Exception as exc:
            raise FinalizationError(f"invalid coordinate plan row {index}: {exc}") from exc
        if parsed.backend != "confornets":
            raise FinalizationError("coordinate plan contains a non-ConforNets coordinate")
        normalized.append(parsed.model_dump(mode="json"))
    if len({canonical_json_bytes(row) for row in normalized}) != len(normalized):
        raise FinalizationError("coordinate plan contains a duplicate coordinate")
    if plan["expected_cardinality"] != len(normalized):
        raise FinalizationError("coordinate plan expected_cardinality mismatch")
    target_id = request["targets"][0]["target_id"]
    if {row["target_id"] for row in normalized} != {target_id}:
        raise FinalizationError("coordinate target identity does not match request")
    rebuilt = build_confornets_coordinate_plan(
        request["confornets"], target_id=target_id
    )
    if [canonical_json_bytes(row) for row in rebuilt] != [
        canonical_json_bytes(row) for row in normalized
    ]:
        raise FinalizationError("coordinate plan does not equal canonical ConforNets settings")
    _validate_group_cardinality(normalized)
    return plan, normalized


def _validate_group_cardinality(coordinates: Sequence[Mapping[str, Any]]) -> None:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for coordinate in coordinates:
        group = (
            coordinate["target_id"],
            coordinate["task"],
            coordinate["test_case_id"],
            coordinate["reference_id"],
        )
        groups[group].append(coordinate)
    if len({group[0] for group in groups}) != 1:
        raise FinalizationError("ConforNets supports one target only")
    references = {group[3] for group in groups if group[3] is not None}
    if len(references) > 2:
        raise FinalizationError("ConforNets supports at most two references")

    execution_groups: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for rows in groups.values():
        for row in rows:
            execution_groups[
                (
                    row["target_id"],
                    row["task"],
                    row["test_case_id"],
                    row["reference_id"],
                    row["run_index"],
                    row["saved_step"],
                    row["confornet_index"],
                )
            ].append(row["sample_index"])
    for key, sample_indexes in execution_groups.items():
        ordered = sorted(sample_indexes)
        if ordered != list(range(len(ordered))):
            raise FinalizationError(
                f"coordinate execution group {key!r} does not select a sample prefix"
            )


def _validate_native_tree(native_root: Path) -> list[str]:
    if not native_root.is_dir():
        raise FinalizationError(f"native root does not exist: {native_root}")
    files: list[str] = []
    inodes: dict[tuple[int, int], str] = {}
    for path in sorted(native_root.rglob("*")):
        if path.is_symlink():
            raise FinalizationError(f"native tree contains a path escape symlink: {path}")
        if path.is_file():
            relative = path.relative_to(native_root).as_posix()
            inode = (path.stat().st_dev, path.stat().st_ino)
            if inode in inodes:
                raise FinalizationError(
                    f"native tree contains hardlink alias paths: {inodes[inode]}, {relative}"
                )
            inodes[inode] = relative
            files.append(relative)
    missing = sorted(_LEGACY_REQUIRED - set(files))
    if missing:
        raise FinalizationError(f"missing required legacy artifacts: {missing}")
    by_basename: dict[str, list[str]] = defaultdict(list)
    for relative in files:
        by_basename[PurePosixPath(relative).name].append(relative)
    collisions: list[str] = []
    for basename, relatives in by_basename.items():
        if len(relatives) < 2 or basename not in _LEGACY_REQUIRED:
            continue
        hashes = {_sha256_file(native_root / relative) for relative in relatives}
        copied_raw_pair = any(relative.startswith("raw/") for relative in relatives)
        if len(hashes) != 1 or not copied_raw_pair:
            collisions.append(basename)
    if collisions:
        raise FinalizationError(
            f"native hierarchy contains protected control basename collision: {sorted(collisions)}"
        )
    return files


def _validate_legacy_manifest(
    native_root: Path,
    native_files: Sequence[str],
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = _load_json(native_root / "artifact_manifest.json", label="legacy artifact manifest")
    if not isinstance(manifest, dict):
        raise FinalizationError("legacy artifact manifest must be an object")
    declared_files = set(_LEGACY_REQUIRED) | {"artifact_manifest.json"}
    declared_directories: set[str] = set()
    for key, value in manifest.items():
        if key.endswith(("_json", "_csv")) or key == "commands_log":
            relative = _safe_relative_path(value, label=f"legacy manifest {key}")
            _contained_file(native_root, relative, label=f"legacy manifest {key}")
            declared_files.add(relative)
        elif key.endswith("_dir"):
            relative = _safe_relative_path(value, label=f"legacy manifest {key}")
            directory = native_root.joinpath(*PurePosixPath(relative).parts)
            if directory.is_symlink() or not directory.is_dir():
                raise FinalizationError(f"missing legacy manifest directory: {relative}")
            declared_directories.add(relative)

    ledger_path = native_root / "authenticated_sidecars.json"
    if ledger_path.exists():
        declared_files.add("authenticated_sidecars.json")
        ledger = _load_json(ledger_path, label="authenticated sidecar ledger")
        if isinstance(ledger, Mapping) and isinstance(ledger.get("files"), list):
            for record in ledger["files"]:
                if isinstance(record, Mapping) and "relative_path" in record:
                    declared_files.add(
                        _safe_relative_path(
                            record["relative_path"], label="authenticated sidecar ledger"
                        )
                    )

    execution_receipt_path = native_root / "execution_receipt.json"
    if execution_receipt_path.exists():
        declared_files.add("execution_receipt.json")

    declared_files.add("cm_output_coordinate_ledger_v1.json")
    native_ledger_path = native_root / "cm_native_file_ledger_v1.json"
    if native_ledger_path.exists():
        declared_files.add("cm_native_file_ledger_v1.json")
        native_ledger = _load_json(native_ledger_path, label="native file ledger")
        if not isinstance(native_ledger, Mapping) or set(native_ledger) != {
            "schema_name",
            "schema_version",
            "request_sha256",
            "coordinate_plan_sha256",
            "files",
        }:
            raise FinalizationError("native file ledger is malformed")
        if native_ledger["schema_name"] != "cm_native_file_ledger" or native_ledger["schema_version"] != 1:
            raise FinalizationError("native file ledger schema is unsupported")
        if native_ledger["request_sha256"] != request["request_sha256"]:
            raise FinalizationError("native file ledger request binding mismatch")
        if native_ledger["coordinate_plan_sha256"] != plan["coordinate_plan_sha256"]:
            raise FinalizationError("native file ledger coordinate-plan binding mismatch")
        records = native_ledger["files"]
        if not isinstance(records, list):
            raise FinalizationError("native file ledger records must be an array")
        ledger_paths: list[str] = []
        for index, record in enumerate(records):
            if not isinstance(record, Mapping) or set(record) != {"relative_path", "bytes", "sha256"}:
                raise FinalizationError(f"native file ledger row {index} is malformed")
            relative = _safe_relative_path(record["relative_path"], label="native file ledger")
            path = _contained_file(native_root, relative, label="native file ledger artifact")
            if record["bytes"] != path.stat().st_size or record["sha256"] != _sha256_file(path):
                raise FinalizationError(f"native file ledger hash/size mismatch: {relative}")
            ledger_paths.append(relative)
            declared_files.add(relative)
        expected_ledger_paths = set(native_files) - {"cm_native_file_ledger_v1.json"}
        if len(ledger_paths) != len(set(ledger_paths)) or set(ledger_paths) != expected_ledger_paths:
            raise FinalizationError("native file ledger has missing, extra, or shared paths")

    unreferenced = [
        path
        for path in native_files
        if path not in declared_files
        and not any(path.startswith(f"{directory}/") for directory in declared_directories)
    ]
    if unreferenced:
        raise FinalizationError(f"unreferenced native artifacts: {sorted(unreferenced)}")
    return manifest


def _validate_single_chain(
    native_root: Path,
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    legacy_request = _load_json(native_root / "request.json", label="legacy ConforNets request")
    if not isinstance(legacy_request, dict):
        raise FinalizationError("legacy ConforNets request must be an object")
    sequence = legacy_request.get("sequence")
    alphabet = set("ACDEFGHIKLMNPQRSTVWYX")
    if (
        not isinstance(sequence, str)
        or not sequence
        or sequence != sequence.strip().upper()
        or not set(sequence).issubset(alphabet)
    ):
        raise FinalizationError("ConforNets requires one single-chain protein sequence")
    references = legacy_request.get("references", [])
    if not isinstance(references, list) or len(references) > 2:
        raise FinalizationError("ConforNets supports at most two references")
    settings = request["confornets"]
    if sequence != settings["sequence"]:
        raise FinalizationError("native sequence does not match canonical sequence")
    if legacy_request.get("task") != settings["task"]:
        raise FinalizationError("native task does not match canonical task")
    if legacy_request.get("test_case") != settings["test_case_id"]:
        raise FinalizationError("native test case does not match canonical test case")
    expected_references = [
        {
            "name": reference["reference_id"],
            "sha256": reference["content_sha256"],
            "state": reference["state"],
            "source": reference["source"],
        }
        for reference in settings["references"]
    ]
    observed_references = [
        {
            "name": reference.get("name"),
            "sha256": reference.get("sha256"),
            "state": reference.get("state"),
            "source": reference.get("source"),
        }
        for reference in references
        if isinstance(reference, Mapping)
    ]
    if observed_references != expected_references:
        raise FinalizationError("native references do not match canonical reference state/source")
    params = legacy_request.get("params")
    if not isinstance(params, Mapping):
        raise FinalizationError("native settings are missing")
    expected_settings = {
        "num_runs": settings["runs"],
        "save_steps": settings["saved_steps"],
        "k_confornets": settings["confornet_count"],
        "num_samples": settings["samples"],
        "max_steps": settings["max_steps"],
        "num_recycles": settings["num_recycles"],
        "num_diffusion_steps": settings["num_diffusion_steps"],
        "lr": settings["learning_rate"],
        "grad_clip": settings["gradient_clip"],
        "skip_msa": settings["skip_msa"],
        "compute_confidence": settings["compute_confidence"],
        "save_full_confidence": settings["save_full_confidence"],
        "compute_evaluation": settings["compute_evaluation"],
    }
    if any(params.get(key) != value for key, value in expected_settings.items()):
        raise FinalizationError("native settings do not match canonical settings")
    if legacy_request.get("backend_identity") != settings["backend_identity"]:
        raise FinalizationError("native backend identity does not match canonical settings")
    expected_binding = {
        "request_sha256": request["request_sha256"],
        "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
        "target_id": request["targets"][0]["target_id"],
    }
    coordinate_binding = {
        **expected_binding,
        "coordinates": plan["coordinates"],
    }
    mapped_binding = {
        **coordinate_binding,
        "coordinate_mapping": {
            "target_id": {"constant": request["targets"][0]["target_id"]},
        },
    }
    if legacy_request.get("canonical_binding") not in (
        expected_binding,
        coordinate_binding,
        mapped_binding,
    ):
        raise FinalizationError("native request canonical binding mismatch")
    return legacy_request


def _bind_samples(
    native_root: Path,
    native_files: Sequence[str],
    request: Mapping[str, Any],
    plan: Mapping[str, Any],
    coordinates: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    samples = _load_json(native_root / "samples.json", label="legacy samples manifest")
    if not isinstance(samples, list):
        raise FinalizationError("legacy samples manifest must be an array")
    ledger = _load_json(
        native_root / "cm_output_coordinate_ledger_v1.json",
        label="output coordinate ledger",
    )
    if not isinstance(ledger, dict) or set(ledger) != {
        "schema_name",
        "schema_version",
        "request_sha256",
        "coordinate_plan_sha256",
        "native_request_sha256",
        "entries",
    }:
        raise FinalizationError("output coordinate ledger is malformed")
    if ledger["schema_name"] != "cm_output_coordinate_ledger" or ledger["schema_version"] != 1:
        raise FinalizationError("output coordinate ledger schema is unsupported")
    if ledger["request_sha256"] != request["request_sha256"]:
        raise FinalizationError("output ledger request binding mismatch")
    if ledger["coordinate_plan_sha256"] != plan["coordinate_plan_sha256"]:
        raise FinalizationError("output ledger coordinate plan binding mismatch")
    if ledger["native_request_sha256"] != _sha256_file(native_root / "request.json"):
        raise FinalizationError("output ledger native request SHA-256 mismatch")
    entries = ledger["entries"]
    if not isinstance(entries, list) or len(entries) != len(coordinates):
        qualifier = "missing" if len(samples) < len(coordinates) else "extra"
        raise FinalizationError(
            f"{qualifier} output ledger coordinates: expected {len(coordinates)}"
        )
    samples_by_path: dict[str, dict[str, Any]] = {}
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise FinalizationError(f"sample row {index} must be an object")
        relative = _safe_relative_path(sample.get("relative_path"), label=f"sample row {index}")
        if PurePosixPath(relative).suffix.lower() != ".cif":
            raise FinalizationError(f"sample row {index} is not an authoritative CIF")
        _contained_file(native_root, relative, label=f"sample coordinate artifact {index}")
        if relative in samples_by_path:
            raise FinalizationError("shared or duplicate coordinate artifact in samples manifest")
        samples_by_path[relative] = sample
    paths: list[str] = []
    bindings: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    ledger_coordinates: list[bytes] = []
    source_cifs: set[str] = set()
    for index, entry in enumerate(entries):
        legacy_fields = {"coordinates", "relative_path", "bytes", "sha256"}
        bound_source_fields = legacy_fields | {
            "source_relative_path", "source_bytes", "source_sha256"
        }
        if not isinstance(entry, Mapping) or frozenset(entry) not in {
            frozenset(legacy_fields), frozenset(bound_source_fields)
        }:
            raise FinalizationError(f"output ledger row {index} is malformed")
        try:
            coordinate = parse_backend_coordinates(entry["coordinates"]).model_dump(mode="json")
        except Exception as exc:
            raise FinalizationError(f"output ledger coordinate {index} is invalid: {exc}") from exc
        relative = _safe_relative_path(entry["relative_path"], label=f"output ledger row {index}")
        if relative not in samples_by_path:
            raise FinalizationError(f"output ledger path is unreferenced by samples: {relative}")
        artifact = _contained_file(native_root, relative, label=f"output ledger artifact {index}")
        sample = samples_by_path[relative]
        actual_sha256 = _sha256_file(artifact)
        actual_bytes = artifact.stat().st_size
        if entry["sha256"] != actual_sha256 or entry["bytes"] != actual_bytes:
            raise FinalizationError(f"output ledger hash/size mismatch: {relative}")
        if sample.get("sha256") != actual_sha256 or sample.get("bytes") != actual_bytes:
            raise FinalizationError(f"legacy sample hash/size mismatch: {relative}")
        try:
            validate_coordinate_mmcif(
                artifact.read_bytes(),
                expected_sequence=request["confornets"]["sequence"],
                expected_chain_id=request["confornets"]["chain_id"],
            )
        except (OSError, StructureMapError) as exc:
            raise FinalizationError(f"coordinate mmCIF {relative} is invalid: {exc}") from exc
        if "source_relative_path" in entry:
            source_relative = _safe_relative_path(
                entry["source_relative_path"], label=f"output ledger source row {index}"
            )
            source_artifact = _contained_file(
                native_root, source_relative, label=f"output ledger source artifact {index}"
            )
            if (
                entry["source_sha256"] != _sha256_file(source_artifact)
                or entry["source_bytes"] != source_artifact.stat().st_size
            ):
                raise FinalizationError(
                    f"output ledger source hash/size mismatch: {source_relative}"
                )
            if PurePosixPath(source_relative).suffix.lower() != ".cif":
                raise FinalizationError("output ledger coordinate source is not a CIF")
            source_cifs.add(source_relative)
        paths.append(relative)
        ledger_coordinates.append(canonical_json_bytes(coordinate))
        bindings.append((coordinate, dict(sample), relative))
    if len(set(paths)) != len(paths):
        raise FinalizationError("shared or duplicate coordinate artifact in samples manifest")
    if ledger_coordinates != [canonical_json_bytes(row) for row in coordinates]:
        raise FinalizationError("output ledger coordinates do not equal ordered canonical plan")
    observed_cifs = {path for path in native_files if PurePosixPath(path).suffix.lower() == ".cif"}
    planned_cifs = set(paths) | source_cifs
    if observed_cifs != planned_cifs:
        raise FinalizationError(
            f"coordinate artifact mismatch: missing={sorted(planned_cifs-observed_cifs)}, "
            f"extra={sorted(observed_cifs-planned_cifs)}"
        )
    return bindings


def _authenticated_analytics(
    native_root: Path, request: Mapping[str, Any]
) -> dict[str, tuple[str, dict[str, Any]]]:
    ledger_path = native_root / "authenticated_sidecars.json"
    if not ledger_path.exists():
        return {}
    ledger = _load_json(ledger_path, label="authenticated sidecar ledger")
    if not isinstance(ledger, dict) or ledger.get("schema_name") != "cm_authenticated_sidecars":
        raise FinalizationError("authenticated sidecar ledger schema is invalid")
    if ledger.get("request_id") != request["request_id"] or ledger.get("request_sha256") != request["request_sha256"]:
        raise FinalizationError("authenticated sidecar ledger request binding mismatch")
    records = ledger.get("files")
    if not isinstance(records, list):
        raise FinalizationError("authenticated sidecar ledger files must be an array")
    authenticated: dict[str, tuple[str, dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "relative_path",
            "sha256",
            "bytes",
            "semantic_role",
        }:
            raise FinalizationError("authenticated sidecar record is malformed")
        role = record["semantic_role"]
        if role not in {"confidence", "evaluation"} or role in authenticated:
            raise FinalizationError("authenticated sidecar role is unknown or duplicate")
        relative = _safe_relative_path(record["relative_path"], label=f"authenticated {role}")
        path = _contained_file(native_root, relative, label=f"authenticated {role}")
        if record["sha256"] != _sha256_file(path) or record["bytes"] != path.stat().st_size:
            raise FinalizationError(f"authenticated {role} sidecar hash/size mismatch")
        payload = _load_json(path, label=f"authenticated {role} sidecar")
        if not isinstance(payload, dict) or payload.get("status") not in _ANALYTIC_STATUSES:
            raise FinalizationError(f"authenticated {role} sidecar status is invalid")
        authenticated[role] = (relative, payload)
    return authenticated


def _requested(legacy_request: Mapping[str, Any], role: str) -> bool:
    params = legacy_request.get("params")
    if not isinstance(params, Mapping):
        return False
    return bool(params.get("compute_confidence" if role == "confidence" else "compute_evaluation"))


def _analytic_record(
    *,
    role: str,
    candidate_identifier: str,
    coordinate: Mapping[str, Any],
    sample: Mapping[str, Any],
    legacy_request: Mapping[str, Any],
    authenticated: Mapping[str, tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    requested = _requested(legacy_request, role)
    source = authenticated.get(role)
    if not requested:
        return {
            "schema_name": f"cm_confornets_{role}",
            "schema_version": 1,
            "candidate_id": candidate_identifier,
            "backend_coordinates": dict(coordinate),
            "status": "not_computed",
            "reason": f"{role} was not requested by the ConforNets request",
            "authenticated_source_path": None,
            "metrics": {},
        }
    if source is None:
        return {
            "schema_name": f"cm_confornets_{role}",
            "schema_version": 1,
            "candidate_id": candidate_identifier,
            "backend_coordinates": dict(coordinate),
            "status": "requested_missing",
            "reason": f"requested {role} has no request-bound authenticated sidecar",
            "authenticated_source_path": None,
            "metrics": {},
        }
    source_path, payload = source
    if payload["status"] != "computed":
        status = payload["status"]
        return {
            "schema_name": f"cm_confornets_{role}",
            "schema_version": 1,
            "candidate_id": candidate_identifier,
            "backend_coordinates": dict(coordinate),
            "status": status,
            "reason": payload.get("reason") or f"authenticated {role} was not computed",
            "authenticated_source_path": f"native/{source_path}",
            "metrics": {},
        }

    metrics: dict[str, Any]
    if role == "confidence":
        sample_id = sample.get("sample_id")
        source_samples = payload.get("samples")
        match = None
        if isinstance(source_samples, list):
            match = next(
                (
                    row
                    for row in source_samples
                    if isinstance(row, dict) and row.get("sample_id") == sample_id
                ),
                None,
            )
        if match is None:
            return {
                "schema_name": "cm_confornets_confidence",
                "schema_version": 1,
                "candidate_id": candidate_identifier,
                "backend_coordinates": dict(coordinate),
                "status": "requested_missing",
                "reason": "authenticated confidence sidecar lacks this planned sample identity",
                "authenticated_source_path": f"native/{source_path}",
                "metrics": {},
            }
        metrics = {
            key: value for key, value in match.items() if key not in {"sample_id", "frame_index"}
        }
    else:
        metrics = {
            key: value
            for key, value in payload.items()
            if key not in {"schema_version", "status", "reason", "samples"}
        }
    return {
        "schema_name": f"cm_confornets_{role}",
        "schema_version": 1,
        "candidate_id": candidate_identifier,
        "backend_coordinates": dict(coordinate),
        "status": "computed",
        "reason": None,
        "authenticated_source_path": f"native/{source_path}",
        "metrics": metrics,
    }


def _media_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    explicit = {
        ".cif": "chemical/x-mmcif",
        ".pt": "application/x-pytorch",
        ".json": "application/json",
        ".csv": "text/csv",
        ".log": "text/plain",
    }
    return explicit.get(suffix) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def _semantic_role(relative_path: str, coordinate_paths: set[str]) -> str:
    native_relative = relative_path.removeprefix("native/")
    if native_relative in coordinate_paths:
        return "authoritative_cif"
    name = PurePosixPath(relative_path).name.lower()
    if relative_path.startswith("canonical_sidecars/"):
        return "confidence_json" if name.endswith("confidence.json") else "full_data_json"
    if name == "request.json":
        return "request"
    if name in {
        "cm_upstream_coordinate_ledger_v1.jsonl",
        "cm_output_coordinate_ledger_v1.json",
    }:
        return "coordinate_ledger"
    if name == "cm_confornets_coordinate_context_v1.json":
        return "coordinate_context"
    if name == "samples.json":
        return "preprocess"
    if "loss" in name:
        return "loss"
    if name.endswith(".pt"):
        return "native_state"
    if "confidence" in relative_path.lower():
        return "optional_analytics"
    if "evaluation" in relative_path.lower() or name == "landscape.json":
        return "optional_analytics"
    if name.endswith(".log"):
        return "command_log"
    if name == "provenance.json":
        return "runtime_provenance"
    return "preprocess"


def finalize(
    request_path: Path,
    native_root: Path,
    output: Path,
    snapshot_path: Path,
) -> None:
    request_path = request_path.resolve()
    native_root = native_root.resolve()
    output = output.resolve()
    snapshot_path = snapshot_path.resolve()
    if output.exists():
        raise FinalizationError(f"output already exists; refusing to overwrite: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    request = _validate_request(request_path)
    plan, coordinates = _validate_coordinate_plan(request_path, request)
    _validate_snapshots(snapshot_path, request)
    native_files = _validate_native_tree(native_root)
    _validate_legacy_manifest(native_root, native_files, request, plan)
    legacy_request = _validate_single_chain(native_root, request, plan)
    bindings = _bind_samples(native_root, native_files, request, plan, coordinates)
    authenticated = _authenticated_analytics(native_root, request)
    execution_receipt_valid = _validate_execution_receipt(
        request_path, native_root, request, plan
    )

    settings_sha256 = canonical_sha256(
        {
            "feature_policy": request["feature_policy"],
            "runtime_policy": request["runtime_policy"],
            "confornets": request["confornets"],
            "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
        }
    )
    provenance_sha256 = canonical_sha256(
        {
            "request_sha256": request["request_sha256"],
            "settings_sha256": settings_sha256,
            "backend": "confornets",
            "adapter": "conformational_mapping_confornets_phase4_v1",
        }
    )

    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    )
    try:
        copied_native = temporary / "native"
        shutil.copytree(native_root, copied_native)
        sidecar_root = temporary / "canonical_sidecars"
        sidecar_root.mkdir()

        candidates: list[dict[str, Any]] = []
        path_identity: dict[str, tuple[str, dict[str, Any]]] = {}
        related_paths: dict[str, list[str]] = {}
        for coordinate, sample, native_relative in bindings:
            identifier = candidate_id(coordinate)
            structure_relative = f"native/{native_relative}"
            confidence_relative = f"canonical_sidecars/{identifier}.confidence.json"
            evaluation_relative = f"canonical_sidecars/{identifier}.evaluation.json"
            confidence = _analytic_record(
                role="confidence",
                candidate_identifier=identifier,
                coordinate=coordinate,
                sample=sample,
                legacy_request=legacy_request,
                authenticated=authenticated,
            )
            evaluation = _analytic_record(
                role="evaluation",
                candidate_identifier=identifier,
                coordinate=coordinate,
                sample=sample,
                legacy_request=legacy_request,
                authenticated=authenticated,
            )
            _atomic_json(temporary / confidence_relative, confidence)
            _atomic_json(temporary / evaluation_relative, evaluation)
            structure_path = temporary / structure_relative
            candidates.append(
                {
                    "candidate_id": identifier,
                    "backend_coordinates": coordinate,
                    "authoritative_structure_path": structure_relative,
                    "authoritative_structure_sha256": _sha256_file(structure_path),
                    "sidecar_paths": [confidence_relative, evaluation_relative],
                }
            )
            for path in (structure_relative, confidence_relative, evaluation_relative):
                if path in path_identity:
                    raise FinalizationError(f"shared candidate artifact: {path}")
                path_identity[path] = (identifier, coordinate)
            related_paths[structure_relative] = [confidence_relative, evaluation_relative]
            related_paths[confidence_relative] = [structure_relative]
            related_paths[evaluation_relative] = [structure_relative]
        try:
            ensure_candidate_id_uniqueness(candidates)
        except ContractValidationError as exc:
            raise FinalizationError(str(exc)) from exc

        manifest_paths = sorted(
            path.relative_to(temporary).as_posix()
            for path in temporary.rglob("*")
            if path.is_file()
        )
        if len(manifest_paths) != len(set(manifest_paths)):
            raise FinalizationError("native manifest contains shared or duplicate relative paths")
        coordinate_paths = {native_relative for _, _, native_relative in bindings}
        file_records = []
        for relative_path in manifest_paths:
            path = temporary / relative_path
            identity = path_identity.get(relative_path)
            file_records.append(
                {
                    "relative_path": relative_path,
                    "sha256": _sha256_file(path),
                    "bytes": path.stat().st_size,
                    "media_type": _media_type(relative_path),
                    "semantic_role": _semantic_role(relative_path, coordinate_paths),
                    "candidate_id": identity[0] if identity else None,
                    "backend_coordinates": identity[1] if identity else None,
                    "provenance_sha256": provenance_sha256,
                    "related_paths": related_paths.get(relative_path, []),
                }
            )
        native_manifest = {
            "schema_name": "cm_native_artifacts",
            "schema_version": 1,
            "request_id": request["request_id"],
            "backend": "confornets",
            "settings_sha256": settings_sha256,
            "files": file_records,
        }
        try:
            validate_schema("cm_native_artifacts_v1", native_manifest)
        except ContractValidationError as exc:
            raise FinalizationError(str(exc)) from exc
        native_manifest_path = temporary / "cm_native_artifacts_v1.json"
        _atomic_json(native_manifest_path, native_manifest)

        provenance = _load_json(native_root / "provenance.json", label="legacy provenance")
        started_at = (
            provenance.get("started_at")
            if isinstance(provenance, Mapping)
            else None
        ) or "1970-01-01T00:00:00Z"
        completed_at = (
            provenance.get("finished_at")
            if isinstance(provenance, Mapping)
            else None
        ) or "1970-01-01T00:00:00Z"
        identity = request["confornets"]["backend_identity"]
        checkpoint_sha256 = request["confornets"]["checkpoint"]["sha256"]
        identity_known = (
            execution_receipt_valid
            and identity["container_digest"] != f"sha256:{'0' * 64}"
            and checkpoint_sha256 != "0" * 64
            and identity["feature_identity_sha256"] != "0" * 64
        )
        omissions = [] if identity_known else [
            "server-authenticated execution receipt with actual container, source, checkpoint, runtime, request, plan, native request, and output-ledger bindings is absent; resume is disabled"
        ]
        resume_descriptor: dict[str, Any] | None = None
        resume_key = "0" * 64
        if identity_known:
            descriptor_payload = {
                "request_sha256": request["request_sha256"],
                "source_snapshot_sha256": request["source"]["sha256"],
                "complex_snapshot_sha256": request["source"]["sha256"],
                "backend": "confornets",
                "backend_version": identity["backend_version"],
                "backend_commit": identity["backend_commit"],
                "runtime_identity": identity["runtime_identity"],
                "container_digest": identity["container_digest"],
                "model_id": identity["model_id"],
                "checkpoint_sha256": checkpoint_sha256,
                "feature_policy": request["feature_policy"],
                "feature_policy_sha256": canonical_sha256(request["feature_policy"]),
                "ordered_seeds": request["ordered_seeds"],
                "samples_per_seed": request["samples_per_seed"],
                "coordinate_plan": coordinates,
                "expected_candidate_cardinality": len(coordinates),
                "expected_manifest_schema": "cm_ensemble",
                "expected_manifest_version": 1,
                "required_artifact_roles": [
                    "authoritative_cif",
                    "confidence_json",
                    "full_data_json",
                ],
                "expected_manifest_contract_sha256": _sha256_file(
                    REPO_ROOT / "schemas" / "conformational_mapping" / "cm_ensemble_v1.schema.json"
                ),
                "settings_runtime_policy_sha256": canonical_sha256(
                    {
                        "runtime_policy": request["runtime_policy"],
                        "confornets": request["confornets"],
                        "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
                    }
                ),
            }
            try:
                descriptor = ResumeDescriptor.model_validate(descriptor_payload)
            except Exception as exc:
                raise FinalizationError(f"resume descriptor is invalid: {exc}") from exc
            resume_key = descriptor.resume_key
            resume_descriptor = descriptor.model_dump(
                mode="json", exclude={"resume_key"}
            )
        ensemble = {
            "schema_name": "cm_ensemble",
            "schema_version": 1,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "source_snapshot_sha256": request["source"]["sha256"],
            "backend": "confornets",
            "runtime_identity": identity["runtime_identity"],
            "container_digest": identity["container_digest"],
            "checkpoint_sha256": checkpoint_sha256,
            "feature_policy_sha256": canonical_sha256(request["feature_policy"]),
            "expected_cardinality": len(coordinates),
            "expected_coordinates": coordinates,
            "candidates": candidates,
            "native_manifest_path": "cm_native_artifacts_v1.json",
            "native_manifest_sha256": _sha256_file(native_manifest_path),
            "warnings": [],
            "omissions": omissions,
            "terminal_status": "complete" if identity_known else "quarantined",
            "started_at": started_at,
            "completed_at": completed_at,
            "command": [
                "PrepConforNetsRequest",
                "RunConforNets",
                "FinalizeConforNetsOutputs",
                "finalize_confornets_conformational_mapping.py",
            ],
            "resume_key": resume_key,
            "resumable": identity_known,
            "resume_descriptor": resume_descriptor,
        }
        try:
            validate_schema("cm_ensemble_v1", ensemble)
        except ContractValidationError as exc:
            raise FinalizationError(str(exc)) from exc
        _atomic_json(temporary / "cm_ensemble_v1.json", ensemble)
        _copy_file_bytes(
            snapshot_path, temporary / "cm_complex_snapshots_v1.json"
        )
        os.replace(temporary, output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        finalize(args.request, args.native_root, args.out, args.snapshot)
    except (FinalizationError, ContractValidationError, OSError) as exc:
        parser.exit(2, f"ConforNets canonical finalization failed: {exc}\n")
    print(f"Wrote canonical ConforNets manifests to {args.out}")


if __name__ == "__main__":
    main()
