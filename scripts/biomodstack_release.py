#!/usr/bin/env python3
"""Transactional BioModStack container release with automatic rollback.

This is the single production deployment path.  Merely importing it, running
``plan``, or running its unit tests has no runtime side effects.  ``deploy``
requires the exact ``--confirm-runtime-activation`` gate.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import biomodstack_services as services


FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
BUILD_SERVICES = (
    "bms-api",
    "bms-host-agent",
    "bms-cpu-power",
    "bms-stats-tools",
    "bms-web",
)
IMAGE_REFS = {
    "bms-api": "BMS_API_IMAGE",
    "bms-host-agent": "BMS_HOST_AGENT_IMAGE",
    "bms-cpu-power": "BMS_CPU_POWER_IMAGE",
    "bms-stats-tools": "BMS_STATS_TOOLS_IMAGE",
    "bms-web": "BMS_WEB_IMAGE",
}
IMAGE_DEFAULTS = {
    "bms-api": "biomodstack/api:local",
    "bms-host-agent": "biomodstack/host-agent:local",
    "bms-cpu-power": "biomodstack/cpu-power:local",
    "bms-stats-tools": "biomodstack/stats-tools:local",
    "bms-web": "biomodstack/web:local",
}


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

    def as_environment(self) -> dict[str, str]:
        return {
            "BMS_BUILD_SHA": self.revision,
            "BMS_BUILD_ID": self.build_id,
            "BMS_BUILD_TIME": self.build_time,
        }


class ReleaseBackend(Protocol):
    def snapshot_known_good(self) -> Mapping[str, Any]: ...
    def build_images(self, identity: BuildIdentity) -> None: ...
    def verify_image_provenance(self, identity: BuildIdentity) -> None: ...
    def install_units(self) -> None: ...
    def restart_runtime(self) -> None: ...
    def validate_runtime(self, identity: BuildIdentity | None = None) -> None: ...
    def restore_known_good(self, snapshot: Mapping[str, Any]) -> None: ...
    def commit_known_good(self, snapshot: Mapping[str, Any], identity: BuildIdentity) -> None: ...


def release_plan() -> tuple[str, ...]:
    return (
        "snapshot-known-good",
        "build-images-explicitly",
        "verify-image-provenance",
        "render-install-units",
        "restart-container-runtime",
        "validate-api-readiness-and-provenance",
        "validate-browser-health",
        "commit-known-good",
    )


def _snapshot_has_restorable_runtime(snapshot: Mapping[str, Any]) -> bool:
    images = snapshot.get("images")
    if not isinstance(images, Mapping) or not images:
        return False
    image_refs = snapshot.get("image_refs")
    required_services = (
        tuple(image_refs)
        if isinstance(image_refs, Mapping) and image_refs
        else tuple(images)
    )
    return bool(required_services) and all(bool(images.get(service)) for service in required_services)


def execute_release(backend: ReleaseBackend, identity: BuildIdentity) -> None:
    snapshot = backend.snapshot_known_good()
    try:
        backend.build_images(identity)
        backend.verify_image_provenance(identity)
        backend.install_units()
        backend.restart_runtime()
        backend.validate_runtime(identity)
    except BaseException as release_error:
        try:
            backend.restore_known_good(snapshot)
            if not _snapshot_has_restorable_runtime(snapshot):
                raise release_error
            backend.restart_runtime()
            backend.validate_runtime()
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
        allow_first_install: bool = False,
    ) -> None:
        self.repo_root = repo_root.resolve()
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
        self.allow_first_install = allow_first_install
        self.image_refs = {
            service: os.environ.get(IMAGE_REFS[service], IMAGE_DEFAULTS[service])
            for service in BUILD_SERVICES
        }
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
        return value if completed.returncode == 0 and value else None

    def _unit_snapshot(self) -> dict[str, str | None]:
        rendered = services.render_user_units(
            project_root=self.repo_root,
            runtime_mode=services.CONTAINER_RUNTIME_MODE,
        )
        systemd_dir = services.get_user_systemd_dir()
        snapshot: dict[str, str | None] = {}
        for unit_name in rendered:
            path = systemd_dir / unit_name
            snapshot[unit_name] = (
                base64.b64encode(path.read_bytes()).decode("ascii") if path.exists() else None
            )
        return snapshot

    def snapshot_known_good(self) -> Mapping[str, Any]:
        images = {service: self._image_id(ref) for service, ref in self.image_refs.items()}
        missing = [service for service, image_id in images.items() if image_id is None]
        if missing and not self.allow_first_install:
            raise RuntimeError(
                "no restorable known-good image for " + ", ".join(missing) +
                "; use --allow-first-install only for a new installation"
            )
        snapshot = {
            "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "images": images,
            "image_refs": self.image_refs,
            "units": self._unit_snapshot(),
        }
        _atomic_json_write(self.state_dir / "pre-deploy.json", snapshot)
        return snapshot

    def build_images(self, identity: BuildIdentity) -> None:
        self.identity = identity
        self._compose(
            "build",
            "--pull",
            *BUILD_SERVICES,
            env=identity.as_environment(),
            capture_output=False,
        )

    def verify_image_provenance(self, identity: BuildIdentity) -> None:
        for service, image_ref in self.image_refs.items():
            completed = self._run(
                [
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
                    image_ref,
                ]
            )
            if completed.stdout.strip() != identity.revision:
                raise ReleaseValidationError(
                    f"{service} image revision does not match {identity.revision}"
                )

    def install_units(self) -> None:
        services.install_user_units(
            project_root=self.repo_root,
            runtime_mode=services.CONTAINER_RUNTIME_MODE,
        )
        services.daemon_reload(project_root=self.repo_root)

    def restart_runtime(self) -> None:
        services.restart_all(
            project_root=self.repo_root,
            runtime_mode=services.CONTAINER_RUNTIME_MODE,
        )

    @staticmethod
    def _fetch(url: str) -> tuple[int, bytes]:
        request = urllib.request.Request(url, headers={"Accept": "application/json,text/html"})
        try:
            with urllib.request.urlopen(request, timeout=5.0) as response:
                return response.status, response.read(2_000_000)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ReleaseValidationError(f"health request failed for {url}: {exc}") from exc

    def validate_runtime(self, identity: BuildIdentity | None = None) -> None:
        api_status, api_body = self._fetch(self.api_url)
        if api_status != 200:
            raise ReleaseValidationError(f"API health returned HTTP {api_status}")
        try:
            payload = json.loads(api_body)
        except (TypeError, ValueError) as exc:
            raise ReleaseValidationError("API health did not return valid JSON") from exc
        if not payload.get("readiness", {}).get("ready"):
            raise ReleaseValidationError(f"API readiness failed: {payload.get('readiness')}")
        if identity is not None and payload.get("build", {}).get("revision") != identity.revision:
            raise ReleaseValidationError("API revision does not match the built release")
        browser_status, browser_body = self._fetch(self.browser_url)
        if browser_status != 200 or b"<html" not in browser_body.lower():
            raise ReleaseValidationError("browser health did not return the BioModStack HTML shell")

    def restore_known_good(self, snapshot: Mapping[str, Any]) -> None:
        image_refs = snapshot.get("image_refs", {})
        images = snapshot.get("images", {})
        if not isinstance(image_refs, Mapping) or not isinstance(images, Mapping):
            raise ReleaseRollbackError("rollback snapshot has invalid image data")
        for service, image_id in images.items():
            if not image_id:
                continue
            image_ref = image_refs.get(service)
            if not isinstance(image_ref, str):
                raise ReleaseRollbackError(f"missing rollback image reference for {service}")
            self._run(["docker", "image", "tag", str(image_id), image_ref])

        units = snapshot.get("units", {})
        if not isinstance(units, Mapping):
            raise ReleaseRollbackError("rollback snapshot has invalid unit data")
        systemd_dir = services.get_user_systemd_dir()
        for unit_name, encoded in units.items():
            if not isinstance(unit_name, str) or Path(unit_name).name != unit_name:
                raise ReleaseRollbackError("unsafe unit name in rollback snapshot")
            path = systemd_dir / unit_name
            if encoded is None:
                path.unlink(missing_ok=True)
            elif isinstance(encoded, str):
                path.write_bytes(base64.b64decode(encoded, validate=True))
            else:
                raise ReleaseRollbackError(f"invalid rollback unit content for {unit_name}")
        services.daemon_reload(project_root=self.repo_root)
        if not _snapshot_has_restorable_runtime(snapshot):
            # A first install has no complete known-good image set to restart.
            # Stop any units that may have been partially activated, without
            # calling services.stop_all() (which would re-render current units
            # over the restored unit snapshot).
            self._run(
                [
                    "systemctl",
                    "--user",
                    "stop",
                    services.TARGET_UNIT,
                    services.WORKFLOW_ADAPTER_SERVICE,
                    services.CORE_RUNTIME_SERVICE,
                ],
                check=False,
            )

    def commit_known_good(
        self, snapshot: Mapping[str, Any], identity: BuildIdentity
    ) -> None:
        current_images = {
            service: self._image_id(image_ref)
            for service, image_ref in self.image_refs.items()
        }
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
    revision = args.revision or _git_revision(REPO_ROOT)
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
