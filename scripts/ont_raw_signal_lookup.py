#!/usr/bin/env python3
"""Read one bounded waveform from an indexed BLOW5 source."""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
from pathlib import Path


def _receive_source_descriptors(socket_path: str, expected_count: int = 4) -> list[int]:
    received = array.array("i")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.connect(socket_path)
        payload, ancillary, _flags, _address = client.recvmsg(
            1, socket.CMSG_SPACE(expected_count * received.itemsize)
        )
        if payload != b"F":
            raise ValueError("waveform source descriptor receipt is invalid")
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                received.frombytes(data[: len(data) - (len(data) % received.itemsize)])
        descriptors = list(received)
        if len(descriptors) != expected_count:
            raise ValueError("waveform source descriptor count is invalid")
        return descriptors
    except BaseException:
        for descriptor in received:
            os.close(descriptor)
        raise
    finally:
        client.close()


def _attest_received_descriptor(
    source_fd: int,
    root_fd: int,
    expected: dict[str, int | str],
    expected_root: dict[str, int],
    label: str,
) -> dict[str, int | str]:
    root = os.fstat(root_fd)
    before = os.fstat(source_fd)
    if not stat.S_ISDIR(root.st_mode):
        raise ValueError(f"{label} governed root must be a directory")
    if {"device": int(root.st_dev), "inode": int(root.st_ino)} != expected_root:
        raise ValueError(f"{label} governed root identity diverged")
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"{label} must be a regular file")
    digest = hashlib.sha256()
    os.lseek(source_fd, 0, os.SEEK_SET)
    while chunk := os.read(source_fd, 1024 * 1024):
        digest.update(chunk)
    after = os.fstat(source_fd)
    actual = {
        "sha256": digest.hexdigest(),
        "bytes": int(after.st_size),
        "device": int(after.st_dev),
        "inode": int(after.st_ino),
        "mtime_ns": int(after.st_mtime_ns),
        "ctime_ns": int(after.st_ctime_ns),
    }
    stable_expected = {
        key: expected[key] for key in ("sha256", "bytes", "device", "inode", "mtime_ns")
    }
    stable_actual = {
        key: actual[key] for key in ("sha256", "bytes", "device", "inode", "mtime_ns")
    }
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if stable_actual != stable_expected or before_identity != after_identity:
        raise ValueError(f"{label} identity diverged")
    return dict(expected)


def _copy_attested(source_fd: int, expected: dict[str, int | str], destination: Path, label: str) -> dict[str, int | str]:
    destination_fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        os.lseek(source_fd, 0, os.SEEK_SET)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        after = os.fstat(source_fd)
        actual = {
            "sha256": digest.hexdigest(),
            "bytes": size,
            "device": int(after.st_dev),
            "inode": int(after.st_ino),
            "mtime_ns": int(after.st_mtime_ns),
            "ctime_ns": int(after.st_ctime_ns),
        }
        stable_expected = {
            key: expected[key] for key in ("sha256", "bytes", "device", "inode", "mtime_ns")
        }
        stable_actual = {
            key: actual[key] for key in ("sha256", "bytes", "device", "inode", "mtime_ns")
        }
        if stable_actual != stable_expected or (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
        ) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError(f"{label} changed during snapshot")
        os.fsync(destination_fd)
        if os.fstat(destination_fd).st_size != size:
            raise ValueError(f"{label} snapshot size mismatch")
        return dict(expected)
    finally:
        os.close(destination_fd)


def _expected_identity(parser: argparse.ArgumentParser, args: argparse.Namespace, prefix: str) -> dict[str, int | str]:
    values = {
        "sha256": getattr(args, f"expected_{prefix}_sha256"),
        "bytes": getattr(args, f"expected_{prefix}_size"),
        "device": getattr(args, f"expected_{prefix}_device"),
        "inode": getattr(args, f"expected_{prefix}_inode"),
        "mtime_ns": getattr(args, f"expected_{prefix}_mtime_ns"),
        "ctime_ns": getattr(args, f"expected_{prefix}_ctime_ns"),
    }
    if len(str(values["sha256"])) != 64:
        parser.error(f"expected {prefix} SHA-256 is invalid")
    return values


