#!/usr/bin/env python3
"""Build minimal per-base support tables for FASTQ plasmid QC BAM alignments."""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

CIGAR_RE = re.compile(r"(\d+)([MIDNSHP=X])")
BASES = ("A", "C", "G", "T", "N")
HEADER = [
    "chrom",
    "position_1based",
    "reference_base",
    "depth",
    "forward_depth",
    "reverse_depth",
    "a_count",
    "c_count",
    "g_count",
    "t_count",
    "n_count",
    "insertion_count",
    "deletion_count",
    "consensus_base",
    "major_allele_fraction",
    "low_coverage",
    "ambiguous",
]


@dataclass
class PositionSupport:
    base_counts: dict[str, int] = field(default_factory=lambda: {base: 0 for base in BASES})
    forward_depth: int = 0
    reverse_depth: int = 0
    insertion_count: int = 0
    deletion_count: int = 0

    @property
    def depth(self) -> int:
        return sum(self.base_counts.values()) + self.deletion_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build per-base support TSV from a coordinate-sorted BAM")
    parser.add_argument("--bam", required=True, type=Path)
    parser.add_argument("--reference-fasta", required=True, type=Path)
    parser.add_argument("--out-per-base-support", required=True, type=Path)
    parser.add_argument("--min-depth", required=False, type=int, default=1)
    parser.add_argument("--ambiguous-fraction", required=False, type=float, default=0.8)
    parser.add_argument(
        "--samtools-cmd",
        required=False,
        nargs=argparse.REMAINDER,
        default=["samtools"],
        help="samtools command prefix; keep this as the final option when passing multi-word commands",
    )
    return parser.parse_args()


def read_first_fasta_record(path: Path) -> tuple[str, str]:
    header = ""
    chunks: list[str] = []
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


def parse_cigar(cigar: str) -> list[tuple[int, str]]:
    return [(int(length), op) for length, op in CIGAR_RE.findall(cigar)]


def _base(seq: str, index: int) -> str:
    if index < 0 or index >= len(seq):
        return "N"
    value = seq[index].upper()
    return value if value in BASES else "N"


def _record_support(
    support: list[PositionSupport],
    pos: int,
    is_reverse: bool,
    base: str | None = None,
    deletion: bool = False,
) -> None:
    if pos <= 0 or pos >= len(support):
        return
    item = support[pos]
    if is_reverse:
        item.reverse_depth += 1
    else:
        item.forward_depth += 1
    if deletion:
        item.deletion_count += 1
    elif base is not None:
        item.base_counts[_base(base, 0)] += 1


def iter_mapped_sam_lines(bam: Path, samtools_cmd: Sequence[str]) -> Iterable[str]:
    cmd = [*samtools_cmd, "view", "-F", "260", str(bam)]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("@"):
            continue
        yield line.rstrip("\n")
    stderr_text = proc.stderr.read() if proc.stderr is not None else ""
    return_code = proc.wait()
    if return_code != 0:
        raise RuntimeError(f"samtools view failed (exit {return_code}) [{' '.join(cmd)}]: {stderr_text.strip()}")


def consume_sam_record(line: str, ref_name: str, ref_len: int, support: list[PositionSupport]) -> None:
    cols = line.split("\t")
    if len(cols) < 11:
        return
    chrom = cols[2]
    if chrom != ref_name:
        return
    try:
        flag = int(cols[1])
        ref_pos = int(cols[3])
    except ValueError:
        return
    if flag & 0x100:
        return
    if ref_pos <= 0 or ref_pos > ref_len:
        return
    cigar = cols[5]
    if cigar == "*":
        return
    seq = cols[9]
    read_pos = 0
    is_reverse = (flag & 0x10) != 0

    for length, op in parse_cigar(cigar):
        if op in {"M", "=", "X"}:
            for offset in range(length):
                _record_support(support, ref_pos + offset, is_reverse, base=_base(seq, read_pos + offset))
            ref_pos += length
            read_pos += length
        elif op == "I":
            anchor = ref_pos - 1 if ref_pos > 1 else ref_pos
            if 1 <= anchor <= ref_len:
                support[anchor].insertion_count += 1
            read_pos += length
        elif op == "D":
            for offset in range(length):
                _record_support(support, ref_pos + offset, is_reverse, deletion=True)
            ref_pos += length
        elif op == "N":
            ref_pos += length
        elif op == "S":
            read_pos += length
        elif op in {"H", "P"}:
            continue


def _consensus_and_fraction(item: PositionSupport, reference_base: str) -> tuple[str, float]:
    base_counts = item.base_counts
    depth = item.depth
    if depth <= 0:
        return (reference_base if reference_base in BASES else "N", 0.0)
    consensus_base, major_count = max(base_counts.items(), key=lambda kv: (kv[1], "ACGTN".index(kv[0]) * -1))
    if item.deletion_count > major_count:
        consensus_base = "-"
        major_count = item.deletion_count
    return consensus_base, major_count / depth if depth else 0.0


def build_per_base_support(
    bam: Path,
    reference_fasta: Path,
    output_tsv: Path,
    samtools_cmd: Sequence[str] | None = None,
    min_depth: int = 1,
    ambiguous_fraction: float = 0.8,
) -> None:
    ref_name, sequence = read_first_fasta_record(reference_fasta)
    ref_len = len(sequence)
    support = [PositionSupport() for _ in range(ref_len + 1)]
    command = list(samtools_cmd or ["samtools"])

    for line in iter_mapped_sam_lines(bam, command):
        consume_sam_record(line, ref_name, ref_len, support)

    output_tsv.parent.mkdir(parents=True, exist_ok=True)
    with output_tsv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        for pos in range(1, ref_len + 1):
            item = support[pos]
            ref_base = sequence[pos - 1].upper() if pos - 1 < len(sequence) else "N"
            ref_base = ref_base if ref_base in BASES else "N"
            consensus_base, major_fraction = _consensus_and_fraction(item, ref_base)
            depth = item.depth
            writer.writerow(
                {
                    "chrom": ref_name,
                    "position_1based": pos,
                    "reference_base": ref_base,
                    "depth": depth,
                    "forward_depth": item.forward_depth,
                    "reverse_depth": item.reverse_depth,
                    "a_count": item.base_counts["A"],
                    "c_count": item.base_counts["C"],
                    "g_count": item.base_counts["G"],
                    "t_count": item.base_counts["T"],
                    "n_count": item.base_counts["N"],
                    "insertion_count": item.insertion_count,
                    "deletion_count": item.deletion_count,
                    "consensus_base": consensus_base,
                    "major_allele_fraction": f"{major_fraction:.4f}",
                    "low_coverage": "true" if depth < min_depth else "false",
                    "ambiguous": "true" if depth >= min_depth and major_fraction < ambiguous_fraction else "false",
                }
            )


def read_tsv(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def main() -> int:
    args = parse_args()
    build_per_base_support(
        bam=args.bam,
        reference_fasta=args.reference_fasta,
        output_tsv=args.out_per_base_support,
        samtools_cmd=args.samtools_cmd or ["samtools"],
        min_depth=args.min_depth,
        ambiguous_fraction=args.ambiguous_fraction,
    )
    print(f"Wrote {args.out_per_base_support}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
