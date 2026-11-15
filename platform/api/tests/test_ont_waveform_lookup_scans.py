"""A waveform lookup hashes a BLOW5 file version once per process, not once per step."""
from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import ont_raw_signal

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    blow5 = tmp_path / "reads.blow5"
    index = tmp_path / "reads.blow5.idx"
    blow5.write_bytes(os.urandom(3 * 1024 * 1024 + 17))
    index.write_bytes(os.urandom(4096))
    artifacts = [ont_raw_signal._file_artifact(blow5, "blow5-1", kind="blow5"),
                 ont_raw_signal._file_artifact(index, "index-1", kind="blow5_index")]
    manifest = {"artifacts": artifacts}
    representation = SimpleNamespace(
        format="blow5", state="ready", validation_receipts={"adjacent_index": True},
        artifact_manifest=manifest, manifest_sha256=ont_raw_signal._digest(manifest),
    )
    getattr(ont_raw_signal, "_VERIFIED_FILE_VERSIONS", {}).clear()
    read = {"bytes": 0}
    real_read = os.read

    def counting_read(descriptor: int, size: int) -> bytes:
        chunk = real_read(descriptor, size)
        read["bytes"] += len(chunk)
        return chunk

    monkeypatch.setattr(ont_raw_signal.os, "read", counting_read)
    return representation, artifacts, blow5, index, read


def one_lookup(representation, artifacts) -> None:
    """The file checks one lookup makes: on request, on claim, when pinning descriptors, and on finish."""
    ont_raw_signal._validated_blow5_paths(representation, "read-1")  # request
    ont_raw_signal._validated_blow5_paths(representation, "read-1")  # claim
    for artifact in artifacts:
        identity = ont_raw_signal._assert_external_blow5_artifact_identity(artifact, authority="waveform artifact")
        _path, root_fd, file_fd = ont_raw_signal._open_descriptor_confined_artifact_fds(artifact, authority="waveform artifact")
        try:
            ont_raw_signal._verified_external_descriptor_identity(file_fd, root_fd, expected=identity, authority="waveform artifact")
        finally:
            os.close(file_fd)
            os.close(root_fd)
    ont_raw_signal._validated_blow5_paths(representation, "read-1")  # finish


def test_first_lookup_reads_each_file_once_and_later_lookups_read_nothing(pair) -> None:
    representation, artifacts, blow5, index, read = pair
    size = blow5.stat().st_size + index.stat().st_size
    one_lookup(representation, artifacts)
    assert read["bytes"] == size  # cold: one pass, not one per step
    one_lookup(representation, artifacts)
    one_lookup(representation, artifacts)
    assert read["bytes"] == size  # warm: no pass at all


def test_a_file_that_changed_or_never_matched_is_still_refused(pair) -> None:
    representation, artifacts, blow5, _index, _read = pair
    one_lookup(representation, artifacts)
    replaced = bytearray(blow5.read_bytes())
    replaced[0] ^= 0xFF
    blow5.write_bytes(bytes(replaced))  # same size, different bytes
    with pytest.raises(ValueError, match="descriptor identity diverged"):
        ont_raw_signal._validated_blow5_paths(representation, "read-1")
    # A file that never matched its recorded hash is refused on first sight.
    wrong = [dict(artifacts[0], sha256="0" * 64), artifacts[1]]
    blow5.write_bytes(bytes(replaced))
    info = blow5.stat()
    wrong[0].update(mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)
    manifest = {"artifacts": wrong}
    other = SimpleNamespace(format="blow5", state="ready", validation_receipts={"adjacent_index": True},
                            artifact_manifest=manifest, manifest_sha256=ont_raw_signal._digest(manifest))
    with pytest.raises(ValueError, match="digest diverged"):
        ont_raw_signal._validated_blow5_paths(other, "read-1")


def test_the_container_checks_the_received_file_version_without_reading_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = importlib.util.spec_from_file_location("ont_raw_signal_lookup_scan_test", ROOT / "scripts" / "ont_raw_signal_lookup.py")
    lookup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lookup)
    blow5 = tmp_path / "reads.blow5"
    blow5.write_bytes(os.urandom(1024 * 1024))
    info, root_info = blow5.stat(), tmp_path.stat()
    expected = {"sha256": "a" * 64, "bytes": info.st_size, "device": info.st_dev, "inode": info.st_ino,
                "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}
    expected_root = {"device": root_info.st_dev, "inode": root_info.st_ino}
    read = {"bytes": 0}
    real_read = os.read
    monkeypatch.setattr(lookup.os, "read", lambda descriptor, size: (read.__setitem__("bytes", read["bytes"] + size), real_read(descriptor, size))[1])
    root_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    file_fd = os.open(blow5, os.O_RDONLY)
    try:
        assert lookup._attest_received_descriptor(file_fd, root_fd, expected, expected_root, "BLOW5") == expected
        assert read["bytes"] == 0
        with pytest.raises(ValueError, match="BLOW5 identity diverged"):
            lookup._attest_received_descriptor(file_fd, root_fd, {**expected, "bytes": info.st_size + 1}, expected_root, "BLOW5")
        with pytest.raises(ValueError, match="governed root identity diverged"):
            lookup._attest_received_descriptor(file_fd, root_fd, expected, {"device": 1, "inode": 1}, "BLOW5")
    finally:
        os.close(file_fd)
        os.close(root_fd)


@pytest.mark.asyncio
async def test_file_checks_for_a_request_run_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    monkeypatch.setattr(ont_raw_signal, "_validated_blow5_paths",
                        lambda _representation, _read_id=None: seen.append(threading.current_thread() is threading.main_thread()))
    representation = SimpleNamespace(id="rep-1", run_id="run-1", observed_generation=1, format="blow5", state="ready",
                                     validation_receipts={}, artifact_manifest={}, manifest_sha256="a" * 64)
    existing = SimpleNamespace(id="lookup-1", run_id="run-1", observed_generation=1, representation_id="rep-1", read_id="read-1",
                               state="ready", reason_code="ready", sample_count=1, samples=[1.0])

    class Session:
        async def get(self, _model, _identifier):
            return representation

        async def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: existing)

    result = await ont_raw_signal.request_waveform_lookup(
        Session(), run_id="run-1", observed_generation=1, representation_id="rep-1", read_id="read-1")
    assert result["lookup_id"] == "lookup-1" and seen == [False]
