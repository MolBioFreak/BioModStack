from __future__ import annotations

import asyncio
import array
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import time
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select, update

from database import (
    InputFile,
    Job,
    OntExternalMoveBamRegistrationReceipt,
    OntMoveTableSource,
    OntRawSignalRepresentation,
    OntSignalCalibrationArtifact,
    OntSignalCalibrationJob,
    OntSignalComparisonArtifact,
    OntSignalComparisonEvent,
    OntSignalComparisonJob,
    OntSignalMappingArtifact,
    OntSignalMappingEvent,
    OntSignalMappingJob,
    OntSignalMappingProfile,
    OntSquigualiserViewJob,
)
from molbio_ngs_models import MolBioNGSReferenceArtifact, MolBioNGSReferenceRevision
from paths import get_allowed_roots, get_molbio_ngs_reference_root, get_results_dir
from services import ngs_alignment_sessions, ont_raw_signal, ont_signal_workbench
from services.file_lease_signals import lease_break_generation

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300
MAX_FAILURE = 4000
MOVE_SOURCE_MAX_ATTEMPTS = 3
SIGNAL_JOB_MAX_ATTEMPTS = 3
HEX64 = re.compile(r"^[0-9a-f]{64}$")
WORKER_LABEL = "io.biomodstack.owner=ont-signal-worker"
RUNTIME_POLICY_PATH = (
    Path(__file__).resolve().parents[1]
    / "config"
    / "ont_signal_workbench"
    / "runtime_policy_v1.json"
)
RUNTIME_POLICY_SHA256 = "34d2af958f51539ba6d09e59f354798c0091f6e4d63428d3f5de0baa86ef6d37"
APPROVED_OCI_DIGEST = "sha256:4061ecf65ad8edbe909592e9e922ee089ee67260fbd8384da3321d0313d5e404"
APPROVED_UPSTREAM_COMMIT = "5a2404f1f43bc3227a85475c59b2b77970078b2e"
MAX_CONTAINER_LOG_BYTES = 8 * 1024 * 1024
COMMAND_DEADLINES = {
    "move": 2 * 60 * 60, "calibration": 2 * 60 * 60, "mapping": 4 * 60 * 60,
    "view": 15 * 60, "squigulator_producer": 5 * 60,
    "squigualiser_comparison_renderer": 15 * 60,
}
COMMAND_LOG_LIMITS = {
    "squigulator_producer": 4 * 1024 * 1024,
    "squigualiser_comparison_renderer": 8 * 1024 * 1024,
}
TOTAL_OUTPUT_LIMITS = {
    "move": 2 * 1024 * 1024 * 1024,
    "calibration": 2 * 1024 * 1024 * 1024,
    "mapping": 2 * 1024 * 1024 * 1024,
    "view": 64 * 1024 * 1024,
    "squigulator_producer": 32 * 1024 * 1024,
    "squigualiser_comparison_renderer": 64 * 1024 * 1024,
}
FILE_SIZE_LIMITS = {
    "move": 1536 * 1024 * 1024,
    "calibration": 1536 * 1024 * 1024,
    "mapping": 1536 * 1024 * 1024,
    "view": 48 * 1024 * 1024,
    "squigulator_producer": 16 * 1024 * 1024,
    "squigualiser_comparison_renderer": 48 * 1024 * 1024,
}
SQUIGULATOR_POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "ont_signal_workbench" / "squigulator_runtime_policy_v1.json"
COMPARISON_RENDER_POLICY_PATH = Path(__file__).resolve().parents[1] / "config" / "ont_signal_workbench" / "comparison_render_runtime_policy_v1.json"
COMPARISON_ARTIFACT_AUTHORITY = {
    "simulation_input_fasta": "comparison_derived", "simulation_coordinate_map": "comparison_derived",
    "simulated_blow5": "simulated_derived", "simulated_blow5_index": "simulated_derived",
    "simulated_read_fasta": "simulated_derived", "simulated_read_id_map": "simulated_derived",
    "simulated_source_paf": "simulated_derived", "simulated_normalized_paf": "comparison_derived",
    "simulated_source_sam": "simulated_derived", "simulated_normalized_sam": "comparison_derived",
    "comparison_html": "comparison_derived", "comparison_manifest": "comparison_derived",
}
COMPARISON_PRODUCER_FILENAMES = {
    "simulation_input.fasta": "simulation_input_fasta",
    "simulation_coordinate_map.json": "simulation_coordinate_map",
    "simulated.blow5": "simulated_blow5", "simulated.blow5.idx": "simulated_blow5_index",
    "simulated_reads.fasta": "simulated_read_fasta",
    "simulated_read_id_map.json": "simulated_read_id_map",
    "simulated_source.paf": "simulated_source_paf",
    "simulated_normalized.paf": "simulated_normalized_paf",
    "simulated_source.sam": "simulated_source_sam",
    "simulated_normalized.sam": "simulated_normalized_sam",
}


class ContainerCleanupError(RuntimeError):
    pass


class OutputLimitExceeded(RuntimeError):
    pass


class ContainerLogLimitExceeded(RuntimeError):
    pass


class TerminalFenceLost(RuntimeError):
    pass


class ParentAuthorityDrift(RuntimeError):
    pass


COMPARISON_FAILURE_REASON_CODES = frozenset({
    "container_timeout", "log_limit", "output_limit", "malformed_signal",
    "malformed_sam", "parent_drift", "lease_loss", "cleanup_failure",
})


