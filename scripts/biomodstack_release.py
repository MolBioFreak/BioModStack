#!/usr/bin/env python3
"""Transactional BioModStack container release with automatic rollback.

This is the single production deployment path.  Merely importing it, running
``plan``, or running its unit tests has no runtime side effects.  ``deploy``
requires the exact ``--confirm-runtime-activation`` gate.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any, Mapping, Protocol


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import biomodstack_services as services


FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SYSTEMD_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@+,-]+$")
BUILD_SERVICES = (
    "bms-api",
    "bms-host-agent",
    "bms-cpu-power",
    "bms-web",
)
IMAGE_REFS = {
    "bms-api": "BMS_API_IMAGE",
    "bms-host-agent": "BMS_HOST_AGENT_IMAGE",
    "bms-cpu-power": "BMS_CPU_POWER_IMAGE",
    "bms-web": "BMS_WEB_IMAGE",
}
IMAGE_DEFAULTS = {
    "bms-api": "biomodstack/api:local",
    "bms-host-agent": "biomodstack/host-agent:local",
    "bms-cpu-power": "biomodstack/cpu-power:local",
    "bms-web": "biomodstack/web:local",
}
CONTAINER_NAMES = {
    "bms-api": "biomodstack-api",
    "bms-host-agent": "biomodstack-host-agent",
    "bms-cpu-power": "biomodstack-cpu-power",
    "bms-web": "biomodstack-web",
}
IMMUTABLE_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
COMPOSE_PROJECT_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MANAGED_UNIT_NAMES = (
    services.CORE_RUNTIME_SERVICE,
)


def _read_runtime_env(path: Path) -> dict[str, str]:
    """Read literal KEY=VALUE assignments with the runtime launcher's semantics."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value
    return values


class ReleaseValidationError(RuntimeError):
    """The newly restarted runtime did not satisfy release acceptance."""


class ReleaseRollbackError(RuntimeError):
    """Known-good restoration or its validation failed."""


@dataclass(frozen=True)
class BuildIdentity:
    revision: str
    build_id: str
    build_time: str

    def __post_init__(self) -> None:
        if not FULL_GIT_SHA.fullmatch(self.revision):
            raise ValueError("release revision must be a full 40-character lowercase git SHA")
        if not self.build_id.strip() or not self.build_time.strip():
            raise ValueError("release build id and build time must be non-empty")
        if not SYSTEMD_SAFE_VALUE.fullmatch(self.build_id) or not SYSTEMD_SAFE_VALUE.fullmatch(
            self.build_time
        ):
            raise ValueError("release build id and build time must be safe systemd values")

    def as_environment(self) -> dict[str, str]:
        return {
            "BMS_BUILD_SHA": self.revision,
            "BMS_BUILD_ID": self.build_id,
            "BMS_BUILD_TIME": self.build_time,
        }


class ReleaseBackend(Protocol):
    def snapshot_known_good(self) -> Mapping[str, Any]: ...
    def build_images(self, identity: BuildIdentity) -> None: ...
    def verify_generated_ownership(self) -> None: ...
    def verify_image_provenance(self, identity: BuildIdentity) -> None: ...
    def stop_installed_owner(self) -> None: ...
    def install_units(self, identity: BuildIdentity) -> None: ...
    def start_candidate(self) -> None: ...
    def stop_candidate(self) -> None: ...
    def validate_candidate_release(self, identity: BuildIdentity) -> None: ...
    def restore_known_good(self, snapshot: Mapping[str, Any]) -> None: ...
    def restart_known_good(self, snapshot: Mapping[str, Any]) -> None: ...
    def revalidate_known_good(self, snapshot: Mapping[str, Any]) -> None: ...
    def commit_known_good(self, snapshot: Mapping[str, Any], identity: BuildIdentity) -> None: ...


def release_plan() -> tuple[str, ...]:
    return (
        "snapshot-known-good",
        "build-images-explicitly",
        "verify-generated-ownership",
        "verify-image-provenance",
        "stop-installed-owner-before-unit-replacement",
        "install-candidate-units",
        "start-candidate-runtime",
        "validate-all-runtime-surfaces-and-ownership",
        "commit-known-good",
    )


def _snapshot_has_restorable_runtime(snapshot: Mapping[str, Any]) -> bool:
    images = snapshot.get("images")
    return isinstance(images, Mapping) and all(bool(images.get(service)) for service in BUILD_SERVICES)


