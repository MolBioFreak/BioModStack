from __future__ import annotations

import asyncio
import array
import hashlib
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any

from sqlalchemy import select

from database import OntInstrumentRun
from services.ont_raw_signal import (
    EXTERNAL_BLOW5_VALIDATION_PROFILE_ID,
    claim_next_derivation,
    claim_next_waveform_lookup,
    close_source_identity,
    complete_external_blow5_validation,
    conversion_partition_groups,
    conversion_semantic_command,
    conversion_unit_commands,
    defer_derivation,
    derivation_cancellation_requested,
    derivation_spawn_admission_lost,
    fail_waveform_lookup,
    finish_waveform_lookup,
    pin_conversion_source_descriptors,
    pin_external_blow5_descriptors,
    publish_derivation,
    recover_expired_derivations,
    raw_signal_runtime_identity,
    renew_derivation_lease,
    renew_waveform_lookup_lease,
    waveform_spawn_admission_lost,
    assert_local_raw_runtime_image,
    source_lease_break_requested,
    SourceLeaseUnavailable,
    transition_derivation,
    _assert_publication_directory_identity,
    _prepare_confined_directory,
)

logger = logging.getLogger(__name__)
RAW_SIGNAL_WAVEFORM_RUNTIME_TIMEOUT_SECONDS = 120.0
RAW_SIGNAL_CHILD_TERMINATE_GRACE_SECONDS = 5.0


