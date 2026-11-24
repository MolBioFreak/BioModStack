"""Overhangs NEB's Golden Gate design tool will not use, and how BMS treats them.

NEB's tool (NEBridge Golden Gate Assembly Tool) rejects AAAA, CCCC, TAAA and their reverse complements TTTT, GGGG,
TTTA, and reports a junction on any of them as unusable (fidelity 0). NEB's published guidance calls AAAA, CCCC and the
TAAA/TTTA pair notably low efficiency. The tool's code lists the 3 nt analogues AAA, CCC, TAA (and reverse complements)
for 3 nt enzymes such as SapI.

BMS treats the list as a preference for overhangs it chooses itself, and as an observation for overhangs it does not:
a fixed boundary, a terminal or standard overhang you supply, a pre-configured part, a vector end. It never moves or
refuses those, and it never refuses a design because every choice is listed.
"""
from __future__ import annotations

from .common import reverse_complement

_BASE = {4: ("AAAA", "CCCC", "TAAA"), 3: ("AAA", "CCC", "TAA")}

KEPT_BECAUSE = {
    "fixed": "It is kept as the fixed boundary you authored.",
    "terminal": "It is kept as the terminal overhang you supplied.",
    "standard": "It is kept as a standard overhang you imposed.",
    "user": "It is kept as the overhang you supplied for this part.",
    "prepared": "It is kept because the pre-configured part already carries it.",
    "vector": "It is kept because the destination vector's closure end carries it.",
    "optimizer": "BMS found no other choice for this junction within the limits it was given.",
}


def listed_overhangs(width: int) -> list[str]:
    """Every listed overhang of this length, reverse complements included."""
    base = _BASE.get(width, ())
    return sorted({*base, *(reverse_complement(x) for x in base)})


def is_low_efficiency(overhang: str) -> bool:
    text = (overhang or "").upper()
    return bool(text) and text in listed_overhangs(len(text))


def low_efficiency_note(overhang: str, origin: str, junction: int | None = None) -> str:
    label = overhang.upper()
    where = f" at junction {junction}" if junction else ""
    return (f"Low-efficiency overhang {label}{where}: NEB's Golden Gate design tool does not use {label} or its reverse "
            f"complement {reverse_complement(label)}. {KEPT_BECAUSE[origin]} Observation only; the design is unchanged.")
