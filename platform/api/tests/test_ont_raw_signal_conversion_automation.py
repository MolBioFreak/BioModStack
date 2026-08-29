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
    OntRawSignalLookup,
    OntRawSignalRepresentation,
)
from migrations.seal_ont_external_source_identity import migrate as seal_external_source_identity
from services import ont_raw_signal, ont_raw_signal_worker, ont_run_control
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


def test_semantic_output_receipt_binds_each_output_identity() -> None:
    expected = {
        "fp": {
            "blow5": {"sha256": "a" * 64, "bytes": 10, "device": 1, "inode": 2, "mtime_ns": 3, "ctime_ns": 4},
            "index": {"sha256": "b" * 64, "bytes": 11, "device": 1, "inode": 5, "mtime_ns": 6, "ctime_ns": 7},
        }
    }
    ont_raw_signal._assert_semantic_output_identity(
        expected,
        "fp",
        "blow5",
        expected["fp"]["blow5"],
    )
    with pytest.raises(ValueError, match="semantic output identity"):
        ont_raw_signal._assert_semantic_output_identity(
            expected,
            "fp",
            "blow5",
            {**expected["fp"]["blow5"], "inode": 99},
        )


def test_confined_stage_leaf_rejects_symlinked_intermediate(tmp_path: Path) -> None:
    root = tmp_path / "stage-root"
    target = tmp_path / "outside"
    root.mkdir()
    target.mkdir()
    (root / "waveforms").symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        ont_raw_signal._prepare_confined_directory(root, ("waveforms", "lookup"))


def test_waveform_output_writer_rejects_symlink(tmp_path: Path) -> None:
    lookup = importlib.util.spec_from_file_location(
        "ont_raw_signal_lookup_test", ROOT / "scripts" / "ont_raw_signal_lookup.py"
    )
    assert lookup and lookup.loader
    module = importlib.util.module_from_spec(lookup)
    lookup.loader.exec_module(module)
    output = tmp_path / "waveform.json"
    target = tmp_path / "outside.json"
    target.write_text("outside", encoding="utf-8")
    output.symlink_to(target)
    with pytest.raises(OSError):
        module._write_output_no_follow(output, {"schema": "bms.ont.raw-waveform.v1"})


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


def _load_lookup():
    path = ROOT / "scripts" / "ont_raw_signal_lookup.py"
    spec = importlib.util.spec_from_file_location("ont_raw_signal_lookup_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lookup_runtime_binds_blow5_and_index_identity_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blow5 = tmp_path / "reads.blow5"
    index = tmp_path / "reads.blow5.idx"
    output = tmp_path / "waveform.json"
    blow5.write_bytes(b"blow5-bytes")
    index.write_bytes(b"index-bytes")

    def expected(path: Path) -> dict[str, int | str]:
        stat_result = path.stat()
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": stat_result.st_size,
            "device": stat_result.st_dev,
            "inode": stat_result.st_ino,
            "mtime_ns": stat_result.st_mtime_ns,
            "ctime_ns": stat_result.st_ctime_ns,
        }

    blow5_expected = expected(blow5)
    index_expected = expected(index)
    root_info = tmp_path.stat()
    argv = [
        "ont_raw_signal_lookup.py",
        "--blow5", "/proc/self/fd/unbound-waveform-blow5",
        "--index", "/proc/self/fd/unbound-waveform-index",
        "--fd-socket", "/output/source-fd.sock",
        "--read-id", "read-1",
        "--max-samples", "20",
        "--output", str(output),
    ]
    for prefix, values in (("blow5", blow5_expected), ("index", index_expected)):
        for field, option in (
            ("sha256", "sha256"),
            ("bytes", "size"),
            ("device", "device"),
            ("inode", "inode"),
            ("mtime_ns", "mtime-ns"),
            ("ctime_ns", "ctime-ns"),
        ):
            argv.extend((f"--expected-{prefix}-{option}", str(values[field])))
        argv.extend(
            (
                f"--expected-{prefix}-root-device", str(root_info.st_dev),
                f"--expected-{prefix}-root-inode", str(root_info.st_ino),
            )
        )

    opened: list[str] = []

    class FakeSlow5:
        def get_read(self, _read_id: str, *, pA: bool) -> dict[str, list[int]]:
            assert pA is True
            return {"signal": [1, 2, 3, 4]}

        def close(self) -> None:
            return None

    def open_slow5(path: str, *_args: object) -> FakeSlow5:
        opened.append(path)
        return FakeSlow5()

    source_fds = [
        os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY),
        os.open(blow5, os.O_RDONLY),
        os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY),
        os.open(index, os.O_RDONLY),
    ]
    monkeypatch.setitem(sys.modules, "pyslow5", SimpleNamespace(Open=open_slow5))
    lookup = _load_lookup()
    monkeypatch.setattr(
        lookup, "_receive_source_descriptors", lambda _socket: [os.dup(fd) for fd in source_fds]
    )
    try:
        monkeypatch.setattr(sys, "argv", argv)
        assert lookup.main() == 0
        assert opened and opened[0] != str(blow5)
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["source_identity"] == {"blow5": blow5_expected, "index": index_expected}

        bad_argv = list(argv)
        bad_argv[bad_argv.index("--expected-blow5-sha256") + 1] = "0" * 64
        monkeypatch.setattr(sys, "argv", bad_argv)
        with pytest.raises(ValueError, match="BLOW5 identity diverged"):
            lookup.main()
    finally:
        for descriptor in source_fds:
            os.close(descriptor)


