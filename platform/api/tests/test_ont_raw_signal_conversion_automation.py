from __future__ import annotations

import argparse
import array
import asyncio
import hashlib
import importlib.util
import inspect
import json
import os
import sqlite3
import socket
import subprocess
import threading
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pod5
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.filterwarnings("ignore:Call to deprecated function.*:DeprecationWarning"),
    pytest.mark.filterwarnings("ignore:datetime.datetime.utcnow.*:DeprecationWarning"),
]

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "platform" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "platform" / "api"))

from database import (
    Base,
    OntInstrumentRun,
    OntInstrumentRunEvent,
    OntRawSignalDerivationJob,
    OntRawSignalRepresentation,
)
from migrations.seal_ont_external_source_identity import migrate as seal_external_source_identity
from services import ont_raw_signal, ont_run_control
from services.ont_raw_signal_worker import OntRawSignalWorker

QUALIFICATION_ROOT = Path("/mnt/BioModStack/ont-raw-signal-qualification/BFX6NB_1_JAN26-EL-Q2-01/subset-pod5")
QUALIFICATION_PARTITIONS = (
    QUALIFICATION_ROOT / "14e84168825dc5524fd61c441b7a5ad31b71ea16796502fccba679b6448bda45.pod5",
    QUALIFICATION_ROOT / "fdb1de8a28aad4ed732e9bf10f35faf75b4d1d8518a9385a5c4cceb1c2dc6cb5.pod5",
)


def _load_validator():
    path = ROOT / "scripts" / "ont_raw_signal_validate.py"
    spec = importlib.util.spec_from_file_location("ont_raw_signal_validate_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_mixed_pod5(path: Path) -> tuple[str, list[Any]]:
    reads = []
    for source in QUALIFICATION_PARTITIONS:
        assert source.is_file(), source
        with pod5.Reader(source) as reader:
            reads.append(next(reader.reads()).to_read())
    assert len({repr(read.run_info) for read in reads}) == 2
    with pod5.Writer(path, software_name="BioModStack raw-signal conversion test") as writer:
        for read in reads:
            writer.add_read(read)
    return str(reads[0].run_info.acquisition_id), reads


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_with_source_fds(command: list[str], fds: list[int], socket_path: Path, *, timeout: int) -> None:
    socket_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)
    errors: list[BaseException] = []

    def send() -> None:
        try:
            connection, _ = server.accept()
            with connection:
                connection.sendmsg(
                    [b"F"],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))],
                )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    try:
        result = subprocess.run(command, check=False, timeout=timeout, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"container failed: {result.stderr}")
    finally:
        server.close()
        thread.join(timeout=5)
        socket_path.unlink(missing_ok=True)
    assert not errors


def _source(path: Path, acquisition_id: str) -> OntRawSignalRepresentation:
    info = path.stat()
    root_info = path.parent.stat()
    return OntRawSignalRepresentation(
        id="source-1",
        run_id="run-1",
        observed_generation=1,
        role="source",
        source_kind="minknow_native",
        format="pod5",
        source_fidelity="native",
        state="preparable",
        reason_code="awaiting_validation",
        artifact_manifest={
            "artifacts": [
                {
                    "kind": "pod5",
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "mtime_ns": info.st_mtime_ns,
                    "ctime_ns": info.st_ctime_ns,
                    "governed_root_path": str(path.parent),
                    "governed_root_device": root_info.st_dev,
                    "governed_root_inode": root_info.st_ino,
                    "governed_relative_path": path.name,
                }
            ]
        },
        manifest_sha256="a" * 64,
        parent_representation_ids=[],
        parent_manifest_sha256s=[],
        compression={},
        runtime_identity={},
        validation_receipts={},
        acquisition_id=acquisition_id,
        profile_id="native",
        created_at=datetime.utcnow(),
    )


def test_public_raw_signal_representation_is_path_opaque(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"source")
    representation = _source(source_path, "acquisition")
    representation.parent_representation_ids = ["parent-representation"]
    representation.parent_manifest_sha256s = ["b" * 64]
    representation.runtime_identity = {"container_digest": "c" * 64}
    representation.validation_receipts = {"adjacent_index": True, "semantic": {"status": "passed"}}

    public = ont_raw_signal._public_representation(representation)

    assert public["artifacts"][0]["sha256"] == _sha256(source_path)
    assert "path" not in public["artifacts"][0]
    assert public["parent_manifest_sha256s"] == ["b" * 64]
    assert public["validation_receipts"]["semantic"]["status"] == "passed"


