#!/usr/bin/env python3
"""Verify and stage one persisted immutable FrustraMPNN request/source pair."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.frustrampnn.contracts import canonical_json_bytes, validate_schema  # noqa: E402


_RECORD_KEYS = {
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


def _decode_record(value: str) -> dict:
    try:
        payload = base64.b64decode(value, validate=True)
        record = json.loads(payload)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("persisted batch record is invalid") from exc
    if not isinstance(record, dict) or set(record) != _RECORD_KEYS:
        raise ValueError("persisted batch record has an invalid field set")
    return record


def prepare(*, record_base64: str, request_path: Path, source_path: Path, output_request: Path, output_source: Path) -> None:
    record = _decode_record(record_base64)
    request_payload = _read_regular(request_path)
    source_payload = _read_regular(source_path)
    for kind, payload in (("request", request_payload), ("source", source_payload)):
        if len(payload) != record[f"{kind}_size_bytes"]:
            raise ValueError(f"persisted {kind} size binding is invalid")
        if hashlib.sha256(payload).hexdigest() != record[f"{kind}_sha256"]:
            raise ValueError(f"persisted {kind} digest binding is invalid")
    request = json.loads(request_payload)
    validate_schema("workflow_component_request_v1", request)
    if canonical_json_bytes(request) != request_payload:
        raise ValueError("persisted component request is not canonical JSON")
    if request["candidate_id"] != record["candidate_id"]:
        raise ValueError("candidate identity binding is invalid")
    if request["invocation_id"] != record["invocation_id"]:
        raise ValueError("invocation identity binding is invalid")
    if request["source_artifact"]["relative_path"] != record["source_relative_path"]:
        raise ValueError("source path binding is invalid")
    if request["source_artifact"]["sha256"] != record["source_sha256"]:
        raise ValueError("source digest binding is invalid")
    for target, payload in ((output_request, request_payload), (output_source, source_payload)):
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o400)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.fchmod(descriptor, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        finally:
            os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-base64", required=True)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-request", required=True, type=Path)
    parser.add_argument("--output-source", required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(
            record_base64=args.record_base64,
            request_path=args.request,
            source_path=args.source,
            output_request=args.output_request,
            output_source=args.output_source,
        )
    except Exception as exc:
        print(f"persisted_frustrampnn_prepare_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
