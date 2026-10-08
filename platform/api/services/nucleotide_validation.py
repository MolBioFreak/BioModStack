"""Strict shared nucleotide canonicalization for Mol Bio public inputs."""
from __future__ import annotations

DNA_IUPAC = frozenset("ACGTRYSWKMBDHVN")
RNA_IUPAC = frozenset("ACGURYSWKMBDHVN")


def canonicalize_nucleotide_sequence(
    sequence: str,
    sequence_type: str,
    *,
    allow_empty: bool = False,
) -> str:
    """Canonicalize DNA/RNA without silently deleting scientific input.

    Formatting whitespace is ignored. T/U are converted according to the declared
    polymer. Every other character must be a valid type-specific IUPAC code.
    """

    if not isinstance(sequence, str):
        raise ValueError("Sequence must be text")
    polymer = str(sequence_type or "dna").strip().lower()
    if polymer not in {"dna", "rna"}:
        raise ValueError("Sequence type must be 'dna' or 'rna'")

    compact = "".join(character for character in sequence.upper() if not character.isspace())
    compact = compact.replace("T", "U") if polymer == "rna" else compact.replace("U", "T")
    valid = RNA_IUPAC if polymer == "rna" else DNA_IUPAC
    invalid = sorted(set(compact).difference(valid))
    if invalid:
        rendered = ", ".join(repr(character) for character in invalid)
        raise ValueError(f"Sequence contains invalid nucleotide characters: {rendered}")
    if not compact and not allow_empty:
        raise ValueError("Sequence must contain at least one nucleotide")
    return compact
