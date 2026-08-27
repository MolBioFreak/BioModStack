#!/usr/bin/env python3
"""Verify and stage one persisted immutable FrustraMPNN scheduler record."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.frustrampnn.contracts import canonical_json_bytes, validate_schema  # noqa: E402


_V1_RECORD_KEYS = {
    "ordinal",
    "candidate_id",
    "invocation_id",
    "request_relative_path",
    "request_sha256",
    "request_size_bytes",
    "source_relative_path",
    "source_sha256",
    "source_size_bytes",
}
_V1_WRITER_RECORD_KEYS = {*_V1_RECORD_KEYS, "launch_authority"}
_V2_RECORD_KEYS = {
    "record_schema_name",
    "record_schema_version",
    *_V1_RECORD_KEYS,
    "structure_map_relative_path",
    "structure_map_sha256",
    "structure_map_size_bytes",
}
_V2_RECORD_SCHEMA = "bms_frustrampnn_scheduler_record"


def _read_regular(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("staged authority is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _record_version(record: dict[str, Any]) -> int:
    keys = set(record)
    if keys in (_V1_RECORD_KEYS, _V1_WRITER_RECORD_KEYS):
        return 1
    if keys != _V2_RECORD_KEYS:
        raise ValueError("persisted batch record has an invalid field set")
    if (
        record["record_schema_name"] != _V2_RECORD_SCHEMA
        or isinstance(record["record_schema_version"], bool)
        or record["record_schema_version"] != 2
    ):
        raise ValueError("persisted batch record has an invalid schema generation")
    return 2


def _decode_record(value: str) -> tuple[int, dict[str, Any]]:
    try:
        payload = base64.b64decode(value, validate=True)
        record = json.loads(payload)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("persisted batch record is invalid") from exc
    if not isinstance(record, dict):
        raise ValueError("persisted batch record has an invalid field set")
    version = _record_version(record)
    for field in ("ordinal", "request_size_bytes", "source_size_bytes"):
        if isinstance(record[field], bool) or not isinstance(record[field], int) or record[field] < 0:
            raise ValueError(f"persisted batch record {field} is invalid")
    if version == 2:
        size = record["structure_map_size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("persisted batch record structure_map_size_bytes is invalid")
    return version, record


def _validate_relative_path(value: object, *, prefix: tuple[str, ...], filename: str | None = None) -> None:
    if not isinstance(value, str) or not value or "\\" in value or "//" in value:
        raise ValueError("persisted batch record path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts or path.parts[: len(prefix)] != prefix:
        raise ValueError("persisted batch record path is invalid")
    if filename is not None and path.name != filename:
        raise ValueError("persisted batch record filename is invalid")


def _verify_bytes(kind: str, payload: bytes, record: dict[str, Any], *, key: str) -> None:
    if len(payload) != record[f"{key}_size_bytes"]:
        raise ValueError(f"persisted {kind} size binding is invalid")
    if hashlib.sha256(payload).hexdigest() != record[f"{key}_sha256"]:
        raise ValueError(f"persisted {kind} digest binding is invalid")


def _canonical_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"persisted {label} is invalid JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
        raise ValueError(f"persisted {label} is not canonical JSON")
    return value


def _immutable_write(target: Path, payload: bytes) -> None:
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o400,
    )
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("immutable staging write made no progress")
            written += count
        os.fsync(descriptor)
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    finally:
        os.close(descriptor)


def prepare(
    *,
    record_base64: str,
    request_path: Path,
    source_path: Path,
    structure_map_path: Path | None,
    output_request: Path,
    output_source: Path,
    output_structure_map: Path | None,
) -> None:
    version, record = _decode_record(record_base64)
    _validate_relative_path(record["request_relative_path"], prefix=("inputs", "requests"))
    _validate_relative_path(record["source_relative_path"], prefix=("inputs", "sources"))

    request_payload = _read_regular(request_path)
    source_payload = _read_regular(source_path)
    _verify_bytes("request", request_payload, record, key="request")
    _verify_bytes("source", source_payload, record, key="source")
    request = _canonical_object(request_payload, label="component request")
    if request.get("candidate_id") != record["candidate_id"]:
        raise ValueError("candidate identity binding is invalid")
    if request.get("invocation_id") != record["invocation_id"]:
        raise ValueError("invocation identity binding is invalid")

    if version == 1:
        if structure_map_path is not None or output_structure_map is not None:
            raise ValueError("historical v1 record rejects structure-map staging")
        validate_schema("workflow_component_request_v1", request)
        if request["source_artifact"]["relative_path"] != record["source_relative_path"]:
            raise ValueError("source path binding is invalid")
        if request["source_artifact"]["sha256"] != record["source_sha256"]:
            raise ValueError("source digest binding is invalid")
        outputs = ((output_request, request_payload), (output_source, source_payload))
    else:
        if structure_map_path is None or output_structure_map is None:
            raise ValueError("v2 record requires exact structure-map staging")
        request_generation = request.get("schema_version")
        if isinstance(request_generation, bool) or request_generation not in (2, 3):
            raise ValueError("v2 record requires component request schema generation 2 or 3")
        _validate_relative_path(
            record["request_relative_path"],
            prefix=("inputs", "requests"),
            filename=f"workflow_component_request_v{request_generation}.json",
        )
        _validate_relative_path(
            record["source_relative_path"],
            prefix=("inputs", "sources"),
            filename="canonical_source.pdb",
        )
        _validate_relative_path(
            record["structure_map_relative_path"],
            prefix=("inputs", "maps"),
            filename="frustrampnn_structure_map_v1.json",
        )
        structure_map_payload = _read_regular(structure_map_path)
        _verify_bytes("structure map", structure_map_payload, record, key="structure_map")
        structure_map = _canonical_object(structure_map_payload, label="structure map")
        validate_schema(f"workflow_component_request_v{request_generation}", request)
        validate_schema("frustrampnn_structure_map_v1", structure_map)
        if request["normalized_pdb_sha256"] != record["source_sha256"]:
            raise ValueError("normalized PDB digest binding is invalid")
        if request["structure_map_sha256"] != record["structure_map_sha256"]:
            raise ValueError("structure map request binding is invalid")
        if structure_map["source_sha256"] != request["source_artifact"]["sha256"]:
            raise ValueError("original source digest binding is invalid")
        if structure_map["normalized_pdb_sha256"] != record["source_sha256"]:
            raise ValueError("structure map normalized PDB binding is invalid")
        if (
            structure_map["parent_job_id"] != request["parent_job_id"]
            or structure_map["target_id"] != request["parent_job_id"]
            or structure_map["candidate_id"] != request["candidate_id"]
        ):
            raise ValueError("structure map owner identity binding is invalid")
        requested_source = request["requested_settings"]["source_structure"]
        preferred_altloc = requested_source["preferred_altloc"] or "<blank>"
        if (
            structure_map["selected_source_model"]
            != requested_source["selected_model_number"]
            or structure_map["altloc_policy"]
            != f"blank_or_explicit:{preferred_altloc}"
        ):
            raise ValueError("structure map source-settings binding is invalid")
        outputs = (
            (output_request, request_payload),
            (output_source, source_payload),
            (output_structure_map, structure_map_payload),
        )

    for target, payload in outputs:
        _immutable_write(target, payload)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-base64", required=True)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--structure-map", type=Path)
    parser.add_argument("--output-request", required=True, type=Path)
    parser.add_argument("--output-source", required=True, type=Path)
    parser.add_argument("--output-structure-map", type=Path)
    args = parser.parse_args()
    try:
        prepare(
            record_base64=args.record_base64,
            request_path=args.request,
            source_path=args.source,
            structure_map_path=args.structure_map,
            output_request=args.output_request,
            output_source=args.output_source,
            output_structure_map=args.output_structure_map,
        )
    except Exception as exc:
        print(f"persisted_frustrampnn_prepare_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
