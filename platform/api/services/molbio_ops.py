"""Core molecular biology operations (digest, PCR, ligation, mutagenesis)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict


DNA_COMPLEMENT = str.maketrans("ATCGNatcgn", "TAGCNtagcn")


def clean_sequence(seq: str) -> str:
    return "".join(c for c in seq.upper().replace(" ", "").replace("\n", "").replace("\r", "") if c in "ATCGN")


def reverse_complement(seq: str) -> str:
    return clean_sequence(seq).translate(DNA_COMPLEMENT)[::-1]


def find_all(seq: str, sub: str) -> List[int]:
    """Return all start indices of substring occurrences (including overlaps)."""
    indices: List[int] = []
    start = 0
    while True:
        idx = seq.find(sub, start)
        if idx == -1:
            break
        indices.append(idx)
        start = idx + 1
    return indices


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
        for site_start in find_all(seq, site):
            cut_pos = site_start + cut_offset
            if 0 <= cut_pos <= len(seq):
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


def pcr_product(template: str, primer_fwd: str, primer_rev: str) -> str:
    seq = clean_sequence(template)
    fwd = clean_sequence(primer_fwd)
    rev = clean_sequence(primer_rev)
    if not (seq and fwd and rev):
        raise ValueError("Template and primers must be non-empty.")

    fwd_start = seq.find(fwd)
    if fwd_start == -1:
        raise ValueError("Forward primer not found in template.")

    rev_rc = reverse_complement(rev)
    rev_start = seq.find(rev_rc)
    if rev_start == -1:
        raise ValueError("Reverse primer binding site not found in template.")

    if rev_start < fwd_start:
        raise ValueError("Reverse primer site occurs before forward primer (linear PCR expected).")

    product_end = rev_start + len(rev_rc)
    return seq[fwd_start:product_end]


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
    for mut in mutations:
        pos = mut.get("pos")
        if not isinstance(pos, int) or pos < 1 or pos > len(seq):
            raise ValueError(f"Invalid mutation position: {pos}")
        idx = pos - 1
        from_base = mut.get("from")
        to_base = mut.get("to")
        if to_base is None:
            raise ValueError("Mutation missing 'to' base.")
        if from_base and seq[idx] != from_base.upper():
            raise ValueError(f"Mismatch at position {pos}: expected {from_base}, found {seq[idx]}")
        seq[idx] = to_base.upper()
    return "".join(seq)
