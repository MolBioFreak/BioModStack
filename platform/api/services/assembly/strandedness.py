"""Strandedness of assembled products, derived from the inputs that are known."""
from __future__ import annotations

from typing import Iterable, Optional


def assembled_strandedness(input_values: Iterable[Optional[str]]) -> str:
    """Assembly simulators join duplex fragments, so the product is double
    unless an input is explicitly single-stranded (then it is not claimed
    double: all-single stays single, a mix is unknown). Unknown/absent input
    strandedness is not evidence of single strands."""
    values = [v for v in input_values if v]
    singles = [v for v in values if v == "single"]
    if not singles:
        return "double"
    return "single" if len(singles) == len(values) else "unknown"
