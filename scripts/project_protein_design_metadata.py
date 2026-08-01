#!/usr/bin/env python3
"""Bind and project canonical protein-design metadata without filename heuristics."""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.frustrampnn.identity import deterministic_candidate_id  # noqa: E402


_SCHEMA_NAME = "protein_design_terminal_candidate"
_SCHEMA_VERSION = 1
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_FIELDS = (
    "schema_name",
    "schema_version",
    "candidate_id",
    "parent_job_id",
    "parent_workflow_id",
    "producer_stage",
    "producer_candidate_key",
    "producer_method",
    "producer_sample",
    "producer_rank",
    "producer_output_key",
    "producer_identity_sha256",
    "producer_artifact_sha256",
    "source_format",
)
_LINEAGE_FIELDS = tuple(
    field for field in _MANIFEST_FIELDS if field not in {"schema_name", "schema_version"}
)
_NULLABLE_LINEAGE_FIELDS = frozenset({"producer_sample", "producer_rank"})
_CANONICAL_NONNEGATIVE_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_MISSING = object()


class ProjectionError(ValueError):
    """Raised when terminal identity and ordinary metadata do not close exactly."""


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectionError(f"terminal manifest is unreadable or malformed: {path}") from exc
    if not isinstance(payload, dict):
        raise ProjectionError("terminal manifest must be a JSON object")
    return payload


def _validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != set(_MANIFEST_FIELDS):
        raise ProjectionError("terminal manifest fields are not exact")
    if payload["schema_name"] != _SCHEMA_NAME or payload["schema_version"] != _SCHEMA_VERSION:
        raise ProjectionError("terminal manifest schema is unsupported")
    required_text = (
        "candidate_id",
        "parent_job_id",
        "parent_workflow_id",
        "producer_stage",
        "producer_candidate_key",
        "producer_method",
        "producer_output_key",
    )
    if any(not isinstance(payload[field], str) or not payload[field].strip() for field in required_text):
        raise ProjectionError("terminal manifest has an empty identity field")
    if payload["parent_workflow_id"] != "protein_design":
        raise ProjectionError("terminal manifest is not owned by protein_design")
    if payload["source_format"] not in {"pdb", "mmcif"}:
        raise ProjectionError("terminal manifest source_format is invalid")
    if not all(
        isinstance(payload[field], str) and _HASH.fullmatch(payload[field])
        for field in ("producer_identity_sha256", "producer_artifact_sha256")
    ):
        raise ProjectionError("terminal manifest producer hashes are invalid")
    sample = payload["producer_sample"]
    if sample is not None and (not isinstance(sample, str) or not sample):
        raise ProjectionError("terminal manifest producer_sample is invalid")
    rank = payload["producer_rank"]
    if rank is not None and (
        isinstance(rank, bool) or not isinstance(rank, int) or rank < 0
    ):
        raise ProjectionError("terminal manifest producer_rank is invalid")
    expected = deterministic_candidate_id(
        parent_job_id=payload["parent_job_id"],
        parent_workflow_id=payload["parent_workflow_id"],
        producer_stage=payload["producer_stage"],
        producer_candidate_key=payload["producer_candidate_key"],
    )
    if payload["candidate_id"] != expected:
        raise ProjectionError("terminal manifest candidate_id is not deterministic")
    return payload


def _decode_manifest(encoded: str) -> dict[str, Any]:
    try:
        raw = base64.b64decode(encoded, validate=True)
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectionError("terminal metadata is not canonical base64 JSON") from exc
    if not isinstance(payload, dict):
        raise ProjectionError("terminal metadata must be a JSON object")
    return _validate_manifest(payload)


def _canonical_lineage_value(field: str, value: Any) -> str | int | None:
    if field == "producer_rank":
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ProjectionError("ordinary metadata producer_rank is not canonical")
        if isinstance(value, int):
            if value < 0:
                raise ProjectionError("ordinary metadata producer_rank is not canonical")
            return value
        if isinstance(value, str) and _CANONICAL_NONNEGATIVE_INTEGER.fullmatch(value):
            return int(value)
        raise ProjectionError("ordinary metadata producer_rank is not canonical")
    if field == "producer_sample" and (value is None or value == ""):
        return None
    if not isinstance(value, str) or not value:
        nullable = field in _NULLABLE_LINEAGE_FIELDS
        suffix = " or null" if nullable else ""
        raise ProjectionError(f"ordinary metadata {field} must be a non-empty string{suffix}")
    return value


