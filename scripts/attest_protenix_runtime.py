#!/usr/bin/env python3
"""Measure the Protenix image, checkpoint, installed backend source, and wrapper."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class ProtenixRuntimeAttestationError(RuntimeError):
    """Observed execution identity does not satisfy the registered contract."""


def _sha256_file(path: Path) -> tuple[str, int]:
    file_fd, parent_fd, leaf, before, path_before = _open_pinned_file(path)
    try:
        if not stat.S_ISREG(before.st_mode):
            raise ProtenixRuntimeAttestationError(f"file is not a regular file: {path}")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(file_fd)
        path_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        visible_after = os.lstat(path)
        if not _same_identity(before, after) or not _same_identity(path_before, path_after) or not _same_identity(before, visible_after):
            raise ProtenixRuntimeAttestationError(f"file path or bytes changed during measurement: {path}")
        if size != before.st_size:
            raise ProtenixRuntimeAttestationError(f"file size changed during measurement: {path}")
        return digest.hexdigest(), size
    finally:
        os.close(file_fd)
        os.close(parent_fd)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _open_pinned_file(path: Path) -> tuple[int, int, str, os.stat_result, os.stat_result]:
    """Open a file without following any path component and pin its parent."""

    absolute = Path(os.path.abspath(path))
    parts = absolute.parts
    if len(parts) < 2 or parts[0] != os.sep:
        raise ProtenixRuntimeAttestationError(f"path is not absolute: {path}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(os.sep, directory_flags)
    file_fd: int | None = None
    try:
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
        leaf = parts[-1]
        file_fd = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        path_before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        return file_fd, parent_fd, leaf, opened, path_before
    except Exception:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)
        raise


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return all(
        getattr(left, field) == getattr(right, field)
        for field in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    )


def _read_stable_bytes(path: Path) -> bytes:
    file_fd, parent_fd, leaf, before, path_before = _open_pinned_file(path)
    try:
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_fd)
        path_after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        visible_after = os.lstat(path)
        if not _same_identity(before, after) or not _same_identity(path_before, path_after) or not _same_identity(before, visible_after):
            raise ProtenixRuntimeAttestationError(f"file path changed while reading: {path}")
        return b"".join(chunks)
    finally:
        os.close(file_fd)
        os.close(parent_fd)


def _safe_regular(path: Path, label: str) -> os.stat_result:
    file_fd: int | None = None
    parent_fd: int | None = None
    try:
        file_fd, parent_fd, _leaf, info, _path_before = _open_pinned_file(path)
    except OSError as exc:
        raise ProtenixRuntimeAttestationError(f"{label} is unavailable: {path}") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if parent_fd is not None:
            os.close(parent_fd)
    if not stat.S_ISREG(info.st_mode):
        raise ProtenixRuntimeAttestationError(f"{label} is not a safe regular file: {path}")
    return info


def _validate_image_receipt(
    registry: Mapping[str, Any], receipt_path: Path, runtime_image: Path
) -> dict[str, Any]:
    _safe_regular(receipt_path, "runtime image receipt")
    try:
        receipt = json.loads(_read_stable_bytes(receipt_path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtenixRuntimeAttestationError("runtime image receipt is invalid") from exc
    required = {
        "schema_name",
        "schema_version",
        "status",
        "measurement_method",
        "expected_sha256",
        "observed_source",
        "verified_snapshot",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ProtenixRuntimeAttestationError("runtime image receipt shape is invalid")
    if (
        receipt["schema_name"] != "cm_runtime_image_receipt"
        or receipt["schema_version"] != 1
        or receipt["status"] != "verified_immutable_snapshot"
    ):
        raise ProtenixRuntimeAttestationError("runtime image receipt status is not verified")
    expected = str(registry.get("container_digest") or "").removeprefix("sha256:")
    observed = receipt.get("observed_source")
    snapshot = receipt.get("verified_snapshot")
    if not isinstance(observed, dict) or not isinstance(snapshot, dict):
        raise ProtenixRuntimeAttestationError("runtime image receipt identities are invalid")
    receipt_digests = {
        receipt.get("expected_sha256"),
        observed.get("sha256"),
        snapshot.get("sha256"),
    }
    if len(receipt_digests) != 1 or expected not in receipt_digests or not SHA256_RE.fullmatch(expected):
        raise ProtenixRuntimeAttestationError("runtime image receipt digest differs from registry")
    image_info = _safe_regular(runtime_image, "verified runtime image")
    image_sha256, image_bytes = _sha256_file(runtime_image)
    if (
        image_sha256 != expected
        or image_bytes != snapshot.get("bytes")
        or image_info.st_size != image_bytes
    ):
        raise ProtenixRuntimeAttestationError(
            "verified runtime image bytes differ from the host preflight receipt"
        )
    receipt_sha256, receipt_bytes = _sha256_file(receipt_path)
    return {
        "sha256": image_sha256,
        "bytes": image_bytes,
        "measurement": "verified_host_snapshot_rehashed_inside_executing_container",
        "host_observed_source": observed,
        "host_verified_snapshot": snapshot,
        "receipt": {
            "schema_name": receipt["schema_name"],
            "schema_version": receipt["schema_version"],
            "status": receipt["status"],
            "sha256": receipt_sha256,
            "bytes": receipt_bytes,
        },
    }


def _validate_execution_receipt(
    receipt_path: Path, checkpoint: Path, wrapper: Path
) -> dict[str, Any]:
    _safe_regular(receipt_path, "execution snapshot receipt")
    try:
        receipt = json.loads(_read_stable_bytes(receipt_path).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtenixRuntimeAttestationError("execution snapshot receipt is invalid") from exc
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"schema_name", "schema_version", "status", "checkpoint", "wrapper"}
        or receipt.get("schema_name") != "cm_protenix_execution_snapshot"
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "verified_before_execution"
    ):
        raise ProtenixRuntimeAttestationError("execution snapshot receipt shape/status is invalid")
    for label, path in (("checkpoint", checkpoint), ("wrapper", wrapper)):
        record = receipt.get(label)
        snapshot = record.get("verified_snapshot") if isinstance(record, Mapping) else None
        if not isinstance(snapshot, Mapping):
            raise ProtenixRuntimeAttestationError(f"execution {label} receipt is malformed")
        digest, size = _sha256_file(path)
        if snapshot.get("sha256") != digest or snapshot.get("bytes") != size:
            raise ProtenixRuntimeAttestationError(
                f"executed {label} bytes differ from the pre-execution snapshot receipt"
            )
    receipt_sha256, receipt_bytes = _sha256_file(receipt_path)
    return {"receipt": receipt, "sha256": receipt_sha256, "bytes": receipt_bytes}


def _source_manifest(source_roots: Sequence[Path]) -> dict[str, Any]:
    roots: list[Path] = []
    for root in source_roots:
        absolute = Path(os.path.abspath(root))
        root_info = os.lstat(absolute)
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise ProtenixRuntimeAttestationError(
                f"backend source root is not a safe directory: {root}"
            )
        if absolute not in roots:
            roots.append(absolute)
    if not roots:
        raise ProtenixRuntimeAttestationError("no executed Protenix source roots were observed")
    records: list[dict[str, Any]] = []
    for root_index, root in enumerate(sorted(roots, key=lambda value: str(value))):
        prefix = f"root_{root_index}_{root.name}"
        paths = sorted(root.rglob("*"), key=lambda value: value.as_posix())
        for path in paths:
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            path_info = os.lstat(path)
            if stat.S_ISLNK(path_info.st_mode):
                raise ProtenixRuntimeAttestationError(
                    f"backend source contains a symlink: {path}"
                )
            if not stat.S_ISREG(path_info.st_mode):
                continue
            digest, size = _sha256_file(path)
            records.append(
                {
                    "relative_path": f"{prefix}/{path.relative_to(root).as_posix()}",
                    "sha256": digest,
                    "bytes": size,
                }
            )
        current_paths = sorted(root.rglob("*"), key=lambda value: value.as_posix())
        if [path.relative_to(root).as_posix() for path in paths] != [path.relative_to(root).as_posix() for path in current_paths]:
            raise ProtenixRuntimeAttestationError("backend source tree changed during measurement")
    if not records:
        raise ProtenixRuntimeAttestationError("executed Protenix source roots are empty")
    return {
        "measurement": "deterministic_regular_file_manifest_sha256",
        "manifest_sha256": _canonical_sha256(records),
        "files": records,
    }


def _observed_commit(direct_url: Mapping[str, Any]) -> str:
    vcs_info = direct_url.get("vcs_info")
    commit = vcs_info.get("commit_id") if isinstance(vcs_info, Mapping) else None
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit.lower()):
        raise ProtenixRuntimeAttestationError(
            "installed Protenix direct_url has no exact Git commit identity"
        )
    return commit.lower()


def build_runtime_attestation(
    *,
    registry: Mapping[str, Any],
    image_receipt_path: Path,
    runtime_image: Path,
    checkpoint: Path,
    source_roots: Sequence[Path],
    direct_url: Mapping[str, Any],
    distribution_version: str,
    wrapper: Path,
    execution_receipt_path: Path,
    command: Sequence[str] = ("run_protenix_inference.py",),
    global_artifacts: Sequence[Mapping[str, Any]] = (),
    started_at: str | None = None,
    completed_at: str | None = None,
    model_name: str = "protenix-v2",
) -> dict[str, Any]:
    runtime_image_identity = _validate_image_receipt(
        registry, image_receipt_path, runtime_image
    )
    _safe_regular(checkpoint, "Protenix checkpoint")
    checkpoint_sha256, checkpoint_bytes = _sha256_file(checkpoint)
    expected_checkpoint = str(registry.get("checkpoint_sha256") or "")
    if checkpoint_sha256 != expected_checkpoint or not SHA256_RE.fullmatch(checkpoint_sha256):
        raise ProtenixRuntimeAttestationError(
            "observed Protenix checkpoint digest differs from registry"
        )
    source = _source_manifest(source_roots)
    commit = _observed_commit(direct_url)
    expected_commit = str(registry.get("backend_commit") or "").lower()
    if commit != expected_commit:
        raise ProtenixRuntimeAttestationError(
            "observed Protenix source commit differs from registry"
        )
    if model_name != registry.get("model_id"):
        raise ProtenixRuntimeAttestationError("executed Protenix model identity differs from registry")
    _safe_regular(wrapper, "executed Protenix wrapper")
    wrapper_sha256, wrapper_bytes = _sha256_file(wrapper)
    execution_snapshot = _validate_execution_receipt(
        execution_receipt_path, checkpoint, wrapper
    )
    version = str(distribution_version).strip()
    if not version:
        raise ProtenixRuntimeAttestationError("observed Protenix distribution version is empty")
    runtime_identity = (
        f"apptainer-sif-sha256:{runtime_image_identity['sha256']}"
        f"+checkpoint-sha256:{checkpoint_sha256}"
        f"+protenix-source-sha256:{source['manifest_sha256']}"
        f"+wrapper-sha256:{wrapper_sha256}"
    )
    attestation = {
        "schema_name": "cm_protenix_runtime_attestation",
        "schema_version": 1,
        "status": "observed_and_verified",
        "runtime_image": runtime_image_identity,
        "execution_snapshot": execution_snapshot,
        "checkpoint": {
            "sha256": checkpoint_sha256,
            "bytes": checkpoint_bytes,
            "relative_path": registry.get("checkpoint_relative_path"),
        },
        "backend_source": {
            "distribution": "protenix",
            "distribution_version": version,
            "commit": commit,
            **source,
        },
        "executed_wrapper": {
            "relative_path": wrapper.name,
            "sha256": wrapper_sha256,
            "bytes": wrapper_bytes,
        },
        "backend_version": version,
        "backend_commit": commit,
        "runtime_identity": runtime_identity,
        "container_digest": f"sha256:{runtime_image_identity['sha256']}",
        "checkpoint_sha256": checkpoint_sha256,
        "model_id": model_name,
        "started_at": started_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "completed_at": completed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "command": [str(value) for value in command],
        "global_artifacts": [dict(value) for value in global_artifacts],
    }
    attestation["attestation_sha256"] = _canonical_sha256(attestation)
    return attestation


def _discover_backend() -> tuple[list[Path], dict[str, Any], str]:
    roots: list[Path] = []
    for module_name in ("protenix", "runner"):
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            raise ProtenixRuntimeAttestationError(
                f"executed backend module is unavailable: {module_name}"
            )
        if spec.submodule_search_locations:
            roots.extend(Path(value) for value in spec.submodule_search_locations)
        elif spec.origin:
            roots.append(Path(spec.origin).parent)
        else:
            raise ProtenixRuntimeAttestationError(
                f"executed backend module has no measurable source: {module_name}"
            )
    try:
        distribution = importlib.metadata.distribution("protenix")
        direct_url_text = distribution.read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ProtenixRuntimeAttestationError(
            "executed Protenix distribution metadata is unavailable"
        ) from exc
    if not direct_url_text:
        raise ProtenixRuntimeAttestationError(
            "executed Protenix distribution has no direct_url commit metadata"
        )
    try:
        direct_url = json.loads(direct_url_text)
    except json.JSONDecodeError as exc:
        raise ProtenixRuntimeAttestationError(
            "executed Protenix direct_url metadata is malformed"
        ) from exc
    return roots, direct_url, distribution.version


def _write_output(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o440,
    )
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while publishing runtime attestation")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--image-receipt", type=Path, required=True)
    parser.add_argument("--runtime-image", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--execution-receipt", type=Path, required=True)
    parser.add_argument("--command-json", type=Path, required=True)
    parser.add_argument("--global-artifacts-json", type=Path, required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--model-name", default="protenix-v2")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        registry = json.loads(_read_stable_bytes(args.registry).decode("utf-8"))
        command = json.loads(_read_stable_bytes(args.command_json).decode("utf-8"))
        global_artifacts = json.loads(_read_stable_bytes(args.global_artifacts_json).decode("utf-8"))
        if not isinstance(command, list) or not command or not all(isinstance(value, str) for value in command):
            raise ProtenixRuntimeAttestationError("executed command record is malformed")
        if not isinstance(global_artifacts, list):
            raise ProtenixRuntimeAttestationError("global artifact record is malformed")
        roots, direct_url, version = _discover_backend()
        attestation = build_runtime_attestation(
            registry=registry,
            image_receipt_path=args.image_receipt,
            runtime_image=args.runtime_image,
            checkpoint=args.checkpoint,
            source_roots=roots,
            direct_url=direct_url,
            distribution_version=version,
            wrapper=args.wrapper,
            execution_receipt_path=args.execution_receipt,
            command=command,
            global_artifacts=global_artifacts,
            started_at=args.started_at,
            model_name=args.model_name,
        )
        _write_output(args.output, attestation)
    except (
        OSError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        ProtenixRuntimeAttestationError,
    ) as exc:
        parser.exit(2, f"Protenix runtime attestation failed: {exc}\n")


if __name__ == "__main__":
    main()
