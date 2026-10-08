#!/usr/bin/env python3
"""Publish every success or classified failure from one grouped FrustraMPNN task."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.frustrampnn.contracts import canonical_json_bytes, canonical_json_loads, validate_schema  # noqa: E402
from services.frustrampnn.manifests import _read_regular  # noqa: E402
from publish_frustrampnn_bundle import _contained_destination, publish  # noqa: E402

_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def publish_group(*, grouped_root: Path, job_root: Path, marker_root: Path) -> list[Path]:
    markers: list[Path] = []
    job_root, destination_root = _contained_destination(
        job_root, job_root / "frustrampnn" / "results"
    )
    destination_root.mkdir(parents=True, exist_ok=True)
    _contained_destination(job_root, destination_root)
    for source in sorted(grouped_root.iterdir(), key=lambda path: path.name):
        if source.is_symlink() or not source.is_dir() or _SAFE.fullmatch(source.name) is None:
            raise ValueError("grouped candidate bundle identity is unsafe")
        result = canonical_json_loads(_read_regular(source, "workflow_component_result_v3.json"))
        validate_schema("workflow_component_result_v3", result)
        if result["candidate_id"] != source.name:
            raise ValueError("grouped candidate directory contradicts terminal identity")
        marker = marker_root / f"published_{source.name}.json"
        destination = destination_root / source.name
        if result["status"] == "succeeded":
            publish(
                source_bundle=source, allowed_root=job_root,
                destination=destination, marker=marker,
            )
        else:
            _, destination = _contained_destination(job_root, destination)
            if destination.exists() or destination.is_symlink():
                raise ValueError("classified failure destination already exists")
            names = (
                "workflow_component_request_v3.json", "normalized_input.pdb",
                "frustrampnn_structure_map_v1.json", "frustrampnn_stdout.log",
                "frustrampnn_stderr.log", "workflow_component_result_v3.json",
            )
            payloads = {name: _read_regular(source, name) for name in names}
            request = canonical_json_loads(payloads["workflow_component_request_v3.json"])
            validate_schema("workflow_component_request_v3", request)
            source_relative = request["source_artifact"]["relative_path"]
            source_payload = _read_regular(job_root, source_relative)
            if hashlib.sha256(source_payload).hexdigest() != request["source_artifact"]["sha256"]:
                raise ValueError("classified failure source contradicts immutable request authority")
            temporary = Path(tempfile.mkdtemp(prefix=f".{source.name}.tmp-", dir=destination_root))
            try:
                for name, payload in payloads.items():
                    target = temporary / name
                    with target.open("xb") as handle:
                        handle.write(payload)
                        handle.flush()
                        os.fsync(handle.fileno())
                directory_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                os.rename(temporary, destination)
                parent_fd = os.open(destination_root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent_fd)
                finally:
                    os.close(parent_fd)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
            marker.write_bytes(canonical_json_bytes({
                "result": (destination / "workflow_component_result_v3.json").relative_to(job_root).as_posix(),
                "source": source_relative,
            }))
        markers.append(marker)
    if not markers:
        raise ValueError("grouped result set is empty")
    return markers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grouped-root", required=True, type=Path)
    parser.add_argument("--job-root", required=True, type=Path)
    parser.add_argument("--marker-root", default=Path("."), type=Path)
    args = parser.parse_args()
    try:
        publish_group(grouped_root=args.grouped_root, job_root=args.job_root, marker_root=args.marker_root)
    except Exception as exc:
        print(f"frustrampnn_grouped_publication_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
