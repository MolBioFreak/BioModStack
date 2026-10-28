"""Read-level report for any ONT job that published a reads BAM.

Basecall-only and DNA-analysis runs have no FASTQ-QC verification package,
but their BAM still carries what an operator needs first: yield, read-length
distribution, basecall quality, mapping, and per-read modification calls.
This is display evidence computed from the job's own published artifact.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "bms.ngs.read-report.v1"
MAX_READS = 200_000
HISTOGRAM_BINS = 60
MOD_PROBABILITY_THRESHOLD = 0.5

# SAM MM base-modification codes (SAMtags §1.7) plus ChEBI ids Dorado emits.
MOD_CODE_NAMES = {
    "m": "5mC", "h": "5hmC", "f": "5fC", "c": "5caC", "a": "6mA", "o": "8oxoG",
    "21839": "4mC", "17596": "inosine", "17802": "pseU", "19227": "2OmeU",
    "19228": "2OmeC", "19229": "2OmeG", "69426": "2OmeA",
}

_BAM_KINDS = ("alignment_bam", "basecall_bam", "calls_bam")


class OntReadReportError(RuntimeError):
    pass


def _n50(lengths: list[int]) -> int:
    total = sum(lengths)
    running = 0
    for length in sorted(lengths, reverse=True):
        running += length
        if running * 2 >= total:
            return length
    return 0


def _histogram(lengths: list[int]) -> dict[str, Any]:
    if not lengths:
        return {"bin_width_bp": 1, "bins": []}
    high = max(lengths)
    width = max(1, math.ceil((high + 1) / HISTOGRAM_BINS))
    counts: Counter[int] = Counter(length // width for length in lengths)
    bases: Counter[int] = Counter()
    for length in lengths:
        bases[length // width] += length
    return {
        "bin_width_bp": width,
        "bins": [
            {"start_bp": index * width, "end_bp_exclusive": (index + 1) * width,
             "read_count": counts.get(index, 0), "base_count": bases.get(index, 0)}
            for index in range(max(counts) + 1)
        ],
    }


def _modification_tallies(read: Any, totals: dict[str, dict[str, int]]) -> None:
    """Count called sites and sites with P(mod) >= 0.5 per modification code."""
    try:
        mods = read.modified_bases_forward or read.modified_bases
    except (ValueError, KeyError):
        return
    if not mods:
        return
    for (canonical, _strand, code), calls in mods.items():
        name = MOD_CODE_NAMES.get(str(code), str(code))
        key = f"{canonical}+{name}"
        entry = totals[key]
        entry["called_sites"] += len(calls)
        entry["modified_sites"] += sum(1 for _position, quality in calls if (quality + 0.5) / 256 >= MOD_PROBABILITY_THRESHOLD)
        entry["reads"] += 1


def build_read_report(bam_path: Path, *, artifact: dict[str, Any]) -> dict[str, Any]:
    try:
        import pysam
    except ImportError as exc:  # pragma: no cover - pinned dependency
        raise OntReadReportError("pysam is unavailable") from exc
    lengths: list[int] = []
    qualities: list[float] = []
    mapped = unmapped = secondary = supplementary = 0
    contigs: Counter[str] = Counter()
    modifications: dict[str, dict[str, int]] = defaultdict(lambda: {"called_sites": 0, "modified_sites": 0, "reads": 0})
    move_tagged = 0
    truncated = False
    try:
        with pysam.AlignmentFile(str(bam_path), "rb", check_sq=False) as handle:
            header = handle.header.to_dict()
            for read in handle.fetch(until_eof=True):
                if read.is_secondary:
                    secondary += 1
                    continue
                if read.is_supplementary:
                    supplementary += 1
                    continue
                if len(lengths) >= MAX_READS:
                    truncated = True
                    break
                length = read.query_length or (len(read.query_sequence) if read.query_sequence else 0)
                lengths.append(length)
                if read.has_tag("qs"):
                    qualities.append(float(read.get_tag("qs")))
                elif read.query_qualities is not None and len(read.query_qualities):
                    # Mean Phred via error probabilities, the convention Dorado uses for qs.
                    errors = sum(10 ** (-q / 10) for q in read.query_qualities) / len(read.query_qualities)
                    qualities.append(-10 * math.log10(max(errors, 1e-12)))
                if read.is_unmapped:
                    unmapped += 1
                else:
                    mapped += 1
                    contigs[read.reference_name] += 1
                if read.has_tag("mv"):
                    move_tagged += 1
                _modification_tallies(read, modifications)
    except (OSError, ValueError) as exc:
        raise OntReadReportError(f"reads BAM could not be read: {exc}") from exc
    programs = [
        {"id": item.get("ID"), "program": item.get("PN"), "version": item.get("VN"),
         "command": (item.get("CL") or "")[:400]}
        for item in header.get("PG", [])
    ]
    read_groups = [
        {"id": item.get("ID"), "basecall_model": item.get("DS", "")[:400]}
        for item in header.get("RG", [])
    ]
    references = [{"name": item.get("SN"), "length": item.get("LN")} for item in header.get("SQ", [])]
    total_bases = sum(lengths)
    q_sorted = sorted(qualities)
    return {
        "schema": REPORT_SCHEMA,
        "source_artifact": {key: artifact.get(key) for key in ("artifact_id", "kind", "filename", "sha256", "size_bytes", "url")},
        "truncated_at_reads": MAX_READS if truncated else None,
        "reads": len(lengths),
        "total_bases": total_bases,
        "mean_read_length_bp": round(total_bases / len(lengths), 2) if lengths else 0,
        "n50_read_length_bp": _n50(lengths),
        "longest_read_bp": max(lengths) if lengths else 0,
        "mean_qscore": round(sum(qualities) / len(qualities), 3) if qualities else None,
        "median_qscore": round(q_sorted[len(q_sorted) // 2], 3) if q_sorted else None,
        "reads_q10_or_better": sum(1 for q in qualities if q >= 10),
        "reads_q20_or_better": sum(1 for q in qualities if q >= 20),
        "read_length_histogram": _histogram(lengths),
        "alignment": {
            "aligned": bool(references),
            "mapped_reads": mapped,
            "unmapped_reads": unmapped,
            "secondary_records": secondary,
            "supplementary_records": supplementary,
            "references": references[:50],
            "reads_per_reference": dict(contigs.most_common(50)),
        },
        "move_tagged_reads": move_tagged,
        "modifications": {
            "probability_threshold": MOD_PROBABILITY_THRESHOLD,
            "by_code": [
                {"code": key, **value,
                 "modified_fraction": round(value["modified_sites"] / value["called_sites"], 6) if value["called_sites"] else 0.0}
                for key, value in sorted(modifications.items())
            ],
        },
        "programs": programs,
        "read_groups": read_groups,
    }


def select_reads_bam(artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer the aligned BAM, then raw calls; skip BAM indexes and dimer subsets."""
    present = [
        item for item in artifacts
        if item.get("state") == "present" and str(item.get("filename") or item.get("relative_path") or "").endswith(".bam")
        and "dimer" not in str(item.get("relative_path") or item.get("filename") or "")
    ]
    # Native publication labels every .bam "alignment_bam"; the aligned reads live under align/.
    for item in present:
        if str(item.get("relative_path") or "").startswith("align/"):
            return item
    for kind in _BAM_KINDS:
        for item in present:
            if item.get("kind") == kind:
                return item
    return present[0] if present else None