def _job(stage_root: Path) -> tuple[OntRawSignalDerivationJob, dict[str, Any]]:
    job = OntRawSignalDerivationJob(
        id="job-1",
        run_id="run-1",
        observed_generation=1,
        source_representation_id="source-1",
        requested_preference="auto",
        consumer_id="test",
        profile_id=ont_raw_signal.BLOW5_PROFILE_ID,
        state="admitted",
        reason_code="test",
        resource_snapshot={},
        attempt=1,
        claim_token="claim-1",
        lease_expires_at=datetime.utcnow() + timedelta(minutes=5),
        stage_receipts={},
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    snapshot = {
        "container_runtime": "docker",
        "container_image": "biomodstack/ont-raw-signal:test",
        "container_digest": "b" * 64,
        "worker_uid": os.getuid(),
        "worker_gid": os.getgid(),
        "staging_root": str(stage_root),
    }
    job.resource_snapshot = snapshot
    return job, snapshot


# Contract tests: 12

def test_contract_01_partitioned_profile_is_versioned() -> None:
    assert ont_raw_signal.BLOW5_PROFILE_ID == "bms.blow5.partitioned-zstd-svb-zd.v2"


def test_contract_02_source_paths_require_immutable_authority(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"pod5")
    source = _source(source_path, "acquisition")
    source.artifact_manifest["artifacts"][0].pop("sha256")
    with pytest.raises(ValueError, match="immutable size, digest, and filesystem identity authority"):
        ont_raw_signal._source_paths(source)


def test_contract_03_commands_bind_sealed_digest_and_partition_map(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"pod5")
    source = _source(source_path, "acquisition")
    job, snapshot = _job(tmp_path / "staging")
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)
    command = commands["source_preflight"]
    assert command[command.index("--expected-sha256") + 1] == _sha256(source_path)
    assert command[command.index("--expected-size") + 1] == str(source_path.stat().st_size)
    assert command[command.index("--expected-inode") + 1] == str(source_path.stat().st_ino)
    partition = commands["partition"]
    assert partition[partition.index("partition-pod5") - 1:partition.index("partition-pod5") + 1] == [
        "/opt/bms/ont_raw_signal_validate.py", "partition-pod5"
    ]
    assert partition[partition.index("--table") + 1] == "/stage/partition-map.csv"
    assert partition[partition.index("--output") + 1] == "/stage/partitions"
    assert partition[partition.index("--receipt") + 1] == "/stage/partition-receipt.json"