def _merge_lineage(row: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    merged = dict(row)
    for field in _LINEAGE_FIELDS:
        value = manifest[field]
        prior = merged.get(field, _MISSING)
        if prior is not _MISSING and _canonical_lineage_value(field, prior) != value:
            raise ProjectionError(f"ordinary metadata conflicts with terminal {field}")
        merged[field] = value
    return merged


def bind_jsonl(
    *,
    metadata_jsonl: Path,
    manifest_metadata_base64: str,
    output_jsonl: Path,
    terminal_manifest: Path,
) -> None:
    manifest = _decode_manifest(manifest_metadata_base64)
    try:
        ordinary_rows = [
            json.loads(line)
            for line in metadata_jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectionError("ordinary terminal metadata JSONL is unreadable or malformed") from exc
    if len(ordinary_rows) != 1 or not isinstance(ordinary_rows[0], dict):
        raise ProjectionError("ordinary terminal metadata must contain exactly one object")
    bound = _merge_lineage(ordinary_rows[0], manifest)
    output_jsonl.write_text(
        json.dumps(bound, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    terminal_manifest.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _load_manifests(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    ordered: list[dict[str, Any]] = []
    by_candidate: dict[str, dict[str, Any]] = {}
    for path in paths:
        manifest = _validate_manifest(_load_json_object(path))
        candidate_id = manifest["candidate_id"]
        if candidate_id in by_candidate:
            raise ProjectionError("terminal manifests contain a duplicate candidate_id")
        by_candidate[candidate_id] = manifest
        ordered.append(manifest)
    if not ordered:
        raise ProjectionError("terminal candidate manifest set is empty")
    return ordered, by_candidate


def project_csv(
    *,
    metadata_csv: Path,
    terminal_manifests: Iterable[Path],
    output: Path,
) -> None:
    _ordered_manifests, manifests = _load_manifests(terminal_manifests)
    try:
        with metadata_csv.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "candidate_id" not in reader.fieldnames:
                raise ProjectionError("ordinary metadata requires candidate_id authority")
            input_fields = list(reader.fieldnames)
            rows: list[dict[str, str]] = []
            seen: set[str] = set()
            for raw in reader:
                row = {str(key): str(value or "") for key, value in raw.items()}
                candidate_id = row["candidate_id"].strip()
                if not candidate_id or candidate_id in seen:
                    raise ProjectionError("ordinary metadata has a missing or duplicate candidate_id")
                if candidate_id not in manifests:
                    raise ProjectionError("ordinary metadata has an unmatched candidate_id")
                seen.add(candidate_id)
                rows.append(row)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ProjectionError("ordinary metadata CSV is unreadable or malformed") from exc
    if seen != set(manifests):
        raise ProjectionError("ordinary metadata candidate set is incomplete")

    output_fields = list(input_fields)
    for field in _LINEAGE_FIELDS:
        if field not in output_fields:
            output_fields.append(field)
    projected = [_merge_lineage(row, manifests[row["candidate_id"]]) for row in rows]
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=output_fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows(projected)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bind = commands.add_parser("bind-jsonl")
    bind.add_argument("--metadata-jsonl", required=True, type=Path)
    bind.add_argument("--manifest-metadata-base64", required=True)
    bind.add_argument("--output-jsonl", required=True, type=Path)
    bind.add_argument("--terminal-manifest", required=True, type=Path)
    project = commands.add_parser("project-csv")
    project.add_argument("--metadata-csv", required=True, type=Path)
    project.add_argument("--terminal-manifest", action="append", required=True, type=Path)
    project.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "bind-jsonl":
            bind_jsonl(
                metadata_jsonl=args.metadata_jsonl,
                manifest_metadata_base64=args.manifest_metadata_base64,
                output_jsonl=args.output_jsonl,
                terminal_manifest=args.terminal_manifest,
            )
        else:
            project_csv(
                metadata_csv=args.metadata_csv,
                terminal_manifests=args.terminal_manifest,
                output=args.output,
            )
    except (ProjectionError, OSError) as exc:
        print(f"protein_design_metadata_projection_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
