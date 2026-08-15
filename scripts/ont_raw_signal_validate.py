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
import sys
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


def _slow5_open(path: Path):
    try:
        import pyslow5  # type: ignore
    except ImportError as exc:
        raise ValueError("pyslow5 is required by the qualified validator runtime") from exc
    handle = pyslow5.Open(str(path), "r")
    if handle is None:
        raise ValueError("BLOW5 could not be opened")
    return handle


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
    for blow5_path, index_path in zip(args.blow5, args.index, strict=True):
        if index_path != Path(f"{blow5_path}.idx") or not index_path.is_file():
            raise ValueError("BLOW5 conversion unit lacks its adjacent index")
        fingerprint = blow5_path.name.removesuffix(".blow5")
        if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
            raise ValueError("BLOW5 unit name is not a run-info fingerprint")
        if fingerprint in groups:
            raise ValueError("duplicate BLOW5 run-info partition")
        slow5 = _slow5_open(blow5_path)
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
        "total_signal_samples_compared": total_samples,
        "signal_samples": "bit_exact_int16",
        "mapping_contract": "verified_signal_and_full_common_field_contract_exact",
        "source_only_authority": "immutable_parent_pod5_manifest",
        "pod5_archival_equivalence": False,
        "index_opened": True,
        "indexed_lookup_count": lookup_count,
        "routing_sha256": hashlib.sha256(args.routing.read_bytes()).hexdigest(),
    }


def external_blow5_validate(args: argparse.Namespace) -> dict[str, Any]:
    slow5 = _slow5_open(args.blow5)
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

    receipt = args.receipt
    try:
        if args.mode in {"source-preflight", "partition-pod5", "semantic-dataset"} and args.fd_socket:
            args.received_fds = _receive_source_descriptors(args)
        if args.mode == "source-preflight":
            payload = source_preflight(args)
        elif args.mode == "external-blow5":
            if not args.index.is_file():
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
