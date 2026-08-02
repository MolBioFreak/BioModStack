"""Neutral hardened runtime identity, descriptor pinning, and invocation building."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class RuntimeValidationError(RuntimeError):
    """A FrustraMPNN runtime identity or invocation boundary is unsafe."""


@dataclass(frozen=True)
class FrustraMPNNRuntimeIdentity:
    sif_name: str
    configured_sif_path: str
    sif_sha256: str
    executable_path: str
    executable_sha256: str
    checkpoint_id: str
    checkpoint_path: str
    checkpoint_sha256: str
    package_version: str
    source_commit: str
    python_version: str
    pytorch_version: str
    image_version: str


FRUSTRAMPNN_RUNTIME_IDENTITY = FrustraMPNNRuntimeIdentity(
    sif_name="frustrampnn.sif",
    configured_sif_path="/mnt/BioModStack/apptainer/frustrampnn.sif",
    sif_sha256="c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da",
    executable_path="/opt/venv/bin/frustrampnn",
    executable_sha256="32089d959f619c08a550c0e7d0fc7b66b508d009ec3179d007f13773a170212f",
    checkpoint_id="megascale.ckpt",
    checkpoint_path="/opt/frustrampnn_weights/megascale.ckpt",
    checkpoint_sha256="eaee71adb7eec366fc672d2aadef87f2c51243042a4518cd897634784dc2da3b",
    package_version="1.0.0",
    source_commit="bbae1d03edf33dbe6f645d45c5604eb4464962ca",
    python_version="3.10.12",
    pytorch_version="2.11.0.dev20260126+cu128",
    image_version="1.3",
)


def runtime_identity_dict(
    identity: FrustraMPNNRuntimeIdentity = FRUSTRAMPNN_RUNTIME_IDENTITY,
) -> dict[str, str]:
    return asdict(identity)


def _immutable_mapping(value: Mapping[str, Any]) -> MappingProxyType:
    return MappingProxyType(
        {
            key: _immutable_mapping(item) if isinstance(item, Mapping) else item
            for key, item in value.items()
        }
    )


FRUSTRAMPNN_RUNTIME_REGISTRY = _immutable_mapping(
    {
        "schema_name": "frustrampnn_runtime_registry",
        "schema_version": 1,
        "component_id": "frustrampnn",
        "runtime_identity": runtime_identity_dict(),
    }
)


def _validate_digest(value: object, *, label: str) -> str:
    digest = str(value or "")
    if _SHA256_RE.fullmatch(digest) is None:
        raise RuntimeValidationError(f"{label} SHA-256 is malformed")
    return digest


def _lexical_parts(path: Path | str, *, label: str) -> tuple[str, tuple[str, ...]]:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise RuntimeValidationError(f"unsafe lexical {label} path: {raw!r}")
    absolute = raw.startswith("/")
    body = raw[1:] if absolute else raw
    parts = tuple(body.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeValidationError(f"unsafe lexical {label} path component: {raw!r}")
    return "/" if absolute else ".", parts


def _open_directory_no_follow(path: Path | str, *, label: str) -> int:
    anchor, parts = _lexical_parts(path, label=label)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(anchor, flags)
    try:
        for part in parts:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise RuntimeValidationError(
            f"cannot open {label} path without following symlinks: {path}"
        ) from exc


def open_regular_no_follow(path: Path | str, *, label: str) -> int:
    anchor, parts = _lexical_parts(path, label=label)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    leaf_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    parent = os.open(anchor, directory_flags)
    descriptor = -1
    try:
        for part in parts[:-1]:
            next_parent = os.open(part, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = next_parent
        descriptor = os.open(parts[-1], leaf_flags, dir_fd=parent)
    except OSError as exc:
        raise RuntimeValidationError(
            f"cannot open {label} path without following symlinks: {path}"
        ) from exc
    finally:
        os.close(parent)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeValidationError(f"{label} must be a regular file")
    return descriptor


def sha256_fd(descriptor: int) -> str:
    """Hash exact bytes from an already-open regular-file descriptor."""

    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise RuntimeValidationError("cannot inspect runtime descriptor") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeValidationError("runtime descriptor must reference a regular file")
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.pread(descriptor, 1024 * 1024, offset):
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


class PinnedContainer:
    """One verified SIF generation retained by descriptor until explicitly closed."""

    def __init__(self, descriptor: int, sha256: str):
        self.fd = descriptor
        self.sha256 = sha256
        self._closed = False

    @property
    def proc_path(self) -> Path:
        return Path(f"/proc/self/fd/{self.fd}")

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if not self._closed:
            os.close(self.fd)
            self._closed = True

    def detach(self) -> tuple[int, str]:
        if self._closed:
            raise RuntimeValidationError("verified FrustraMPNN container is already closed")
        self._closed = True
        return self.fd, self.sha256

    def __enter__(self) -> PinnedContainer:
        if self._closed:
            raise RuntimeValidationError("verified FrustraMPNN container is already closed")
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass


def open_verified_container(path: Path | str, expected_sha256: object) -> PinnedContainer:
    """Lexically validate, no-follow open, hash, and pin one SIF generation."""

    expected = _validate_digest(expected_sha256, label="registered FrustraMPNN image")
    descriptor = open_regular_no_follow(path, label="FrustraMPNN container")
    try:
        actual = sha256_fd(descriptor)
        if actual != expected:
            raise RuntimeValidationError(
                "registered FrustraMPNN image SHA-256 does not match installed bytes"
            )
        return PinnedContainer(descriptor, actual)
    except Exception:
        os.close(descriptor)
        raise


def validate_configured_container_path(
    path: Path | str,
    *,
    identity: FrustraMPNNRuntimeIdentity = FRUSTRAMPNN_RUNTIME_IDENTITY,
) -> str:
    """Require the exact centrally registered host path before descriptor pinning."""

    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise RuntimeValidationError("configured FrustraMPNN container path is invalid")
    configured = os.path.abspath(raw)
    if configured != identity.configured_sif_path:
        raise RuntimeValidationError(
            "configured FrustraMPNN container path does not match the central runtime registry"
        )
    return configured


def cm_analysis_runtime_registry_v1(container_dir: Path | str) -> dict[str, str]:
    """Return CM's stable v1 projection from the neutral central registry."""

    raw_root = os.fspath(container_dir)
    separator = "" if raw_root.endswith("/") else "/"
    image_path = f"{raw_root}{separator}{FRUSTRAMPNN_RUNTIME_IDENTITY.sif_name}"
    descriptor = open_regular_no_follow(image_path, label="registered FrustraMPNN container")
    os.close(descriptor)
    return {
        "container_name": FRUSTRAMPNN_RUNTIME_IDENTITY.sif_name,
        "container_sha256": FRUSTRAMPNN_RUNTIME_IDENTITY.sif_sha256,
    }


