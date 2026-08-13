#!/usr/bin/env python3
"""Fail-closed ONT POD5/BLOW5 identity and semantic validator."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pod5


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _pod5_records(paths: list[Path]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    records: dict[str, dict[str, Any]] = {}
    acquisitions: set[str] = set()
    counts: Counter[str] = Counter()
    for path in paths:
        with pod5.Reader(path) as reader:
            for read in reader.reads():
                read_id = str(read.read_id)
                counts[read_id] += 1
                acquisitions.add(str(read.run_info.acquisition_id))
                records[read_id] = {
                    "signal": np.asarray(read.signal, dtype=np.int16),
                    "start_sample": int(read.start_sample),
                    "read_number": int(read.read_number),
                    "channel": int(read.pore.channel),
                    "well": int(read.pore.well),
                    "calibration_offset": float(read.calibration.offset),
                    "calibration_scale": float(read.calibration.scale),
                    "sample_rate": int(read.run_info.sample_rate),
                }
    duplicates = sum(count - 1 for count in counts.values() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate POD5 read IDs: {duplicates}")
    return records, acquisitions


def _slow5_open(path: Path):
    try:
        import pyslow5  # type: ignore
    except ImportError as exc:
        raise ValueError("pyslow5 is required by the qualified validator runtime") from exc
    handle = pyslow5.Open(str(path), "r")
    if handle is None:
        raise ValueError("BLOW5 could not be opened")
    return handle


def source_preflight(args: argparse.Namespace) -> dict[str, Any]:
    records, acquisitions = _pod5_records(args.pod5)
    expected = str(args.expected_acquisition_id)
    if expected != "external-native" and acquisitions != {expected}:
        raise ValueError("POD5 acquisition_id does not match the bound MinKNOW acquisition")
    if len(acquisitions) != 1:
        raise ValueError("POD5 source spans more than one acquisition identity")
    return {
        "schema": "bms.ont.raw-signal-source-preflight.v1",
        "status": "passed",
        "acquisition_id": next(iter(acquisitions)),
        "read_count": len(records),
        "duplicate_read_ids": 0,
    }


def semantic_validate(args: argparse.Namespace) -> dict[str, Any]:
    pod5_records, acquisitions = _pod5_records(args.pod5)
    slow5 = _slow5_open(args.blow5)
    seen: Counter[str] = Counter()
    compared = 0
    required = {"read_id", "raw_signal", "len_raw_signal", "digitisation", "offset", "range", "sampling_rate"}
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
            signal = np.asarray(record["raw_signal"], dtype=np.int16)
            if int(record["len_raw_signal"]) != len(signal) or not np.array_equal(signal, source["signal"]):
                raise ValueError(f"raw signal mismatch for read {read_id}")
            scale = float(record["range"]) / float(record["digitisation"])
            checks = {
                "calibration_offset": (float(record["offset"]), source["calibration_offset"]),
                "calibration_scale": (scale, source["calibration_scale"]),
                "sample_rate": (int(record["sampling_rate"]), source["sample_rate"]),
            }
            aux = record.get("aux_data") or {}
            for field in ("start_sample", "read_number", "channel_number", "well"):
                if field not in aux:
                    raise ValueError(f"BLOW5 auxiliary field is absent: {field}")
            checks.update({
                "start_sample": (int(aux["start_sample"]), source["start_sample"]),
                "read_number": (int(aux["read_number"]), source["read_number"]),
                "channel": (int(aux["channel_number"]), source["channel"]),
                "well": (int(aux["well"]), source["well"]),
            })
            for field, (observed, expected) in checks.items():
                if not np.isclose(observed, expected, rtol=0.0, atol=1e-9):
                    raise ValueError(f"{field} mismatch for read {read_id}")
            compared += 1
        duplicate_count = sum(count - 1 for count in seen.values() if count > 1)
        if duplicate_count:
            raise ValueError(f"duplicate BLOW5 read IDs: {duplicate_count}")
        if set(seen) != set(pod5_records):
            raise ValueError("POD5 and BLOW5 read-ID multisets differ")
        for read_id in sorted(seen)[: min(64, len(seen))]:
            indexed = slow5.get_read(read_id, pA=False, aux="all")
            if indexed is None or str(indexed.get("read_id")) != read_id:
                raise ValueError(f"BLOW5 index lookup failed for read {read_id}")
    finally:
        slow5.close()
    return {
        "schema": "bms.ont.raw-signal-semantic-validation.v1",
        "status": "passed",
        "read_count": compared,
        "duplicate_read_ids": 0,
        "acquisition_ids": sorted(acquisitions),
        "signal_samples": "bit_exact_int16",
        "mapping_contract": "verified_signal_and_mapping_contract_exact",
        "preserved_fields": [
            "read_id", "raw_signal", "len_raw_signal", "start_sample", "read_number",
            "channel", "well", "calibration_offset", "calibration_scale", "sample_rate",
        ],
        "source_only_fields": [
            "pod5_run_info", "pod5_end_reason", "pod5_end_reason_forced", "pod5_pore_type",
            "pod5_median_before", "pod5_num_minknow_events", "pod5_tracked_scaling",
            "pod5_predicted_scaling", "pod5_num_reads_since_mux_change", "pod5_time_since_mux_change",
        ],
        "source_only_authority": "immutable_parent_pod5_manifest",
        "pod5_archival_equivalence": False,
        "index_opened": True,
        "indexed_lookup_count": min(64, compared),
    }


def external_blow5_validate(args: argparse.Namespace) -> dict[str, Any]:
    slow5 = _slow5_open(args.blow5)
    seen: Counter[str] = Counter()
    required = {"read_id", "raw_signal", "len_raw_signal", "digitisation", "offset", "range", "sampling_rate"}
    try:
        for record in slow5.seq_reads(pA=False, aux="all"):
            missing = required.difference(record)
            if missing:
                raise ValueError(f"BLOW5 record lacks required fields: {sorted(missing)}")
            read_id = str(record["read_id"])
            seen[read_id] += 1
            if int(record["len_raw_signal"]) != len(record["raw_signal"]):
                raise ValueError(f"BLOW5 signal length mismatch for read {read_id}")
        duplicate_count = sum(count - 1 for count in seen.values() if count > 1)
        if duplicate_count:
            raise ValueError(f"duplicate BLOW5 read IDs: {duplicate_count}")
        for read_id in sorted(seen)[: min(64, len(seen))]:
            indexed = slow5.get_read(read_id, pA=False, aux="all")
            if indexed is None or str(indexed.get("read_id")) != read_id:
                raise ValueError(f"BLOW5 index lookup failed for read {read_id}")
    finally:
        slow5.close()
    return {
        "schema": "bms.ont.external-blow5-validation.v1",
        "status": "passed",
        "read_count": len(seen),
        "duplicate_read_ids": 0,
        "index_opened": True,
        "indexed_lookup_count": min(64, len(seen)),
        "pod5_parity_claimed": False,
        "ancestry": "external_native_without_pod5_parent",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode")
    source = subparsers.add_parser("source-preflight")
    source.add_argument("--pod5", action="append", type=Path, required=True)
    source.add_argument("--expected-acquisition-id", required=True)
    source.add_argument("--receipt", type=Path, required=True)
    external = subparsers.add_parser("external-blow5")
    external.add_argument("--blow5", type=Path, required=True)
    external.add_argument("--index", type=Path, required=True)
    external.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--pod5", action="append", type=Path)
    parser.add_argument("--blow5", type=Path)
    parser.add_argument("--index", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    receipt = args.receipt
    try:
        if args.mode == "source-preflight":
            payload = source_preflight(args)
        elif args.mode == "external-blow5":
            if not args.index.is_file():
                raise ValueError("external BLOW5 validation requires an adjacent index")
            payload = external_blow5_validate(args)
        else:
            if not args.pod5 or not args.blow5 or not args.index or not args.index.is_file() or receipt is None:
                raise ValueError("semantic validation requires POD5, BLOW5, adjacent index, and receipt")
            payload = semantic_validate(args)
        _write(receipt, payload)
        return 0
    except Exception as exc:
        if receipt is not None:
            _write(receipt, {"schema": "bms.ont.raw-signal-validation-failure.v1", "status": "failed", "error_type": type(exc).__name__})
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
