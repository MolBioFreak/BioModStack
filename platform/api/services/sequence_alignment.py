"""Pairwise nucleotide alignment helpers for the molecular biology toolkit."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

from Bio import Align


VALID_ALIGNMENT_BASES = frozenset("ATUCGNRYMKSWHBVD")


class SequenceAlignmentError(ValueError):
    """Raised when an alignment request is invalid."""


AlignmentMode = Literal["global", "local", "placement", "fragment"]
AlignmentStrand = Literal["auto", "forward", "reverse"]

_DNA_COMPLEMENTS = {
    "A": "T",
    "T": "A",
    "U": "A",
    "C": "G",
    "G": "C",
    "R": "Y",
    "Y": "R",
    "M": "K",
    "K": "M",
    "S": "S",
    "W": "W",
    "H": "D",
    "D": "H",
    "B": "V",
    "V": "B",
    "N": "N",
}

_RNA_COMPLEMENTS = {
    **_DNA_COMPLEMENTS,
    "A": "U",
}


@dataclass(slots=True)
class AlignmentSettings:
    mode: AlignmentMode = "placement"
    strand: AlignmentStrand = "auto"
    reference_is_circular: bool = False
    match_score: float = 2.0
    mismatch_score: float = -1.0
    gap_open_score: float = -6.0
    gap_extend_score: float = -1.0


@dataclass(slots=True)
class _AlignmentCandidate:
    alignment: Align.Alignment
    reference_sequence: str
    original_reference_sequence: str
    query_sequence: str
    strand: Literal["forward", "reverse"]
    mode: Literal["global", "local", "placement"]
    reference_is_circular: bool


def clean_alignment_sequence(sequence: str) -> str:
    compact = "".join((sequence or "").upper().split())
    if not compact:
        raise SequenceAlignmentError("Sequence contains no valid nucleotide characters")
    invalid = sorted(set(compact).difference(VALID_ALIGNMENT_BASES))
    if invalid:
        raise SequenceAlignmentError(
            "Sequence contains invalid nucleotide characters: " + ", ".join(invalid)
        )
    return compact


def _validate_settings(settings: AlignmentSettings) -> None:
    numeric = {
        "match_score": settings.match_score,
        "mismatch_score": settings.mismatch_score,
        "gap_open_score": settings.gap_open_score,
        "gap_extend_score": settings.gap_extend_score,
    }
    if any(not math.isfinite(value) for value in numeric.values()):
        raise SequenceAlignmentError("Alignment scores must be finite")
    if settings.match_score <= 0:
        raise SequenceAlignmentError("match_score must be positive")
    if settings.mismatch_score > 0 or settings.gap_open_score >= 0 or settings.gap_extend_score > 0:
        raise SequenceAlignmentError(
            "Mismatch and gap scores must be non-positive, with a negative gap-open score"
        )


def _normalize_mode(mode: AlignmentMode | str) -> Literal["global", "local", "placement"]:
    normalized = (mode or "placement").strip().lower()
    if normalized in {"global", "local"}:
        return normalized  # type: ignore[return-value]
    if normalized in {"placement", "fragment"}:
        return "placement"
    raise SequenceAlignmentError(f"Unsupported alignment mode: {mode}")


def _normalize_strand(strand: AlignmentStrand | str) -> AlignmentStrand:
    normalized = (strand or "auto").strip().lower()
    if normalized in {"auto", "forward", "reverse"}:
        return normalized  # type: ignore[return-value]
    raise SequenceAlignmentError(f"Unsupported alignment strand: {strand}")


def _is_rna(sequence: str) -> bool:
    return "U" in sequence and "T" not in sequence


def _reverse_complement(sequence: str) -> str:
    complements = _RNA_COMPLEMENTS if _is_rna(sequence) else _DNA_COMPLEMENTS
    return "".join(complements.get(base, base) for base in reversed(sequence))


def _build_aligner(settings: AlignmentSettings) -> Align.PairwiseAligner:
    aligner = Align.PairwiseAligner()
    aligner.match_score = settings.match_score
    aligner.mismatch_score = settings.mismatch_score
    aligner.open_gap_score = settings.gap_open_score
    aligner.extend_gap_score = settings.gap_extend_score

    mode = _normalize_mode(settings.mode)
    if mode == "local":
        aligner.mode = "local"
    else:
        aligner.mode = "global"
        if mode == "placement":
            # Overlap-style alignment: let the fragment float inside the reference
            # without turning unclipped flanks into terminal deletion events.
            aligner.target_end_open_gap_score = 0.0
            aligner.target_end_extend_gap_score = 0.0
            aligner.query_end_open_gap_score = 0.0
            aligner.query_end_extend_gap_score = 0.0

    return aligner


def _take_first_alignment(alignments: object) -> Align.Alignment | None:
    try:
        return next(iter(alignments))  # type: ignore[arg-type]
    except StopIteration:
        return None
    except OverflowError as exc:
        raise SequenceAlignmentError(
            "Alignment produced too many equally optimal solutions; try local mode or trim repetitive sequence content"
        ) from exc


def _alignment_columns(
    alignment: Align.Alignment,
    reference: str,
    query: str,
) -> list[tuple[int | None, int | None, str, str]]:
    columns: list[tuple[int | None, int | None, str, str]] = []
    coordinates = alignment.coordinates

    for index in range(coordinates.shape[1] - 1):
        ref_start = int(coordinates[0, index])
        ref_end = int(coordinates[0, index + 1])
        query_start = int(coordinates[1, index])
        query_end = int(coordinates[1, index + 1])

        ref_delta = ref_end - ref_start
        query_delta = query_end - query_start

        if ref_delta > 0 and query_delta > 0:
            for offset in range(ref_delta):
                columns.append(
                    (
                        ref_start + offset,
                        query_start + offset,
                        reference[ref_start + offset],
                        query[query_start + offset],
                    )
                )
        elif ref_delta > 0:
            for offset in range(ref_delta):
                columns.append((ref_start + offset, None, reference[ref_start + offset], "-"))
        elif query_delta > 0:
            for offset in range(query_delta):
                columns.append((None, query_start + offset, "-", query[query_start + offset]))

    return columns


def _aligned_strings_from_columns(
    columns: list[tuple[int | None, int | None, str, str]],
) -> tuple[str, str]:
    return (
        "".join(column[2] for column in columns),
        "".join(column[3] for column in columns),
    )


def _midline(reference_aligned: str, query_aligned: str) -> str:
    chars: list[str] = []
    for ref_base, query_base in zip(reference_aligned, query_aligned, strict=True):
        if ref_base == "-" or query_base == "-":
            chars.append(" ")
        elif ref_base == query_base:
            chars.append("|")
        else:
            chars.append(".")
    return "".join(chars)


def _count_metrics(reference_aligned: str, query_aligned: str) -> tuple[int, int, int, int]:
    matches = 0
    mismatches = 0
    gap_columns = 0
    aligned_columns = 0
    for ref_base, query_base in zip(reference_aligned, query_aligned, strict=True):
        if ref_base == "-" or query_base == "-":
            gap_columns += 1
            continue
        aligned_columns += 1
        if ref_base == query_base:
            matches += 1
        else:
            mismatches += 1
    return matches, mismatches, gap_columns, aligned_columns


def _trim_terminal_gap_columns(
    columns: list[tuple[int | None, int | None, str, str]],
) -> tuple[list[tuple[int | None, int | None, str, str]], dict[str, int]]:
    left = 0
    right = len(columns)
    trims = {
        "reference_left": 0,
        "reference_right": 0,
        "query_left": 0,
        "query_right": 0,
    }

    while left < right and (columns[left][0] is None or columns[left][1] is None):
        if columns[left][0] is not None:
            trims["reference_left"] += 1
        if columns[left][1] is not None:
            trims["query_left"] += 1
        left += 1

    while right > left and (columns[right - 1][0] is None or columns[right - 1][1] is None):
        if columns[right - 1][0] is not None:
            trims["reference_right"] += 1
        if columns[right - 1][1] is not None:
            trims["query_right"] += 1
        right -= 1

    return columns[left:right], trims


def _count_unique_positions(positions: list[int], modulus: int) -> int:
    if modulus <= 0:
        return len(positions)
    return len({position % modulus for position in positions})


def _normalize_reference_interval(
    raw_start: int,
    raw_end: int,
    reference_length: int,
    circular: bool,
) -> tuple[int, int, bool]:
    if reference_length <= 0 or not circular:
        return raw_start, raw_end, False

    while raw_start >= reference_length and raw_end >= reference_length:
        raw_start -= reference_length
        raw_end -= reference_length

    wraps_origin = raw_end > reference_length
    if wraps_origin:
        raw_end -= reference_length

    return raw_start, raw_end, wraps_origin


def _detect_variants(
    columns: list[tuple[int | None, int | None, str, str]],
    reference_length: int,
    circular: bool,
) -> list[dict[str, object]]:
    variants: list[dict[str, object]] = []
    active: dict[str, object] | None = None
    reference_cursor = 0
    query_cursor = 0

    def flush() -> None:
        nonlocal active
        if active is None:
            return

        active["reference"] = "".join(active["reference"])  # type: ignore[index]
        active["query"] = "".join(active["query"])  # type: ignore[index]
        reference_positions = active.pop("_reference_positions")  # type: ignore[assignment]

        if active["type"] == "insertion":
            raw_start = int(active["anchor"])
            raw_end = raw_start
        else:
            raw_start = reference_positions[0]
            raw_end = reference_positions[-1] + 1

        start, end, wraps_origin = _normalize_reference_interval(
            raw_start,
            raw_end,
            reference_length,
            circular,
        )
        active["start"] = start
        active["end"] = end
        active["reference_wraps_origin"] = wraps_origin

        ref_seq = str(active["reference"])
        query_seq = str(active["query"])
        if active["type"] == "insertion":
            active["label"] = f"ins {query_seq}"
            active["length"] = len(query_seq)
        elif active["type"] == "deletion":
            active["label"] = f"del {ref_seq}"
            active["length"] = len(ref_seq)
        else:
            active["label"] = f"{ref_seq}>{query_seq}"
            active["length"] = max(len(ref_seq), len(query_seq))

        active.pop("anchor", None)
        variants.append(active)
        active = None

    for reference_position, query_position, ref_base, query_base in columns:
        if ref_base == query_base and ref_base != "-":
            flush()
            if reference_position is not None:
                reference_cursor = reference_position + 1
            if query_position is not None:
                query_cursor = query_position + 1
            continue

        if reference_position is None:
            event_type = "insertion"
        elif query_position is None:
            event_type = "deletion"
        else:
            event_type = "substitution"

        anchor = reference_cursor if reference_position is None else reference_position
        if active is None or active["type"] != event_type:
            flush()
            active = {
                "type": event_type,
                "_reference_positions": [],
                "anchor": anchor,
                "query_start": query_cursor if query_position is None else query_position,
                "query_end": query_cursor if query_position is None else (query_position + 1),
                "reference": [],
                "query": [],
            }

        if reference_position is not None:
            active["_reference_positions"].append(reference_position)  # type: ignore[index]
            active["reference"].append(ref_base)  # type: ignore[index]
            reference_cursor = reference_position + 1
        else:
            active["_reference_positions"].append(anchor)  # type: ignore[index]

        if query_position is not None:
            active["query"].append(query_base)  # type: ignore[index]
            active["query_end"] = query_position + 1
            query_cursor = query_position + 1

    flush()
    return variants


def _select_candidate(
    reference_sequence: str,
    query_sequence: str,
    settings: AlignmentSettings,
) -> _AlignmentCandidate:
    mode = _normalize_mode(settings.mode)
    strand = _normalize_strand(settings.strand)
    reference_is_circular = settings.reference_is_circular and mode in {"placement", "local"}

    query_variants: list[tuple[Literal["forward", "reverse"], str]]
    if strand == "auto":
        query_variants = [
            ("forward", query_sequence),
            ("reverse", _reverse_complement(query_sequence)),
        ]
    elif strand == "reverse":
        query_variants = [("reverse", _reverse_complement(query_sequence))]
    else:
        query_variants = [("forward", query_sequence)]

    reference_search = (
        reference_sequence + reference_sequence
        if reference_is_circular
        else reference_sequence
    )
    aligner = _build_aligner(settings)

    best: _AlignmentCandidate | None = None
    best_key: tuple[float, int, int, int, int] | None = None

    for candidate_strand, oriented_query in query_variants:
        alignments = aligner.align(reference_search, oriented_query)
        alignment = _take_first_alignment(alignments)
        if alignment is None:
            continue

        columns = _alignment_columns(alignment, reference_search, oriented_query)
        if mode == "placement":
            columns, _ = _trim_terminal_gap_columns(columns)
        reference_positions = [position for position, _, _, _ in columns if position is not None]
        query_positions = [position for _, position, _, _ in columns if position is not None]

        current_key = (
            float(alignment.score),
            _count_unique_positions(query_positions, len(oriented_query)),
            _count_unique_positions(reference_positions, len(reference_sequence)),
            len(columns),
            1 if candidate_strand == "forward" else 0,
        )
        if best is None or current_key > best_key:  # type: ignore[operator]
            best = _AlignmentCandidate(
                alignment=alignment,
                reference_sequence=reference_search,
                original_reference_sequence=reference_sequence,
                query_sequence=oriented_query,
                strand=candidate_strand,
                mode=mode,
                reference_is_circular=reference_is_circular,
            )
            best_key = current_key

    if best is None:
        raise SequenceAlignmentError("No alignment could be computed for the provided sequences")
    return best


def _result_from_candidate(candidate: _AlignmentCandidate) -> dict[str, object]:
    reference_length = len(candidate.original_reference_sequence)
    columns = _alignment_columns(
        candidate.alignment,
        candidate.reference_sequence,
        candidate.query_sequence,
    )
    trim_info = {
        "reference_left": 0,
        "reference_right": 0,
        "query_left": 0,
        "query_right": 0,
    }
    if candidate.mode == "placement":
        columns, trim_info = _trim_terminal_gap_columns(columns)

    if not columns:
        raise SequenceAlignmentError("Alignment produced no comparable span")

    reference_aligned, query_aligned = _aligned_strings_from_columns(columns)
    midline = _midline(reference_aligned, query_aligned)
    matches, mismatches, gap_columns, aligned_columns = _count_metrics(reference_aligned, query_aligned)

    reference_positions = [position for position, _, _, _ in columns if position is not None]
    query_positions = [position for _, position, _, _ in columns if position is not None]
    raw_reference_start = reference_positions[0] if reference_positions else 0
    raw_reference_end = (reference_positions[-1] + 1) if reference_positions else 0
    reference_start, reference_end, wraps_origin = _normalize_reference_interval(
        raw_reference_start,
        raw_reference_end,
        reference_length,
        candidate.reference_is_circular,
    )
    query_start = query_positions[0] if query_positions else 0
    query_end = (query_positions[-1] + 1) if query_positions else 0

    variants = _detect_variants(
        columns,
        reference_length,
        candidate.reference_is_circular,
    )

    reference_aligned_bases = _count_unique_positions(reference_positions, reference_length)
    query_aligned_bases = _count_unique_positions(query_positions, len(candidate.query_sequence))
    reference_coverage = (
        round((reference_aligned_bases / reference_length) * 100, 2)
        if reference_length
        else 0.0
    )
    query_coverage = (
        round((query_aligned_bases / len(candidate.query_sequence)) * 100, 2)
        if candidate.query_sequence
        else 0.0
    )
    identity_pct = round((matches / aligned_columns) * 100, 2) if aligned_columns else 0.0

    return {
        "reference_sequence": candidate.original_reference_sequence,
        "query_sequence": candidate.query_sequence,
        "reference_aligned": reference_aligned,
        "query_aligned": query_aligned,
        "midline": midline,
        "score": float(candidate.alignment.score),
        "mode": candidate.mode,
        "strand": candidate.strand,
        "reference_start": reference_start,
        "reference_end": reference_end,
        "reference_wraps_origin": wraps_origin,
        "query_start": query_start,
        "query_end": query_end,
        "query_soft_clip_left": trim_info["query_left"],
        "query_soft_clip_right": trim_info["query_right"],
        "reference_flank_left": trim_info["reference_left"],
        "reference_flank_right": trim_info["reference_right"],
        "alignment_length": len(reference_aligned),
        "matches": matches,
        "mismatches": mismatches,
        "gap_columns": gap_columns,
        "aligned_columns": aligned_columns,
        "reference_aligned_bases": reference_aligned_bases,
        "query_aligned_bases": query_aligned_bases,
        "identity_pct": identity_pct,
        "ungapped_identity": identity_pct,
        "reference_coverage": reference_coverage,
        "query_coverage": query_coverage,
        "variants": variants,
    }


def align_sequences(
    reference_sequence: str,
    query_sequence: str,
    settings: AlignmentSettings | None = None,
) -> dict[str, object]:
    reference = clean_alignment_sequence(reference_sequence)
    query = clean_alignment_sequence(query_sequence)
    resolved = settings or AlignmentSettings()
    _validate_settings(resolved)
    candidate = _select_candidate(reference, query, resolved)
    result = _result_from_candidate(candidate)
    result["reference_sequence"] = reference
    result["query_sequence"] = query
    result["mode"] = _normalize_mode(resolved.mode)
    result["strand"] = candidate.strand
    return result
