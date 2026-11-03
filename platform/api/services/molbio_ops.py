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


@dataclass(frozen=True)
class PrimerBinding:
    start: int
    end: int
    anneal_length: int
    overhang_length: int


@dataclass
class PCRProductResult:
    sequence: str
    start: int
    end: int
    length: int
    wraps_origin: bool
    template_span: int
    forward_binding: PrimerBinding
    reverse_binding: PrimerBinding
    min_anneal_length: int


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
    """Maximal exact IUPAC-compatible 3′ suffix at every distinct extension site.

    A longer match at one site must not suppress a shorter qualifying match
    elsewhere. A minimum-length seed is extended toward the primer 5′ end at each site.
    """
    if not seq or not primer_seq or min_anneal_length < 1:
        return []
    if min_anneal_length > min(len(primer_seq), len(seq)):
        return []
    query = reverse_complement(primer_seq, sequence_type) if reverse else primer_seq
    seed = query[:min_anneal_length] if reverse else query[-min_anneal_length:]
    matches = []
    for seed_start in _find_pattern_positions_canonical(seq, seed, circular=circular):
        anneal_length = min_anneal_length
        while anneal_length < min(len(primer_seq), len(seq)):
            template_index = seed_start + anneal_length if reverse else seed_start - (anneal_length - min_anneal_length) - 1
            if not circular and not 0 <= template_index < len(seq):
                break
            query_index = anneal_length if reverse else len(query) - anneal_length - 1
            if not _bases_overlap(seq[template_index % len(seq)], query[query_index]):
                break
            anneal_length += 1
        start = seed_start if reverse else seed_start - (anneal_length - min_anneal_length)
        if circular:
            start %= len(seq)
        matches.append(PrimerBinding(
            start=start, end=start + anneal_length, anneal_length=anneal_length,
            overhang_length=len(primer_seq) - anneal_length,
        ))
    return sorted(matches, key=lambda site: (-site.anneal_length, site.start))


def pcr_product(
    template: str,
    primer_fwd: str,
    primer_rev: str,
    circular: bool = False,
    sequence_type: str = "dna",
    min_anneal_length: int = 8,
    forward_binding: tuple[int, int] | None = None,
    reverse_binding: tuple[int, int] | None = None,
) -> PCRProductResult:
    """Predict a DNA sequence from exact 3′ matches; not amplification yield.

    The complete primers supply the ends. The template supplies only the
    interval between their extension sites. Circular inverse-PCR products may
    include more than one template length when primer footprints overlap.
    """
    if sequence_type != "dna":
        raise ValueError("PCR prediction requires a DNA template. Convert RNA to cDNA in a reverse-transcription workflow first.")
    seq = canonicalize_nucleotide_sequence(template, "dna", allow_empty=False)
    fwd = canonicalize_nucleotide_sequence(primer_fwd, "dna", allow_empty=False)
    rev = canonicalize_nucleotide_sequence(primer_rev, "dna", allow_empty=False)
    if min_anneal_length < 1:
        raise ValueError("Minimum annealing length must be positive.")

    def bindings(primer: str, reverse: bool, selected: tuple[int, int] | None) -> list[PrimerBinding]:
        matches = _resolve_primer_binding_sites_canonical(
            seq, primer, reverse=reverse, circular=circular,
            min_anneal_length=min_anneal_length,
        )
        label = "Reverse" if reverse else "Forward"
        if selected is not None:
            matches = [b for b in matches if (b.start, b.anneal_length) == selected]
            if not matches:
                raise ValueError(f"{label} binding selection is not a maximal qualifying 3′ match on this template.")
        if not matches:
            raise ValueError(f"{label} primer has no binding site with at least {min_anneal_length} matching 3′ bases.")
        return matches

    fwd_sites = bindings(fwd, False, forward_binding)
    rev_sites = bindings(rev, True, reverse_binding)
    candidate: tuple[PrimerBinding, PrimerBinding, int] | None = None
    n = len(seq)
    for fb in fwd_sites:
        for rb in rev_sites:
            internal_span = rb.start - fb.end
            if circular:
                internal_span %= n
            elif internal_span < 0:
                # Their 3′ ends extend away from one another; not a standard
                # two-primer linear amplicon. Primer-dimer chemistry is separate.
                continue
            current = (fb, rb, internal_span)
            if candidate is not None and candidate != current:
                raise ValueError(
                    "Ambiguous PCR primer placement produced multiple distinct amplicons; "
                    "select explicit binding sites or increase the minimum annealing length."
                )
            candidate = current
    if candidate is None:
        raise ValueError("Primer 3′ ends do not face an intervening region on this linear template. Overlapping 3′ footprints and primer-dimer extension are outside this PCR sequence model.")

    fb, rb, internal_span = candidate
    internal_start = fb.end % n if circular else fb.end
    internal = (seq + seq)[internal_start:internal_start + internal_span] if circular else seq[internal_start:rb.start]
    product_sequence = fwd + internal + reverse_complement(rev)
    template_span = fb.anneal_length + internal_span + rb.anneal_length
    unwrapped_end = fb.start + template_span
    wraps = circular and unwrapped_end > n
    # Keep an ordinary endpoint at n (not zero) when it does not wrap.
    end = unwrapped_end % n if wraps else unwrapped_end
    return PCRProductResult(
        product_sequence, fb.start, end, len(product_sequence), wraps,
        template_span, fb, rb, min_anneal_length,
    )



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
