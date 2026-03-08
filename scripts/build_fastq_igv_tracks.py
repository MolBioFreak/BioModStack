#!/usr/bin/env python3
"""Build FASTQ plasmid-QC IGV analysis tracks and report loci."""

from __future__ import annotations

import argparse
import csv
import math
import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

CIGAR_RE = re.compile(r"(\d+)([MIDNSHP=X])")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build IGV analysis tracks for FASTQ plasmid QC")
    parser.add_argument("--bam", required=True, type=Path)
    parser.add_argument("--reference-fasta", required=True, type=Path)
    parser.add_argument("--coverage-tsv", required=False, type=Path, default=None)
    parser.add_argument(
        "--samtools-cmd",
        required=False,
        nargs="+",
        default=["samtools"],
        help="samtools command prefix (e.g. samtools OR apptainer exec /path/dorado.sif samtools)",
    )
    parser.add_argument("--window-bp", required=False, type=int, default=100)
    parser.add_argument("--hotspot-max", required=False, type=int, default=40)
    parser.add_argument("--out-coverage-depth", required=True, type=Path)
    parser.add_argument("--out-position-gradient", required=True, type=Path)
    parser.add_argument("--out-gc-content", required=True, type=Path)
    parser.add_argument("--out-gc-zscore", required=True, type=Path)
    parser.add_argument("--out-split-read-density", required=True, type=Path)
    parser.add_argument("--out-softclip-density", required=True, type=Path)
    parser.add_argument("--out-junction-hotspots-bed", required=True, type=Path)
    parser.add_argument("--out-report-sites-bed", required=True, type=Path)
    parser.add_argument("--out-report-sites-tsv", required=True, type=Path)
    return parser.parse_args()


