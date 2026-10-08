#!/usr/bin/env python3
"""Closed, offline Squigualiser runtime entrypoint used only by the BMS leased worker."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pysam
import pyslow5

MAX_STDERR = 64 * 1024
EXTERNAL_URL = re.compile(rb"(?:https?|wss?)://", re.IGNORECASE)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write_json(path: Path, value: Any) -> None:
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def run(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    receipt = {
        "argv_sha256": hashlib.sha256("\0".join(command).encode()).hexdigest(),
        "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stdout_tail": completed.stdout[-MAX_STDERR:].decode("utf-8", "replace"),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
        "stderr_tail": completed.stderr[-MAX_STDERR:].decode("utf-8", "replace"),
    }
    if completed.returncode != 0:
        raise RuntimeError(receipt["stderr_tail"] or "runtime command failed")
    return receipt


def selected_read_fastq(bam_path: Path, read_id: str, output_path: Path) -> None:
    """Materialize one governed basecalled sequence for Squigualiser read plotting."""
    found = 0
    with pysam.AlignmentFile(str(bam_path), "rb", check_sq=False) as bam, output_path.open("w", encoding="utf-8") as handle:
        for record in bam.fetch(until_eof=True):
            if record.query_name != read_id:
                continue
            sequence = record.query_sequence or ""
            if not sequence:
                raise ValueError(f"selected read has no basecalled sequence: {read_id}")
            quality = record.qual if record.qual and len(record.qual) == len(sequence) else "I" * len(sequence)
            handle.write(f"@{read_id}\n{sequence}\n+\n{quality}\n")
            found += 1
    if found != 1:
        raise ValueError(f"selected read must resolve to one governed basecall record: {read_id}")


def reads_overlapping_region(mapping: Path, region: str, limit: int) -> list[str]:
    match = re.fullmatch(r"([^:]+):(\d+)-(\d+)", region)
    if match is None:
        raise ValueError("reference region must be contig:start-end")
    contig, start_text, end_text = match.groups()
    start, end = int(start_text), int(end_text)
    if start < 1 or end < start:
        raise ValueError("reference region is invalid")
    selected: list[str] = []
    seen: set[str] = set()
    with mapping.open("r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 12 or fields[5] != contig:
                continue
            target_start, target_end = int(fields[7]) + 1, int(fields[8])
            if target_end < start or target_start > end or fields[0] in seen:
                continue
            selected.append(fields[0])
            seen.add(fields[0])
            if len(selected) >= limit:
                break
    if not selected:
        raise ValueError("no governed signal-aligned reads overlap the requested region")
    return selected


def bounded_blow5(parents: list[Path], read_ids: list[str], work_dir: Path) -> tuple[Path, list[dict[str, Any]]]:
    """Extract only bounded requested reads, then merge partitions for one plot input."""
    read_list = work_dir / "bounded_read_ids.txt"
    read_list.write_text("".join(f"{read_id}\n" for read_id in read_ids), encoding="utf-8")
    subsets: list[Path] = []
    receipts: list[dict[str, Any]] = []
    for index, parent in enumerate(parents):
        subset = work_dir / f"subset-{index}.blow5"
        receipts.append(run([
            "slow5tools", "get", "--skip", "--to", "blow5", "-l", str(read_list),
            "-o", str(subset), str(parent),
        ]))
        subsets.append(subset)
    merged = work_dir / "bounded.blow5"
    receipts.append(run(["slow5tools", "merge", "-o", str(merged), *map(str, subsets)]))
    receipts.append(run(["slow5tools", "index", str(merged)]))
    available = set(blow5_ids([merged])[0])
    if available != set(read_ids):
        raise ValueError("bounded BLOW5 extraction did not preserve the exact requested read set")
    return merged, receipts


def blow5_ids(paths: list[Path]) -> tuple[list[str], dict[str, str]]:
    identities: dict[str, str] = {}
    read_ids: list[str] = []
    for path in paths:
        identities[path.name] = sha(path)
        slow5 = pyslow5.Open(str(path), "r")
        ids = list(slow5.get_read_ids())
        slow5.close()
        read_ids.extend(str(value) for value in ids)
    if not read_ids or len(read_ids) != len(set(read_ids)):
        raise ValueError("BLOW5 routing parents contain no reads or duplicate read IDs")
    return sorted(read_ids), identities


def model_from_header(header: dict[str, Any]) -> str:
    models: set[str] = set()
    for read_group in header.get("RG", []):
        text = " ".join(str(read_group.get(key, "")) for key in ("DS", "PM", "PU"))
        for match in re.findall(r"(?:basecall_model|model)[=: ]+([A-Za-z0-9_.@-]+)", text, re.IGNORECASE):
            models.add(match)
    if len(models) != 1:
        raise ValueError("BAM @RG metadata does not identify one coherent basecall model")
    return next(iter(models))


def cmd_validate_moves(args: argparse.Namespace) -> None:
    raw_ids, blow5_sha = blow5_ids(args.blow5)
    raw_set = set(raw_ids)
    input_bam = pysam.AlignmentFile(str(args.bam), "rb", check_sq=False)
    header = input_bam.header.to_dict()
    header_sha = hashlib.sha256(str(input_bam.header).encode()).hexdigest()
    model_id = model_from_header(header)
    output_bam = pysam.AlignmentFile(str(args.filtered_bam), "wb", header=input_bam.header)
    seen: set[str] = set()
    included: set[str] = set()
    counts = {"records": 0, "mv": 0, "ts": 0, "ns": 0, "excluded_bam_only": 0}
    try:
        for record in input_bam.fetch(until_eof=True):
            counts["records"] += 1
            read_id = record.query_name
            if not read_id or read_id in seen:
                raise ValueError("move BAM contains an empty or duplicate read ID")
            seen.add(read_id)
            if not record.query_sequence:
                raise ValueError(f"move BAM read lacks sequence: {read_id}")
            tags = dict(record.get_tags())
            for name in ("mv", "ts", "ns"):
                if name not in tags:
                    raise ValueError(f"move BAM read lacks {name}: {read_id}")
                counts[name] += 1
            moves = list(tags["mv"])
            if not moves or int(moves[0]) <= 0 or int(tags["ts"]) < 0 or int(tags["ns"]) <= int(tags["ts"]):
                raise ValueError(f"move BAM read has malformed signal tags: {read_id}")
            if read_id in raw_set:
                included.add(read_id)
                output_bam.write(record)
            else:
                counts["excluded_bam_only"] += 1
    finally:
        output_bam.close()
        input_bam.close()
    missing = raw_set - included
    if missing:
        raise ValueError(f"BLOW5 reads missing from move BAM: {len(missing)}")
    inventory_bytes = "".join(f"{read_id}\n" for read_id in raw_ids).encode()
    args.inventory.write_bytes(inventory_bytes)
    result = {
        "schema": "bms.ont-move-source-validation.v1",
        "move_bam_sha256": sha(args.bam),
        "move_bam_header_sha256": header_sha,
        "basecall_model_id": model_id,
        "molecule_type": args.molecule_type,
        "record_count": counts["records"],
        "unique_read_count": len(seen),
        "tag_counts": {key: counts[key] for key in ("mv", "ts", "ns")},
        "included_read_count": len(included),
        "missing_blow5_read_count": 0,
        "excluded_bam_only_read_count": counts["excluded_bam_only"],
        "read_inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "filtered_move_bam": {"sha256": sha(args.filtered_bam), "size_bytes": args.filtered_bam.stat().st_size},
        "blow5_parents": blow5_sha,
    }
    result["content_sha256"] = hashlib.sha256(canonical(result)).hexdigest()
    write_json(args.report, result)


def _calibration_score(sequence: str, signal: list[float], moves: list[int], kmer_length: int, move_offset: int) -> tuple[int, float]:
    if len(sequence) < kmer_length + move_offset or len(moves) < len(sequence):
        raise ValueError("calibration read is too short for the fixed candidate bound")
    start_raw = sum(moves[:move_offset])
    model: dict[str, float] = {}
    for index in range(0, len(sequence) - kmer_length + 1 - move_offset):
        end_raw = start_raw + moves[index + move_offset]
        kmer = sequence[index:index + kmer_length].upper()
        values = signal[start_raw:end_raw]
        if len(values) == 0 or not set(kmer) <= {"A", "C", "G", "T"}:
            raise ValueError("calibration sequence or move span is not closed DNA evidence")
        model[kmer] = float(statistics.median(values))
        start_raw = end_raw
    scores: list[float] = []
    for base_offset in range(kmer_length):
        groups = [[value for kmer, value in model.items() if kmer[base_offset] == base] for base in "ACGT"]
        if any(not group for group in groups):
            raise ValueError("calibration candidate lacks complete A/C/G/T score evidence")
        medians = [float(statistics.median(group)) for group in groups]
        score = max(medians) - min(medians)
        if not math.isfinite(score):
            raise ValueError("calibration score is non-finite")
        scores.append(score)
    best = max(range(kmer_length), key=lambda offset: scores[offset])
    return best, scores[best]


def cmd_calibrate(args: argparse.Namespace) -> None:
    if not 1 <= args.sample_count <= 100:
        raise ValueError("calibration sample count is outside bounded policy")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_ids, blow5_hashes = blow5_ids(args.blow5)
    raw_set = set(raw_ids)
    records: dict[str, pysam.AlignedSegment] = {}
    sequences: dict[str, str] = {}
    with pysam.AlignmentFile(str(args.filtered_bam), "rb", check_sq=False) as bam:
        header = bam.header
        for record in bam.fetch(until_eof=True):
            read_id = record.query_name
            if not read_id or read_id in records or not record.query_sequence:
                raise ValueError("filtered move BAM contains missing or duplicate calibration reads")
            records[read_id] = record
            sequences[read_id] = record.query_sequence
    if set(records) != raw_set:
        raise ValueError("filtered move BAM and governed BLOW5 intersection is incomplete")
    inventory_sha256 = hashlib.sha256("".join(f"{read_id}\n" for read_id in sorted(raw_set)).encode()).hexdigest()
    if inventory_sha256 != args.move_inventory_sha256:
        raise ValueError("exact signal/move intersection digest diverges from the governed inventory")
    ranked = sorted(raw_ids, key=lambda read_id: (hashlib.sha256(read_id.encode()).hexdigest(), read_id))
    if len(ranked) < args.sample_count:
        raise ValueError("exact signal/move intersection is smaller than requested calibration sample")
    selected = ranked[:args.sample_count]
    selection_digest = hashlib.sha256(canonical(selected)).hexdigest()
    sample_bam = args.output_dir / "sample.bam"
    sequence_fastq = args.output_dir / "sample.fastq"
    with pysam.AlignmentFile(str(sample_bam), "wb", header=header) as output_bam, sequence_fastq.open("w", encoding="utf-8") as fastq:
        for read_id in selected:
            record = records[read_id]
            output_bam.write(record)
            quality = record.qual if record.qual and len(record.qual) == len(sequences[read_id]) else "I" * len(sequences[read_id])
            fastq.write(f"@{read_id}\n{sequences[read_id]}\n+\n{quality}\n")
    with tempfile.TemporaryDirectory(prefix="bms-calibration-") as temporary:
        bounded, extraction = bounded_blow5(args.blow5, selected, Path(temporary))
        bounded_output = args.output_dir / "sample.blow5"
        shutil.copyfile(bounded, bounded_output)
        shutil.copyfile(Path(f"{bounded}.idx"), Path(f"{bounded_output}.idx"))
    baseline = args.output_dir / "baseline.paf"
    reform_receipt = run(["squigualiser", "reform", "--bam", str(sample_bam), "--output", str(baseline), "-c", "--kmer_length", "1", "--sig_move_offset", "0"])
    validate_paf(baseline, (b"ss:Z:",), set(selected))
    paf_rows: dict[str, tuple[int, list[int]]] = {}
    with baseline.open("r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            tags = [field[5:] for field in fields[12:] if field.startswith("ss:Z:")]
            if len(fields) < 12 or len(tags) != 1 or fields[0] in paf_rows:
                raise ValueError("baseline PAF calibration evidence is ambiguous")
            tokens = [token for token in re.split(r",+", tags[0]) if token]
            if not tokens or any(not token.isdigit() for token in tokens):
                raise ValueError("baseline PAF move evidence is unparseable")
            paf_rows[fields[0]] = (int(fields[2]), [int(token) for token in tokens])
    slow5 = pyslow5.Open(str(bounded_output), "r")
    evidence: list[dict[str, Any]] = []
    try:
        for move_offset in range(9):
            per_read: list[tuple[str, int, float]] = []
            for read_id in selected:
                read = slow5.get_read(read_id, pA=True)
                if read is None or read_id not in paf_rows:
                    raise ValueError("selected calibration read is missing signal or baseline mapping")
                start_raw, moves = paf_rows[read_id]
                signal = list(read["signal"])[start_raw:]
                best_offset, score = _calibration_score(sequences[read_id], signal, moves, 9, move_offset)
                per_read.append((read_id, best_offset, score))
            median_index = sorted(range(len(per_read)), key=lambda index: per_read[index][1])[len(per_read) // 2]
            median_read, best_offset, score = per_read[median_index]
            evidence.append({"candidate_signal_move_offset": move_offset, "candidate_kmer_bound": 9, "median_best_base_offset": best_offset, "score": score, "median_read_id": median_read, "read_count": len(per_read)})
    finally:
        slow5.close()
    best_candidate = max(evidence, key=lambda item: item["score"])
    zero_candidates = [item for item in evidence if item["median_best_base_offset"] == 0]
    if not zero_candidates:
        raise ValueError("calibration score evidence has no zero-base-offset candidate")
    zero_candidate = zero_candidates[-1]
    if zero_candidate["candidate_signal_move_offset"] != best_candidate["candidate_signal_move_offset"] - best_candidate["median_best_base_offset"]:
        raise ValueError("calibration candidate evidence does not yield an unambiguous recommendation")
    independent_m = int(zero_candidate["candidate_signal_move_offset"])
    independent_k = independent_m + 1
    offset_receipt = run(["squigualiser", "calculate_offsets", "-k", "9", "-p", str(baseline), "-f", str(sequence_fastq), "-s", str(bounded_output), "--read_limit", str(args.sample_count)])
    stdout = offset_receipt["stdout_tail"]
    matches = re.findall(r"recommended kmer_length:(\d+) recommended sig_move_offset:(\d+)", stdout)
    if len(matches) != 1 or "please refer" in stdout.lower():
        raise ValueError("pinned calculate_offsets recommendation is ambiguous or unparseable")
    upstream_k, upstream_m = map(int, matches[0])
    if (upstream_k, upstream_m) != (independent_k, independent_m):
        raise ValueError("pinned and independently calculated recommendations are inconsistent")
    parent_hashes = {
        "raw_manifest_sha256": args.raw_manifest_sha256,
        "move_bam_sha256": args.move_artifact_sha256,
        "move_read_inventory_sha256": args.move_inventory_sha256,
        "filtered_move_bam_sha256": sha(args.filtered_bam),
        "blow5_partitions": blow5_hashes,
        "blow5_indexes": {Path(f"{path.name}.idx").name: sha(Path(f"{path}.idx")) for path in args.blow5},
        "sample_bam_sha256": sha(sample_bam),
        "sample_fastq_sha256": sha(sequence_fastq),
        "sample_blow5_sha256": sha(bounded_output),
        "baseline_paf_sha256": sha(baseline),
    }
    report = {
        "schema": "bms.ont-signal-calibration.v1",
        "basecall_model_id": args.basecall_model_id,
        "sample_selection": {"method": "sha256_read_id_rank_v1", "requested_count": args.sample_count, "selected_count": len(selected), "intersection_count": len(raw_set), "read_ids": selected, "selection_sha256": selection_digest},
        "parent_sha256s": parent_hashes,
        "baseline_paf_sha256": parent_hashes["baseline_paf_sha256"],
        "tool_identity": {"name": "squigualiser", "version": "0.7.0", "commit": "5a2404f1f43bc3227a85475c59b2b77970078b2e", "candidate_kmer_bound": 9},
        "recommendation": {"kmer_length": upstream_k, "signal_move_offset": upstream_m},
        "score_evidence": evidence,
        "validation": {"exact_intersection": True, "independent_recommendation_equal": True, "assumption_unambiguous": True},
        "commands": {"bounded_blow5": extraction, "baseline_reform": reform_receipt, "calculate_offsets": offset_receipt},
    }
    write_json(args.report, report)
    if args.report.stat().st_size > 1024 * 1024:
        args.report.unlink()
        raise ValueError("calibration report exceeds bounded JSON policy")


def validate_paf(path: Path, required_tags: tuple[bytes, ...], expected_ids: set[str] | None = None) -> dict[str, Any]:
    observed: set[str] = set()
    count = 0
    with path.open("rb") as handle:
        for line in handle:
            fields = line.rstrip(b"\n").split(b"\t")
            if len(fields) < 12 or any(not any(field.startswith(tag) for field in fields[12:]) for tag in required_tags):
                raise ValueError("Squigualiser output PAF structure or required tags are invalid")
            read_id = fields[0].decode("utf-8")
            if read_id in observed:
                raise ValueError("Squigualiser output contains duplicate read IDs")
            observed.add(read_id)
            count += 1
    if not observed or (expected_ids is not None and observed != expected_ids):
        raise ValueError("Squigualiser output read inventory diverges from governed parents")
    inventory = "".join(f"{value}\n" for value in sorted(observed)).encode()
    return {"record_count": count, "read_inventory_sha256": hashlib.sha256(inventory).hexdigest()}


def cmd_reform(args: argparse.Namespace) -> None:
    expected = set(args.inventory.read_text().splitlines())
    receipt = run([
        "squigualiser", "reform", "--bam", str(args.filtered_bam), "--output", str(args.output),
        "-c", "--kmer_length", str(args.kmer_length), "--sig_move_offset", str(args.signal_move_offset),
    ])
    validation = validate_paf(args.output, (b"ss:Z:",), expected)
    write_json(args.report, {"schema": "bms.ont-signal-reform.v1", **validation, "output_sha256": sha(args.output), "command": receipt})


def cmd_realign(args: argparse.Namespace) -> None:
    receipt = run([
        "squigualiser", "realign", "-c", "--paf", str(args.reform_paf), "--bam", str(args.alignment_bam),
        "--output", str(args.output),
    ])
    validation = validate_paf(args.output, (b"ss:Z:", b"si:Z:"))
    write_json(args.report, {"schema": "bms.ont-signal-realign.v1", **validation, "output_sha256": sha(args.output), "command": receipt})


def safe_render_artifacts(output_dir: Path, report: Path, command_receipt: dict[str, Any]) -> None:
    artifacts = []
    for path in sorted(output_dir.iterdir()):
        if path == report or not path.is_file() or path.is_symlink():
            continue
        suffix = path.suffix.lower()
        if suffix not in {".html", ".svg"}:
            continue
        limit = 8 * 1024 * 1024 if suffix == ".html" else 4 * 1024 * 1024
        raw = path.read_bytes()
        if suffix == ".html":
            csp = (
                b'<meta http-equiv="Content-Security-Policy" '
                b'content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; '
                b'img-src data: blob:; font-src data:; connect-src \'none\'; object-src \'none\'; '
                b'base-uri \'none\'; form-action \'none\'; frame-ancestors \'none\'">'
            )
            head = raw.lower().find(b"<head>")
            if head < 0:
                raise ValueError("rendered HTML lacks a head element for the enforced CSP")
            raw = raw[:head + len(b"<head>")] + csp + raw[head + len(b"<head>"):]
            path.write_bytes(raw)
        if not raw or len(raw) > limit or EXTERNAL_URL.search(raw) or b"/api/" in raw or b"file://" in raw:
            raise ValueError("render artifact violates bounded no-network sandbox policy")
        artifacts.append({
            "artifact_id": hashlib.sha256(raw).hexdigest()[:32], "filename": path.name,
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw),
            "media_type": "text/html" if suffix == ".html" else "image/svg+xml",
        })
    if not artifacts:
        raise ValueError("Squigualiser produced no bounded view artifact")
    write_json(report, {"schema": "bms.ont-squigualiser-render.v1", "artifacts": artifacts, "command": command_receipt, "network": "denied"})


def cmd_render(args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    extraction_receipts: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="bms-squigualiser-") as temporary:
        work_dir = Path(temporary)
        if args.mode == "read":
            if not args.read_id or args.sequence_bam is None or len(args.blow5) != 1:
                raise ValueError("read view requires one read ID, one routed BLOW5 partition, and its move BAM")
            sequence_file = work_dir / "selected.fastq"
            selected_read_fastq(args.sequence_bam, args.read_id, sequence_file)
            signal_file = args.blow5[0]
        else:
            if args.reference_fasta is None or not args.region:
                raise ValueError("reference views require one managed reference and a bounded region")
            sequence_file = args.reference_fasta
            read_ids = reads_overlapping_region(args.mapping, args.region, args.pileup_read_limit)
            signal_file, extraction_receipts = bounded_blow5(args.blow5, read_ids, work_dir)
        common = ["--file", str(sequence_file), "--slow5", str(signal_file), "--alignment", str(args.mapping), "--output_dir", str(args.output_dir)]
        if args.mode == "read":
            command = ["squigualiser", "plot", *common, "--read_id", args.read_id, "--plot_limit", "1"]
        elif args.mode == "reference":
            command = ["squigualiser", "plot", *common, "--region", args.region, "--plot_limit", str(args.pileup_read_limit), "--sig_ref"]
        else:
            command = ["squigualiser", "plot_pileup", *common, "--region", args.region, "--plot_limit", str(args.pileup_read_limit)]
        if args.strand == "reverse": command.append("--plot_reverse")
        if args.signal_units == "raw_adc": command.append("--no_pa")
        if args.scale != "none": command.extend(["--sig_scale", args.scale])
        command.extend(["--base_shift", str(args.base_shift), "--point_size", str(args.point_size), "--base_limit", str(args.base_limit), "--sig_plot_limit", str(args.signal_sample_limit)])
        if args.fixed_width: command.extend(["--fixed_width", "--base_width", str(args.base_width)])
        if args.loose_bound: command.append("--loose_bound")
        if not args.show_samples: command.append("--no_samples")
        if not args.show_base_colours: command.append("--no_colours")
        if args.remove_signal_outliers: command.append("--remove_signal_outliers")
        if args.bed is not None: command.extend(["--bed", str(args.bed)])
        receipt = run(command)
    receipt["bounded_blow5_extraction"] = extraction_receipts
    safe_render_artifacts(args.output_dir, args.report, receipt)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="operation", required=True)
    moves = sub.add_parser("validate-moves")
    moves.add_argument("--bam", type=Path, required=True); moves.add_argument("--blow5", type=Path, action="append", required=True)
    moves.add_argument("--molecule-type", choices=("dna", "rna"), required=True)
    moves.add_argument("--filtered-bam", type=Path, required=True); moves.add_argument("--inventory", type=Path, required=True); moves.add_argument("--report", type=Path, required=True)
    calibrate = sub.add_parser("calibrate")
    calibrate.add_argument("--filtered-bam", type=Path, required=True); calibrate.add_argument("--blow5", type=Path, action="append", required=True)
    calibrate.add_argument("--sample-count", type=int, required=True); calibrate.add_argument("--raw-manifest-sha256", required=True); calibrate.add_argument("--move-artifact-sha256", required=True); calibrate.add_argument("--move-inventory-sha256", required=True); calibrate.add_argument("--basecall-model-id", required=True)
    calibrate.add_argument("--output-dir", type=Path, required=True); calibrate.add_argument("--report", type=Path, required=True)
    reform = sub.add_parser("reform")
    reform.add_argument("--filtered-bam", type=Path, required=True); reform.add_argument("--inventory", type=Path, required=True); reform.add_argument("--output", type=Path, required=True); reform.add_argument("--report", type=Path, required=True)
    reform.add_argument("--kmer-length", type=int, required=True); reform.add_argument("--signal-move-offset", type=int, required=True)
    realign = sub.add_parser("realign")
    realign.add_argument("--reform-paf", type=Path, required=True); realign.add_argument("--alignment-bam", type=Path, required=True); realign.add_argument("--reference-fasta", type=Path, required=True); realign.add_argument("--output", type=Path, required=True); realign.add_argument("--report", type=Path, required=True)
    render = sub.add_parser("render")
    render.add_argument("--mode", choices=("read", "reference", "pileup"), required=True); render.add_argument("--blow5", type=Path, action="append", required=True); render.add_argument("--mapping", type=Path, required=True); render.add_argument("--output-dir", type=Path, required=True); render.add_argument("--report", type=Path, required=True)
    render.add_argument("--read-id"); render.add_argument("--region"); render.add_argument("--sequence-bam", type=Path); render.add_argument("--reference-fasta", type=Path); render.add_argument("--bed", type=Path)
    render.add_argument("--strand", choices=("forward", "reverse"), default="forward"); render.add_argument("--signal-units", choices=("pA", "raw_adc"), default="pA"); render.add_argument("--scale", choices=("none", "medmad", "znorm", "scaledpA"), default="none")
    render.add_argument("--base-shift", type=int, default=0); render.add_argument("--point-size", type=float, default=0.5)
    render.add_argument("--fixed-width", action="store_true"); render.add_argument("--base-width", type=int, default=10); render.add_argument("--base-limit", type=int, default=1000); render.add_argument("--signal-sample-limit", type=int, default=100000); render.add_argument("--pileup-read-limit", type=int, default=20)
    render.add_argument("--loose-bound", action="store_true"); render.add_argument("--show-samples", action="store_true"); render.add_argument("--show-base-colours", action="store_true"); render.add_argument("--remove-signal-outliers", action="store_true")
    return root


def main() -> int:
    args = parser().parse_args()
    {"validate-moves": cmd_validate_moves, "calibrate": cmd_calibrate, "reform": cmd_reform, "realign": cmd_realign, "render": cmd_render}[args.operation](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