def test_lookup_uses_the_descriptor_snapshot_without_path_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blow5 = tmp_path / "reads.blow5"
    index = tmp_path / "reads.blow5.idx"
    output = tmp_path / "waveform.json"
    blow5.write_bytes(b"blow5-bytes")
    index.write_bytes(b"index-bytes")

    def expected(path: Path) -> dict[str, int | str]:
        info = path.stat()
        return {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": info.st_size,
            "device": info.st_dev,
            "inode": info.st_ino,
            "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
        }

    blow5_expected = expected(blow5)
    index_expected = expected(index)
    root_info = tmp_path.stat()
    argv = [
        "ont_raw_signal_lookup.py",
        "--blow5", "/proc/self/fd/unbound-waveform-blow5",
        "--index", "/proc/self/fd/unbound-waveform-index",
        "--fd-socket", "/output/source-fd.sock",
        "--read-id", "read-1", "--max-samples", "20", "--output", str(output),
    ]
    for prefix, values in (("blow5", blow5_expected), ("index", index_expected)):
        for field, option in (
            ("sha256", "sha256"), ("bytes", "size"), ("device", "device"),
            ("inode", "inode"), ("mtime_ns", "mtime-ns"), ("ctime_ns", "ctime-ns"),
        ):
            argv.extend((f"--expected-{prefix}-{option}", str(values[field])))
        argv.extend(
            (
                f"--expected-{prefix}-root-device", str(root_info.st_dev),
                f"--expected-{prefix}-root-inode", str(root_info.st_ino),
            )
        )

    class FakeSlow5:
        def get_read(self, _read_id: str, *, pA: bool) -> dict[str, list[int]]:
            assert pA is True
            return {"signal": [1]}

        def close(self) -> None:
            return None

    source_fds = [
        os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY),
        os.open(blow5, os.O_RDONLY),
        os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY),
        os.open(index, os.O_RDONLY),
    ]
    blow5.unlink()
    index.unlink()
    monkeypatch.setitem(sys.modules, "pyslow5", SimpleNamespace(Open=lambda _path, _mode: FakeSlow5()))
    lookup = _load_lookup()
    monkeypatch.setattr(
        lookup, "_receive_source_descriptors", lambda _socket: [os.dup(fd) for fd in source_fds]
    )
    monkeypatch.setattr(sys, "argv", argv)

    try:
        assert lookup.main() == 0
        assert json.loads(output.read_text(encoding="utf-8"))["returned_sample_count"] == 1
    finally:
        for descriptor in source_fds:
            os.close(descriptor)


def test_external_validator_opens_an_attested_snapshot_and_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _load_validator()
    blow5 = tmp_path / "reads.blow5"
    index = tmp_path / "reads.blow5.idx"
    blow5.write_bytes(b"blow5-bytes")
    index.write_bytes(b"index-bytes")

    def expected(path: Path) -> dict[str, int | str]:
        info = path.stat()
        return {
            "sha256": _sha256(path), "size": info.st_size, "device": info.st_dev,
            "inode": info.st_ino, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "root_device": path.parent.stat().st_dev, "root_inode": path.parent.stat().st_ino,
        }

    args = argparse.Namespace(blow5=blow5, index=index)
    for prefix, path in (("blow5", blow5), ("index", index)):
        values = expected(path)
        for field in ("sha256", "device", "inode", "mtime_ns", "ctime_ns", "root_device", "root_inode"):
            setattr(args, f"expected_{prefix}_{field}", values[field])
        setattr(args, f"expected_{prefix}_size", values["size"])

    opened: list[Path] = []

    class FakeSlow5:
        def seq_reads(self, **_kwargs: object):
            yield {
                "read_id": "read-1", "signal": [1], "len_raw_signal": 1,
                "digitisation": 1, "offset": 0, "range": 1, "sampling_rate": 1,
            }

        def get_read(self, _read_id: str, **_kwargs: object) -> dict[str, object]:
            return {"read_id": "read-1"}

        def close(self) -> None:
            return None

    def open_slow5(path: Path, *_args: object, **_kwargs: object) -> FakeSlow5:
        opened.append(Path(path))
        return FakeSlow5()

    monkeypatch.setitem(sys.modules, "pyslow5", SimpleNamespace(Open=open_slow5))
    payload = validator.external_blow5_validate(args)

    assert payload["status"] == "passed"
    assert opened and opened[0] != blow5
    assert not opened[0].exists()


def test_external_validator_consumes_received_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validator = _load_validator()
    blow5 = tmp_path / "reads.blow5"
    index = tmp_path / "reads.blow5.idx"
    blow5.write_bytes(b"blow5-bytes")
    index.write_bytes(b"index-bytes")
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    blow5_fd = os.open(blow5, os.O_RDONLY)
    index_fd = os.open(index, os.O_RDONLY)

    def expected(path: Path) -> dict[str, int | str]:
        info = path.stat()
        return {
            "sha256": _sha256(path), "size": info.st_size, "device": info.st_dev,
            "inode": info.st_ino, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "root_device": path.parent.stat().st_dev, "root_inode": path.parent.stat().st_ino,
        }

    args = argparse.Namespace(
        blow5=Path(f"/proc/self/fd/{blow5_fd}"),
        index=Path(f"/proc/self/fd/{index_fd}"),
        external_file_fds=[blow5_fd, index_fd],
        external_root_fds=[root_fd, root_fd],
        receipt=tmp_path / "receipt.json",
    )
    for prefix, path in (("blow5", blow5), ("index", index)):
        values = expected(path)
        for field in ("sha256", "device", "inode", "mtime_ns", "ctime_ns", "root_device", "root_inode"):
            setattr(args, f"expected_{prefix}_{field}", values[field])
        setattr(args, f"expected_{prefix}_size", values["size"])

    class FakeSlow5:
        def seq_reads(self, **_kwargs: object):
            yield {
                "read_id": "read-1", "signal": [1], "len_raw_signal": 1,
                "digitisation": 1, "offset": 0, "range": 1, "sampling_rate": 1,
            }

        def get_read(self, _read_id: str, **_kwargs: object) -> dict[str, object]:
            return {"read_id": "read-1"}

        def close(self) -> None:
            return None

    opened: list[str] = []

    def open_slow5(path: str, *_args: object, **_kwargs: object) -> FakeSlow5:
        opened.append(path)
        return FakeSlow5()

    monkeypatch.setitem(sys.modules, "pyslow5", SimpleNamespace(Open=open_slow5))
    try:
        payload = validator.external_blow5_validate(args)
    finally:
        os.close(blow5_fd)
        os.close(index_fd)
        os.close(root_fd)

    assert payload["status"] == "passed"
    assert opened and Path(opened[0]).name == "source.blow5"


def test_external_validation_uses_descriptor_socket_without_input_path_mount(tmp_path: Path) -> None:
    blow5 = tmp_path / "reads.blow5"
    index = tmp_path / "reads.blow5.idx"
    blow5.write_bytes(b"blow5-bytes")
    index.write_bytes(b"index-bytes")
    manifest = {
        "artifacts": [
            ont_raw_signal._file_artifact(blow5, "blow5", kind="blow5"),
            ont_raw_signal._file_artifact(index, "index", kind="blow5_index"),
        ]
    }
    source = OntRawSignalRepresentation(
        id="external-source",
        source_kind="external_native",
        format="blow5",
        artifact_manifest=manifest,
    )
    job, snapshot = _job(tmp_path / "staging")
    job.profile_id = ont_raw_signal.EXTERNAL_BLOW5_VALIDATION_PROFILE_ID

    commands = ont_raw_signal._external_blow5_validation_commands(job, source, snapshot)

    assert len(commands["source_authorities"]) == 2
    assert commands["source_fd_count"] == 4
    for command_name in ("quickcheck", "semantic_validate"):
        command = commands[command_name]
        assert "--fd-socket" in command
        assert "/proc/self/fd/unbound-external-blow5" in command
        assert "/proc/self/fd/unbound-external-index" in command
        assert not any("dst=/input" in argument for argument in command)


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
async def test_waveform_runtime_timeout_terminates_and_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    class Child:
        returncode: int | None = None
        terminated = False
        killed = False
        waited = False

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.waited = True
            if not self.killed:
                await asyncio.Event().wait()
            return self.returncode

    child = Child()

    async def fake_create(*_args, **_kwargs):
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(
        OntRawSignalWorker,
        "_assert_waveform_command_policy",
        staticmethod(lambda _command: None),
    )
    monkeypatch.setattr(
        ont_raw_signal_worker,
        "RAW_SIGNAL_WAVEFORM_RUNTIME_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )
    monkeypatch.setattr(
        ont_raw_signal_worker,
        "RAW_SIGNAL_CHILD_TERMINATE_GRACE_SECONDS",
        0.01,
        raising=False,
    )
    worker = OntRawSignalWorker(lambda: None)

    with pytest.raises(RuntimeError, match="waveform runtime exceeded"):
        await asyncio.wait_for(
            worker._execute(["fake"], "lookup-timeout", "claim-timeout", waveform=True),
            timeout=0.5,
        )

    assert started.is_set()
    assert child.terminated is True
    assert child.killed is True
    assert child.waited is True
    assert worker._child is None


@pytest.mark.asyncio
async def test_transient_source_lease_failure_is_deferred_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class SessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class Job:
        id = "job-transient-lease"
        claim_token = "claim-transient-lease"
        profile_id = ont_raw_signal.BLOW5_PROFILE_ID

    deferred: list[tuple[str, str, str, dict[str, Any]]] = []
    job = Job()
    source = object()
    stage = tmp_path / "stage"

    async def fake_defer(_session: object, job_id: str, claim_token: str, reason_code: str, receipt: dict[str, Any]) -> None:
        deferred.append((job_id, claim_token, reason_code, receipt))

    async def fake_claim(_session: object) -> tuple[Job, object, dict[str, Any]]:
        return job, source, {"stage": str(stage)}

    async def fake_cancel(_session: object, _job_id: str, _claim_token: str) -> bool:
        return False

    class TransientLeaseError(RuntimeError):
        pass

    async def no_waveform(_session: object) -> None:
        return None

    async def no_recovery(_session: object) -> None:
        return None

    monkeypatch.setattr(ont_raw_signal_worker, "SourceLeaseUnavailable", TransientLeaseError, raising=False)
    monkeypatch.setattr(ont_raw_signal_worker, "recover_expired_derivations", no_recovery)
    monkeypatch.setattr(ont_raw_signal_worker, "claim_next_waveform_lookup", no_waveform)
    monkeypatch.setattr(ont_raw_signal_worker, "claim_next_derivation", fake_claim)
    monkeypatch.setattr(ont_raw_signal_worker, "derivation_cancellation_requested", fake_cancel)
    monkeypatch.setattr(ont_raw_signal_worker, "defer_derivation", fake_defer, raising=False)
    monkeypatch.setattr(
        ont_raw_signal_worker,
        "_prepare_confined_directory",
        lambda *_args: os.open(os.devnull, os.O_RDONLY),
    )
    monkeypatch.setattr(
        ont_raw_signal_worker,
        "pin_conversion_source_descriptors",
        lambda _commands: (_ for _ in ()).throw(TransientLeaseError("source lease temporarily unavailable")),
    )

    worker = OntRawSignalWorker(lambda: SessionContext())
    assert await worker.run_once() == 1
    assert deferred == [
        (
            "job-transient-lease",
            "claim-transient-lease",
            "source_lease_unavailable_retry",
            {"error_type": "TransientLeaseError"},
        )
    ]

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
    (outputs / ("b" * 64 + ".blow5")).write_bytes(b"blow5-b")
    (outputs / ("b" * 64 + ".blow5.idx")).write_bytes(b"index-b")
    routing = final / "routing.json"
    routing.write_text(json.dumps({"groups": {fingerprint: {"blow5": blow5.name, "index": index.name}}, "read_to_group": {"read-1": fingerprint}}), encoding="utf-8")
    artifacts = [
        ont_raw_signal._file_artifact(blow5, "blow5-a", kind="blow5"),
        ont_raw_signal._file_artifact(
            outputs / ("b" * 64 + ".blow5"), "blow5-b", kind="blow5"
        ),
        ont_raw_signal._file_artifact(index, "index-a", kind="blow5_index"),
        ont_raw_signal._file_artifact(
            outputs / ("b" * 64 + ".blow5.idx"), "index-b", kind="blow5_index"
        ),
        ont_raw_signal._file_artifact(routing, "routing", kind="read_routing"),
    ]
    for item in artifacts:
        if item["path"].endswith(f"{fingerprint}.blow5"):
            item["partition_fingerprint"] = fingerprint
        elif item["path"].endswith(f"{'b' * 64}.blow5"):
            item["partition_fingerprint"] = "b" * 64
        elif item["path"].endswith(f"{fingerprint}.blow5.idx"):
            item["partition_fingerprint"] = fingerprint
        elif item["path"].endswith(f"{'b' * 64}.blow5.idx"):
            item["partition_fingerprint"] = "b" * 64
    manifest = {"artifacts": artifacts}
    representation = OntRawSignalRepresentation(
        format="blow5", state="ready", validation_receipts={"adjacent_index": True},
        artifact_manifest=manifest,
        manifest_sha256=ont_raw_signal._digest(manifest),
    )
    assert ont_raw_signal._validated_blow5_paths(representation, "read-1") == (blow5, index)


def test_single_file_waveform_requires_sealed_manifest_digest(tmp_path: Path) -> None:
    blow5 = tmp_path / "source.blow5"
    index = tmp_path / "source.blow5.idx"
    blow5.write_bytes(b"blow5")
    index.write_bytes(b"index")
    manifest = {
        "artifacts": [
            ont_raw_signal._file_artifact(blow5, "blow5", kind="blow5"),
            ont_raw_signal._file_artifact(index, "index", kind="blow5_index"),
        ]
    }
    representation = OntRawSignalRepresentation(
        format="blow5",
        state="ready",
        validation_receipts={"adjacent_index": True},
        artifact_manifest=manifest,
        manifest_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="manifest.*digest|manifest.*authority"):
        ont_raw_signal._validated_blow5_paths(representation)


def test_single_file_waveform_reopens_descriptor_identity(tmp_path: Path) -> None:
    blow5 = tmp_path / "source.blow5"
    index = tmp_path / "source.blow5.idx"
    blow5.write_bytes(b"blow5")
    index.write_bytes(b"index")
    manifest = {
        "artifacts": [
            ont_raw_signal._file_artifact(blow5, "blow5", kind="blow5"),
            ont_raw_signal._file_artifact(index, "index", kind="blow5_index"),
        ]
    }
    representation = OntRawSignalRepresentation(
        format="blow5",
        state="ready",
        validation_receipts={"adjacent_index": True},
        artifact_manifest=manifest,
        manifest_sha256=ont_raw_signal._digest(manifest),
    )
    blow5.unlink()
    blow5.write_bytes(b"replacement")
    with pytest.raises(ValueError, match="descriptor identity|governed"):
        ont_raw_signal._validated_blow5_paths(representation)


class _LookupSession:
    def __init__(self, lookup: OntRawSignalLookup):
        self.lookup = lookup
        self.commits = 0

    async def get(self, _model, _identifier):
        return self.lookup

    async def commit(self):
        self.commits += 1

    async def execute(self, _statement):
        return type("Result", (), {"rowcount": 0})()

    async def rollback(self):
        return None


@pytest.mark.asyncio
async def test_waveform_execute_rechecks_policy_command(monkeypatch: pytest.MonkeyPatch) -> None:
    policy_digest = "d" * 64
    admitted: list[tuple[str, str]] = []

    monkeypatch.setattr(
        ont_raw_signal_worker,
        "raw_signal_runtime_identity",
        lambda: {"image": f"sha256:{policy_digest}", "digest": policy_digest},
        raising=False,
    )
    monkeypatch.setattr(
        ont_raw_signal_worker,
        "assert_local_raw_runtime_image",
        lambda runtime, image: admitted.append((runtime, image)),
        raising=False,
    )

    class Child:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*_command, **_kwargs):
        return Child()

    monkeypatch.setattr(ont_raw_signal_worker.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    worker = OntRawSignalWorker(lambda: None)
    command = [
        "docker", "run", "--rm", "--pull=never", "--network=none",
        f"sha256:{policy_digest}", "python", "/opt/bms/ont_raw_signal_lookup.py",
    ]

    receipt = await worker._execute(command, "lookup-1", "claim-1", waveform=True)

    assert receipt["returncode"] == 0
    assert admitted == [("docker", f"sha256:{policy_digest}")]

    with pytest.raises(RuntimeError, match="network"):
        await worker._execute(
            [argument if argument != "--network=none" else "--network=host" for argument in command],
            "lookup-1",
            "claim-1",
            waveform=True,
        )


@pytest.mark.asyncio
async def test_waveform_claim_uses_checked_in_runtime_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blow5 = tmp_path / "source.blow5"
    index = tmp_path / "source.blow5.idx"
    blow5.write_bytes(b"blow5")
    index.write_bytes(b"index")
    representation = OntRawSignalRepresentation(id="representation-1")
    lookup = OntRawSignalLookup(
        id="lookup-1",
        representation_id=representation.id,
        read_id="read-1",
        state="requested",
        created_at=datetime.utcnow(),
    )
    class ClaimResult:
        def __init__(self, value: Any):
            self.value = value
            self.rowcount = 1

        def scalars(self):
            return self

        def first(self):
            return self.value

        def scalar_one_or_none(self):
            return self.value

    class ClaimSession:
        def __init__(self):
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            return ClaimResult(None if self.calls == 1 else lookup)

        async def get(self, _model, _identifier):
            return representation

        async def commit(self):
            return None

    session = ClaimSession()
    monkeypatch.setattr(
        ont_raw_signal,
        "_validated_blow5_paths",
        lambda _representation, _read_id=None: (blow5, index),
    )
    monkeypatch.setattr(
        ont_raw_signal,
        "_artifact_for_resolved_path",
        lambda _representation, path, _kind: ont_raw_signal._file_artifact(
            path, path.name, kind="blow5" if path == blow5 else "blow5_index"
        ),
    )
    monkeypatch.setattr(
        ont_raw_signal,
        "_resource_snapshot",
        lambda _source_bytes: {
            "staging_root": str(tmp_path),
            "container_runtime": "docker",
            "container_image": "environment-image",
            "container_digest": "a" * 64,
            "disk_free_bytes": 100,
            "required_free_bytes": 1,
            "worker_uid": os.getuid(),
            "worker_gid": os.getgid(),
        },
    )
    policy_digest = "b" * 64
    monkeypatch.setattr(
        ont_raw_signal,
        "raw_signal_runtime_identity",
        lambda: {
            "image": f"sha256:{policy_digest}",
            "digest": policy_digest,
            "policy_sha256": "c" * 64,
        },
    )
    admitted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        ont_raw_signal,
        "assert_local_raw_runtime_image",
        lambda runtime, image: admitted.append((runtime, image)),
    )
    lease_calls: list[tuple[int, int, int]] = []
    monkeypatch.setattr(
        ont_raw_signal.fcntl,
        "fcntl",
        lambda descriptor, operation, value: lease_calls.append(
            (descriptor, operation, value)
        ) or 0,
    )

    claimed = await ont_raw_signal.claim_next_waveform_lookup(session)
    assert claimed is not None
    _lookup, command, _output, source = claimed

    assert admitted == [("docker", f"sha256:{policy_digest}")]
    assert f"sha256:{policy_digest}" in command
    assert "environment-image" not in command
    assert "--fd-socket" in command
    assert "/proc/self/fd/unbound-waveform-blow5" in command
    assert "/proc/self/fd/unbound-waveform-index" in command
    assert not any("/input/" in value for value in command)
    assert source["fd_socket"].endswith("/source-fd.sock")
    assert len(source["source_fds"]) == 4
    assert lease_calls == [
        (
            source["source_fds"][1],
            ont_raw_signal.fcntl.F_SETLEASE,
            ont_raw_signal.fcntl.F_RDLCK,
        ),
        (
            source["source_fds"][3],
            ont_raw_signal.fcntl.F_SETLEASE,
            ont_raw_signal.fcntl.F_RDLCK,
        ),
    ]
    assert _lookup.receipt["runtime_identity"] == {
        "image_digest": policy_digest,
        "runtime_policy_sha256": "c" * 64,
    }
    for descriptor in source["source_fds"]:
        os.close(descriptor)


def test_waveform_terminal_receipt_retains_admitted_runtime_identity() -> None:
    runtime_identity = {
        "image_digest": "a" * 64,
        "runtime_policy_sha256": "b" * 64,
    }
    receipt = ont_raw_signal._waveform_terminal_receipt(
        {
            "schema": "bms.ont.waveform-output-authority.v1",
            "output_identity": {"device": 1},
            "runtime_identity": runtime_identity,
        },
        {"argv_sha256": "c" * 64, "returncode": 0},
        {
            "schema": "bms.ont.raw-waveform.v1",
            "read_id": "read-1",
            "sample_count": 4,
            "returned_sample_count": 2,
            "stride": 2,
        },
        {"blow5": {}, "index": {}},
        {"sha256": "d" * 64},
    )

    assert receipt["runtime_identity"] == runtime_identity


def test_waveform_output_descriptor_rejects_rewrite_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "waveform.json"
    authority = ont_raw_signal._create_waveform_output_placeholder(output)
    output.write_text("{\"samples\": [1]}", encoding="utf-8")
    original_read = ont_raw_signal.os.read
    rewritten = False

    def read_once(fd: int, size: int) -> bytes:
        nonlocal rewritten
        data = original_read(fd, size)
        if data and not rewritten:
            rewritten = True
            output.write_text("{\"samples\": [2]}", encoding="utf-8")
        return data

    monkeypatch.setattr(ont_raw_signal.os, "read", read_once)
    with pytest.raises(ValueError, match="changed"):
        ont_raw_signal._read_waveform_output_descriptor(output, authority)


def test_atomic_directory_publication_rejects_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    source_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    destination_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(FileExistsError):
            ont_raw_signal._rename_directory_noreplace(
                source_fd, "source", destination_fd, "destination"
            )
    finally:
        os.close(source_fd)
        os.close(destination_fd)
    assert source.is_dir()
    assert destination.is_dir()


def test_waveform_output_descriptor_rejects_replaced_inode(tmp_path: Path) -> None:
    output = tmp_path / "waveform.json"
    authority = ont_raw_signal._create_waveform_output_placeholder(output)
    output.write_text("{\"samples\": []}", encoding="utf-8")

    raw, identity = ont_raw_signal._read_waveform_output_descriptor(output, authority)
    assert raw == b'{"samples": []}'
    assert identity["sha256"]

    output.unlink()
    output.write_text("{\"samples\": [1]}", encoding="utf-8")
    with pytest.raises(ValueError, match="descriptor identity"):
        ont_raw_signal._read_waveform_output_descriptor(output, authority)


def test_waveform_receipt_contract_binds_read_schema_and_counts() -> None:
    valid = {
        "schema": "bms.ont.raw-waveform.v1",
        "read_id": "read-1",
        "sample_count": 4,
        "returned_sample_count": 2,
        "stride": 2,
        "samples": [1.0, 2.0],
        "source_identity": {"blow5": {}, "index": {}},
    }
    ont_raw_signal._validate_waveform_payload(valid, "read-1")
    for mutation in (
        {"read_id": "read-2"},
        {"schema": "wrong"},
        {"returned_sample_count": 3},
        {"sample_count": 1},
    ):
        payload = {**valid, **mutation}
        with pytest.raises(ValueError, match="waveform receipt"):
            ont_raw_signal._validate_waveform_payload(payload, "read-1")


def test_terminal_waveform_lookup_migration_blocks_update_and_delete(
    tmp_path: Path,
) -> None:
    from migrations import runner as migration_runner

    migration = next(
        (
            item
            for item in migration_runner.MIGRATIONS
            if item.name == "seal_ont_raw_signal_lookup_terminal_immutability"
        ),
        None,
    )
    assert migration is not None, "terminal waveform lookup migration is not registered"
    assert migration.version == 40
    database_path = tmp_path / "waveform-terminal.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE ont_raw_signal_lookups (id TEXT PRIMARY KEY, state TEXT NOT NULL, reason_code TEXT)"
        )
        connection.executemany(
            "INSERT INTO ont_raw_signal_lookups VALUES (?, ?, ?)",
            [
                ("lookup-ready", "ready", "ready"),
                ("lookup-failed", "failed", "failed"),
                ("lookup-running", "running", "running"),
            ],
        )
        connection.commit()

    migration.fn(str(database_path))

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE ont_raw_signal_lookups SET state='ready' WHERE id='lookup-running'"
        )
        connection.commit()
        for lookup_id in ("lookup-ready", "lookup-failed", "lookup-running"):
            with pytest.raises(sqlite3.IntegrityError, match="terminal.*immutable"):
                connection.execute(
                    "UPDATE ont_raw_signal_lookups SET reason_code='changed' WHERE id=?",
                    (lookup_id,),
                )
            with pytest.raises(sqlite3.IntegrityError, match="terminal.*immutable"):
                connection.execute(
                    "DELETE FROM ont_raw_signal_lookups WHERE id=?",
                    (lookup_id,),
                )


def test_publication_artifact_ids_are_deterministic() -> None:
    first = ont_raw_signal._deterministic_publication_artifact_id(
        "run-1", 1, "outputs/a.blow5", "a" * 64
    )
    second = ont_raw_signal._deterministic_publication_artifact_id(
        "run-1", 1, "outputs/a.blow5", "a" * 64
    )
    changed = ont_raw_signal._deterministic_publication_artifact_id(
        "run-1", 1, "outputs/a.blow5", "b" * 64
    )
    assert first == second
    assert first != changed


@pytest.mark.asyncio
async def test_expired_waveform_lookup_cannot_publish_ready(tmp_path: Path) -> None:
    output = tmp_path / "waveform.json"
    output.write_text(json.dumps({"sample_count": 1, "samples": [0.25]}), encoding="utf-8")
    lookup = OntRawSignalLookup(
        id="lookup-expired",
        state="running",
        claim_token="claim-expired",
        lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
        receipt={},
    )
    session = _LookupSession(lookup)
    with pytest.raises(ValueError, match="lease|ownership"):
        await ont_raw_signal.finish_waveform_lookup(
            session,
            lookup.id,
            str(lookup.claim_token),
            output,
            {"source": "test"},
        )
    assert lookup.state == "running"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_expired_waveform_lookup_cannot_renew_lease() -> None:
    lookup = OntRawSignalLookup(
        id="lookup-renew-expired",
        state="running",
        claim_token="claim-renew-expired",
        lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
        receipt={},
    )
    session = _LookupSession(lookup)
    with pytest.raises(ValueError, match="lease|ownership"):
        await ont_raw_signal.renew_waveform_lookup_lease(
            session,
            lookup.id,
            str(lookup.claim_token),
        )
    assert session.commits == 0


@pytest.mark.asyncio
async def test_expired_waveform_lookup_cannot_mark_failed() -> None:
    lookup = OntRawSignalLookup(
        id="lookup-fail-expired",
        state="running",
        claim_token="claim-fail-expired",
        lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
        receipt={},
    )
    session = _LookupSession(lookup)
    await ont_raw_signal.fail_waveform_lookup(
        session,
        lookup.id,
        str(lookup.claim_token),
        "worker_failed",
    )
    assert lookup.state == "running"
    assert session.commits == 0


def test_single_file_waveform_rejects_manifest_path_only_authority(tmp_path: Path) -> None:
    blow5 = tmp_path / "source.blow5"
    index = tmp_path / "source.blow5.idx"
    blow5.write_bytes(b"blow5")
    index.write_bytes(b"index")
    manifest = {
        "artifacts": [
            {"kind": "blow5", "path": str(blow5)},
            {"kind": "blow5_index", "path": str(index)},
        ]
    }
    representation = OntRawSignalRepresentation(
        format="blow5",
        state="ready",
        validation_receipts={"adjacent_index": True},
        artifact_manifest=manifest,
        manifest_sha256=ont_raw_signal._digest(manifest),
    )
    with pytest.raises(ValueError, match="descriptor authority"):
        ont_raw_signal._validated_blow5_paths(representation)


def test_partitioned_waveform_rejects_replaced_routing_root_symlink(tmp_path: Path) -> None:
    final = tmp_path / "governed" / "job"
    outputs = final / "outputs"
    outputs.mkdir(parents=True)
    fingerprint = "a" * 64
    blow5 = outputs / f"{fingerprint}.blow5"
    index = outputs / f"{fingerprint}.blow5.idx"
    blow5.write_bytes(b"blow5")
    index.write_bytes(b"index")
    other_fingerprint = "b" * 64
    other_blow5 = outputs / f"{other_fingerprint}.blow5"
    other_index = outputs / f"{other_fingerprint}.blow5.idx"
    other_blow5.write_bytes(b"other-blow5")
    other_index.write_bytes(b"other-index")
    routing = final / "routing.json"
    routing.write_text(json.dumps({
        "groups": {fingerprint: {"blow5": blow5.name, "index": index.name}},
        "read_to_group": {"read-1": fingerprint},
    }), encoding="utf-8")
    artifacts = [
        ont_raw_signal._file_artifact(blow5, "blow5", kind="blow5"),
        ont_raw_signal._file_artifact(index, "index", kind="blow5_index"),
        ont_raw_signal._file_artifact(other_blow5, "other-blow5", kind="blow5"),
        ont_raw_signal._file_artifact(other_index, "other-index", kind="blow5_index"),
        ont_raw_signal._file_artifact(routing, "routing", kind="read_routing"),
    ]
    artifacts[0]["partition_fingerprint"] = fingerprint
    artifacts[1]["partition_fingerprint"] = fingerprint
    artifacts[2]["partition_fingerprint"] = other_fingerprint
    artifacts[3]["partition_fingerprint"] = other_fingerprint
    manifest = {"artifacts": artifacts}
    representation = OntRawSignalRepresentation(
        format="blow5",
        state="ready",
        validation_receipts={"adjacent_index": True},
        artifact_manifest=manifest,
        manifest_sha256=ont_raw_signal._digest(manifest),
    )
    escaped = tmp_path / "escaped-job"
    final.rename(escaped)
    final.symlink_to(escaped, target_is_directory=True)

    with pytest.raises(ValueError, match="routing.*governed|symbolic"):
        ont_raw_signal._validated_blow5_paths(representation, "read-1")


class _Session:
    def __init__(self, job: OntRawSignalDerivationJob, *, rowcount: int = 1):
        self.job = job
        self.rowcount = rowcount
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

    async def execute(self, _statement):
        return type(
            "Result",
            (),
            {
                "rowcount": self.rowcount,
                "scalar_one_or_none": lambda _result: None,
            },
        )()

    async def rollback(self):
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
async def test_contract_13_derivation_transition_rejects_lost_cas_race(tmp_path: Path) -> None:
    job, _snapshot = _job(tmp_path)
    session = _Session(job, rowcount=0)
    with pytest.raises(ValueError, match="lease|ownership"):
        await ont_raw_signal.transition_derivation(
            session,
            job.id,
            str(job.claim_token),
            "converting",
            "started",
            {},
        )
    assert job.state == "admitted"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_contract_12_expired_derivation_renewal_is_rejected(tmp_path: Path) -> None:
    job, _snapshot = _job(tmp_path)
    job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    session = _Session(job, rowcount=0)
    with pytest.raises(ValueError, match="lease|ownership"):
        await ont_raw_signal.renew_derivation_lease(
            session,
            job.id,
            str(job.claim_token),
        )
    assert session.commits == 0


