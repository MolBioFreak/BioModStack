#!/usr/bin/env python3
"""Fail-closed ONT POD5/BLOW5 identity, partition, and semantic validator."""
from __future__ import annotations

import argparse
import array
import csv
import hashlib
import json
import os
import socket
import stat
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pod5
import lib_pod5 as p5b
from pod5.tools.pod5_subset import build_targets_dict, parse_table_mapping


def _receive_source_descriptors(args: argparse.Namespace) -> list[int]:
    expected = len(args.pod5) * 2
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(args.fd_socket))
        _message, ancillary, _flags, _address = client.recvmsg(
            1,
            socket.CMSG_SPACE(expected * array.array("i").itemsize),
        )
    received = array.array("i")
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            usable = len(data) - (len(data) % received.itemsize)
            received.frombytes(data[:usable])
    descriptors = received.tolist()
    if len(descriptors) != expected:
        for descriptor in descriptors:
            os.close(descriptor)
        raise ValueError("source descriptor transfer count is invalid")
    for descriptor in descriptors:
        os.set_inheritable(descriptor, True)
    args.governed_root = [Path(f"/proc/self/fd/{descriptors[index]}") for index in range(0, expected, 2)]
    args.pod5 = [Path(f"/proc/self/fd/{descriptors[index]}") for index in range(1, expected, 2)]
    return descriptors


def _receive_external_descriptors(args: argparse.Namespace) -> list[int]:
    expected = 4
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(str(args.fd_socket))
        _message, ancillary, _flags, _address = client.recvmsg(
            1,
            socket.CMSG_SPACE(expected * array.array("i").itemsize),
        )
    received = array.array("i")
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            usable = len(data) - (len(data) % received.itemsize)
            received.frombytes(data[:usable])
    descriptors = received.tolist()
    if len(descriptors) != expected:
        for descriptor in descriptors:
            os.close(descriptor)
        raise ValueError("external descriptor transfer count is invalid")
    for descriptor in descriptors:
        os.set_inheritable(descriptor, True)
    args.external_root_fds = [descriptors[0], descriptors[2]]
    args.external_file_fds = [descriptors[1], descriptors[3]]
    args.blow5 = Path(f"/proc/self/fd/{descriptors[1]}")
    args.index = Path(f"/proc/self/fd/{descriptors[3]}")
    return descriptors


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _run_info_fingerprint(run_info: Any) -> str:
    """Match the qualified full typed run-info partition contract."""
    return hashlib.sha256(repr(run_info).encode("utf-8")).hexdigest()


def _pod5_records(
    paths: list[Path],
) -> tuple[dict[str, dict[str, Any]], set[str], Counter[str]]:
    records: dict[str, dict[str, Any]] = {}
    acquisitions: set[str] = set()
    group_counts: Counter[str] = Counter()
    duplicate_count = 0
    for path in paths:
        with pod5.Reader(path) as reader:
            for read in reader.reads():
                read_id = str(read.read_id)
                duplicate_count += read_id in records
                acquisition_id = str(read.run_info.acquisition_id)
                fingerprint = _run_info_fingerprint(read.run_info)
                acquisitions.add(acquisition_id)
                group_counts[fingerprint] += 1
                signal = np.asarray(read.signal, dtype=np.int16)
                records[read_id] = {
                    "group": fingerprint,
                    "acquisition_id": acquisition_id,
                    "signal_sha256": hashlib.sha256(signal.tobytes(order="C")).hexdigest(),
                    "signal_length": len(signal),
                    "start_time": int(read.start_sample),
                    "read_number": int(read.read_number),
                    "channel_number": int(read.pore.channel),
                    "start_mux": int(read.pore.well),
                    "offset": float(read.calibration.offset),
                    "scale": float(read.calibration.scale),
                    "sampling_rate": int(read.run_info.sample_rate),
                    "median_before": float(read.median_before),
                    "num_minknow_events": int(read.num_minknow_events),
                    "tracked_scaling_shift": float(read.tracked_scaling.shift),
                    "tracked_scaling_scale": float(read.tracked_scaling.scale),
                    "predicted_scaling_shift": float(read.predicted_scaling.shift),
                    "predicted_scaling_scale": float(read.predicted_scaling.scale),
                    "num_reads_since_mux_change": int(read.num_reads_since_mux_change),
                    "time_since_mux_change": float(read.time_since_mux_change),
                    "open_pore_level": float(read.open_pore_level),
                }
    if duplicate_count:
        raise ValueError(f"duplicate POD5 read IDs: {duplicate_count}")
    return records, acquisitions, group_counts


