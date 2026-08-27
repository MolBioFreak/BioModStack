#!/usr/bin/env python3
"""Validate closed modern publication markers and emit stage-reporter paths."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.frustrampnn.contracts import canonical_json_loads, validate_schema  # noqa: E402
from services.frustrampnn.manifests import (  # noqa: E402
    V2_MANIFEST_PATH,
    V3_MANIFEST_PATH,
    ManifestValidationError,
    _read_regular,
    load_result_manifest,
    validate_result_manifest,
)


_FIELDS_BY_GENERATION = {
    2: {"manifest", "result", "source", "statistics"},
    3: {"manifest", "result", "source"},
}
_SOURCE_SUFFIXES = {".pdb", ".cif", ".mmcif"}


def _relative_path(value: Any, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"{field} must be a nonempty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{field} must be a contained POSIX relative path")
    return path


def validate_marker(payload: Any) -> dict[str, PurePosixPath]:
    if isinstance(payload, dict) and set(payload) == {"result", "source"}:
        paths = {field: _relative_path(payload[field], field) for field in payload}
        if paths["result"].name != "workflow_component_result_v3.json":
            raise ValueError("classified failure result has the wrong artifact name")
        if paths["source"].suffix.lower() not in _SOURCE_SUFFIXES:
            raise ValueError("source has an unsupported structure artifact name")
        return paths
    if not isinstance(payload, dict) or "manifest" not in payload:
        raise ValueError("marker fields are not exact")
    manifest = _relative_path(payload["manifest"], "manifest")
    generation = (
        3 if manifest.name == V3_MANIFEST_PATH
        else 2 if manifest.name == V2_MANIFEST_PATH
        else None
    )
    if generation is None or set(payload) != _FIELDS_BY_GENERATION[generation]:
        raise ValueError("marker generation/fields are not exact")
    paths = {field: _relative_path(payload[field], field) for field in payload}
    if paths["result"].name != f"workflow_component_result_v{generation}.json":
        raise ValueError("result has the wrong generation-specific artifact name")
    if generation == 2 and paths["statistics"].name != "frustrampnn_statistics_v1.json":
        raise ValueError("statistics has the wrong v2 artifact name")
    if paths["source"].suffix.lower() not in _SOURCE_SUFFIXES:
        raise ValueError("source has an unsupported structure artifact name")
    bundle_parent = paths["manifest"].parent
    for field in set(paths) - {"manifest", "source"}:
        if paths[field].parent != bundle_parent:
            raise ValueError(f"{field} does not belong to the manifest-attested bundle")
    return paths


def _validate_closed_marker(
    *,
    job_root: Path,
    marker: Path,
    include_status: bool = False,
) -> tuple[str, ...] | tuple[tuple[str, ...], str]:
    marker_root = marker.parent if marker.parent != Path(".") else Path.cwd()
    marker_payload = canonical_json_loads(_read_regular(marker_root, marker.name))
    paths = validate_marker(marker_payload)
    if "manifest" not in paths:
        bundle_root = job_root.joinpath(*paths["result"].parent.parts)
        terminal = canonical_json_loads(
            _read_regular(bundle_root, "workflow_component_result_v3.json")
        )
        validate_schema("workflow_component_result_v3", terminal)
        if terminal["status"] != "failed" or terminal["result_manifest"] is not None:
            raise ValueError("terminal-only marker does not reference a classified v3 failure")
        request = canonical_json_loads(
            _read_regular(bundle_root, "workflow_component_request_v3.json")
        )
        validate_schema("workflow_component_request_v3", request)
        if paths["source"].as_posix() != request["source_artifact"]["relative_path"]:
            raise ValueError("classified failure source does not match request authority")
        if hashlib.sha256(_read_regular(job_root, paths["source"].as_posix())).hexdigest() != request["source_artifact"]["sha256"]:
            raise ValueError("classified failure source bytes contradict request authority")
        outputs = (paths["result"].as_posix(), paths["source"].as_posix())
        return (outputs, "failed") if include_status else outputs
    bundle_root = job_root.joinpath(*paths["manifest"].parent.parts)
    manifest = load_result_manifest(bundle_root)
    generation = manifest.get("schema_version")
    if generation not in {2, 3}:
        raise ValueError("marker does not reference a modern result manifest")
    payloads = validate_result_manifest(bundle_root, manifest)

    request = canonical_json_loads(
        payloads[f"workflow_component_request_v{generation}.json"]
    )
    if paths["source"].as_posix() != request["source_artifact"]["relative_path"]:
        raise ValueError("marker source does not match the manifest-attested request authority")
    source_payload = _read_regular(job_root, paths["source"].as_posix())
    expected_source_sha256 = request["source_artifact"]["sha256"]
    if hashlib.sha256(source_payload).hexdigest() != expected_source_sha256:
        raise ValueError("marker source bytes contradict the manifest-attested source authority")

    outputs = [
        paths["result"].as_posix(),
        paths["manifest"].as_posix(),
        paths["source"].as_posix(),
    ]
    if generation == 2:
        outputs.append(paths["statistics"].as_posix())
    validated_outputs = tuple(outputs)
    return (validated_outputs, "complete") if include_status else validated_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("--status-output", type=Path)
    parser.add_argument("markers", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        outputs: list[str] = []
        statuses: set[str] = set()
        for marker in sorted(args.markers, key=lambda path: path.as_posix()):
            marker_outputs, marker_status = _validate_closed_marker(
                job_root=args.job_root, marker=marker, include_status=True
            )
            outputs.extend(marker_outputs)
            statuses.add(marker_status)
        if args.status_output is not None:
            stage_status = "failed" if "failed" in statuses else "complete"
            fd = os.open(
                args.status_output,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(stage_status + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    except (OSError, ValueError, TypeError, ManifestValidationError) as exc:
        print(f"invalid FrustraMPNN publication marker: {exc}", file=sys.stderr)
        return 2
    print("\n".join(outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
