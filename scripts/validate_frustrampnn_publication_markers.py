#!/usr/bin/env python3
"""Validate closed v2 publication markers and emit stage-reporter paths."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.frustrampnn.contracts import canonical_json_loads  # noqa: E402
from services.frustrampnn.manifests import (  # noqa: E402
    V2_MANIFEST_PATH,
    ManifestValidationError,
    _read_regular,
    load_result_manifest,
    validate_result_manifest,
)


_FIELDS = {"manifest", "result", "source", "statistics"}
_EXPECTED_BASENAMES = {
    "manifest": V2_MANIFEST_PATH,
    "result": "workflow_component_result_v2.json",
    "statistics": "frustrampnn_statistics_v1.json",
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
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError("v2 marker fields are not exact")
    paths = {field: _relative_path(payload[field], field) for field in _FIELDS}
    for field, expected in _EXPECTED_BASENAMES.items():
        if paths[field].name != expected:
            raise ValueError(f"{field} has the wrong v2 artifact name")
    if paths["source"].suffix.lower() not in _SOURCE_SUFFIXES:
        raise ValueError("source has an unsupported structure artifact name")
    bundle_parent = paths["manifest"].parent
    for field in ("result", "statistics"):
        if paths[field].parent != bundle_parent:
            raise ValueError(f"{field} does not belong to the manifest-attested bundle")
    return paths


def _validate_closed_marker(
    *,
    job_root: Path,
    marker: Path,
) -> tuple[str, str, str, str]:
    marker_root = marker.parent if marker.parent != Path(".") else Path.cwd()
    marker_payload = canonical_json_loads(_read_regular(marker_root, marker.name))
    paths = validate_marker(marker_payload)
    bundle_root = job_root.joinpath(*paths["manifest"].parent.parts)
    manifest = load_result_manifest(bundle_root)
    if manifest.get("schema_version") != 2:
        raise ValueError("marker does not reference a v2 result manifest")
    payloads = validate_result_manifest(bundle_root, manifest)

    request = canonical_json_loads(payloads["workflow_component_request_v2.json"])
    if paths["source"].as_posix() != request["source_artifact"]["relative_path"]:
        raise ValueError("marker source does not match the manifest-attested request authority")
    source_payload = _read_regular(job_root, paths["source"].as_posix())
    expected_source_sha256 = request["source_artifact"]["sha256"]
    if hashlib.sha256(source_payload).hexdigest() != expected_source_sha256:
        raise ValueError("marker source bytes contradict the manifest-attested source authority")

    return (
        paths["result"].as_posix(),
        paths["manifest"].as_posix(),
        paths["source"].as_posix(),
        paths["statistics"].as_posix(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("markers", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        outputs: list[str] = []
        for marker in sorted(args.markers, key=lambda path: path.as_posix()):
            outputs.extend(_validate_closed_marker(job_root=args.job_root, marker=marker))
    except (OSError, ValueError, TypeError, ManifestValidationError) as exc:
        print(f"invalid FrustraMPNN publication marker: {exc}", file=sys.stderr)
        return 2
    print("\n".join(outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
