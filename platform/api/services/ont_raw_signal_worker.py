from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Any

from services.ont_raw_signal import (
    EXTERNAL_BLOW5_VALIDATION_PROFILE_ID,
    claim_next_derivation,
    claim_next_waveform_lookup,
    close_source_identity,
    complete_external_blow5_validation,
    conversion_partition_groups,
    conversion_semantic_command,
    conversion_unit_commands,
    derivation_cancellation_requested,
    fail_waveform_lookup,
    finish_waveform_lookup,
    publish_derivation,
    recover_expired_derivations,
    renew_derivation_lease,
    renew_waveform_lookup_lease,
    transition_derivation,
)

logger = logging.getLogger(__name__)


class OntRawSignalWorker:
    """Single-owner leased conversion, validation, and publication worker."""

    def __init__(self, session_factory: Any, *, poll_interval: float = 5.0):
        self._session_factory = session_factory
        self._poll_interval = max(1.0, float(poll_interval))
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._child: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        async with self._session_factory() as session:
            await recover_expired_derivations(session)
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ont-raw-signal-worker")

    async def stop(self) -> None:
        self._stop.set()
        child = self._child
        if child is not None and child.returncode is None:
            child.terminate()
            await child.wait()
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _command_receipt(command: list[str], returncode: int, stdout: bytes, stderr: bytes) -> dict[str, Any]:
        return {
            "argv_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
            "returncode": returncode,
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        }

    async def _execute(self, command: list[str], job_id: str, claim_token: str, *, waveform: bool = False) -> dict[str, Any]:
        self._child = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
            start_new_session=True,
        )
        communication = asyncio.create_task(self._child.communicate())
        while not communication.done():
            try:
                await asyncio.wait_for(asyncio.shield(communication), timeout=60.0)
            except asyncio.TimeoutError:
                try:
                    async with self._session_factory() as session:
                        if waveform:
                            await renew_waveform_lookup_lease(session, job_id, claim_token)
                        else:
                            await renew_derivation_lease(session, job_id, claim_token)
                except Exception:
                    if self._child is not None and self._child.returncode is None:
                        self._child.terminate()
                        await self._child.wait()
                    communication.cancel()
                    self._child = None
                    raise
        stdout, stderr = await communication
        receipt = self._command_receipt(command, int(self._child.returncode or 0), stdout, stderr)
        self._child = None
        if receipt["returncode"] != 0:
            raise RuntimeError(f"raw-signal stage command failed with exit {receipt['returncode']}")
        return receipt

    async def run_once(self) -> int:
        async with self._session_factory() as session:
            waveform = await claim_next_waveform_lookup(session)
        if waveform is not None:
            lookup, command, output = waveform
            claim_token = str(lookup.claim_token)
            try:
                receipt = await self._execute(command, lookup.id, claim_token, waveform=True)
                async with self._session_factory() as session:
                    await finish_waveform_lookup(session, lookup.id, claim_token, output, receipt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self._session_factory() as session:
                    await fail_waveform_lookup(session, lookup.id, claim_token, f"waveform_{type(exc).__name__}")
            return 1
        async with self._session_factory() as session:
            claimed = await claim_next_derivation(session)
        if claimed is None:
            return 0
        job, source, commands = claimed
        claim_token = str(job.claim_token)
        stage = Path(commands["stage"])
        stage.mkdir(parents=True, mode=0o700, exist_ok=False)
        try:
            if job.profile_id == EXTERNAL_BLOW5_VALIDATION_PROFILE_ID:
                async with self._session_factory() as session:
                    await transition_derivation(session, job.id, claim_token, "structural_check", "external_quickcheck_started", {})
                quickcheck_receipt = await self._execute(commands["quickcheck"], job.id, claim_token)
                async with self._session_factory() as session:
                    await transition_derivation(session, job.id, claim_token, "index_validation", "external_index_validation_started", quickcheck_receipt)
                validation_receipt = await self._execute(commands["semantic_validate"], job.id, claim_token)
                async with self._session_factory() as session:
                    live_job = await session.get(type(job), job.id)
                    live_source = await session.get(type(source), source.id)
                    if live_job is None or live_source is None:
                        raise RuntimeError("external BLOW5 validation authority disappeared")
                    representation = await complete_external_blow5_validation(session, live_job, live_source, commands)
                    await transition_derivation(session, job.id, claim_token, "ready", "external_indexed_blow5_validated", {"representation_id": representation.id, "validation": validation_receipt})
                return 1
            source_receipt = await self._execute(commands["source_preflight"], job.id, claim_token)
            async with self._session_factory() as session:
                source = await close_source_identity(
                    session, source.id, job.id, claim_token, commands["source_receipt"]
                )
                await transition_derivation(
                    session, job.id, claim_token, "partitioning", "source_preflight_passed", source_receipt
                )
            groups = conversion_partition_groups(commands)
            Path(commands["partitions"]).mkdir(mode=0o700)
            Path(commands["outputs"]).mkdir(mode=0o700)
            partition_receipt = await self._execute(commands["partition"], job.id, claim_token)
            async with self._session_factory() as session:
                await transition_derivation(
                    session,
                    job.id,
                    claim_token,
                    "partitioning",
                    "complete_run_info_partitioning_passed",
                    {"partition_count": len(groups), "command": partition_receipt},
                )
            unit_commands = {group: conversion_unit_commands(commands, group) for group in groups}
            stages = (
                ("converting", "conversion_processes_complete", "convert"),
                ("structural_check", "slow5tools_quickcheck_passed", "quickcheck"),
                ("indexing", "adjacent_indexes_created", "index_create"),
            )
            for state, reason, command_name in stages:
                async with self._session_factory() as session:
                    await transition_derivation(session, job.id, claim_token, state, f"{state}_started", {})
                unit_receipts: dict[str, Any] = {}
                for group in groups:
                    unit_receipts[group] = await self._execute(
                        unit_commands[group][command_name], job.id, claim_token
                    )
                async with self._session_factory() as session:
                    await transition_derivation(
                        session,
                        job.id,
                        claim_token,
                        state,
                        reason,
                        {"partition_count": len(groups), "units": unit_receipts},
                    )
            async with self._session_factory() as session:
                await transition_derivation(session, job.id, claim_token, "index_validation", "index_validation_started", {})
            validation_receipt = await self._execute(
                conversion_semantic_command(commands, groups), job.id, claim_token
            )
            async with self._session_factory() as session:
                await transition_derivation(session, job.id, claim_token, "index_validation", "index_open_lookup_validation_passed", validation_receipt)
                await transition_derivation(session, job.id, claim_token, "semantic_validation", "exhaustive_semantic_validation_passed", validation_receipt)
                await transition_derivation(session, job.id, claim_token, "publishing", "atomic_publication_started", {})
                live_job = await session.get(type(job), job.id)
                live_source = await session.get(type(source), source.id)
                if live_job is None or live_source is None:
                    raise RuntimeError("raw-signal publication authority disappeared")
                representation = await publish_derivation(session, live_job, live_source, commands)
                await transition_derivation(session, job.id, claim_token, "ready", "indexed_blow5_ready", {"representation_id": representation.id})
            return 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("ONT raw-signal derivation failed: %s", job.id)
            async with self._session_factory() as session:
                try:
                    cancelled = await derivation_cancellation_requested(session, job.id, claim_token)
                    await transition_derivation(
                        session,
                        job.id,
                        claim_token,
                        "cancelled" if cancelled else "failed",
                        "cancelled_child_terminated" if cancelled else "derivation_stage_failed",
                        {"error_type": type(exc).__name__},
                    )
                except Exception:
                    logger.exception("Could not persist ONT raw-signal failure receipt: %s", job.id)
            return 1

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                handled = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ONT raw-signal worker iteration failed")
                handled = 0
            if handled:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass
