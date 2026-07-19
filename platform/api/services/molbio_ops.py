"""Core molecular biology operations (digest, PCR, ligation, mutagenesis)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from services.nucleotide_validation import canonicalize_nucleotide_sequence


IUPAC_BASES: Dict[str, set[str]] = {
    "A": {"A"},
    "C": {"C"},
    "G": {"G"},
    "T": {"T"},
    "U": {"T"},
    "R": {"A", "G"},
    "Y": {"C", "T"},
    "S": {"G", "C"},
    "W": {"A", "T"},
    "K": {"G", "T"},
    "M": {"A", "C"},
    "B": {"C", "G", "T"},
    "D": {"A", "G", "T"},
    "H": {"A", "C", "T"},
    "V": {"A", "C", "G"},
    "N": {"A", "C", "G", "T"},
}

IUPAC_COMPLEMENT = str.maketrans(
    "ACGTRYSWKMBDHVNUacgtryswkmbdhvnu",
    "TGCAYRSWMKVHDBNAtgcayrswmkvhdbna",
)

RNA_IUPAC_COMPLEMENT = str.maketrans(
    "ACGTRYSWKMBDHVNUacgtryswkmbdhvnu",
    "UGCAYRSWMKVHDBNAugcayrswmkvhdbna",
)


def clean_sequence(seq: str) -> str:
    """Normalize sequence text while preserving valid IUPAC ambiguity codes."""
    sequence_type = "rna" if "U" in seq.upper() and "T" not in seq.upper() else "dna"
    return canonicalize_nucleotide_sequence(
        seq,
        sequence_type,
        allow_empty=True,
    )


def reverse_complement(seq: str, sequence_type: str = "dna") -> str:
    complement = RNA_IUPAC_COMPLEMENT if sequence_type == "rna" else IUPAC_COMPLEMENT
    return clean_sequence(seq).translate(complement)[::-1]


def _bases_overlap(template_base: str, pattern_base: str) -> bool:
    template_set = IUPAC_BASES.get(template_base, {template_base})
    pattern_set = IUPAC_BASES.get(pattern_base, {pattern_base})
    return bool(template_set.intersection(pattern_set))


def _matches_pattern(sequence: str, pattern: str, start: int) -> bool:
    return all(
        _bases_overlap(sequence[start + idx], pattern_base)
        for idx, pattern_base in enumerate(pattern)
    )


def find_pattern_positions(sequence: str, pattern: str, circular: bool = False) -> List[int]:
    """Return all pattern start indices, including IUPAC-aware circular matches."""
    seq = clean_sequence(sequence)
    pat = clean_sequence(pattern)
    if not seq or not pat:
        return []
    if len(pat) > len(seq):
        return []

    if circular:
        search_space = seq + seq[: len(pat) - 1]
        max_start = len(seq)
    else:
        search_space = seq
        max_start = len(seq) - len(pat) + 1

    return [
        start
        for start in range(max_start)
        if _matches_pattern(search_space, pat, start)
    ]


@dataclass
class DigestEnzyme:
    name: str
    site: str
    cut_index: Optional[int] = None  # offset from site start


@dataclass
class DigestFragment:
    sequence: str
    start: int
    end: int


@dataclass
class PCRProductResult:
    sequence: str
    start: int
    end: int
    length: int
    wraps_origin: bool = False


@dataclass
class PrimerBinding:
    start: int
    end: int
    anneal_length: int
    overhang_length: int


def resolve_primer_binding_sites(
    template: str,
    primer: str,
    reverse: bool = False,
    circular: bool = False,
    sequence_type: str = "dna",
    min_anneal_length: int = 8,
) -> List[PrimerBinding]:
    seq = clean_sequence(template)
    primer_seq = clean_sequence(primer)
    if not seq or not primer_seq:
        return []

    minimum = max(1, min(len(primer_seq), min_anneal_length))
    for anneal_length in range(len(primer_seq), minimum - 1, -1):
        anneal_sequence = primer_seq[-anneal_length:]
        query = reverse_complement(anneal_sequence, sequence_type) if reverse else anneal_sequence
        positions = find_pattern_positions(seq, query, circular=circular)
        if positions:
            return [
                PrimerBinding(
                    start=position,
                    end=position + anneal_length,
                    anneal_length=anneal_length,
                    overhang_length=len(primer_seq) - anneal_length,
                )
                for position in positions
            ]

    return []


def digest_sequence(sequence: str, enzymes: List[DigestEnzyme], circular: bool = False) -> List[DigestFragment]:
    seq = clean_sequence(sequence)
    if not seq:
        return []

    cut_positions: List[int] = []
    for enzyme in enzymes:
        site = clean_sequence(enzyme.site)
        if not site:
            continue
        cut_offset = enzyme.cut_index if enzyme.cut_index is not None else max(1, len(site) // 2)
        patterns = {site, reverse_complement(site)}
        for pattern in patterns:
            for site_start in find_pattern_positions(seq, pattern, circular=circular):
                cut_pos = site_start + cut_offset
                if circular:
                    cut_positions.append(cut_pos % len(seq))
                elif 0 <= cut_pos <= len(seq):
                    cut_positions.append(cut_pos)

    cut_positions = sorted(set(cut_positions))
    if not cut_positions:
        return [DigestFragment(sequence=seq, start=0, end=len(seq))]

    if not circular:
        cut_positions = [0] + cut_positions + [len(seq)]
        fragments = []
        for i in range(len(cut_positions) - 1):
            start = cut_positions[i]
            end = cut_positions[i + 1]
            if start == end:
                continue
            fragments.append(DigestFragment(sequence=seq[start:end], start=start, end=end))
        return fragments

    # Circular: wrap around
    cut_positions = sorted(cut_positions)
    fragments = []
    for i in range(len(cut_positions)):
        start = cut_positions[i]
        end = cut_positions[(i + 1) % len(cut_positions)]
        if start < end:
            frag_seq = seq[start:end]
        else:
            frag_seq = seq[start:] + seq[:end]
        fragments.append(DigestFragment(sequence=frag_seq, start=start, end=end))
    return fragments


def pcr_product(
    template: str,
    primer_fwd: str,
    primer_rev: str,
    circular: bool = False,
    sequence_type: str = "dna",
) -> PCRProductResult:
    seq = clean_sequence(template)
    fwd = clean_sequence(primer_fwd)
    rev = clean_sequence(primer_rev)
    if not (seq and fwd and rev):
        raise ValueError("Template and primers must be non-empty.")

    fwd_sites = resolve_primer_binding_sites(
        seq,
        fwd,
        reverse=False,
        circular=circular,
        sequence_type=sequence_type,
    )
    if not fwd_sites:
        raise ValueError("Forward primer not found in template.")

    rev_sites = resolve_primer_binding_sites(
        seq,
        rev,
        reverse=True,
        circular=circular,
        sequence_type=sequence_type,
    )
    if not rev_sites:
        raise ValueError("Reverse primer binding site not found in template.")

    candidates: list[PCRProductResult] = []
    template_length = len(seq)
    fwd_overhang_prefix = lambda binding: fwd[: binding.overhang_length]
    rev_overhang_suffix = lambda binding: reverse_complement(rev[: binding.overhang_length], sequence_type)

    for fwd_binding in fwd_sites:
        for rev_binding in rev_sites:
            fwd_start = fwd_binding.start
            rev_end = rev_binding.end

            if circular:
                template_span = (rev_end - fwd_start) % template_length
                if template_span == 0:
                    template_span = template_length
                wraps_origin = fwd_start + template_span > template_length
                wrapped_end = (fwd_start + template_span) % template_length
                if wraps_origin:
                    template_segment = seq[fwd_start:] + seq[:wrapped_end]
                else:
                    template_segment = seq[fwd_start:fwd_start + template_span]
                product_sequence = (
                    fwd_overhang_prefix(fwd_binding)
                    + template_segment
                    + rev_overhang_suffix(rev_binding)
                )
                candidate = PCRProductResult(
                    sequence=product_sequence,
                    start=fwd_start,
                    end=wrapped_end,
                    length=len(product_sequence),
                    wraps_origin=wraps_origin,
                )
            else:
                if rev_end <= fwd_start:
                    continue
                template_segment = seq[fwd_start:rev_end]
                product_sequence = (
                    fwd_overhang_prefix(fwd_binding)
                    + template_segment
                    + rev_overhang_suffix(rev_binding)
                )
                candidate = PCRProductResult(
                    sequence=product_sequence,
                    start=fwd_start,
                    end=rev_end,
                    length=len(product_sequence),
                    wraps_origin=False,
                )

            candidates.append(candidate)

    if not candidates:
        if circular:
            raise ValueError("Unable to construct a circular-template PCR product from the primer pair.")
        raise ValueError("Reverse primer site occurs before forward primer (linear PCR expected).")

    distinct_candidates = {
        (
            candidate.start,
            candidate.end,
            candidate.wraps_origin,
            candidate.sequence,
        ): candidate
        for candidate in candidates
    }
    if len(distinct_candidates) > 1:
        raise ValueError(
            "Ambiguous PCR primer placement produced "
            f"{len(distinct_candidates)} distinct amplicons; explicit binding sites are required."
        )
    return next(iter(distinct_candidates.values()))


def ligate_fragments(fragments: List[str], circular: bool = True) -> str:
    if not fragments:
        return ""
    seq = "".join(clean_sequence(f) for f in fragments)
    return seq


def gibson_assembly(fragments: List[str], overlap_length: int = 20) -> str:
    if not fragments:
        return ""
    if overlap_length < 0:
        raise ValueError("overlap_length must be >= 0")
    assembled = clean_sequence(fragments[0])
    for frag in fragments[1:]:
        frag_clean = clean_sequence(frag)
        if overlap_length > 0:
            prev_overlap = assembled[-overlap_length:]
            next_overlap = frag_clean[:overlap_length]
            if prev_overlap != next_overlap:
                raise ValueError("Gibson overlap mismatch between fragments.")
            assembled += frag_clean[overlap_length:]
        else:
            assembled += frag_clean
    return assembled


def golden_gate_assembly(fragments: List[str], enzymes: List[DigestEnzyme]) -> str:
    # Remove recognition sites (simplified) then ligate
    cleaned = []
    for frag in fragments:
        frag_seq = clean_sequence(frag)
        for enzyme in enzymes:
            site = clean_sequence(enzyme.site)
            if site:
                frag_seq = frag_seq.replace(site, "")
        cleaned.append(frag_seq)
    return ligate_fragments(cleaned, circular=True)


def apply_mutations(sequence: str, mutations: List[Dict]) -> str:
    seq = list(clean_sequence(sequence))
    sequence_type = "rna" if "U" in seq and "T" not in seq else "dna"
    for mut in mutations:
        pos = mut.get("pos")
        if not isinstance(pos, int) or pos < 1 or pos > len(seq):
            raise ValueError(f"Invalid mutation position: {pos}")
        idx = pos - 1
        from_base = mut.get("from")
        to_base = mut.get("to")
        if to_base is None:
            raise ValueError("Mutation missing 'to' base.")
        normalized_to = canonicalize_nucleotide_sequence(
            str(to_base), sequence_type, allow_empty=False
        )
        normalized_from = (
            canonicalize_nucleotide_sequence(
                str(from_base), sequence_type, allow_empty=False
            )
            if from_base is not None
            else None
        )
        if len(normalized_to) != 1 or (
            normalized_from is not None and len(normalized_from) != 1
        ):
            raise ValueError("Mutations require exactly one source and replacement residue.")
        if normalized_from and seq[idx] != normalized_from:
            raise ValueError(
                f"Mismatch at position {pos}: expected {normalized_from}, found {seq[idx]}"
            )
        seq[idx] = normalized_to
    return "".join(seq)