def read_first_fasta_record(path: Path) -> Tuple[str, str]:
    header = ""
    chunks: List[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                if chunks:
                    break
                header = line[1:].split()[0]
                continue
            chunks.append(line.upper())

    return (header or "reference", "".join(chunks))


def parse_cigar(cigar: str) -> Tuple[int, int, int, bool]:
    ref_span = 0
    lead_soft = 0
    trail_soft = 0
    has_split_n = False
    ops = [(int(length), op) for length, op in CIGAR_RE.findall(cigar)]
    if not ops:
        return (0, 0, 0, False)

    if ops[0][1] == "S":
        lead_soft = ops[0][0]
    if ops[-1][1] == "S":
        trail_soft = ops[-1][0]

    for length, op in ops:
        if op in {"M", "D", "N", "=", "X"}:
            ref_span += length
        if op == "N":
            has_split_n = True

    return (ref_span, lead_soft, trail_soft, has_split_n)


def build_alignment_evidence(
    bam: Path,
    ref_name: str,
    ref_len: int,
    samtools_cmd: Sequence[str],
) -> Tuple[List[int], List[int], int]:
    split_counts = [0] * (ref_len + 1)
    softclip_counts = [0] * (ref_len + 1)
    mapped_records = 0

    samtools_view_cmd = [*samtools_cmd, "view", "-F", "4", str(bam)]
    proc = subprocess.Popen(
        samtools_view_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdout is not None

    for line in proc.stdout:
        cols = line.rstrip("\n").split("\t")
        if len(cols) < 11:
            continue

        chrom = cols[2]
        if chrom != ref_name:
            continue

        try:
            flag = int(cols[1])
            pos = int(cols[3])
        except ValueError:
            continue

        if pos <= 0 or pos > ref_len:
            continue

        cigar = cols[5]
        if cigar == "*":
            continue

        ref_span, lead_soft, trail_soft, has_split_n = parse_cigar(cigar)
        if ref_span <= 0:
            continue

        mapped_records += 1
        end = pos + ref_span - 1
        if end < 1:
            continue
        if end > ref_len:
            end = ref_len

        tags = cols[11:]
        has_sa = any(tag.startswith("SA:Z:") for tag in tags)
        is_supplementary = (flag & 0x800) != 0
        split_evidence = is_supplementary or has_sa or has_split_n
        if split_evidence:
            split_counts[pos] += 1

        if lead_soft > 0:
            softclip_counts[pos] += 1
        if trail_soft > 0:
            softclip_counts[end] += 1

    stderr_text = ""
    if proc.stderr is not None:
        stderr_text = proc.stderr.read()
    return_code = proc.wait()
    if return_code != 0:
        cmd_str = " ".join(samtools_view_cmd)
        raise RuntimeError(f"samtools view failed (exit {return_code}) [{cmd_str}]: {stderr_text.strip()}")

    return split_counts, softclip_counts, mapped_records


def read_coverage_depth(coverage_tsv: Path | None, ref_len: int) -> List[int]:
    depth = [0] * (ref_len + 1)
    if coverage_tsv is None or not coverage_tsv.exists():
        return depth

    with coverage_tsv.open("r", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter="\t")
        header_seen = False
        for row in reader:
            if not row:
                continue
            if not header_seen:
                header_seen = True
                maybe_header = row[0].strip().lower()
                if maybe_header in {"reference", "chrom", "chr"}:
                    continue
            if len(row) < 3:
                continue
            try:
                pos = int(row[1])
                val = int(float(row[2]))
            except ValueError:
                continue
            if 1 <= pos <= ref_len:
                depth[pos] = max(0, val)

    return depth


def write_bedgraph(path: Path, chrom: str, entries: Iterable[Tuple[int, int, float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for start1, end1, value in entries:
            if end1 < start1:
                continue
            start0 = start1 - 1
            if math.isclose(value, round(value), rel_tol=0.0, abs_tol=1e-9):
                value_str = str(int(round(value)))
            else:
                value_str = f"{value:.6f}".rstrip("0").rstrip(".")
            handle.write(f"{chrom}\t{start0}\t{end1}\t{value_str}\n")


def compress_per_base_values(values: Sequence[int]) -> List[Tuple[int, int, float]]:
    out: List[Tuple[int, int, float]] = []
    if len(values) <= 1:
        return out

    run_start = 1
    run_value = values[1]
    for pos in range(2, len(values)):
        val = values[pos]
        if val == run_value:
            continue
        out.append((run_start, pos - 1, float(run_value)))
        run_start = pos
        run_value = val

    out.append((run_start, len(values) - 1, float(run_value)))
    return out


def build_windows(ref_len: int, window_bp: int) -> List[Tuple[int, int]]:
    windows: List[Tuple[int, int]] = []
    start = 1
    while start <= ref_len:
        end = min(ref_len, start + window_bp - 1)
        windows.append((start, end))
        start = end + 1
    return windows


def gc_percent_for_window(seq: str, start1: int, end1: int) -> float:
    window = seq[start1 - 1:end1].upper()
    if not window:
        return 0.0
    gc = sum(1 for ch in window if ch in {"G", "C"})
    return (100.0 * gc) / len(window)


def build_prefix_sums(values: Sequence[int]) -> List[int]:
    prefix = [0] * len(values)
    for i in range(1, len(values)):
        prefix[i] = prefix[i - 1] + int(values[i])
    return prefix


def range_sum(prefix: Sequence[int], start1: int, end1: int) -> int:
    return int(prefix[end1] - prefix[start1 - 1])


def make_hotspot_rows(
    chrom: str,
    windows: Sequence[Tuple[int, int]],
    split_window_values: Sequence[float],
    softclip_window_values: Sequence[float],
    gc_window_values: Sequence[float],
    gradient_values: Sequence[float],
    max_rows: int,
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for i, (start1, end1) in enumerate(windows):
        split_v = float(split_window_values[i])
        soft_v = float(softclip_window_values[i])
        combined = (2.0 * split_v) + soft_v
        if combined <= 0:
            continue
        rows.append(
            {
                "chrom": chrom,
                "start1": start1,
                "end1": end1,
                "split": split_v,
                "softclip": soft_v,
                "combined": combined,
                "gc_pct": float(gc_window_values[i]),
                "gradient": float(gradient_values[i]),
            }
        )

    rows.sort(key=lambda row: (-float(row["combined"]), int(row["start1"])))
    if max_rows > 0:
        rows = rows[:max_rows]

    if rows:
        return rows

    if windows:
        start1, end1 = windows[0]
    else:
        start1, end1 = 1, 1
    return [
        {
            "chrom": chrom,
            "start1": start1,
            "end1": end1,
            "split": 0.0,
            "softclip": 0.0,
            "combined": 0.0,
            "gc_pct": 0.0,
            "gradient": 0.0,
        }
    ]


def write_hotspots_bed(path: Path, rows: Sequence[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    max_score = max((float(row["combined"]) for row in rows), default=0.0)

    with path.open("w", encoding="utf-8") as handle:
        for idx, row in enumerate(rows, start=1):
            chrom = str(row["chrom"])
            start1 = int(row["start1"])
            end1 = int(row["end1"])
            split_v = float(row["split"])
            soft_v = float(row["softclip"])
            combined = float(row["combined"])
            name = f"junction_hotspot_{idx};split={split_v:.0f};soft={soft_v:.0f}"
            if max_score > 0:
                score = int(round((combined / max_score) * 1000.0))
            else:
                score = 0
            score = max(0, min(1000, score))
            handle.write(f"{chrom}\t{start1 - 1}\t{end1}\t{name}\t{score}\t.\n")


def write_report_sites(
    bed_path: Path,
    tsv_path: Path,
    rows: Sequence[Dict[str, object]],
) -> None:
    bed_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_path.parent.mkdir(parents=True, exist_ok=True)

    max_score = max((float(row["combined"]) for row in rows), default=0.0)

    with bed_path.open("w", encoding="utf-8") as bed_handle, tsv_path.open("w", encoding="utf-8", newline="") as tsv_handle:
        writer = csv.writer(tsv_handle, delimiter="\t")
        writer.writerow(
            [
                "site_id",
                "locus",
                "split_density",
                "softclip_density",
                "combined_score",
                "gc_content_pct",
                "position_gradient",
            ]
        )

        for idx, row in enumerate(rows, start=1):
            chrom = str(row["chrom"])
            start1 = int(row["start1"])
            end1 = int(row["end1"])
            split_v = float(row["split"])
            soft_v = float(row["softclip"])
            combined = float(row["combined"])
            gc_pct = float(row["gc_pct"])
            gradient = float(row["gradient"])
            locus = f"{chrom}:{start1}-{end1}"
            site_id = f"hotspot_{idx:02d}"

            if max_score > 0:
                score = int(round((combined / max_score) * 1000.0))
            else:
                score = 0
            score = max(0, min(1000, score))

            bed_name = f"{site_id};split={split_v:.0f};soft={soft_v:.0f}"
            bed_handle.write(f"{chrom}\t{start1 - 1}\t{end1}\t{bed_name}\t{score}\t.\n")

            writer.writerow(
                [
                    site_id,
                    locus,
                    f"{split_v:.4f}",
                    f"{soft_v:.4f}",
                    f"{combined:.4f}",
                    f"{gc_pct:.4f}",
                    f"{gradient:.6f}",
                ]
            )


def main() -> None:
    args = parse_args()

    window_bp = max(1, int(args.window_bp))
    hotspot_max = max(1, int(args.hotspot_max))

    ref_name, ref_seq = read_first_fasta_record(args.reference_fasta)
    if not ref_seq:
        raise RuntimeError(f"Reference FASTA has no sequence: {args.reference_fasta}")
    ref_len = len(ref_seq)

    split_counts, softclip_counts, _mapped_records = build_alignment_evidence(
        args.bam,
        ref_name,
        ref_len,
        args.samtools_cmd,
    )
    depth_by_pos = read_coverage_depth(args.coverage_tsv, ref_len)

    coverage_entries = compress_per_base_values(depth_by_pos)
    write_bedgraph(args.out_coverage_depth, ref_name, coverage_entries)

    windows = build_windows(ref_len, window_bp)
    split_prefix = build_prefix_sums(split_counts)
    soft_prefix = build_prefix_sums(softclip_counts)

    gradient_entries: List[Tuple[int, int, float]] = []
    gc_entries: List[Tuple[int, int, float]] = []
    split_entries: List[Tuple[int, int, float]] = []
    soft_entries: List[Tuple[int, int, float]] = []

    gc_window_values: List[float] = []
    gradient_values: List[float] = []
    split_values: List[float] = []
    soft_values: List[float] = []

    for start1, end1 in windows:
        mid = (start1 + end1) / 2.0
        gradient = ((mid - 1.0) / (ref_len - 1.0)) if ref_len > 1 else 0.0
        gc_pct = gc_percent_for_window(ref_seq, start1, end1)
        split_sum = float(range_sum(split_prefix, start1, end1))
        soft_sum = float(range_sum(soft_prefix, start1, end1))

        gradient_entries.append((start1, end1, gradient))
        gc_entries.append((start1, end1, gc_pct))
        split_entries.append((start1, end1, split_sum))
        soft_entries.append((start1, end1, soft_sum))

        gc_window_values.append(gc_pct)
        gradient_values.append(gradient)
        split_values.append(split_sum)
        soft_values.append(soft_sum)

    mean_gc = sum(gc_window_values) / len(gc_window_values) if gc_window_values else 0.0
    variance_gc = (
        sum((value - mean_gc) ** 2 for value in gc_window_values) / len(gc_window_values)
        if gc_window_values
        else 0.0
    )
    std_gc = math.sqrt(variance_gc)

    gc_z_entries: List[Tuple[int, int, float]] = []
    for idx, (start1, end1) in enumerate(windows):
        gc = gc_window_values[idx]
        gc_z = (gc - mean_gc) / std_gc if std_gc > 0 else 0.0
        gc_z_entries.append((start1, end1, gc_z))

    write_bedgraph(args.out_position_gradient, ref_name, gradient_entries)
    write_bedgraph(args.out_gc_content, ref_name, gc_entries)
    write_bedgraph(args.out_gc_zscore, ref_name, gc_z_entries)
    write_bedgraph(args.out_split_read_density, ref_name, split_entries)
    write_bedgraph(args.out_softclip_density, ref_name, soft_entries)

    hotspot_rows = make_hotspot_rows(
        ref_name,
        windows,
        split_values,
        soft_values,
        gc_window_values,
        gradient_values,
        hotspot_max,
    )
    write_hotspots_bed(args.out_junction_hotspots_bed, hotspot_rows)
    write_report_sites(args.out_report_sites_bed, args.out_report_sites_tsv, hotspot_rows)


if __name__ == "__main__":
    main()
