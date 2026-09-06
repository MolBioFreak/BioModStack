#!/usr/bin/env python3
"""Fail-closed origin/test synchronizer for canonical native Development.

Policy: poll every 60 seconds. Canonical Development is deployment-owned; a
human or agent must use an isolated worktree and push a fast-forward update to
origin/test rather than editing the canonical checkout.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from textwrap import dedent
from typing import Literal

SYNC_SERVICE = "biomodstack-dev-sync.service"
SYNC_TIMER = "biomodstack-dev-sync.timer"
SYNC_INTERVAL_SECONDS = 60
MAX_RUNTIME_SOURCE_PATHS = 512
SYNC_QUEUE_FILENAME = "dev-sync-queue.json"
SYNC_CONTROL_FILENAME = "dev-sync-control.json"
SYNC_REFRESH_FILENAME = "dev-sync-refresh.json"
DEFAULT_CANONICAL_ROOT = Path("/home/dalab/biomodstack/dev-test-canonical")
DEFAULT_STATE_DIR = Path.home() / ".local" / "state" / "biomodstack"
DEFAULT_INSTALLED_SYNC = Path.home() / ".local" / "libexec" / "biomodstack" / "biomodstack_dev_sync.py"
DEPLOYMENT_ADMISSION_LOCK_ENV = "BMS_DEPLOYMENT_ADMISSION_LOCK"
DEPLOYMENT_ADMISSION_LOCK_FILENAME = "deployment-admission.lock"
RUNTIME_DENOMINATOR_PATH = "schemas/ngs_molbio_runtime/runtime-source-denominator-v2.json"
RUNTIME_IMPLEMENTATION_PATH = "platform/api/config/ngs_molbio_runtime/runtime_implementation_v2.json"
SOURCE_PIN_PATH = "platform/api/config/ngs_molbio/source_pin_v1.json"
DENOMINATOR_SCHEMA = "bms.ngs-molbio.runtime-source-denominator.v2"
RUNTIME_SCHEMA = "bms.ngs-molbio.runtime-implementation.v1"
SOURCE_PIN_SCHEMA = "bms.ngs-molbio.source-pin.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
SyncDecision = Literal[
    "blocked-dirty",
    "blocked-diverged",
    "blocked-health-unavailable",
    "deferred-active-work",
    "paused",
    "fast-forward-deploy",
    "deploy-current",
    "idle",
]


class RuntimeAuthorityError(RuntimeError):
    """Candidate authority failed before source mutation."""


class DeploymentRolledBackError(RuntimeError):
    """Candidate deployment failed and the prior revision was restored."""


class DeploymentRollbackFailedError(RuntimeError):
    """Candidate deployment and restoration both failed."""


def plan_sync(
    *,
    dirty: bool,
    local_revision: str,
    remote_revision: str,
    deployed_revision: str | None,
    remote_descends_from_local: bool,
    active_work: bool,
) -> SyncDecision:
    if dirty:
        return "blocked-dirty"
    if not remote_descends_from_local:
        return "blocked-diverged"
    if deployed_revision is None:
        return "blocked-health-unavailable"
    deployment_needed = local_revision != remote_revision or deployed_revision != remote_revision
    if deployment_needed and active_work:
        return "deferred-active-work"
    if local_revision != remote_revision:
        return "fast-forward-deploy"
    if deployed_revision != remote_revision:
        return "deploy-current"
    return "idle"


def render_sync_units(project_root: Path, executable_path: Path | None = None) -> dict[str, str]:
    root = project_root.resolve()
    script = (executable_path or (root / "scripts" / "biomodstack_dev_sync.py")).resolve()
    service = dedent(
        f"""\
        [Unit]
        Description=BioModStack Development origin/test sync transaction
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=oneshot
        Environment=BMS_DEV_CANONICAL_ROOT={root}
        ExecStart=/usr/bin/env python3 {script} --once
        TimeoutStartSec=180
        """
    )
    timer = dedent(
        f"""\
        [Unit]
        Description=BioModStack Development origin/test sync every 60 seconds

        [Timer]
        OnBootSec={SYNC_INTERVAL_SECONDS}s
        OnActiveSec={SYNC_INTERVAL_SECONDS}s
        OnUnitInactiveSec={SYNC_INTERVAL_SECONDS}s
        AccuracySec=1s
        Persistent=true
        Unit={SYNC_SERVICE}

        [Install]
        WantedBy=timers.target
        """
    )
    return {SYNC_SERVICE: service, SYNC_TIMER: timer}


def _run(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # Keep HOME/XDG installation selection while discarding lane-process
    # overrides; the manager must resolve the persisted installation itself.
    env = {key: value for key, value in os.environ.items() if not key.startswith("BMS_")}
    env["HOME"] = str(Path.home())
    env.pop("GIT_INDEX_FILE", None)
    return subprocess.run(
        list(args),
        cwd=root,
        env=env,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = _run(root, "git", *args, check=check)
    return result.stdout.strip()


def _git_blob(root: Path, revision: str, path: str) -> bytes:
    env = os.environ.copy()
    env["HOME"] = str(Path.home())
    env.pop("GIT_INDEX_FILE", None)
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=root,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeAuthorityError(f"candidate source is unavailable: {path}: {detail}")
    return result.stdout


def _authority_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise RuntimeAuthorityError(f"duplicate candidate authority key: {key}")
        value[key] = child
    return value


def _json_blob(root: Path, revision: str, path: str) -> dict[str, object]:
    try:
        payload = json.loads(_git_blob(root, revision, path), object_pairs_hook=_authority_pairs)
    except RuntimeAuthorityError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeAuthorityError(f"candidate authority JSON is invalid: {path}: {exc}") from exc
    if type(payload) is not dict:
        raise RuntimeAuthorityError(f"candidate authority JSON must be an object: {path}")
    return payload


def _validate_canonical_domain(value: object) -> None:
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RuntimeAuthorityError("candidate authority contains an invalid Unicode scalar") from exc
        return
    if type(value) is list:
        for child in value:
            _validate_canonical_domain(child)
        return
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise RuntimeAuthorityError("candidate authority contains a non-string object key")
            _validate_canonical_domain(key)
            _validate_canonical_domain(child)
        return
    raise RuntimeAuthorityError("candidate authority contains a non-canonical JSON value")


def _content_sha256(document: dict[str, object]) -> str:
    value = dict(document)
    value.pop("content_sha256", None)
    _validate_canonical_domain(value)
    raw = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require_content_digest(document: dict[str, object], label: str) -> None:
    digest = document.get("content_sha256")
    if not _is_sha256(digest) or digest != _content_sha256(document):
        raise RuntimeAuthorityError(f"candidate {label} authority has an invalid canonical digest")


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _is_git_object(value: object) -> bool:
    return type(value) is str and _GIT_OBJECT_RE.fullmatch(value) is not None


def _candidate_tree_without_record(root: Path, revision: str) -> str:
    descriptor, index_name = tempfile.mkstemp(prefix="bms-dev-sync-index-")
    os.close(descriptor)
    os.unlink(index_name)
    env = os.environ.copy()
    env["HOME"] = str(Path.home())
    env["GIT_INDEX_FILE"] = index_name
    try:
        for command in (
            ("git", "read-tree", revision),
            ("git", "rm", "--cached", "--quiet", "-f", "--", RUNTIME_IMPLEMENTATION_PATH),
        ):
            result = subprocess.run(
                command,
                cwd=root,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                raise RuntimeAuthorityError(
                    f"candidate source-tree authority failed: {' '.join(command)}: {result.stderr.strip()}"
                )
        result = subprocess.run(
            ("git", "write-tree"),
            cwd=root,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise RuntimeAuthorityError(f"candidate source-tree authority failed: {result.stderr.strip()}")
        return result.stdout.strip()
    finally:
        if os.path.exists(index_name):
            os.unlink(index_name)


def validate_candidate_runtime_authority(root: Path, revision: str) -> dict[str, object]:
    denominator = _json_blob(root, revision, RUNTIME_DENOMINATOR_PATH)
    runtime = _json_blob(root, revision, RUNTIME_IMPLEMENTATION_PATH)
    source_pin = _json_blob(root, revision, SOURCE_PIN_PATH)

    _require_content_digest(denominator, "denominator")
    _require_content_digest(runtime, "runtime")
    _require_content_digest(source_pin, "source-pin")

    paths = denominator.get("paths")
    if (
        set(denominator) != {"schema", "paths", "content_sha256"}
        or denominator.get("schema") != DENOMINATOR_SCHEMA
        or type(paths) is not list
        or not paths
        or len(paths) > MAX_RUNTIME_SOURCE_PATHS
        or any(type(path) is not str or not path or path.startswith("/") for path in paths)
        or any(".." in Path(path).parts for path in paths)
        or len(paths) != len(set(paths))
        or RUNTIME_DENOMINATOR_PATH not in paths
    ):
        raise RuntimeAuthorityError("candidate denominator authority shape is invalid")

    expected_runtime_keys = {
        "adapter_runtime_count", "baseline_source_commit", "baseline_source_tree",
        "binding_runtime_state", "capability_exposure_state", "connector_event_runtime_count",
        "content_sha256", "dataset_exposure_state", "implementation_state",
        "n0_package_fingerprint", "n0_receipt_content_sha256", "payload_scanner_runtime_state",
        "phases", "release_acceptance_state", "schema", "source_authorities",
        "source_denominator", "successor_source_commit", "successor_source_tree",
        "tests_run", "verification_state",
    }
    rows = runtime.get("source_authorities")
    phases = runtime.get("phases")
    source_denominator = runtime.get("source_denominator")
    if (
        set(runtime) != expected_runtime_keys
        or runtime.get("schema") != RUNTIME_SCHEMA
        or runtime.get("implementation_state") != "implemented_unverified"
        or runtime.get("release_acceptance_state") != "open"
        or runtime.get("verification_state") != "source_audit_only"
        or runtime.get("tests_run") != 0
        or runtime.get("capability_exposure_state") != "fail_closed"
        or runtime.get("dataset_exposure_state") != "fail_closed"
        or runtime.get("binding_runtime_state") != "implemented_unverified"
        or runtime.get("payload_scanner_runtime_state") != "implemented_unverified"
        or type(runtime.get("adapter_runtime_count")) is not int
        or type(runtime.get("connector_event_runtime_count")) is not int
        or type(rows) is not list
        or not rows
        or type(phases) is not list
        or len(phases) != 6
        or type(source_denominator) is not dict
        or set(source_denominator) != {"path", "content_sha256"}
        or any(not _is_git_object(runtime.get(field)) for field in (
            "baseline_source_commit", "baseline_source_tree", "successor_source_commit", "successor_source_tree"
        ))
        or any(not _is_sha256(runtime.get(field)) for field in (
            "n0_package_fingerprint", "n0_receipt_content_sha256"
        ))
    ):
        raise RuntimeAuthorityError("candidate runtime authority shape is invalid")
    for number, phase in enumerate(phases, start=1):
        if (
            type(phase) is not dict
            or set(phase) != {"phase_id", "source_state", "acceptance_state", "evidence"}
            or phase.get("phase_id") != f"N{number}"
            or phase.get("source_state") != "implemented"
            or phase.get("acceptance_state") != "unverified"
            or type(phase.get("evidence")) is not str
            or not phase["evidence"]
        ):
            raise RuntimeAuthorityError("candidate runtime phase authority shape is invalid")
    if source_denominator.get("path") != RUNTIME_DENOMINATOR_PATH:
        raise RuntimeAuthorityError("candidate runtime denominator path mismatch")
    if source_denominator.get("content_sha256") != denominator.get("content_sha256"):
        raise RuntimeAuthorityError("candidate runtime denominator content digest mismatch")

    pins = source_pin.get("authorities")
    if (
        set(source_pin) != {"schema", "baseline_commit", "baseline_tree", "authorities", "content_sha256"}
        or source_pin.get("schema") != SOURCE_PIN_SCHEMA
        or not _is_git_object(source_pin.get("baseline_commit"))
        or not _is_git_object(source_pin.get("baseline_tree"))
        or type(pins) is not list
        or not pins
    ):
        raise RuntimeAuthorityError("candidate source-pin authority shape is invalid")

    runtime_by_path: dict[str, dict[str, object]] = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"path", "sha256", "size_bytes"}:
            raise RuntimeAuthorityError("candidate runtime source authority row shape is invalid")
        path = row.get("path")
        digest = row.get("sha256")
        size = row.get("size_bytes")
        if (
            type(path) is not str or not path or path in runtime_by_path
            or not _is_sha256(digest)
            or type(size) is not int or size <= 0
        ):
            raise RuntimeAuthorityError("candidate runtime source authority paths are invalid or duplicated")
        runtime_by_path[path] = row
    if set(runtime_by_path) != set(paths) or len(runtime_by_path) != len(paths):
        raise RuntimeAuthorityError("candidate runtime source set does not match the denominator")

    for path, row in runtime_by_path.items():
        blob = _git_blob(root, revision, path)
        if row.get("size_bytes") != len(blob):
            raise RuntimeAuthorityError(f"runtime size mismatch: {path}")
        if row.get("sha256") != hashlib.sha256(blob).hexdigest():
            raise RuntimeAuthorityError(f"runtime digest mismatch: {path}")

    overlay_count = 0
    pin_paths: set[str] = set()
    for pin in pins:
        if type(pin) is not dict or set(pin) != {"path", "sha256"}:
            raise RuntimeAuthorityError("candidate source-pin authority row is invalid")
        path = pin.get("path")
        expected_sha = pin.get("sha256")
        if (
            type(path) is not str or not path or path in pin_paths
            or not _is_sha256(expected_sha)
        ):
            raise RuntimeAuthorityError("candidate source-pin authority row is invalid")
        pin_paths.add(path)
        actual_sha = hashlib.sha256(_git_blob(root, revision, path)).hexdigest()
        if actual_sha == expected_sha:
            continue
        if path not in runtime_by_path:
            raise RuntimeAuthorityError(f"source pin drift lacks runtime coverage: {path}")
        overlay_count += 1

    expected_tree = runtime.get("successor_source_tree")
    actual_tree = _candidate_tree_without_record(root, revision)
    if expected_tree != actual_tree:
        raise RuntimeAuthorityError(
            f"runtime successor tree mismatch: expected {expected_tree or 'unavailable'}, got {actual_tree}"
        )
    return {
        "candidate_revision": revision,
        "runtime_source_count": len(runtime_by_path),
        "source_pin_overlay_count": overlay_count,
    }


def _deployed_revision(root: Path) -> str | None:
    sys.path.insert(0, str(root))
    import biomodstack_services as services  # pylint: disable=import-outside-toplevel

    url = services.runtime_api_health_url(services.DEV_RUNTIME_MODE, project_root=root)
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.load(response)
    except Exception:
        return None
    build = payload.get("build") if isinstance(payload, dict) else None
    revision = build.get("revision") if isinstance(build, dict) else None
    return revision if _is_git_object(revision) else None


def _development_database(root: Path) -> Path:
    sys.path.insert(0, str(root))
    import biomodstack_services as services  # pylint: disable=import-outside-toplevel

    profile = services.install_profile_snapshot(project_root=root)
    configured = profile.get("dev_db_path") if isinstance(profile, dict) else None
    return Path(str(configured or (Path.home() / ".biomodstack-dev" / "biomodstack.db"))).expanduser().resolve()


def _active_development_work(root: Path) -> tuple[bool, int]:
    database = _development_database(root)
    if not database.is_file():
        raise RuntimeError(f"Development jobs database is unavailable: {database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=3)
    try:
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM jobs
            WHERE lower(status) IN ('queued', 'running', 'cancelling', 'canceling', 'finalizing')
               OR lower(queue_status) IN ('queued', 'running', 'pending_msa', 'cancelling', 'canceling', 'finalizing')
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise RuntimeError(f"Development active-work check failed: {exc}") from exc
    finally:
        connection.close()
    count = int(row[0]) if row is not None else 0
    return count > 0, count


@contextmanager
def _deployment_fence(state_dir: Path):
    configured = os.getenv(DEPLOYMENT_ADMISSION_LOCK_ENV)
    lock_path = Path(configured).expanduser() if configured else state_dir / DEPLOYMENT_ADMISSION_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".dev-sync-", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _write_receipt(state_dir: Path, payload: dict[str, object]) -> None:
    _write_json_file(state_dir / "dev-sync.json", payload)


def _read_queued_revision(state_dir: Path) -> str | None:
    path = state_dir / SYNC_QUEUE_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Development sync queue is unreadable: {path}") from exc
    revision = payload.get("queued_revision") if isinstance(payload, dict) else None
    if not isinstance(revision, str) or len(revision) != 40:
        raise RuntimeError(f"Development sync queue is malformed: {path}")
    return revision


def _write_queued_revision(state_dir: Path, revision: str) -> None:
    _write_json_file(
        state_dir / SYNC_QUEUE_FILENAME,
        {"queued_revision": revision},
    )


def _clear_queued_revision(state_dir: Path) -> None:
    try:
        (state_dir / SYNC_QUEUE_FILENAME).unlink()
    except FileNotFoundError:
        pass


_SYNC_REFRESH_KEYS = {
    "schema",
    "target_revision",
    "sync_sha256",
    "rollback_revision",
    "installed_before_sha256",
    "installed_before_base64",
    "phase",
}
_SYNC_REFRESH_PHASES = {"prepared", "source-live", "rolling-back", "rollback-failed"}


def _write_sync_refresh_marker(state_dir: Path, marker: dict[str, object]) -> None:
    _write_json_file(state_dir / SYNC_REFRESH_FILENAME, marker)


def _new_sync_refresh_marker(
    target_revision: str,
    candidate_raw: bytes,
    rollback_revision: str,
    installed_before: bytes | None,
) -> dict[str, object]:
    return {
        "schema": "biomodstack.dev-sync-refresh.v2",
        "target_revision": target_revision,
        "sync_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "rollback_revision": rollback_revision,
        "installed_before_sha256": (
            hashlib.sha256(installed_before).hexdigest() if installed_before is not None else None
        ),
        "installed_before_base64": (
            base64.b64encode(installed_before).decode("ascii") if installed_before is not None else None
        ),
        "phase": "prepared",
    }


def _read_sync_refresh_required(state_dir: Path) -> dict[str, object] | None:
    path = state_dir / SYNC_REFRESH_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_authority_pairs)
    except (OSError, json.JSONDecodeError, RuntimeAuthorityError) as exc:
        raise RuntimeError(f"Development sync refresh marker is unreadable: {path}") from exc
    if type(payload) is not dict or set(payload) != _SYNC_REFRESH_KEYS:
        raise RuntimeError(f"Development sync refresh marker is malformed: {path}")
    target = payload.get("target_revision")
    rollback = payload.get("rollback_revision")
    candidate_sha = payload.get("sync_sha256")
    installed_sha = payload.get("installed_before_sha256")
    installed_b64 = payload.get("installed_before_base64")
    phase = payload.get("phase")
    if (
        payload.get("schema") != "biomodstack.dev-sync-refresh.v2"
        or not _is_git_object(target)
        or not _is_git_object(rollback)
        or not _is_sha256(candidate_sha)
        or phase not in _SYNC_REFRESH_PHASES
        or not (
            (installed_sha is None and installed_b64 is None)
            or (type(installed_sha) is str and _is_sha256(installed_sha) and type(installed_b64) is str)
        )
    ):
        raise RuntimeError(f"Development sync refresh marker is malformed: {path}")
    if installed_b64 is not None:
        try:
            installed_raw = base64.b64decode(installed_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"Development sync refresh marker is malformed: {path}") from exc
        if hashlib.sha256(installed_raw).hexdigest() != installed_sha:
            raise RuntimeError(f"Development sync refresh marker backup digest mismatch: {path}")
    return payload


def _set_sync_refresh_phase(state_dir: Path, marker: dict[str, object], phase: str) -> dict[str, object]:
    if phase not in _SYNC_REFRESH_PHASES:
        raise RuntimeError(f"invalid Development sync refresh phase: {phase}")
    updated = dict(marker)
    updated["phase"] = phase
    _write_sync_refresh_marker(state_dir, updated)
    return updated


def _clear_sync_refresh_required(state_dir: Path) -> None:
    path = state_dir / SYNC_REFRESH_FILENAME
    try:
        path.unlink()
    except FileNotFoundError:
        return
    _fsync_directory(path.parent)


def _complete_sync_refresh(
    root: Path,
    state_dir: Path,
    revision: str,
    *,
    allow_prepared: bool = False,
) -> bool:
    marker = _read_sync_refresh_required(state_dir)
    if marker is None:
        return False
    target_revision = marker["target_revision"]
    expected_sha256 = marker["sync_sha256"]
    phase = marker["phase"]
    if target_revision != revision:
        raise RuntimeError(
            "Development sync refresh revision mismatch: "
            f"expected {target_revision}, got {revision}"
        )
    if phase == "rollback-failed":
        raise RuntimeError("Development sync refresh is blocked by failed rollback evidence")
    if phase == "prepared":
        if not allow_prepared:
            raise RuntimeError("Development sync refresh source-live proof is missing")
        marker = _set_sync_refresh_phase(state_dir, marker, "source-live")
    source = root / "scripts" / "biomodstack_dev_sync.py"
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeError("Development sync refresh source digest mismatch")
    _atomic_write_bytes(raw, DEFAULT_INSTALLED_SYNC, mode=0o755)
    if (
        DEFAULT_INSTALLED_SYNC.read_bytes() != raw
        or stat.S_IMODE(DEFAULT_INSTALLED_SYNC.stat().st_mode) != 0o755
    ):
        raise RuntimeError("Development stable synchronizer verification failed")
    _clear_sync_refresh_required(state_dir)
    return True


def _resume_sync_rollback(root: Path, state_dir: Path, marker: dict[str, object]) -> None:
    rollback_revision = marker.get("rollback_revision")
    if not isinstance(rollback_revision, str) or not _is_git_object(rollback_revision):
        raise RuntimeError("Development sync rollback revision is invalid")
    marker = _set_sync_refresh_phase(state_dir, marker, "rolling-back")
    manager = root / "scripts" / "manage_desktop_services.py"
    try:
        _git(root, "reset", "--hard", rollback_revision)
        _run(root, sys.executable, str(manager), "restart", "--runtime", "dev")
        rollback_deployed = _deployed_revision(root)
        if rollback_deployed != rollback_revision:
            raise RuntimeError(
                "Development rollback exposure mismatch: "
                f"expected {rollback_revision}, got {rollback_deployed or 'unavailable'}"
            )
        installed_b64 = marker.get("installed_before_base64")
        if isinstance(installed_b64, str):
            installed_before = base64.b64decode(installed_b64, validate=True)
            _atomic_write_bytes(installed_before, DEFAULT_INSTALLED_SYNC, mode=0o755)
        else:
            DEFAULT_INSTALLED_SYNC.unlink(missing_ok=True)
            _fsync_directory(DEFAULT_INSTALLED_SYNC.parent)
        _clear_sync_refresh_required(state_dir)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as rollback_error:
        try:
            _set_sync_refresh_phase(state_dir, marker, "rollback-failed")
        except (OSError, RuntimeError):
            pass
        raise DeploymentRollbackFailedError(
            f"Development rollback to {rollback_revision} failed: {rollback_error}"
        ) from rollback_error


def _read_deployment_paused(state_dir: Path) -> bool:
    path = state_dir / SYNC_CONTROL_FILENAME
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Development sync control is unreadable: {path}") from exc
    paused = payload.get("deployment_paused") if isinstance(payload, dict) else None
    if not isinstance(paused, bool):
        raise RuntimeError(f"Development sync control is malformed: {path}")
    return paused


def set_deployment_paused(state_dir: Path, paused: bool) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "dev-sync.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _write_json_file(
            state_dir / SYNC_CONTROL_FILENAME,
            {"deployment_paused": paused},
        )


def bootstrap_successor_sync(root: Path, state_dir: Path, revision: str) -> dict[str, object]:
    root = root.resolve()
    if not (root / ".git").exists() and not (root / ".git").is_file():
        raise RuntimeError(f"canonical Development is not a Git worktree: {root}")
    if len(revision) != 40:
        raise RuntimeAuthorityError("successor bootstrap revision is invalid")
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "dev-sync.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not _read_deployment_paused(state_dir):
            raise RuntimeError("successor bootstrap requires paused Development deployment")
        if _read_sync_refresh_required(state_dir) is not None:
            raise RuntimeError("successor bootstrap is blocked by pending refresh evidence")
        _git(root, "fetch", "--quiet", "origin", "+refs/heads/test:refs/remotes/origin/test")
        remote = _git(root, "rev-parse", "refs/remotes/origin/test")
        if remote != revision:
            raise RuntimeAuthorityError(
                f"successor bootstrap revision mismatch: expected {revision}, got {remote}"
            )
        authority = validate_candidate_runtime_authority(root, revision)
        expected = _git_blob(root, revision, "scripts/biomodstack_dev_sync.py")
        executor = Path(__file__).resolve().read_bytes()
        if executor != expected:
            raise RuntimeAuthorityError("successor bootstrap executor does not match candidate Git bytes")
        with _deployment_fence(state_dir):
            _atomic_write_bytes(executor, DEFAULT_INSTALLED_SYNC, mode=0o755)
            if (
                DEFAULT_INSTALLED_SYNC.read_bytes() != executor
                or stat.S_IMODE(DEFAULT_INSTALLED_SYNC.stat().st_mode) != 0o755
            ):
                raise RuntimeError("successor bootstrap stable synchronizer verification failed")
        _write_queued_revision(state_dir, revision)
        _write_receipt(
            state_dir,
            {
                "decision": "bootstrap-successor-sync",
                "status": "installed",
                "queue_state": "pending",
                "queued_revision": revision,
                "runtime_authority": authority,
            },
        )
        return authority


def _deployment_failure_state(error: BaseException) -> str:
    if isinstance(error, RuntimeAuthorityError):
        return "blocked-runtime-authority"
    if isinstance(error, DeploymentRolledBackError):
        return "blocked-deployment-rolled-back"
    if isinstance(error, DeploymentRollbackFailedError):
        return "blocked-deployment-rollback-failed"
    return "blocked-deployment-unknown"


def _deploy_candidate(
    root: Path,
    state_dir: Path,
    decision: SyncDecision,
    local_revision: str,
    remote_revision: str,
    deployed_revision: str | None,
) -> dict[str, object]:
    authority = validate_candidate_runtime_authority(root, remote_revision)
    candidate_sync = _git_blob(root, remote_revision, "scripts/biomodstack_dev_sync.py")
    rollback_revision = (
        deployed_revision
        if isinstance(deployed_revision, str) and _is_git_object(deployed_revision)
        else local_revision
    )
    marker = _read_sync_refresh_required(state_dir)
    if marker is None:
        installed_before = DEFAULT_INSTALLED_SYNC.read_bytes() if DEFAULT_INSTALLED_SYNC.is_file() else None
        marker = _new_sync_refresh_marker(
            remote_revision,
            candidate_sync,
            rollback_revision,
            installed_before,
        )
        _write_sync_refresh_marker(state_dir, marker)
    else:
        phase = marker.get("phase")
        if phase == "rollback-failed":
            raise RuntimeError("Development sync refresh is blocked by failed rollback evidence")
        if phase in {"source-live", "rolling-back"}:
            _resume_sync_rollback(root, state_dir, marker)
            raise DeploymentRolledBackError(
                f"Development resumed rollback to {marker.get('rollback_revision')}"
            )
        expected = (
            marker.get("target_revision") == remote_revision
            and marker.get("sync_sha256") == hashlib.sha256(candidate_sync).hexdigest()
            and marker.get("rollback_revision") == rollback_revision
            and marker.get("phase") == "prepared"
        )
        if not expected:
            if phase == "prepared" and marker.get("target_revision") == remote_revision:
                _resume_sync_rollback(root, state_dir, marker)
                raise DeploymentRolledBackError(
                    f"Development resumed rollback to {marker.get('rollback_revision')}"
                )
            raise RuntimeError("Development sync refresh marker conflicts with the pending deployment")
        installed_sha = marker.get("installed_before_sha256")
        installed_now = DEFAULT_INSTALLED_SYNC.read_bytes() if DEFAULT_INSTALLED_SYNC.is_file() else None
        actual_sha = hashlib.sha256(installed_now).hexdigest() if installed_now is not None else None
        if actual_sha != installed_sha:
            raise RuntimeError("Development sync refresh installed baseline mismatch")

    manager = root / "scripts" / "manage_desktop_services.py"
    try:
        if decision == "fast-forward-deploy":
            _git(root, "merge", "--ff-only", "refs/remotes/origin/test")
        _run(root, sys.executable, str(manager), "restart", "--runtime", "dev")
        deployed_after = _deployed_revision(root)
        if deployed_after != remote_revision:
            raise RuntimeError(
                "Development exposure mismatch after deployment: "
                f"expected {remote_revision}, got {deployed_after or 'unavailable'}"
            )
        marker = _set_sync_refresh_phase(state_dir, marker, "source-live")
        _complete_sync_refresh(root, state_dir, remote_revision)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as deployment_error:
        try:
            _resume_sync_rollback(root, state_dir, marker)
        except DeploymentRollbackFailedError as rollback_error:
            raise DeploymentRollbackFailedError(
                f"Development deployment failed for {remote_revision}; rollback to "
                f"{rollback_revision} also failed: {rollback_error}"
            ) from deployment_error
        raise DeploymentRolledBackError(
            f"Development deployment failed for {remote_revision}; rolled back to {rollback_revision}"
        ) from deployment_error

    return {
        "runtime_authority": authority,
        "deployed_revision_after": deployed_after,
    }


def _sync_once_transaction(root: Path, state_dir: Path) -> SyncDecision:
    root = root.resolve()
    if not (root / ".git").exists() and not (root / ".git").is_file():
        raise RuntimeError(f"canonical Development is not a Git worktree: {root}")

    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "dev-sync.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        dirty = bool(_git(root, "status", "--porcelain"))
        local = _git(root, "rev-parse", "HEAD")
        _git(root, "fetch", "--quiet", "origin", "+refs/heads/test:refs/remotes/origin/test")
        remote = _git(root, "rev-parse", "refs/remotes/origin/test")
        queued_revision = _read_queued_revision(state_dir)
        deployment_paused = _read_deployment_paused(state_dir)
        refresh_marker = _read_sync_refresh_required(state_dir)
        ancestry = _run(
            root,
            "git",
            "merge-base",
            "--is-ancestor",
            local,
            remote,
            check=False,
        ).returncode == 0
        deployed = _deployed_revision(root)
        marker_phase = refresh_marker.get("phase") if refresh_marker is not None else None
        marker_target = refresh_marker.get("target_revision") if refresh_marker is not None else None
        recovery_required = (
            refresh_marker is not None
            and marker_phase in {"source-live", "rolling-back"}
            and (
                marker_phase == "rolling-back"
                or local != marker_target
                or deployed != marker_target
            )
        )
        if recovery_required and refresh_marker is not None:
            try:
                with _deployment_fence(state_dir):
                    fresh_marker = _read_sync_refresh_required(state_dir)
                    if fresh_marker is None:
                        raise RuntimeError("Development rollback marker disappeared during recovery")
                    _resume_sync_rollback(root, state_dir, fresh_marker)
            except DeploymentRollbackFailedError as exc:
                _write_receipt(
                    state_dir,
                    {
                        "decision": "blocked-deployment-rollback-failed",
                        "deployment_error": str(exc),
                        "local_revision": local,
                        "remote_revision": remote,
                        "deployed_revision_before": deployed,
                        "queue_state": "pending",
                        "queued_revision": marker_target,
                    },
                )
                raise
            exc = DeploymentRolledBackError(
                f"Development resumed rollback to {refresh_marker.get('rollback_revision')}"
            )
            _write_receipt(
                state_dir,
                {
                    "decision": "blocked-deployment-rolled-back",
                    "deployment_error": str(exc),
                    "local_revision": local,
                    "remote_revision": remote,
                    "deployed_revision_before": deployed,
                    "queue_state": "pending",
                    "queued_revision": marker_target,
                },
            )
            raise exc
        active_work, active_work_count = _active_development_work(root)
        decision = plan_sync(
            dirty=dirty,
            local_revision=local,
            remote_revision=remote,
            deployed_revision=deployed,
            remote_descends_from_local=ancestry,
            active_work=active_work,
        )
        refresh_revision = (
            refresh_marker.get("target_revision") if refresh_marker is not None else None
        )
        pending_revision = (
            remote
            if local != remote or deployed != remote
            else refresh_revision if isinstance(refresh_revision, str) else None
        )
        if pending_revision is None:
            _clear_queued_revision(state_dir)
        elif queued_revision != pending_revision:
            _write_queued_revision(state_dir, pending_revision)
        queued_revision = pending_revision
        receipt: dict[str, object] = {
            "decision": decision,
            "local_revision": local,
            "remote_revision": remote,
            "deployed_revision_before": deployed,
            "active_work_count": active_work_count,
            "deployment_paused": deployment_paused,
            "poll_interval_seconds": SYNC_INTERVAL_SECONDS,
            "queue_state": "pending" if queued_revision is not None else "empty",
            "queued_revision": queued_revision,
        }
        if decision.startswith("blocked-"):
            _write_receipt(state_dir, receipt)
            raise RuntimeError(f"Development sync {decision}: canonical={local} origin/test={remote}")
        if deployment_paused:
            receipt["decision"] = "paused"
            _write_receipt(state_dir, receipt)
            return "paused"
        if decision == "deferred-active-work":
            _write_receipt(state_dir, receipt)
            return decision

        if decision == "idle":
            sync_refresh_reconciled = False
            if _read_sync_refresh_required(state_dir) is not None:
                with _deployment_fence(state_dir):
                    fresh_local = _git(root, "rev-parse", "HEAD")
                    _git(root, "fetch", "--quiet", "origin", "+refs/heads/test:refs/remotes/origin/test")
                    fresh_remote = _git(root, "rev-parse", "refs/remotes/origin/test")
                    fresh_deployed = _deployed_revision(root)
                    if (fresh_local, fresh_remote, fresh_deployed) != (remote, remote, remote):
                        raise RuntimeError("Development identity changed before refresh recovery")
                    sync_refresh_reconciled = _complete_sync_refresh(
                        root,
                        state_dir,
                        remote,
                        allow_prepared=True,
                    )
            _clear_queued_revision(state_dir)
            receipt["queue_state"] = "empty"
            receipt["queued_revision"] = None
            receipt["sync_refresh_reconciled"] = sync_refresh_reconciled
            _write_receipt(state_dir, receipt)
            return decision

        with _deployment_fence(state_dir):
            active_work, active_work_count = _active_development_work(root)
            if active_work:
                decision = "deferred-active-work"
                receipt["decision"] = decision
                receipt["active_work_count"] = active_work_count
                _write_receipt(state_dir, receipt)
                return decision
            try:
                deployment = _deploy_candidate(root, state_dir, decision, local, remote, deployed)
            except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
                receipt["decision"] = _deployment_failure_state(exc)
                receipt["deployment_error"] = str(exc)
                receipt["local_revision_after"] = _git(root, "rev-parse", "HEAD")
                receipt["deployed_revision_after"] = _deployed_revision(root)
                _write_receipt(state_dir, receipt)
                raise
            receipt.update(deployment)
            receipt["status"] = "deployed"
            receipt["queue_state"] = "empty"
            receipt["queued_revision"] = None
            _clear_queued_revision(state_dir)
            _write_receipt(state_dir, receipt)
            return decision


def _specific_failure_receipt_matches(state_dir: Path, error: BaseException) -> bool:
    path = state_dir / "dev-sync.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("decision")
        in {
            "blocked-runtime-authority",
            "blocked-deployment-rolled-back",
            "blocked-deployment-rollback-failed",
        }
        and payload.get("deployment_error") == str(error)
    )


def sync_once(root: Path, state_dir: Path) -> SyncDecision:
    try:
        return _sync_once_transaction(root, state_dir)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        state_dir.mkdir(parents=True, exist_ok=True)
        if _specific_failure_receipt_matches(state_dir, exc):
            raise
        _write_receipt(
            state_dir,
            {
                "decision": "blocked-sync-transaction",
                "status": "failed",
                "sync_error": str(exc),
                "error_type": type(exc).__name__,
                "queue_state": "unknown",
            },
        )
        raise


def _atomic_write_bytes(raw: bytes, target: Path, *, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(raw)
        handle.flush()
        os.fchmod(handle.fileno(), mode)
        os.fsync(handle.fileno())
    os.replace(temp_path, target)
    _fsync_directory(target.parent)


def _atomic_copy(source: Path, target: Path) -> None:
    _atomic_write_bytes(source.read_bytes(), target, mode=0o755)


def install_sync_units(
    root: Path,
    systemd_dir: Path,
    *,
    state_dir: Path = DEFAULT_STATE_DIR,
    libexec_dir: Path | None = None,
) -> list[Path]:
    installed_script = (libexec_dir / "biomodstack_dev_sync.py") if libexec_dir else DEFAULT_INSTALLED_SYNC
    _atomic_copy(root / "scripts" / "biomodstack_dev_sync.py", installed_script)
    systemd_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in render_sync_units(root, installed_script).items():
        path = systemd_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    _run(root, "systemctl", "--user", "daemon-reload")
    _run(root, "systemctl", "--user", "enable", "--now", SYNC_TIMER)
    return written


def install_units(root: Path, systemd_dir: Path) -> list[Path]:
    """Compatibility wrapper for older callers."""
    return install_sync_units(root, systemd_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync canonical BioModStack Development from origin/test every 60 seconds")
    parser.add_argument("--once", action="store_true", help="run one synchronization transaction")
    parser.add_argument("--install", action="store_true", help="install and enable the 60-second user timer")
    parser.add_argument(
        "--bootstrap-successor",
        metavar="REVISION",
        help="self-attest and install this exact successor synchronizer while deployment is paused",
    )
    control_group = parser.add_mutually_exclusive_group()
    control_group.add_argument("--pause-deploy", action="store_true", help="pause automatic Development deployment")
    control_group.add_argument("--resume-deploy", action="store_true", help="resume automatic Development deployment and poll now")
    parser.add_argument("--root", type=Path, default=Path(os.getenv("BMS_DEV_CANONICAL_ROOT", DEFAULT_CANONICAL_ROOT)))
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--systemd-dir", type=Path, default=Path.home() / ".config" / "systemd" / "user")
    args = parser.parse_args()
    try:
        if args.bootstrap_successor:
            authority = bootstrap_successor_sync(args.root, args.state_dir, args.bootstrap_successor)
            print(json.dumps(authority, sort_keys=True))
            return 0
        if args.install:
            install_sync_units(args.root, args.systemd_dir, state_dir=args.state_dir)
            print(f"Installed {SYNC_TIMER}: origin/test is checked every {SYNC_INTERVAL_SECONDS} seconds")
            return 0
        if args.pause_deploy:
            set_deployment_paused(args.state_dir, True)
            print("Paused automatic Development deployment; polling continues")
            return 0
        if args.resume_deploy:
            set_deployment_paused(args.state_dir, False)
            _run(args.root, "systemctl", "--user", "start", SYNC_SERVICE)
            print("Resumed automatic Development deployment and started one poll")
            return 0
        if args.once:
            decision = sync_once(args.root, args.state_dir)
            print(json.dumps({"decision": decision, "poll_interval_seconds": SYNC_INTERVAL_SECONDS}, sort_keys=True))
            return 0
        parser.error(
            "one of --once, --install, --bootstrap-successor, --pause-deploy, or --resume-deploy is required"
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