class OntRawSignalWorker:
    """Single-owner leased conversion, validation, and publication worker."""

    def __init__(self, session_factory: Any, *, poll_interval: float = 5.0):
        self._session_factory = session_factory
        self._poll_interval = max(1.0, float(poll_interval))
        self._task: asyncio.Task[None] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._child: asyncio.subprocess.Process | None = None

    async def _terminate_child(self) -> None:
        child = self._child
        if child is None or child.returncode is not None:
            return
        child.terminate()
        try:
            await asyncio.wait_for(
                child.wait(), timeout=RAW_SIGNAL_CHILD_TERMINATE_GRACE_SECONDS
            )
        except asyncio.TimeoutError:
            if child.returncode is None:
                child.kill()
            await asyncio.wait_for(
                child.wait(), timeout=RAW_SIGNAL_CHILD_TERMINATE_GRACE_SECONDS
            )

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        async with self._session_factory() as session:
            await recover_expired_derivations(session)
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ont-raw-signal-worker")
        self._monitor_task = asyncio.create_task(
            self._monitor_live_runs(), name="ont-live-pod5-monitor"
        )

    async def stop(self) -> None:
        self._stop.set()
        task = self._task
        monitor_task = self._monitor_task
        self._task = None
        self._monitor_task = None
        for running_task in (task, monitor_task):
            if running_task is None:
                continue
            running_task.cancel()
            try:
                await running_task
            except asyncio.CancelledError:
                pass
        await self._terminate_child()
        self._child = None

    async def _reconcile_live_runs_once(self) -> int:
        from services.ont_run_control import reconcile_instrument_run

        async with self._session_factory() as session:
            run_ids = list(
                (
                    await session.execute(
                        select(OntInstrumentRun.id)
                        .where(
                            OntInstrumentRun.state.in_(("starting", "running", "stopping")),
                            OntInstrumentRun.minknow_run_id.is_not(None),
                        )
                        .order_by(OntInstrumentRun.observed_at, OntInstrumentRun.id)
                        .limit(100)
                    )
                ).scalars()
            )
        reconciled = 0
        for run_id in run_ids:
            try:
                await reconcile_instrument_run(str(run_id))
                reconciled += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ONT live POD5 monitor reconciliation failed: %s", run_id)
        return reconciled

    async def _monitor_live_runs(self) -> None:
        while not self._stop.is_set():
            try:
                await self._reconcile_live_runs_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ONT live POD5 monitor iteration failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    def _command_receipt(command: list[str], returncode: int, stdout: bytes, stderr: bytes) -> dict[str, Any]:
        return {
            "argv_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
            "returncode": returncode,
            "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        }

    @staticmethod
    def _assert_waveform_command_policy(command: list[str]) -> None:
        if len(command) < 3 or command[1] != "run":
            raise RuntimeError("waveform command is not a container run")
        identity = raw_signal_runtime_identity()
        assert_local_raw_runtime_image(command[0], identity["image"])
        if [arg for arg in command if arg.startswith("--pull=")] != ["--pull=never"]:
            raise RuntimeError("waveform command must disable image pulls")
        if [arg for arg in command if arg.startswith("--network=")] != ["--network=none"]:
            raise RuntimeError("waveform command must disable network access")
        value_options = {"--mount", "--user", "--cpus", "--memory", "--pids-limit", "--ulimit"}
        index = 2
        while index < len(command):
            argument = command[index]
            if argument.startswith("--"):
                index += 2 if argument in value_options else 1
                continue
            break
        if index >= len(command) or command[index] != identity["image"]:
            raise RuntimeError("waveform command image does not match checked-in policy")

    async def _execute(
        self,
        command: list[str],
        job_id: str,
        claim_token: str,
        *,
        waveform: bool = False,
        source_fds: list[int] | None = None,
        fd_socket: str | None = None,
    ) -> dict[str, Any]:
        if waveform:
            self._assert_waveform_command_policy(command)
        if source_fds and source_lease_break_requested():
            raise RuntimeError("raw-signal source write lease break was requested")
        descriptor_server: socket.socket | None = None
        descriptor_thread: threading.Thread | None = None
        descriptor_errors: list[BaseException] = []
        if source_fds:
            if not fd_socket:
                raise RuntimeError("source descriptor socket is missing")
            socket_path = Path(fd_socket)
            socket_path.unlink(missing_ok=True)
            descriptor_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            descriptor_server.bind(str(socket_path))
            descriptor_server.listen(1)
            descriptor_server.settimeout(0.5)

            def send_descriptors() -> None:
                try:
                    assert descriptor_server is not None
                    while True:
                        try:
                            connection, _ = descriptor_server.accept()
                            break
                        except socket.timeout:
                            continue
                    with connection:
                        payload = array.array("i", source_fds)
                        connection.sendmsg(
                            [b"F"],
                            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, payload)],
                        )
                except BaseException as exc:
                    descriptor_errors.append(exc)

            descriptor_thread = threading.Thread(target=send_descriptors, daemon=True)
            descriptor_thread.start()
        session_context = self._session_factory() if self._session_factory is not None else None
        if session_context is not None:
            async with session_context as session:
                if waveform:
                    await renew_waveform_lookup_lease(session, job_id, claim_token)
                else:
                    await renew_derivation_lease(session, job_id, claim_token)
        communication: asyncio.Task[tuple[bytes, bytes]] | None = None
        try:
            self._child = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent"},
                start_new_session=True,
            )
            admission_lost = False
            session_context = self._session_factory() if self._session_factory is not None else None
            if session_context is not None:
                try:
                    async with session_context as session:
                        admission_lost = (
                            await waveform_spawn_admission_lost(session, job_id, claim_token)
                            if waveform
                            else await derivation_spawn_admission_lost(session, job_id, claim_token)
                        )
                except BaseException:
                    await self._terminate_child()
                    self._child = None
                    raise
            if admission_lost:
                await self._terminate_child()
                self._child = None
                raise RuntimeError("raw-signal claim was cancelled or lost before stage execution")
            communication = asyncio.create_task(self._child.communicate())
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            last_renewal = started_at
            while not communication.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(communication),
                        timeout=0.25 if source_fds or waveform else 60.0,
                    )
                except asyncio.TimeoutError:
                    if source_fds and source_lease_break_requested():
                        await self._terminate_child()
                        communication.cancel()
                        raise RuntimeError("raw-signal source write lease break was requested")
                    now = loop.time()
                    if waveform and now - started_at >= RAW_SIGNAL_WAVEFORM_RUNTIME_TIMEOUT_SECONDS:
                        await self._terminate_child()
                        communication.cancel()
                        try:
                            await communication
                        except asyncio.CancelledError:
                            pass
                        self._child = None
                        raise RuntimeError("raw-signal waveform runtime exceeded")
                    if now - last_renewal < 60.0:
                        continue
                    try:
                        async with self._session_factory() as session:
                            if waveform:
                                await renew_waveform_lookup_lease(session, job_id, claim_token)
                            else:
                                await renew_derivation_lease(session, job_id, claim_token)
                        last_renewal = now
                    except Exception:
                        await self._terminate_child()
                        communication.cancel()
                        self._child = None
                        raise
            stdout, stderr = await communication
        except asyncio.CancelledError:
            await self._terminate_child()
            if communication is not None and not communication.done():
                communication.cancel()
                try:
                    await communication
                except asyncio.CancelledError:
                    pass
            self._child = None
            raise
        finally:
            if descriptor_server is not None:
                descriptor_server.close()
            if descriptor_thread is not None:
                descriptor_thread.join(timeout=5.0)
            if fd_socket:
                Path(fd_socket).unlink(missing_ok=True)
        if descriptor_errors:
            self._child = None
            raise RuntimeError("source descriptor transfer failed") from descriptor_errors[0]
        receipt = self._command_receipt(command, int(self._child.returncode or 0), stdout, stderr)
        self._child = None
        if receipt["returncode"] != 0:
            raise RuntimeError(f"raw-signal stage command failed with exit {receipt['returncode']}")
        return receipt

    async def run_once(self) -> int:
        async with self._session_factory() as session:
            await recover_expired_derivations(session)
            waveform = await claim_next_waveform_lookup(session)
        if waveform is not None:
            lookup, command, output, source = waveform
            claim_token = str(lookup.claim_token)
            source_fds = list(source["source_fds"])
            try:
                receipt = await self._execute(
                    command,
                    lookup.id,
                    claim_token,
                    waveform=True,
                    source_fds=source_fds,
                    fd_socket=str(source["fd_socket"]),
                )
                async with self._session_factory() as session:
                    await finish_waveform_lookup(session, lookup.id, claim_token, output, receipt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self._session_factory() as session:
                    await fail_waveform_lookup(session, lookup.id, claim_token, f"waveform_{type(exc).__name__}")
            finally:
                for descriptor in source_fds:
                    os.close(descriptor)
            return 1
        async with self._session_factory() as session:
            claimed = await claim_next_derivation(session)
        if claimed is None:
            return 0
        job, source, commands = claimed
        claim_token = str(job.claim_token)
        stage = Path(commands["stage"])
        stage_fd = -1
        pinned_source_fds: list[int] = []
        external_source_fds: list[int] = []
        try:
            stage_fd = _prepare_confined_directory(
                stage.parents[1], (stage.parent.name, stage.name)
            )

            async def execute_stage(command: list[str], item_id: str, token: str, **kwargs: Any) -> dict[str, Any]:
                _assert_publication_directory_identity(
                    stage, stage_fd, authority="derivation staging directory"
                )
                return await self._execute(command, item_id, token, **kwargs)

            if job.profile_id == EXTERNAL_BLOW5_VALIDATION_PROFILE_ID:
                external_source_fds = pin_external_blow5_descriptors(commands)
            else:
                pinned_source_fds = pin_conversion_source_descriptors(commands)
            if job.profile_id == EXTERNAL_BLOW5_VALIDATION_PROFILE_ID:
                async with self._session_factory() as session:
                    await transition_derivation(session, job.id, claim_token, "structural_check", "external_quickcheck_started", {})
                quickcheck_receipt = await execute_stage(
                    commands["quickcheck"], job.id, claim_token,
                    source_fds=external_source_fds, fd_socket=commands["fd_socket"],
                )
                async with self._session_factory() as session:
                    await transition_derivation(session, job.id, claim_token, "index_validation", "external_index_validation_started", quickcheck_receipt)
                validation_receipt = await execute_stage(
                    commands["semantic_validate"], job.id, claim_token,
                    source_fds=external_source_fds, fd_socket=commands["fd_socket"],
                )
                async with self._session_factory() as session:
                    live_job = await session.get(type(job), job.id)
                    live_source = await session.get(type(source), source.id)
                    if live_job is None or live_source is None:
                        raise RuntimeError("external BLOW5 validation authority disappeared")
                    representation = await complete_external_blow5_validation(session, live_job, live_source, commands)
                    await transition_derivation(session, job.id, claim_token, "ready", "external_indexed_blow5_validated", {"representation_id": representation.id, "validation": validation_receipt})
                return 1
            source_receipt = await execute_stage(
                commands["source_preflight"], job.id, claim_token,
                source_fds=pinned_source_fds, fd_socket=commands["fd_socket"],
            )
            async with self._session_factory() as session:
                source = await close_source_identity(
                    session, source.id, job.id, claim_token, commands["source_receipt"]
                )
                await transition_derivation(
                    session, job.id, claim_token, "partitioning", "source_preflight_passed", source_receipt
                )
            groups = conversion_partition_groups(commands)
            for directory_name in ("partitions", "outputs"):
                directory_fd = _prepare_confined_directory(stage, (directory_name,))
                os.close(directory_fd)
            partition_receipt = await execute_stage(
                commands["partition"], job.id, claim_token,
                source_fds=pinned_source_fds, fd_socket=commands["fd_socket"],
            )
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
                    unit_receipts[group] = await execute_stage(
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
            validation_receipt = await execute_stage(
                conversion_semantic_command(commands, groups), job.id, claim_token,
                source_fds=pinned_source_fds, fd_socket=commands["fd_socket"],
            )
            if source_lease_break_requested():
                raise RuntimeError("raw-signal source write lease break was requested")
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
        except SourceLeaseUnavailable as exc:
            logger.info("ONT raw-signal source lease is temporarily unavailable: %s", job.id)
            async with self._session_factory() as session:
                try:
                    cancelled = await derivation_cancellation_requested(session, job.id, claim_token)
                    if cancelled:
                        await transition_derivation(
                            session, job.id, claim_token, "cancelled",
                            "cancelled_child_terminated", {"error_type": type(exc).__name__},
                        )
                    else:
                        await defer_derivation(
                            session, job.id, claim_token, "source_lease_unavailable_retry",
                            {"error_type": type(exc).__name__},
                        )
                except Exception:
                    logger.exception("Could not persist ONT raw-signal retry receipt: %s", job.id)
            return 1
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
        finally:
            for fd in (*pinned_source_fds, *external_source_fds):
                os.close(fd)
            if stage_fd >= 0:
                os.close(stage_fd)

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
