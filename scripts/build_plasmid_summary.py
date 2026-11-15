#!/usr/bin/env python3
"""Build the per-sample plasmid summary from outputs the run already wrote.

Nothing is computed that is not in those outputs: lengths come from the reads,
depth from the per-base support table, read counts from the alignment
statistics, features from the annotation BED. Each number in summary.json
names the file it came from. Plots are SVG drawn directly.

Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from pathlib import Path
from xml.sax.saxutils import escape

SCHEMA = "bms.ngs.plasmid-summary.v1"
BIN_BP = 100
MAX_COVERAGE_POINTS = 600
GEL_KB = (40, 20, 10, 5, 3, 2, 1)


def read_fasta(path: Path) -> tuple[str, str]:
    name, chunks = "", []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(">"):
            if chunks:
                break
            name = line[1:].split()[0] if len(line) > 1 else ""
        elif line:
            chunks.append(line.upper())
    return name, "".join(chunks)


def read_lengths_from_fastq(path: Path) -> list[int]:
    opener = gzip.open if path.name.endswith(".gz") else open
    lengths = []
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            if index % 4 == 1:
                lengths.append(len(line.rstrip("\r\n")))
    return lengths


def read_lengths_from_table(path: Path) -> list[int]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [int(row["length_bp"]) for row in csv.DictReader(handle, delimiter="\t")]


def read_metrics(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        cells = raw.split("\t")
        if len(cells) >= 2:
            out[cells[0].strip()] = cells[1].strip()
    return out


def read_depth(path: Path) -> list[int]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [int(row["depth"]) for row in csv.DictReader(handle, delimiter="\t")]


def read_features(path: Path | None) -> list[dict]:
    if path is None or not path.is_file():
        return []
    features = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        cells = raw.split("\t")
        if len(cells) >= 4 and cells[1].isdigit() and cells[2].isdigit():
            features.append({"name": cells[3], "start": int(cells[1]), "end": int(cells[2]),
                             "strand": -1 if len(cells) > 4 and cells[4].strip() == "-" else 1})
    return features


def histogram(lengths: list[int]) -> list[dict]:
    counts: dict[int, list[int]] = {}
    for length in lengths:
        entry = counts.setdefault(length // BIN_BP, [0, 0])
        entry[0] += 1
        entry[1] += length
    return [{"start_bp": index * BIN_BP, "end_bp_exclusive": (index + 1) * BIN_BP,
             "read_count": reads, "base_count": bases} for index, (reads, bases) in sorted(counts.items())]


def coverage_points(depth: list[int]) -> list[dict]:
    """Per-window minimum and maximum, so a narrow dip is never averaged away."""
    if not depth:
        return []
    window = max(1, math.ceil(len(depth) / MAX_COVERAGE_POINTS))
    points = []
    for start in range(0, len(depth), window):
        chunk = depth[start:start + window]
        points.append({"position_1based": start + 1, "end_1based": start + len(chunk),
                       "minimum_depth": min(chunk), "maximum_depth": max(chunk)})
    return points


def _svg(width: int, height: int, body: str, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
            f'font-family="sans-serif" font-size="11"><title>{escape(title)}</title>'
            f'<rect width="{width}" height="{height}" fill="white"/>{body}</svg>\n')


def _axes(x0, y0, x1, y1, x_ticks, y_ticks, x_label, y_label) -> str:
    parts = [f'<line x1="{x0}" y1="{y1}" x2="{x1}" y2="{y1}" stroke="black"/>',
             f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="black"/>']
    for x, label in x_ticks:
        parts.append(f'<text x="{x:.1f}" y="{y1 + 14}" text-anchor="middle">{escape(label)}</text>')
    for y, label in y_ticks:
        parts.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="#ddd"/>')
        parts.append(f'<text x="{x0 - 4}" y="{y + 4:.1f}" text-anchor="end">{escape(label)}</text>')
    parts.append(f'<text x="{(x0 + x1) / 2}" y="{y1 + 30}" text-anchor="middle">{escape(x_label)}</text>')
    parts.append(f'<text x="14" y="{(y0 + y1) / 2}" text-anchor="middle" transform="rotate(-90 14 {(y0 + y1) / 2})">{escape(y_label)}</text>')
    return "".join(parts)


def _nice_ticks(top: float, count: int = 4) -> list[float]:
    if top <= 0:
        return [0.0]
    raw = top / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw)
    return [step * i for i in range(0, int(top / step) + 1)]


def coverage_svg(points: list[dict], length: int, sample: str) -> str:
    x0, y0, x1, y1 = 60, 20, 700, 220
    top = max((p["maximum_depth"] for p in points), default=0) or 1
    sx = lambda position: x0 + (position - 1) / max(1, length - 1) * (x1 - x0)
    sy = lambda value: y1 - value / top * (y1 - y0)
    line = " ".join(f"{sx(p['position_1based']):.1f},{sy(p['minimum_depth']):.1f}" for p in points)
    area = f"{x0},{y1} {line} {x1},{y1}"
    body = (_axes(x0, y0, x1, y1, [(sx(v + 1), f"{int(v):,}") for v in _nice_ticks(length) if v < length],
                  [(sy(v), f"{int(v):,}") for v in _nice_ticks(top)], "Position (bp)", "Depth")
            + f'<polygon points="{area}" fill="#9cc3ee"/><polyline points="{line}" fill="none" stroke="#1f6fbf"/>')
    return _svg(720, 260, body, f"Coverage across the consensus, {sample}")


def histogram_svg(bins: list[dict], sample: str) -> str:
    x0, y0, x1, y1 = 60, 20, 700, 220
    longest = max((b["end_bp_exclusive"] for b in bins), default=BIN_BP)
    top = max((b["read_count"] for b in bins), default=0) or 1
    sx = lambda bp: x0 + bp / longest * (x1 - x0)
    bars = "".join(
        f'<rect x="{sx(b["start_bp"]):.1f}" y="{y1 - b["read_count"] / top * (y1 - y0):.1f}" '
        f'width="{max(0.6, sx(b["end_bp_exclusive"]) - sx(b["start_bp"])):.1f}" '
        f'height="{b["read_count"] / top * (y1 - y0):.1f}" fill="#1f6fbf"/>' for b in bins)
    body = _axes(x0, y0, x1, y1, [(sx(v), f"{v / 1000:g} kb") for v in _nice_ticks(longest, 5)],
                 [(y1 - v / top * (y1 - y0), f"{int(v):,}") for v in _nice_ticks(top)],
                 "Read length", f"Reads per {BIN_BP} bp") + bars
    return _svg(720, 260, body, f"Read-length distribution, {sample}")


def gel_position(bp: float, top: float = 30.0, bottom: float = 250.0) -> float:
    kb = min(GEL_KB[0], max(GEL_KB[-1] * 0.5, bp / 1000))
    span = math.log10(GEL_KB[0]) - math.log10(GEL_KB[-1] * 0.5)
    return top + (math.log10(GEL_KB[0]) - math.log10(kb)) / span * (bottom - top)


def gel_svg(bins: list[dict], sample: str) -> str:
    """One lane. Band brightness is the bases in each length bin, scaled to the lane's brightest bin."""
    brightest = max((b["base_count"] for b in bins), default=0) or 1
    parts = ['<rect x="0" y="0" width="200" height="280" fill="#111"/>']
    for kb in GEL_KB:
        y = gel_position(kb * 1000)
        parts.append(f'<text x="34" y="{y + 4:.1f}" text-anchor="end" fill="#ccc">{kb}</text>'
                     f'<line x1="38" y1="{y:.1f}" x2="46" y2="{y:.1f}" stroke="#ccc"/>')
    parts.append('<text x="34" y="18" text-anchor="end" fill="#ccc">kb</text>')
    for b in bins:
        intensity = b["base_count"] / brightest
        if intensity < 0.01:
            continue
        y = gel_position((b["start_bp"] + b["end_bp_exclusive"]) / 2)
        parts.append(f'<rect x="70" y="{y - 1.5:.1f}" width="90" height="3" fill="#d8ffd0" opacity="{intensity:.3f}"/>')
    parts.append(f'<text x="115" y="272" text-anchor="middle" fill="#ccc">{escape(sample[:24])}</text>')
    return _svg(200, 280, "".join(parts), f"Virtual gel, {sample}")


