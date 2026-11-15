"""Tm labelling for Gibson design: the model actually used, never a different one."""
from __future__ import annotations

from pydna.tm import tm_default

TM_MODEL = (
    "pydna tm_default: Biopython Tm_NN, DNA_NN4 (SantaLucia & Hicks 2004), "
    "Owczarzy 2008 salt correction"
)
TM_ASSUMPTIONS = {
    "primer_nM_each": 250.0, "Na_mM": 40.0, "K_mM": 0.0, "Tris_mM": 75.0,
    "Mg_mM": 1.5, "dNTPs_mM": 0.8, "salt_correction": 7,
}


def primer_tm(sequence: str) -> float | None:
    try:
        return round(float(tm_default(sequence)), 2)
    except Exception:
        return None


def rule_2_4(sequence: str) -> float:
    """NEB rule of thumb: 2 C per A/T, 4 C per G/C."""
    gc = sum(base in "GC" for base in sequence.upper())
    return float(2 * (len(sequence) - gc) + 4 * gc)


NEB_MIN_OVERLAP_TM = 48.0
GC_RANGE = (40.0, 65.0)
LOW_COMPLEXITY_WINDOW = 8


def gc_percent(sequence: str) -> float:
    sequence = sequence.upper()
    return round(100.0 * sum(base in "GC" for base in sequence) / len(sequence), 1) if sequence else 0.0


def _extra_copies(overlap: str, product: str, circular: bool, limit: int = 3) -> int:
    haystack = product + product[: len(overlap) - 1] if circular else product
    found, start = 0, haystack.find(overlap)
    while start != -1 and found <= limit:
        found += 1
        start = haystack.find(overlap, start + 1)
    return max(0, found - 1)


def overlap_observations(overlap: str, product: str, circular: bool) -> list[str]:
    """Plain observations about an overlap; they inform the user and never block a design."""
    if not overlap:
        return []
    notes: list[str] = []
    tm = rule_2_4(overlap)
    if tm < NEB_MIN_OVERLAP_TM:
        notes.append(f"Overlap Tm by the NEB 2/4 rule is {tm:.0f} C, below the 48 C NEB aims for")
    gc = gc_percent(overlap)
    if not GC_RANGE[0] <= gc <= GC_RANGE[1]:
        notes.append(f"Overlap GC is {gc:.0f} %, outside {GC_RANGE[0]:.0f}-{GC_RANGE[1]:.0f} %")
    head = overlap.upper()[:LOW_COMPLEXITY_WINDOW]
    if len(head) == LOW_COMPLEXITY_WINDOW and len(set(head)) <= 2:
        notes.append(f"The 5' end of the overlap ({head}) is low complexity (two bases or fewer)")
    extra = _extra_copies(overlap.upper(), product.upper(), circular)
    if extra:
        notes.append(
            f"The overlap sequence also occurs {'3 or more' if extra > 2 else extra} more time"
            f"{'' if extra == 1 else 's'} in the assembled product, so the ends could anneal at the wrong place"
        )
    return notes