def test_contract_04_partition_authority_rejects_invalid_groups(tmp_path: Path) -> None:
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"status": "passed", "groups": [{"fingerprint": "bad", "read_count": 1}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid run-info partition"):
        ont_raw_signal.conversion_partition_groups({"source_receipt": str(receipt)})


def test_contract_05_unit_commands_are_lossless_and_indexed(tmp_path: Path) -> None:
    fingerprint = "a" * 64
    commands = {"common": ["runtime", "image"]}
    unit = ont_raw_signal.conversion_unit_commands(commands, fingerprint)
    assert unit["convert"][-12:] == [
        "blue-crab", "p2s", "-c", "zstd", "-s", "svb-zd", "--iop", "1", "--threads", "4", "--batchsize", "1000",
        f"/stage/partitions/{fingerprint}.pod5", "-o", f"/stage/outputs/{fingerprint}.blow5",
    ][-12:]
    assert unit["index_create"][-2:] == ["slow5tools", "index"][-2:] or unit["index_create"][-3:-1] == ["slow5tools", "index"]


def test_contract_06_semantic_command_covers_every_partition() -> None:
    groups = ["a" * 64, "b" * 64]
    command = ont_raw_signal.conversion_semantic_command(
        {"common": ["runtime", "image"], "validator_input_args": ["--pod5", "/input/source.pod5"]},
        groups,
    )
    for group in groups:
        assert f"/stage/outputs/{group}.blow5" in command
        assert f"/stage/outputs/{group}.blow5.idx" in command
    assert command[-4:] == ["--routing", "/stage/routing.json", "--receipt", "/stage/semantic-receipt.json"]


def test_contract_07_runtime_packages_both_executables() -> None:
    dockerfile = (ROOT / "docker" / "ont-raw-signal.Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts/ont_raw_signal_validate.py /opt/bms/ont_raw_signal_validate.py" in dockerfile
    assert "COPY scripts/ont_raw_signal_lookup.py /opt/bms/ont_raw_signal_lookup.py" in dockerfile
    assert "ont_raw_signal_validate.py --help" in dockerfile
    assert "ont_raw_signal_lookup.py --help" in dockerfile


def test_contract_08_terminal_registration_requests_automatic_conversion() -> None:
    source = inspect.getsource(ont_raw_signal.register_native_pod5_generation)
    assert 'consumer_id="ont-terminal-reconciliation"' in source
    assert 'preference="auto"' in source
    assert "automatic=True" in source


def test_live_conversion_defaults_to_dual_retention_and_is_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BMS_ONT_LIVE_CONVERSION_ENABLED", raising=False)
    monkeypatch.delenv("BMS_ONT_RAW_SIGNAL_RETENTION_POLICY", raising=False)

    assert ont_raw_signal.live_conversion_enabled() is True
    assert ont_raw_signal.raw_signal_retention_policy() == "pod5_and_blow5"


def test_blow5_only_retention_is_a_dormant_standalone_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BMS_ONT_RAW_SIGNAL_RETENTION_POLICY", "blow5_only")

    assert ont_raw_signal.raw_signal_retention_policy() == "blow5_only"
    assert ont_raw_signal.retention_disposition() == "future_delete_after_verified_blow5_not_active_in_integrated_bms"
    assert ont_raw_signal.retention_deletion_enabled() is False


def test_live_pod5_chunk_requires_two_identical_running_observations(tmp_path: Path) -> None:
    pod5 = tmp_path / "chunk.pod5"
    pod5.write_bytes(b"closed-pod5")
    first = ont_raw_signal.live_pod5_identity_snapshot([str(pod5)])
    second = ont_raw_signal.live_pod5_identity_snapshot([str(pod5)])

    assert ont_raw_signal.stable_live_pod5_paths(first, second) == [pod5]
    pod5.write_bytes(b"still-growing")
    changed = ont_raw_signal.live_pod5_identity_snapshot([str(pod5)])
    assert ont_raw_signal.stable_live_pod5_paths(second, changed) == []


def test_live_conversion_gate_allows_one_managed_job_during_active_acquisition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = {
        "qualified_conversion_enabled": True,
        "container_image": "biomodstack/ont-raw-signal",
        "container_digest": "a" * 64,
        "container_runtime": "docker",
        "disk_free_bytes": 10,
        "required_free_bytes": 10,
        "active_acquisition_pressure": "active",
        "conversion_mode": "live_minknow_pod5_chunk",
    }
    monkeypatch.setattr(ont_raw_signal.shutil, "which", lambda _runtime: "/usr/bin/docker")
    monkeypatch.setenv("BMS_ONT_LIVE_CONVERSION_ENABLED", "1")

    assert ont_raw_signal._qualification_gate(snapshot) is None
    monkeypatch.setenv("BMS_ONT_LIVE_CONVERSION_ENABLED", "0")
    assert ont_raw_signal._qualification_gate(snapshot) == "acquisition_pressure_not_proven_clear"


@pytest.mark.asyncio
async def test_live_chunk_registration_is_idempotent_and_queues_one_conversion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'live.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    pod5 = tmp_path / "chunk.pod5"
    pod5.write_bytes(b"closed-pod5")
    identities = ont_raw_signal.live_pod5_identity_snapshot([str(pod5)])
    observed_at = datetime.utcnow()
    monkeypatch.setattr(ont_raw_signal, "_qualification_gate", lambda _snapshot: None)
    monkeypatch.setattr(
        ont_raw_signal,
        "_derivation_resource_snapshot",
        lambda _run, _source: {"conversion_mode": "live_minknow_pod5_chunk"},
    )

    async with session_factory() as session:
        session.add(
            OntInstrumentRun(
                id="ont-run-live",
                position_id="position-1",
                minknow_run_id="minknow-live",
                state="running",
                observed_at=observed_at,
                observed_generation=2,
                output_directories={"reads": str(tmp_path)},
                output_files={"fastq": [], "pod5": [str(pod5)], "bam": []},
                handoff_ready=False,
                last_minknow_payload={"acquisition_id": "acquisition-live"},
            )
        )
        session.add(
            OntInstrumentRunEvent(
                id="ont-event-live",
                run_id="ont-run-live",
                event_type="active_observed",
                state="running",
                observed_at=observed_at,
                observed_generation=2,
                minknow_payload={"acquisition_id": "acquisition-live"},
                output_files={"fastq": [], "pod5": [str(pod5)], "bam": []},
            )
        )
        await session.commit()
        first = await ont_raw_signal.register_live_pod5_chunks(
            session,
            run_id="ont-run-live",
            observed_generation=2,
            stable_paths=[pod5],
            identity_snapshot=identities,
        )
        await session.commit()
        second = await ont_raw_signal.register_live_pod5_chunks(
            session,
            run_id="ont-run-live",
            observed_generation=2,
            stable_paths=[pod5],
            identity_snapshot=identities,
        )

        representations = (
            await session.execute(
                ont_raw_signal.select(OntRawSignalRepresentation).where(
                    OntRawSignalRepresentation.source_kind == "minknow_live"
                )
            )
        ).scalars().all()
        jobs = (await session.execute(ont_raw_signal.select(OntRawSignalDerivationJob))).scalars().all()

    await engine.dispose()
    assert len(first) == 1
    assert second == []
    assert len(representations) == 1
    assert len(jobs) == 1
    assert jobs[0].consumer_id == "ont-live-minknow-conversion"


@pytest.mark.asyncio
async def test_live_chunk_registration_reassesses_failed_derivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    pod5 = tmp_path / "chunk.pod5"
    pod5.write_bytes(b"closed-pod5")
    identities = ont_raw_signal.live_pod5_identity_snapshot([str(pod5)])
    observed_at = datetime.utcnow()
    monkeypatch.setattr(ont_raw_signal, "_qualification_gate", lambda _snapshot: None)
    monkeypatch.setattr(
        ont_raw_signal,
        "_derivation_resource_snapshot",
        lambda _run, _source: {"conversion_mode": "live_minknow_pod5_chunk"},
    )
    async with session_factory() as session:
        session.add_all([
            OntInstrumentRun(
                id="ont-run-live-retry", position_id="position-1", minknow_run_id="minknow-live",
                state="running", observed_at=observed_at, observed_generation=2,
                output_directories={"reads": str(tmp_path)},
                output_files={"fastq": [], "pod5": [str(pod5)], "bam": []},
                handoff_ready=False, last_minknow_payload={"acquisition_id": "acquisition-live"},
            ),
            OntInstrumentRunEvent(
                id="ont-event-live-retry", run_id="ont-run-live-retry", event_type="active_observed",
                state="running", observed_at=observed_at, observed_generation=2,
                minknow_payload={"acquisition_id": "acquisition-live"},
                output_files={"fastq": [], "pod5": [str(pod5)], "bam": []},
            ),
        ])
        await session.commit()
        await ont_raw_signal.register_live_pod5_chunks(
            session, run_id="ont-run-live-retry", observed_generation=2,
            stable_paths=[pod5], identity_snapshot=identities,
        )
        await session.commit()
        job = (await session.execute(ont_raw_signal.select(OntRawSignalDerivationJob))).scalar_one()
        job.state = "failed"
        job.reason_code = "lease_expired_partial_attempt_discarded"
        job.completed_at = datetime.utcnow()
        await session.commit()
        replay = await ont_raw_signal.register_live_pod5_chunks(
            session, run_id="ont-run-live-retry", observed_generation=2,
            stable_paths=[pod5], identity_snapshot=identities,
        )
        await session.commit()
        representations = (await session.execute(
            ont_raw_signal.select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.source_kind == "minknow_live"
            )
        )).scalars().all()
        await session.refresh(job)
    await engine.dispose()
    assert replay == []
    assert len(representations) == 1
    assert job.state == "requested"
    assert job.completed_at is None


@pytest.mark.asyncio
async def test_live_chunk_registration_rejects_intermediate_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved"
    reads = root / "reads"
    reads.mkdir(parents=True)
    pod5 = reads / "chunk.pod5"
    pod5.write_bytes(b"closed-pod5")
    identities = ont_raw_signal.live_pod5_identity_snapshot([str(pod5)])
    outside = tmp_path / "outside"
    reads.rename(outside)
    reads.symlink_to(outside, target_is_directory=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'escape.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    observed_at = datetime.utcnow()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all([
            OntInstrumentRun(
                id="ont-run-live-escape", position_id="position-1", minknow_run_id="minknow-live",
                state="running", observed_at=observed_at, observed_generation=2,
                output_directories={"reads": str(root)},
                output_files={"fastq": [], "pod5": [str(pod5)], "bam": []},
                handoff_ready=False, last_minknow_payload={"acquisition_id": "acquisition-live"},
            ),
            OntInstrumentRunEvent(
                id="ont-event-live-escape", run_id="ont-run-live-escape", event_type="active_observed",
                state="running", observed_at=observed_at, observed_generation=2,
                minknow_payload={"acquisition_id": "acquisition-live"}, output_files={},
            ),
        ])
        await session.commit()
        registered = await ont_raw_signal.register_live_pod5_chunks(
            session, run_id="ont-run-live-escape", observed_generation=2,
            stable_paths=[pod5], identity_snapshot=identities,
        )
        representations = (await session.execute(
            ont_raw_signal.select(OntRawSignalRepresentation).where(
                OntRawSignalRepresentation.source_kind == "minknow_live"
            )
        )).scalars().all()
    await engine.dispose()
    assert registered == []
    assert representations == []


