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
