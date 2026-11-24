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
POLY_A_BIN_NT = 10
REFERENCE_LIST_LIMIT = 50
HISTOGRAM_BINS = 60
MOD_PROBABILITY_THRESHOLD = 0.5

# SAM MM base-modification codes (SAMtags §1.7) plus ChEBI ids Dorado emits.
MOD_CODE_NAMES = {
    "m": "5mC", "h": "5hmC", "f": "5fC", "c": "5caC", "a": "6mA", "o": "8oxoG",
    "21839": "4mC", "17596": "inosine", "17802": "pseU", "19227": "2OmeU",
    "19228": "2OmeC", "19229": "2OmeG", "69426": "2OmeA",
}

# RNA names for the codes that DNA calls 6mA and 5mC.
RNA_MOD_CODE_NAMES = {**MOD_CODE_NAMES, "a": "m6A", "m": "m5C"}
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


_COMPLEMENT = str.maketrans("ACGTN", "TGCAN")
_DCM = {"CCAGG", "CCTGG"}


def _motif_context(code_name: str, canonical: str, strand: int, sequence: str, index: int) -> str | None:
    """Methylase motif at a called base, from the read's own forward sequence.

    Dam methylates the A of GATC. Dcm methylates the second C of CCWGG, so only
    that C is a Dcm site: counting the first C as well halves the fraction.
    A call reported on the opposite strand (G-m) sits on the G paired with the
    other strand's target C, the fourth base of the motif. 5hmC is not Dcm.
    """
    if code_name == "6mA" and canonical == "A" and strand == 0 and sequence[max(0, index - 1):index + 3] == "GATC":
        return "GATC"
    if code_name == "5mC":
        if canonical == "C" and strand == 0 and index >= 1 and sequence[index - 1:index + 4] in _DCM:
            return "CCWGG"
        if canonical == "G" and strand == 1 and index >= 3 and sequence[index - 3:index + 2] in _DCM:
            return "CCWGG"
    return None
def _implicit_modes(read: Any) -> set[tuple[str, str]]:
    """(canonical, code) pairs whose MM mode is implicit ('.' or omitted).

    In implicit mode a canonical base absent from the MM list was called
    unmodified (SAMtags 1.7); only explicit '?' leaves unlisted bases uncalled.
    """
    if not read.has_tag("MM") and not read.has_tag("Mm"):
        return set()
    tag = str(read.get_tag("MM") if read.has_tag("MM") else read.get_tag("Mm"))
    implicit: set[tuple[str, str]] = set()
    for entry in tag.split(";"):
        head = entry.split(",", 1)[0]
        if len(head) < 3:
            continue
        canonical, codes = head[0], head[2:]
        mode = codes[-1] if codes[-1] in ".?" else "."
        codes = codes[:-1] if codes[-1] in ".?" else codes
        if mode != ".":
            continue
        for code in ([codes] if codes.isdigit() else list(codes)):
            implicit.add((canonical, code))
    return implicit


def _modification_tallies(read: Any, totals: dict[str, dict[str, int]],
                          motifs: dict[str, dict[str, int]] | None = None, *, rna: bool = False) -> None:
    """Count called sites and sites with P(mod) >= 0.5, overall and at Dam/Dcm motifs."""
    try:
        mods = read.modified_bases_forward or read.modified_bases
    except (ValueError, KeyError):
        return
    if not mods:
        return
    sequence = (read.get_forward_sequence() or "").upper()
    implicit = _implicit_modes(read)
    for (canonical, _strand, code), calls in mods.items():
        name = (RNA_MOD_CODE_NAMES if rna else MOD_CODE_NAMES).get(str(code), str(code))
        key = f"{canonical}+{name}"
        probability = {position: (quality + 0.5) / 256 for position, quality in calls}
        if (canonical, str(code)) in implicit and sequence:
            sites = [index for index, base in enumerate(sequence) if base == canonical]
        else:
            sites = sorted(probability)
        entry = totals[key]
        entry["called_sites"] += len(sites)
        entry["modified_sites"] += sum(1 for index in sites if probability.get(index, 0.0) >= MOD_PROBABILITY_THRESHOLD)
        entry["reads"] += 1
        # Dam and Dcm are bacterial DNA methylases; an RNA run has no such motif.
        if motifs is None or not sequence or rna:
            continue
        for index in sites:
            motif = _motif_context(name, canonical, int(_strand), sequence, index)
            if motif is None:
                continue
            bucket = motifs[f"{key}@{motif}"]
            bucket["called_sites"] += 1
            bucket["modified_sites"] += int(probability.get(index, 0.0) >= MOD_PROBABILITY_THRESHOLD)