def build(args: argparse.Namespace) -> dict:
    consensus_name, consensus = read_fasta(args.consensus_fasta)
    depth = read_depth(args.per_base_support)
    if args.read_lengths is not None:
        lengths, lengths_source = read_lengths_from_table(args.read_lengths), args.read_lengths.name
    else:
        lengths, lengths_source = read_lengths_from_fastq(args.reads_fastq), args.reads_fastq.name
    metrics = read_metrics(args.alignment_stats)
    stats_source = args.alignment_stats.name if metrics else None
    bins = histogram(lengths)
    covered = sum(1 for value in depth if value > 0)
    number = lambda key: int(metrics[key]) if metrics.get(key, "").isdigit() else None
    return {
        "schema": SCHEMA,
        "sample": args.sample,
        "expected_sequence_provided": bool(args.expected_sequence_provided),
        "coordinates": args.coordinates,
        "topology": args.topology,
        "consensus": {"name": consensus_name, "length_bp": len(consensus), "sequence": consensus,
                      "source": args.consensus_fasta.name},
        "reads": {"total_reads": len(lengths), "total_bases": sum(lengths), "longest_read_bp": max(lengths, default=0),
                  "source": lengths_source},
        "alignment": {"mapped_reads": number("mapped_reads"), "unmapped_reads": number("unmapped_reads"),
                      "source": stats_source},
        "coverage": {"positions": len(depth), "positions_with_reads": covered,
                     "covered_fraction": covered / len(depth) if depth else None,
                     "mean_depth": sum(depth) / len(depth) if depth else None,
                     "minimum_depth": min(depth, default=None), "maximum_depth": max(depth, default=None),
                     "points": coverage_points(depth), "source": args.per_base_support.name},
        "read_length_histogram": {"bin_width_bp": BIN_BP, "bins": bins, "source": lengths_source},
        "features": read_features(args.annotations_bed),
        "features_source": args.annotations_bed.name if args.annotations_bed and args.annotations_bed.is_file() else None,
    }