@pytest.mark.asyncio
async def test_live_chunk_registration_waits_for_open_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pod5 = tmp_path / "chunk.pod5"
    pod5.write_bytes(b"quiescent-open-pod5")
    identities = ont_raw_signal.live_pod5_identity_snapshot([str(pod5)])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'writer.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    observed_at = datetime.utcnow()
    writer_fd = os.open(pod5, os.O_WRONLY)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add_all([
                OntInstrumentRun(
                    id="ont-run-live-writer", position_id="position-1", minknow_run_id="minknow-live",
                    state="running", observed_at=observed_at, observed_generation=2,
                    output_directories={"reads": str(tmp_path)},
                    output_files={"fastq": [], "pod5": [str(pod5)], "bam": []},
                    handoff_ready=False, last_minknow_payload={"acquisition_id": "acquisition-live"},
                ),
                OntInstrumentRunEvent(
                    id="ont-event-live-writer", run_id="ont-run-live-writer", event_type="active_observed",
                    state="running", observed_at=observed_at, observed_generation=2,
                    minknow_payload={"acquisition_id": "acquisition-live"}, output_files={},
                ),
            ])
            await session.commit()
            registered = await ont_raw_signal.register_live_pod5_chunks(
                session, run_id="ont-run-live-writer", observed_generation=2,
                stable_paths=[pod5], identity_snapshot=identities,
            )
            representations = (await session.execute(
                ont_raw_signal.select(OntRawSignalRepresentation).where(
                    OntRawSignalRepresentation.source_kind == "minknow_live"
                )
            )).scalars().all()
    finally:
        os.close(writer_fd)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        after_close = await ont_raw_signal.register_live_pod5_chunks(
            session, run_id="ont-run-live-writer", observed_generation=2,
            stable_paths=[pod5], identity_snapshot=identities,
        )
        await session.commit()
    await engine.dispose()
    assert registered == []
    assert representations == []
    assert len(after_close) == 1


