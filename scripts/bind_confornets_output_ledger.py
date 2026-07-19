#!/usr/bin/env python3
"""Bind explicit upstream ConforNets coordinates to normalized output bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path, PurePosixPath
from typing import Any


class LedgerError(ValueError):
    pass


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


def bind(request_path: Path, plan_path: Path, native_root: Path, output: Path) -> None:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    samples = json.loads((native_root / "samples.json").read_text(encoding="utf-8"))
    upstream_path = native_root / "raw" / "cm_upstream_coordinate_ledger_v1.json"
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    if not isinstance(upstream, dict) or not isinstance(upstream.get("entries"), list):
        raise LedgerError("upstream coordinate ledger is invalid")
    if upstream.get("request_sha256") != request["request_sha256"]:
        raise LedgerError("upstream ledger request binding mismatch")
    if upstream.get("coordinate_plan_sha256") != plan["coordinate_plan_sha256"]:
        raise LedgerError("upstream ledger coordinate-plan binding mismatch")

    by_source: dict[str, dict[str, Any]] = {}
    for entry in upstream["entries"]:
        source_path = _relative(entry.get("source_relative_path"))
        if source_path in by_source:
            raise LedgerError("duplicate upstream source path")
        by_source[source_path] = entry
    expected_coordinates = {_canonical_bytes(value) for value in plan["coordinates"]}
    if {_canonical_bytes(entry.get("coordinates")) for entry in by_source.values()} != expected_coordinates:
        raise LedgerError("upstream ledger coordinates do not equal canonical plan")

    normalized_entries: list[dict[str, Any]] = []
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
        normalized_entries.append(
            {
                "coordinates": by_source[source_path]["coordinates"],
                "relative_path": relative_path,
                "bytes": artifact.stat().st_size,
                "sha256": _sha256(artifact),
                "source_relative_path": f"raw/{source_path}",
                "source_bytes": source_artifact.stat().st_size,
                "source_sha256": _sha256(source_artifact),
            }
        )
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
