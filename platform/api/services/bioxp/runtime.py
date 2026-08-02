from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from pathlib import Path

from paths import get_data_root

from .command_coordinator import CommandCoordinator
from .command_registry import DEFAULT_COMMAND_REGISTRY
from .connection import BioXpConnectionService
from .errors import ProfileStoreError
from .job_store import BioXpJobStore, LegacyMigrationResult
from .profile_store import BioXpProfileStore
from .target_policy import BioXpTargetPolicy


def bioxp_connection_enabled() -> bool:
    return os.getenv("BMS_BIOXP_CONNECTION_ENABLED", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(slots=True)
class BioXpRuntime:
    connection: BioXpConnectionService
    commands: CommandCoordinator
    jobs: BioXpJobStore
    startup_warnings: list[str] = field(default_factory=list)
    legacy_jobs: LegacyMigrationResult = field(default_factory=LegacyMigrationResult)
    _restore_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)

    async def start(self) -> None:
        """Restore the saved managed target without activating or moving hardware."""
        if (
            not bioxp_connection_enabled()
            or self.connection.load_profile() is None
            or self._restore_task is not None
        ):
            return
        self._restore_task = asyncio.create_task(
            self._restore_saved_profile(),
            name="bioxp-saved-profile-restore",
        )
        # Let an immediate in-memory probe settle, but never couple API startup
        # to robot network or snapshot collection timeouts.
        await asyncio.sleep(0)

    async def _restore_saved_profile(self) -> None:
        try:
            await self.connection.connect()
        except Exception as exc:
            self.startup_warnings.append(
                f"Saved BioXP profile was not restored: {str(exc) or exc.__class__.__name__}"
            )

    async def start(self) -> None:
        """Restore the saved managed target without activating or moving hardware."""
        try:
            if self.connection.load_profile() is None:
                return
            await self.connection.connect()
        except Exception as exc:
            self.startup_warnings.append(
                f"Saved BioXP profile was not restored: {str(exc) or exc.__class__.__name__}"
            )

    async def close(self) -> None:
        restore_task = self._restore_task
        self._restore_task = None
        if restore_task is not None and not restore_task.done():
            restore_task.cancel()
            try:
                await restore_task
            except asyncio.CancelledError:
                pass
        await self.connection.close()
        self.jobs.close()


def create_bioxp_runtime(*, data_root: Path | None = None) -> BioXpRuntime:
    """Create one application-lifetime BioXP runtime; start() restores a saved target."""

    root = (data_root or get_data_root()).expanduser().resolve()
    state_root = root / "bioxp"
    profile_store = BioXpProfileStore(
        state_root / "profile.json",
        legacy_path=root / "bioxp_interlink_profile.json",
    )
    policy = BioXpTargetPolicy.from_environment()
    connection = BioXpConnectionService(
        profile_store=profile_store,
        target_policy=policy,
        active_probe_interval_seconds=10.0,
    )
    jobs = BioXpJobStore(state_root / "jobs.sqlite3")
    runtime = BioXpRuntime(
        connection=connection,
        commands=CommandCoordinator(connection=connection, registry=dict(DEFAULT_COMMAND_REGISTRY)),
        jobs=jobs,
    )

    try:
        profile_store.migrate_legacy()
    except ProfileStoreError as exc:
        runtime.startup_warnings.append(f"Legacy BioXP profile was not migrated: {exc}")

    legacy_jobs = root / "bioxp_jobs"
    quarantine = state_root / "legacy-quarantine"
    runtime.legacy_jobs = jobs.migrate_legacy_json(legacy_jobs, quarantine)
    return runtime