def _validate_container_internal_path(path: Path | str, *, label: str) -> str:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw.startswith("/") or "\\" in raw or "\x00" in raw:
        raise RuntimeValidationError(f"{label} must be an absolute container path")
    parsed = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in raw[1:].split("/")) or str(parsed) != raw:
        raise RuntimeValidationError(f"{label} has unsafe lexical path components")
    return raw


def container_sha256(
    apptainer: Path | str,
    container: Path | str,
    internal_path: Path | str,
    *,
    container_fd: int | None = None,
) -> str:
    """Hash one in-container asset through the inherited pinned SIF descriptor."""

    executable = os.fspath(apptainer)
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise RuntimeValidationError("Apptainer executable is invalid")
    target = _validate_container_internal_path(internal_path, label="container asset")
    pass_fds: tuple[int, ...] = ()
    if container_fd is not None:
        if isinstance(container_fd, bool) or not isinstance(container_fd, int) or container_fd < 0:
            raise RuntimeValidationError("pinned container descriptor is invalid")
        expected_container = f"/proc/self/fd/{container_fd}"
        if os.fspath(container) != expected_container:
            raise RuntimeValidationError("container path does not match the pinned descriptor")
        pass_fds = (container_fd,)
    try:
        result = subprocess.run(
            [executable, "exec", os.fspath(container), "sha256sum", target],
            check=True,
            capture_output=True,
            text=True,
            pass_fds=pass_fds,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeValidationError(f"cannot authenticate container asset: {target}") from exc
    digest = result.stdout.split(maxsplit=1)[0] if result.stdout.strip() else ""
    return _validate_digest(digest, label=f"container asset {target}")


def verify_container_assets(
    apptainer: Path | str,
    pinned: PinnedContainer,
    *,
    identity: FrustraMPNNRuntimeIdentity = FRUSTRAMPNN_RUNTIME_IDENTITY,
) -> dict[str, str]:
    if pinned.closed:
        raise RuntimeValidationError("verified FrustraMPNN container is already closed")
    executable_sha256 = container_sha256(
        apptainer,
        pinned.proc_path,
        identity.executable_path,
        container_fd=pinned.fd,
    )
    if executable_sha256 != _validate_digest(
        identity.executable_sha256, label="registered FrustraMPNN executable"
    ):
        raise RuntimeValidationError(
            "FrustraMPNN executable SHA-256 does not match the central runtime registry"
        )
    checkpoint_sha256 = container_sha256(
        apptainer,
        pinned.proc_path,
        identity.checkpoint_path,
        container_fd=pinned.fd,
    )
    if checkpoint_sha256 != _validate_digest(
        identity.checkpoint_sha256, label="registered FrustraMPNN checkpoint"
    ):
        raise RuntimeValidationError(
            "FrustraMPNN checkpoint SHA-256 does not match the central runtime registry"
        )
    return {
        "executable_sha256": executable_sha256,
        "checkpoint_sha256": checkpoint_sha256,
    }


@dataclass(frozen=True)
class FrustraMPNNInvocation:
    argv: tuple[str, ...]
    physical_gpu_id: int
    task_visible_gpu_id: int = 0

    @property
    def receipt_metadata(self) -> dict[str, int]:
        return {
            "physical_gpu_id": self.physical_gpu_id,
            "task_visible_gpu_id": self.task_visible_gpu_id,
        }


def execute_frustrampnn(
    invocation: FrustraMPNNInvocation,
    pinned_container: PinnedContainer,
    *,
    stdout: Any = None,
    stderr: Any = None,
    timeout: float | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[Any]:
    """Execute one validated invocation through its pinned SIF generation.

    This is the sole neutral execution boundary used by workflow components and
    conformational mapping. Callers normalize and project their own contracts,
    but never reconstruct Apptainer or GPU execution policy.
    """

    if not isinstance(invocation, FrustraMPNNInvocation):
        raise RuntimeValidationError("FrustraMPNN invocation authority is missing")
    if not isinstance(pinned_container, PinnedContainer) or pinned_container.closed:
        raise RuntimeValidationError("verified FrustraMPNN container is unavailable")
    pinned_path = os.fspath(pinned_container.proc_path)
    if pinned_path not in invocation.argv:
        raise RuntimeValidationError("FrustraMPNN invocation is not bound to the pinned container")
    if invocation.task_visible_gpu_id != 0:
        raise RuntimeValidationError("FrustraMPNN task-visible GPU must be 0")
    return subprocess.run(
        list(invocation.argv),
        stdout=stdout,
        stderr=stderr,
        timeout=timeout,
        check=check,
        pass_fds=(pinned_container.fd,),
    )


def _absolute_safe_host_path(path: Path | str, *, label: str) -> Path:
    anchor, parts = _lexical_parts(path, label=label)
    if anchor != "/":
        raw = os.path.abspath(os.fspath(path))
        anchor, parts = _lexical_parts(raw, label=label)
    result = Path(anchor).joinpath(*parts)
    raw_result = os.fspath(result)
    if any(character in raw_result for character in (":", ",", "\n", "\r")):
        raise RuntimeValidationError(f"unsafe Apptainer bind path for {label}")
    return result


def build_frustrampnn_command(
    *,
    apptainer: Path | str,
    container: Path | str,
    normalized: Path | str,
    raw: Path | str,
    output_root: Path | str,
    physical_gpu_id: object,
    tool: Path | str = FRUSTRAMPNN_RUNTIME_IDENTITY.executable_path,
    checkpoint: Path | str = FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path,
) -> FrustraMPNNInvocation:
    """Build the exact scheduler-to-container GPU-safe FrustraMPNN invocation."""

    if (
        isinstance(physical_gpu_id, bool)
        or not isinstance(physical_gpu_id, int)
        or physical_gpu_id < 0
    ):
        raise RuntimeValidationError("assigned FrustraMPNN physical GPU ID must be a non-negative integer")
    executable = os.fspath(apptainer)
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise RuntimeValidationError("Apptainer executable is invalid")
    container_path = _absolute_safe_host_path(container, label="container")
    normalized_path = _absolute_safe_host_path(normalized, label="normalized input")
    output_path = _absolute_safe_host_path(output_root, label="output root")
    raw_path = _absolute_safe_host_path(raw, label="raw output")
    input_descriptor = open_regular_no_follow(normalized_path, label="normalized input")
    os.close(input_descriptor)
    output_descriptor = _open_directory_no_follow(output_path, label="output root")
    os.close(output_descriptor)
    try:
        raw_relative = raw_path.relative_to(output_path)
    except ValueError as exc:
        raise RuntimeValidationError("raw output path must remain beneath output root") from exc
    if not raw_relative.parts or any(part in {"", ".", ".."} for part in raw_relative.parts):
        raise RuntimeValidationError("raw output path is invalid")
    try:
        normalized_path.relative_to(output_path)
    except ValueError:
        pass
    else:
        raise RuntimeValidationError("normalized input and writable output bind paths collide")
    if normalized_path == raw_path:
        raise RuntimeValidationError("normalized input and raw output paths collide")
    tool_path = _validate_container_internal_path(tool, label="FrustraMPNN executable")
    checkpoint_path = _validate_container_internal_path(checkpoint, label="FrustraMPNN checkpoint")
    if tool_path != FRUSTRAMPNN_RUNTIME_IDENTITY.executable_path:
        raise RuntimeValidationError("FrustraMPNN executable path does not match the central registry")
    if checkpoint_path != FRUSTRAMPNN_RUNTIME_IDENTITY.checkpoint_path:
        raise RuntimeValidationError("FrustraMPNN checkpoint path does not match the central registry")
    contained_output = PurePosixPath("/bms/output").joinpath(*raw_relative.parts)
    argv = (
        executable,
        "exec",
        "--containall",
        "--writable-tmpfs",
        "--nv",
        "--env",
        "CUDA_DEVICE_ORDER=PCI_BUS_ID",
        "--env",
        f"CUDA_VISIBLE_DEVICES={physical_gpu_id}",
        "--bind",
        f"{normalized_path}:/bms/input/normalized.pdb:ro",
        "--bind",
        f"{output_path}:/bms/output:rw",
        os.fspath(container_path),
        tool_path,
        "predict",
        "--pdb",
        "/bms/input/normalized.pdb",
        "--checkpoint",
        checkpoint_path,
        "--output",
        str(contained_output),
        "--device",
        "cuda",
    )
    return FrustraMPNNInvocation(argv=argv, physical_gpu_id=physical_gpu_id)


__all__ = [
    "FRUSTRAMPNN_RUNTIME_IDENTITY",
    "FRUSTRAMPNN_RUNTIME_REGISTRY",
    "FrustraMPNNInvocation",
    "FrustraMPNNRuntimeIdentity",
    "PinnedContainer",
    "RuntimeValidationError",
    "build_frustrampnn_command",
    "cm_analysis_runtime_registry_v1",
    "container_sha256",
    "execute_frustrampnn",
    "open_verified_container",
    "runtime_identity_dict",
    "sha256_fd",
    "verify_container_assets",
]