def _poly_a_summary(values: list[int], reads: int) -> dict[str, Any] | None:
    """Dorado's pt tag: a tail length per read, -1 where it made no estimate."""
    if not values:
        return None
    estimated = sorted(value for value in values if value >= 0)
    middle = len(estimated) // 2
    median = None if not estimated else (
        estimated[middle] if len(estimated) % 2 else (estimated[middle - 1] + estimated[middle]) / 2)
    bins = Counter(value // POLY_A_BIN_NT for value in estimated)
    return {
        "source": "Dorado pt tag, one value per read",
        "reads": reads,
        "estimated_reads": len(estimated),
        # Kept apart: no estimate (-1) is not a tail of length zero, and neither is a read with no tag.
        "no_estimate_reads": len(values) - len(estimated),
        "zero_length_reads": sum(1 for value in estimated if value == 0),
        "reads_without_tag": reads - len(values),
        "median_tail_nt": median,
        "mean_tail_nt": round(sum(estimated) / len(estimated), 2) if estimated else None,
        "longest_tail_nt": estimated[-1] if estimated else None,
        "histogram": {
            "bin_width_nt": POLY_A_BIN_NT,
            "bins": [{"start_nt": index * POLY_A_BIN_NT, "end_nt_exclusive": (index + 1) * POLY_A_BIN_NT, "read_count": count}
                     for index, count in sorted(bins.items())],
        },
    }


def poly_a_tsv_text(bam_path: Path) -> str:
    """Every primary read with the exact value Dorado wrote."""
    try:
        import pysam
    except ImportError as exc:  # pragma: no cover - pinned dependency
        raise OntReadReportError("pysam is unavailable") from exc
    lines = ["read_id\tpoly_a_tail_nt\testimate"]
    try:
        with pysam.AlignmentFile(str(bam_path), "rb", check_sq=False) as handle:
            for read in handle.fetch(until_eof=True):
                if read.is_secondary or read.is_supplementary:
                    continue
                if len(lines) > MAX_READS:
                    break
                if not read.has_tag("pt"):
                    lines.append(f"{read.query_name}\t\tnot_tagged")
                    continue
                value = int(read.get_tag("pt"))
                lines.append(f"{read.query_name}\t{value}\t{'estimated' if value >= 0 else 'no_estimate'}")
    except (OSError, ValueError) as exc:
        raise OntReadReportError(f"reads BAM could not be read: {exc}") from exc
    return "\n".join(lines) + "\n"


def build_read_report(bam_path: Path, *, artifact: dict[str, Any], molecule_type: str | None = None) -> dict[str, Any]:
    try:
        import pysam
    except ImportError as exc:  # pragma: no cover - pinned dependency
        raise OntReadReportError("pysam is unavailable") from exc
    lengths: list[int] = []
    qualities: list[float] = []
    mapped = unmapped = secondary = supplementary = 0
    contigs: Counter[str] = Counter()
    modifications: dict[str, dict[str, int]] = defaultdict(lambda: {"called_sites": 0, "modified_sites": 0, "reads": 0})
    motif_calls: dict[str, dict[str, int]] = defaultdict(lambda: {"called_sites": 0, "modified_sites": 0})
    move_tagged = 0
    poly_a_values: list[int] = []
    truncated = False
    try:
        with pysam.AlignmentFile(str(bam_path), "rb", check_sq=False) as handle:
            header = handle.header.to_dict()
            # The job's molecule type wins; without it the basecall model name decides.
            rna = (molecule_type or "").lower() == "rna" if molecule_type else any(
                "basecall_model=rna" in str(item.get("DS") or "") for item in header.get("RG", []))
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
                if read.has_tag("pt"):
                    poly_a_values.append(int(read.get_tag("pt")))
                _modification_tallies(read, modifications, motif_calls, rna=rna)
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
            "references": references[:REFERENCE_LIST_LIMIT],
            "reference_count": len(references),
            "reads_per_reference": dict(contigs.most_common(REFERENCE_LIST_LIMIT)),
            "references_with_reads": len(contigs),
            "reference_list_limit": REFERENCE_LIST_LIMIT,
        },
        "move_tagged_reads": move_tagged,
        "poly_a": _poly_a_summary(poly_a_values, len(lengths)),
        "molecule_type": "rna" if rna else "dna",
        "modifications": {
            "probability_threshold": MOD_PROBABILITY_THRESHOLD,
            # How these tallies are made, so they are not read as modkit's per-site estimates.
            "method": {
                "source": "read MM/ML tags",
                "population": "primary reads" + (f", first {MAX_READS} in file order" if truncated else ""),
                "denominator": "called bases of the modified base type",
                "filter": "none",
            },
            "by_code": [
                {"code": key, **value,
                 "modified_fraction": round(value["modified_sites"] / value["called_sites"], 6) if value["called_sites"] else 0.0}
                for key, value in sorted(modifications.items())
            ],
            # Dam (6mA@GATC) and Dcm (5mC@CCWGG) status is read at its motif:
            # whole-read fractions dilute a fully methylated GATC set to ~1%.
            "by_motif": [
                {"code": key.split("@")[0], "motif": key.split("@")[1], **value,
                 "modified_fraction": round(value["modified_sites"] / value["called_sites"], 6) if value["called_sites"] else 0.0}
                for key, value in sorted(motif_calls.items())
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
