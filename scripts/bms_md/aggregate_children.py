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


def validate_collection_receipt(
    status: dict[str, Any], receipt_path: Path, schema: str,
) -> dict[str, Any]:
    """Bind native collection to the exact submitted set, including failed lanes."""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    children = receipt.get("children")
    if receipt.get("schema") != schema or not receipt.get("parent_job_id") or not isinstance(children, list) or not children:
        raise ValueError("invalid MD spawn receipt")
    ids = [child.get("id") for child in children if isinstance(child, dict)]
    indices = [child.get("replica_index") for child in children if isinstance(child, dict)]
    if (
        len(ids) != len(children)
        or any(not isinstance(value, str) or not value for value in ids)
        or len(set(ids)) != len(ids)
        or any(isinstance(index, bool) or not isinstance(index, int) or index < 0 for index in indices)
        or len(set(indices)) != len(indices)
    ):
        raise ValueError("invalid or duplicate MD spawn identity")
    observed = status.get("child_ids") or []
    directories = [str(Path(value).expanduser().resolve()) for value in status.get("child_output_dirs") or []]
    if (
        set(observed) != set(ids) or len(observed) != len(ids)
        or status.get("total") != len(ids)
        or sum(int(status.get(key) or 0) for key in ("completed", "execution_finished", "failed", "cancelled")) != len(ids)
        or int(status.get("completed") or 0) + int(status.get("execution_finished") or 0) != len(directories)
        or len(set(directories)) != len(directories)
    ):
        raise ValueError("MD collection does not match the exact terminal child set")
    return receipt


def collect_children(
    child_status_path: Path, output_dir: Path, *, spawn_receipt: Path | None = None,
) -> dict[str, Any]:
    status = json.loads(child_status_path.read_text(encoding="utf-8"))
    receipt = (
        validate_collection_receipt(status, spawn_receipt, "bms.md.replica-spawn.v1")
        if spawn_receipt is not None else None
    )
    child_dirs = [Path(value).expanduser().resolve() for value in status.get("child_output_dirs") or []]
    if not child_dirs:
        raise ValueError("no completed MD child output directories were supplied")

    source_manifests = [_find_run_manifest(child_dir) for child_dir in child_dirs]
    # Validate native identity before publishing any lane into the parent.
    aggregate = aggregate_manifests(source_manifests)
    from scripts.child_job_utils import component_runtime_enabled, seal_validated_child_files
    use_runtime = component_runtime_enabled()
    validated_files = {}
    if use_runtime:
        if receipt is None:
            raise ValueError("runtime MD collection requires its exact spawn receipt")
        from scripts.lib.component_adapter import runtime_from_environment
        runtime = runtime_from_environment()
        expected_ids = [child['id'] for child in receipt['children']]
        if tuple(expected_ids) != runtime.group_children(f"{receipt['parent_job_id']}:md_replica"):
            raise ValueError('MD collector receipt is not the current exact required replica set')
        observed_dirs = {
            str(Path(row['output_dir']).resolve()) for row in
            (runtime.child_status(identity) for identity in expected_ids)
            if row['status'] in {'completed', 'execution_finished'} and row['output_dir']}
        if observed_dirs != {str(path) for path in child_dirs}:
            raise ValueError('MD collector directories do not belong to its exact attempts')
        from .analysis import _verify_artifact
        for source_manifest in source_manifests:
            run = json.loads(source_manifest.read_bytes())
            validated_files[source_manifest] = [source_manifest] + [
                _verify_artifact(source_manifest.parent, record)[0]
                for record in run["artifacts"].values()]
    if receipt is not None:
        expected = {child["replica_index"]: child for child in receipt["children"]}
        if set(expected) != set(range(receipt["replica_count"])):
            raise ValueError("MD spawn receipt does not cover its declared replicas")
        for source_manifest in source_manifests:
            run = json.loads(source_manifest.read_text(encoding="utf-8"))
            lane = expected.get(run["replica_index"])
            if (
                lane is None or run.get("job_id") != receipt["parent_job_id"]
                or run.get("replica_seed") != lane.get("replica_seed")
                or not isinstance(run.get("engine"), dict)
                or run["engine"].get("name") != receipt["engine"]
            ):
                raise ValueError("MD replica output does not match submitted native identity")

    output_dir.mkdir(parents=True, exist_ok=True)
    for source_manifest in source_manifests:
        run_manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
        replica_index = int(run_manifest["replica_index"])
        source_replica_dir = source_manifest.parent
        target_replica_dir = output_dir / "replicas" / f"replica_{replica_index}"
        publish_tree_immutable(source_replica_dir, target_replica_dir)
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
    if use_runtime:
        for child_dir, source_manifest in zip(child_dirs, source_manifests, strict=True):
            run = json.loads(source_manifest.read_bytes())
            child_id = expected[run["replica_index"]]["id"]
            seal_validated_child_files(child_id, output_dir=child_dir, result=run,
                                      files=validated_files[source_manifest], role="md-native-replica")
        if aggregate["status"] == "completed":
            from scripts.lib.component_adapter import join_children
            join_children([child["id"] for child in receipt["children"]])
    return aggregate


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect durable MD replica child outputs")
    parser.add_argument("--child-status", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--spawn-receipt", type=Path)
    args = parser.parse_args()

    collect_children(args.child_status, args.output_dir, spawn_receipt=args.spawn_receipt)
    print(args.output_dir / "manifest.json")


if __name__ == "__main__":
    main()
