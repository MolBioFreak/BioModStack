from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from .aggregate import aggregate_manifests


class ImmutableCollectionConflict(RuntimeError):
    code = "MD_IMMUTABLE_COLLECTION_CONFLICT"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_identity(root: Path) -> list[tuple[str, int, str]]:
    if not root.is_dir() or root.is_symlink():
        raise ImmutableCollectionConflict(f"immutable collection source is not a regular directory: {root}")
    identity: list[tuple[str, int, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ImmutableCollectionConflict(f"immutable collection contains a symbolic link: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ImmutableCollectionConflict(f"immutable collection contains a non-regular file: {path}")
        identity.append((path.relative_to(root).as_posix(), path.stat().st_size, _sha256(path)))
    return identity


def publish_tree_immutable(source: Path, destination: Path) -> None:
    """Create-if-absent directory publication with exact replay and typed conflict."""

    source_identity = _tree_identity(source)
    if destination.exists():
        if _tree_identity(destination) == source_identity:
            return
        raise ImmutableCollectionConflict(f"immutable collection destination conflicts: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, temporary, symlinks=False)
        try:
            os.rename(temporary, destination)
        except OSError:
            if destination.exists() and _tree_identity(destination) == source_identity:
                return
            raise ImmutableCollectionConflict(f"immutable collection destination conflicts: {destination}")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def publish_file_immutable(
    source: Path,
    destination: Path,
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> None:
    """Publish the exact regular-file descriptor that passed identity verification."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, flags)
    except OSError as exc:
        raise ImmutableCollectionConflict(f"immutable collection source cannot be opened safely: {source}") from exc
    temporary = destination.parent / f".{destination.name}.tmp-{uuid.uuid4().hex}"
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ImmutableCollectionConflict(f"immutable collection source is not a regular file: {source}")
        digest = hashlib.sha256()
        while chunk := os.read(source_fd, 1024 * 1024):
            digest.update(chunk)
        source_size = source_stat.st_size
        source_hash = digest.hexdigest()
        if expected_size is not None and source_size != expected_size:
            raise ImmutableCollectionConflict(f"immutable collection source size changed: {source}")
        if expected_sha256 is not None and source_hash != expected_sha256:
            raise ImmutableCollectionConflict(f"immutable collection source digest changed: {source}")
        if destination.exists():
            if (
                destination.is_file()
                and not destination.is_symlink()
                and destination.stat().st_size == source_size
                and _sha256(destination) == source_hash
            ):
                return
            raise ImmutableCollectionConflict(f"immutable collection destination conflicts: {destination}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        os.lseek(source_fd, 0, os.SEEK_SET)
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            stat.S_IMODE(source_stat.st_mode),
        )
        try:
            while chunk := os.read(source_fd, 1024 * 1024):
                view = memoryview(chunk)
                while view:
                    written = os.write(temporary_fd, view)
                    if written <= 0:
                        raise ImmutableCollectionConflict("immutable collection copy made no progress")
                    view = view[written:]
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            if (
                destination.is_file()
                and not destination.is_symlink()
                and destination.stat().st_size == source_size
                and _sha256(destination) == source_hash
            ):
                return
            raise ImmutableCollectionConflict(f"immutable collection destination conflicts: {destination}")
    finally:
        os.close(source_fd)
        temporary.unlink(missing_ok=True)


def publish_json_immutable(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.json-{uuid.uuid4().hex}"
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        publish_file_immutable(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _find_run_manifest(output_dir: Path) -> Path:
    matches: list[Path] = []
    for candidate in output_dir.rglob("manifest.json"):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("schema") == "bms.md.run.v1":
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError(f"expected exactly one bms.md.run.v1 manifest below {output_dir}, found {len(matches)}")
    return matches[0]


def collect_children(child_status_path: Path, output_dir: Path) -> dict[str, Any]:
    status = json.loads(child_status_path.read_text(encoding="utf-8"))
    child_dirs = [Path(value).expanduser().resolve() for value in status.get("child_output_dirs") or []]
    if not child_dirs:
        raise ValueError("no completed MD child output directories were supplied")

    output_dir.mkdir(parents=True, exist_ok=True)
    collected_manifests: list[Path] = []
    for child_dir in child_dirs:
        source_manifest = _find_run_manifest(child_dir)
        run_manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        replica_index = int(run_manifest["replica_index"])
        source_replica_dir = source_manifest.parent
        target_replica_dir = output_dir / "replicas" / f"replica_{replica_index}"
        publish_tree_immutable(source_replica_dir, target_replica_dir)
        collected_manifests.append(target_replica_dir / "manifest.json")

    aggregate = aggregate_manifests(collected_manifests)
    aggregate["lineage"] = {
        "total_children": int(status.get("total") or len(child_dirs)),
        "completed_children": int(status.get("completed") or len(child_dirs)),
        "failed_children": int(status.get("failed") or 0),
        "cancelled_children": int(status.get("cancelled") or 0),
        "child_ids": list(status.get("child_ids") or []),
    }
    if aggregate["lineage"]["failed_children"] or aggregate["lineage"]["cancelled_children"]:
        aggregate["status"] = "partial_failure"
    publish_json_immutable(aggregate, output_dir / "manifest.json")
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect durable MD replica child outputs")
    parser.add_argument("--child-status", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    collect_children(args.child_status, args.output_dir)
    print(args.output_dir / "manifest.json")


if __name__ == "__main__":
    main()