def sample_text(summary: dict) -> str:
    c, r, a, v = summary["consensus"], summary["reads"], summary["alignment"], summary["coverage"]
    show = lambda value, pattern="{:,}": "not available" if value is None else pattern.format(value)
    lines = [
        f"Plasmid summary: {summary['sample']}",
        "Expected sequence: " + ("given" if summary["expected_sequence_provided"] else "not given; assembled from reads"),
        f"Positions are in {summary['coordinates']} coordinates.",
        "",
        f"Consensus length: {c['length_bp']:,} bp ({c['source']})",
        f"Total reads: {r['total_reads']:,} ({r['source']})",
        f"Total bases: {r['total_bases']:,} bp ({r['source']})",
        f"Mapped reads: {show(a['mapped_reads'])} ({a['source'] or 'no alignment statistics'})",
        f"Mean depth: {show(v['mean_depth'], '{:,.1f}')} ({v['source']})",
        f"Depth range: {show(v['minimum_depth'])} to {show(v['maximum_depth'])} ({v['source']})",
        f"Positions with reads: {show(v['covered_fraction'] if v['covered_fraction'] is None else v['covered_fraction'] * 100, '{:.2f}%')} ({v['source']})",
        f"Annotated features: {len(summary['features'])} ({summary['features_source'] or 'no annotation file'})",
    ]
    return "\n".join(lines) + "\n"


RUN_COLUMNS = ("sample", "expected_sequence_provided", "consensus_length_bp", "total_reads", "total_bases",
               "mapped_reads", "mean_depth", "minimum_depth", "maximum_depth", "covered_fraction", "annotated_features")


def run_row(summary: dict) -> dict:
    v = summary["coverage"]
    return {"sample": summary["sample"], "expected_sequence_provided": str(summary["expected_sequence_provided"]).lower(),
            "consensus_length_bp": summary["consensus"]["length_bp"], "total_reads": summary["reads"]["total_reads"],
            "total_bases": summary["reads"]["total_bases"], "mapped_reads": summary["alignment"]["mapped_reads"],
            "mean_depth": None if v["mean_depth"] is None else round(v["mean_depth"], 2),
            "minimum_depth": v["minimum_depth"], "maximum_depth": v["maximum_depth"],
            "covered_fraction": None if v["covered_fraction"] is None else round(v["covered_fraction"], 6),
            "annotated_features": len(summary["features"])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--consensus-fasta", required=True, type=Path)
    parser.add_argument("--per-base-support", required=True, type=Path)
    parser.add_argument("--alignment-stats", type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--read-lengths", type=Path)
    source.add_argument("--reads-fastq", type=Path)
    parser.add_argument("--annotations-bed", type=Path)
    parser.add_argument("--expected-sequence-provided", action="store_true")
    parser.add_argument("--coordinates", choices=("consensus", "reference"), default="consensus")
    parser.add_argument("--topology", choices=("circular", "linear"), default="circular")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    summary = build(args)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    (out / "sample_summary.txt").write_text(sample_text(summary), encoding="utf-8")
    with (out / "run_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RUN_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerow(run_row(summary))
    length = summary["consensus"]["length_bp"] if args.coordinates == "consensus" else summary["coverage"]["positions"]
    (out / "coverage.svg").write_text(coverage_svg(summary["coverage"]["points"], length, args.sample), encoding="utf-8")
    (out / "read_length_histogram.svg").write_text(histogram_svg(summary["read_length_histogram"]["bins"], args.sample), encoding="utf-8")
    (out / "virtual_gel.svg").write_text(gel_svg(summary["read_length_histogram"]["bins"], args.sample), encoding="utf-8")
    print(f"Wrote plasmid summary for {args.sample} to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