def _publication_unit(staging_root: Path) -> tuple[Path, dict[str, int], dict[str, str]]:
    stage = staging_root / "job-1" / "attempt-1"
    outputs = stage / "outputs"
    outputs.mkdir(parents=True)
    groups = {"a" * 64: 1, "b" * 64: 2}
    routing_payload = {
        "schema": "bms.ont.raw-signal-routing.v1",
        "groups": {
            group: {
                "blow5": f"{group}.blow5",
                "index": f"{group}.blow5.idx",
                "read_count": count,
            }
            for group, count in groups.items()
        },
        "read_to_group": {"read-a": "a" * 64, "read-b": "b" * 64},
    }
    routing = stage / "routing.json"
    routing.write_text(
        json.dumps(routing_payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    for group in groups:
        (outputs / f"{group}.blow5").write_bytes(group.encode())
        (outputs / f"{group}.blow5.idx").write_bytes(b"index")

    def output_identity(path: Path) -> dict[str, int | str]:
        info = path.stat()
        return {
            "sha256": _sha256(path),
            "bytes": info.st_size,
            "device": info.st_dev,
            "inode": info.st_ino,
            "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
        }

    semantic = {
        "status": "passed",
        "duplicate_read_ids": 0,
        "partition_counts": groups,
        "output_identities": {
            group: {
                "blow5": output_identity(outputs / f"{group}.blow5"),
                "index": output_identity(outputs / f"{group}.blow5.idx"),
            }
            for group in groups
        },
        "read_count": 3,
        "routing_sha256": _sha256(routing),
    }
    (stage / "semantic-receipt.json").write_text(
        json.dumps(semantic), encoding="utf-8"
    )
    commands = {
        "stage": str(stage),
        "outputs": str(outputs),
        "routing": str(routing),
    }
    return stage, groups, commands


@pytest.mark.asyncio
async def test_contract_12_publication_emits_one_pair_per_partition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    staging_root = tmp_path / "staging"
    stage, groups, commands = _publication_unit(staging_root)
    source_file = tmp_path / "source.pod5"
    source_file.write_bytes(b"source")
    source = _source(source_file, "acquisition")
    job, snapshot = _job(staging_root)
    monkeypatch.setenv(ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(staging_root))
    session = _Session(job)
    representation = await ont_raw_signal.publish_derivation(session, job, source, commands)
    kinds = [artifact["kind"] for artifact in representation.artifact_manifest["artifacts"]]
    assert kinds.count("blow5") == 2
    assert kinds.count("blow5_index") == 2
    assert kinds.count("read_routing") == 1
    assert representation.read_count == 3
    assert not stage.exists()
    assert session.commits == 0

    recovered = await ont_raw_signal.publish_derivation(
        _Session(job), job, source, commands
    )
    assert [
        (artifact["kind"], artifact["sha256"])
        for artifact in recovered.artifact_manifest["artifacts"]
    ] == [
        (artifact["kind"], artifact["sha256"])
        for artifact in representation.artifact_manifest["artifacts"]
    ]
    assert set(groups) == {"a" * 64, "b" * 64}


@pytest.mark.asyncio
async def test_publication_rejects_symlinked_destination_without_touching_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging_root = tmp_path / "staging"
    stage, _groups, commands = _publication_unit(staging_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside-must-remain-unchanged")
    (tmp_path / "ont-raw-signal").symlink_to(outside, target_is_directory=True)
    source_file = tmp_path / "source.pod5"
    source_file.write_bytes(b"source")
    source = _source(source_file, "acquisition")
    job, _snapshot = _job(staging_root)
    monkeypatch.setenv(ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(staging_root))

    with pytest.raises(ValueError, match="descriptor|symbolic|publication"):
        await ont_raw_signal.publish_derivation(
            _Session(job), job, source, commands
        )

    assert sentinel.read_bytes() == b"outside-must-remain-unchanged"
    assert not (outside / job.run_id).exists()
    assert stage.is_dir()


@pytest.mark.asyncio
async def test_publication_rejects_symlinked_stage_generation_without_consuming_outside(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging_root = tmp_path / "staging"
    stage, _groups, commands = _publication_unit(staging_root)
    outside = tmp_path / "outside-stage"
    staging_root.rename(outside)
    staging_root.symlink_to(outside, target_is_directory=True)
    outside_semantic = outside / "job-1" / "attempt-1" / "semantic-receipt.json"
    before = outside_semantic.read_bytes()
    source_file = tmp_path / "source.pod5"
    source_file.write_bytes(b"source")
    source = _source(source_file, "acquisition")
    job, _snapshot = _job(staging_root)
    monkeypatch.setenv(ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(staging_root))

    with pytest.raises(ValueError, match="descriptor|symbolic|publication"):
        await ont_raw_signal.publish_derivation(
            _Session(job), job, source, commands
        )

    assert outside_semantic.read_bytes() == before
    assert outside_semantic.is_file()
    assert not (tmp_path / "ont-raw-signal" / job.run_id).exists()
    assert stage.is_symlink() is False


@pytest.mark.asyncio
async def test_publication_rolls_back_when_root_identity_changes_at_atomic_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging_root = tmp_path / "staging"
    stage, _groups, commands = _publication_unit(staging_root)
    outside = tmp_path / "outside-interposition"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside-must-remain-unchanged")
    source_file = tmp_path / "source.pod5"
    source_file.write_bytes(b"source")
    source = _source(source_file, "acquisition")
    job, _snapshot = _job(staging_root)
    monkeypatch.setenv(ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(staging_root))
    real_rename = ont_raw_signal._rename_directory_noreplace
    interposed = False

    def interposing_rename(*args: Any, **kwargs: Any) -> None:
        nonlocal interposed
        if not interposed:
            interposed = True
            publication_root = tmp_path / "ont-raw-signal"
            publication_root.rename(tmp_path / "displaced-publication-root")
            publication_root.symlink_to(outside, target_is_directory=True)
        real_rename(*args, **kwargs)

    monkeypatch.setattr(ont_raw_signal, "_rename_directory_noreplace", interposing_rename)
    with pytest.raises(ValueError, match="publication root.*confinement|identity changed"):
        await ont_raw_signal.publish_derivation(
            _Session(job), job, source, commands
        )

    assert interposed
    assert sentinel.read_bytes() == b"outside-must-remain-unchanged"
    assert list(outside.iterdir()) == [sentinel]
    assert stage.is_dir()
    assert not (
        tmp_path
        / "displaced-publication-root"
        / job.run_id
        / str(job.observed_generation)
        / job.id
    ).exists()


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
        self.source_identity = {
            "sha256": "0" * 64,
            "bytes": 1,
            "device": 1,
            "inode": 2,
            "mtime_ns": 3,
            "ctime_ns": 4,
        }
        self.index_identity = {
            "sha256": "1" * 64,
            "bytes": 1,
            "device": 1,
            "inode": 5,
            "mtime_ns": 6,
            "ctime_ns": 7,
        }

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
    monkeypatch.setattr(
        validator,
        "_slow5_open",
        lambda _path, **_kwargs: _FakeSlow5([_slow5_record(reads[0])]),
    )
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


def test_conversion_pin_rejects_intermediate_root_symlink_replacement(tmp_path: Path) -> None:
    approved_parent = tmp_path / "approved"
    source_root = approved_parent / "run"
    source_root.mkdir(parents=True)
    source_path = source_root / "chunk.pod5"
    source_path.write_bytes(b"closed-pod5")
    job, snapshot = _job(tmp_path / "staging")
    source = _source(source_path, "acquisition-live")
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)
    moved_parent = tmp_path / "moved-approved"
    approved_parent.rename(moved_parent)
    approved_parent.symlink_to(moved_parent, target_is_directory=True)

    with pytest.raises(OSError):
        ont_raw_signal.pin_conversion_source_descriptors(commands)


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


def test_raw_runtime_identity_is_bound_to_checked_in_policy_and_rejects_env_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = json.loads(
        (ROOT / "platform/api/config/ont_signal_workbench/raw_signal_runtime_policy_v1.json").read_text(
            encoding="utf-8"
        )
    )
    image_id = policy["runtime_id"]
    monkeypatch.setenv(ont_raw_signal.BLOW5_CONTAINER_ENV, image_id)
    monkeypatch.setenv(
        ont_raw_signal.BLOW5_CONTAINER_DIGEST_ENV,
        image_id.removeprefix("sha256:"),
    )
    identity = ont_raw_signal.raw_signal_runtime_identity()
    assert identity["image"] == image_id
    assert identity["digest"] == image_id.removeprefix("sha256:")

    monkeypatch.setenv(ont_raw_signal.BLOW5_CONTAINER_DIGEST_ENV, "0" * 64)
    with pytest.raises(RuntimeError, match="raw-signal runtime policy"):
        ont_raw_signal.raw_signal_runtime_identity()


def test_raw_runtime_admission_inspects_exact_local_image_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspected: list[list[str]] = []
    monkeypatch.setattr(ont_raw_signal.shutil, "which", lambda runtime: f"/usr/bin/{runtime}")

    def inspect(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        inspected.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="sha256:" + "a" * 64 + "\n", stderr="")

    monkeypatch.setattr(ont_raw_signal.subprocess, "run", inspect)
    ont_raw_signal.assert_local_raw_runtime_image("docker", "sha256:" + "a" * 64)
    assert inspected == [["docker", "image", "inspect", "--format", "{{.Id}}", "sha256:" + "a" * 64]]


def test_every_raw_container_command_uses_pull_never(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ont_raw_signal, "_container_image_ref", lambda _snapshot: "sha256:" + "a" * 64)
    source_path = tmp_path / "source.pod5"
    source_path.write_bytes(b"pod5")
    source = _source(source_path, "acquisition")
    job, snapshot = _job(tmp_path / "staging")
    commands = ont_raw_signal._conversion_commands(job, source, snapshot)
    assert "--pull=never" in commands["common"]
    assert "--pull=never" in ont_raw_signal.conversion_unit_commands(commands, "a" * 64)["convert"]
    assert "--pull=never" in ont_raw_signal.conversion_unit_commands(commands, "a" * 64)["quickcheck"]
    assert "--pull=never" in ont_raw_signal.conversion_unit_commands(commands, "a" * 64)["index_create"]

    blow5 = tmp_path / "source.blow5"
    index = Path(f"{blow5}.idx")
    blow5.write_bytes(b"blow5")
    index.write_bytes(b"index")
    external = OntRawSignalRepresentation(
        id="external-source",
        artifact_manifest={
            "artifacts": [
                ont_raw_signal._file_artifact(blow5, "blow5", kind="blow5"),
                ont_raw_signal._file_artifact(index, "index", kind="blow5_index"),
            ]
        },
    )
    external_commands = ont_raw_signal._external_blow5_validation_commands(job, external, snapshot)
    assert "--pull=never" in external_commands["quickcheck"]
    assert "--pull=never" in external_commands["semantic_validate"]
    assert "--expected-blow5-sha256" in external_commands["semantic_validate"]
    assert "--expected-index-sha256" in external_commands["semantic_validate"]
    assert "--expected-blow5-root-device" in external_commands["semantic_validate"]
    assert "--expected-index-root-inode" in external_commands["semantic_validate"]


def test_raw_runtime_build_script_uses_independent_policy_before_emitting_identity() -> None:
    script = (ROOT / "scripts/build_ont_raw_signal_runtime.sh").read_text(encoding="utf-8")
    assert "raw_signal_runtime_policy_v1.json" in script
    assert "does not match the approved raw-signal runtime policy" in script
    assert "BMS_ONT_SLOW5TOOLS_IMAGE" in script
    assert f"EXPECTED_POLICY_SHA256=\"{ont_raw_signal.RAW_SIGNAL_RUNTIME_POLICY_SHA256}\"" in script
    assert "sha256sum \"$POLICY_PATH\"" in script


@pytest.mark.asyncio
async def test_expired_publication_recovery_cannot_promote_cancelled_job(tmp_path: Path) -> None:
    job, _snapshot = _job(tmp_path / "staging")
    job.state = "publishing"
    job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    job.output_representation_id = "rep-1"
    job.cancel_requested_at = datetime.utcnow()
    source = SimpleNamespace(id="source-1")
    representation = SimpleNamespace(id="rep-1", state="ready")

    class _Result:
        def __init__(self, values: list[Any], *, rowcount: int = 1):
            self.values = values
            self.rowcount = rowcount

        def scalars(self) -> "_Result":
            return self

        def __iter__(self):
            return iter(self.values)

    class _RecoverySession:
        def __init__(self) -> None:
            self.results = [_Result([]), _Result([job]), _Result([], rowcount=1)]
            self.added: list[Any] = []
            self.commits = 0

        async def execute(self, _statement: Any) -> _Result:
            result = self.results.pop(0)
            if result.rowcount == 1 and job.cancel_requested_at is not None:
                job.state = "cancelled"
                job.reason_code = "cancelled_after_publication_before_recovery"
            return result

        async def get(self, _model: Any, identifier: str) -> Any:
            return {"source-1": source, "rep-1": representation}.get(identifier)

        def add(self, value: Any) -> None:
            self.added.append(value)

        async def commit(self) -> None:
            self.commits += 1

    session = _RecoverySession()
    recovered = await ont_raw_signal.recover_expired_derivations(session)

    assert recovered == 1
    assert job.state == "cancelled"
    assert job.reason_code == "cancelled_after_publication_before_recovery"
    assert not any(getattr(event, "state", None) == "ready" for event in session.added)


@pytest.mark.asyncio
async def test_expired_recovery_ignores_lost_cas_race(tmp_path: Path) -> None:
    job, _snapshot = _job(tmp_path / "staging")
    job.state = "publishing"
    job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    job.output_representation_id = "rep-1"
    job.cancel_requested_at = datetime.utcnow()

    class _Result:
        def __init__(self, values: list[Any], *, rowcount: int = 1):
            self.values = values
            self.rowcount = rowcount

        def scalars(self) -> "_Result":
            return self

        def __iter__(self):
            return iter(self.values)

    class _RecoverySession:
        def __init__(self) -> None:
            self.results = [_Result([]), _Result([job]), _Result([], rowcount=0)]
            self.commits = 0

        async def execute(self, _statement: Any) -> _Result:
            return self.results.pop(0)

        async def get(self, _model: Any, _identifier: str) -> Any:
            raise AssertionError("lost recovery CAS must not inspect or publish output")

        async def rollback(self) -> None:
            return None

        async def commit(self) -> None:
            self.commits += 1

    session = _RecoverySession()
    recovered = await ont_raw_signal.recover_expired_derivations(session)

    assert recovered == 0
    assert job.state == "publishing"
    assert session.commits == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["expired", "cancelled"])
async def test_publication_rejects_expired_or_cancelled_claim_before_commit(
    tmp_path: Path, tamper: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging_root = tmp_path / "staging"
    _stage, _groups, commands = _publication_unit(staging_root)
    source_file = tmp_path / "source.pod5"
    source_file.write_bytes(b"source")
    source = _source(source_file, "acquisition")
    job, _snapshot = _job(staging_root)
    if tamper == "expired":
        job.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    else:
        job.cancel_requested_at = datetime.utcnow()
    monkeypatch.setenv(ont_raw_signal.BLOW5_STAGING_ROOT_ENV, str(staging_root))
    session = _Session(job)

    with pytest.raises(ValueError, match="lease|cancellation"):
        await ont_raw_signal.publish_derivation(session, job, source, commands)
    assert session.commits == 0
