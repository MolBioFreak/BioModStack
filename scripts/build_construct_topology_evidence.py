#!/usr/bin/env python3
"""Derive fail-closed circular-topology evidence from BAM and dimer tables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

CIGAR_TOKEN = re.compile(r"(\d+)([MIDNSHP=X])")
REFERENCE_CONSUMING = frozenset("MDN=X")
QUERY_CONSUMING = frozenset("MIS=X")
QUERY_LENGTH_CONSUMING = frozenset("MIS=XH")
MIN_SPLIT_MAPQ = 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_tsv_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [
            {str(key).strip(): str(value or "").strip() for key, value in row.items() if key is not None}
            for row in reader
        ]


def reference_length_from_fasta(path: Path) -> int:
    records = 0
    chunks: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                records += 1
                if records > 1:
                    raise ValueError("reference FASTA must contain exactly one record")
                continue
            if records != 1:
                raise ValueError("reference FASTA sequence appears before header")
            chunks.append(line)
    if records != 1 or not chunks:
        raise ValueError("reference FASTA must contain one non-empty record")
    return len("".join(chunks))


def cigar_reference_length(cigar: str) -> int:
    if cigar in {"", "*"}:
        return 0
    tokens = CIGAR_TOKEN.findall(cigar)
    if not tokens or "".join(f"{length}{op}" for length, op in tokens) != cigar:
        raise ValueError(f"malformed CIGAR: {cigar!r}")
    return sum(int(length) for length, op in tokens if op in REFERENCE_CONSUMING)


def alignment_segment(flag: int, start: int, mapq: int, cigar: str) -> dict[str, Any]:
    tokens = [(int(length), op) for length, op in CIGAR_TOKEN.findall(cigar)]
    if not tokens or "".join(f"{length}{op}" for length, op in tokens) != cigar:
        raise ValueError(f"malformed CIGAR: {cigar!r}")
    reference_span = sum(length for length, op in tokens if op in REFERENCE_CONSUMING)
    query_length = sum(length for length, op in tokens if op in QUERY_LENGTH_CONSUMING)
    aligned_query_length = sum(length for length, op in tokens if op in QUERY_CONSUMING and op != "S")
    leading_clip = tokens[0][0] if tokens[0][1] in {"S", "H"} else 0
    trailing_clip = tokens[-1][0] if tokens[-1][1] in {"S", "H"} else 0
    query_start = leading_clip
    query_end = query_length - trailing_clip
    reverse = bool(flag & 0x10)
    if reverse:
        query_start, query_end = query_length - query_end, query_length - query_start
    return {
        "reference_start": start,
        "reference_end": start + reference_span - 1,
        "query_start": query_start,
        "query_end": query_end,
        "query_length": query_length,
        "aligned_query_length": aligned_query_length,
        "clipped": leading_clip > 0 or trailing_clip > 0,
        "supplementary": bool(flag & 0x800),
        "reverse": reverse,
        "mapq": mapq,
    }


def valid_origin_wrap_pair(
    first: dict[str, Any],
    second: dict[str, Any],
    *,
    reference_length: int,
    edge: int,
) -> bool:
    if first["supplementary"] == second["supplementary"]:
        return False
    if first["reverse"] != second["reverse"] or min(first["mapq"], second["mapq"]) < MIN_SPLIT_MAPQ:
        return False
    if not first["clipped"] or not second["clipped"] or first["query_length"] != second["query_length"]:
        return False
    left, right = sorted((first, second), key=lambda segment: (segment["query_start"], segment["query_end"]))
    if not (
        left["query_start"] == 0
        and left["query_end"] == right["query_start"]
        and right["query_end"] == left["query_length"]
        and left["query_end"] > left["query_start"]
        and right["query_end"] > right["query_start"]
    ):
        return False
    start_segments = [segment for segment in (first, second) if segment["reference_start"] <= edge + 1]
    end_segments = [segment for segment in (first, second) if segment["reference_end"] >= reference_length - edge]
    if len(start_segments) != 1 or len(end_segments) != 1 or start_segments[0] is end_segments[0]:
        return False
    start_segment = start_segments[0]
    end_segment = end_segments[0]
    if first["reverse"]:
        return start_segment["query_start"] < end_segment["query_start"]
    return end_segment["query_start"] < start_segment["query_start"]


def valid_origin_wrap_read_count(
    segments_by_read: dict[str, list[dict[str, Any]]],
    *,
    reference_length: int,
    edge: int,
) -> int:
    count = 0
    for segments in segments_by_read.values():
        if any(
            valid_origin_wrap_pair(
                segments[first_index],
                segments[second_index],
                reference_length=reference_length,
                edge=edge,
            )
            for first_index in range(len(segments))
            for second_index in range(first_index + 1, len(segments))
        ):
            count += 1
    return count


def int_field(row: dict[str, str], *keys: str) -> int:
    for key in keys:
        raw = row.get(key)
        if raw not in {None, ""}:
            token = str(raw).strip()
            if not re.fullmatch(r"(?:0|[1-9][0-9]*)", token):
                raise ValueError(f"{key} must be a non-negative decimal integer; got {raw!r}")
            return int(token)
    return 0


def false_like(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in {"0", "false", "no", "n", "outside"}


def breakpoint_is_contradictory(row: dict[str, str]) -> bool:
    status = str(row.get("breakpoint_status") or row.get("status") or "").strip().lower()
    confidence = str(row.get("confidence") or row.get("breakpoint_confidence") or "").strip().lower()
    in_boundary = row.get("primary_breakpoint_in_boundary_window")
    if in_boundary is None:
        in_boundary = row.get("in_boundary_window")
    boundary_artifact = str(row.get("boundary_artifact_suspected") or "").strip().lower()
    split_supported = "split" in status and status not in {"split_detected_unresolved"}
    credible = confidence in {"high", "medium"} or status in {"split_supported", "provisional_split_supported"}
    non_boundary = false_like(in_boundary) or boundary_artifact in {"0", "false", "no"}
    return split_supported and credible and non_boundary


def derive_topology_evidence(
    *,
    reference_length: int,
    sam_rows: Iterable[str],
    breakpoint_rows: list[dict[str, str]],
    secondary_rows: list[dict[str, str]],
    edge_window_bp: int,
) -> dict[str, Any]:
    if reference_length <= 0:
        raise ValueError("reference_length must be positive")
    edge = max(1, min(edge_window_bp, max(1, reference_length // 2)))
    read_edges: dict[str, set[str]] = {}
    segments_by_read: dict[str, list[dict[str, Any]]] = {}
    alignment_records = 0
    for raw in sam_rows:
        line = str(raw).rstrip("\n")
        if not line or line.startswith("@"):
            continue
        fields = line.split("\t")
        if len(fields) < 6:
            continue
        qname, flag_raw, _rname, pos_raw, mapq_raw, cigar = fields[:6]
        try:
            flag = int(flag_raw)
            start = int(pos_raw)
            mapq = int(mapq_raw)
            segment = alignment_segment(flag, start, mapq, cigar)
        except ValueError:
            continue
        if flag & 0x4 or flag & 0x100 or segment["reference_end"] < start or start <= 0:
            continue
        end = int(segment["reference_end"])
        edges = read_edges.setdefault(qname, set())
        segments_by_read.setdefault(qname, []).append(segment)
        if start <= edge + 1:
            edges.add("start")
        if end >= reference_length - edge:
            edges.add("end")
        alignment_records += 1

    mapped_unique_reads = len(read_edges)
    if mapped_unique_reads == 0:
        return {
            "schema": "biomodstack.construct_topology_evidence.v1",
            "state": "unavailable",
            "reason": "NO_MAPPED_ALIGNMENT_EVIDENCE",
            "expected_topology": "circular",
            "evidence_basis": "primary_and_supplementary_alignment_edges_plus_dimer_screen",
            "origin_spanning_reads": 0,
            "mapped_unique_reads": 0,
            "alignment_records": 0,
            "secondary_anomaly_fraction": 0.0,
            "contradictory_breakpoint_evidence": False,
            "edge_window_bp": edge,
        }

    origin_spanning = valid_origin_wrap_read_count(
        segments_by_read,
        reference_length=reference_length,
        edge=edge,
    )
    secondary = secondary_rows[0] if secondary_rows else {}
    non_boundary_split = int_field(secondary, "non_boundary_split_reads")
    aligned_dimer_reads = int_field(secondary, "aligned_dimer_reads")
    anomaly_denominator = aligned_dimer_reads or mapped_unique_reads
    anomaly_fraction = non_boundary_split / anomaly_denominator if anomaly_denominator else 0.0
    contradictory = any(breakpoint_is_contradictory(row) for row in breakpoint_rows)

    return {
        "schema": "biomodstack.construct_topology_evidence.v1",
        "state": "present",
        "reason": None,
        "expected_topology": "circular",
        "evidence_basis": "primary_and_supplementary_alignment_edges_plus_dimer_screen",
        "origin_spanning_reads": origin_spanning,
        "mapped_unique_reads": mapped_unique_reads,
        "alignment_records": alignment_records,
        "secondary_anomaly_fraction": anomaly_fraction,
        "non_boundary_split_reads": non_boundary_split,
        "aligned_dimer_reads": aligned_dimer_reads,
        "contradictory_breakpoint_evidence": contradictory,
        "edge_window_bp": edge,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-fasta", type=Path, required=True)
    parser.add_argument("--alignment-bam", type=Path, required=True)
    parser.add_argument("--breakpoint-call", type=Path)
    parser.add_argument("--secondary-summary", type=Path)
    parser.add_argument(
        "--samtools-command",
        action="append",
        dest="samtools_command",
        help="one command-prefix argument; repeat for wrappers such as apptainer exec",
    )
    parser.add_argument("--edge-window-bp", type=int)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    reference_length = reference_length_from_fasta(args.reference_fasta)
    edge_window = args.edge_window_bp or min(100, max(10, reference_length // 50))
    samtools_prefix = list(args.samtools_command or ["samtools"])
    samtools_argv = [*samtools_prefix, "view", "-F", "260", str(args.alignment_bam)]
    completed = subprocess.run(
        samtools_argv,
        check=False,
        capture_output=True,
        text=True,
    )
    sam_rows = completed.stdout.splitlines() if completed.returncode == 0 else []
    evidence: dict[str, Any]
    try:
        evidence = derive_topology_evidence(
            reference_length=reference_length,
            sam_rows=sam_rows,
            breakpoint_rows=read_tsv_rows(args.breakpoint_call),
            secondary_rows=read_tsv_rows(args.secondary_summary),
            edge_window_bp=edge_window,
        )
    except ValueError as exc:
        evidence = {
            "schema": "biomodstack.construct_topology_evidence.v1",
            "state": "malformed",
            "reason": f"MALFORMED_NUMERIC_EVIDENCE: {exc}",
            "expected_topology": "circular",
            "evidence_basis": "primary_and_supplementary_alignment_edges_plus_dimer_screen",
        }
    evidence["provenance"] = {
        "reference_sha256": sha256_file(args.reference_fasta),
        "alignment_bam_sha256": sha256_file(args.alignment_bam),
        "breakpoint_call_sha256": sha256_file(args.breakpoint_call) if args.breakpoint_call and args.breakpoint_call.is_file() else None,
        "secondary_summary_sha256": sha256_file(args.secondary_summary) if args.secondary_summary and args.secondary_summary.is_file() else None,
        "samtools_command": [*samtools_prefix, "view", "-F", "260", "<alignment_bam>"],
        "samtools_returncode": completed.returncode,
        "samtools_stderr": completed.stderr.strip()[:1000] if completed.returncode else "",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
