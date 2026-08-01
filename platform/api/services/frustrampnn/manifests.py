"""Canonical bundle inventory construction and no-follow validation."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import os
import stat
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
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
_SCHEMA_KEYS = {
    "workflow_component_request_v1.json": "workflow_component_request_v1",
    "frustrampnn_structure_map_v1.json": "frustrampnn_structure_map_v1",
    "frustrampnn_landscape_v1.json": "frustrampnn_landscape_v1",
    "frustrampnn_summary_v1.json": "frustrampnn_summary_v1",
    "frustrampnn_execution_receipt_v1.json": "frustrampnn_execution_receipt_v1",
    "workflow_component_result_v1.json": "workflow_component_result_v1",
}
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
}


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


def _read_regular(root: Path | str | int, relative: str) -> bytes:
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
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            return handle.read()
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
    root: Path | str, *, require_manifest: bool,
    expected: set[str] | tuple[set[str], ...],
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
        paths: list[str] = list(
            EXTERNAL_CANONICAL_ARTIFACT_PATHS
            if AUTHORITY_ARTIFACT_PATH in observed
            else CANONICAL_ARTIFACT_PATHS
        )
        if require_manifest:
            paths.append(MANIFEST_PATH)
        payloads = {relative: _read_regular(root_fd, relative) for relative in paths}
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
    if payload != canonical_json_bytes(instance):
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
    if relative == "frustrampnn_landscape_v1.json":
        return {"kind": "residues", "count": len(instance["residues"])}
    if relative in {"frustrampnn_summary_v1.json", "frustrampnn_execution_receipt_v1.json", "workflow_component_result_v1.json"}:
        return {"kind": "records", "count": 1}
    return None


def _record(
    relative: str,
    payload: bytes,
    declared_cardinality: Mapping[str, Any] | None,
) -> dict[str, Any]:
    schema_name: str | None = None
    schema_version: int | None = None
    instance: Any | None = None
    schema_key = _SCHEMA_KEYS.get(relative)
    if schema_key is not None:
        schema_name, schema_version, instance = _json_identity(payload, relative)
        expected_name = {
            "workflow_component_request_v1": "workflow_component_request",
            "workflow_component_result_v1": "workflow_component_result",
            "frustrampnn_structure_map_v1": "frustrampnn_structure_map",
            "frustrampnn_landscape_v1": "frustrampnn_landscape",
            "frustrampnn_summary_v1": "frustrampnn_summary",
            "frustrampnn_execution_receipt_v1": "frustrampnn_execution_receipt",
        }[schema_key]
        if schema_name != expected_name or schema_version != 1:
            raise ManifestValidationError(f"schema identity mismatch for {relative}")
        try:
            validate_schema(schema_key, instance)
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


def build_result_manifest(root: Path | str) -> dict[str, Any]:
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
    for relative in _SCHEMA_KEYS:
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
) -> None:
    """Close canonical producer authority bytes against source and mapped identities."""

    authority_payload = payloads.get(AUTHORITY_ARTIFACT_PATH)
    external = request["identity_authority"] in {
        "producer_manifest", "cm_complex_snapshot",
    }
    if external != (authority_payload is not None):
        raise ManifestValidationError(
            "external identity authority artifact presence disagrees with request authority"
        )
    if authority_payload is None:
        return
    schema_name, schema_version, authority = _json_identity(
        authority_payload, AUTHORITY_ARTIFACT_PATH,
    )
    if schema_name != "producer_manifest" or schema_version != 1:
        raise ManifestValidationError("external authority artifact typed schema is invalid")
    authority_digest = hashlib.sha256(authority_payload).hexdigest()
    envelope = request.get("identity_authority_artifact")
    if not isinstance(envelope, Mapping):
        raise ManifestValidationError("request lacks the external authority artifact envelope")
    try:
        request_bound_payload = base64.b64decode(
            envelope["canonical_json_base64"], validate=True,
        )
    except Exception as exc:
        raise ManifestValidationError("request authority artifact base64 is invalid") from exc
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
            expected_label = residue_mappings.get(mapping_key)
            if row["label_seq_id"] != expected_label:
                raise ManifestValidationError("external authority residue mapping disagrees with structure map")


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
    for relative in _SCHEMA_KEYS:
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


def validate_result_manifest(
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
        bundle, require_manifest=True, expected=expected_options,
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


__all__ = [
    "CANONICAL_ARTIFACT_PATHS", "MANIFEST_PATH", "ManifestValidationError",
    "build_result_manifest", "validate_result_manifest",
]
