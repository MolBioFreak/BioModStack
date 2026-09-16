"""Core molecular biology operations (digest, PCR, ligation, mutagenesis)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

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
    return _find_pattern_positions_canonical(clean_sequence(sequence), clean_sequence(pattern), circular)


def _find_pattern_positions_canonical(seq: str, pat: str, circular: bool = False) -> List[int]:
    """Internal search on request-local validated nucleotide strings."""
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
    return _resolve_primer_binding_sites_canonical(
        clean_sequence(template), clean_sequence(primer), reverse, circular,
        sequence_type, min_anneal_length,
    )


def _resolve_primer_binding_sites_canonical(
    seq: str, primer_seq: str, reverse: bool = False, circular: bool = False,
    sequence_type: str = "dna", min_anneal_length: int = 8,
) -> List[PrimerBinding]:
    if not seq or not primer_seq:
        return []

    minimum = max(1, min(len(primer_seq), min_anneal_length))
    for anneal_length in range(len(primer_seq), minimum - 1, -1):
        anneal_sequence = primer_seq[-anneal_length:]
        query = reverse_complement(anneal_sequence, sequence_type) if reverse else anneal_sequence
        positions = _find_pattern_positions_canonical(seq, query, circular=circular)
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

    fwd_sites = _resolve_primer_binding_sites_canonical(
        seq,
        fwd,
        reverse=False,
        circular=circular,
        sequence_type=sequence_type,
    )
    if not fwd_sites:
        raise ValueError("Forward primer not found in template.")

    rev_sites = _resolve_primer_binding_sites_canonical(
        seq,
        rev,
        reverse=True,
        circular=circular,
        sequence_type=sequence_type,
    )
    if not rev_sites:
        raise ValueError("Reverse primer binding site not found in template.")

    # Each resolver returns a single anneal length, hence constant overhangs.
    # Start/end/wrap geometry therefore determines the exact product bytes.
    # Reject on the second distinct geometry without allocating either amplicon.
    geometry: tuple[int, int, bool, int] | None = None
    template_length = len(seq)
    for fwd_binding in fwd_sites:
        for rev_binding in rev_sites:
            start, end = fwd_binding.start, rev_binding.end
            if circular:
                span = (end - start) % template_length or template_length
                wraps = start + span > template_length
                end = (start + span) % template_length
            else:
                if end <= start:
                    continue
                span, wraps = end - start, False
            current = (start, end, wraps, span)
            if geometry is not None and geometry != current:
                raise ValueError(
                    "Ambiguous PCR primer placement produced multiple distinct amplicons; "
                    "explicit binding sites are required."
                )
            geometry = current

    if geometry is None:
        if circular:
            raise ValueError("Unable to construct a circular-template PCR product from the primer pair.")
        raise ValueError("Reverse primer site occurs before forward primer (linear PCR expected).")
    start, end, wraps, span = geometry
    segment = seq[start:] + seq[:end] if wraps else seq[start:start + span]
    product_sequence = (
        fwd[:fwd_sites[0].overhang_length] + segment
        + reverse_complement(rev[:rev_sites[0].overhang_length], sequence_type)
    )
    return PCRProductResult(product_sequence, start, end, len(product_sequence), wraps)



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
