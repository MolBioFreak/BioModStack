"""Canonical bundle inventory construction and no-follow validation."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import stat
import math
import rfc8785
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    AA_ORDER,
    AUTHORITY_ARTIFACT_PATH,
    ContractValidationError,
    canonical_json_bytes,
    canonical_sha256,
    canonical_json_loads,
    request_sha256,
    validate_relative_path,
    validate_schema,
)
from . import runtime as _runtime

CANONICAL_ARTIFACT_PATHS = (
    "workflow_component_request_v1.json",
    "normalized_input.pdb",
    "frustrampnn_structure_map_v1.json",
    "raw_frustrampnn.csv",
    "frustrampnn_landscape_v1.json",
    "frustrampnn_summary_v1.json",
    "frustrampnn_stdout.log",
    "frustrampnn_stderr.log",
    "frustrampnn_execution_receipt_v1.json",
    "workflow_component_result_v1.json",
)
EXTERNAL_CANONICAL_ARTIFACT_PATHS = (
    "workflow_component_request_v1.json",
    AUTHORITY_ARTIFACT_PATH,
    *CANONICAL_ARTIFACT_PATHS[1:],
)
MANIFEST_PATH = "frustrampnn_result_manifest_v1.json"
V2_MANIFEST_PATH = "frustrampnn_result_manifest_v2.json"
V2_MANIFEST_ARTIFACT_PATHS = (
    "workflow_component_request_v2.json",
    "normalized_input.pdb",
    "frustrampnn_structure_map_v1.json",
    "raw_frustrampnn.csv",
    "frustrampnn_landscape_v2.json",
    "frustrampnn_summary_v2.json",
    "frustrampnn_stdout.log",
    "frustrampnn_stderr.log",
    "frustrampnn_execution_receipt_v2.json",
    "frustrampnn_statistics_v1.json",
)
V2_EXTERNAL_MANIFEST_ARTIFACT_PATHS = (
    "workflow_component_request_v2.json",
    AUTHORITY_ARTIFACT_PATH,
    *V2_MANIFEST_ARTIFACT_PATHS[1:],
)
V2_CANONICAL_ARTIFACT_PATHS = (
    *V2_MANIFEST_ARTIFACT_PATHS,
    "workflow_component_result_v2.json",
)
V2_EXTERNAL_CANONICAL_ARTIFACT_PATHS = (
    *V2_EXTERNAL_MANIFEST_ARTIFACT_PATHS,
    "workflow_component_result_v2.json",
)
_V1_SCHEMA_KEYS = {
    "workflow_component_request_v1.json": "workflow_component_request_v1",
    "frustrampnn_structure_map_v1.json": "frustrampnn_structure_map_v1",
    "frustrampnn_landscape_v1.json": "frustrampnn_landscape_v1",
    "frustrampnn_summary_v1.json": "frustrampnn_summary_v1",
    "frustrampnn_execution_receipt_v1.json": "frustrampnn_execution_receipt_v1",
    "workflow_component_result_v1.json": "workflow_component_result_v1",
}
_V2_SCHEMA_KEYS = {
    "workflow_component_request_v2.json": "workflow_component_request_v2",
    "frustrampnn_structure_map_v1.json": "frustrampnn_structure_map_v1",
    "frustrampnn_landscape_v2.json": "frustrampnn_landscape_v2",
    "frustrampnn_summary_v2.json": "frustrampnn_summary_v2",
    "frustrampnn_execution_receipt_v2.json": "frustrampnn_execution_receipt_v2",
    "frustrampnn_statistics_v1.json": "frustrampnn_statistics_v1",
    "workflow_component_result_v2.json": "workflow_component_result_v2",
}
_SCHEMA_KEYS = {**_V1_SCHEMA_KEYS, **_V2_SCHEMA_KEYS}
_MEDIA_TYPES = {
    AUTHORITY_ARTIFACT_PATH: "application/json",
    "workflow_component_request_v1.json": "application/json",
    "normalized_input.pdb": "chemical/x-pdb",
    "frustrampnn_structure_map_v1.json": "application/json",
    "raw_frustrampnn.csv": "text/csv",
    "frustrampnn_landscape_v1.json": "application/json",
    "frustrampnn_summary_v1.json": "application/json",
    "frustrampnn_stdout.log": "text/plain",
    "frustrampnn_stderr.log": "text/plain",
    "frustrampnn_execution_receipt_v1.json": "application/json",
    "workflow_component_result_v1.json": "application/json",
    "workflow_component_request_v2.json": "application/json",
    "frustrampnn_landscape_v2.json": "application/json",
    "frustrampnn_summary_v2.json": "application/json",
    "frustrampnn_execution_receipt_v2.json": "application/json",
    "frustrampnn_statistics_v1.json": "application/json",
    "workflow_component_result_v2.json": "application/json",
}

# FrustraMPNN admits at most 64 MiB source structures. Derived scientific
# artifacts retain that ceiling, runtime logs retain the component's 4 MiB
# closure ceiling, and the fixed-cardinality bundle remains capped in aggregate.
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_AUTHORITY_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_LOG_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 8 * MAX_ARTIFACT_BYTES
MAX_DECLARED_CARDINALITY = MAX_ARTIFACT_BYTES


class ManifestValidationError(ValueError):
    """A result bundle is not exact, immutable, or internally closed."""


def _lexical_path(path: Path | str) -> tuple[int, list[str]]:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise ManifestValidationError(f"unsafe lexical path: {raw!r}")
    absolute = raw.startswith("/")
    body = raw[1:] if absolute else raw
    parts = body.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ManifestValidationError(f"unsafe lexical path component: {raw!r}")
    anchor = "/" if absolute else "."
    return os.open(anchor, os.O_RDONLY | os.O_DIRECTORY), parts


def _open_root(root: Path | str) -> int:
    fd, parts = _lexical_path(root)
    try:
        for part in parts:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
            os.close(fd); fd = next_fd
        return fd
    except OSError as exc:
        os.close(fd)
        raise ManifestValidationError(f"bundle root symlink/invalid component: {root}: {exc}") from exc


def _artifact_limit(relative: str) -> int:
    if relative in {MANIFEST_PATH, V2_MANIFEST_PATH}:
        return MAX_MANIFEST_BYTES
    if relative.endswith(".log"):
        return MAX_RUNTIME_LOG_BYTES
    if relative == AUTHORITY_ARTIFACT_PATH:
        return MAX_AUTHORITY_ARTIFACT_BYTES
    return MAX_ARTIFACT_BYTES


def _file_generation(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_regular(
    root: Path | str | int,
    relative: str,
    *,
    max_bytes: int | None = None,
) -> bytes:
    try:
        validate_relative_path(relative)
    except ContractValidationError as exc:
        raise ManifestValidationError(f"unsafe manifest path: {relative!r}") from exc
    owns_root = not isinstance(root, int)
    root_fd = _open_root(root) if owns_root else root
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(relative, flags, dir_fd=root_fd)
    except OSError as exc:
        if owns_root:
            os.close(root_fd)
        raise ManifestValidationError(
            f"cannot open manifest artifact without following symlinks: {relative}: {exc}"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ManifestValidationError(f"manifest artifact must be regular: {relative}")
        limit = _artifact_limit(relative) if max_bytes is None else max_bytes
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ManifestValidationError(f"artifact read limit is invalid: {relative}")
        if metadata.st_size > limit:
            raise ManifestValidationError(
                f"artifact size exceeds the {limit}-byte read limit: {relative}"
            )
        generation_before = _file_generation(metadata)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            payload = handle.read(limit + 1)
            metadata_after = os.fstat(handle.fileno())
        if len(payload) > limit:
            raise ManifestValidationError(f"artifact grew beyond its read limit: {relative}")
        if (
            len(payload) != metadata.st_size
            or _file_generation(metadata_after) != generation_before
        ):
            raise ManifestValidationError(f"artifact changed size or identity during read: {relative}")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if owns_root:
            os.close(root_fd)


def _root_generation(root_fd: int) -> tuple[int, ...]:
    metadata = os.fstat(root_fd)
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _declared_read_limits(
    manifest: Mapping[str, Any],
    expected_paths: tuple[str, ...],
) -> dict[str, int]:
    records = manifest.get("artifacts")
    if not isinstance(records, list):
        raise ManifestValidationError("manifest artifact declarations are invalid")
    paths = [record.get("relative_path") for record in records if isinstance(record, Mapping)]
    if len(paths) != len(records) or paths != list(expected_paths):
        raise ManifestValidationError("manifest path order/set is not canonical")
    limits: dict[str, int] = {}
    total = 0
    for record in records:
        relative = record["relative_path"]
        declared = record.get("bytes")
        if isinstance(declared, bool) or not isinstance(declared, int) or declared < 0:
            raise ManifestValidationError(f"declared artifact size is invalid: {relative}")
        role_limit = _artifact_limit(relative)
        if declared > role_limit:
            raise ManifestValidationError(
                f"declared artifact size exceeds the {role_limit}-byte role limit: {relative}"
            )
        cardinality = record.get("cardinality")
        if isinstance(cardinality, Mapping):
            count = cardinality.get("count")
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or count > MAX_DECLARED_CARDINALITY
            ):
                raise ManifestValidationError(
                    f"declared artifact cardinality exceeds policy: {relative}"
                )
        total += declared
        if total > MAX_BUNDLE_BYTES:
            raise ManifestValidationError(
                f"declared total bundle size exceeds the {MAX_BUNDLE_BYTES}-byte limit"
            )
        limits[relative] = declared
    return limits


def _enforce_actual_bundle_size(payloads: Mapping[str, bytes]) -> None:
    total = sum(len(payload) for payload in payloads.values())
    if total > MAX_BUNDLE_BYTES:
        raise ManifestValidationError(
            f"actual total bundle size exceeds the {MAX_BUNDLE_BYTES}-byte limit"
        )


def _load_manifest_bytes(
    root: Path | str | int,
    manifest_name: str,
) -> tuple[dict[str, Any], bytes]:
    try:
        payload = _read_regular(root, manifest_name, max_bytes=MAX_MANIFEST_BYTES)
    except ManifestValidationError as exc:
        raise ManifestValidationError(f"manifest bounded read/size limit failed: {exc}") from exc
    try:
        manifest = canonical_json_loads(payload)
    except Exception as exc:
        raise ManifestValidationError(f"physical result manifest is invalid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ManifestValidationError("physical result manifest is not an object")
    if payload != canonical_json_bytes(manifest):
        raise ManifestValidationError("physical result manifest is not canonical bytes")
    expected_version = 2 if manifest_name == V2_MANIFEST_PATH else 1
    if manifest.get("schema_version") != expected_version:
        raise ManifestValidationError("physical result manifest filename/schema generation mismatch")
    return manifest, payload


def _scan_bundle_paths(root_fd: int) -> tuple[set[str], bool]:
    observed: set[str] = set()
    saw_manifest = False
    for entry in os.scandir(root_fd):
        if entry.name == MANIFEST_PATH:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise ManifestValidationError("result manifest path must be regular")
            saw_manifest = True
            continue
        if entry.is_symlink():
            raise ManifestValidationError(f"bundle contains symlink: {entry.name}")
        if not entry.is_file(follow_symlinks=False):
            raise ManifestValidationError(f"bundle contains nonregular entry: {entry.name}")
        observed.add(entry.name)
    return observed, saw_manifest


def _snapshot_bundle(
    root: Path | str,
    *,
    require_manifest: bool,
    expected: set[str] | tuple[set[str], ...],
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, bytes]:
    """Read one exact bundle generation through one retained root descriptor."""

    root_fd = _open_root(root)
    try:
        generation_before = _root_generation(root_fd)
        observed, saw_manifest = _scan_bundle_paths(root_fd)
        if require_manifest and not saw_manifest:
            raise ManifestValidationError("physical result manifest is missing")
        if not require_manifest and saw_manifest:
            raise ManifestValidationError("pre-existing result manifest is forbidden during construction")
        expected_options = (expected,) if isinstance(expected, set) else expected
        if observed not in expected_options:
            expected_union = set().union(*expected_options)
            expected_intersection = set.intersection(*expected_options)
            raise ManifestValidationError(
                f"bundle path set mismatch; missing={sorted(expected_intersection-observed)}, "
                f"unmanifested={sorted(observed-expected_union)}"
            )
        canonical_paths = (
            EXTERNAL_CANONICAL_ARTIFACT_PATHS
            if AUTHORITY_ARTIFACT_PATH in observed
            else CANONICAL_ARTIFACT_PATHS
        )
        payloads: dict[str, bytes] = {}
        if require_manifest:
            physical, physical_bytes = _load_manifest_bytes(root_fd, MANIFEST_PATH)
            if manifest is None or physical != dict(manifest):
                raise ManifestValidationError(
                    "physical result manifest is not exactly equal to supplied manifest"
                )
            try:
                validate_schema("frustrampnn_result_manifest_v1", physical)
            except Exception as exc:
                raise ManifestValidationError(f"manifest schema failed: {exc}") from exc
            limits = _declared_read_limits(physical, canonical_paths)
            payloads[MANIFEST_PATH] = physical_bytes
        else:
            limits = {relative: _artifact_limit(relative) for relative in canonical_paths}
        for relative in canonical_paths:
            payloads[relative] = _read_regular(
                root_fd,
                relative,
                max_bytes=limits[relative],
            )
        _enforce_actual_bundle_size(payloads)
        generation_after = _root_generation(root_fd)
        final_observed, final_saw_manifest = _scan_bundle_paths(root_fd)
        generation_final = _root_generation(root_fd)
        if (
            generation_after != generation_before
            or generation_final != generation_after
            or final_observed != observed
            or final_saw_manifest != saw_manifest
        ):
            raise ManifestValidationError(
                "bundle path set or root directory generation mutated during validation"
            )
        return payloads
    finally:
        os.close(root_fd)


def _json_identity(payload: bytes, relative: str) -> tuple[str, int, Any]:
    try:
        instance = canonical_json_loads(payload)
    except Exception as exc:
        raise ManifestValidationError(f"invalid JSON artifact {relative}: {exc}") from exc
    if not isinstance(instance, dict):
        raise ManifestValidationError(f"JSON artifact is not an object: {relative}")
    expected_payload = (
        rfc8785.dumps(instance)
        if relative == "frustrampnn_statistics_v1.json"
        else canonical_json_bytes(instance)
    )
    if payload != expected_payload:
        raise ManifestValidationError(f"JSON artifact is not canonical bytes: {relative}")
    schema_name = instance.get("schema_name")
    schema_version = instance.get("schema_version")
    if not isinstance(schema_name, str) or not schema_name:
        raise ManifestValidationError(f"JSON artifact has no schema identity: {relative}")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
        raise ManifestValidationError(f"JSON artifact has invalid schema version: {relative}")
    return schema_name, schema_version, instance


def _observed_cardinality(relative: str, payload: bytes, instance: Any | None) -> dict[str, Any] | None:
    if relative == "normalized_input.pdb":
        try:
            residues = {(line[21:22], line[22:26], line[26:27]) for line in payload.decode("ascii").splitlines() if line.startswith("ATOM")}
        except UnicodeDecodeError as exc:
            raise ManifestValidationError("normalized PDB is not ASCII") from exc
        return {"kind": "residues", "count": len(residues)}
    if relative == "raw_frustrampnn.csv":
        try:
            rows = list(csv.reader(io.StringIO(payload.decode("utf-8"), newline="")))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ManifestValidationError("raw CSV cardinality cannot be read") from exc
        if not rows:
            raise ManifestValidationError("raw CSV has no header")
        expected_header = ["frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"]
        if rows[0] != expected_header:
            raise ManifestValidationError("raw CSV header disagrees with physical contract")
        for line_number, row in enumerate(rows[1:], start=2):
            if len(row) != len(expected_header):
                raise ManifestValidationError(
                    f"raw CSV row {line_number} has extra/trailing fields or wrong width"
                )
        return {"kind": "rows", "count": len(rows) - 1}
    if relative == AUTHORITY_ARTIFACT_PATH:
        return {"kind": "records", "count": 1}
    if relative == "frustrampnn_structure_map_v1.json":
        return {"kind": "residues", "count": len(instance["rows"])}
    if relative in {"frustrampnn_landscape_v1.json", "frustrampnn_landscape_v2.json"}:
        return {"kind": "residues", "count": len(instance["residues"])}
    if relative in {
        "frustrampnn_summary_v1.json", "frustrampnn_summary_v2.json",
        "frustrampnn_execution_receipt_v1.json", "frustrampnn_execution_receipt_v2.json",
        "frustrampnn_statistics_v1.json",
        "workflow_component_result_v1.json", "workflow_component_result_v2.json",
    }:
        return {"kind": "records", "count": 1}
    return None


def _record(
    relative: str,
    payload: bytes,
    declared_cardinality: Mapping[str, Any] | None,
    *,
    allow_legacy_external_authority: bool = False,
) -> dict[str, Any]:
    schema_name: str | None = None
    schema_version: int | None = None
    instance: Any | None = None
    schema_key = _SCHEMA_KEYS.get(relative)
    if schema_key is not None:
        schema_name, schema_version, instance = _json_identity(payload, relative)
        expected_name = {
            "workflow_component_request_v1": "workflow_component_request",
            "workflow_component_request_v2": "workflow_component_request",
            "workflow_component_result_v1": "workflow_component_result",
            "workflow_component_result_v2": "workflow_component_result",
            "frustrampnn_structure_map_v1": "frustrampnn_structure_map",
            "frustrampnn_landscape_v1": "frustrampnn_landscape",
            "frustrampnn_landscape_v2": "frustrampnn_landscape",
            "frustrampnn_summary_v1": "frustrampnn_summary",
            "frustrampnn_summary_v2": "frustrampnn_summary",
            "frustrampnn_execution_receipt_v1": "frustrampnn_execution_receipt",
            "frustrampnn_execution_receipt_v2": "frustrampnn_execution_receipt",
            "frustrampnn_statistics_v1": "frustrampnn_statistics",
        }[schema_key]
        expected_version = 2 if schema_key.endswith("_v2") else 1
        if schema_name != expected_name or schema_version != expected_version:
            raise ManifestValidationError(f"schema identity mismatch for {relative}")
        try:
            schema_instance = instance
            if (
                allow_legacy_external_authority
                and schema_key == "workflow_component_request_v2"
                and isinstance(instance, Mapping)
                and instance.get("identity_authority") in {"producer_manifest", "cm_complex_snapshot"}
                and "bytes" not in instance.get("identity_authority_artifact", {})
            ):
                schema_instance = dict(instance)
                envelope = dict(instance["identity_authority_artifact"])
                envelope["bytes"] = len(base64.b64decode(envelope["canonical_json_base64"], validate=True))
                schema_instance["identity_authority_artifact"] = envelope
            validate_schema(schema_key, schema_instance)
        except Exception as exc:
            raise ManifestValidationError(f"schema validation failed for {relative}: {exc}") from exc
    elif relative == AUTHORITY_ARTIFACT_PATH:
        schema_name, schema_version, instance = _json_identity(payload, relative)
        if schema_name != "producer_manifest" or schema_version != 1:
            raise ManifestValidationError("schema identity mismatch for authority artifact")
    observed_cardinality = _observed_cardinality(relative, payload, instance)
    cardinality = dict(declared_cardinality) if declared_cardinality is not None else None
    if cardinality != observed_cardinality:
        raise ManifestValidationError(
            f"cardinality mismatch for {relative}: declared={cardinality}, observed={observed_cardinality}"
        )
    record = {
        "relative_path": relative,
        "schema_name": schema_name,
        "schema_version": schema_version,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "cardinality": cardinality,
    }
    if relative == AUTHORITY_ARTIFACT_PATH:
        record["role"] = "identity_authority"
    return record


def _build_result_manifest_v1(root: Path | str) -> dict[str, Any]:
    """Build a manifest only for an exact, regular canonical artifact set."""

    bundle: Path | str = root
    expected_options: tuple[set[str], ...] = (
        set(CANONICAL_ARTIFACT_PATHS),
        set(EXTERNAL_CANONICAL_ARTIFACT_PATHS),
    )
    payloads = _snapshot_bundle(
        bundle, require_manifest=False, expected=expected_options,
    )
    instances: dict[str, Any] = {}
    for relative in _V1_SCHEMA_KEYS:
        _, _, instances[relative] = _json_identity(payloads[relative], relative)
    request = instances["workflow_component_request_v1.json"]
    try:
        validate_schema("workflow_component_request_v1", request)
    except Exception as exc:
        raise ManifestValidationError(f"request schema validation failed: {exc}") from exc
    external_authority = request["identity_authority"] in {
        "producer_manifest", "cm_complex_snapshot",
    }
    artifact_paths = (
        EXTERNAL_CANONICAL_ARTIFACT_PATHS
        if external_authority
        else CANONICAL_ARTIFACT_PATHS
    )
    if set(payloads) != set(artifact_paths):
        raise ManifestValidationError(
            "external identity authority artifact presence disagrees with request authority"
        )
    cardinalities = {
        "workflow_component_request_v1.json": None,
        AUTHORITY_ARTIFACT_PATH: {"kind": "records", "count": 1},
        "normalized_input.pdb": {
            "kind": "residues",
            "count": sum(
                row["status"] == "mapped"
                for row in instances["frustrampnn_structure_map_v1.json"]["rows"]
            ),
        },
        "frustrampnn_structure_map_v1.json": {"kind": "residues", "count": len(instances["frustrampnn_structure_map_v1.json"]["rows"])},
        "raw_frustrampnn.csv": {"kind": "rows", "count": len(instances["frustrampnn_landscape_v1.json"]["residues"]) * 20},
        "frustrampnn_landscape_v1.json": {"kind": "residues", "count": len(instances["frustrampnn_landscape_v1.json"]["residues"])},
        "frustrampnn_summary_v1.json": {"kind": "records", "count": 1},
        "frustrampnn_stdout.log": None,
        "frustrampnn_stderr.log": None,
        "frustrampnn_execution_receipt_v1.json": {"kind": "records", "count": 1},
        "workflow_component_result_v1.json": {"kind": "records", "count": 1},
    }
    artifacts = [
        _record(
            relative,
            payloads[relative],
            cardinalities[relative],
        )
        for relative in artifact_paths
    ]
    manifest = {
        "schema_name": "frustrampnn_result_manifest",
        "schema_version": 1,
        "invocation_id": request["invocation_id"],
        "parent_job_id": request["parent_job_id"],
        "candidate_id": request["candidate_id"],
        "request_sha256": request_sha256(request),
        "source_sha256": request["source_artifact"]["sha256"],
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    try:
        validate_schema("frustrampnn_result_manifest_v1", manifest)
    except Exception as exc:
        raise ManifestValidationError(f"manifest contract failed: {exc}") from exc
    _validate_closure(payloads, manifest, instances)
    return manifest


def _validate_external_authority(
    payloads: Mapping[str, bytes],
    request: Mapping[str, Any],
    structure: Mapping[str, Any],
    *,
    allow_embedded_only: bool = False,
    allow_legacy_missing_bytes: bool = False,
) -> None:
    """Close canonical producer authority bytes against source and mapped identities."""

    authority_payload = payloads.get(AUTHORITY_ARTIFACT_PATH)
    external = request["identity_authority"] in {
        "producer_manifest", "cm_complex_snapshot",
    }
    embedded_only = external and authority_payload is None and allow_embedded_only
    if external != (authority_payload is not None) and not embedded_only:
        raise ManifestValidationError(
            "external identity authority artifact presence disagrees with request authority"
        )
    if authority_payload is None and not embedded_only:
        return
    envelope = request.get("identity_authority_artifact")
    if not isinstance(envelope, Mapping):
        raise ManifestValidationError("request lacks the external authority artifact envelope")
    try:
        request_bound_payload = base64.b64decode(
            envelope["canonical_json_base64"], validate=True,
        )
    except Exception as exc:
        raise ManifestValidationError("request authority artifact base64 is invalid") from exc
    if request.get("schema_version") == 2:
        declared_bytes = envelope.get("bytes")
        if (
            isinstance(declared_bytes, bool)
            or not isinstance(declared_bytes, int)
            or declared_bytes <= 0
            or declared_bytes != len(request_bound_payload)
        ):
            if not (allow_legacy_missing_bytes and declared_bytes is None):
                raise ManifestValidationError(
                    "request authority artifact byte count does not match canonical bytes"
                )
    if embedded_only:
        authority_payload = request_bound_payload
    assert authority_payload is not None
    schema_name, schema_version, authority = _json_identity(
        authority_payload, AUTHORITY_ARTIFACT_PATH,
    )
    if schema_name != "producer_manifest" or schema_version != 1:
        raise ManifestValidationError("external authority artifact typed schema is invalid")
    authority_digest = hashlib.sha256(authority_payload).hexdigest()
    if (
        envelope.get("relative_path") != AUTHORITY_ARTIFACT_PATH
        or envelope.get("media_type") != "application/json"
        or envelope.get("sha256") != authority_digest
        or request_bound_payload != authority_payload
    ):
        raise ManifestValidationError(
            "physical authority bytes disagree with the request-bound authority artifact"
        )
    base_authority_fields = {
        "schema_name", "schema_version", "source_sha256", "entities",
    }
    if request["identity_authority"] == "cm_complex_snapshot":
        snapshot_digest = authority.get("cm_complex_snapshot_sha256")
        if (
            set(authority) != base_authority_fields | {"cm_complex_snapshot_sha256"}
            or snapshot_digest != envelope.get("cm_complex_snapshot_sha256")
            or not isinstance(snapshot_digest, str)
            or len(snapshot_digest) != 64
            or any(character not in "0123456789abcdef" for character in snapshot_digest)
        ):
            raise ManifestValidationError(
                "CM authority projection snapshot digest binding is invalid"
            )
    elif (
        set(authority) != base_authority_fields
        or "cm_complex_snapshot_sha256" in envelope
    ):
        raise ManifestValidationError("external authority artifact schema fields are not exact")
    if structure["authority_artifact_sha256"] != authority_digest:
        raise ManifestValidationError("external authority artifact digest mismatch")
    source_hash = request["source_artifact"]["sha256"]
    if authority["source_sha256"] != source_hash or structure["source_sha256"] != source_hash:
        raise ManifestValidationError("external authority artifact source binding mismatch")

    raw_entities = authority["entities"]
    if not isinstance(raw_entities, list) or not raw_entities:
        raise ManifestValidationError("external authority artifact has no entities")
    contexts: dict[tuple[str, str, str, str], tuple[str, dict[tuple[int, str], int]]] = {}
    required = {
        "entity_type", "entity_instance_id", "source_entity_id", "label_asym_id",
        "auth_asym_id", "sequence",
    }
    amino_acids = set("ACDEFGHIKLMNPQRSTVWY")
    for raw in raw_entities:
        if (
            not isinstance(raw, Mapping)
            or not required <= set(raw)
            or set(raw) - (required | {"residue_mappings"})
        ):
            raise ManifestValidationError("external authority entity schema is invalid")
        if raw["entity_type"] != "protein":
            continue
        identity = tuple(raw[field] for field in (
            "entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id",
        ))
        if any(not isinstance(value, str) or not value for value in identity):
            raise ManifestValidationError("external authority protein entity identity is incomplete")
        sequence = raw["sequence"]
        if not isinstance(sequence, str) or not sequence or not set(sequence) <= amino_acids:
            raise ManifestValidationError("external authority protein sequence is invalid")
        residue_mappings: dict[tuple[int, str], int] = {}
        used_labels: set[int] = set()
        for mapping in raw.get("residue_mappings", []):
            if not isinstance(mapping, Mapping) or set(mapping) != {
                "auth_seq_id", "insertion_code", "label_seq_id",
            }:
                raise ManifestValidationError("external authority residue mapping schema is invalid")
            auth_seq = mapping["auth_seq_id"]
            insertion = mapping["insertion_code"]
            label_seq = mapping["label_seq_id"]
            if (
                isinstance(auth_seq, bool) or not isinstance(auth_seq, int)
                or not isinstance(insertion, str) or len(insertion) > 1
                or isinstance(label_seq, bool) or not isinstance(label_seq, int)
                or not 1 <= label_seq <= len(sequence)
                or (auth_seq, insertion) in residue_mappings
                or label_seq in used_labels
            ):
                raise ManifestValidationError("external authority residue mapping identity is ambiguous")
            residue_mappings[(auth_seq, insertion)] = label_seq
            used_labels.add(label_seq)
        if identity in contexts:
            raise ManifestValidationError("external authority protein entity identity is duplicated")
        contexts[identity] = (sequence, residue_mappings)
    if not contexts:
        raise ManifestValidationError("external authority authorizes no protein entities")

    rows_by_identity: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    for row in structure["rows"]:
        identity = tuple(row[field] for field in (
            "entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id",
        ))
        rows_by_identity.setdefault(identity, []).append(row)
    if set(rows_by_identity) != set(contexts):
        raise ManifestValidationError("external authority entity identity disagrees with structure map")
    for identity, rows in rows_by_identity.items():
        sequence, residue_mappings = contexts[identity]
        ordered = sorted(rows, key=lambda row: row["sequence_index"])
        if [row["sequence_index"] for row in ordered] != list(range(1, len(sequence) + 1)):
            raise ManifestValidationError("external authority sequence coverage disagrees with structure map")
        for row in ordered:
            expected_wt = sequence[row["sequence_index"] - 1]
            if row["wt"] is not None and row["wt"] != expected_wt:
                raise ManifestValidationError("external authority sequence identity disagrees with structure map")
            mapping_key = (row["auth_seq_id"], row["insertion_code"])
            if (
                residue_mappings
                and row["label_seq_id"] != residue_mappings.get(mapping_key)
            ):
                raise ManifestValidationError("external authority residue mapping disagrees with structure map")


def validate_external_authority_artifact(
    request: Mapping[str, Any],
    structure: Mapping[str, Any],
    authority_payload: bytes | None,
) -> None:
    """Validate one physical external-authority artifact against v2 input authority."""

    payloads = (
        {AUTHORITY_ARTIFACT_PATH: authority_payload}
        if authority_payload is not None
        else {}
    )
    _validate_external_authority(payloads, request, structure)


def _pdb_rows(payload: bytes) -> tuple[list[tuple[str, int, str, str]], dict[tuple[str, int, str], list[str]]]:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise ManifestValidationError("normalized PDB is not ASCII") from exc
    ordered: list[tuple[str, int, str, str]] = []
    atoms: dict[tuple[str, int, str], list[str]] = {}
    serials: set[int] = set()
    for line_number, line in enumerate(lines, start=1):
        if line == "END":
            continue
        if not line.startswith("ATOM  "):
            raise ManifestValidationError(f"normalized PDB line {line_number} is not an ATOM/END record")
        if len(line) != 80:
            raise ManifestValidationError(f"normalized PDB ATOM line {line_number} must be exactly 80 columns")
        try:
            serial = int(line[6:11])
            residue_id = int(line[22:26])
        except ValueError as exc:
            raise ManifestValidationError(f"normalized PDB numeric field is malformed at line {line_number}") from exc
        if serial in serials:
            raise ManifestValidationError(f"normalized PDB duplicate atom serial at line {line_number}")
        serials.add(serial)
        atom_name = line[12:16].strip()
        altloc = line[16]
        residue_name = line[17:20].strip()
        chain = line[21]
        insertion = line[26].strip()
        element = line[76:78].strip().upper()
        if not atom_name or altloc != " ":
            raise ManifestValidationError(f"normalized PDB atom identity/altloc is invalid at line {line_number}")
        expected_element = next((char for char in atom_name if char.isalpha()), "").upper()
        if element != expected_element:
            raise ManifestValidationError(
                f"normalized PDB atom element disagrees with atom name at line {line_number}"
            )
        key = (chain, residue_id, insertion)
        if key not in atoms:
            atoms[key] = []
            ordered.append((chain, residue_id, insertion, residue_name))
        elif ordered[-1][:3] != key:
            raise ManifestValidationError("normalized PDB residue atom records are not contiguous")
        if atom_name in atoms[key]:
            raise ManifestValidationError(f"normalized PDB duplicate atom identity at line {line_number}")
        atoms[key].append(atom_name)
    if not ordered:
        raise ManifestValidationError("normalized PDB has no ATOM records")
    return ordered, atoms


def _validate_physical_pdb(payload: bytes, structure: Mapping[str, Any]) -> None:
    observed, atoms = _pdb_rows(payload)
    mapped_rows = [row for row in structure["rows"] if row["status"] == "mapped"]
    expected = [
        (row["pdb_chain_id"], row["pdb_residue_id"], row["pdb_insertion_code"], row["residue_name"])
        for row in mapped_rows
    ]
    if observed != expected:
        raise ManifestValidationError("normalized PDB residues/order disagree with mapped structure rows")
    positions: dict[str, list[int]] = {}
    for row in mapped_rows:
        positions.setdefault(row["pdb_chain_id"], []).append(row["model_position"])
        key = (row["pdb_chain_id"], row["pdb_residue_id"], row["pdb_insertion_code"])
        missing = {"N", "CA", "C", "O"} - set(atoms[key])
        if missing:
            raise ManifestValidationError(
                f"normalized PDB mapped residue lacks unique backbone atoms: {sorted(missing)}"
            )
        if atoms[key][:4] != ["N", "CA", "C", "O"]:
            raise ManifestValidationError(
                "normalized PDB backbone atom order must be exactly N, CA, C, O"
            )
    for chain, observed_positions in positions.items():
        if observed_positions != list(range(len(observed_positions))):
            raise ManifestValidationError(
                f"structure map model_position must be contiguous zero-based within chain {chain}"
            )


def _argv_value(argv: list[str], flag: str) -> str:
    indexes = [index for index, token in enumerate(argv) if token == flag]
    if len(indexes) != 1 or indexes[0] + 1 >= len(argv):
        raise ManifestValidationError(f"receipt argv must contain exactly one {flag} value")
    return argv[indexes[0] + 1]


def _argv_artifact(value: str, expected_name: str) -> None:
    parts = value.split("/")
    body = parts[1:] if value.startswith("/") else parts
    if not body or any(part in {"", ".", ".."} for part in body) or body[-1] != expected_name:
        raise ManifestValidationError(f"receipt argv artifact must name {expected_name} lexically")


def _validate_receipt_argv(receipt: Mapping[str, Any]) -> None:
    argv = receipt["argv"]
    if len(argv) != 24:
        raise ManifestValidationError("receipt launcher argv has an unexpected token count")
    launcher = argv[0]
    if not launcher or launcher.split("/")[-1] != "apptainer":
        raise ManifestValidationError("receipt launcher executable must be Apptainer")
    sif_path = receipt["sif_path"]
    if (
        not sif_path.startswith("/proc/self/fd/")
        or not sif_path.removeprefix("/proc/self/fd/").isdigit()
    ):
        raise ManifestValidationError("receipt SIF path is not a pinned proc-fd path")
    expected = [
        launcher,
        "exec", "--containall", "--writable-tmpfs", "--nv",
        "--env", "CUDA_DEVICE_ORDER=PCI_BUS_ID",
        "--env", f"CUDA_VISIBLE_DEVICES={receipt['assigned_physical_gpu_id']}",
        "--bind", receipt["bind_policy"][0] if len(receipt["bind_policy"]) == 2 else "",
        "--bind", receipt["bind_policy"][1] if len(receipt["bind_policy"]) == 2 else "",
        receipt["sif_path"], receipt["executable_path"], "predict",
        "--pdb", "/bms/input/normalized.pdb",
        "--checkpoint", receipt["checkpoint_path"],
        "--output", "/bms/output/raw_frustrampnn.csv",
        "--device", "cuda",
    ]
    if argv != expected:
        raise ManifestValidationError("receipt argv does not match the exact hardened launcher grammar")

    def bind_parts(value: str, *, container_path: str, mode: str) -> str:
        parts = value.split(":")
        if len(parts) != 3 or parts[1:] != [container_path, mode]:
            raise ManifestValidationError("receipt bind policy is not exact")
        host = parts[0]
        body = host[1:].split("/") if host.startswith("/") else []
        if not body or any(part in {"", ".", ".."} for part in body) or "\\" in host:
            raise ManifestValidationError("receipt bind host path is lexically unsafe")
        return host

    normalized_host = bind_parts(
        receipt["bind_policy"][0], container_path="/bms/input/normalized.pdb", mode="ro",
    )
    output_host = bind_parts(
        receipt["bind_policy"][1], container_path="/bms/output", mode="rw",
    )
    if normalized_host == output_host or normalized_host.startswith(output_host.rstrip("/") + "/"):
        raise ManifestValidationError("receipt read-only input collides with writable output bind")

    identity = _runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
    if receipt["working_directory_policy"] != "apptainer_containall_v1":
        raise ManifestValidationError("receipt working-directory policy is not canonical")
    if receipt["task_visible_device_index"] != 0:
        raise ManifestValidationError("receipt task-visible GPU index must be zero")
    if (
        receipt["stdout_artifact"] != "frustrampnn_stdout.log"
        or receipt["stderr_artifact"] != "frustrampnn_stderr.log"
    ):
        raise ManifestValidationError("receipt stdout/stderr artifact references are not canonical")
    if (
        receipt["sif_sha256"] != identity.sif_sha256
        or receipt["configured_sif_path"] != identity.configured_sif_path
        or receipt["executable_path"] != identity.executable_path
        or receipt["executable_sha256"] != identity.executable_sha256
        or receipt["checkpoint_path"] != identity.checkpoint_path
        or receipt["checkpoint_id"] != identity.checkpoint_id
        or receipt["checkpoint_sha256"] != identity.checkpoint_sha256
        or receipt["software_versions"] != {
            "frustrampnn": identity.package_version,
            "adapter": "run_frustrampnn_component_v1",
            "normalizer": "frustrampnn_structure_normalizer_v1",
            "finalizer": "frustrampnn_landscape_finalizer_v1",
            "source_commit": identity.source_commit,
            "python": identity.python_version,
            "pytorch": identity.pytorch_version,
            "image": identity.image_version,
        }
    ):
        raise ManifestValidationError("receipt runtime/checkpoint identity disagrees with central registry")


def _validate_closure(payloads: Mapping[str, bytes], manifest: Mapping[str, Any], instances: Mapping[str, Any] | None = None) -> None:
    values = dict(instances or {})
    for relative in _V1_SCHEMA_KEYS:
        if relative not in values:
            _, _, values[relative] = _json_identity(payloads[relative], relative)
    request = values["workflow_component_request_v1.json"]
    structure = values["frustrampnn_structure_map_v1.json"]
    landscape = values["frustrampnn_landscape_v1.json"]
    summary = values["frustrampnn_summary_v1.json"]
    receipt = values["frustrampnn_execution_receipt_v1.json"]
    result = values["workflow_component_result_v1.json"]
    _validate_physical_pdb(payloads["normalized_input.pdb"], structure)
    identity = (request["invocation_id"], request["parent_job_id"], request["candidate_id"])
    if (manifest["invocation_id"], manifest["parent_job_id"], manifest["candidate_id"]) != identity:
        raise ManifestValidationError("manifest identity does not match request")
    if manifest["request_sha256"] != request_sha256(request):
        raise ManifestValidationError("manifest request hash mismatch")
    if manifest["source_sha256"] != request["source_artifact"]["sha256"]:
        raise ManifestValidationError("manifest source hash mismatch")
    authority_map = {
        "pdb_coordinates": "pdb_self_identity_v1",
        "mmcif_atom_site": "mmcif_atom_site_v1",
        "producer_manifest": "producer_manifest_v1",
        # CM publishes a neutral producer-manifest projection into the bundle.
        "cm_complex_snapshot": "producer_manifest_v1",
    }
    expected_authority = authority_map[request["identity_authority"]]
    if structure["identity_authority"] != expected_authority:
        raise ManifestValidationError("request identity authority disagrees with structure map")
    _validate_external_authority(payloads, request, structure)
    expected_media = {
        "pdb": {"chemical/x-pdb", "application/pdb"},
        "mmcif": {"chemical/x-mmcif", "application/mmcif"},
    }[structure["source_format"]]
    if request["source_artifact"]["media_type"] not in expected_media:
        raise ManifestValidationError("request source media type disagrees with structure format")
    parameters = request["parameters"]
    if parameters["selected_model_number"] != structure["selected_source_model"]:
        raise ManifestValidationError("request selected model disagrees with structure map")
    if parameters["altloc_policy"] != structure["altloc_policy"]:
        raise ManifestValidationError("request altloc policy disagrees with structure map")
    if parameters["threshold_policy_id"] != landscape["threshold_policy"]["id"]:
        raise ManifestValidationError("request threshold policy disagrees with landscape")
    if landscape["model_ready_sequence_sha256"] != structure["model_ready_sequence_sha256"]:
        raise ManifestValidationError("landscape model-ready sequence hash disagrees with structure map")
    for obj, name in ((structure, "structure map"), (landscape, "landscape"), (summary, "summary")):
        if (obj["parent_job_id"], obj["candidate_id"]) != identity[1:]:
            raise ManifestValidationError(f"{name} identity mismatch")
    # Target is representable in produced artifacts and must agree everywhere it appears.
    targets = {obj["target_id"] for obj in (structure, landscape, summary)}
    if len(targets) != 1:
        raise ManifestValidationError("target identity mismatch across artifacts")
    pdb = payloads["normalized_input.pdb"]
    raw = payloads["raw_frustrampnn.csv"]
    if structure["source_sha256"] != manifest["source_sha256"] or structure["normalized_pdb_sha256"] != hashlib.sha256(pdb).hexdigest():
        raise ManifestValidationError("structure map source or normalized PDB hash mismatch")
    if landscape["structure_map_sha256"] != canonical_sha256(structure) or landscape["normalized_pdb_sha256"] != structure["normalized_pdb_sha256"] or landscape["raw_csv_sha256"] != hashlib.sha256(raw).hexdigest():
        raise ManifestValidationError("landscape structure, normalized PDB, or raw hash mismatch")
    if summary["landscape_sha256"] != canonical_sha256(landscape):
        raise ManifestValidationError("summary landscape hash mismatch")
    _validate_physical_semantics(pdb, raw, structure, landscape, summary)
    if receipt["invocation_id"] != identity[0] or receipt["input_sha256"] != manifest["source_sha256"] or receipt["normalized_pdb_sha256"] != hashlib.sha256(pdb).hexdigest() or receipt["raw_csv_sha256"] != hashlib.sha256(raw).hexdigest() or receipt["landscape_sha256"] != canonical_sha256(landscape) or receipt["summary_sha256"] != canonical_sha256(summary):
        raise ManifestValidationError("receipt artifact identity/hash mismatch")
    if result["request_sha256"] != manifest["request_sha256"] or (result["invocation_id"], result["parent_job_id"], result["candidate_id"]) != identity or result["source_artifact"] != request["source_artifact"]:
        raise ManifestValidationError("result request/source identity mismatch")
    if result["runtime_identity"] != {k: receipt[k] for k in ("sif_sha256", "executable_sha256", "checkpoint_id", "checkpoint_sha256")} or result["assigned_gpu"] != {"physical_device_id": receipt["assigned_physical_gpu_id"], "task_visible_device_index": receipt["task_visible_device_index"]}:
        raise ManifestValidationError("result runtime/GPU mismatch with receipt")
    if result["parent_workflow_id"] != request["parent_workflow_id"] or receipt["checkpoint_id"] != parameters["checkpoint_id"]:
        raise ManifestValidationError("workflow/checkpoint closure mismatch")
    _validate_receipt_argv(receipt)
    selection = request["protein_selection"]
    if selection["mode"] == "explicit":
        selected = {(item["entity_instance_id"], item["source_entity_id"], item["label_asym_id"], item["auth_asym_id"]): item["sequence"] for item in selection["entities"]}
        mapped_by_entity: dict[tuple[Any, ...], list[Mapping[str, Any]]] = {}
        for row in structure["rows"]:
            key = (row["entity_instance_id"], row["source_entity_id"], row["label_asym_id"], row["auth_asym_id"])
            mapped_by_entity.setdefault(key, []).append(row)
        if set(mapped_by_entity) != set(selected):
            raise ManifestValidationError("request selection closure mismatch")
        for key, rows in mapped_by_entity.items():
            sequence = selected[key]
            ordered = sorted(rows, key=lambda row: row["sequence_index"])
            if [row["sequence_index"] for row in ordered] != list(range(1, len(sequence) + 1)):
                raise ManifestValidationError("request selection sequence coverage mismatch")
            if any(
                row["wt"] is not None and row["wt"] != sequence[row["sequence_index"] - 1]
                for row in ordered
            ):
                raise ManifestValidationError("request selection sequence identity mismatch")
    if result["status"] != "succeeded" or receipt["exit_code"] != 0:
        raise ManifestValidationError("success result requires receipt exit=0 and succeeded status")
    if (result["started_at"], result["ended_at"], result["duration_seconds"]) != (receipt["started_at"], receipt["ended_at"], receipt["duration_seconds"]):
        raise ManifestValidationError("receipt/result timestamps or duration mismatch")
    try:
        started = datetime.fromisoformat(receipt["started_at"].replace("Z", "+00:00"))
        ended = datetime.fromisoformat(receipt["ended_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError("receipt timestamps are invalid") from exc
    if ended < started or not math.isclose((ended - started).total_seconds(), receipt["duration_seconds"], rel_tol=0, abs_tol=1e-9):
        raise ManifestValidationError("receipt timestamps/duration are not chronological or exact")
    by_path = {record["relative_path"]: record for record in manifest["artifacts"]}
    for record in result["artifacts"]:
        observed = by_path.get(record["relative_path"])
        if observed is None or record != {key: observed[key] for key in record}:
            raise ManifestValidationError("result artifact inventory mismatch")


def _validate_physical_semantics(pdb: bytes, raw: bytes, structure: Mapping[str, Any], landscape: Mapping[str, Any], summary: Mapping[str, Any]) -> None:
    """Reconstruct semantic links: hashes alone cannot authenticate rehashed lies."""
    try:
        lines = pdb.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise ManifestValidationError("PDB physical bytes are not ASCII") from exc
    residues: list[tuple[str, int, str, str]] = []
    seen: set[tuple[str, int, str]] = set()
    for line in lines:
        if not line.startswith("ATOM"):
            continue
        if len(line) < 27:
            raise ManifestValidationError("PDB physical record is truncated")
        try: key = (line[21], int(line[22:26]), line[26].strip())
        except ValueError as exc: raise ManifestValidationError("PDB physical residue identity is invalid") from exc
        if key not in seen:
            seen.add(key); residues.append((*key, line[17:20].strip().upper()))
    mapped = [row for row in structure["rows"] if row["status"] == "mapped"]
    expected_pdb = [(row["pdb_chain_id"], row["pdb_residue_id"], row["pdb_insertion_code"], row["residue_name"]) for row in mapped]
    if residues != expected_pdb:
        raise ManifestValidationError("PDB physical identity/resname/order disagrees with structure map")
    map_identity = ("entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id", "label_seq_id", "auth_seq_id", "insertion_code", "sequence_index", "pdb_chain_id", "pdb_residue_id", "pdb_insertion_code", "model_position", "residue_name", "wt")
    if len(landscape["residues"]) != len(mapped) or any(any(residue[field] != row[field] for field in map_identity) for residue, row in zip(landscape["residues"], mapped, strict=True)):
        raise ManifestValidationError("landscape residue identity disagrees with mapped structure map")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8"), newline=""))
        fields = ["frustration_pred", "position", "wildtype", "mutation", "chain", "pdb"]
        if reader.fieldnames != fields: raise ManifestValidationError("raw physical header is invalid")
        raw_rows = list(reader)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ManifestValidationError("raw physical CSV is invalid") from exc
    slots = {(residue["pdb_chain_id"], residue["model_position"], slot["mutation_aa"]): (residue["wt"], slot["score"]) for residue in landscape["residues"] for slot in residue["slots"]}
    if len(raw_rows) != len(slots): raise ManifestValidationError("raw physical row cardinality is invalid")
    observed: set[tuple[str, int, str]] = set()
    for raw_row in raw_rows:
        try: key = (raw_row["chain"].strip(), int(raw_row["position"]), raw_row["mutation"].strip().upper()); score = float(raw_row["frustration_pred"])
        except (ValueError, TypeError) as exc: raise ManifestValidationError("raw physical tuple/score is invalid") from exc
        if not raw_row["pdb"].strip() or not math.isfinite(score) or key in observed or key not in slots or raw_row["wildtype"].strip().upper() != slots[key][0] or score != slots[key][1]:
            raise ManifestValidationError("raw physical tuple/wt/score disagrees with landscape")
        observed.add(key)
    from .analysis import summarize_landscape
    if summary != summarize_landscape(landscape, structure):
        raise ManifestValidationError("summary content does not exactly recompute from landscape")


def _validate_result_manifest_v1(
    root: Path | str, manifest: Mapping[str, Any],
) -> dict[str, bytes]:
    """Rehash and close every manifest field against no-follow filesystem reads."""

    try:
        validate_schema("frustrampnn_result_manifest_v1", manifest)
    except Exception as exc:
        raise ManifestValidationError(f"manifest schema failed: {exc}") from exc
    bundle: Path | str = root
    expected_options: tuple[set[str], ...] = (
        set(CANONICAL_ARTIFACT_PATHS),
        set(EXTERNAL_CANONICAL_ARTIFACT_PATHS),
    )
    payloads = _snapshot_bundle(
        bundle,
        require_manifest=True,
        expected=expected_options,
        manifest=manifest,
    )
    physical_bytes = payloads[MANIFEST_PATH]
    try:
        physical = canonical_json_loads(physical_bytes)
    except Exception as exc:
        raise ManifestValidationError(f"physical result manifest is invalid JSON: {exc}") from exc
    if physical_bytes != canonical_json_bytes(physical):
        raise ManifestValidationError("physical result manifest is not canonical bytes")
    if not isinstance(physical, dict) or physical != dict(manifest):
        raise ManifestValidationError(
            "physical result manifest is not exactly equal to supplied manifest"
        )
    records = manifest["artifacts"]
    paths = [record["relative_path"] for record in records]
    expected_paths = (
        EXTERNAL_CANONICAL_ARTIFACT_PATHS
        if AUTHORITY_ARTIFACT_PATH in payloads
        else CANONICAL_ARTIFACT_PATHS
    )
    if paths != list(expected_paths):
        raise ManifestValidationError("manifest path order/set is not canonical")
    for declared in records:
        observed_record = _record(
            declared["relative_path"],
            payloads[declared["relative_path"]],
            declared["cardinality"],
        )
        if dict(declared) != observed_record:
            raise ManifestValidationError(
                f"hash/size/schema/cardinality mismatch for {declared['relative_path']}"
            )
    _validate_closure(payloads, manifest)
    return dict(payloads)


def _snapshot_v2(
    root: Path | str,
    *,
    require_manifest: bool,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, bytes]:
    root_fd = _open_root(root)
    try:
        generation_before = _root_generation(root_fd)
        observed: set[str] = set()
        saw_manifest = False
        for entry in os.scandir(root_fd):
            if entry.name == V2_MANIFEST_PATH:
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    raise ManifestValidationError("v2 result manifest path must be regular")
                saw_manifest = True
                continue
            if entry.is_symlink():
                raise ManifestValidationError(f"bundle contains symlink: {entry.name}")
            if not entry.is_file(follow_symlinks=False):
                raise ManifestValidationError(f"bundle contains nonregular entry: {entry.name}")
            observed.add(entry.name)
        path_options = (
            (V2_CANONICAL_ARTIFACT_PATHS, V2_EXTERNAL_CANONICAL_ARTIFACT_PATHS)
            if require_manifest
            else (V2_MANIFEST_ARTIFACT_PATHS, V2_EXTERNAL_MANIFEST_ARTIFACT_PATHS)
        )
        matching_paths = [paths for paths in path_options if observed == set(paths)]
        if saw_manifest != require_manifest or len(matching_paths) != 1:
            expected_sets = [set(paths) for paths in path_options]
            raise ManifestValidationError(
                "v2 bundle generation/path set mismatch; "
                f"observed={sorted(observed)}, expected_one_of="
                f"{[sorted(expected) for expected in expected_sets]}"
            )
        paths = matching_paths[0]
        manifest_artifact_paths = paths[:-1] if require_manifest else paths
        payloads: dict[str, bytes] = {}
        if require_manifest:
            physical, physical_bytes = _load_manifest_bytes(root_fd, V2_MANIFEST_PATH)
            if manifest is None or physical != dict(manifest):
                raise ManifestValidationError(
                    "physical v2 result manifest is not exact canonical supplied bytes"
                )
            try:
                validate_schema("frustrampnn_result_manifest_v2", physical)
            except Exception as exc:
                raise ManifestValidationError(f"v2 manifest schema failed: {exc}") from exc
            limits = _declared_read_limits(physical, manifest_artifact_paths)
            limits["workflow_component_result_v2.json"] = _artifact_limit(
                "workflow_component_result_v2.json"
            )
            payloads[V2_MANIFEST_PATH] = physical_bytes
        else:
            limits = {relative: _artifact_limit(relative) for relative in paths}
        for relative in paths:
            payloads[relative] = _read_regular(
                root_fd,
                relative,
                max_bytes=limits[relative],
            )
        _enforce_actual_bundle_size(payloads)
        generation_after = _root_generation(root_fd)
        final_names = {entry.name for entry in os.scandir(root_fd)}
        expected_names = set(paths) | ({V2_MANIFEST_PATH} if require_manifest else set())
        if generation_after != generation_before or final_names != expected_names:
            raise ManifestValidationError(
                "v2 bundle path set or root directory generation mutated during validation"
            )
        return payloads
    finally:
        os.close(root_fd)


def validate_v2_input_closure(
    request: Mapping[str, Any],
    normalized_pdb: bytes,
    structure_map_payload: bytes,
    *,
    allow_legacy_external_authority: bool = False,
):
    """Validate the exact three-file v2 execution authority before runtime access."""

    from .configuration import FrustraMPNNExecutionConfigurationV2
    from .settings import (
        FrustraMPNNEffectiveSettings,
        FrustraMPNNRequestedSettings,
        resolve_effective_settings,
    )

    try:
        schema_request = request
        if (
            allow_legacy_external_authority
            and request.get("identity_authority") in {"producer_manifest", "cm_complex_snapshot"}
            and "bytes" not in request.get("identity_authority_artifact", {})
        ):
            schema_request = dict(request)
            envelope = dict(request["identity_authority_artifact"])
            envelope["bytes"] = len(base64.b64decode(envelope["canonical_json_base64"], validate=True))
            schema_request["identity_authority_artifact"] = envelope
        validate_schema("workflow_component_request_v2", schema_request)
        structure = canonical_json_loads(structure_map_payload)
        if not isinstance(structure, dict):
            raise ManifestValidationError("v2 structure map is not an object")
        if canonical_json_bytes(structure) != structure_map_payload:
            raise ManifestValidationError("v2 structure map bytes are not canonical JSON")
        validate_schema("frustrampnn_structure_map_v1", structure)
        effective = FrustraMPNNEffectiveSettings.model_validate(request["effective_settings"])
        requested = FrustraMPNNRequestedSettings.model_validate(request["requested_settings"])
        configuration = FrustraMPNNExecutionConfigurationV2.model_validate(
            request["execution_configuration"]
        )
    except ManifestValidationError:
        raise
    except Exception as exc:
        raise ManifestValidationError(f"v2 request/map contract is invalid: {exc}") from exc

    normalized_sha256 = hashlib.sha256(normalized_pdb).hexdigest()
    structure_sha256 = hashlib.sha256(structure_map_payload).hexdigest()
    if normalized_sha256 != request["normalized_pdb_sha256"]:
        raise ManifestValidationError("physical normalized PDB hash disagrees with v2 request")
    if structure_sha256 != request["structure_map_sha256"]:
        raise ManifestValidationError("physical structure-map hash disagrees with v2 request")
    resolution = effective.resolution_identity
    producer_provenance = request.get("producer_provenance")
    source_binding = (
        producer_provenance.get("source_to_normalized_binding")
        if isinstance(producer_provenance, Mapping)
        else None
    )
    source_hashes_closed = (
        structure["source_sha256"] == resolution.source_artifact_sha256
    )
    if isinstance(source_binding, Mapping):
        source_hashes_closed = source_hashes_closed and (
            source_binding.get("source_sha256") == structure["source_sha256"]
            and source_binding.get("normalized_pdb_sha256") == normalized_sha256
            and request["source_artifact"]["sha256"]
            in {structure["source_sha256"], normalized_sha256}
        )
    else:
        source_hashes_closed = source_hashes_closed and (
            request["source_artifact"]["sha256"] == structure["source_sha256"]
        )
    if (
        normalized_sha256 != structure["normalized_pdb_sha256"]
        or normalized_sha256 != resolution.normalized_pdb_sha256
        or structure_sha256 != resolution.structure_map_sha256
        or not source_hashes_closed
    ):
        raise ManifestValidationError("v2 source/map/normalized resolution hashes are not closed")
    if (
        structure["parent_job_id"] != request["parent_job_id"]
        or structure["candidate_id"] != request["candidate_id"]
    ):
        raise ManifestValidationError("v2 structure-map candidate/parent binding is stale")
    source_settings = requested.source_structure
    expected_altloc = source_settings.preferred_altloc or "<blank>"
    if (
        structure["selected_source_model"] != source_settings.selected_model_number
        or structure["altloc_policy"] != f"blank_or_explicit:{expected_altloc}"
    ):
        raise ManifestValidationError("v2 structure-map model/altloc binding is stale")
    authority = {
        "pdb_coordinates": "pdb_self_identity_v1",
        "mmcif_atom_site": "mmcif_atom_site_v1",
        "producer_manifest": "producer_manifest_v1",
        "cm_complex_snapshot": "producer_manifest_v1",
    }[request["identity_authority"]]
    if structure["identity_authority"] != authority:
        raise ManifestValidationError("v2 structure-map identity authority binding is stale")

    try:
        resolved_again = resolve_effective_settings(requested, structure)
    except Exception as exc:
        raise ManifestValidationError(f"v2 effective residue resolution is invalid: {exc}") from exc
    if resolved_again != effective:
        raise ManifestValidationError("v2 effective settings are stale relative to the exact map")

    source_fields = (
        "entity_instance_id", "source_entity_id", "label_asym_id", "auth_asym_id",
        "auth_seq_id", "insertion_code", "sequence_index", "wt",
        "pdb_chain_id", "model_position",
    )
    rows = structure["rows"]
    matched_indexes: set[int] = set()
    for chain in effective.resolved_chains:
        for residue in chain.residues:
            expected = residue.model_dump(mode="json", exclude_none=False)
            matches = [
                index for index, row in enumerate(rows)
                if all(row[field] == expected[field] for field in source_fields)
            ]
            if len(matches) != 1 or rows[matches[0]]["status"] != "mapped":
                raise ManifestValidationError(
                    "v2 effective residue does not match exactly one scoreable source/normalized map row"
                )
            if matches[0] in matched_indexes:
                raise ManifestValidationError("v2 effective residue map match is duplicated")
            matched_indexes.add(matches[0])
    _validate_physical_pdb(normalized_pdb, structure)
    return structure, effective, configuration


def summarize_landscape_v2(landscape: Mapping[str, Any], effective: Any) -> dict[str, Any]:
    """Derive the exact complete selected-residue v2 summary."""

    residues = list(landscape["residues"])
    all_slots = [slot for residue in residues for slot in residue["slots"]]
    native = [slot for slot in all_slots if slot["native"]]
    classes = ("high", "neutral", "minimal")
    native_counts = {name: sum(slot["class"] == name for slot in native) for name in classes}
    complete_counts = {name: sum(slot["class"] == name for slot in all_slots) for name in classes}
    support = []
    for chain in effective.resolved_chains:
        count = len(chain.residues)
        support.append({
            "entity_instance_id": chain.entity.entity_instance_id,
            "auth_asym_id": chain.entity.auth_asym_id,
            "expected_residues": count,
            "mapped_residues": count,
            "scoreable_residues": count,
            "expected_slots": count * len(AA_ORDER),
            "observed_slots": count * len(AA_ORDER),
            "scoreable_slots": count * len(AA_ORDER),
        })
    count = len(residues)
    summary = {
        "schema_name": "frustrampnn_summary",
        "schema_version": 2,
        **{
            key: landscape[key]
            for key in (
                "execution_configuration_id", "execution_configuration_sha256",
                "requested_settings_sha256", "effective_settings_sha256",
                "runtime_identity_sha256", "target_id", "parent_job_id", "candidate_id",
                "source_artifact_sha256", "structure_map_sha256", "normalized_pdb_sha256",
                "threshold_policy_id", "threshold_policy", "threshold_policy_sha256",
            )
        },
        "landscape_sha256": canonical_sha256(dict(landscape)),
        "residue_support": {
            "expected": count, "mapped": count, "scoreable": count,
            "excluded": 0, "ambiguous": 0,
        },
        "slot_support": {
            "expected": count * len(AA_ORDER),
            "observed": count * len(AA_ORDER),
            "scoreable": count * len(AA_ORDER),
        },
        "missingness_by_reason": {},
        "native_slot_counts": native_counts,
        "native_slot_fractions": {
            name: native_counts[name] / count for name in classes
        },
        "complete_landscape_counts": complete_counts,
        "complete_landscape_fractions": {
            name: complete_counts[name] / (count * len(AA_ORDER)) for name in classes
        },
        "support_by_entity_chain": support,
    }
    try:
        validate_schema("frustrampnn_summary_v2", summary)
    except Exception as exc:
        raise ManifestValidationError(f"v2 summary contract failed: {exc}") from exc
    return summary


def _validate_receipt_argv_v2(receipt: Mapping[str, Any], configuration: Any) -> None:
    runtime = configuration.runtime
    for command in receipt["commands"]:
        argv = command["argv"]
        tail: list[str] = []
        if command["chains"] is not None:
            tail.extend(["--chains", ",".join(command["chains"])])
        if command["positions"] is not None:
            tail.extend(["--positions", ",".join(map(str, command["positions"]))])
        base_length = 24
        if len(argv) != base_length + len(tail):
            raise ManifestValidationError("v2 receipt launcher argv has an unexpected token count")
        launcher = argv[0]
        if not launcher or launcher.split("/")[-1] != "apptainer":
            raise ManifestValidationError("v2 receipt launcher executable must be Apptainer")
        sif_path = argv[13]
        if not sif_path.startswith("/proc/self/fd/") or not sif_path[14:].isdigit():
            raise ManifestValidationError("v2 receipt SIF path is not descriptor pinned")
        binds = [argv[index + 1] for index, token in enumerate(argv) if token == "--bind"]
        if len(binds) != 2:
            raise ManifestValidationError("v2 receipt bind policy is not exact")
        expected = [
            launcher, "exec", "--containall", "--writable-tmpfs", "--nv",
            "--env", "CUDA_DEVICE_ORDER=PCI_BUS_ID",
            "--env", f"CUDA_VISIBLE_DEVICES={receipt['assigned_physical_gpu_id']}",
            "--bind", binds[0], "--bind", binds[1], sif_path,
            runtime.executable_path, "predict", "--pdb", "/bms/input/normalized.pdb",
            "--checkpoint", runtime.checkpoint_path,
            "--output", f"/bms/output/{command['shard_relative_path']}",
            "--device", "cuda", *tail,
        ]
        if argv != expected:
            raise ManifestValidationError("v2 receipt argv is outside the exact hardened grammar")

        def bind_host(value: str, *, container_path: str, mode: str) -> str:
            parts = value.split(":")
            if len(parts) != 3 or parts[1:] != [container_path, mode]:
                raise ManifestValidationError("v2 receipt bind policy is not exact")
            host = parts[0]
            host_parts = host[1:].split("/") if host.startswith("/") else []
            if (
                not host_parts
                or any(part in {"", ".", ".."} for part in host_parts)
                or "\\" in host
                or "\x00" in host
            ):
                raise ManifestValidationError("v2 receipt bind host path is lexically unsafe")
            return host

        normalized_host = bind_host(
            binds[0], container_path="/bms/input/normalized.pdb", mode="ro"
        )
        output_host = bind_host(binds[1], container_path="/bms/output", mode="rw")
        if normalized_host == output_host or normalized_host.startswith(
            output_host.rstrip("/") + "/"
        ):
            raise ManifestValidationError(
                "v2 receipt read-only input collides with writable output bind"
            )


def _validate_v2_closure(
    payloads: Mapping[str, bytes], manifest: Mapping[str, Any], *, require_result: bool,
    allow_legacy_external_authority: bool = False,
) -> None:
    from .analysis import finalize_landscape_v2
    from .analytics import build_statistics_receipt, validate_statistics_receipt
    from .runtime import compile_frustrampnn_command_plan
    from . import settings as _settings

    values: dict[str, Any] = {}
    schema_paths = dict(_V2_SCHEMA_KEYS)
    if not require_result:
        schema_paths.pop("workflow_component_result_v2.json")
    for relative in schema_paths:
        _, _, values[relative] = _json_identity(payloads[relative], relative)
        try:
            schema_instance = values[relative]
            if (
                allow_legacy_external_authority
                and relative == "workflow_component_request_v2.json"
                and isinstance(schema_instance, Mapping)
                and schema_instance.get("identity_authority") in {"producer_manifest", "cm_complex_snapshot"}
                and "bytes" not in schema_instance.get("identity_authority_artifact", {})
            ):
                schema_instance = dict(schema_instance)
                envelope = dict(schema_instance["identity_authority_artifact"])
                envelope["bytes"] = len(base64.b64decode(envelope["canonical_json_base64"], validate=True))
                schema_instance["identity_authority_artifact"] = envelope
            validate_schema(_V2_SCHEMA_KEYS[relative], schema_instance)
        except Exception as exc:
            raise ManifestValidationError(f"v2 schema validation failed for {relative}: {exc}") from exc
    request = values["workflow_component_request_v2.json"]
    structure, effective, configuration = validate_v2_input_closure(
        request,
        payloads["normalized_input.pdb"],
        payloads["frustrampnn_structure_map_v1.json"],
        allow_legacy_external_authority=allow_legacy_external_authority,
    )
    authority_envelope = request.get("identity_authority_artifact")
    legacy_missing_bytes = (
        allow_legacy_external_authority
        and request.get("schema_version") == 2
        and isinstance(authority_envelope, Mapping)
        and "bytes" not in authority_envelope
    )
    _validate_external_authority(
        payloads,
        request,
        structure,
        allow_embedded_only=legacy_missing_bytes,
        allow_legacy_missing_bytes=legacy_missing_bytes,
    )
    landscape = values["frustrampnn_landscape_v2.json"]
    summary = values["frustrampnn_summary_v2.json"]
    receipt = values["frustrampnn_execution_receipt_v2.json"]
    statistics = values["frustrampnn_statistics_v1.json"]
    raw = payloads["raw_frustrampnn.csv"]
    try:
        merged, expected_landscape = finalize_landscape_v2(
            (raw,), effective,
            execution_configuration=configuration,
            target_id=structure["target_id"],
            parent_job_id=request["parent_job_id"],
            candidate_id=request["candidate_id"],
            source_artifact_sha256=request["source_artifact"]["sha256"],
        )
    except Exception as exc:
        raise ManifestValidationError(f"v2 raw/landscape recomputation failed: {exc}") from exc
    if merged != raw or expected_landscape != landscape:
        raise ManifestValidationError("v2 raw bytes and landscape do not exactly recompute")
    if summarize_landscape_v2(landscape, effective) != summary:
        raise ManifestValidationError("v2 summary does not exactly recompute")
    try:
        capability_inventory, inventory_sha256 = _settings.load_capability_inventory()
        descriptor = _runtime.open_regular_no_follow(
            _settings._CAPABILITY_INVENTORY_PATH,
            label="canonical capability inventory",
        )
        try:
            chunks: list[bytes] = []
            offset = 0
            while chunk := os.pread(descriptor, 1024 * 1024, offset):
                chunks.append(chunk)
                offset += len(chunk)
            capability_inventory_bytes = b"".join(chunks)
        finally:
            os.close(descriptor)
        if (
            hashlib.sha256(capability_inventory_bytes).hexdigest() != inventory_sha256
            or inventory_sha256 != request["capability_inventory_byte_sha256"]
        ):
            raise ContractValidationError(
                "installed capability inventory bytes disagree with v2 request authority"
            )
        expected_statistics = build_statistics_receipt(
            request=request,
            execution_receipt=receipt,
            landscape=landscape,
            structure_map=structure,
            capability_inventory=capability_inventory,
            capability_inventory_bytes=capability_inventory_bytes,
            allow_legacy_external_authority=allow_legacy_external_authority,
        )
        validate_statistics_receipt(statistics)
    except (OSError, ContractValidationError, rfc8785.CanonicalizationError) as exc:
        raise ManifestValidationError(
            f"v2 statistics authority validation failed: {exc}"
        ) from exc
    if statistics != expected_statistics or payloads[
        "frustrampnn_statistics_v1.json"
    ] != rfc8785.dumps(expected_statistics):
        raise ManifestValidationError(
            "v2 statistics do not exactly recompute from canonical bundle authority"
        )
    plan = compile_frustrampnn_command_plan(effective)
    entries = [entry.canonical_payload() for entry in plan.entries]
    if receipt["command_plan"] != {"entries": entries, "plan_sha256": plan.plan_sha256}:
        raise ManifestValidationError("v2 receipt command plan disagrees with effective settings")
    if any(command["status"] != "succeeded" or command["exit_code"] != 0 for command in receipt["commands"]):
        raise ManifestValidationError("v2 success receipt contains a failed or partial command")
    if (
        receipt["invocation_id"] != request["invocation_id"]
        or receipt["execution_configuration_sha256"] != request["execution_configuration_sha256"]
        or receipt["requested_settings_sha256"] != request["requested_settings_sha256"]
        or receipt["effective_settings_sha256"] != request["effective_settings_sha256"]
        or receipt["runtime_identity_sha256"] != request["runtime_identity_sha256"]
        or receipt["source_artifact_sha256"] != request["source_artifact"]["sha256"]
        or receipt["structure_map_sha256"] != request["structure_map_sha256"]
        or receipt["normalized_pdb_sha256"] != request["normalized_pdb_sha256"]
        or receipt["merged_raw_csv_sha256"] != hashlib.sha256(raw).hexdigest()
        or receipt["landscape_sha256"] != canonical_sha256(landscape)
        or receipt["summary_sha256"] != canonical_sha256(summary)
    ):
        raise ManifestValidationError("v2 receipt request/config/settings/runtime/artifact hash closure failed")
    _validate_receipt_argv_v2(receipt, configuration)
    identity = (request["invocation_id"], request["parent_job_id"], request["candidate_id"])
    if (
        (manifest["invocation_id"], manifest["parent_job_id"], manifest["candidate_id"]) != identity
        or manifest["request_sha256"] != request_sha256(request)
        or manifest["source_artifact_sha256"] != request["source_artifact"]["sha256"]
        or manifest["execution_configuration_sha256"] != request["execution_configuration_sha256"]
        or manifest["statistics_sha256"] != statistics["statistics_sha256"]
        or manifest["comparison_compatibility_id"]
        != statistics["comparison_compatibility_id"]
    ):
        raise ManifestValidationError("v2 manifest identity/request/source/config closure failed")
    if require_result:
        result = values["workflow_component_result_v2.json"]
        if (
            result["status"] != "succeeded"
            or result["request_sha256"] != manifest["request_sha256"]
            or (result["invocation_id"], result["parent_job_id"], result["candidate_id"]) != identity
            or result["parent_workflow_id"] != request["parent_workflow_id"]
            or result["result_manifest"] != {
                "relative_path": V2_MANIFEST_PATH,
                "sha256": canonical_sha256(dict(manifest)),
            }
            or result["result_payload"] != {
                "relative_path": "frustrampnn_summary_v2.json",
                "schema_name": "frustrampnn_summary",
                "schema_version": 2,
                "sha256": canonical_sha256(summary),
            }
        ):
            raise ManifestValidationError("v2 component-result manifest/payload closure failed")


def _build_result_manifest_v2(root: Path | str) -> dict[str, Any]:
    payloads = _snapshot_v2(root, require_manifest=False)
    instances = {
        relative: _json_identity(payloads[relative], relative)[2]
        for relative in _V2_SCHEMA_KEYS
        if relative != "workflow_component_result_v2.json"
    }
    request = instances["workflow_component_request_v2.json"]
    structure = instances["frustrampnn_structure_map_v1.json"]
    landscape = instances["frustrampnn_landscape_v2.json"]
    cardinalities = {
        "workflow_component_request_v2.json": None,
        AUTHORITY_ARTIFACT_PATH: {"kind": "records", "count": 1},
        "normalized_input.pdb": {
            "kind": "residues",
            "count": sum(row["status"] == "mapped" for row in structure["rows"]),
        },
        "frustrampnn_structure_map_v1.json": {
            "kind": "residues", "count": len(structure["rows"]),
        },
        "raw_frustrampnn.csv": {
            "kind": "rows", "count": len(landscape["residues"]) * len(AA_ORDER),
        },
        "frustrampnn_landscape_v2.json": {
            "kind": "residues", "count": len(landscape["residues"]),
        },
        "frustrampnn_summary_v2.json": {"kind": "records", "count": 1},
        "frustrampnn_stdout.log": None,
        "frustrampnn_stderr.log": None,
        "frustrampnn_execution_receipt_v2.json": {"kind": "records", "count": 1},
        "frustrampnn_statistics_v1.json": {"kind": "records", "count": 1},
    }
    artifact_paths = (
        V2_EXTERNAL_MANIFEST_ARTIFACT_PATHS
        if AUTHORITY_ARTIFACT_PATH in payloads
        else V2_MANIFEST_ARTIFACT_PATHS
    )
    artifacts = []
    for relative in artifact_paths:
        record = _record(relative, payloads[relative], cardinalities[relative])
        record.pop("role", None)
        artifacts.append(record)
    manifest = {
        "schema_name": "frustrampnn_result_manifest",
        "schema_version": 2,
        "invocation_id": request["invocation_id"],
        "parent_job_id": request["parent_job_id"],
        "candidate_id": request["candidate_id"],
        "request_sha256": request_sha256(request),
        "source_artifact_sha256": request["source_artifact"]["sha256"],
        "execution_configuration_sha256": request["execution_configuration_sha256"],
        "statistics_sha256": instances["frustrampnn_statistics_v1.json"][
            "statistics_sha256"
        ],
        "comparison_compatibility_id": instances[
            "frustrampnn_statistics_v1.json"
        ]["comparison_compatibility_id"],
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    try:
        validate_schema("frustrampnn_result_manifest_v2", manifest)
    except Exception as exc:
        raise ManifestValidationError(f"v2 manifest contract failed: {exc}") from exc
    _validate_v2_closure(payloads, manifest, require_result=False)
    return manifest


def _validate_result_manifest_v2(
    root: Path | str,
    manifest: Mapping[str, Any],
    *,
    allow_legacy_external_authority: bool = False,
) -> dict[str, bytes]:
    try:
        validate_schema("frustrampnn_result_manifest_v2", manifest)
    except Exception as exc:
        raise ManifestValidationError(f"v2 manifest schema failed: {exc}") from exc
    payloads = _snapshot_v2(root, require_manifest=True, manifest=manifest)
    physical = _json_identity(payloads[V2_MANIFEST_PATH], V2_MANIFEST_PATH)[2]
    if physical != dict(manifest) or payloads[V2_MANIFEST_PATH] != canonical_json_bytes(physical):
        raise ManifestValidationError("physical v2 result manifest is not exact canonical supplied bytes")
    artifact_paths = (
        V2_EXTERNAL_MANIFEST_ARTIFACT_PATHS
        if AUTHORITY_ARTIFACT_PATH in payloads
        else V2_MANIFEST_ARTIFACT_PATHS
    )
    if [record["relative_path"] for record in manifest["artifacts"]] != list(artifact_paths):
        raise ManifestValidationError("v2 manifest path order/set is not canonical")
    for declared in manifest["artifacts"]:
        observed = _record(
            declared["relative_path"],
            payloads[declared["relative_path"]],
            declared["cardinality"],
            allow_legacy_external_authority=allow_legacy_external_authority,
        )
        observed.pop("role", None)
        if dict(declared) != observed:
            raise ManifestValidationError(
                f"v2 hash/size/schema/cardinality mismatch for {declared['relative_path']}"
            )
    _validate_v2_closure(
        payloads,
        manifest,
        require_result=True,
        allow_legacy_external_authority=allow_legacy_external_authority,
    )
    return dict(payloads)


def build_result_manifest(root: Path | str) -> dict[str, Any]:
    root_fd = _open_root(root)
    try:
        names = {entry.name for entry in os.scandir(root_fd)}
    finally:
        os.close(root_fd)
    if "workflow_component_request_v2.json" in names:
        return _build_result_manifest_v2(root)
    return _build_result_manifest_v1(root)


def validate_result_manifest(
    root: Path | str,
    manifest: Mapping[str, Any],
    *,
    allow_legacy_v2_external_authority: bool = False,
) -> dict[str, bytes]:
    version = manifest.get("schema_version") if isinstance(manifest, Mapping) else None
    if version == 2:
        return _validate_result_manifest_v2(
            root,
            manifest,
            allow_legacy_external_authority=allow_legacy_v2_external_authority,
        )
    if version == 1:
        return _validate_result_manifest_v1(root, manifest)
    raise ManifestValidationError("result manifest schema generation is unsupported")


def result_manifest_path(root: Path | str) -> str:
    root_fd = _open_root(root)
    try:
        names = {entry.name for entry in os.scandir(root_fd)}
    finally:
        os.close(root_fd)
    present = [name for name in (MANIFEST_PATH, V2_MANIFEST_PATH) if name in names]
    if len(present) != 1:
        raise ManifestValidationError("bundle must contain exactly one recognized manifest generation")
    return present[0]


def load_result_manifest(root: Path | str) -> dict[str, Any]:
    """Load one canonical recognized manifest through the bounded no-follow reader."""

    _, _, manifest = load_result_manifest_bytes_and_document(root)
    return manifest


def load_result_manifest_bytes_and_document(
    root: Path | str,
) -> tuple[str, bytes, dict[str, Any]]:
    """Return one recognized manifest's name, exact bytes, and document.

    The physical file is opened without following symlinks, rejected from its
    metadata before any allocation above 64 KiB, and generation-checked while
    read.  Callers that subsequently validate the bundle must pass the returned
    document to :func:`validate_result_manifest`, which reopens the same named
    generation and requires exact physical equality.
    """

    manifest_name = result_manifest_path(root)
    manifest, payload = _load_manifest_bytes(root, manifest_name)
    return manifest_name, payload, manifest


__all__ = [
    "CANONICAL_ARTIFACT_PATHS", "MANIFEST_PATH", "V2_CANONICAL_ARTIFACT_PATHS",
    "V2_MANIFEST_ARTIFACT_PATHS", "V2_MANIFEST_PATH", "ManifestValidationError",
    "build_result_manifest", "load_result_manifest", "load_result_manifest_bytes_and_document",
    "result_manifest_path",
    "summarize_landscape_v2", "validate_external_authority_artifact",
    "validate_result_manifest", "validate_v2_input_closure",
]