@pytest.mark.asyncio
async def test_execute_cancellation_terminates_and_reaps_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()

    class Child:
        returncode: int | None = None
        terminated = False
        waited = False

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        async def wait(self):
            self.waited = True
            return self.returncode

    child = Child()

    async def fake_create(*_args, **_kwargs):
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    worker = OntRawSignalWorker(lambda: None)
    task = asyncio.create_task(worker._execute(["fake"], "job", "claim"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert child.terminated is True
    assert child.waited is True
    assert worker._child is None


@pytest.mark.asyncio
async def test_worker_monitors_active_minknow_runs_without_browser_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'monitor.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    observed_at = datetime.utcnow()
    async with session_factory() as session:
        session.add_all(
            [
                OntInstrumentRun(
                    id="ont-run-active",
                    position_id="position-1",
                    minknow_run_id="minknow-active",
                    state="running",
                    observed_at=observed_at,
                    observed_generation=1,
                    output_directories={},
                    output_files={},
                    handoff_ready=False,
                ),
                OntInstrumentRun(
                    id="ont-run-complete",
                    position_id="position-2",
                    minknow_run_id="minknow-complete",
                    state="completed",
                    observed_at=observed_at,
                    observed_generation=1,
                    output_directories={},
                    output_files={},
                    handoff_ready=True,
                ),
            ]
        )
        await session.commit()
    reconciled: list[str] = []

    async def fake_reconcile(run_id: str) -> dict[str, Any]:
        reconciled.append(run_id)
        return {"id": run_id}

    monkeypatch.setattr(ont_run_control, "reconcile_instrument_run", fake_reconcile)
    worker = OntRawSignalWorker(session_factory, poll_interval=5.0)

    assert await worker._reconcile_live_runs_once() == 1
    assert reconciled == ["ont-run-active"]
    await engine.dispose()


def test_native_terminal_artifact_is_sealed_for_descriptor_conversion(tmp_path: Path) -> None:
    source_path = tmp_path / "native.pod5"
    source_path.write_bytes(b"native-pod5")
    terminal = {
        "kind": "pod5",
        "path": str(source_path),
        "bytes": source_path.stat().st_size,
        "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
    }
    artifacts = ont_raw_signal._seal_native_pod5_artifacts([terminal])
    representation = OntRawSignalRepresentation(
        format="pod5",
        artifact_manifest={"artifacts": artifacts},
    )
    assert ont_raw_signal._source_paths(representation) == [source_path]
    assert all(
        isinstance(artifacts[0][field], int)
        for field in (
            "device", "inode", "mtime_ns", "ctime_ns",
            "governed_root_device", "governed_root_inode",
        )
    )


def test_contract_09_partitioned_waveform_uses_digest_bound_route(tmp_path: Path) -> None:
    final = tmp_path / "job"
    outputs = final / "outputs"
    outputs.mkdir(parents=True)
    fingerprint = "a" * 64
    blow5 = outputs / f"{fingerprint}.blow5"
    index = outputs / f"{fingerprint}.blow5.idx"
    blow5.write_bytes(b"blow5")
    index.write_bytes(b"index")
    routing = final / "routing.json"
    routing.write_text(json.dumps({"groups": {fingerprint: {"blow5": blow5.name, "index": index.name}}, "read_to_group": {"read-1": fingerprint}}), encoding="utf-8")
    representation = OntRawSignalRepresentation(
        format="blow5", state="ready", validation_receipts={"adjacent_index": True},
        artifact_manifest={"artifacts": [
            {"kind": "blow5", "path": str(blow5)},
            {"kind": "blow5", "path": str(outputs / ("b" * 64 + ".blow5"))},
            {"kind": "blow5_index", "path": str(index)},
            {"kind": "blow5_index", "path": str(outputs / ("b" * 64 + ".blow5.idx"))},
            {"kind": "read_routing", "path": str(routing)},
        ]},
    )
    assert ont_raw_signal._validated_blow5_paths(representation, "read-1") == (blow5, index)


class _Session:
    def __init__(self, job: OntRawSignalDerivationJob):
        self.job = job
        self.added: list[Any] = []
        self.commits = 0

    async def get(self, _model, _identifier):
        return self.job

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_contract_10_cancellation_blocks_further_progress(tmp_path: Path) -> None:
    job, _snapshot = _job(tmp_path)
    job.cancel_requested_at = datetime.utcnow()
    session = _Session(job)
    with pytest.raises(ValueError, match="cancellation requested"):
        await ont_raw_signal.transition_derivation(session, job.id, str(job.claim_token), "converting", "started", {})


@pytest.mark.asyncio
async def test_contract_11_cancelled_terminal_transition_clears_lease(tmp_path: Path) -> None:
    job, _snapshot = _job(tmp_path)
    job.cancel_requested_at = datetime.utcnow()
    session = _Session(job)
    await ont_raw_signal.transition_derivation(session, job.id, str(job.claim_token), "cancelled", "cancelled_child_terminated", {})
    assert job.state == "cancelled"
    assert job.claim_token is None
    assert job.lease_expires_at is None
    assert job.completed_at is not None


@pytest.mark.asyncio
async def test_contract_12_publication_emits_one_pair_per_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staging_root = tmp_path / "staging"
    stage = staging_root / "job-1" / "attempt-1"
    outputs = stage / "outputs"
    outputs.mkdir(parents=True)
    groups = {"a" * 64: 1, "b" * 64: 2}
    routing_payload = {
        "schema": "bms.ont.raw-signal-routing.v1",
        "groups": {group: {"blow5": f"{group}.blow5", "index": f"{group}.blow5.idx", "read_count": count} for group, count in groups.items()},
        "read_to_group": {"read-a": "a" * 64, "read-b": "b" * 64},
    }
    routing = stage / "routing.json"
    routing.write_text(json.dumps(routing_payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    for group in groups:
        (outputs / f"{group}.blow5").write_bytes(group.encode())
        (outputs / f"{group}.blow5.idx").write_bytes(b"index")
    semantic = {
        "status": "passed", "duplicate_read_ids": 0, "partition_counts": groups,
        "read_count": 3, "routing_sha256": _sha256(routing),
    }
    (stage / "semantic-receipt.json").write_text(json.dumps(semantic), encoding="utf-8")
    source_file = tmp_path / "source.pod5"
    source_file.write_bytes(b"source")
    source = _source(source_file, "acquisition")
    job, snapshot = _job(staging_root)
    commands = {"stage": str(stage), "outputs": str(outputs), "routing": str(routing)}
    monkeypatch.setenv(ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(staging_root))
    session = _Session(job)
    representation = await ont_raw_signal.publish_derivation(session, job, source, commands)
    kinds = [artifact["kind"] for artifact in representation.artifact_manifest["artifacts"]]
    assert kinds.count("blow5") == 2
    assert kinds.count("blow5_index") == 2
    assert kinds.count("read_routing") == 1
    assert representation.read_count == 3
    assert not stage.exists()


# Validator fixture tests: 2

def test_validator_fixture_01_preflight_partitions_complete_run_info(tmp_path: Path) -> None:
    validator = _load_validator()
    source = tmp_path / "mixed.pod5"
    acquisition_id, _reads = _write_mixed_pod5(source)
    partition_map = tmp_path / "partition-map.csv"
    source_info = source.stat()
    root_info = source.parent.stat()
    args = argparse.Namespace(
        pod5=[source], expected_sha256=[_sha256(source)], expected_size=[source_info.st_size],
        governed_root=[source.parent], expected_root_device=[root_info.st_dev], expected_root_inode=[root_info.st_ino],
        expected_device=[source_info.st_dev], expected_inode=[source_info.st_ino],
        expected_mtime_ns=[source_info.st_mtime_ns], expected_ctime_ns=[source_info.st_ctime_ns],
        expected_acquisition_id=acquisition_id, partition_map=partition_map,
    )
    receipt = validator.source_preflight(args)
    assert receipt["status"] == "passed"
    assert receipt["read_count"] == 2
    assert len(receipt["groups"]) == 2
    assert partition_map.read_text(encoding="utf-8").count("\n") == 3


def _slow5_record(read: Any) -> dict[str, Any]:
    digitisation = 8192.0
    return {
        "read_id": str(read.read_id), "signal": np.asarray(read.signal, dtype=np.int16),
        "len_raw_signal": len(read.signal), "digitisation": digitisation,
        "offset": float(read.calibration.offset), "range": float(read.calibration.scale) * digitisation,
        "sampling_rate": int(read.run_info.sample_rate), "start_time": int(read.start_sample),
        "read_number": int(read.read_number), "channel_number": int(read.pore.channel),
        "start_mux": int(read.pore.well), "median_before": float(read.median_before),
        "num_minknow_events": int(read.num_minknow_events),
        "tracked_scaling_shift": float(read.tracked_scaling.shift),
        "tracked_scaling_scale": float(read.tracked_scaling.scale),
        "predicted_scaling_shift": float(read.predicted_scaling.shift),
        "predicted_scaling_scale": float(read.predicted_scaling.scale),
        "num_reads_since_mux_change": int(read.num_reads_since_mux_change),
        "time_since_mux_change": float(read.time_since_mux_change),
        "open_pore_level": float(read.open_pore_level),
    }


class _FakeSlow5:
    def __init__(self, records: list[dict[str, Any]]):
        self.records = records

    def seq_reads(self, **_kwargs):
        yield from self.records

    def get_read(self, read_id: str, **_kwargs):
        return next((record for record in self.records if record["read_id"] == read_id), None)

    def close(self):
        return None


def test_validator_fixture_02_rejects_wrong_run_info_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    validator = _load_validator()
    source = tmp_path / "mixed.pod5"
    _acquisition_id, reads = _write_mixed_pod5(source)
    fingerprints = [validator._run_info_fingerprint(read.run_info) for read in reads]
    blow5 = tmp_path / f"{fingerprints[1]}.blow5"
    index = tmp_path / f"{fingerprints[1]}.blow5.idx"
    blow5.write_bytes(b"fixture")
    index.write_bytes(b"index")
    monkeypatch.setattr(validator, "_slow5_open", lambda _path: _FakeSlow5([_slow5_record(reads[0])]))
    source_info = source.stat()
    root_info = source.parent.stat()
    args = argparse.Namespace(
        pod5=[source], expected_sha256=[_sha256(source)], expected_size=[source_info.st_size],
        governed_root=[source.parent], expected_root_device=[root_info.st_dev], expected_root_inode=[root_info.st_ino],
        expected_device=[source_info.st_dev], expected_inode=[source_info.st_ino],
        expected_mtime_ns=[source_info.st_mtime_ns], expected_ctime_ns=[source_info.st_ctime_ns],
        blow5=[blow5], index=[index], routing=tmp_path / "routing.json",
    )
    with pytest.raises(ValueError, match="wrong complete-run-info partition"):
        validator.semantic_validate(args)


# Mixed-run end-to-end conversion: 1

@pytest.mark.runtime_integration
def test_mixed_run_end_to_end_conversion(tmp_path: Path) -> None:
    source_path = tmp_path / "mixed.pod5"
    acquisition_id, _reads = _write_mixed_pod5(source_path)
    image_tag = "biomodstack/ont-raw-signal:focused-test"
    subprocess.run(
        ["docker", "build", "--network=host", "-f", str(ROOT / "docker" / "ont-raw-signal.Dockerfile"), "-t", image_tag, str(ROOT)],
        check=True, cwd=ROOT, timeout=600,
    )
    image_id = subprocess.run(
        ["docker", "image", "inspect", image_tag, "--format", "{{.Id}}"],
        check=True, text=True, capture_output=True, timeout=30,
    ).stdout.strip()
    assert image_id.startswith("sha256:")
    job, snapshot = _job(tmp_path / "staging")
    snapshot["container_image"] = image_id
    snapshot["container_digest"] = image_id.removeprefix("sha256:")
    source = _source(source_path, acquisition_id)
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)
    pinned_fds = ont_raw_signal.pin_conversion_source_descriptors(commands)
    selected_inode = os.fstat(pinned_fds[1]).st_ino
    replacement = tmp_path / "replacement.pod5"
    replacement.write_bytes(b"path-replaced-after-pin")
    replacement.replace(source_path)
    assert source_path.stat().st_ino != selected_inode
    try:
        Path(commands["stage"]).mkdir(parents=True, mode=0o700)
        Path(commands["partitions"]).mkdir(mode=0o700)
        Path(commands["outputs"]).mkdir(mode=0o700)
        _run_with_source_fds(commands["source_preflight"], pinned_fds, Path(commands["fd_socket"]), timeout=120)
        groups = ont_raw_signal.conversion_partition_groups(commands)
        assert len(groups) == 2
        _run_with_source_fds(commands["partition"], pinned_fds, Path(commands["fd_socket"]), timeout=120)
        for group in groups:
            unit = ont_raw_signal.conversion_unit_commands(commands, group)
            subprocess.run(unit["convert"], check=True, timeout=120)
            subprocess.run(unit["quickcheck"], check=True, timeout=30)
            subprocess.run(unit["index_create"], check=True, timeout=30)
        _run_with_source_fds(
            ont_raw_signal.conversion_semantic_command(commands, groups),
            pinned_fds,
            Path(commands["fd_socket"]),
            timeout=180,
        )
    finally:
        for fd in pinned_fds:
            os.close(fd)
    semantic = json.loads((Path(commands["stage"]) / "semantic-receipt.json").read_text(encoding="utf-8"))
    assert semantic["status"] == "passed"
    assert semantic["read_count"] == 2
    assert semantic["partition_count"] == 2
    assert semantic["indexed_lookup_count"] == 2
    assert semantic["total_signal_samples_compared"] > 0


def test_existing_pod5_candidates_are_server_rooted_and_path_opaque(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "existing-pod5"
    source_root.mkdir()
    source = source_root / "BFX6NB_4.pod5"
    source.write_bytes(b"sealed-pod5")
    (source_root / "ignore.fastq").write_bytes(b"reads")
    monkeypatch.setenv(ont_raw_signal.EXTERNAL_POD5_ROOT_ENV, str(source_root))

    candidates = ont_raw_signal.list_external_pod5_candidates()

    source_info = source.stat()
    assert candidates == [{
        "candidate_id": ont_raw_signal._candidate_identity("BFX6NB_4.pod5", source_info),
        "display_name": "BFX6NB_4.pod5",
        "size_bytes": len(b"sealed-pod5"),
        "modified_at_ns": source_info.st_mtime_ns,
    }]
    assert str(source_root) not in json.dumps(candidates)


def test_existing_pod5_candidates_exclude_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "existing-pod5"
    source_root.mkdir()
    outside = tmp_path / "outside.pod5"
    outside.write_bytes(b"outside")
    (source_root / "escaped.pod5").symlink_to(outside)
    monkeypatch.setenv(ont_raw_signal.EXTERNAL_POD5_ROOT_ENV, str(source_root))

    assert ont_raw_signal.list_external_pod5_candidates() == []
    with pytest.raises(KeyError):
        ont_raw_signal.resolve_external_pod5_candidate(hashlib.sha256(b"escaped.pod5").hexdigest())


def test_existing_pod5_root_rejects_lexical_parent_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    governed = tmp_path / "governed"
    governed.mkdir()
    monkeypatch.setenv(
        ont_raw_signal.EXTERNAL_POD5_ROOT_ENV,
        str(governed / ".." / "governed"),
    )

    with pytest.raises(RuntimeError, match="parent traversal"):
        ont_raw_signal.list_external_pod5_candidates()


def test_conversion_pins_selected_inode_across_path_replacement(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"sealed-pod5")
    source = _source(source_path, "acquisition")
    original_inode = source_path.stat().st_ino
    job, snapshot = _job(tmp_path / "staging")
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)

    replacement = tmp_path / "replacement.pod5"
    replacement.write_bytes(b"sealed-pod5")
    replacement.replace(source_path)

    assert source_path.stat().st_ino != original_inode
    with pytest.raises(ValueError, match="filesystem identity changed"):
        ont_raw_signal.pin_conversion_source_descriptors(commands)


def test_conversion_rejects_governed_root_replaced_by_symlink(tmp_path: Path) -> None:
    root = tmp_path / "governed"
    root.mkdir()
    source_path = root / "source.pod5"
    source_path.write_bytes(b"sealed-pod5")
    source = _source(source_path, "acquisition")
    job, snapshot = _job(tmp_path / "stage")
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)

    moved = tmp_path / "moved"
    root.rename(moved)
    root.symlink_to(moved, target_is_directory=True)

    with pytest.raises(OSError):
        ont_raw_signal.pin_conversion_source_descriptors(commands)


def test_existing_pod5_candidate_token_is_bound_to_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "existing-pod5"
    source_root.mkdir()
    source = source_root / "BFX6NB_4.pod5"
    source.write_bytes(b"first")
    monkeypatch.setenv(ont_raw_signal.EXTERNAL_POD5_ROOT_ENV, str(source_root))
    stale_token = ont_raw_signal.list_external_pod5_candidates()[0]["candidate_id"]

    source.write_bytes(b"replacement-with-different-identity")

    assert ont_raw_signal.list_external_pod5_candidates()[0]["candidate_id"] != stale_token
    with pytest.raises(KeyError):
        ont_raw_signal.resolve_external_pod5_candidate(stale_token)


def test_existing_pod5_root_rejects_symlinked_ancestors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real_root, target_is_directory=True)
    monkeypatch.setenv(ont_raw_signal.EXTERNAL_POD5_ROOT_ENV, str(alias))

    with pytest.raises(RuntimeError, match="symbolic links"):
        ont_raw_signal.list_external_pod5_candidates()


def test_external_registration_trigger_seals_generation_and_source_identity(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    connection = sqlite3.connect(database)
    connection.execute(
        """CREATE TABLE ont_instrument_runs (
            id TEXT PRIMARY KEY,
            external_registration_key TEXT,
            experiment_group TEXT,
            sample_id TEXT,
            last_minknow_payload TEXT,
            observed_generation INTEGER
        )"""
    )
    connection.execute(
        "INSERT INTO ont_instrument_runs VALUES ('run', 'key', 'experiment', NULL, '{}', 1)"
    )
    connection.execute(
        "INSERT INTO ont_instrument_runs VALUES ('unkeyed', NULL, 'experiment', NULL, '{}', 1)"
    )
    connection.commit()
    connection.close()

    seal_external_source_identity(str(database))
    connection = sqlite3.connect(database)
    connection.execute(
        """UPDATE ont_instrument_runs SET
            external_source_device=1,
            external_source_inode=2,
            external_source_bytes=3,
            external_source_mtime_ns=4,
            external_source_ctime_ns=5,
            external_source_root_device=6,
            external_source_root_inode=7,
            external_source_relative_path='source.pod5'
        WHERE id IN ('run', 'unkeyed')"""
    )
    connection.commit()
    for statement in (
        "UPDATE ont_instrument_runs SET observed_generation=2 WHERE id='run'",
        "UPDATE ont_instrument_runs SET observed_generation=2 WHERE id='unkeyed'",
        "UPDATE ont_instrument_runs SET external_source_inode=9 WHERE id='run'",
        "UPDATE ont_instrument_runs SET external_source_inode=9 WHERE id='unkeyed'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(statement)
            connection.commit()
        connection.rollback()
    for run_id in ("run", "unkeyed"):
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM ont_instrument_runs WHERE id=?", (run_id,))
            connection.commit()
        connection.rollback()
    connection.close()


def test_source_read_lease_blocks_in_place_writer_until_cleanup(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"sealed")
    source = _source(source_path, "acquisition")
    job, snapshot = _job(tmp_path)
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)
    pinned = ont_raw_signal.pin_conversion_source_descriptors(commands)
    writer = subprocess.Popen(
        [sys.executable, "-c", "from pathlib import Path; Path(__import__('sys').argv[1]).write_bytes(b'mutated')", str(source_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 2.0
        while not ont_raw_signal.source_lease_break_requested() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ont_raw_signal.source_lease_break_requested()
        assert writer.poll() is None
        assert source_path.read_bytes() == b"sealed"
    finally:
        for descriptor in pinned:
            os.close(descriptor)
    assert writer.wait(timeout=5) == 0
    assert source_path.read_bytes() == b"mutated"


@pytest.mark.asyncio
async def test_worker_transfers_exact_source_descriptor_and_cleans_socket(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"selected-inode")
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    source_fd = os.open(source_path, os.O_RDONLY)
    socket_path = tmp_path / "source-fd.sock"
    script = """
import array, os, socket, sys
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(sys.argv[1])
_, ancillary, _, _ = connection.recvmsg(1, socket.CMSG_SPACE(2 * array.array('i').itemsize))
received = array.array('i')
for level, kind, data in ancillary:
    if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
        received.frombytes(data[:len(data) - (len(data) % received.itemsize)])
assert len(received) == 2
assert os.read(received[1], 64) == b'selected-inode'
for descriptor in received:
    os.close(descriptor)
"""
    worker = OntRawSignalWorker(None)
    ont_raw_signal._SOURCE_LEASE_BREAK.clear()
    try:
        receipt = await worker._execute(
            [sys.executable, "-c", script, str(socket_path)],
            "job",
            "claim",
            source_fds=[root_fd, source_fd],
            fd_socket=str(socket_path),
        )
        assert receipt["returncode"] == 0
        assert not socket_path.exists()
    finally:
        os.close(root_fd)
        os.close(source_fd)


@pytest.mark.asyncio
async def test_registration_holds_source_descriptor_through_outer_transaction(tmp_path: Path) -> None:
    source = tmp_path / "source.pod5"
    source.write_bytes(b"source")
    fd = os.open(source, os.O_RDONLY)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        async with session.begin():
            ont_raw_signal._hold_source_descriptors_through_transaction(session, [fd])
            async with session.begin_nested():
                os.fstat(fd)
            os.fstat(fd)
        with pytest.raises(OSError):
            os.fstat(fd)
    await engine.dispose()