def execute_release(backend: ReleaseBackend, identity: BuildIdentity) -> None:
    snapshot = backend.snapshot_known_good()
    candidate_touched = False
    try:
        backend.build_images(identity)
        backend.verify_generated_ownership()
        backend.verify_image_provenance(identity)
        backend.stop_installed_owner()
        candidate_touched = True
        backend.install_units(identity)
        backend.start_candidate()
        backend.validate_candidate_release(identity)
    except BaseException as release_error:
        try:
            if candidate_touched:
                backend.stop_candidate()
            backend.restore_known_good(snapshot)
            if not _snapshot_has_restorable_runtime(snapshot):
                raise release_error
            backend.restart_known_good(snapshot)
            backend.revalidate_known_good(snapshot)
        except BaseException as rollback_error:
            if rollback_error is release_error:
                raise
            raise ReleaseRollbackError(
                f"release failed ({release_error}); rollback also failed ({rollback_error})"
            ) from rollback_error
        raise
    backend.commit_known_good(snapshot, identity)


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class ProductionReleaseBackend:
    def __init__(
        self,
        *,
        repo_root: Path = REPO_ROOT,
        state_dir: Path | None = None,
        api_url: str | None = None,
        browser_url: str | None = None,
        operator_url: str | None = None,
        validation_attempts: int = 10,
        validation_retry_delay: float = 0.5,
        allow_first_install: bool = False,
    ) -> None:
        self.repo_root = repo_root.resolve()
        if not SYSTEMD_SAFE_VALUE.fullmatch(str(self.repo_root)):
            raise ReleaseValidationError("release root must be an absolute systemd-safe path")
        self.compose_file = self.repo_root / "compose.core-runtime.yml"
        self.state_dir = (
            state_dir
            or Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
            / "biomodstack"
            / "releases"
        ).resolve()
        self.api_url = api_url or os.environ.get(
            "BMS_RELEASE_API_URL", "http://127.0.0.1:8000/api/health"
        )
        self.browser_url = browser_url or os.environ.get(
            "BMS_RELEASE_BROWSER_URL", "http://127.0.0.1:18080/bms/"
        )
        self.operator_url = operator_url or os.environ.get(
            "BMS_RELEASE_OPERATOR_URL", "http://127.0.0.1:5173/"
        )
        operator_origin = self.operator_url.rstrip("/")
        self.operator_api_url = f"{operator_origin}/api/health"
        self.operator_identity_url = f"{operator_origin}/src/lib/buildIdentity.ts"
        if validation_attempts < 1 or validation_retry_delay < 0:
            raise ValueError("release validation retry bounds are invalid")
        self.validation_attempts = validation_attempts
        self.validation_retry_delay = validation_retry_delay
        self.allow_first_install = allow_first_install
        self.runtime_env_file = Path(
            os.environ.get(
                "BMS_CORE_RUNTIME_ENV_FILE",
                Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
                / "biomodstack"
                / "core-runtime.env",
            )
        ).expanduser().resolve()
        runtime_env = _read_runtime_env(self.runtime_env_file)
        self.image_refs = {
            service: runtime_env.get(
                IMAGE_REFS[service],
                os.environ.get(IMAGE_REFS[service], IMAGE_DEFAULTS[service]),
            )
            for service in BUILD_SERVICES
        }
        self.compose_project = runtime_env.get(
            "COMPOSE_PROJECT_NAME",
            os.environ.get("COMPOSE_PROJECT_NAME", "biomodstack-core-runtime"),
        )
        if not COMPOSE_PROJECT_NAME.fullmatch(self.compose_project):
            raise ReleaseValidationError("Compose project name is missing or unsafe")
        self.candidate_image_ids: dict[str, str] = {}
        self.identity: BuildIdentity | None = None

    def _run(
        self,
        command: list[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            command,
            cwd=self.repo_root,
            env=merged_env,
            check=check,
            text=True,
            capture_output=capture_output,
        )

    def _compose(
        self,
        *arguments: str,
        env: Mapping[str, str] | None = None,
        capture_output: bool = True,
    ) -> None:
        self._run(
            ["docker", "compose", "-f", str(self.compose_file), *arguments],
            env=env,
            capture_output=capture_output,
        )

    def _image_id(self, image_ref: str) -> str | None:
        completed = self._run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image_ref],
            check=False,
        )
        value = completed.stdout.strip()
        return (
            value
            if completed.returncode == 0 and IMMUTABLE_IMAGE_ID.fullmatch(value)
            else None
        )

    def _unit_snapshot(self) -> dict[str, dict[str, Any]]:
        systemd_dir = services.get_user_systemd_dir()
        snapshot: dict[str, dict[str, Any]] = {}
        for unit_name in MANAGED_UNIT_NAMES:
            path = systemd_dir / unit_name
            dropin_dir = systemd_dir / f"{unit_name}.d"
            if path.is_symlink() or (dropin_dir.exists() and dropin_dir.is_symlink()):
                raise RuntimeError(f"unsafe systemd unit path for {unit_name}")
            drop_ins = (
                {
                    dropin.name: base64.b64encode(dropin.read_bytes()).decode("ascii")
                    for dropin in sorted(dropin_dir.iterdir())
                    if dropin.is_file()
                    and not dropin.is_symlink()
                    and dropin.name.endswith(".conf")
                }
                if dropin_dir.is_dir()
                else {}
            )
            if dropin_dir.is_dir():
                expected = {
                    dropin.name
                    for dropin in dropin_dir.iterdir()
                    if dropin.is_file() and not dropin.is_symlink() and dropin.name.endswith(".conf")
                }
                actual = {dropin.name for dropin in dropin_dir.iterdir()}
                if actual != expected:
                    raise RuntimeError(f"unsafe systemd drop-in entry for {unit_name}")
            active = self._run(
                ["systemctl", "--user", "is-active", unit_name],
                check=False,
            )
            enabled = self._run(
                ["systemctl", "--user", "is-enabled", unit_name],
                check=False,
            )
            snapshot[unit_name] = {
                "base": (
                    base64.b64encode(path.read_bytes()).decode("ascii")
                    if path.is_file()
                    else None
                ),
                "drop_ins": drop_ins,
                "active": active.returncode == 0 and active.stdout.strip() == "active",
                "enabled": enabled.returncode == 0 and enabled.stdout.strip() == "enabled",
            }
        return snapshot

    def _running_container_snapshot(self, service: str) -> dict[str, str] | None:
        container_name = CONTAINER_NAMES[service]
        completed = self._run(
            ["docker", "inspect", container_name],
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        try:
            raw = json.loads(completed.stdout)
            if not isinstance(raw, list) or len(raw) != 1:
                raise TypeError("Docker inspect must return exactly one record")
            record = raw[0]
            labels = record["Config"]["Labels"]
            image_id = record["Image"]
            running = record["State"]["Running"] is True
        except (KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid Docker inspection for managed service {service}") from exc
        config_files = {
            item.strip()
            for item in str(labels.get("com.docker.compose.project.config_files", "")).split(",")
            if item.strip()
        }
        working_dir_value = labels.get("com.docker.compose.project.working_dir")
        if not isinstance(working_dir_value, str):
            raise RuntimeError(f"container for {service} has no managed working directory")
        working_dir = Path(working_dir_value)
        if not working_dir.is_absolute() or working_dir.resolve() != working_dir:
            raise RuntimeError(f"container for {service} has an unsafe managed working directory")
        expected_compose_file = str(working_dir / "compose.core-runtime.yml")
        project = labels.get("com.docker.compose.project")
        if not (
            running
            and labels.get("com.docker.compose.service") == service
            and isinstance(project, str)
            and COMPOSE_PROJECT_NAME.fullmatch(project)
            and config_files == {expected_compose_file}
            and isinstance(image_id, str)
            and IMMUTABLE_IMAGE_ID.fullmatch(image_id)
        ):
            raise RuntimeError(f"container for {service} is not the expected running managed service")
        return {
            "id": str(record.get("Id") or container_name),
            "service": service,
            "project": project,
            "config_file": expected_compose_file,
            "image_id": image_id,
            "root": str(working_dir),
        }

    def snapshot_known_good(self) -> Mapping[str, Any]:
        units = self._unit_snapshot()
        validation = self._snapshot_known_good_validation(units)
        containers = {
            service: running
            for service in BUILD_SERVICES
            if (running := self._running_container_snapshot(service)) is not None
        }
        core_record = units.get(services.CORE_RUNTIME_SERVICE)
        managed_runtime_active = (
            isinstance(core_record, Mapping) and core_record.get("active") is True
        )
        if (managed_runtime_active or containers) and set(containers) != set(BUILD_SERVICES):
            missing_containers = sorted(set(BUILD_SERVICES) - set(containers))
            raise RuntimeError(
                "incomplete running managed-container discovery: " + ", ".join(missing_containers)
            )
        if containers:
            self._validate_snapshot_container_listener_ownership(containers)
        images = {
            service: containers[service]["image_id"]
            if service in containers
            else self._image_id(ref)
            for service, ref in self.image_refs.items()
        }
        missing = [service for service, image_id in images.items() if image_id is None]
        if missing and not self.allow_first_install:
            raise RuntimeError(
                "no restorable known-good image for " + ", ".join(missing) +
                "; use --allow-first-install only for a new installation"
            )
        snapshot: dict[str, Any] = {
            "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "images": images,
            "image_refs": self.image_refs,
            "containers": containers,
            "units": units,
        }
        if _snapshot_has_restorable_runtime(snapshot):
            snapshot["validation"] = validation
        _atomic_json_write(self.state_dir / "pre-deploy.json", snapshot)
        return snapshot

    def build_images(self, identity: BuildIdentity) -> None:
        self.identity = identity
        _validated_source_revision(self.repo_root, identity.revision)
        with tempfile.TemporaryDirectory(prefix="biomodstack-release-source-") as temporary:
            materialized_root = Path(temporary)
            _materialize_git_revision(self.repo_root, identity.revision, materialized_root)
            self._build_materialized_images(
                materialized_root,
                identity,
                image_refs=self.image_refs,
            )

    @staticmethod
    def _build_materialized_images(
        materialized_root: Path,
        identity: BuildIdentity,
        *,
        image_refs: Mapping[str, str],
    ) -> None:
        merged_env = os.environ.copy()
        merged_env.update(identity.as_environment())
        merged_env.update(
            {
                IMAGE_REFS[service]: image_ref
                for service, image_ref in image_refs.items()
            }
        )
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(materialized_root / "compose.core-runtime.yml"),
                "build",
                "--pull",
                "--no-cache",
                *BUILD_SERVICES,
            ],
            cwd=materialized_root,
            env=merged_env,
            check=True,
        )

    def verify_generated_ownership(self) -> None:
        self._run(
            [
                sys.executable,
                str(self.repo_root / "scripts" / "normalize_generated_ownership.py"),
                "--check",
            ]
        )

    def verify_image_provenance(self, identity: BuildIdentity) -> None:
        candidate_image_ids: dict[str, str] = {}
        for service, image_ref in self.image_refs.items():
            completed = self._run(
                ["docker", "image", "inspect", image_ref]
            )
            try:
                payload = json.loads(completed.stdout)
                if not isinstance(payload, list) or len(payload) != 1:
                    raise TypeError("image inspect must return exactly one record")
                record = payload[0]
                image_id = record["Id"]
                labels = record["Config"]["Labels"]
            except (KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
                raise ReleaseValidationError(
                    f"{service} image inspection is invalid"
                ) from exc
            if not isinstance(image_id, str) or not IMMUTABLE_IMAGE_ID.fullmatch(image_id):
                raise ReleaseValidationError(f"{service} image identity is not immutable")
            if not isinstance(labels, Mapping):
                raise ReleaseValidationError(f"{service} image labels are invalid")
            if labels.get("org.opencontainers.image.revision") != identity.revision:
                raise ReleaseValidationError(
                    f"{service} image revision does not match {identity.revision}"
                )
            if labels.get("org.opencontainers.image.version") != identity.build_id:
                raise ReleaseValidationError(
                    f"{service} image build id does not match {identity.build_id}"
                )
            if labels.get("org.opencontainers.image.created") != identity.build_time:
                raise ReleaseValidationError(
                    f"{service} image build time does not match {identity.build_time}"
                )
            candidate_image_ids[service] = image_id
        self.candidate_image_ids = candidate_image_ids

    def _candidate_running_image_ids(self) -> dict[str, str]:
        if set(self.candidate_image_ids) != set(BUILD_SERVICES):
            raise ReleaseValidationError("candidate immutable image preflight is incomplete")
        running_image_ids: dict[str, str] = {}
        for service in BUILD_SERVICES:
            record = self._running_container_snapshot(service)
            if record is None:
                raise ReleaseValidationError(f"candidate running image is missing for {service}")
            if (
                record["root"] != str(self.repo_root)
                or record["project"] != self.compose_project
                or record["config_file"] != str(self.compose_file)
                or record["image_id"] != self.candidate_image_ids[service]
            ):
                raise ReleaseValidationError(
                    f"candidate running image/topology differs for {service}"
                )
            running_image_ids[service] = record["image_id"]
        return running_image_ids

    def _stop_managed_units(self) -> None:
        stopped = self._run(
            ["systemctl", "--user", "stop", *MANAGED_UNIT_NAMES],
            check=False,
        )
        observed: dict[str, str] = {}
        for unit_name in MANAGED_UNIT_NAMES:
            state = self._run(
                ["systemctl", "--user", "is-active", unit_name],
                check=False,
            )
            observed[unit_name] = state.stdout.strip()
        if stopped.returncode != 0:
            detail = stopped.stderr.strip() or stopped.stdout.strip() or "unknown systemctl error"
            raise ReleaseValidationError(f"failed to stop managed units: {detail}")
        not_stopped = [
            f"{unit_name}={state or 'unknown'}"
            for unit_name, state in observed.items()
            if state not in {"inactive", "failed"}
        ]
        if not_stopped:
            raise ReleaseValidationError(
                "managed units remained active or indeterminate: " + ", ".join(not_stopped)
            )

    def stop_installed_owner(self) -> None:
        self._stop_managed_units()
        names = [CONTAINER_NAMES[service] for service in BUILD_SERVICES]
        self._run(["docker", "rm", "--force", *names], check=False)
        still_present: list[str] = []
        for name in names:
            state = self._run(
                ["docker", "inspect", "--format", "{{.State.Running}}", name],
                check=False,
            )
            if state.returncode == 0:
                still_present.append(name)
        if still_present:
            raise ReleaseValidationError(
                "managed containers remained present after removal: "
                + ", ".join(still_present)
            )

    def render_operator_frontend_unit(self, identity: BuildIdentity) -> str:
        frontend_root = self.repo_root / "platform" / "frontend"
        vite = frontend_root / "node_modules" / ".bin" / "vite"
        log_rotator = self.repo_root / "scripts" / "rotate_biomodstack_logs.py"
        limits = services.render_systemd_resource_boundaries(
            services.FRONTEND_SERVICE
        ).replace("\n", "\n            ")
        return dedent(
            f"""\
            [Unit]
            Description=BioModStack operator frontend for managed production
            PartOf={services.TARGET_UNIT}
            After=network-online.target {services.CORE_RUNTIME_SERVICE}
            Wants=network-online.target {services.CORE_RUNTIME_SERVICE}
            StartLimitIntervalSec=300
            StartLimitBurst=3

            [Service]
            Type=simple
            Environment=BMS_HOME={self.repo_root}
            Environment=BMS_RUNTIME_MODE={services.CONTAINER_RUNTIME_MODE}
            Environment=BMS_DEV_API_PROXY_TARGET=http://127.0.0.1:8000
            Environment=VITE_BMS_BUILD_SHA={identity.revision}
            Environment=VITE_BMS_BUILD_ID={identity.build_id}
            Environment=VITE_BMS_BUILD_TIME={identity.build_time}
            WorkingDirectory={frontend_root}
            ExecStartPre=/usr/bin/env python3 {log_rotator}
            ExecStart={vite} --host 127.0.0.1 --port 5173 --strictPort
            Restart=on-failure
            RestartSec=10
            TimeoutStopSec=20
            KillMode=control-group
            {limits}
            StandardOutput=append:{services.FRONTEND_LOG}
            StandardError=append:{services.FRONTEND_LOG}

            [Install]
            WantedBy={services.TARGET_UNIT}
            """
        )

    def install_units(self, identity: BuildIdentity) -> None:
        systemd_dir = services.get_user_systemd_dir()
        systemd_dir.mkdir(parents=True, exist_ok=True)
        rendered = services.render_user_units(
            self.repo_root,
            runtime_mode=services.CONTAINER_RUNTIME_MODE,
        )
        for unit_name in MANAGED_UNIT_NAMES:
            dropin_dir = systemd_dir / f"{unit_name}.d"
            if dropin_dir.exists():
                if not dropin_dir.is_dir() or dropin_dir.is_symlink():
                    raise RuntimeError(
                        f"unsafe systemd drop-in directory for {unit_name}"
        )
                shutil.rmtree(dropin_dir)
            (systemd_dir / unit_name).write_text(rendered[unit_name], encoding="utf-8")
        self._run(["systemctl", "--user", "daemon-reload"])

    def start_candidate(self) -> None:
        services.assert_production_core_listener_preflight(
            project_root=self.repo_root,
        )
        self._run(["systemctl", "--user", "enable", services.CORE_RUNTIME_SERVICE])
        self._run(["systemctl", "--user", "start", services.CORE_RUNTIME_SERVICE])

    def stop_candidate(self) -> None:
        self.stop_installed_owner()

    def restart_known_good(self, snapshot: Mapping[str, Any]) -> None:
        units = self._decoded_unit_snapshot(snapshot)
        for unit_name in MANAGED_UNIT_NAMES:
            if units[unit_name]["active"] is True:
                self._run(
                    [
                        "systemctl",
                        "--user",
                        "start",
                        "--job-mode=ignore-dependencies",
                        unit_name,
                    ]
                )

    @staticmethod
    def _fetch(url: str) -> tuple[int, bytes]:
        request = urllib.request.Request(url, headers={"Accept": "application/json,text/html"})
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                return response.status, response.read(2_000_000)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ReleaseValidationError(f"health request failed for {url}: {exc}") from exc

    @staticmethod
    def _health_revision(
        url: str, label: str, status: int, body: bytes, *, require_full_sha: bool = True
    ) -> str:
        if status != 200:
            raise ReleaseValidationError(f"{label} returned HTTP {status}")
        try:
            payload = json.loads(body)
            ready = payload["readiness"]["ready"] is True
            revision = payload["build"]["revision"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ReleaseValidationError(f"{label} did not return valid readiness JSON") from exc
        if not ready:
            raise ReleaseValidationError(f"{label} readiness is not ready")
        if not isinstance(revision, str) or not revision or (
            require_full_sha and not FULL_GIT_SHA.fullmatch(revision)
        ):
            raise ReleaseValidationError(f"{label} build revision is invalid")
        return revision

    @staticmethod
    def _require_html(label: str, status: int, body: bytes) -> None:
        if status != 200 or b"<html" not in body.lower():
            raise ReleaseValidationError(f"{label} did not return the BioModStack HTML shell")

    @staticmethod
    def _frontend_identity(body: bytes) -> dict[str, str]:
        try:
            source = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseValidationError("operator frontend identity is not UTF-8") from exc
        transformed_env = re.search(r"import\.meta\.env\s*=\s*(\{[^\r\n]*\});", source)
        if transformed_env is not None:
            try:
                env = json.loads(transformed_env.group(1))
            except json.JSONDecodeError as exc:
                raise ReleaseValidationError(
                    "operator frontend identity has invalid transformed environment"
                ) from exc
            layer_match = re.search(r'\blayer\s*:\s*("(?:\\.|[^"\\])*")', source)
            if layer_match is None:
                raise ReleaseValidationError("operator frontend identity is missing layer")
            try:
                layer = json.loads(layer_match.group(1))
            except json.JSONDecodeError as exc:
                raise ReleaseValidationError(
                    "operator frontend identity has invalid layer"
                ) from exc
            transformed_fields = {
                "layer": layer,
                "revision": env.get("VITE_BMS_BUILD_SHA"),
                "buildId": env.get("VITE_BMS_BUILD_ID"),
                "buildTime": env.get("VITE_BMS_BUILD_TIME"),
            }
            for field, value in transformed_fields.items():
                if not isinstance(value, str):
                    raise ReleaseValidationError(
                        f"operator frontend identity is missing {field}"
                    )
            return transformed_fields

        values: dict[str, str] = {}
        for field in ("layer", "revision", "buildId", "buildTime"):
            match = re.search(
                rf'["\']{field}["\']\s*:\s*("(?:\\.|[^"\\])*")',
                source,
            )
            if match is None:
                raise ReleaseValidationError(
                    f"operator frontend identity is missing {field}"
                )
            try:
                value = json.loads(match.group(1))
            except json.JSONDecodeError as exc:
                raise ReleaseValidationError(
                    f"operator frontend identity has invalid {field}"
                ) from exc
            if not isinstance(value, str):
                raise ReleaseValidationError(
                    f"operator frontend identity has invalid {field}"
                )
            values[field] = value
        return values

    def _systemd_property(self, unit_name: str, property_name: str) -> str:
        completed = self._run(
            [
                "systemctl",
                "--user",
                "show",
                unit_name,
                f"--property={property_name}",
                "--value",
            ]
        )
        return completed.stdout.strip()

    @staticmethod
    def _runtime_root_from_environment(environment: str) -> Path:
        values = [
            token.removeprefix("BMS_HOME=")
            for token in environment.split()
            if token.startswith("BMS_HOME=")
        ]
        if len(values) != 1 or not values[0]:
            raise ReleaseValidationError("effective runtime environment has no exact BMS_HOME")
        root = Path(values[0])
        if (
            not root.is_absolute()
            or root.resolve() != root
            or not SYSTEMD_SAFE_VALUE.fullmatch(str(root))
        ):
            raise ReleaseValidationError("effective runtime root is unsafe")
        return root

    def _validate_listener_ownership(self, runtime_root: Path) -> None:
        preflight = services.production_core_listener_preflight(runtime_root)
        components = preflight.get("components", {})
        for component in ("api", "frontend", "cpu-power", "host-agent"):
            record = (
                components.get(component) if isinstance(components, Mapping) else None
            )
            if not isinstance(record, Mapping) or record.get("ok") is not True:
                raise ReleaseValidationError(
                    f"listener ownership is not proven for {component}"
                )

    def _validate_effective_units_and_ownership(self, identity: BuildIdentity) -> None:
        systemd_dir = services.get_user_systemd_dir()
        for unit_name in MANAGED_UNIT_NAMES:
            expected_fragment = str(systemd_dir / unit_name)
            if self._systemd_property(unit_name, "FragmentPath") != expected_fragment:
                raise ReleaseValidationError(
                    f"effective unit source for {unit_name} is not the candidate unit"
                )
        environment = self._systemd_property(
            services.CORE_RUNTIME_SERVICE, "Environment"
        )
        if self._runtime_root_from_environment(environment) != self.repo_root:
            raise ReleaseValidationError(
                "effective production core root is not the candidate root"
        )
        self._validate_listener_ownership(self.repo_root)

    def _observe_runtime_surfaces(
        self,
        expected_identity: BuildIdentity | None = None,
        *,
        allow_unsealed: bool = False,
    ) -> dict[str, Any]:
        api_revision = self._health_revision(
            self.api_url,
            "direct API health",
            *self._fetch(self.api_url),
            require_full_sha=not allow_unsealed,
        )
        if expected_identity is not None and api_revision != expected_identity.revision:
            raise ReleaseValidationError(
                "direct API revision does not match the built release"
            )

        self._require_html("container browser health", *self._fetch(self.browser_url))
        return {
            "api_revision": api_revision,
            "browser_html": True,
        }

    def _snapshot_known_good_validation(
        self, units: Mapping[str, Any]
    ) -> dict[str, Any]:
        effective_units: dict[str, dict[str, str]] = {}
        unit_roots: dict[str, Path] = {}
        any_active = False
        for unit_name in MANAGED_UNIT_NAMES:
            record = units.get(unit_name)
            if not isinstance(record, Mapping):
                raise ReleaseValidationError(f"known-good unit snapshot is invalid for {unit_name}")
            active = record.get("active") is True
            any_active = any_active or active
            if record.get("base") is None:
                if active:
                    raise ReleaseValidationError(f"active known-good unit {unit_name} has no unit file")
                continue
            properties = {
                "FragmentPath": self._systemd_property(unit_name, "FragmentPath")
            }
            if unit_name in {
                services.FRONTEND_SERVICE,
                services.WORKFLOW_ADAPTER_SERVICE,
                services.CORE_RUNTIME_SERVICE,
            }:
                properties["Environment"] = self._systemd_property(unit_name, "Environment")
                properties["ExecStart"] = self._systemd_property(unit_name, "ExecStart")
                if unit_name == services.FRONTEND_SERVICE:
                    properties["WorkingDirectory"] = self._systemd_property(
                        unit_name, "WorkingDirectory"
                    )
                if active:
                    unit_roots[unit_name] = self._runtime_root_from_environment(
                        properties["Environment"]
                    )
            effective_units[unit_name] = properties

        if not any_active:
            return {
                "unit_roots": {},
                "runtime_roots": [],
                "effective_units": effective_units,
                "surfaces": None,
            }
        if not unit_roots:
            raise ReleaseValidationError("active known-good runtime has no exact managed unit roots")
        self._validate_snapshot_listener_ownership(unit_roots)
        return {
            "unit_roots": {
                unit_name: str(runtime_root) for unit_name, runtime_root in unit_roots.items()
            },
            "runtime_roots": sorted({str(runtime_root) for runtime_root in unit_roots.values()}),
            "effective_units": effective_units,
            "surfaces": self._observe_runtime_surfaces(allow_unsealed=True),
        }

    def _validate_snapshot_listener_ownership(self, unit_roots: Mapping[str, Path]) -> None:
        frontend_root = unit_roots.get(services.FRONTEND_SERVICE)
        if frontend_root is not None:
            operator = services.runtime_listener_ownership(
                "operator-frontend", 5173, "dev-frontend", frontend_root
            )
            if operator.get("ok") is not True:
                raise ReleaseValidationError(
                    "listener ownership is not proven for operator frontend 5173"
                )
        workflow_root = unit_roots.get(services.WORKFLOW_ADAPTER_SERVICE)
        if workflow_root is not None:
            workflow = services.runtime_listener_ownership(
                "workflow-adapter",
                services.WORKFLOW_ADAPTER_PORT,
                "workflow-adapter",
                workflow_root,
            )
            if workflow.get("ok") is not True:
                raise ReleaseValidationError(
                    "listener ownership is not proven for workflow adapter"
                )

    def _validate_snapshot_container_listener_ownership(
        self, containers: Mapping[str, Mapping[str, str]]
    ) -> None:
        for service, component, port, owner_kind in (
            ("bms-api", "api", 8000, "api"),
            ("bms-web", "frontend", 18080, "frontend"),
        ):
            record = containers.get(service)
            if not isinstance(record, Mapping):
                raise ReleaseValidationError(f"running container truth is missing for {service}")
            root = Path(record.get("root", ""))
            ownership = services.runtime_listener_ownership(
                component, port, owner_kind, root
            )
            if ownership.get("ok") is not True:
                raise ReleaseValidationError(
                    f"listener ownership is not proven for {component}"
                )

    def _retry_release_validation(self, validator) -> None:
        last_error: ReleaseValidationError | None = None
        for attempt in range(self.validation_attempts):
            try:
                validator()
                return
            except ReleaseValidationError as exc:
                last_error = exc
                if attempt + 1 < self.validation_attempts:
                    time.sleep(self.validation_retry_delay)
        assert last_error is not None
        raise last_error

    def validate_candidate_release(self, identity: BuildIdentity) -> None:
        def validate_once() -> None:
            self._observe_runtime_surfaces(identity)
            self._validate_effective_units_and_ownership(identity)
            self._candidate_running_image_ids()

        self._retry_release_validation(validate_once)

    @staticmethod
    def _decoded_unit_snapshot(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        units = snapshot.get("units")
        if not isinstance(units, Mapping) or set(units) != set(MANAGED_UNIT_NAMES):
            raise ReleaseRollbackError("rollback snapshot has an incomplete or unsafe unit set")
        decoded: dict[str, dict[str, Any]] = {}
        for unit_name in MANAGED_UNIT_NAMES:
            record = units.get(unit_name)
            if not isinstance(record, Mapping):
                raise ReleaseRollbackError(f"invalid rollback unit content for {unit_name}")
            encoded = record.get("base")
            drop_ins = record.get("drop_ins")
            active = record.get("active")
            enabled = record.get("enabled")
            if active is not True and active is not False:
                raise ReleaseRollbackError(f"invalid rollback active state for {unit_name}")
            if enabled is not True and enabled is not False:
                raise ReleaseRollbackError(f"invalid rollback enabled state for {unit_name}")
            if not isinstance(drop_ins, Mapping):
                raise ReleaseRollbackError(f"invalid rollback drop-ins for {unit_name}")
            try:
                base = None if encoded is None else base64.b64decode(encoded, validate=True)
            except (TypeError, ValueError, binascii.Error) as exc:
                raise ReleaseRollbackError(
                    f"invalid rollback unit encoding for {unit_name}"
                ) from exc
            decoded_drop_ins: dict[str, bytes] = {}
            for dropin_name, dropin_encoded in drop_ins.items():
                if (
                    not isinstance(dropin_name, str)
                    or Path(dropin_name).name != dropin_name
                    or not dropin_name.endswith(".conf")
                    or not isinstance(dropin_encoded, str)
                ):
                    raise ReleaseRollbackError(f"unsafe drop-in content for {unit_name}")
                try:
                    decoded_drop_ins[dropin_name] = base64.b64decode(
                        dropin_encoded, validate=True
                    )
                except (ValueError, binascii.Error) as exc:
                    raise ReleaseRollbackError(
                        f"invalid rollback drop-in encoding for {unit_name}"
                    ) from exc
            decoded[unit_name] = {
                "base": base,
                "drop_ins": decoded_drop_ins,
                "active": active,
                "enabled": enabled,
            }
        return decoded

    def _validate_known_good_once(self, snapshot: Mapping[str, Any]) -> None:
        validation = snapshot.get("validation")
        if not isinstance(validation, Mapping):
            raise ReleaseRollbackError("rollback snapshot has no known-good validation truth")
        decoded_units = self._decoded_unit_snapshot(snapshot)
        systemd_dir = services.get_user_systemd_dir()
        for unit_name, record in decoded_units.items():
            path = systemd_dir / unit_name
            expected_base = record["base"]
            if path.is_symlink() or (path.read_bytes() if path.is_file() else None) != expected_base:
                raise ReleaseValidationError(
                    f"restored unit bytes do not match known-good for {unit_name}"
                )
            dropin_dir = systemd_dir / f"{unit_name}.d"
            actual_drop_ins = {}
            if dropin_dir.exists():
                if not dropin_dir.is_dir() or dropin_dir.is_symlink():
                    raise ReleaseValidationError(f"restored drop-in directory is unsafe for {unit_name}")
                for dropin in dropin_dir.iterdir():
                    if not dropin.is_file() or dropin.is_symlink():
                        raise ReleaseValidationError(f"restored drop-in entry is unsafe for {unit_name}")
                    actual_drop_ins[dropin.name] = dropin.read_bytes()
            if actual_drop_ins != record["drop_ins"]:
                raise ReleaseValidationError(
                    f"restored drop-ins do not match known-good for {unit_name}"
                )
            active = self._run(
                ["systemctl", "--user", "is-active", unit_name], check=False
            )
            observed_active = active.returncode == 0 and active.stdout.strip() == "active"
            if observed_active is not record["active"]:
                raise ReleaseValidationError(
                    f"restored active state does not match known-good for {unit_name}"
                )
            enabled = self._run(
                ["systemctl", "--user", "is-enabled", unit_name], check=False
            )
            observed_enabled = enabled.returncode == 0 and enabled.stdout.strip() == "enabled"
            if observed_enabled is not record["enabled"]:
                raise ReleaseValidationError(
                    f"restored enabled state does not match known-good for {unit_name}"
                )

        effective_units = validation.get("effective_units")
        if not isinstance(effective_units, Mapping):
            raise ReleaseRollbackError("rollback snapshot has invalid effective-unit truth")
        for unit_name, properties in effective_units.items():
            if unit_name not in MANAGED_UNIT_NAMES or not isinstance(properties, Mapping):
                raise ReleaseRollbackError("rollback snapshot has unsafe effective-unit truth")
            for property_name, expected_value in properties.items():
                if property_name not in {
                    "FragmentPath",
                    "Environment",
                    "ExecStart",
                    "WorkingDirectory",
                } or not isinstance(expected_value, str):
                    raise ReleaseRollbackError("rollback snapshot has unsafe systemd property truth")
                if self._systemd_property(unit_name, property_name) != expected_value:
                    raise ReleaseValidationError(
                        f"restored effective {property_name} differs for {unit_name}"
                    )

        unit_roots_value = validation.get("unit_roots")
        runtime_roots_value = validation.get("runtime_roots")
        surfaces = validation.get("surfaces")
        if unit_roots_value == {} and runtime_roots_value == [] and surfaces is None:
            return
        if (
            not isinstance(unit_roots_value, Mapping)
            or not isinstance(runtime_roots_value, list)
            or not isinstance(surfaces, Mapping)
        ):
            raise ReleaseRollbackError("rollback snapshot has invalid runtime validation truth")
        unit_roots: dict[str, Path] = {}
        for unit_name, runtime_root_value in unit_roots_value.items():
            if (
                unit_name not in MANAGED_UNIT_NAMES
                or unit_name == services.TARGET_UNIT
                or not isinstance(runtime_root_value, str)
            ):
                raise ReleaseRollbackError("rollback snapshot has unsafe unit-root truth")
            runtime_root = Path(runtime_root_value)
            if (
                not runtime_root.is_absolute()
                or runtime_root.resolve() != runtime_root
                or not SYSTEMD_SAFE_VALUE.fullmatch(runtime_root_value)
            ):
                raise ReleaseRollbackError("rollback snapshot has unsafe runtime root")
            unit_roots[unit_name] = runtime_root
        if runtime_roots_value != sorted({str(root) for root in unit_roots.values()}):
            raise ReleaseRollbackError("rollback snapshot has inconsistent runtime roots")
        observed_surfaces = self._observe_runtime_surfaces(allow_unsealed=True)
        if observed_surfaces != dict(surfaces):
            raise ReleaseValidationError("restored runtime surfaces differ from known-good snapshot")
        self._validate_snapshot_listener_ownership(unit_roots)

    def revalidate_known_good(self, snapshot: Mapping[str, Any]) -> None:
        self._retry_release_validation(lambda: self._validate_known_good_once(snapshot))

    def restore_known_good(self, snapshot: Mapping[str, Any]) -> None:
        image_refs = snapshot.get("image_refs", {})
        images = snapshot.get("images", {})
        if (
            not isinstance(image_refs, Mapping)
            or not isinstance(images, Mapping)
            or set(image_refs) != set(BUILD_SERVICES)
            or set(images) != set(BUILD_SERVICES)
        ):
            raise ReleaseRollbackError("rollback snapshot has invalid image data")
        validated_images: list[tuple[str, str]] = []
        for service in BUILD_SERVICES:
            image_ref = image_refs.get(service)
            image_id = images.get(service)
            if image_ref != self.image_refs.get(service):
                raise ReleaseRollbackError(f"rollback image reference changed for {service}")
            if not image_id:
                continue
            if (
                not isinstance(image_id, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
                or not isinstance(image_ref, str)
            ):
                raise ReleaseRollbackError(f"invalid rollback image identity for {service}")
            validated_images.append((image_id, image_ref))

        units = self._decoded_unit_snapshot(snapshot)
        self._stop_managed_units()
        for image_id, image_ref in validated_images:
            self._run(["docker", "image", "tag", image_id, image_ref])

        systemd_dir = services.get_user_systemd_dir()
        if systemd_dir.is_symlink():
            raise ReleaseRollbackError("unsafe systemd directory for rollback")
        for unit_name in MANAGED_UNIT_NAMES:
            record = units[unit_name]
            path = systemd_dir / unit_name
            dropin_dir = systemd_dir / f"{unit_name}.d"
            path.unlink(missing_ok=True)
            if dropin_dir.exists():
                if not dropin_dir.is_dir() or dropin_dir.is_symlink():
                    raise ReleaseRollbackError(f"unsafe drop-in directory for {unit_name}")
                shutil.rmtree(dropin_dir)
            if record["base"] is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(record["base"])
            for dropin_name, dropin_content in record["drop_ins"].items():
                dropin_dir.mkdir(parents=True, exist_ok=True)
                (dropin_dir / dropin_name).write_bytes(dropin_content)
        self._run(["systemctl", "--user", "daemon-reload"])
        for unit_name in MANAGED_UNIT_NAMES:
            action = "enable" if units[unit_name]["enabled"] else "disable"
            self._run(["systemctl", "--user", action, unit_name])
        if not _snapshot_has_restorable_runtime(snapshot):
            # A first install has no complete known-good image set to restart.
            # Stop any units that may have been partially activated, without
            # calling services.stop_all() (which would re-render current units
            # over the restored unit snapshot).
            self._stop_managed_units()

    def commit_known_good(
        self, snapshot: Mapping[str, Any], identity: BuildIdentity
    ) -> None:
        current_images = self._candidate_running_image_ids()
        payload = {
            "accepted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "build": identity.as_environment(),
            "images": current_images,
            "image_refs": self.image_refs,
            "previous": snapshot,
        }
        _atomic_json_write(self.state_dir / "known-good.json", payload)


def _git_revision(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _materialize_git_revision(repo_root: Path, revision: str, destination: Path) -> None:
    """Write exact committed object bytes without invoking checkout machinery."""
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--full-tree", revision],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
    ).stdout
    entries: list[tuple[bytes, bytes, Path]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_type, object_id = metadata.split(b" ", 2)
        relative_path = Path(os.fsdecode(raw_path))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ReleaseValidationError(f"unsafe path in release source: {relative_path}")
        if object_type != b"blob" or mode not in {b"100644", b"100755", b"120000"}:
            raise ReleaseValidationError(
                f"unsupported Git entry in release source: {mode.decode()} {object_type.decode()} {relative_path}"
            )
        entries.append((mode, object_id, relative_path))

    process = subprocess.Popen(
        ["git", "cat-file", "--batch"],
        cwd=repo_root,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        for mode, object_id, relative_path in entries:
            process.stdin.write(object_id + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().rstrip(b"\n").split(b" ")
            if len(header) != 3 or header[1] != b"blob":
                raise ReleaseValidationError(f"cannot read committed blob for {relative_path}")
            size = int(header[2])
            content = process.stdout.read(size)
            if len(content) != size or process.stdout.read(1) != b"\n":
                raise ReleaseValidationError(f"truncated committed blob for {relative_path}")
            target = destination / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == b"120000":
                os.symlink(os.fsdecode(content), target)
            else:
                target.write_bytes(content)
                target.chmod(0o755 if mode == b"100755" else 0o644)
        process.stdin.close()
        if process.wait() != 0:
            raise ReleaseValidationError("git cat-file failed while materializing release source")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _validated_source_revision(repo_root: Path, requested_revision: str | None = None) -> str:
    """Bind release identity to the exact clean working tree that Compose will build."""
    head = _git_revision(repo_root)
    if requested_revision is not None:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--verify", f"{requested_revision}^{{commit}}"],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise ReleaseValidationError("--revision must resolve to a Git commit") from exc
        if completed.stdout.strip() != head:
            raise ReleaseValidationError(
                "--revision must resolve to the current HEAD because release builds the current checkout"
            )
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ReleaseValidationError(
            "release checkout must be clean; commit or remove tracked and untracked changes before deploy"
        )
    return head


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("plan", help="print the side-effect-free release stages")
    deploy = subparsers.add_parser("deploy", help="run the transactional production release")
    deploy.add_argument("--confirm-runtime-activation", action="store_true")
    deploy.add_argument("--allow-first-install", action="store_true")
    deploy.add_argument("--build-id")
    deploy.add_argument("--revision")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "plan":
        print(json.dumps({"side_effects": False, "stages": release_plan()}, indent=2))
        return 0
    if not args.confirm_runtime_activation:
        raise SystemExit("deploy requires exact --confirm-runtime-activation")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    revision = _validated_source_revision(REPO_ROOT, args.revision)
    identity = BuildIdentity(
        revision=revision,
        build_id=args.build_id or f"{revision[:12]}-{now}",
        build_time=now,
    )
    backend = ProductionReleaseBackend(allow_first_install=args.allow_first_install)
    execute_release(backend, identity)
    print(json.dumps({"status": "accepted", "build": identity.as_environment()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