class _SnapshotSlow5:
    def __init__(
        self,
        handle: Any,
        temporary: tempfile.TemporaryDirectory[str],
        source_identity: dict[str, Any] | None = None,
        index_identity: dict[str, Any] | None = None,
    ) -> None:
        self._handle = handle
        self._temporary = temporary
        self.source_identity = source_identity
        self.index_identity = index_identity

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)

    def close(self) -> None:
        try:
            self._handle.close()
        finally:
            self._temporary.cleanup()


def _copy_slow5_snapshot(
    path: Path | None,
    destination: Path,
    *,
    expected: dict[str, Any] | None,
    label: str,
    source_fd: int | None = None,
) -> dict[str, Any]:
    source_descriptor = destination_fd = -1
    try:
        if source_fd is not None:
            source_descriptor = os.dup(source_fd)
        elif path is not None:
            source_descriptor = os.open(
                path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            )
        else:
            raise ValueError(f"{label} source is missing")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        os.lseek(source_descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        after = os.fstat(source_descriptor)
        identity = {
            "sha256": digest.hexdigest(),
            "bytes": size,
            "device": after.st_dev,
            "inode": after.st_ino,
            "mtime_ns": after.st_mtime_ns,
            "ctime_ns": after.st_ctime_ns,
        }
        if (
            expected is not None
            and any(
                identity[key] != expected.get(key)
                for key in ("sha256", "bytes", "device", "inode", "mtime_ns", "ctime_ns")
            )
        ) or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            raise ValueError(f"{label} changed before scientific consumption")
        os.fsync(destination_fd)
        if os.fstat(destination_fd).st_size != size:
            raise ValueError(f"{label} snapshot size mismatch")
        return identity
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if destination_fd >= 0:
            os.close(destination_fd)


def _slow5_open(
    path: Path | None,
    *,
    index_path: Path | None = None,
    expected: dict[str, Any] | None = None,
    expected_index: dict[str, Any] | None = None,
    temporary_root: Path | None = None,
    source_fd: int | None = None,
    index_fd: int | None = None,
):
    try:
        import pyslow5  # type: ignore
    except ImportError as exc:
        raise ValueError("pyslow5 is required by the qualified validator runtime") from exc
    index = index_path if index_path is not None else (Path(f"{path}.idx") if path is not None else None)
    if index is None and index_fd is None:
        raise ValueError("BLOW5 index source is missing")
    if temporary_root is None and Path("/stage").is_dir():
        temporary_root = Path("/stage")
    temporary = tempfile.TemporaryDirectory(
        prefix=".bms-slow5-",
        dir=str(temporary_root) if temporary_root is not None else None,
    )
    root = Path(temporary.name)
    try:
        snapshot = root / "source.blow5"
        snapshot_index = root / "source.blow5.idx"
        source_identity = _copy_slow5_snapshot(
            path, snapshot, expected=expected, label="BLOW5", source_fd=source_fd
        )
        index_identity = _copy_slow5_snapshot(
            index, snapshot_index, expected=expected_index, label="BLOW5 index", source_fd=index_fd
        )
        handle = pyslow5.Open(str(snapshot), "r")
        if handle is None:
            raise ValueError("BLOW5 could not be opened")
        return _SnapshotSlow5(handle, temporary, source_identity, index_identity)
    except BaseException:
        temporary.cleanup()
        raise


def _verify_inputs(args: argparse.Namespace) -> None:
    if not (
        len(args.pod5)
        == len(args.expected_sha256)
        == len(args.expected_size)
        == len(args.expected_device)
        == len(args.expected_inode)
        == len(args.expected_mtime_ns)
        == len(args.expected_ctime_ns)
        == len(args.governed_root)
        == len(args.expected_root_device)
        == len(args.expected_root_inode)
    ):
        raise ValueError("each POD5 input requires one immutable size and digest authority")
    for path, root, expected_root_device, expected_root_inode, expected_sha256, expected_size, device, inode, mtime_ns, ctime_ns in zip(
        args.pod5,
        args.governed_root,
        args.expected_root_device,
        args.expected_root_inode,
        args.expected_sha256,
        args.expected_size,
        args.expected_device,
        args.expected_inode,
        args.expected_mtime_ns,
        args.expected_ctime_ns,
        strict=True,
    ):
        root_info = root.stat()
        if (root_info.st_dev, root_info.st_ino) != (expected_root_device, expected_root_inode):
            raise ValueError("governed POD5 root identity differs from sealed authority")
        if not getattr(args, "received_fds", None):
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ValueError("POD5 input escaped the governed root mount") from exc
        info = path.stat()
        if getattr(args, "received_fds", None):
            observed_identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            expected_identity = (device, inode, expected_size, mtime_ns)
        else:
            observed_identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            expected_identity = (device, inode, expected_size, mtime_ns, ctime_ns)
        if observed_identity != expected_identity:
            raise ValueError("POD5 input filesystem identity differs from sealed authority")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected_sha256:
            raise ValueError("POD5 input digest differs from the sealed artifact manifest")


def partition_pod5(args: argparse.Namespace) -> dict[str, Any]:
    _verify_inputs(args)
    targets = parse_table_mapping(
        args.table,
        args.template,
        args.columns,
        args.read_id_column,
        False,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    p5b.subset_pod5s_with_mapping(
        list(args.pod5),
        args.output,
        build_targets_dict(targets),
        args.missing_ok,
        False,
        False,
    )
    return {
        "schema": "bms.ont.raw-signal-partition.v1",
        "status": "passed",
        "source_identity": "verified_in_mount_namespace",
    }


def _pod5_partition_inventory(
    paths: list[Path],
) -> tuple[dict[str, str], set[str], Counter[str]]:
    read_groups: dict[str, str] = {}
    acquisitions: set[str] = set()
    group_counts: Counter[str] = Counter()
    duplicate_count = 0
    for path in paths:
        with pod5.Reader(path) as reader:
            for read in reader.reads():
                read_id = str(read.read_id)
                duplicate_count += read_id in read_groups
                fingerprint = _run_info_fingerprint(read.run_info)
                read_groups[read_id] = fingerprint
                acquisitions.add(str(read.run_info.acquisition_id))
                group_counts[fingerprint] += 1
    if duplicate_count:
        raise ValueError(f"duplicate POD5 read IDs: {duplicate_count}")
    return read_groups, acquisitions, group_counts


def source_preflight(args: argparse.Namespace) -> dict[str, Any]:
    _verify_inputs(args)
    read_groups, acquisitions, group_counts = _pod5_partition_inventory(args.pod5)
    expected = str(args.expected_acquisition_id)
    if expected != "external-native" and expected not in acquisitions:
        raise ValueError("POD5 acquisition_id does not match the bound MinKNOW acquisition")
    if not acquisitions:
        raise ValueError("POD5 source has no acquisition identity")
    with args.partition_map.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("read_id", "group"))
        for read_id in sorted(read_groups):
            writer.writerow((read_id, read_groups[read_id]))
    return {
        "schema": "bms.ont.raw-signal-source-preflight.v2",
        "status": "passed",
        "acquisition_id": expected if expected != "external-native" else (next(iter(acquisitions)) if len(acquisitions) == 1 else None),
        "acquisition_ids": sorted(acquisitions),
        "read_count": len(read_groups),
        "duplicate_read_ids": 0,
        "partition_contract": "complete_typed_run_info_repr_sha256_pod5_0.3.35",
        "partition_map_sha256": hashlib.sha256(args.partition_map.read_bytes()).hexdigest(),
        "groups": [
            {"fingerprint": fingerprint, "read_count": group_counts[fingerprint]}
            for fingerprint in sorted(group_counts)
        ],
    }


def _equal_float(observed: float, expected: float) -> bool:
    return bool(np.isclose(observed, expected, equal_nan=True))


def semantic_validate(args: argparse.Namespace) -> dict[str, Any]:
    _verify_inputs(args)
    if len(args.blow5) != len(args.index):
        raise ValueError("each BLOW5 conversion unit requires one adjacent index")
    pod5_records, acquisitions, source_group_counts = _pod5_records(args.pod5)
    seen: Counter[str] = Counter()
    observed_group_counts: Counter[str] = Counter()
    read_to_group: dict[str, str] = {}
    total_samples = 0
    lookup_count = 0
    required = {
        "read_id", "signal", "len_raw_signal", "digitisation", "offset", "range",
        "sampling_rate", "start_time", "read_number", "channel_number", "start_mux",
        "median_before", "num_minknow_events", "tracked_scaling_shift",
        "tracked_scaling_scale", "predicted_scaling_shift", "predicted_scaling_scale",
        "num_reads_since_mux_change", "time_since_mux_change", "open_pore_level",
    }
    groups: dict[str, dict[str, Any]] = {}
    output_identities: dict[str, dict[str, dict[str, Any]]] = {}
    for blow5_path, index_path in zip(args.blow5, args.index, strict=True):
        if index_path != Path(f"{blow5_path}.idx") or not index_path.is_file():
            raise ValueError("BLOW5 conversion unit lacks its adjacent index")
        fingerprint = blow5_path.name.removesuffix(".blow5")
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("BLOW5 unit name is not a run-info fingerprint")
        if fingerprint in groups:
            raise ValueError("duplicate BLOW5 run-info partition")
        slow5 = _slow5_open(
            blow5_path,
            index_path=index_path,
            temporary_root=args.routing.parent,
        )
        source_identity = getattr(slow5, "source_identity", None)
        index_identity = getattr(slow5, "index_identity", None)
        if not isinstance(source_identity, dict) or not isinstance(index_identity, dict):
            raise ValueError("BLOW5 scientific reader did not retain output identity authority")
        unit_ids: list[str] = []
        try:
            for record in slow5.seq_reads(pA=False, aux="all"):
                missing = required.difference(record)
                if missing:
                    raise ValueError(f"BLOW5 record lacks required fields: {sorted(missing)}")
                read_id = str(record["read_id"])
                seen[read_id] += 1
                source = pod5_records.get(read_id)
                if source is None:
                    raise ValueError("BLOW5 contains a read ID outside the admitted POD5 scope")
                if source["group"] != fingerprint:
                    raise ValueError("BLOW5 record is in the wrong complete-run-info partition")
                signal = np.asarray(record["signal"], dtype=np.int16)
                if (
                    int(record["len_raw_signal"]) != len(signal)
                    or len(signal) != source["signal_length"]
                    or hashlib.sha256(signal.tobytes(order="C")).hexdigest() != source["signal_sha256"]
                ):
                    raise ValueError(f"raw signal mismatch for read {read_id}")
                checks = {
                    "start_time": (int(record["start_time"]), source["start_time"]),
                    "read_number": (int(record["read_number"]), source["read_number"]),
                    "channel_number": (int(record["channel_number"]), source["channel_number"]),
                    "start_mux": (int(record["start_mux"]), source["start_mux"]),
                    "sampling_rate": (int(record["sampling_rate"]), source["sampling_rate"]),
                    "num_minknow_events": (int(record["num_minknow_events"]), source["num_minknow_events"]),
                    "num_reads_since_mux_change": (
                        int(record["num_reads_since_mux_change"]),
                        source["num_reads_since_mux_change"],
                    ),
                }
                if any(observed != expected for observed, expected in checks.values()):
                    raise ValueError(f"integer metadata mismatch for read {read_id}")
                floats = {
                    "offset": float(record["offset"]),
                    "scale": float(record["range"]) / float(record["digitisation"]),
                    "median_before": float(record["median_before"]),
                    "tracked_scaling_shift": float(record["tracked_scaling_shift"]),
                    "tracked_scaling_scale": float(record["tracked_scaling_scale"]),
                    "predicted_scaling_shift": float(record["predicted_scaling_shift"]),
                    "predicted_scaling_scale": float(record["predicted_scaling_scale"]),
                    "time_since_mux_change": float(record["time_since_mux_change"]),
                    "open_pore_level": float(record["open_pore_level"]),
                }
                if any(not _equal_float(observed, source[field]) for field, observed in floats.items()):
                    raise ValueError(f"floating-point metadata mismatch for read {read_id}")
                unit_ids.append(read_id)
                read_to_group[read_id] = fingerprint
                observed_group_counts[fingerprint] += 1
                total_samples += len(signal)
            for read_id in unit_ids:
                indexed = slow5.get_read(read_id, pA=False, aux="all")
                if indexed is None or str(indexed.get("read_id")) != read_id:
                    raise ValueError(f"BLOW5 index lookup failed for read {read_id}")
                lookup_count += 1
        finally:
            slow5.close()
        groups[fingerprint] = {
            "blow5": blow5_path.name,
            "index": index_path.name,
            "read_count": len(unit_ids),
        }
        output_identities[fingerprint] = {
            "blow5": dict(source_identity),
            "index": dict(index_identity),
        }
    duplicate_count = sum(count - 1 for count in seen.values() if count > 1)
    if duplicate_count:
        raise ValueError(f"duplicate BLOW5 read IDs: {duplicate_count}")
    if set(seen) != set(pod5_records):
        raise ValueError("POD5 and BLOW5 read-ID multisets differ")
    if observed_group_counts != source_group_counts:
        raise ValueError("published BLOW5 partition counts differ from source run-info groups")
    _verify_inputs(args)
    routing = {
        "schema": "bms.ont.raw-signal-routing.v1",
        "partition_contract": "complete_typed_run_info_repr_sha256_pod5_0.3.35",
        "groups": groups,
        "read_to_group": read_to_group,
    }
    _write(args.routing, routing)
    return {
        "schema": "bms.ont.raw-signal-semantic-validation.v2",
        "status": "passed",
        "read_count": len(seen),
        "duplicate_read_ids": 0,
        "acquisition_ids": sorted(acquisitions),
        "partition_count": len(groups),
        "partition_counts": dict(sorted(observed_group_counts.items())),
        "output_identities": output_identities,
        "total_signal_samples_compared": total_samples,
        "signal_samples": "bit_exact_int16",
        "mapping_contract": "verified_signal_and_full_common_field_contract_exact",
        "source_only_authority": "immutable_parent_pod5_manifest",
        "pod5_archival_equivalence": False,
        "index_opened": True,
        "indexed_lookup_count": lookup_count,
        "routing_sha256": hashlib.sha256(args.routing.read_bytes()).hexdigest(),
    }


def _open_verified_external_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    expected_device: int,
    expected_inode: int,
    expected_mtime_ns: int,
    expected_ctime_ns: int,
    expected_root_device: int,
    expected_root_inode: int,
    label: str,
) -> tuple[int, dict[str, Any]]:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    root_fd = -1
    try:
        root_fd = os.open(
            path.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        root_info = os.fstat(root_fd)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or (root_info.st_dev, root_info.st_ino) != (expected_root_device, expected_root_inode)
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (expected_device, expected_inode, expected_size, expected_mtime_ns, expected_ctime_ns)
        ):
            raise ValueError(f"{label} descriptor identity diverged")
        digest = hashlib.sha256()
        os.lseek(descriptor, 0, os.SEEK_SET)
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or digest.hexdigest() != expected_sha256
        ):
            raise ValueError(f"{label} bytes diverged from registration manifest")
        return descriptor, {
            "sha256": digest.hexdigest(), "bytes": after.st_size, "device": after.st_dev,
            "inode": after.st_ino, "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns,
            "root_device": root_info.st_dev, "root_inode": root_info.st_ino,
        }
    except BaseException:
        os.close(descriptor)
        raise
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _verified_external_descriptor_identity(
    file_descriptor: int,
    root_descriptor: int,
    *,
    expected: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    root_info = os.fstat(root_descriptor)
    before = os.fstat(file_descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or (root_info.st_dev, root_info.st_ino)
        != (expected["root_device"], expected["root_inode"])
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        != (
            expected["device"],
            expected["inode"],
            expected["bytes"],
            expected["mtime_ns"],
            expected["ctime_ns"],
        )
    ):
        raise ValueError(f"{label} descriptor identity diverged")
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(file_descriptor, 1024 * 1024):
        digest.update(chunk)
    after = os.fstat(file_descriptor)
    identity = {
        "sha256": digest.hexdigest(),
        "bytes": after.st_size,
        "device": after.st_dev,
        "inode": after.st_ino,
        "mtime_ns": after.st_mtime_ns,
        "ctime_ns": after.st_ctime_ns,
        "root_device": root_info.st_dev,
        "root_inode": root_info.st_ino,
    }
    if identity != expected or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError(f"{label} bytes diverged from registration manifest")
    return identity


def _copy_attested_external_file(
    source_fd: int, expected: dict[str, Any], destination: Path, label: str
) -> dict[str, Any]:
    destination_fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{label} must be a regular file")
        os.lseek(source_fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(source_fd, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        after = os.fstat(source_fd)
        actual = {
            "sha256": digest.hexdigest(), "bytes": size, "device": after.st_dev,
            "inode": after.st_ino, "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns,
            "root_device": expected["root_device"], "root_inode": expected["root_inode"],
        }
        if actual != expected or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError(f"{label} changed during snapshot")
        os.fsync(destination_fd)
        if os.fstat(destination_fd).st_size != size:
            raise ValueError(f"{label} snapshot size mismatch")
        return actual
    finally:
        os.close(destination_fd)


def _verify_external_file(path: Path, **kwargs: Any) -> dict[str, Any]:
    descriptor, identity = _open_verified_external_file(path, **kwargs)
    os.close(descriptor)
    return identity


def _external_expected(args: argparse.Namespace, prefix: str) -> dict[str, Any]:
    return {
        "sha256": getattr(args, f"expected_{prefix}_sha256"),
        "bytes": getattr(args, f"expected_{prefix}_size"),
        "device": getattr(args, f"expected_{prefix}_device"),
        "inode": getattr(args, f"expected_{prefix}_inode"),
        "mtime_ns": getattr(args, f"expected_{prefix}_mtime_ns"),
        "ctime_ns": getattr(args, f"expected_{prefix}_ctime_ns"),
        "root_device": getattr(args, f"expected_{prefix}_root_device"),
        "root_inode": getattr(args, f"expected_{prefix}_root_inode"),
    }


def external_blow5_validate(args: argparse.Namespace) -> dict[str, Any]:
    received_files = getattr(args, "external_file_fds", [])
    if received_files:
        root_fds = getattr(args, "external_root_fds", [])
        if len(received_files) != 2 or len(root_fds) != 2:
            raise ValueError("external descriptor authority is incomplete")
        expected_blow5 = _external_expected(args, "blow5")
        expected_index = _external_expected(args, "index")
        blow5_identity = _verified_external_descriptor_identity(
            received_files[0], root_fds[0], expected=expected_blow5, label="external BLOW5 artifact"
        )
        index_identity = _verified_external_descriptor_identity(
            received_files[1], root_fds[1], expected=expected_index, label="external BLOW5 index"
        )
        receipt_value = getattr(args, "receipt", None)
        temporary_root = Path(str(receipt_value)).parent if receipt_value is not None else None
        slow5 = _slow5_open(
            args.blow5,
            expected=blow5_identity,
            expected_index=index_identity,
            temporary_root=temporary_root,
            source_fd=received_files[0],
            index_fd=received_files[1],
        )
    else:
        blow5_identity = _verify_external_file(
            args.blow5,
            expected_sha256=args.expected_blow5_sha256,
            expected_size=args.expected_blow5_size,
            expected_device=args.expected_blow5_device,
            expected_inode=args.expected_blow5_inode,
            expected_mtime_ns=args.expected_blow5_mtime_ns,
            expected_ctime_ns=args.expected_blow5_ctime_ns,
            expected_root_device=args.expected_blow5_root_device,
            expected_root_inode=args.expected_blow5_root_inode,
            label="external BLOW5 artifact",
        )
        index_identity = _verify_external_file(
            args.index,
            expected_sha256=args.expected_index_sha256,
            expected_size=args.expected_index_size,
            expected_device=args.expected_index_device,
            expected_inode=args.expected_index_inode,
            expected_mtime_ns=args.expected_index_mtime_ns,
            expected_ctime_ns=args.expected_index_ctime_ns,
            expected_root_device=args.expected_index_root_device,
            expected_root_inode=args.expected_index_root_inode,
            label="external BLOW5 index",
        )
        receipt_value = getattr(args, "receipt", None)
        temporary_root = Path(str(receipt_value)).parent if receipt_value is not None else None
        slow5 = _slow5_open(
            args.blow5,
            index_path=args.index,
            expected=blow5_identity,
            expected_index=index_identity,
            temporary_root=temporary_root,
        )
    seen: Counter[str] = Counter()
    required = {"read_id", "signal", "len_raw_signal", "digitisation", "offset", "range", "sampling_rate"}
    try:
        for record in slow5.seq_reads(pA=False, aux="all"):
            missing = required.difference(record)
            if missing:
                raise ValueError(f"BLOW5 record lacks required fields: {sorted(missing)}")
            read_id = str(record["read_id"])
            seen[read_id] += 1
            if int(record["len_raw_signal"]) != len(record["signal"]):
                raise ValueError(f"BLOW5 signal length mismatch for read {read_id}")
        duplicate_count = sum(count - 1 for count in seen.values() if count > 1)
        if duplicate_count:
            raise ValueError(f"duplicate BLOW5 read IDs: {duplicate_count}")
        for read_id in sorted(seen):
            indexed = slow5.get_read(read_id, pA=False, aux="all")
            if indexed is None or str(indexed.get("read_id")) != read_id:
                raise ValueError(f"BLOW5 index lookup failed for read {read_id}")
    finally:
        slow5.close()
    return {
        "schema": "bms.ont.external-blow5-validation.v2",
        "status": "passed",
        "read_count": len(seen),
        "duplicate_read_ids": 0,
        "index_opened": True,
        "indexed_lookup_count": len(seen),
        "pod5_parity_claimed": False,
        "ancestry": "external_native_without_pod5_parent",
        "blow5_identity": blow5_identity,
        "blow5_index_identity": index_identity,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode")
    def add_source_authority(target: argparse.ArgumentParser) -> None:
        target.add_argument("--pod5", action="append", type=Path, required=True)
        target.add_argument("--governed-root", action="append", type=Path, required=True)
        target.add_argument("--expected-root-device", action="append", type=int, required=True)
        target.add_argument("--expected-root-inode", action="append", type=int, required=True)
        target.add_argument("--expected-sha256", action="append", required=True)
        target.add_argument("--expected-size", action="append", type=int, required=True)
        target.add_argument("--expected-device", action="append", type=int, required=True)
        target.add_argument("--expected-inode", action="append", type=int, required=True)
        target.add_argument("--expected-mtime-ns", action="append", type=int, required=True)
        target.add_argument("--expected-ctime-ns", action="append", type=int, required=True)
        target.add_argument("--fd-socket", type=Path)

    source = subparsers.add_parser("source-preflight")
    add_source_authority(source)
    source.add_argument("--expected-acquisition-id", required=True)
    source.add_argument("--partition-map", type=Path, required=True)
    source.add_argument("--receipt", type=Path, required=True)
    external = subparsers.add_parser("external-blow5")
    external.add_argument("--blow5", type=Path, required=True)
    external.add_argument("--index", type=Path, required=True)
    for prefix in ("blow5", "index"):
        external.add_argument(f"--expected-{prefix}-sha256", required=True)
        external.add_argument(f"--expected-{prefix}-size", type=int, required=True)
        external.add_argument(f"--expected-{prefix}-device", type=int, required=True)
        external.add_argument(f"--expected-{prefix}-inode", type=int, required=True)
        external.add_argument(f"--expected-{prefix}-mtime-ns", type=int, required=True)
        external.add_argument(f"--expected-{prefix}-ctime-ns", type=int, required=True)
        external.add_argument(f"--expected-{prefix}-root-device", type=int, required=True)
        external.add_argument(f"--expected-{prefix}-root-inode", type=int, required=True)
    external.add_argument("--fd-socket", type=Path)
    external.add_argument("--receipt", type=Path, required=True)
    partition = subparsers.add_parser("partition-pod5")
    add_source_authority(partition)
    partition.add_argument("--table", type=Path, required=True)
    partition.add_argument("--read-id-column", required=True)
    partition.add_argument("--columns", nargs="+", required=True)
    partition.add_argument("--output", type=Path, required=True)
    partition.add_argument("--template", required=True)
    partition.add_argument("--threads", type=int, required=True)
    partition.add_argument("--missing-ok", action="store_true")
    partition.add_argument("--receipt", type=Path, required=True)
    semantic = subparsers.add_parser("semantic-dataset")
    add_source_authority(semantic)
    semantic.add_argument("--blow5", action="append", type=Path, required=True)
    semantic.add_argument("--index", action="append", type=Path, required=True)
    semantic.add_argument("--routing", type=Path, required=True)
    semantic.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    args.received_fds = []
    args.external_root_fds = []
    args.external_file_fds = []

    receipt = args.receipt
    try:
        if args.mode in {"source-preflight", "partition-pod5", "semantic-dataset"} and args.fd_socket:
            args.received_fds = _receive_source_descriptors(args)
        elif args.mode == "external-blow5" and args.fd_socket:
            args.received_fds = _receive_external_descriptors(args)
        if args.mode == "source-preflight":
            payload = source_preflight(args)
        elif args.mode == "external-blow5":
            if not args.external_file_fds and not args.index.is_file():
                raise ValueError("external BLOW5 validation requires an adjacent index")
            payload = external_blow5_validate(args)
        elif args.mode == "partition-pod5":
            payload = partition_pod5(args)
        elif args.mode == "semantic-dataset":
            payload = semantic_validate(args)
        else:
            raise ValueError("a raw-signal validation mode is required")
        _write(receipt, payload)
        return 0
    except Exception as exc:
        if receipt is not None:
            _write(receipt, {"schema": "bms.ont.raw-signal-validation-failure.v1", "status": "failed", "error_type": type(exc).__name__})
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        for descriptor in args.received_fds:
            os.close(descriptor)



if __name__ == "__main__":
    raise SystemExit(main())