class ComparisonRuntimeFailure(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        if reason_code not in COMPARISON_FAILURE_REASON_CODES:
            raise ValueError("comparison runtime failure reason is outside the closed vocabulary")
        super().__init__(message)
        self.reason_code = reason_code


def _comparison_failure_reason(exc: Exception) -> str:
    if isinstance(exc, ComparisonRuntimeFailure):
        return exc.reason_code
    if isinstance(exc, TimeoutError):
        return "container_timeout"
    if isinstance(exc, ContainerLogLimitExceeded):
        return "log_limit"
    if isinstance(exc, OutputLimitExceeded):
        return "output_limit"
    if isinstance(exc, ParentAuthorityDrift):
        return "parent_drift"
    if isinstance(exc, TerminalFenceLost):
        return "lease_loss"
    if isinstance(exc, ContainerCleanupError):
        return "cleanup_failure"
    return "runtime_validation_failed"


def _comparison_container_failure(kind: str, stderr_tail: str) -> RuntimeError:
    terminal_line = next(
        (line.strip() for line in reversed(stderr_tail.splitlines()) if line.strip()),
        "",
    )
    reason_code: str | None = None
    if kind == "squigulator_producer":
        if terminal_line == "RuntimeError: Squigulator combined log limit exceeded":
            reason_code = "log_limit"
        elif terminal_line.startswith("ValueError: Squigulator SAM "):
            reason_code = "malformed_sam"
        elif terminal_line.startswith((
            "ValueError: simulated BLOW5 ",
            "ValueError: Squigulator PAF dwell truth diverges from signal coordinates",
        )):
            reason_code = "malformed_signal"
    elif kind == "squigualiser_comparison_renderer":
        if terminal_line == "RuntimeError: comparison renderer command log ceiling exceeded":
            reason_code = "log_limit"
        elif terminal_line.startswith("ValueError: comparison BLOW5 "):
            reason_code = "malformed_signal"
    message = stderr_tail or "comparison runtime failed"
    return ComparisonRuntimeFailure(reason_code, message) if reason_code else RuntimeError(message)


def _effective_base_shift(params: dict[str, Any]) -> int:
    source = params.get("base_shift_source")
    value = int(params.get("base_shift_value", 0))
    if source == "profile":
        profile_id = params.get("base_shift_profile_id")
        profile_sha256 = params.get("base_shift_profile_sha256")
        effective_value = params.get("base_shift_effective_value")
        if (
            value != 0
            or not isinstance(profile_id, str)
            or not profile_id
            or not isinstance(profile_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", profile_sha256) is None
            or isinstance(effective_value, bool)
            or not isinstance(effective_value, int)
            or not -64 <= effective_value <= 64
        ):
            raise RuntimeError("profile-sourced base shift lacks bound profile authority")
        return effective_value
    if source == "explicit":
        return value
    raise RuntimeError("render base-shift source is invalid")


def _mapping_profile_render_args(profile: Any) -> list[str]:
    kmer_length = getattr(profile, "kmer_length", None)
    if (
        isinstance(kmer_length, bool)
        or not isinstance(kmer_length, int)
        or not 1 <= kmer_length <= 9
    ):
        raise RuntimeError("render mapping-profile k-mer length is invalid")
    return ["--kmer-length", str(kmer_length)]


def _identity_from_descriptor(descriptor: int) -> tuple[str, int, tuple[int, int, int, int, int]]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise RuntimeError("governed parent must be a non-empty retained regular file")
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.pread(descriptor, 1024 * 1024, offset):
        digest.update(chunk)
        offset += len(chunk)
    after = os.fstat(descriptor)
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise RuntimeError("governed parent changed while hashing")
    return digest.hexdigest(), offset, identity


def _lexical_absolute(path: Path) -> Path:
    raw = str(path)
    if not os.path.isabs(raw) or any(part in {"", ".", ".."} for part in raw.split(os.sep)[1:]):
        raise RuntimeError("retained parent path is not lexical absolute authority")
    return Path(os.path.abspath(raw))


def _open_beneath_governed_root(path: Path, governed_roots: tuple[Path, ...]) -> int:
    candidate = _lexical_absolute(path)
    matches: list[tuple[int, Path, Path]] = []
    for raw_root in governed_roots:
        try:
            root = _lexical_absolute(Path(raw_root))
            relative = candidate.relative_to(root)
        except (RuntimeError, ValueError):
            continue
        if relative.parts:
            matches.append((len(root.parts), root, relative))
    if not matches:
        raise RuntimeError("retained parent is outside approved governed roots")
    _depth, root, relative = max(matches, key=lambda item: item[0])
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(os.sep, flags)
    try:
        for component in root.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        for component in relative.parts[:-1]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_descriptor = os.open(
            relative.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=descriptor,
        )
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            os.close(file_descriptor)
            raise RuntimeError("governed parent must be a retained regular file")
        return file_descriptor
    except RuntimeError:
        raise
    except OSError as exc:
        raise RuntimeError(
            "retained parent cannot be traversed beneath its governed root without symbolic links"
        ) from exc
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class RetainedParent:
    fd: int
    alias: str
    sha256: str
    size_bytes: int
    identity: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class MoveBamAuthority:
    path: Path | None
    receipt: OntExternalMoveBamRegistrationReceipt | None
    root_fd: int | None = None
    relative: Path | None = None


class RetainedParentSet:
    """Own read-leased descriptors until OCI cleanup and publication finish."""

    def __init__(self, governed_roots: tuple[Path, ...] | None = None) -> None:
        self._opened_at_generation = lease_break_generation()
        self._parents: list[RetainedParent] = []
        self._root_descriptors: list[tuple[int, tuple[int, int, int]]] = []
        self._closed = False
        self._governed_roots = governed_roots

    @property
    def parents(self) -> tuple[RetainedParent, ...]:
        return tuple(self._parents)

    def pin(
        self,
        path: Path,
        *,
        alias: str,
        expected_sha256: str,
        expected_size: int,
    ) -> RetainedParent:
        if self._closed:
            raise RuntimeError("retained-parent set is closed")
        if (
            not alias
            or alias in {".", ".."}
            or "/" in alias
            or "\\" in alias
            or len(alias) > 128
            or any(parent.alias == alias for parent in self._parents)
        ):
            raise RuntimeError("retained-parent alias is invalid or duplicated")
        governed_roots = self._governed_roots or (path.parent,)
        descriptor = _open_beneath_governed_root(path, governed_roots)
        leased = False
        try:
            if not hasattr(fcntl, "F_SETLEASE"):
                raise RuntimeError("Linux file leases are unavailable")
            fcntl.fcntl(descriptor, fcntl.F_SETOWN, os.getpid())
            fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_RDLCK)
            leased = True
            digest, size, identity = _identity_from_descriptor(descriptor)
            if digest != expected_sha256 or size != expected_size:
                raise ParentAuthorityDrift("retained parent diverged from immutable hash/size authority")
            parent = RetainedParent(descriptor, alias, digest, size, identity)
            self._parents.append(parent)
            self.assert_unbroken()
            return parent
        except BaseException:
            if leased:
                try:
                    fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_UNLCK)
                except OSError:
                    pass
            os.close(descriptor)
            raise

    async def pin_async(
        self,
        path: Path,
        *,
        alias: str,
        expected_sha256: str,
        expected_size: int,
    ) -> RetainedParent:
        if self._closed:
            raise RuntimeError("retained-parent set is closed")
        if (
            not alias
            or alias in {".", ".."}
            or "/" in alias
            or "\\" in alias
            or len(alias) > 128
            or any(parent.alias == alias for parent in self._parents)
        ):
            raise RuntimeError("retained-parent alias is invalid or duplicated")
        governed_roots = self._governed_roots or (path.parent,)
        descriptor = _open_beneath_governed_root(path, governed_roots)
        leased = False
        try:
            if not hasattr(fcntl, "F_SETLEASE"):
                raise RuntimeError("Linux file leases are unavailable")
            fcntl.fcntl(descriptor, fcntl.F_SETOWN, os.getpid())
            fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_RDLCK)
            leased = True
            digest, size, identity = await asyncio.to_thread(_identity_from_descriptor, descriptor)
            if digest != expected_sha256 or size != expected_size:
                raise ParentAuthorityDrift("retained parent diverged from immutable hash/size authority")
            parent = RetainedParent(descriptor, alias, digest, size, identity)
            self._parents.append(parent)
            self.assert_unbroken()
            return parent
        except BaseException:
            if leased:
                try:
                    fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_UNLCK)
                except OSError:
                    pass
            os.close(descriptor)
            raise

    async def pin_beneath_root_async(
        self,
        root_fd: int,
        relative: Path,
        *,
        alias: str,
        expected_sha256: str,
        expected_size: int,
    ) -> RetainedParent:
        if self._closed or not relative.parts or relative.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            os.close(root_fd)
            raise RuntimeError("external retained-parent selector is invalid")
        if (
            not alias
            or alias in {".", ".."}
            or "/" in alias
            or "\\" in alias
            or len(alias) > 128
            or any(parent.alias == alias for parent in self._parents)
        ):
            os.close(root_fd)
            raise RuntimeError("retained-parent alias is invalid or duplicated")
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            os.close(root_fd)
            raise RuntimeError("external retained-parent root is not a directory")
        root_identity = (root_info.st_dev, root_info.st_ino, root_info.st_ctime_ns)
        self._root_descriptors.append((root_fd, root_identity))
        descriptor: int | None = None
        parent_fd = os.dup(root_fd)
        leased = False
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            for component in relative.parts[:-1]:
                child_fd = os.open(component, flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = child_fd
            descriptor = os.open(
                relative.parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            if not hasattr(fcntl, "F_SETLEASE"):
                raise RuntimeError("Linux file leases are unavailable")
            fcntl.fcntl(descriptor, fcntl.F_SETOWN, os.getpid())
            fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_RDLCK)
            leased = True
            digest, size, identity = await asyncio.to_thread(
                _identity_from_descriptor, descriptor
            )
            if digest != expected_sha256 or size != expected_size:
                raise ParentAuthorityDrift("retained parent diverged from immutable hash/size authority")
            parent = RetainedParent(descriptor, alias, digest, size, identity)
            self._parents.append(parent)
            self.assert_unbroken()
            return parent
        except BaseException:
            if descriptor is not None:
                if leased:
                    try:
                        fcntl.fcntl(descriptor, fcntl.F_SETLEASE, fcntl.F_UNLCK)
                    except OSError:
                        pass
                os.close(descriptor)
            self._root_descriptors.remove((root_fd, root_identity))
            os.close(root_fd)
            raise
        finally:
            os.close(parent_fd)

    def assert_unbroken(self) -> None:
        if self._closed or lease_break_generation() != self._opened_at_generation:
            raise ParentAuthorityDrift("retained-parent read lease was broken")
        for parent in self._parents:
            current = os.fstat(parent.fd)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != parent.identity:
                raise ParentAuthorityDrift("retained-parent descriptor identity changed")
        for descriptor, identity in self._root_descriptors:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino, current.st_ctime_ns) != identity:
                raise ParentAuthorityDrift("retained-parent root descriptor identity changed")

    def metadata(self, operation_argv: list[str]) -> dict[str, Any]:
        self.assert_unbroken()
        return {
            "schema": "bms.ont-signal-fd-broker.v1",
            "operation_argv": operation_argv,
            "parents": [
                {"alias": parent.alias, "sha256": parent.sha256, "size_bytes": parent.size_bytes}
                for parent in self._parents
            ],
        }

    def subset(self, aliases: set[str]) -> "RetainedParentView":
        return RetainedParentView(self, aliases)

    def close(self) -> None:
        if self._closed:
            return
        for parent in reversed(self._parents):
            try:
                fcntl.fcntl(parent.fd, fcntl.F_SETLEASE, fcntl.F_UNLCK)
            except OSError:
                pass
            try:
                os.close(parent.fd)
            except OSError:
                pass
        for descriptor, _identity in reversed(self._root_descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._closed = True

    def __enter__(self) -> "RetainedParentSet":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


class RetainedParentView:
    """Expose only the retained descriptors needed by one runtime stage."""

    def __init__(self, owner: RetainedParentSet, aliases: set[str]) -> None:
        owner.assert_unbroken()
        selected = tuple(parent for parent in owner.parents if parent.alias in aliases)
        if {parent.alias for parent in selected} != aliases:
            raise RuntimeError("retained-parent subset is incomplete")
        self._owner, self._parents = owner, selected

    @property
    def parents(self) -> tuple[RetainedParent, ...]:
        return self._parents

    def assert_unbroken(self) -> None:
        self._owner.assert_unbroken()

    def metadata(self, operation_argv: list[str]) -> dict[str, Any]:
        self.assert_unbroken()
        return {
            "schema": "bms.ont-signal-fd-broker.v1",
            "operation_argv": operation_argv,
            "parents": [
                {"alias": parent.alias, "sha256": parent.sha256, "size_bytes": parent.size_bytes}
                for parent in self._parents
            ],
        }


def _append_lease_recovery_receipt(
    receipts: dict[str, Any],
    *,
    expired_attempt: int,
    recovered_at: datetime,
    max_attempts: int,
) -> dict[str, Any]:
    prior = receipts.get("lease_recoveries", [])
    if not isinstance(prior, list):
        raise RuntimeError("lease recovery receipt history is malformed")
    return {
        **receipts,
        "lease_recoveries": [
            *prior,
            {
                "recovered_at": recovered_at.isoformat(),
                "expired_attempt": expired_attempt,
                "max_attempts": max_attempts,
            },
        ],
    }


class OntSignalWorker:
    """Single-owner leased worker for move validation, reusable mapping, and bounded renders."""

    def __init__(self, session_factory: Any, domain_session_factory: Any, *, poll_interval: float = 5.0):
        self._session_factory = session_factory
        self._domain_session_factory = domain_session_factory
        self._poll_interval = max(1.0, poll_interval)
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._child: asyncio.subprocess.Process | None = None
        self._active_container: tuple[str, str] | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        await self._recover_stale_containers()
        await self._recover_expired()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ont-signal-workbench-worker")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._remove_active_container()
        if self._child is not None and self._child.returncode is None:
            self._child.terminate()
            await self._child.wait()
        self._child = None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    @staticmethod
    def _output_root() -> Path:
        return get_results_dir() / "ont_signal_workbench"

    @staticmethod
    def _container_user_identity() -> tuple[int, int]:
        uid, gid = os.getuid(), os.getgid()
        if uid != 0 and gid != 0:
            return uid, gid

        def outer_identity(path: str) -> int:
            fields = Path(path).read_text(encoding="ascii").splitlines()[0].split()
            if len(fields) != 3 or fields[0] != "0" or fields[2] != "1":
                return 0
            return int(fields[1])

        mapped_uid = outer_identity("/proc/self/uid_map")
        mapped_gid = outer_identity("/proc/self/gid_map")
        if mapped_uid <= 0 or mapped_gid <= 0:
            raise RuntimeError("SCM_RIGHTS broker requires a non-root host uid/gid")
        return mapped_uid, mapped_gid

    @staticmethod
    def _prepare_output_directory(path: Path, owner_id: str) -> None:
        if path.exists():
            owner = path / ".owner"
            if not owner.is_file() or owner.read_text(encoding="utf-8") != owner_id:
                raise RuntimeError("signal-workbench recovery output ownership is invalid")
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=False)
        (path / ".owner").write_text(owner_id, encoding="utf-8")

    @staticmethod
    def _runtime_identity() -> dict[str, str]:
        image = os.environ.get("BMS_ONT_SQUIGUALISER_IMAGE", "").strip()
        digest = os.environ.get("BMS_ONT_SQUIGUALISER_IMAGE_DIGEST", "").strip().lower()
        descriptor = os.open(
            RUNTIME_POLICY_PATH,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        try:
            raw = os.read(descriptor, 8193)
            if len(raw) > 8192 or os.read(descriptor, 1):
                raise RuntimeError("approved runtime policy manifest exceeds its bound")
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise RuntimeError("approved runtime policy manifest is not a regular file")
        finally:
            os.close(descriptor)
        policy_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            policy = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("approved runtime policy manifest is invalid") from exc
        expected_policy = {
            "schema": "bms.ont-squigualiser-runtime-policy.v1",
            "runtime_id": APPROVED_OCI_DIGEST,
            "oci_digest": APPROVED_OCI_DIGEST,
            "upstream": {
                "name": "Squigualiser",
                "version": "0.7.0",
                "commit": APPROVED_UPSTREAM_COMMIT,
            },
            "network": "none",
        }
        if policy_sha256 != RUNTIME_POLICY_SHA256 or policy != expected_policy:
            raise RuntimeError("approved runtime policy manifest identity diverged")
        if (
            image != policy["runtime_id"]
            or digest != policy["oci_digest"].removeprefix("sha256:")
            or not HEX64.fullmatch(digest)
        ):
            raise RuntimeError("configured Squigualiser identity does not equal approved runtime policy")
        return {
            "image": image,
            "image_digest": digest,
            "upstream_version": policy["upstream"]["version"],
            "upstream_commit": policy["upstream"]["commit"],
            "network": "none",
            "policy_manifest_sha256": policy_sha256,
        }

    @staticmethod
    def _assert_local_runtime_image(runtime: str, image: str) -> None:
        try:
            result = subprocess.run(
                [runtime, "image", "inspect", "--format", "{{.Id}}", image],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=30,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("local approved Squigualiser image inspection failed") from exc
        observed = result.stdout.strip()
        if result.returncode != 0 or observed != image:
            raise RuntimeError("local approved Squigualiser image is absent or diverged")

    def _container_command(
        self,
        output_dir: Path,
        broker_dir: Path,
        *,
        kind: str,
    ) -> list[str]:
        identity = self._runtime_identity()
        runtime = os.environ.get("BMS_CONTAINER_RUNTIME", "podman").strip()
        if runtime not in {"podman", "docker"}:
            raise RuntimeError("unsupported container runtime")
        self._assert_local_runtime_image(runtime, identity["image"])
        uid, gid = self._container_user_identity()
        output = Path(os.path.abspath(output_dir))
        broker = Path(os.path.abspath(broker_dir))
        if not output.is_dir() or output.is_symlink() or not broker.is_dir() or broker.is_symlink():
            raise RuntimeError("container output or broker directory is invalid")
        command = [
            runtime, "run", "--pull=never", "--network", "none", "--read-only", "--user", f"{uid}:{gid}",
            "--pids-limit", "128", "--memory", "4g", "--cpus", "4", "--cap-drop", "ALL",
            "--label", WORKER_LABEL,
            "--ulimit", f"fsize={FILE_SIZE_LIMITS[kind]}:{FILE_SIZE_LIMITS[kind]}",
            "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m",
            "--mount", f"type=bind,src={output},dst=/output",
            "--mount", f"type=bind,src={broker},dst=/broker",
            identity["image"],
            "python3", "/opt/bms/ont_signal_runtime.py", "broker",
            "--socket", "/broker/parents.sock", "--timeout-seconds", "30",
        ]
        return command

    @staticmethod
    def _comparison_runtime_identity(stage: str) -> dict[str, str]:
        approvals: dict[str, tuple[Path, str, str, str, str, dict[str, Any]]] = {
            "squigulator_producer": (
                SQUIGULATOR_POLICY_PATH, "BMS_ONT_SQUIGULATOR_IMAGE",
                "BMS_ONT_SQUIGULATOR_IMAGE_DIGEST", "scripts/ont_squigulator_runtime.py",
                "edd60f7d2930674767df43d3f196b2111f899a28feb71c5cf9a59b05815fa871",
                {"schema": "bms.ont-squigulator-runtime-policy.v1",
                 "runtime_id": "sha256:10690870e22ae777ada80688060eb30977e63034b56bedb38c160a827604351b",
                 "oci_digest": "sha256:10690870e22ae777ada80688060eb30977e63034b56bedb38c160a827604351b",
                 "upstream": {"name": "Squigulator", "version": "0.5.0", "commit": "c5f0c619a28b9532388877096acb7568c34b9c4b"},
                 "source_asset": {"name": "squigulator-v0.5.0-release.tar.gz", "sha256": "f8b428655d586427c6e0c939d4a0383fa8569523234e3c21951edcd23372a66a"},
                 "licenses": {"squigulator": "MIT", "slow5lib": "MIT", "streamvbyte": "Apache-2.0"},
                 "wrapper": "scripts/ont_squigulator_runtime.py",
                 "wrapper_sha256": "e5ff983ab508c14424b93aa2787127eedc546bde5f6fbd349b5ff939956338b1",
                 "network": "none"},
            ),
            "squigualiser_comparison_renderer": (
                COMPARISON_RENDER_POLICY_PATH, "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE",
                "BMS_ONT_SQUIGUALISER_COMPARISON_IMAGE_DIGEST", "scripts/ont_signal_comparison_runtime.py",
                "a5a2d25ef8bfc9e49e9244454e48641907ac34d42aa786af5302e2ecfca4a182",
                {"schema": "bms.ont-squigualiser-comparison-runtime-policy.v1",
                 "runtime_id": "sha256:e1a5778525539c1fd6c98c2bf53a3f341bac53b80044abd7f633e1a7e7fd70c0",
                 "oci_digest": "sha256:e1a5778525539c1fd6c98c2bf53a3f341bac53b80044abd7f633e1a7e7fd70c0",
                 "upstream": {"name": "Squigualiser", "version": "0.7.0", "commit": "5a2404f1f43bc3227a85475c59b2b77970078b2e"},
                 "wrapper": "scripts/ont_signal_comparison_runtime.py",
                 "wrapper_sha256": "80f780ed1a34eb09f6f5a95db826ac13a5684a276575c2a2d867165032889ee7",
                 "network": "none"},
            ),
        }
        try:
            policy_path, image_env, digest_env, wrapper_name, approved_policy_sha256, expected_policy = approvals[stage]
        except KeyError as exc:
            raise RuntimeError("unknown comparison runtime stage") from exc
        descriptor = os.open(policy_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            raw = os.read(descriptor, 8193)
            opened = os.fstat(descriptor)
            if len(raw) > 8192 or os.read(descriptor, 1) or not stat.S_ISREG(opened.st_mode):
                raise RuntimeError("approved comparison runtime policy is not a bounded regular file")
        finally:
            os.close(descriptor)
        policy_sha256 = hashlib.sha256(raw).hexdigest()
        try:
            policy = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("approved comparison runtime policy is invalid") from exc
        wrapper_relative = Path(wrapper_name)
        if wrapper_relative.is_absolute() or wrapper_relative.parts[0] != "scripts" or len(wrapper_relative.parts) != 2:
            raise RuntimeError("approved comparison wrapper path is invalid")
        if policy_sha256 != approved_policy_sha256 or policy != expected_policy:
            raise RuntimeError("approved comparison runtime policy identity diverged")
        wrapper_path = Path(__file__).resolve().parents[3] / wrapper_relative
        wrapper_descriptor = os.open(wrapper_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            wrapper_info = os.fstat(wrapper_descriptor)
            if not stat.S_ISREG(wrapper_info.st_mode) or wrapper_info.st_size <= 0 or wrapper_info.st_size > 1024 * 1024:
                raise RuntimeError("approved comparison wrapper is not a bounded regular file")
            wrapper_digest = hashlib.sha256()
            while chunk := os.read(wrapper_descriptor, 1024 * 1024):
                wrapper_digest.update(chunk)
            wrapper_sha256 = wrapper_digest.hexdigest()
        finally:
            os.close(wrapper_descriptor)
        image = os.environ.get(image_env, "").strip()
        digest = os.environ.get(digest_env, "").strip().lower()
        if (
            image != expected_policy["runtime_id"]
            or digest != str(expected_policy["oci_digest"]).removeprefix("sha256:")
            or wrapper_sha256 != expected_policy["wrapper_sha256"]
            or not HEX64.fullmatch(digest)
        ):
            raise RuntimeError(f"configured {stage} identity diverges from approved policy")
        return {"stage": stage, "image": image, "image_digest": digest,
                "policy_sha256": policy_sha256, "wrapper_sha256": wrapper_sha256}

    def _comparison_container_command(
        self, stage: str, output_dir: Path, broker_dir: Path
    ) -> list[str]:
        identity = self._comparison_runtime_identity(stage)
        runtime = os.environ.get("BMS_CONTAINER_RUNTIME", "podman").strip()
        if runtime not in {"podman", "docker"}:
            raise RuntimeError("unsupported container runtime")
        self._assert_local_runtime_image(runtime, identity["image"])
        uid, gid = self._container_user_identity()
        output = Path(os.path.abspath(output_dir))
        broker = Path(os.path.abspath(broker_dir))
        if not output.is_dir() or output.is_symlink() or not broker.is_dir() or broker.is_symlink():
            raise RuntimeError("comparison output or broker directory is invalid")
        common = [runtime, "run", "--pull=never", "--network", "none", "--read-only",
                  "--user", f"{uid}:{gid}", "--cap-drop", "ALL", "--label", WORKER_LABEL,
                  "--security-opt", "no-new-privileges", "--mount",
                  f"type=bind,src={output},dst=/output", "--mount",
                  f"type=bind,src={broker},dst=/broker"]
        if stage == "squigulator_producer":
            return [*common, "--pids-limit", "64", "--memory", "1g", "--cpus", "1",
                    "--ulimit", f"fsize={FILE_SIZE_LIMITS[stage]}:{FILE_SIZE_LIMITS[stage]}",
                    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=256m", identity["image"],
                    "python3", "/opt/bms/ont_squigulator_runtime.py", "broker",
                    "--socket", "/broker/parents.sock", "--timeout-seconds", "30"]
        return [*common, "--pids-limit", "128", "--memory", "4g", "--cpus", "4",
                "--ulimit", f"fsize={FILE_SIZE_LIMITS[stage]}:{FILE_SIZE_LIMITS[stage]}",
                "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=512m", identity["image"],
                "python3", "/opt/bms/ont_signal_comparison_runtime.py", "broker",
                "--socket", "/broker/parents.sock", "--timeout-seconds", "30"]

    @staticmethod
    def _stable_file_identity(path: Path) -> tuple[str, int]:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            digest, size, _identity = _identity_from_descriptor(descriptor)
            return digest, size
        finally:
            os.close(descriptor)

    @staticmethod
    async def _stable_file_identity_async(path: Path) -> tuple[str, int]:
        return await asyncio.to_thread(OntSignalWorker._stable_file_identity, path)

    @staticmethod
    async def _pin_parent_async(
        parents: RetainedParentSet,
        path: Path,
        *,
        alias: str,
        expected_sha256: str,
        expected_size: int,
    ) -> RetainedParent:
        return await parents.pin_async(
            path,
            alias=alias,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )

    @staticmethod
    async def _resolve_session_alignment_bundle_async(
        alignment_job_id: str,
        alignment_session_id: str,
        authority: dict[str, str],
        job_output_dir: str | None,
    ) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
        return await asyncio.to_thread(
            ngs_alignment_sessions.resolve_session_alignment_bundle,
            alignment_job_id,
            alignment_session_id,
            **authority,
            job_output_dir=job_output_dir,
        )

    @staticmethod
    def _require_hash_contract(label: str, expected: Any, actual: Any) -> None:
        if expected != actual:
            raise RuntimeError(f"{label} diverged from the immutable parent hash contract")

    @classmethod
    def _read_json_report(cls, path: Path) -> dict[str, Any]:
        value, _digest, _size = cls._read_json_report_identity(path)
        return value

    @staticmethod
    def _read_json_report_identity(path: Path) -> tuple[dict[str, Any], str, int]:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > 1024 * 1024:
                raise RuntimeError("runtime JSON report violates the bounded regular-file policy")
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            size = 0
            while chunk := os.read(descriptor, 64 * 1024):
                size += len(chunk)
                if size > 1024 * 1024:
                    raise RuntimeError("runtime JSON report exceeds the 1 MiB policy")
                digest.update(chunk)
                chunks.append(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise RuntimeError("runtime JSON report changed while reading")
        value = json.loads(b"".join(chunks))
        if not isinstance(value, dict):
            raise RuntimeError("runtime JSON report must be an object")
        return value, digest.hexdigest(), size

    @classmethod
    def _resolve_selected_raw_partitions(
        cls,
        representation: OntRawSignalRepresentation,
        read_ids: list[str] | None,
    ) -> tuple[list[tuple[Path, Path]], dict[str, Any]]:
        manifest = representation.artifact_manifest if isinstance(representation.artifact_manifest, dict) else {}
        manifest_sha256 = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        if manifest_sha256 != getattr(representation, "manifest_sha256", None):
            raise RuntimeError("raw artifact manifest digest authority diverged")
        artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
        by_path: dict[Path, dict[str, Any]] = {}
        routing_sha256: str | None = None
        routing_path: Path | None = None
        for item in artifacts:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            path = Path(str(item["path"])).expanduser()
            if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
                raise RuntimeError("raw artifact manifest path is not absolute and lexical")
            by_path[path] = item
            if item.get("kind") == "read_routing":
                if routing_path is not None:
                    raise RuntimeError("raw artifact manifest contains ambiguous routing authority")
                routing_sha256 = str(item.get("sha256") or "")
                routing_path = path
        if routing_path is not None:
            routing_item = by_path[routing_path]
            routing_actual, routing_size = cls._stable_file_identity(routing_path)
            if routing_actual != routing_sha256 or routing_size != routing_item.get("bytes"):
                raise RuntimeError("raw routing manifest diverged from immutable artifact authority")
        if read_ids is None:
            blow5_paths = sorted(
                (path for path, item in by_path.items() if item.get("kind") == "blow5"),
                key=str,
            )
            selected = [(path, Path(f"{path}.idx")) for path in blow5_paths]
        else:
            if routing_path is None:
                raise RuntimeError("selected-read partition resolution requires governed routing authority")
            selected = []
            for read_id in read_ids:
                blow5, index = ont_raw_signal._validated_blow5_paths(representation, read_id)
                pair = (blow5, index)
                if pair not in selected:
                    selected.append(pair)
        if not selected:
            raise RuntimeError("no governed BLOW5 partitions were selected")
        identities: list[dict[str, str]] = []
        for blow5, index in selected:
            blow5_item, index_item = by_path.get(blow5), by_path.get(index)
            if blow5_item is None or index_item is None or index != Path(f"{blow5}.idx"):
                raise RuntimeError("selected BLOW5 partition/index lacks immutable manifest authority")
            blow5_actual, blow5_size = cls._stable_file_identity(blow5)
            index_actual, index_size = cls._stable_file_identity(index)
            if (
                blow5_actual != blow5_item.get("sha256")
                or blow5_size != blow5_item.get("bytes")
                or index_actual != index_item.get("sha256")
                or index_size != index_item.get("bytes")
            ):
                raise RuntimeError("selected BLOW5 partition/index diverged from immutable artifact authority")
            identities.append({"sha256": blow5_actual, "index_sha256": index_actual})
        return selected, {"routing_sha256": routing_sha256, "blow5": identities}

    @staticmethod
    def _pin_raw_partitions(
        parents: RetainedParentSet,
        representation: OntRawSignalRepresentation,
        selected: list[tuple[Path, Path]],
    ) -> list[dict[str, str]]:
        manifest = representation.artifact_manifest if isinstance(representation.artifact_manifest, dict) else {}
        artifacts_value: Any = manifest.get("artifacts")
        artifacts: list[Any] = artifacts_value if isinstance(artifacts_value, list) else []
        by_path = {
            Path(str(item["path"])): item
            for item in artifacts
            if isinstance(item, dict) and item.get("path")
        }
        identities: list[dict[str, str]] = []
        for index, (blow5, adjacent) in enumerate(selected):
            blow5_item = by_path.get(blow5)
            adjacent_item = by_path.get(adjacent)
            if blow5_item is None or adjacent_item is None:
                raise RuntimeError("selected raw partition lacks manifest authority")
            retained_blow5 = parents.pin(
                blow5,
                alias=f"raw-{index}.blow5",
                expected_sha256=str(blow5_item.get("sha256") or ""),
                expected_size=int(blow5_item.get("bytes") or 0),
            )
            retained_index = parents.pin(
                adjacent,
                alias=f"raw-{index}.blow5.idx",
                expected_sha256=str(adjacent_item.get("sha256") or ""),
                expected_size=int(adjacent_item.get("bytes") or 0),
            )
            identities.append({
                "sha256": retained_blow5.sha256,
                "index_sha256": retained_index.sha256,
            })
        return identities

    @classmethod
    async def _resolve_selected_raw_partitions_async(
        cls,
        representation: OntRawSignalRepresentation,
        read_ids: list[str] | None,
    ) -> tuple[list[tuple[Path, Path]], dict[str, Any]]:
        return await asyncio.to_thread(cls._resolve_selected_raw_partitions, representation, read_ids)

    @staticmethod
    async def _pin_raw_partitions_async(
        parents: RetainedParentSet,
        representation: OntRawSignalRepresentation,
        selected: list[tuple[Path, Path]],
    ) -> list[dict[str, str]]:
        manifest = representation.artifact_manifest if isinstance(representation.artifact_manifest, dict) else {}
        artifacts_value: Any = manifest.get("artifacts")
        artifacts: list[Any] = artifacts_value if isinstance(artifacts_value, list) else []
        by_path = {
            Path(str(item["path"])): item
            for item in artifacts
            if isinstance(item, dict) and item.get("path")
        }
        identities: list[dict[str, str]] = []
        for index, (blow5, adjacent) in enumerate(selected):
            blow5_item = by_path.get(blow5)
            adjacent_item = by_path.get(adjacent)
            if blow5_item is None or adjacent_item is None:
                raise RuntimeError("selected raw partition lacks manifest authority")
            retained_blow5 = await OntSignalWorker._pin_parent_async(
                parents,
                blow5,
                alias=f"raw-{index}.blow5",
                expected_sha256=str(blow5_item.get("sha256") or ""),
                expected_size=int(blow5_item.get("bytes") or 0),
            )
            retained_index = await OntSignalWorker._pin_parent_async(
                parents,
                adjacent,
                alias=f"raw-{index}.blow5.idx",
                expected_sha256=str(adjacent_item.get("sha256") or ""),
                expected_size=int(adjacent_item.get("bytes") or 0),
            )
            identities.append({
                "sha256": retained_blow5.sha256,
                "index_sha256": retained_index.sha256,
            })
        return identities

    @staticmethod
    def _output_tree_size(root: Path, limit: int) -> int:
        total = 0
        for directory, names, filenames in os.walk(root, followlinks=False):
            names.sort(); filenames.sort()
            for name in names:
                info = (Path(directory) / name).lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise OutputLimitExceeded("runtime output contains an unexpected directory entry")
            for name in filenames:
                path = Path(directory) / name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    raise OutputLimitExceeded("runtime output contains a non-regular file")
                total += info.st_size
                if total > limit:
                    raise OutputLimitExceeded("runtime total output limit exceeded")
        return total

    @staticmethod
    def _assert_expected_outputs(root: Path, kind: str, allowed_extra: set[str] | None = None) -> None:
        entries = list(root.iterdir())
        if any(path.is_dir() or path.is_symlink() for path in entries):
            raise RuntimeError("runtime produced an unexpected output tree entry")
        names = {path.name for path in entries if path.is_file()}
        common = {".owner"}
        allowed = {
            "move": common | {"filtered_moves.bam", "read_inventory.txt", "validation.json"},
            "calibration": common | {"sample.bam", "sample.fasta", "sample.blow5", "sample.blow5.idx", "baseline.paf", "calibration.json"},
            "mapping": common | {"reform.paf", "realign.paf.gz", "realign.paf.gz.tbi", "validation.json"},
            "squigulator_producer": common | set(COMPARISON_PRODUCER_FILENAMES) | {"producer_manifest.json"},
            "squigualiser_comparison_renderer": common | set(COMPARISON_PRODUCER_FILENAMES) | {
                "producer_manifest.json", "real_track.html", "simulated_track.html",
                "comparison.html", "comparison_manifest.json",
            },
        }
        if kind == "view":
            extras = allowed_extra or set()
            unexpected = {name for name in names if name != "render_manifest.json" and name not in extras and Path(name).suffix.lower() not in {".html", ".svg"} and name != ".owner"}
        else:
            unexpected = names - allowed[kind]
        if unexpected:
            raise RuntimeError(f"runtime produced unexpected output files: {sorted(unexpected)}")

    async def _remove_active_container(self) -> None:
        active = self._active_container
        if active is None:
            return
        runtime, name = active
        cleanup = await asyncio.create_subprocess_exec(
            runtime, "rm", "-f", name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
        )
        try:
            _stdout, stderr = await asyncio.wait_for(cleanup.communicate(), timeout=30)
        except asyncio.TimeoutError:
            cleanup.kill()
            await cleanup.wait()
            self._stop.set()
            raise ContainerCleanupError("timed out while removing the bounded Squigualiser container")
        if cleanup.returncode != 0:
            self._stop.set()
            raise ContainerCleanupError(stderr[-MAX_FAILURE:].decode("utf-8", "replace") or "bounded container cleanup failed")
        self._active_container = None

    async def _recover_stale_containers(self) -> None:
        runtime = os.environ.get("BMS_CONTAINER_RUNTIME", "podman").strip()
        if runtime not in {"podman", "docker"}:
            raise RuntimeError("unsupported container runtime")
        process = await asyncio.create_subprocess_exec(
            runtime, "ps", "-aq", "--filter", f"label={WORKER_LABEL}",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except asyncio.TimeoutError as exc:
            process.kill(); await process.wait(); self._stop.set()
            raise ContainerCleanupError("timed out while discovering stale signal-worker containers") from exc
        if process.returncode != 0:
            self._stop.set()
            raise ContainerCleanupError(stderr[-MAX_FAILURE:].decode("utf-8", "replace") or "stale container discovery failed")
        for name in stdout.decode("utf-8", "strict").splitlines():
            if not name.strip():
                continue
            self._active_container = (runtime, name.strip())
            await self._remove_active_container()

    @staticmethod
    async def _drain_stream(stream: asyncio.StreamReader, stream_name: str) -> dict[str, Any]:
        digest = hashlib.sha256()
        tail = bytearray()
        size = 0
        while chunk := await stream.read(64 * 1024):
            size += len(chunk)
            digest.update(chunk)
            tail.extend(chunk)
            if len(tail) > MAX_FAILURE:
                del tail[:-MAX_FAILURE]
            if size > MAX_CONTAINER_LOG_BYTES:
                raise ContainerLogLimitExceeded(f"container {stream_name} log output limit exceeded")
        return {"sha256": digest.hexdigest(), "size_bytes": size, "tail": bytes(tail).decode("utf-8", "replace")}

    async def _send_fd_request(
        self,
        socket_path: Path,
        parents: RetainedParentSet | RetainedParentView,
        operation_argv: list[str],
    ) -> None:
        payload = json.dumps(
            parents.metadata(operation_argv),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(payload) > 256 * 1024:
            raise RuntimeError("SCM_RIGHTS broker request exceeds its bound")
        deadline = time.monotonic() + 30
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.setblocking(False)
        try:
            while True:
                parents.assert_unbroken()
                try:
                    await asyncio.get_running_loop().sock_connect(connection, str(socket_path))
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if self._child is not None and self._child.returncode is not None:
                        raise RuntimeError("SCM_RIGHTS broker exited before descriptor transfer")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("SCM_RIGHTS broker socket did not become ready")
                    await asyncio.sleep(0.01)
            connection.setblocking(True)
            ancillary = [(
                socket.SOL_SOCKET,
                socket.SCM_RIGHTS,
                array.array("i", [parent.fd for parent in parents.parents]).tobytes(),
            )]
            sent = connection.sendmsg([payload], ancillary)
            if sent != len(payload):
                raise RuntimeError("SCM_RIGHTS broker request was only partially transferred")
        finally:
            connection.close()

    async def _invoke(
        self,
        parents: RetainedParentSet | RetainedParentView,
        operation_argv: list[str],
        kind: str,
        item_id: str,
        claim_token: str,
        output_dir: Path,
        allowed_output_names: set[str] | None = None,
    ) -> dict[str, Any]:
        broker_dir = Path(tempfile.mkdtemp(prefix=f"bms-ont-{kind}-", dir="/tmp"))
        os.chmod(broker_dir, 0o700)
        try:
            command = (
                self._comparison_container_command(kind, output_dir, broker_dir)
                if kind in {"squigulator_producer", "squigualiser_comparison_renderer"}
                else self._container_command(output_dir, broker_dir, kind=kind)
            )
            return await self._execute(
                command,
                kind,
                item_id,
                claim_token,
                output_dir,
                allowed_output_names,
                parents=parents,
                operation_argv=operation_argv,
                broker_socket=broker_dir / "parents.sock",
            )
        finally:
            shutil.rmtree(broker_dir, ignore_errors=True)

    async def _execute(
        self,
        command: list[str],
        kind: str,
        item_id: str,
        claim_token: str,
        output_dir: Path,
        allowed_output_names: set[str] | None = None,
        *,
        parents: RetainedParentSet | RetainedParentView,
        operation_argv: list[str],
        broker_socket: Path,
    ) -> dict[str, Any]:
        table = {
            "move": OntMoveTableSource, "calibration": OntSignalCalibrationJob,
            "mapping": OntSignalMappingJob, "view": OntSquigualiserViewJob,
            "squigulator_producer": OntSignalComparisonJob,
            "squigualiser_comparison_renderer": OntSignalComparisonJob,
        }[kind]
        state_field = "validation_state" if kind == "move" else "state"
        async with self._session_factory() as session:
            lease_now = self._now()
            conditions = [table.id == item_id, table.claim_token == claim_token, getattr(table, state_field) == "running"]
            if hasattr(table, "cancel_requested_at"):
                conditions.append(table.cancel_requested_at.is_(None))
            if hasattr(table, "lease_expires_at"):
                conditions.append(table.lease_expires_at > lease_now)
            guarded = await session.execute(update(table).where(*conditions).values(
                lease_expires_at=lease_now + timedelta(seconds=LEASE_SECONDS),
                **({"updated_at": lease_now} if hasattr(table, "updated_at") else {}),
            ))
            if guarded.rowcount != 1:
                await session.rollback()
                current = await session.get(table, item_id)
                if current is not None and getattr(current, "cancel_requested_at", None) is not None:
                    raise asyncio.CancelledError()
                raise TerminalFenceLost(f"{kind} execution lease was lost before launch")
            await session.commit()
        runtime = command[0]
        container_name = f"bms-ont-signal-{kind}-{hashlib.sha256(f'{item_id}:{claim_token}'.encode()).hexdigest()[:24]}"
        command = [*command[:2], "--name", container_name, *command[2:]]
        self._active_container = (runtime, container_name)
        drains: list[asyncio.Task[dict[str, Any]]] = []
        try:
            self._child = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
                start_new_session=True,
            )
            await self._send_fd_request(broker_socket, parents, operation_argv)
            assert self._child.stdout is not None and self._child.stderr is not None
            drains = [
                asyncio.create_task(self._drain_stream(self._child.stdout, "stdout")),
                asyncio.create_task(self._drain_stream(self._child.stderr, "stderr")),
            ]
            deadline = time.monotonic() + COMMAND_DEADLINES[kind]
            next_lease_check = time.monotonic()
            while self._child.returncode is None:
                parents.assert_unbroken()
                for task in drains:
                    if task.done() and task.exception() is not None:
                        raise task.exception()  # type: ignore[misc]
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"{kind} runtime deadline exceeded")
                self._output_tree_size(output_dir, TOTAL_OUTPUT_LIMITS[kind])
                try:
                    await asyncio.wait_for(self._child.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
                if time.monotonic() >= next_lease_check:
                    async with self._session_factory() as session:
                        renew_now = self._now()
                        conditions = [table.id == item_id, table.claim_token == claim_token, getattr(table, state_field) == "running"]
                        if hasattr(table, "cancel_requested_at"):
                            conditions.append(table.cancel_requested_at.is_(None))
                        if hasattr(table, "lease_expires_at"):
                            conditions.append(table.lease_expires_at > renew_now)
                        result = await session.execute(
                            update(table).where(*conditions).values(lease_expires_at=renew_now + timedelta(seconds=LEASE_SECONDS))
                        )
                        if result.rowcount != 1:
                            await session.rollback()
                            row = await session.get(table, item_id)
                            if row is not None and getattr(row, "cancel_requested_at", None) is not None:
                                raise asyncio.CancelledError()
                            raise TerminalFenceLost("signal-workbench lease was lost")
                        await session.commit()
                    next_lease_check = time.monotonic() + 30
            stream_receipts = await asyncio.gather(*drains)
            combined_log_size = sum(int(item["size_bytes"]) for item in stream_receipts)
            if combined_log_size > COMMAND_LOG_LIMITS.get(kind, 2 * MAX_CONTAINER_LOG_BYTES):
                raise ContainerLogLimitExceeded(f"{kind} combined log ceiling exceeded")
            parents.assert_unbroken()
            self._output_tree_size(output_dir, TOTAL_OUTPUT_LIMITS[kind])
            self._assert_expected_outputs(output_dir, kind, allowed_output_names)
            returncode = self._child.returncode
            receipt = {
                "argv_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
                "returncode": returncode,
                "stdout_sha256": stream_receipts[0]["sha256"],
                "stdout_size_bytes": stream_receipts[0]["size_bytes"],
                "stderr_sha256": stream_receipts[1]["sha256"],
                "stderr_size_bytes": stream_receipts[1]["size_bytes"],
                "stderr_tail": stream_receipts[1]["tail"],
                "container_name_sha256": hashlib.sha256(container_name.encode()).hexdigest(),
            }
            if returncode != 0:
                if kind in {"squigulator_producer", "squigualiser_comparison_renderer"}:
                    raise _comparison_container_failure(kind, receipt["stderr_tail"])
                raise RuntimeError(receipt["stderr_tail"] or "Squigualiser runtime failed")
            return receipt
        finally:
            if self._child is not None and self._child.returncode is None:
                self._child.kill()
                await self._child.wait()
            for task in drains:
                if not task.done():
                    task.cancel()
            if drains:
                await asyncio.gather(*drains, return_exceptions=True)
            await self._remove_active_container()
            self._child = None

    async def _recover_expired_table(self, table: Any, state_field: str, now: datetime) -> None:
        async with self._session_factory() as session:
            rows = list((await session.execute(select(table).where(
                getattr(table, state_field) == "running",
                table.lease_expires_at.is_not(None),
                table.lease_expires_at <= now,
            ))).scalars())
            for row in rows:
                observed_token = row.claim_token
                observed_expiry = row.lease_expires_at
                cancelled = getattr(row, "cancel_requested_at", None) is not None
                comparison_recovery_receipt: dict[str, Any] | None = None
                values: dict[str, Any] = {
                    state_field: "cancelled" if cancelled else "requested",
                    "reason_code": "cancelled_after_expired_lease" if cancelled else "expired_lease_recovered",
                    "claim_token": None,
                    "lease_expires_at": None,
                }
                if isinstance(
                    row,
                    (OntSignalCalibrationJob, OntSignalMappingJob, OntSquigualiserViewJob),
                ):
                    receipt_field = (
                        "render_receipt"
                        if isinstance(row, OntSquigualiserViewJob)
                        else "stage_receipts"
                    )
                    receipts = dict(getattr(row, receipt_field, {}) or {})
                    values[receipt_field] = _append_lease_recovery_receipt(
                        receipts,
                        expired_attempt=row.attempt,
                        recovered_at=now,
                        max_attempts=SIGNAL_JOB_MAX_ATTEMPTS,
                    )
                    if not cancelled and row.attempt >= SIGNAL_JOB_MAX_ATTEMPTS:
                        values.update(
                            {
                                state_field: "failed",
                                "reason_code": "expired_lease_retry_exhausted",
                                "failure_code": "ExpiredLeaseRetryExhausted",
                                "failure_message": "expired worker lease exhausted bounded attempt policy",
                                "completed_at": now,
                            }
                        )
                if isinstance(row, OntSignalComparisonJob):
                    receipts = dict(row.stage_receipts or {})
                    prior_recoveries = receipts.get("lease_recoveries", [])
                    if not isinstance(prior_recoveries, list):
                        raise RuntimeError("comparison lease recovery receipt history is malformed")
                    expired_execution = len(prior_recoveries) + 1
                    values["stage_receipts"] = _append_lease_recovery_receipt(
                        receipts,
                        expired_attempt=expired_execution,
                        recovered_at=now,
                        max_attempts=SIGNAL_JOB_MAX_ATTEMPTS,
                    )
                    comparison_recovery_receipt = values["stage_receipts"]["lease_recoveries"][-1]
                    if not cancelled and expired_execution >= SIGNAL_JOB_MAX_ATTEMPTS:
                        values.update(
                            {
                                state_field: "failed",
                                "reason_code": "expired_lease_retry_exhausted",
                                "failure_code": "ExpiredLeaseRetryExhausted",
                                "failure_message": "expired worker lease exhausted bounded attempt policy",
                                "completed_at": now,
                            }
                        )
                if isinstance(row, OntMoveTableSource) and not cancelled:
                    receipt = row.validation_receipt if isinstance(row.validation_receipt, dict) else {}
                    retry_value: Any = receipt.get("retry")
                    retry: dict[str, Any] = retry_value if isinstance(retry_value, dict) else {}
                    prior_value: Any = retry.get("failures")
                    prior: list[Any] = prior_value if isinstance(prior_value, list) else []
                    failures = [*prior, {
                        "attempt": len(prior) + 1,
                        "failed_at": now.isoformat(),
                        "failure_code": "ExpiredLease",
                        "message_sha256": hashlib.sha256(b"move-source worker lease expired").hexdigest(),
                    }]
                    values["validation_receipt"] = {
                        **receipt,
                        "retry": {
                            "max_attempts": MOVE_SOURCE_MAX_ATTEMPTS,
                            "failures": failures,
                        },
                    }
                    if len(failures) >= MOVE_SOURCE_MAX_ATTEMPTS:
                        values[state_field] = "failed"
                        values["reason_code"] = "expired_lease_retry_exhausted"
                        values["validated_at"] = now
                    else:
                        values["reason_code"] = "move_source_retry_requested_after_expired_lease"
                if hasattr(row, "updated_at"):
                    values["updated_at"] = now
                if cancelled and hasattr(row, "completed_at"):
                    values["completed_at"] = now
                result = await session.execute(
                    update(table).where(
                        table.id == row.id,
                        getattr(table, state_field) == "running",
                        table.claim_token == observed_token,
                        table.lease_expires_at == observed_expiry,
                        table.lease_expires_at <= now,
                    ).values(**values)
                )
                if result.rowcount not in {0, 1}:
                    await session.rollback()
                    raise RuntimeError("expired lease recovery CAS affected an invalid row count")
                if result.rowcount == 1 and comparison_recovery_receipt is not None:
                    session.add(OntSignalComparisonEvent(
                        id=f"ont-comparison-event-{uuid.uuid4().hex}",
                        comparison_job_id=row.id,
                        state=str(values[state_field]),
                        reason_code=str(values["reason_code"]),
                        receipt={"lease_recovery": comparison_recovery_receipt},
                        created_at=now,
                    ))
            await session.commit()

    async def _recover_expired(self) -> None:
        now = self._now()
        for table, state_field in (
            (OntMoveTableSource, "validation_state"),
            (OntSignalCalibrationJob, "state"),
            (OntSignalMappingJob, "state"),
            (OntSquigualiserViewJob, "state"),
            (OntSignalComparisonJob, "state"),
        ):
            await self._recover_expired_table(table, state_field, now)

    @staticmethod
    async def _verify_managed_bed_parent(
        session: Any,
        render_params: dict[str, Any],
    ) -> tuple[Path, dict[str, Any]]:
        from services import ont_signal_workbench

        bed_id = render_params.get("managed_bed_artifact_id")
        if not bed_id:
            raise RuntimeError("managed BED immutable parent identity is absent")
        try:
            path, identity = await ont_signal_workbench.resolve_managed_bed_authority(
                session, str(bed_id)
            )
        except (KeyError, ont_signal_workbench.OntSignalError) as exc:
            raise RuntimeError("managed BED immutable parent authority is unavailable") from exc
        expected = {
            "artifact_id": str(bed_id),
            "source_job_id": render_params.get("managed_bed_source_job_id"),
            "sha256": render_params.get("managed_bed_sha256"),
            "size_bytes": render_params.get("managed_bed_size_bytes"),
        }
        if identity != expected:
            raise RuntimeError("managed BED diverged from the immutable parent contract")
        return path, identity

    async def _claim(self, table: Any, state_field: str) -> tuple[str, str] | None:
        token = uuid.uuid4().hex
        async with self._session_factory() as session:
            row = (await session.execute(select(table).where(
                getattr(table, state_field) == "requested",
                table.claim_token.is_(None),
                *((table.cancel_requested_at.is_(None),) if hasattr(table, "cancel_requested_at") else ()),
            ).order_by(table.created_at, table.id).limit(1))).scalar_one_or_none()
            if row is None:
                return None
            claim_conditions = [table.id == row.id, getattr(table, state_field) == "requested", table.claim_token.is_(None)]
            if hasattr(table, "cancel_requested_at"):
                claim_conditions.append(table.cancel_requested_at.is_(None))
            result = await session.execute(update(table).where(*claim_conditions).values(**{
                state_field: "running", "reason_code": "worker_claimed", "claim_token": token,
                "lease_expires_at": self._now() + timedelta(seconds=LEASE_SECONDS),
                **({"attempt": row.attempt + 1, "updated_at": self._now()} if hasattr(row, "attempt") else {}),
            }))
            if result.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            return str(row.id), token

    async def _fail(self, table: Any, state_field: str, item_id: str, token: str, exc: Exception) -> None:
        async with self._session_factory() as session:
            now = self._now()
            row = await session.get(table, item_id)
            if row is None or row.claim_token != token:
                return
            if hasattr(table, "cancel_requested_at") and row.cancel_requested_at is not None:
                await session.rollback()
                await self._cancel_claim(table, state_field, item_id, token)
                return
            failure_reason = _comparison_failure_reason(exc)
            values: dict[str, Any] = {
                state_field: "failed", "reason_code": failure_reason,
                "claim_token": None, "lease_expires_at": None,
            }
            if hasattr(table, "failure_code"): values["failure_code"] = exc.__class__.__name__
            if hasattr(table, "failure_message"): values["failure_message"] = str(exc)[:MAX_FAILURE]
            if hasattr(table, "updated_at"): values["updated_at"] = now
            if hasattr(table, "completed_at"): values["completed_at"] = now
            if isinstance(row, OntMoveTableSource):
                receipt = row.validation_receipt if isinstance(row.validation_receipt, dict) else {}
                retry_value: Any = receipt.get("retry")
                retry: dict[str, Any] = retry_value if isinstance(retry_value, dict) else {}
                failures_value: Any = retry.get("failures")
                prior_failures: list[Any] = failures_value if isinstance(failures_value, list) else []
                failures = [*prior_failures, {
                    "attempt": len(prior_failures) + 1,
                    "failed_at": self._now().isoformat(),
                    "failure_code": exc.__class__.__name__,
                    "message_sha256": hashlib.sha256(str(exc).encode()).hexdigest(),
                }]
                values["validation_receipt"] = {
                    **receipt,
                    "retry": {
                        "max_attempts": MOVE_SOURCE_MAX_ATTEMPTS,
                        "failures": failures,
                    },
                }
                if len(failures) < MOVE_SOURCE_MAX_ATTEMPTS:
                    values[state_field] = "requested"
                    values["reason_code"] = "move_source_retry_requested"
                else:
                    values["reason_code"] = "runtime_validation_failed_retry_exhausted"
                    values["validated_at"] = self._now()
            if isinstance(row, OntSignalCalibrationJob):
                values["stage_receipts"] = {**(row.stage_receipts or {}), "failure": {"failed_at": self._now().isoformat(), "failure_code": exc.__class__.__name__, "message_sha256": hashlib.sha256(str(exc).encode()).hexdigest()}}
            conditions = [table.id == item_id, table.claim_token == token, getattr(table, state_field) == "running"]
            if hasattr(table, "cancel_requested_at"):
                conditions.append(table.cancel_requested_at.is_(None))
            if hasattr(table, "lease_expires_at"):
                conditions.append(table.lease_expires_at > now)
            result = await session.execute(update(table).where(*conditions).values(**values))
            if result.rowcount != 1:
                await session.rollback()
                return
            if isinstance(row, OntSignalMappingJob):
                session.add(OntSignalMappingEvent(id=f"ont-signal-event-{uuid.uuid4().hex}", job_id=row.id, state="failed", reason_code=failure_reason, receipt={"error_class": exc.__class__.__name__}, created_at=self._now()))
            if isinstance(row, OntSignalComparisonJob):
                session.add(OntSignalComparisonEvent(id=f"ont-comparison-event-{uuid.uuid4().hex}", comparison_job_id=row.id, state="failed", reason_code=failure_reason, receipt={"error_class": exc.__class__.__name__}, created_at=self._now()))
            await session.commit()

    @staticmethod
    def _raw_paths(representation: OntRawSignalRepresentation) -> list[Path]:
        pairs, _identities = OntSignalWorker._resolve_selected_raw_partitions(representation, None)
        return [path for path, _index in pairs]

    @staticmethod
    def _governed_parent_roots() -> tuple[Path, ...]:
        roots = [Path(value) for value in get_allowed_roots().values()]
        managed_output_root = _lexical_absolute(OntSignalWorker._output_root())
        if managed_output_root not in roots:
            roots.append(managed_output_root)
        raw_signal_root = _lexical_absolute(
            Path(
                os.getenv(
                    ont_raw_signal.BLOW5_STAGING_ROOT_ENV,
                    ont_raw_signal.BLOW5_DEFAULT_STAGING_ROOT,
                )
            ).parent
            / "ont-raw-signal"
        )
        if raw_signal_root not in roots:
            roots.append(raw_signal_root)
        configured = os.getenv(ont_signal_workbench.EXTERNAL_MOVE_BAM_ROOT_ENV, "").strip()
        external_root = ont_signal_workbench._lexical_absolute_path(configured)
        if external_root is not None and external_root not in roots:
            roots.append(external_root)
        return tuple(roots)

    @staticmethod
    async def _resolve_move_bam_authority(
        session: Any, source: OntMoveTableSource
    ) -> MoveBamAuthority:
        if source.external_registration_receipt_id is None:
            tracked = await session.get(InputFile, source.input_file_id)
            if tracked is None or source.source_job_id is None:
                raise RuntimeError("original move BAM authority disappeared")
            return MoveBamAuthority(path=Path(tracked.directory) / tracked.filename, receipt=None)
        if source.source_job_id is not None:
            raise RuntimeError("move source has conflicting producer authorities")
        receipt = await session.get(
            OntExternalMoveBamRegistrationReceipt,
            source.external_registration_receipt_id,
        )
        if receipt is None:
            raise RuntimeError("external move-BAM registration receipt disappeared")
        if (
            receipt.run_id != source.run_id
            or receipt.observed_generation != source.observed_generation
            or receipt.raw_representation_id != source.raw_representation_id
            or receipt.artifact_sha256 != source.artifact_sha256
            or receipt.artifact_size_bytes != source.artifact_size_bytes
            or receipt.molecule_type != source.molecule_type
        ):
            raise RuntimeError("external move-BAM receipt does not bind the exact move-source tuple")
        relative = Path(receipt.server_relative_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(component in {"", ".", ".."} for component in relative.parts)
            or not relative.name.lower().endswith(".bam")
        ):
            raise RuntimeError("external move-BAM receipt path authority is invalid")
        _root, root_fd, root_info = ont_signal_workbench._open_external_move_bam_root()
        if (root_info.st_dev, root_info.st_ino) != (receipt.root_device, receipt.root_inode):
            os.close(root_fd)
            raise RuntimeError("external move BAM identity diverged from registration receipt")
        return MoveBamAuthority(
            path=None,
            receipt=receipt,
            root_fd=root_fd,
            relative=relative,
        )

    @staticmethod
    async def _pin_move_bam_authority(
        parents: RetainedParentSet,
        authority: MoveBamAuthority,
        *,
        alias: str,
        expected_sha256: str,
        expected_size: int,
    ) -> RetainedParent:
        if authority.root_fd is not None and authority.relative is not None:
            return await parents.pin_beneath_root_async(
                authority.root_fd,
                authority.relative,
                alias=alias,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )
        if authority.path is None:
            raise RuntimeError("move BAM authority has no retained source")
        return await OntSignalWorker._pin_parent_async(
            parents,
            authority.path,
            alias=alias,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )

    @staticmethod
    def _assert_external_move_bam_identity(
        retained: RetainedParent,
        authority: MoveBamAuthority,
    ) -> None:
        receipt = authority.receipt
        if receipt is None:
            return
        if authority.root_fd is None:
            raise RuntimeError("external move BAM root descriptor authority disappeared")
        root_info = os.fstat(authority.root_fd)
        current = os.fstat(retained.fd)
        if (
            root_info.st_dev,
            root_info.st_ino,
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
            retained.sha256,
        ) != (
            receipt.root_device,
            receipt.root_inode,
            receipt.file_device,
            receipt.file_inode,
            receipt.artifact_size_bytes,
            receipt.file_mtime_ns,
            receipt.file_ctime_ns,
            receipt.artifact_sha256,
        ):
            raise RuntimeError("external move BAM identity diverged from registration receipt")

    @staticmethod
    def _validate_move_source_producer_authority(
        source: OntMoveTableSource,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        identity = source.source_runtime_identity if isinstance(source.source_runtime_identity, dict) else {}
        counts = report.get("tag_counts") if isinstance(report.get("tag_counts"), dict) else {}
        record_count = report.get("record_count")
        unique_count = report.get("unique_read_count")
        inventory_sha256 = report.get("read_inventory_sha256")
        if (
            identity.get("schema") != "bms.ont-move-source-producer-runtime.v1"
            or identity.get("source_job_id") != source.source_job_id
            or identity.get("source_bam_sha256") != source.artifact_sha256
            or isinstance(record_count, bool)
            or not isinstance(record_count, int)
            or record_count <= 0
            or unique_count != record_count
            or counts != {"mv": record_count, "ts": record_count, "ns": record_count}
            or not isinstance(inventory_sha256, str)
            or not HEX64.fullmatch(inventory_sha256)
        ):
            raise RuntimeError("move report lacks exhaustive independent move-tag/model/read-set validation")
        state = identity.get("authority_state")
        if state == "legacy_unknown":
            if (
                identity.get("reason_code") != "producer_runtime_provenance_unavailable"
                or identity.get("requires_independent_move_validation") is not True
            ):
                raise RuntimeError("legacy move-source authority is incomplete")
            return {
                "authority_state": "legacy_unknown",
                "basecall_model_id": report["basecall_model_id"],
                "emit_moves": "validated_from_bam_tags",
                "independent_move_validation": True,
            }
        if state != "verified":
            raise RuntimeError("move-source producer runtime authority state is invalid")
        if identity.get("emit_moves") is not True:
            raise RuntimeError("verified producer emit-moves authority is not true")
        if identity.get("basecall_model_id") != report.get("basecall_model_id"):
            raise RuntimeError("validated BAM basecall model diverges from verified producer authority")
        if (
            identity.get("read_count") != record_count
            or identity.get("read_inventory_sha256") != inventory_sha256
            or identity.get("move_tag_counts") != counts
        ):
            raise RuntimeError("validated BAM move/read-set evidence diverges from verified producer authority")
        return {
            "authority_state": "verified",
            "basecall_model_id": report["basecall_model_id"],
            "emit_moves": True,
            "independent_move_validation": True,
        }

    @staticmethod
    def _move_registration_authority(source: OntMoveTableSource) -> dict[str, Any]:
        runtime_identity = source.source_runtime_identity
        return {
            "source_job_id": source.source_job_id,
            "external_registration_receipt_id": source.external_registration_receipt_id,
            "source_runtime_identity": json.loads(json.dumps(runtime_identity, sort_keys=True)),
        }

    @staticmethod
    def _move_publication_fence(
        item_id: str,
        token: str,
        authority: dict[str, Any],
    ) -> tuple[Any, ...]:
        return (
            OntMoveTableSource.id == item_id,
            OntMoveTableSource.claim_token == token,
            OntMoveTableSource.validation_state == "running",
            OntMoveTableSource.lease_expires_at > datetime.now(UTC).replace(tzinfo=None),
            OntMoveTableSource.source_job_id == authority["source_job_id"],
            OntMoveTableSource.external_registration_receipt_id
            == authority["external_registration_receipt_id"],
            OntMoveTableSource.source_runtime_identity == authority["source_runtime_identity"],
        )

    async def _process_move(self, item_id: str, token: str) -> None:
        with RetainedParentSet(self._governed_parent_roots()) as parents:
            await self._process_move_retained(item_id, token, parents)

    async def _process_move_retained(
        self, item_id: str, token: str, parents: RetainedParentSet
    ) -> None:
        async with self._session_factory() as session:
            source = await session.get(OntMoveTableSource, item_id)
            if source is None or source.claim_token != token: return
            registration_authority = self._move_registration_authority(source)
            representation = await session.get(OntRawSignalRepresentation, source.raw_representation_id)
            if representation is None: raise RuntimeError("move-source parents disappeared")
            bam_authority = await self._resolve_move_bam_authority(session, source)
            retained_bam = await self._pin_move_bam_authority(
                parents,
                bam_authority,
                alias="moves.bam",
                expected_sha256=source.artifact_sha256,
                expected_size=source.artifact_size_bytes,
            )
            self._assert_external_move_bam_identity(retained_bam, bam_authority)
            raw_manifest_sha = (source.validation_receipt or {}).get("raw_manifest_sha256")
            self._require_hash_contract("raw manifest", raw_manifest_sha, representation.manifest_sha256)
            raw_pairs, raw_identities = await self._resolve_selected_raw_partitions_async(representation, None)
            raw_identities["blow5"] = await self._pin_raw_partitions_async(parents, representation, raw_pairs)
            output = self._output_root() / "move-sources" / source.id
            self._prepare_output_directory(output, source.id)
            arguments = ["validate-moves", "--bam", "/parents/moves.bam", "--molecule-type", source.molecule_type, "--filtered-bam", "/output/filtered_moves.bam", "--inventory", "/output/read_inventory.txt", "--report", "/output/validation.json"]
            for index, _pair in enumerate(raw_pairs):
                arguments.extend(["--blow5", f"/parents/raw-{index}.blow5"])
        command_receipt = await self._invoke(parents, arguments, "move", item_id, token, output)
        report = self._read_json_report(output / "validation.json")
        producer_validation = self._validate_move_source_producer_authority(source, report)
        expected_parents = {"original_move_bam_sha256": retained_bam.sha256, "blow5": raw_identities["blow5"]}
        self._require_hash_contract("move runtime parents", expected_parents, report.get("parent_sha256s"))
        filtered_path, inventory_path = output / "filtered_moves.bam", output / "read_inventory.txt"
        filtered_sha, filtered_size = await self._stable_file_identity_async(filtered_path)
        inventory_sha, inventory_size = await self._stable_file_identity_async(inventory_path)
        self._require_hash_contract("filtered move BAM", {"sha256": filtered_sha, "size_bytes": filtered_size}, report.get("filtered_move_bam"))
        self._require_hash_contract("move inventory", inventory_sha, report.get("read_inventory_sha256"))
        async with self._session_factory() as session:
            parents.assert_unbroken()
            source = await session.get(OntMoveTableSource, item_id)
            if source is None: raise TerminalFenceLost("move-source disappeared before publication")
            receipt = {**report, "producer_runtime_authority_validation": producer_validation, "command": command_receipt, "managed_outputs": {"filtered_move_bam": str(filtered_path), "read_inventory": str(inventory_path)}, "managed_output_sha256s": {"filtered_move_bam_sha256": filtered_sha, "filtered_move_bam_size_bytes": filtered_size, "read_inventory_sha256": inventory_sha, "read_inventory_size_bytes": inventory_size}}
            result = await session.execute(update(OntMoveTableSource).where(
                *self._move_publication_fence(item_id, token, registration_authority),
            ).values(
                bam_header_sha256=report["move_bam_header_sha256"], record_count=report["record_count"],
                unique_read_count=report["unique_read_count"], mv_tag_count=report["tag_counts"]["mv"],
                ts_tag_count=report["tag_counts"]["ts"], ns_tag_count=report["tag_counts"]["ns"],
                basecall_model_id=report["basecall_model_id"], read_inventory_sha256=report["read_inventory_sha256"],
                validation_receipt=receipt, validation_state="ready", reason_code="move_source_exact_read_set_ready",
                claim_token=None, lease_expires_at=None, validated_at=self._now(),
            ))
            if result.rowcount != 1:
                await session.rollback()
                raise TerminalFenceLost("move-source terminal publication fence was lost")
            parents.assert_unbroken()
            await session.commit()

    async def _process_calibration(self, item_id: str, token: str) -> None:
        with RetainedParentSet(self._governed_parent_roots()) as parents:
            await self._process_calibration_retained(item_id, token, parents)

    async def _process_calibration_retained(
        self, item_id: str, token: str, parents: RetainedParentSet
    ) -> None:
        async with self._session_factory() as session:
            job = await session.get(OntSignalCalibrationJob, item_id)
            if job is None or job.claim_token != token: return
            if job.cancel_requested_at is not None: raise asyncio.CancelledError()
            source = await session.get(OntMoveTableSource, job.move_source_id)
            representation = await session.get(OntRawSignalRepresentation, job.raw_representation_id)
            if source is None or representation is None or source.validation_state != "ready" or representation.state != "ready":
                raise RuntimeError("calibration parents are not ready")
            original_bam_authority = await self._resolve_move_bam_authority(session, source)
            outputs = source.validation_receipt.get("managed_outputs", {}) if isinstance(source.validation_receipt, dict) else {}
            filtered_bam = Path(str(outputs.get("filtered_move_bam", "")))
            inventory = Path(str(outputs.get("read_inventory", "")))
            managed_hashes = source.validation_receipt.get("managed_output_sha256s", {})
            retained_original = await self._pin_move_bam_authority(
                parents,
                original_bam_authority,
                alias="original_moves.bam",
                expected_sha256=source.artifact_sha256,
                expected_size=source.artifact_size_bytes,
            )
            self._assert_external_move_bam_identity(retained_original, original_bam_authority)
            retained_filtered = await self._pin_parent_async(
                parents,
                filtered_bam,
                alias="filtered_moves.bam",
                expected_sha256=str(managed_hashes.get("filtered_move_bam_sha256") or ""),
                expected_size=int(managed_hashes.get("filtered_move_bam_size_bytes") or 0),
            )
            retained_inventory = await self._pin_parent_async(
                parents,
                inventory,
                alias="read_inventory.txt",
                expected_sha256=str(source.read_inventory_sha256 or ""),
                expected_size=int(managed_hashes.get("read_inventory_size_bytes") or 0),
            )
            original_sha, original_size = retained_original.sha256, retained_original.size_bytes
            filtered_sha, filtered_size = retained_filtered.sha256, retained_filtered.size_bytes
            inventory_sha, inventory_size = retained_inventory.sha256, retained_inventory.size_bytes
            raw_pairs, raw_identities = await self._resolve_selected_raw_partitions_async(representation, None)
            raw_identities["blow5"] = await self._pin_raw_partitions_async(parents, representation, raw_pairs)
            output = self._output_root() / "calibrations" / job.id
            self._prepare_output_directory(output, job.id)
            args = [
                "calibrate", "--original-bam", "/parents/original_moves.bam", "--filtered-bam", "/parents/filtered_moves.bam", "--inventory", "/parents/read_inventory.txt", "--molecule-type", source.molecule_type, "--sample-count", str(job.sample_count),
                "--raw-manifest-sha256", representation.manifest_sha256, "--move-artifact-sha256", source.artifact_sha256,
                "--move-inventory-sha256", str(source.read_inventory_sha256), "--basecall-model-id", str(source.basecall_model_id),
                "--output-dir", "/output", "--report", "/output/calibration.json",
            ]
            for index, _pair in enumerate(raw_pairs):
                args.extend(["--blow5", f"/parents/raw-{index}.blow5"])
            snapshot_parents = job.resource_snapshot.get("parents", {})
        command_receipt = await self._invoke(parents, args, "calibration", item_id, token, output)
        report_path = output / "calibration.json"
        report, artifact_sha, artifact_size = self._read_json_report_identity(report_path)
        recommendation = report.get("recommendation", {})
        selected = report.get("sample_selection", {})
        tool = report.get("tool_identity", {})
        score_evidence = report.get("score_evidence")
        selected_ids = selected.get("read_ids")
        selection_sha = hashlib.sha256(json.dumps(selected_ids, sort_keys=True, separators=(",", ":")).encode()).hexdigest() if isinstance(selected_ids, list) else None
        if (
            report.get("schema") != "bms.ont-signal-calibration.v1"
            or report.get("parent_sha256s", {}).get("raw_manifest_sha256") != snapshot_parents.get("raw_manifest_sha256")
            or report.get("parent_sha256s", {}).get("move_bam_sha256") != snapshot_parents.get("move_bam_sha256")
            or report.get("parent_sha256s", {}).get("filtered_move_bam_sha256") != filtered_sha
            or report.get("parent_sha256s", {}).get("move_inventory_actual_sha256") != snapshot_parents.get("move_read_inventory_sha256")
            or report.get("parent_sha256s", {}).get("blow5") != raw_identities["blow5"]
            or report.get("basecall_model_id") != snapshot_parents.get("basecall_model_id")
            or selected.get("requested_count") != job.sample_count or selected.get("selected_count") != job.sample_count
            or not isinstance(selected_ids, list) or len(selected_ids) != job.sample_count
            or len(set(selected_ids)) != job.sample_count or any(not isinstance(read_id, str) or not read_id for read_id in selected_ids)
            or selected.get("selection_sha256") != selection_sha
            or tool.get("version") != "0.7.0" or tool.get("commit") != "5a2404f1f43bc3227a85475c59b2b77970078b2e"
            or recommendation.get("kmer_length") not in range(1, 10)
            or recommendation.get("signal_move_offset") not in range(0, 9)
            or recommendation.get("kmer_length") != recommendation.get("signal_move_offset") + 1
            or not isinstance(score_evidence, list) or len(score_evidence) != 9
            or [item.get("candidate_signal_move_offset") for item in score_evidence if isinstance(item, dict)] != list(range(9))
            or any(not isinstance(item, dict) or item.get("read_count") != job.sample_count or not isinstance(item.get("score"), (int, float)) for item in score_evidence)
        ):
            raise RuntimeError("calibration report failed governed validation")
        async with self._session_factory() as session:
            parents.assert_unbroken()
            job = await session.get(OntSignalCalibrationJob, item_id)
            if job is not None and job.claim_token == token and job.cancel_requested_at is not None:
                raise asyncio.CancelledError()
            if job is None or job.claim_token != token or job.calibration_artifact_id is not None:
                raise RuntimeError("calibration lease or single-publication invariant was lost")
            artifact = OntSignalCalibrationArtifact(
                id=f"ont-signal-calibration-artifact-{uuid.uuid4().hex}", raw_representation_id=job.raw_representation_id,
                move_source_id=job.move_source_id, basecall_model_id=report["basecall_model_id"],
                sample_selection=report["sample_selection"], recommended_kmer_length=recommendation["kmer_length"],
                recommended_signal_move_offset=recommendation["signal_move_offset"], score_evidence=report["score_evidence"],
                runtime_identity={**self._runtime_identity(), **tool}, parent_sha256s=report["parent_sha256s"],
                artifact_sha256=artifact_sha, created_at=self._now(),
            )
            session.add(artifact); await session.flush()
            result = await session.execute(update(OntSignalCalibrationJob).where(
                OntSignalCalibrationJob.id == item_id,
                OntSignalCalibrationJob.claim_token == token,
                OntSignalCalibrationJob.state == "running",
                OntSignalCalibrationJob.cancel_requested_at.is_(None),
                OntSignalCalibrationJob.lease_expires_at > self._now(),
                OntSignalCalibrationJob.calibration_artifact_id.is_(None),
            ).values(
                calibration_artifact_id=artifact.id, state="ready", reason_code="validated_calibration_ready",
                claim_token=None, lease_expires_at=None, updated_at=self._now(), completed_at=self._now(),
                stage_receipts={**(job.stage_receipts or {}), "runtime": command_receipt, "report_sha256": artifact_sha, "report_size_bytes": artifact_size, "validation": report.get("validation", {})},
            ))
            if result.rowcount != 1:
                await session.rollback()
                current = await session.get(OntSignalCalibrationJob, item_id)
                if current is not None and current.cancel_requested_at is not None:
                    raise asyncio.CancelledError()
                raise TerminalFenceLost("calibration terminal publication fence was lost")
            parents.assert_unbroken()
            await session.commit()

    @staticmethod
    def _alignment_authority(job: Job) -> dict[str, str]:
        params = job.params if isinstance(job.params, dict) else {}
        values = {"source_reference_sha256": params.get("reference_sequence_sha256"), "workflow_id": params.get("ont_workflow_id") or params.get("workflow_id"), "input_mode": params.get("ont_input_mode") or params.get("input_mode")}
        if not all(isinstance(value, str) and value for value in values.values()): raise RuntimeError("alignment job authority is incomplete")
        return {key: str(value) for key, value in values.items()}

    async def _process_mapping(self, item_id: str, token: str) -> None:
        with RetainedParentSet(self._governed_parent_roots()) as parents:
            await self._process_mapping_retained(item_id, token, parents)

    async def _process_mapping_retained(
        self, item_id: str, token: str, parents: RetainedParentSet
    ) -> None:
        async with self._session_factory() as session:
            job = await session.get(OntSignalMappingJob, item_id)
            if job is None or job.claim_token != token: return
            if job.cancel_requested_at is not None: raise asyncio.CancelledError()
            parent_snapshot = job.resource_snapshot if isinstance(job.resource_snapshot, dict) else {}
            parent_identities = parent_snapshot.get("parents", {})
            if not isinstance(parent_identities, dict):
                raise RuntimeError("mapping parent authority is incomplete")
            source = await session.get(OntMoveTableSource, job.move_source_id)
            profile = await session.get(OntSignalMappingProfile, job.mapping_profile_id)
            representation = await session.get(OntRawSignalRepresentation, job.raw_representation_id)
            if source is None or profile is None or representation is None or source.validation_state != "ready": raise RuntimeError("mapping parents are not ready")
            original_bam_authority = await self._resolve_move_bam_authority(session, source)
            outputs = source.validation_receipt.get("managed_outputs", {})
            filtered_bam = Path(str(outputs.get("filtered_move_bam", ""))); inventory = Path(str(outputs.get("read_inventory", "")))
            managed_hashes = source.validation_receipt.get("managed_output_sha256s", {})
            retained_original = await self._pin_move_bam_authority(
                parents,
                original_bam_authority,
                alias="original_moves.bam",
                expected_sha256=source.artifact_sha256,
                expected_size=source.artifact_size_bytes,
            )
            self._assert_external_move_bam_identity(retained_original, original_bam_authority)
            retained_filtered = await self._pin_parent_async(
                parents,
                filtered_bam,
                alias="filtered_moves.bam",
                expected_sha256=str(managed_hashes.get("filtered_move_bam_sha256") or ""),
                expected_size=int(managed_hashes.get("filtered_move_bam_size_bytes") or 0),
            )
            retained_inventory = await self._pin_parent_async(
                parents,
                inventory,
                alias="read_inventory.txt",
                expected_sha256=str(source.read_inventory_sha256 or ""),
                expected_size=int(managed_hashes.get("read_inventory_size_bytes") or 0),
            )
            original_sha, original_size = retained_original.sha256, retained_original.size_bytes
            filtered_sha, filtered_size = retained_filtered.sha256, retained_filtered.size_bytes
            inventory_sha, inventory_size = retained_inventory.sha256, retained_inventory.size_bytes
            raw_pairs, raw_identities = await self._resolve_selected_raw_partitions_async(representation, None)
            raw_identities["blow5"] = await self._pin_raw_partitions_async(parents, representation, raw_pairs)
            output = self._output_root() / "mappings" / job.id
            self._prepare_output_directory(output, job.id)
            alignment_sha: str | None = None
            alignment_index_sha: str | None = None
            reference_sha: str | None = None
            if job.mode == "signal_to_read":
                args = ["reform", "--original-bam", "/parents/original_moves.bam", "--filtered-bam", "/parents/filtered_moves.bam", "--inventory", "/parents/read_inventory.txt", "--molecule-type", source.molecule_type, "--kmer-length", str(profile.kmer_length), "--signal-move-offset", str(profile.signal_move_offset), "--output", "/output/reform.paf", "--report", "/output/validation.json"]
                for index, _pair in enumerate(raw_pairs): args.extend(["--blow5", f"/parents/raw-{index}.blow5"])
                artifact_kind, artifact_path, media = "reform_paf", output / "reform.paf", "text/plain"
                expected_runtime_parents = {"original_move_bam_sha256": original_sha, "filtered_move_bam_sha256": filtered_sha, "move_inventory_sha256": inventory_sha, "blow5": raw_identities["blow5"]}
            else:
                parent_artifact = (await session.execute(select(OntSignalMappingArtifact).where(OntSignalMappingArtifact.mapping_job_id == job.parent_mapping_job_id, OntSignalMappingArtifact.kind == "reform_paf"))).scalar_one()
                alignment_job = await session.get(Job, job.alignment_job_id)
                if alignment_job is None: raise RuntimeError("alignment job disappeared")
                alignment_bam, alignment_meta, alignment_index, alignment_index_meta = await self._resolve_session_alignment_bundle_async(
                    alignment_job.id,
                    str(job.alignment_session_id),
                    self._alignment_authority(alignment_job),
                    getattr(alignment_job, "child_output_dir", None) or alignment_job.output_dir,
                )
                parent_reform = Path(parent_artifact.managed_relative_path)
                retained_reform = await self._pin_parent_async(
                    parents,
                    parent_reform,
                    alias="reform.paf",
                    expected_sha256=parent_artifact.sha256,
                    expected_size=parent_artifact.size_bytes,
                )
                retained_alignment = await self._pin_parent_async(
                    parents,
                    alignment_bam,
                    alias="alignment.bam",
                    expected_sha256=str(alignment_meta.get("sha256") or ""),
                    expected_size=int(alignment_meta.get("size_bytes") or 0),
                )
                retained_alignment_index = await self._pin_parent_async(
                    parents,
                    alignment_index,
                    alias="alignment.bam.bai",
                    expected_sha256=str(alignment_index_meta.get("sha256") or ""),
                    expected_size=int(alignment_index_meta.get("size_bytes") or 0),
                )
                parent_reform_sha = retained_reform.sha256
                alignment_sha, alignment_size = retained_alignment.sha256, retained_alignment.size_bytes
                alignment_index_sha, alignment_index_size = retained_alignment_index.sha256, retained_alignment_index.size_bytes
                async with self._domain_session_factory() as domain_session:
                    revision = await domain_session.get(MolBioNGSReferenceRevision, job.reference_revision_id)
                    artifact = None if revision is None else await domain_session.get(MolBioNGSReferenceArtifact, revision.artifact_id)
                    if revision is None or artifact is None: raise RuntimeError("managed reference authority disappeared")
                    expected_domain_revision = parent_identities.get("domain_revision")
                    live_domain_revision = await ont_signal_workbench._resolve_domain_revision_authority(
                        domain_session, revision.global_domain_experiment_id
                    )
                    if expected_domain_revision != live_domain_revision:
                        raise RuntimeError("mapping domain revision authority diverged")
                    reference = get_molbio_ngs_reference_root() / artifact.managed_relative_path
                    retained_reference = await self._pin_parent_async(
                        parents,
                        reference,
                        alias="reference.fasta",
                        expected_sha256=artifact.sha256,
                        expected_size=artifact.size_bytes,
                    )
                    reference_sha, reference_size = retained_reference.sha256, retained_reference.size_bytes
                args = ["realign", "--original-bam", "/parents/original_moves.bam", "--filtered-bam", "/parents/filtered_moves.bam", "--inventory", "/parents/read_inventory.txt", "--reform-paf", "/parents/reform.paf", "--alignment-bam", "/parents/alignment.bam", "--alignment-index", "/parents/alignment.bam.bai", "--reference-fasta", "/parents/reference.fasta", "--molecule-type", source.molecule_type, "--kmer-length", str(profile.kmer_length), "--signal-move-offset", str(profile.signal_move_offset), "--output", "/output/realign.paf", "--report", "/output/validation.json"]
                for index, _pair in enumerate(raw_pairs): args.extend(["--blow5", f"/parents/raw-{index}.blow5"])
                artifact_kind, artifact_path, media = "realign_paf", output / "realign.paf.gz", "application/gzip"
                expected_runtime_parents = {"original_move_bam_sha256": original_sha, "filtered_move_bam_sha256": filtered_sha, "move_inventory_sha256": inventory_sha, "blow5": raw_identities["blow5"], "parent_reform_sha256": parent_reform_sha, "managed_reference_sha256": reference_sha, "alignment_bam_sha256": alignment_sha, "alignment_index_sha256": alignment_index_sha}
            self._require_hash_contract("mapping original move snapshot", parent_identities.get("move_bam_sha256"), original_sha)
            self._require_hash_contract("mapping inventory snapshot", parent_identities.get("move_read_inventory_sha256"), inventory_sha)
            self._require_hash_contract("mapping raw manifest snapshot", parent_identities.get("raw_manifest_sha256"), representation.manifest_sha256)
            if job.mode == "signal_to_reference":
                if alignment_sha is None or alignment_index_sha is None or reference_sha is None:
                    raise RuntimeError("mapping reference runtime hash evidence is incomplete")
                alignment_snapshot = parent_identities.get("alignment_artifacts")
                if not isinstance(alignment_snapshot, dict):
                    raise RuntimeError("mapping alignment resource snapshot is incomplete")
                self._require_hash_contract("mapping alignment BAM snapshot", alignment_snapshot.get("alignment", {}).get("sha256"), alignment_sha)
                self._require_hash_contract("mapping alignment index snapshot", alignment_snapshot.get("alignment_index", {}).get("sha256"), alignment_index_sha)
                self._require_hash_contract("mapping managed reference snapshot", parent_identities.get("reference_fasta_sha256"), reference_sha)
        command_receipt = await self._invoke(parents, args, "mapping", item_id, token, output)
        validation = self._read_json_report(output / "validation.json")
        self._require_hash_contract("mapping runtime parents", expected_runtime_parents, validation.get("parent_sha256s"))
        validation = {
            **validation,
            "domain_revision": parent_identities.get("domain_revision"),
        }
        digest, artifact_size = await self._stable_file_identity_async(artifact_path)
        if validation.get("output_sha256") != digest:
            raise RuntimeError("mapping artifact digest does not match the runtime validation receipt")
        if artifact_kind == "realign_paf":
            adjacent_index = Path(f"{artifact_path}.tbi")
            index_digest, index_size = await self._stable_file_identity_async(adjacent_index)
            if validation.get("index_sha256") != index_digest:
                raise RuntimeError("realignment index does not match the runtime validation receipt")
            validation = {**validation, "index_size_bytes": index_size}
        async with self._session_factory() as session:
            parents.assert_unbroken()
            job = await session.get(OntSignalMappingJob, item_id)
            if job is None or job.claim_token != token: raise RuntimeError("mapping lease lost before publication")
            artifact = OntSignalMappingArtifact(
                id=f"ont-signal-artifact-{uuid.uuid4().hex}", mapping_job_id=job.id, kind=artifact_kind,
                managed_relative_path=str(artifact_path), media_type=media, sha256=digest, size_bytes=artifact_size,
                parent_identities=parent_identities, runtime_identity=self._runtime_identity(), validation_receipt=validation,
                created_at=self._now(),
            )
            session.add(artifact)
            await session.flush()
            reason = f"validated_{job.mode}_mapping_ready"
            result = await session.execute(update(OntSignalMappingJob).where(
                OntSignalMappingJob.id == item_id,
                OntSignalMappingJob.claim_token == token,
                OntSignalMappingJob.state == "running",
                OntSignalMappingJob.cancel_requested_at.is_(None),
                OntSignalMappingJob.lease_expires_at > self._now(),
            ).values(
                state="ready", reason_code=reason, claim_token=None, lease_expires_at=None,
                stage_receipts={**(job.stage_receipts or {}), "runtime": command_receipt, "validation": validation},
                updated_at=self._now(), completed_at=self._now(),
            ))
            if result.rowcount != 1:
                await session.rollback()
                current = await session.get(OntSignalMappingJob, item_id)
                if current is not None and current.cancel_requested_at is not None: raise asyncio.CancelledError()
                raise TerminalFenceLost("mapping terminal publication fence was lost")
            session.add(OntSignalMappingEvent(id=f"ont-signal-event-{uuid.uuid4().hex}", job_id=job.id, state="ready", reason_code=reason, receipt={"artifact_sha256": digest, "runtime": self._runtime_identity()}, created_at=self._now()))
            parents.assert_unbroken()
            await session.commit()

    async def _process_view(self, item_id: str, token: str) -> None:
        with RetainedParentSet(self._governed_parent_roots()) as parents:
            await self._process_view_retained(item_id, token, parents)

    async def _process_view_retained(
        self, item_id: str, token: str, parents: RetainedParentSet
    ) -> None:
        async with self._session_factory() as session:
            view = await session.get(OntSquigualiserViewJob, item_id)
            if view is None or view.claim_token != token: return
            if view.cancel_requested_at is not None: raise asyncio.CancelledError()
            artifact = await session.get(OntSignalMappingArtifact, view.mapping_artifact_id)
            mapping = None if artifact is None else await session.get(OntSignalMappingJob, artifact.mapping_job_id)
            representation = None if mapping is None else await session.get(OntRawSignalRepresentation, mapping.raw_representation_id)
            if artifact is None or mapping is None or representation is None or mapping.state != "ready": raise RuntimeError("render parents are not ready")
            source = await session.get(OntMoveTableSource, mapping.move_source_id)
            if source is None or source.validation_state != "ready": raise RuntimeError("render move-source authority is not ready")
            profile = await session.get(
                OntSignalMappingProfile, mapping.mapping_profile_id
            )
            if profile is None:
                raise RuntimeError("render mapping-profile authority is unavailable")
            if view.render_params.get("base_shift_source") == "profile":
                from services import ont_signal_workbench

                authority = ont_signal_workbench._mapping_profile_base_shift_authority(
                    profile
                )
                if (
                    view.render_params.get("base_shift_profile_id")
                    != authority["mapping_profile_id"]
                    or view.render_params.get("base_shift_profile_sha256")
                    != authority["profile_sha256"]
                    or view.render_params.get("base_shift_effective_value")
                    != authority["effective_value"]
                ):
                    raise RuntimeError("render mapping-profile authority diverged")
            mapping_parents = artifact.parent_identities if isinstance(artifact.parent_identities, dict) else {}
            self._require_hash_contract("render raw manifest snapshot", mapping_parents.get("raw_manifest_sha256"), representation.manifest_sha256)
            self._require_hash_contract("render original move snapshot", mapping_parents.get("move_bam_sha256"), source.artifact_sha256)
            self._require_hash_contract("render move inventory snapshot", mapping_parents.get("move_read_inventory_sha256"), source.read_inventory_sha256)
            source_outputs = source.validation_receipt.get("managed_outputs", {}) if isinstance(source.validation_receipt, dict) else {}
            filtered_moves = Path(str(source_outputs.get("filtered_move_bam", "")))
            mapping_path = Path(artifact.managed_relative_path)
            mapping_alias = "mapping.paf.gz" if artifact.kind == "realign_paf" else "mapping.paf"
            retained_mapping = await self._pin_parent_async(
                parents,
                mapping_path,
                alias=mapping_alias,
                expected_sha256=artifact.sha256,
                expected_size=artifact.size_bytes,
            )
            mapping_sha, mapping_size = retained_mapping.sha256, retained_mapping.size_bytes
            mapping_index_sha: str | None = None
            if artifact.kind == "realign_paf":
                mapping_index = Path(f"{mapping_path}.tbi")
                retained_mapping_index = await self._pin_parent_async(
                    parents,
                    mapping_index,
                    alias=f"{mapping_alias}.tbi",
                    expected_sha256=str(artifact.validation_receipt.get("index_sha256") or ""),
                    expected_size=int(artifact.validation_receipt.get("index_size_bytes") or 0),
                )
                mapping_index_sha = retained_mapping_index.sha256
            params = view.render_params
            output = self._output_root() / "views" / view.id
            self._prepare_output_directory(output, view.id)
            mapping_target = "/parents/mapping.paf.gz" if artifact.kind == "realign_paf" else "/parents/mapping.paf"
            selection_receipt: dict[str, Any] | None = None
            if view.mode == "read":
                selected_ids = [str(view.read_id)]
            else:
                candidate_limit = min(params["pileup_read_limit"], 5) if view.mode == "reference" else params["pileup_read_limit"]
                selection_args = [
                    "select-region", "--mapping", mapping_target,
                    "--region", f"{view.reference_contig}:{view.reference_start}-{view.reference_end}",
                    "--limit", str(candidate_limit), "--strand", params["strand"],
                    "--molecule-type", source.molecule_type, "--report", "/output/selection.json",
                ]
                selection_receipt = await self._invoke(
                    parents,
                    selection_args,
                    "view", item_id, token, output, {"selection.json"},
                )
                selection_path = output / "selection.json"
                selection = self._read_json_report(selection_path)
                self._require_hash_contract(
                    "render region-selection parents",
                    {"mapping_sha256": mapping_sha, "mapping_index_sha256": mapping_index_sha},
                    selection.get("parent_sha256s"),
                )
                selected_ids = selection.get("selected_read_ids")
                if (
                    selection.get("schema") != "bms.ont-signal-region-selection.v1"
                    or not isinstance(selected_ids, list)
                    or not 1 <= len(selected_ids) <= candidate_limit
                    or len(set(selected_ids)) != len(selected_ids)
                    or any(not isinstance(read_id, str) or not read_id for read_id in selected_ids)
                ):
                    raise RuntimeError("runtime region selection report is invalid")
                selection_path.unlink()
            blow5_paths, raw_identities = await self._resolve_selected_raw_partitions_async(representation, selected_ids)
            raw_identities["blow5"] = await self._pin_raw_partitions_async(parents, representation, blow5_paths)
            args = ["render", "--mode", view.mode, "--mapping", mapping_target, "--molecule-type", source.molecule_type, "--output-dir", "/output", "--report", "/output/render_manifest.json"]
            args.extend(_mapping_profile_render_args(profile))
            for index, _pair in enumerate(blow5_paths):
                args.extend(["--blow5", f"/parents/raw-{index}.blow5"])
            if view.mode == "read":
                expected_hashes = source.validation_receipt.get("managed_output_sha256s", {}) or {}
                retained_sequence = await self._pin_parent_async(
                    parents,
                    filtered_moves,
                    alias="filtered_moves.bam",
                    expected_sha256=str(expected_hashes.get("filtered_move_bam_sha256") or ""),
                    expected_size=int(expected_hashes.get("filtered_move_bam_size_bytes") or 0),
                )
                args.extend(["--read-id", str(view.read_id), "--sequence-bam", "/parents/filtered_moves.bam"])
                filtered_sha = retained_sequence.sha256
                reference_sha = None
            else:
                async with self._domain_session_factory() as domain_session:
                    revision = await domain_session.get(MolBioNGSReferenceRevision, mapping.reference_revision_id)
                    reference_artifact = None if revision is None else await domain_session.get(MolBioNGSReferenceArtifact, revision.artifact_id)
                    if revision is None or reference_artifact is None: raise RuntimeError("render reference authority disappeared")
                    reference = get_molbio_ngs_reference_root() / reference_artifact.managed_relative_path
                    retained_reference = await self._pin_parent_async(
                        parents,
                        reference,
                        alias="reference.fasta",
                        expected_sha256=reference_artifact.sha256,
                        expected_size=reference_artifact.size_bytes,
                    )
                    reference_sha = retained_reference.sha256
                args.extend(["--reference-fasta", "/parents/reference.fasta", "--region", f"{view.reference_contig}:{view.reference_start}-{view.reference_end}"])
                for read_id in selected_ids: args.extend(["--selected-read-id", read_id])
                filtered_sha = None
            args.extend(["--strand", params["strand"], "--signal-units", params["signal_units"], "--scale", params["scale"], "--base-shift", str(_effective_base_shift(params)), "--point-size", str(params["point_size"]), "--base-width", str(params["base_width"]), "--base-limit", str(params["base_limit"]), "--signal-sample-limit", str(params["signal_sample_limit"]), "--pileup-read-limit", str(params["pileup_read_limit"])])
            for key, flag in (("fixed_width", "--fixed-width"), ("loose_bound", "--loose-bound"), ("show_samples", "--show-samples"), ("show_base_colours", "--show-base-colours"), ("remove_signal_outliers", "--remove-signal-outliers")):
                if params.get(key): args.append(flag)
            managed_bed_id = params.get("managed_bed_artifact_id")
            managed_bed_identity: dict[str, Any] | None = None
            if managed_bed_id:
                bed_path, managed_bed_identity = await self._verify_managed_bed_parent(
                    session, params
                )
                await self._pin_parent_async(
                    parents,
                    bed_path,
                    alias="annotation.bed",
                    expected_sha256=str(managed_bed_identity["sha256"]),
                    expected_size=int(managed_bed_identity["size_bytes"]),
                )
                args.extend(["--bed", "/parents/annotation.bed"])
            expected_runtime_parents = {
                "mapping_sha256": mapping_sha,
                "mapping_index_sha256": mapping_index_sha,
                "blow5": raw_identities["blow5"],
                "sequence_bam_sha256": filtered_sha,
                "managed_reference_sha256": reference_sha,
                "managed_bed": None if managed_bed_identity is None else {
                    "sha256": managed_bed_identity["sha256"],
                    "size_bytes": managed_bed_identity["size_bytes"],
                },
            }
        command_receipt = await self._invoke(parents, args, "view", item_id, token, output)
        manifest = self._read_json_report(output / "render_manifest.json")
        self._require_hash_contract("render runtime parents", expected_runtime_parents, manifest.get("command", {}).get("parent_sha256s"))
        self._require_hash_contract("render selected read inventory", selected_ids, manifest.get("command", {}).get("selected_read_ids"))
        governed_root = self._output_root().resolve()
        for item in manifest["artifacts"]:
            artifact_path = (output / item.pop("filename")).resolve()
            if governed_root not in artifact_path.parents:
                raise RuntimeError("render artifact escaped the governed output root")
            actual_sha, actual_size = await self._stable_file_identity_async(artifact_path)
            self._require_hash_contract("render artifact", {"sha256": item.get("sha256"), "size_bytes": item.get("size_bytes")}, {"sha256": actual_sha, "size_bytes": actual_size})
            item["managed_relative_path"] = artifact_path.relative_to(governed_root).as_posix()
        async with self._session_factory() as session:
            parents.assert_unbroken()
            view = await session.get(OntSquigualiserViewJob, item_id)
            if view is None or view.claim_token != token: raise RuntimeError("view lease lost before publication")
            if managed_bed_identity is not None:
                _bed_path, publication_bed_identity = await self._verify_managed_bed_parent(
                    session, view.render_params
                )
                self._require_hash_contract(
                    "managed BED publication parent",
                    managed_bed_identity,
                    publication_bed_identity,
                )
            result = await session.execute(update(OntSquigualiserViewJob).where(
                OntSquigualiserViewJob.id == item_id,
                OntSquigualiserViewJob.claim_token == token,
                OntSquigualiserViewJob.state == "running",
                OntSquigualiserViewJob.cancel_requested_at.is_(None),
                OntSquigualiserViewJob.lease_expires_at > self._now(),
            ).values(
                output_manifest=manifest,
                render_receipt={**(view.render_receipt or {}), "runtime": self._runtime_identity(), "selection_command": selection_receipt, "command": command_receipt, "parent_sha256s": expected_runtime_parents},
                state="ready", reason_code="bounded_squigualiser_view_ready", claim_token=None, lease_expires_at=None,
                updated_at=self._now(), completed_at=self._now(),
            ))
            if result.rowcount != 1:
                await session.rollback()
                current = await session.get(OntSquigualiserViewJob, item_id)
                if current is not None and current.cancel_requested_at is not None: raise asyncio.CancelledError()
                raise TerminalFenceLost("view terminal publication fence was lost")
            parents.assert_unbroken()
            await session.commit()

    async def _process_comparison(self, item_id: str, token: str) -> None:
        """Run the descriptor-brokered producer before the comparison renderer."""
        with RetainedParentSet(self._governed_parent_roots()) as parents:
            await self._process_comparison_retained(item_id, token, parents)

    async def _process_comparison_retained(
        self, item_id: str, token: str, parents: RetainedParentSet
    ) -> None:
        output = self._output_root() / "comparisons" / item_id
        self._prepare_output_directory(output, item_id)
        async with self._session_factory() as session:
            job = await session.get(OntSignalComparisonJob, item_id)
            if job is None or job.claim_token != token or job.state != "running":
                raise TerminalFenceLost("comparison lease was lost before orchestration")
            if job.cancel_requested_at is not None:
                raise asyncio.CancelledError()
            artifact = await session.get(OntSignalMappingArtifact, job.mapping_artifact_id)
            mapping = None if artifact is None else await session.get(OntSignalMappingJob, artifact.mapping_job_id)
            viewer = await session.get(ont_signal_workbench.OntSignalViewerSession, job.viewer_session_id)
            representation = await session.get(OntRawSignalRepresentation, job.raw_representation_id)
            source = None if mapping is None else await session.get(OntMoveTableSource, mapping.move_source_id)
            profile = None if mapping is None else await session.get(OntSignalMappingProfile, mapping.mapping_profile_id)
            if (
                artifact is None or mapping is None or viewer is None or representation is None or source is None or profile is None
                or artifact.kind != "realign_paf" or mapping.mode != "signal_to_reference"
                or mapping.state != "ready" or representation.state != "ready" or representation.format != "blow5"
                or source.validation_state != "ready" or mapping.raw_representation_id != representation.id
                or mapping.reference_revision_id != job.reference_revision_id
                or mapping.run_id != job.run_id or mapping.observed_generation != job.observed_generation
                or representation.run_id != job.run_id or representation.observed_generation != job.observed_generation
                or source.raw_representation_id != representation.id or source.molecule_type != profile.molecule_type
                or job.sequence_basis != "managed_reference"
            ):
                raise RuntimeError("comparison real signal/reference authority is not exactly ready")
            try:
                await ont_signal_workbench._require_comparison_mapping_chain(
                    session, viewer=viewer, artifact=artifact, mapping=mapping
                )
            except ont_signal_workbench.OntSignalError as exc:
                raise RuntimeError("comparison viewer mapping chain is not exactly ready") from exc
            mapping_parents = artifact.parent_identities if isinstance(artifact.parent_identities, dict) else {}
            self._require_hash_contract("comparison raw manifest snapshot", mapping_parents.get("raw_manifest_sha256"), representation.manifest_sha256)
            self._require_hash_contract("comparison original move snapshot", mapping_parents.get("move_bam_sha256"), source.artifact_sha256)
            self._require_hash_contract("comparison move inventory snapshot", mapping_parents.get("move_read_inventory_sha256"), source.read_inventory_sha256)
            mapping_path = Path(artifact.managed_relative_path)
            retained_mapping = await self._pin_parent_async(
                parents, mapping_path, alias="mapping.paf.gz",
                expected_sha256=artifact.sha256, expected_size=artifact.size_bytes,
            )
            mapping_index_path = Path(f"{mapping_path}.tbi")
            retained_mapping_index = await self._pin_parent_async(
                parents, mapping_index_path, alias="mapping.paf.gz.tbi",
                expected_sha256=str(artifact.validation_receipt.get("index_sha256") or ""),
                expected_size=int(artifact.validation_receipt.get("index_size_bytes") or 0),
            )
            selected_raw, raw_resolution = await self._resolve_selected_raw_partitions_async(
                representation, [job.selected_read_id]
            )
            raw_identities = await self._pin_raw_partitions_async(parents, representation, selected_raw)
            real_blow5_parents = {
                "routing_sha256": raw_resolution.get("routing_sha256"),
                "blow5": raw_identities,
            }
            source_outputs = source.validation_receipt.get("managed_outputs", {}) if isinstance(source.validation_receipt, dict) else {}
            source_hashes = source.validation_receipt.get("managed_output_sha256s", {}) if isinstance(source.validation_receipt, dict) else {}
            retained_moves = await self._pin_parent_async(
                parents, Path(str(source_outputs.get("filtered_move_bam", ""))), alias="filtered_moves.bam",
                expected_sha256=str(source_hashes.get("filtered_move_bam_sha256") or ""),
                expected_size=int(source_hashes.get("filtered_move_bam_size_bytes") or 0),
            )
            settings = job.simulation_settings if isinstance(job.simulation_settings, dict) else {}
            operator = settings.get("operator_owned", {}) if isinstance(settings.get("operator_owned"), dict) else {}
            simulated_profile = settings.get("profile", {}) if isinstance(settings.get("profile"), dict) else {}
            profile_id, seed = operator.get("profile_id"), operator.get("seed")
            simulated_kmer = simulated_profile.get("kmer_length")
            if (
                not isinstance(profile_id, str) or isinstance(seed, bool) or not isinstance(seed, int)
                or isinstance(simulated_kmer, bool) or not isinstance(simulated_kmer, int)
            ):
                raise RuntimeError("comparison effective simulation settings are malformed")
            base_shift = int(ont_signal_workbench._mapping_profile_base_shift_authority(profile)["effective_value"])
            padding = max(int(profile.kmer_length) - 1 + abs(base_shift), simulated_kmer - 1)
            window_start, window_end = job.reference_start - padding, job.reference_end + padding
            validation_receipt = artifact.validation_receipt if isinstance(artifact.validation_receipt, dict) else {}
            if "read_spans" in validation_receipt:
                spans = validation_receipt.get("read_spans", {})
                span = spans.get(job.selected_read_id) if isinstance(spans, dict) else None
            else:
                span = await asyncio.to_thread(
                    ont_signal_workbench._selected_read_span_from_indexed_artifact,
                    artifact,
                    job.selected_read_id,
                    job.reference_contig,
                    job.reference_start,
                    job.reference_end,
                )
            if (
                window_start < 1 or window_end - window_start + 1 > 2048 or not isinstance(span, dict)
                or span.get("contig") != job.reference_contig
                or int(span.get("start", 0)) > window_start or int(span.get("end", 0)) < window_end
                or job.simulation_orientation not in {"forward", "reverse"}
            ):
                raise RuntimeError("comparison derived reference window authority is invalid")
            async with self._domain_session_factory() as domain_session:
                revision = await domain_session.get(MolBioNGSReferenceRevision, job.reference_revision_id)
                reference_artifact = None if revision is None else await domain_session.get(MolBioNGSReferenceArtifact, revision.artifact_id)
                if revision is None or reference_artifact is None:
                    raise RuntimeError("comparison managed reference authority disappeared")
                reference_path = get_molbio_ngs_reference_root() / reference_artifact.managed_relative_path
                retained_reference = await self._pin_parent_async(
                    parents, reference_path, alias="reference.fasta",
                    expected_sha256=reference_artifact.sha256, expected_size=reference_artifact.size_bytes,
                )
            expected_parents = {
                "reference_fasta_sha256": retained_reference.sha256,
                "mapping_sha256": retained_mapping.sha256,
                "mapping_index_sha256": retained_mapping_index.sha256,
                "real_blow5": real_blow5_parents,
                "real_moves_sha256": retained_moves.sha256,
                "raw_manifest_sha256": representation.manifest_sha256,
                "run_id": job.run_id, "observed_generation": job.observed_generation,
                "selected_read_id": job.selected_read_id,
            }
            producer_args = [
                "produce", "--reference-fasta", "/parents/reference.fasta",
                "--reference-sha256", retained_reference.sha256, "--contig", job.reference_contig,
                "--window-start", str(window_start), "--window-end", str(window_end),
                "--orientation", job.simulation_orientation, "--profile-id", profile_id,
                "--seed", str(seed),
            ]
        producer_parents = parents.subset({"reference.fasta"})
        producer_receipt = await self._invoke(
            producer_parents, producer_args, "squigulator_producer", item_id, token, output
        )
        producer_manifest = self._read_json_report(output / "producer_manifest.json")
        relation = producer_manifest.get("generated_read_id_relation")
        producer_artifacts = producer_manifest.get("artifacts")
        if (
            producer_manifest.get("schema") != "bms.ont-squigulator-producer-manifest.v1"
            or not isinstance(relation, dict) or not isinstance(relation.get("generated_read_id"), str)
            or not isinstance(producer_artifacts, list)
            or producer_manifest.get("parents", {}).get("reference_fasta_sha256") != expected_parents["reference_fasta_sha256"]
        ):
            raise RuntimeError("comparison producer manifest is malformed or unbound")
        generated_read_id = str(relation["generated_read_id"])
        producer_by_kind: dict[str, dict[str, Any]] = {}
        for item in producer_artifacts:
            if not isinstance(item, dict) or item.get("kind") not in set(COMPARISON_PRODUCER_FILENAMES.values()):
                continue
            kind = str(item["kind"]); filename = str(item.get("filename", ""))
            expected_filename = next((name for name, value in COMPARISON_PRODUCER_FILENAMES.items() if value == kind), None)
            if filename != expected_filename:
                raise RuntimeError("comparison producer artifact filename is not canonical")
            path = output / filename
            actual_sha, actual_size = await self._stable_file_identity_async(path)
            self._require_hash_contract("comparison producer artifact", {"sha256": item.get("sha256"), "size_bytes": item.get("size_bytes")}, {"sha256": actual_sha, "size_bytes": actual_size})
            producer_by_kind[kind] = item
        if set(producer_by_kind) != set(COMPARISON_PRODUCER_FILENAMES.values()):
            raise RuntimeError("comparison producer artifact set is incomplete")
        for filename in (
            "simulated.blow5", "simulated.blow5.idx", "simulated_reads.fasta",
            "simulated_normalized.paf", "producer_manifest.json",
        ):
            path = output / filename
            digest, size = await self._stable_file_identity_async(path)
            await self._pin_parent_async(parents, path, alias=filename, expected_sha256=digest, expected_size=size)
        render_args = [
            "render", "--real-blow5", "/parents/raw-0.blow5",
            "--real-mapping", "/parents/mapping.paf.gz", "--real-moves", "/parents/filtered_moves.bam",
            "--reference-fasta", "/parents/reference.fasta", "--simulated-blow5", "/parents/simulated.blow5",
            "--simulated-fasta", "/parents/simulated_reads.fasta", "--simulated-mapping", "/parents/simulated_normalized.paf",
            "--producer-manifest", "/parents/producer_manifest.json",
            "--real-read-id", job.selected_read_id, "--profile-id", profile_id,
            "--contig", job.reference_contig, "--start", str(job.reference_start), "--end", str(job.reference_end),
            "--orientation", job.simulation_orientation, "--molecule-type", source.molecule_type,
            "--real-kmer-length", str(profile.kmer_length),
            "--simulated-kmer-length", str(simulated_kmer),
            "--base-shift", str(base_shift),
            "--render-params-json", json.dumps(job.render_params, sort_keys=True, separators=(",", ":")),
        ]
        renderer_receipt = await self._invoke(
            parents, render_args, "squigualiser_comparison_renderer", item_id, token, output
        )
        manifest_path = output / "comparison_manifest.json"
        manifest = self._read_json_report(manifest_path)
        raw_artifacts = manifest.get("artifacts")
        if manifest.get("schema") != "bms.ont-signal-comparison-manifest.v1" or not isinstance(raw_artifacts, list):
            raise RuntimeError("comparison manifest artifact inventory is malformed")
        stage_receipts = {
            "squigulator_producer": {**producer_receipt, "runtime_identity": self._comparison_runtime_identity("squigulator_producer")},
            "squigualiser_comparison_renderer": {**renderer_receipt, "runtime_identity": self._comparison_runtime_identity("squigualiser_comparison_renderer")},
        }
        manifest = {**manifest, "parents": expected_parents,
                    "runtime_identities": {key: value["runtime_identity"] for key, value in stage_receipts.items()},
                    "stage_receipts": stage_receipts}
        manifest_path.write_bytes(json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n")
        manifest_sha, manifest_size = await self._stable_file_identity_async(manifest_path)
        raw_artifacts = [*raw_artifacts, {"kind": "comparison_manifest", "filename": "comparison_manifest.json",
            "media_type": "application/json", "sha256": manifest_sha, "size_bytes": manifest_size,
            "validation_receipt": {"schema": manifest["schema"]}}]
        if {item.get("kind") for item in raw_artifacts if isinstance(item, dict)} != set(COMPARISON_ARTIFACT_AUTHORITY):
            raise RuntimeError("comparison final artifact set is incomplete or unexpected")
        artifacts: list[OntSignalComparisonArtifact] = []
        governed_root = self._output_root().resolve()
        for item in raw_artifacts:
            if not isinstance(item, dict) or item.get("kind") not in COMPARISON_ARTIFACT_AUTHORITY:
                raise RuntimeError("comparison manifest contains an unsupported artifact")
            kind = str(item["kind"]); relative = Path(str(item.get("filename", "")))
            if relative.is_absolute() or len(relative.parts) != 1:
                raise RuntimeError("comparison artifact path is invalid")
            path = output / relative
            sha256, size = await self._stable_file_identity_async(path)
            if sha256 != item.get("sha256") or size != item.get("size_bytes"):
                raise RuntimeError("comparison artifact diverged from manifest")
            artifacts.append(OntSignalComparisonArtifact(
                id=f"ont-comparison-artifact-{uuid.uuid4().hex}", comparison_job_id=item_id,
                kind=kind, authority_class=COMPARISON_ARTIFACT_AUTHORITY[kind],
                managed_relative_path=path.resolve().relative_to(governed_root).as_posix(),
                media_type=str(item.get("media_type")), sha256=sha256, size_bytes=size,
                parent_identities=expected_parents,
                squigulator_runtime_identity=stage_receipts["squigulator_producer"]["runtime_identity"] if kind != "comparison_html" else None,
                squigualiser_runtime_identity=stage_receipts["squigualiser_comparison_renderer"]["runtime_identity"] if kind in {"comparison_html", "comparison_manifest"} else None,
                validation_receipt=item.get("validation_receipt", {}), created_at=self._now(),
            ))
        async with self._session_factory() as session:
            parents.assert_unbroken()
            current = await session.get(OntSignalComparisonJob, item_id)
            if current is None or current.claim_token != token or current.cancel_requested_at is not None:
                raise asyncio.CancelledError()
            session.add_all(artifacts)
            now = self._now()
            result = await session.execute(update(OntSignalComparisonJob).where(
                OntSignalComparisonJob.id == item_id, OntSignalComparisonJob.claim_token == token,
                OntSignalComparisonJob.state == "running", OntSignalComparisonJob.cancel_requested_at.is_(None),
                OntSignalComparisonJob.lease_expires_at > now,
            ).values(state="ready", reason_code="ideal_comparison_ready", claim_token=None,
                lease_expires_at=None, generated_read_id=generated_read_id,
                resource_snapshot={"parents": expected_parents}, stage_receipts=stage_receipts,
                output_manifest=manifest, updated_at=now, completed_at=now))
            if result.rowcount != 1:
                await session.rollback(); raise TerminalFenceLost("comparison publication fence was lost")
            session.add(OntSignalComparisonEvent(id=f"ont-comparison-event-{uuid.uuid4().hex}",
                comparison_job_id=item_id, state="ready", reason_code="ideal_comparison_ready",
                receipt={"stage_receipts": stage_receipts, "generated_read_id": generated_read_id}, created_at=now))
            parents.assert_unbroken()
            await session.commit()

    async def _cancel_claim(self, table: Any, state_field: str, item_id: str, token: str) -> None:
        async with self._session_factory() as session:
            now = self._now()
            row = await session.get(table, item_id)
            if row is None or row.claim_token != token or not hasattr(table, "cancel_requested_at"):
                return
            values: dict[str, Any] = {state_field: "cancelled", "reason_code": "cancelled", "claim_token": None, "lease_expires_at": None}
            if isinstance(row, OntSignalCalibrationJob):
                values["stage_receipts"] = {**(row.stage_receipts or {}), "cancellation": {**((row.stage_receipts or {}).get("cancellation", {})), "completed_at": now.isoformat(), "disposition": "cancelled"}}
            if hasattr(table, "updated_at"): values["updated_at"] = now
            if hasattr(table, "completed_at"): values["completed_at"] = now
            result = await session.execute(update(table).where(
                table.id == item_id,
                table.claim_token == token,
                getattr(table, state_field) == "running",
                table.cancel_requested_at.is_not(None),
                table.lease_expires_at > now,
            ).values(**values))
            if result.rowcount != 1:
                await session.rollback()
                return
            if table is OntSignalComparisonJob:
                session.add(OntSignalComparisonEvent(
                    id=f"ont-comparison-event-{uuid.uuid4().hex}",
                    comparison_job_id=item_id,
                    state="cancelled",
                    reason_code="cancelled",
                    receipt={"disposition": "cancelled_during_owned_execution"},
                    created_at=now,
                ))
            await session.commit()

    async def _run(self) -> None:
        while not self._stop.is_set():
            work = None
            for table, field, kind, handler in (
                (OntMoveTableSource, "validation_state", "move", self._process_move),
                (OntSignalCalibrationJob, "state", "calibration", self._process_calibration),
                (OntSignalMappingJob, "state", "mapping", self._process_mapping),
                (OntSquigualiserViewJob, "state", "view", self._process_view),
                (OntSignalComparisonJob, "state", "comparison", self._process_comparison),
            ):
                claimed = await self._claim(table, field)
                if claimed is None: continue
                work = True; item_id, token = claimed
                try:
                    await handler(item_id, token)
                except asyncio.CancelledError:
                    if self._stop.is_set(): raise
                    await self._cancel_claim(table, field, item_id, token)
                except ContainerCleanupError as exc:
                    logger.critical("ONT signal worker stopped after unreconciled container cleanup failure", exc_info=True)
                    await self._fail(table, field, item_id, token, exc)
                    self._stop.set()
                    return
                except Exception as exc:
                    logger.exception("ONT signal workbench %s failed: %s", kind, item_id)
                    await self._fail(table, field, item_id, token, exc)
                break
            if work is None:
                try: await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
                except asyncio.TimeoutError: pass
