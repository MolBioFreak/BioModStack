#!/usr/bin/env python3
"""Atomically publish one validated immutable FrustraMPNN candidate bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "platform" / "api"))

from services.frustrampnn.contracts import canonical_json_bytes, canonical_json_loads  # noqa: E402
from services.frustrampnn.manifests import (  # noqa: E402
    MANIFEST_PATH,
    _read_regular,
    load_result_manifest,
    result_manifest_path,
    validate_result_manifest,
)


def _contained_destination(root: Path, destination: Path) -> tuple[Path, Path]:
    root = root.absolute()
    destination = destination.absolute()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("allowed root must be an existing non-symlink directory")
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("destination escapes allowed root") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("destination is not canonical")
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.exists() and cursor.is_symlink():
            raise ValueError("destination parent contains a symlink")
    return root, destination


def _same_payloads(left: dict[str, bytes], right: dict[str, bytes]) -> bool:
    return left.keys() == right.keys() and all(left[key] == right[key] for key in left)


def _publish_source(*, payload: bytes, allowed_root: Path, relative_path: str) -> tuple[Path, bool]:
    target = allowed_root / relative_path
    root, target = _contained_destination(allowed_root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _contained_destination(root, target)
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise ValueError("existing canonical source is not a regular file")
        if _read_regular(root, relative_path) != payload:
            raise ValueError("existing canonical source contradicts immutable source authority")
        return target, False
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if _read_regular(root, relative_path) != payload:
                raise ValueError("existing canonical source contradicts immutable source authority")
            return target, False
    finally:
        if temporary.exists():
            temporary.unlink()
    return target, True


def publish(*, source_bundle: Path, allowed_root: Path, destination: Path, marker: Path) -> dict[str, str]:
    manifest_name = result_manifest_path(source_bundle)
    manifest = load_result_manifest(source_bundle)
    payloads = validate_result_manifest(
        source_bundle,
        manifest,
    )
    generation = manifest["schema_version"]
    request_name = f"workflow_component_request_v{generation}.json"
    result_name = f"workflow_component_result_v{generation}.json"
    root, destination = _contained_destination(allowed_root, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _contained_destination(root, destination)

    destination_created = False
    source_created = False
    source_target: Path | None = None
    marker_temporary: Path | None = None
    try:
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError("existing destination is not a regular directory")
            existing = validate_result_manifest(
                destination,
                load_result_manifest(destination),
            )
            if not _same_payloads(payloads, existing):
                raise ValueError("existing published bundle contradicts immutable candidate authority")
        else:
            temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
            try:
                for relative, payload in payloads.items():
                    target = temporary / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(payload)
                    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
                copied = validate_result_manifest(
                    temporary,
                    load_result_manifest(temporary),
                )
                if not _same_payloads(payloads, copied):
                    raise ValueError("published snapshot differs from retained validated bytes")
                os.rename(temporary, destination)
                destination_created = True
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)

        request = canonical_json_loads(payloads[request_name])
        source_relative = request["source_artifact"]["relative_path"]
        if generation == 1:
            source_target, source_created = _publish_source(
                payload=payloads["normalized_input.pdb"],
                allowed_root=root,
                relative_path=source_relative,
            )
        else:
            source_target = root / source_relative
            _contained_destination(root, source_target)
            expected_source_sha256 = request.get("source_artifact", {}).get("sha256")
            if (
                not isinstance(expected_source_sha256, str)
                or hashlib.sha256(_read_regular(root, source_relative)).hexdigest()
                != expected_source_sha256
            ):
                raise ValueError("existing canonical v2 source contradicts declared source authority")
        result = {
            # Stage-report authority is always relative to the exact job root.
            # This remains stable whether Nextflow passed an absolute out_dir or
            # a data-root-relative one and lets ingestion reject path escapes.
            "result": (
                destination / result_name
            ).relative_to(root).as_posix(),
            "manifest": (destination / manifest_name).relative_to(root).as_posix(),
            "source": source_target.relative_to(root).as_posix(),
        }
        if generation == 2:
            result["statistics"] = (
                destination / "frustrampnn_statistics_v1.json"
            ).relative_to(root).as_posix()
        marker.parent.mkdir(parents=True, exist_ok=True)
        descriptor, marker_name = tempfile.mkstemp(prefix=f".{marker.name}.tmp-", dir=marker.parent)
        marker_temporary = Path(marker_name)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(canonical_json_bytes(result))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(marker_temporary, marker)
        marker_temporary = None
        return result
    except Exception:
        if source_created and source_target is not None and source_target.exists():
            source_relative = source_target.relative_to(root).as_posix()
            if generation == 1 and _read_regular(root, source_relative) == payloads[
                "normalized_input.pdb"
            ]:
                source_target.unlink()
        if destination_created and destination.exists():
            shutil.rmtree(destination)
        raise
    finally:
        if marker_temporary is not None and marker_temporary.exists():
            marker_temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-bundle", required=True, type=Path)
    parser.add_argument("--allowed-root", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--marker", required=True, type=Path)
    args = parser.parse_args()
    try:
        publish(
            source_bundle=args.source_bundle,
            allowed_root=args.allowed_root,
            destination=args.destination,
            marker=args.marker,
        )
    except Exception as exc:
        print(f"frustrampnn_bundle_publication_error:{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