def _expected_root_identity(args: argparse.Namespace, prefix: str) -> dict[str, int]:
    return {
        "device": int(getattr(args, f"expected_{prefix}_root_device")),
        "inode": int(getattr(args, f"expected_{prefix}_root_inode")),
    }


def _open_absolute_directory_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    current_fd = os.open(os.sep, flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _write_output_no_follow(path: Path, payload: dict[str, object]) -> None:
    parent_fd = _open_absolute_directory_nofollow(path.parent)
    try:
        descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            raise
    finally:
        os.close(parent_fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blow5", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--fd-socket", required=True)
    parser.add_argument("--read-id", required=True)
    parser.add_argument("--max-samples", required=True, type=int)
    parser.add_argument("--output", required=True)
    for prefix in ("blow5", "index"):
        parser.add_argument(f"--expected-{prefix}-sha256", required=True)
        parser.add_argument(f"--expected-{prefix}-size", required=True, type=int)
        parser.add_argument(f"--expected-{prefix}-device", required=True, type=int)
        parser.add_argument(f"--expected-{prefix}-inode", required=True, type=int)
        parser.add_argument(f"--expected-{prefix}-mtime-ns", required=True, type=int)
        parser.add_argument(f"--expected-{prefix}-ctime-ns", required=True, type=int)
        parser.add_argument(f"--expected-{prefix}-root-device", required=True, type=int)
        parser.add_argument(f"--expected-{prefix}-root-inode", required=True, type=int)
    args = parser.parse_args()
    if args.max_samples < 1 or args.max_samples > 20_000:
        raise ValueError("max-samples exceeds the bounded waveform contract")

    import pyslow5

    output_path = Path(args.output)
    snapshot_dir = Path(tempfile.mkdtemp(prefix=".ont-waveform-", dir=str(output_path.parent)))
    slow5 = None
    received_fds: list[int] = []
    try:
        snapshot_blow5 = snapshot_dir / "source.blow5"
        snapshot_index = snapshot_dir / "source.blow5.idx"
        received_fds = _receive_source_descriptors(args.fd_socket)
        blow5_root_fd, blow5_fd, index_root_fd, index_fd = received_fds
        blow5_identity = _attest_received_descriptor(
            blow5_fd,
            blow5_root_fd,
            _expected_identity(parser, args, "blow5"),
            _expected_root_identity(args, "blow5"),
            "BLOW5",
        )
        index_identity = _attest_received_descriptor(
            index_fd,
            index_root_fd,
            _expected_identity(parser, args, "index"),
            _expected_root_identity(args, "index"),
            "BLOW5 index",
        )
        _copy_attested(blow5_fd, blow5_identity, snapshot_blow5, "BLOW5")
        _copy_attested(index_fd, index_identity, snapshot_index, "BLOW5 index")
        slow5 = pyslow5.Open(str(snapshot_blow5), "r")
        read = slow5.get_read(args.read_id, pA=True)
        if read is None:
            raise KeyError("read ID is absent from indexed BLOW5")
        signal = read.get("signal")
        if signal is None:
            raise ValueError("BLOW5 read lacks signal")
    finally:
        if slow5 is not None:
            slow5.close()
        for descriptor in received_fds:
            os.close(descriptor)
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    full_count = int(len(signal))
    stride = max(1, (full_count + args.max_samples - 1) // args.max_samples)
    bounded = [float(value) for value in signal[::stride]][: args.max_samples]
    receipt = {
        "schema": "bms.ont.raw-waveform.v1",
        "read_id": args.read_id,
        "sample_count": full_count,
        "returned_sample_count": len(bounded),
        "stride": stride,
        "units": "pA",
        "samples": bounded,
        "source_identity": {"blow5": blow5_identity, "index": index_identity},
    }
    _write_output_no_follow(output_path, receipt)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(2)
