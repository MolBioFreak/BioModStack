#!/usr/bin/env python3
"""Write a consensus read-pileup chromatogram (AB1) from per-base read counts.

The height of each channel at a base is the share of aligned reads carrying
that base. This is a read pileup drawn in the AB1 container so sequence
viewers can open it. It is not a Sanger trace.

The counts must be in the coordinates of the consensus being written. When the
supplied table was counted against a different sequence (the consensus has an
insertion or deletion against it), the reads are aligned to the consensus once
and counted again.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_TRACE_POINTS = 32767  # peak positions are 16-bit and read as signed
COMMENT = ("Consensus pileup from nanopore reads. Peak height = share of aligned "
           "reads with that base. Not a Sanger trace.")
BASES = "ACGT"


def read_consensus(path: Path) -> str:
    chunks: list[str] = []
    records = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(">"):
            records += 1
            if records > 1:
                break
        elif line:
            chunks.append(line.upper())
    return "".join(chunks)


def read_counts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _major(row: dict[str, str]) -> str:
    return max(BASES, key=lambda base: int(row[base.lower() + "_count"]))


def table_is_in_consensus_coordinates(rows: list[dict[str, str]], consensus: str) -> bool:
    """True when every consensus base sits at the table position that supports it."""
    if len(rows) != len(consensus):
        return False
    for row, base in zip(rows, consensus):
        if base in BASES and base != row["reference_base"].upper() and base != _major(row):
            return False
    return True


def shares(rows: list[dict[str, str]]) -> list[dict[str, float]]:
    out = []
    for row in rows:
        depth = int(row["depth"]) or 1
        out.append({base: int(row[base.lower() + "_count"]) / depth for base in BASES})
    return out


def build_traces(consensus: str, share_rows: list[dict[str, float]], scale: int = 1000):
    count = len(consensus)
    spacing = max(1, min(10, MAX_TRACE_POINTS // count))
    half = spacing // 2
    sigma = max(0.6, spacing * 0.28)
    kernel = [math.exp(-(d * d) / (2 * sigma * sigma)) for d in range(-spacing, spacing + 1)]
    points = count * spacing
    channels = {base: [0.0] * points for base in BASES}
    peaks = []
    for index, share in enumerate(share_rows):
        centre = index * spacing + half
        peaks.append(centre)
        for base in BASES:
            height = share[base] * scale
            if height <= 0:
                continue
            for offset, weight in enumerate(kernel):
                position = centre + offset - spacing
                if 0 <= position < points:
                    channels[base][position] += height * weight
    traces = {base: [min(32767, int(round(value))) for value in values] for base, values in channels.items()}
    quality = bytes(
        min(60, int(round(-10 * math.log10(max(1 - share[base], 1e-6))))) if base in BASES else 0
        for base, share in zip(consensus, share_rows)
    )
    return peaks, traces, quality, spacing


def _pstring(text: str) -> bytes:
    raw = text.encode("ascii", "replace")[:255]
    return bytes([len(raw)]) + raw


def write_ab1(path: Path, consensus: str, peaks, traces, quality: bytes, sample: str) -> None:
    def shorts(values):
        return struct.pack(">%dh" % len(values), *values)
    sequence = consensus.encode("ascii")
    mobility = _pstring("KB_3500_POP7_BDTv3.mob")
    items = [
        (b"DATA", 9, 4, 2, len(traces["G"]), shorts(traces["G"])),
        (b"DATA", 10, 4, 2, len(traces["A"]), shorts(traces["A"])),
        (b"DATA", 11, 4, 2, len(traces["T"]), shorts(traces["T"])),
        (b"DATA", 12, 4, 2, len(traces["C"]), shorts(traces["C"])),
        (b"FWO_", 1, 2, 1, 4, b"GATC"),
        (b"LANE", 1, 4, 2, 1, struct.pack(">h", 1)),
        (b"PBAS", 1, 2, 1, len(sequence), sequence),
        (b"PBAS", 2, 2, 1, len(sequence), sequence),
        (b"PCON", 1, 2, 1, len(quality), quality),
        (b"PCON", 2, 2, 1, len(quality), quality),
        (b"PLOC", 1, 4, 2, len(peaks), shorts(peaks)),
        (b"PLOC", 2, 4, 2, len(peaks), shorts(peaks)),
        (b"PDMF", 1, 18, 1, len(mobility), mobility),
        (b"PDMF", 2, 18, 1, len(mobility), mobility),
        (b"S/N%", 1, 4, 2, 4, shorts([max(traces[base]) for base in "GATC"])),
        (b"SMPL", 1, 18, 1, len(_pstring(sample)), _pstring(sample)),
        (b"CMNT", 1, 18, 1, len(_pstring(COMMENT)), _pstring(COMMENT)),
    ]
    blob = bytearray()
    offsets = []
    for *_, data in items:
        offsets.append(128 + len(blob))
        blob += data + b"\0" * (-len(data) % 4)
    directory_offset = 128 + len(blob)
    out = bytearray(b"ABIF" + struct.pack(">H", 101) + struct.pack(
        ">4sIHHIIII", b"tdir", 1, 1023, 28, len(items), len(items) * 28, directory_offset, 0))
    out += b"\0" * (128 - len(out)) + blob
    for (name, number, element_type, element_size, count, data), offset in zip(items, offsets):
        field = data.ljust(4, b"\0") if len(data) <= 4 else struct.pack(">I", offset)
        out += struct.pack(">4sIHHII", name, number, element_type, element_size, count, len(data)) + field + b"\0" * 4
    path.write_bytes(bytes(out))


def recount_against_consensus(consensus_fasta: Path, reads: Path, preset: str, threads: int,
                              samtools: list[str], minimap2: str) -> list[dict[str, str]]:
    """Align the reads to the consensus once and count per-base support there."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_fastq_support_tables import build_per_base_support
    with tempfile.TemporaryDirectory(prefix="pileup-ab1-", dir=".") as scratch:
        bam = Path(scratch) / "consensus.bam"
        table = Path(scratch) / "per_base_support.consensus.tsv"
        aligner = subprocess.Popen(
            [minimap2, "-a", "-x", preset, "--secondary=no", "-t", str(threads), str(consensus_fasta), str(reads)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        sorter = subprocess.run([*samtools, "sort", "-o", str(bam), "-"], stdin=aligner.stdout,
                                stderr=subprocess.DEVNULL)
        aligner.stdout.close()
        if aligner.wait() != 0 or sorter.returncode != 0:
            raise RuntimeError("could not align the reads to the consensus")
        build_per_base_support(bam=bam, reference_fasta=consensus_fasta, output_tsv=table, samtools_cmd=samtools)
        return read_counts(table)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-base-support", required=True, type=Path)
    parser.add_argument("--consensus-fasta", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--reads", type=Path, help="FASTQ of the reads; used only when the table must be recounted")
    parser.add_argument("--minimap2-preset", default="map-ont")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--minimap2", default="minimap2")
    parser.add_argument("--samtools-cmd", nargs=argparse.REMAINDER, default=["samtools"])
    args = parser.parse_args(argv)
    consensus = read_consensus(args.consensus_fasta)
    if not consensus:
        print("no consensus sequence; pileup chromatogram not written", file=sys.stderr)
        return 2
    if len(consensus) > MAX_TRACE_POINTS:
        print(f"consensus is {len(consensus)} bases; one AB1 file holds at most {MAX_TRACE_POINTS}", file=sys.stderr)
        return 2
    rows = read_counts(args.per_base_support)
    source = "supplied per-base table"
    if not table_is_in_consensus_coordinates(rows, consensus):
        if args.reads is None:
            print("the per-base table is not in consensus coordinates and no reads were given", file=sys.stderr)
            return 2
        rows = recount_against_consensus(args.consensus_fasta, args.reads, args.minimap2_preset, args.threads,
                                         args.samtools_cmd or ["samtools"], args.minimap2)
        source = "reads recounted against the consensus"
        if len(rows) != len(consensus):
            print("recounted table does not cover the consensus", file=sys.stderr)
            return 2
    peaks, traces, quality, spacing = build_traces(consensus, shares(rows))
    write_ab1(args.out, consensus, peaks, traces, quality, args.sample)
    print(f"Wrote {args.out}: {len(consensus)} bases, {spacing} points per base, counts from the {source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
