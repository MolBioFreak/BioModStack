"""Audit actual coordinate changes rather than labeling a selected repack shell as changed."""
from __future__ import annotations


def changed_residues(original: dict, prepared: dict, *, tolerance_angstrom: float = 0.001) -> list[dict]:
    """Compare atom-coordinate maps keyed by (chain, number, insertion, residue, atom)."""
    if not original or original.keys() != prepared.keys():
        raise ValueError("PPIFlow preparation changed or omitted atom/chain/residue identity")
    changed = set()
    for (chain, number, icode, _resname, atom), point in original.items():
        other = prepared[(chain, number, icode, _resname, atom)]
        if any(abs(left - right) > tolerance_angstrom for left, right in zip(point, other)):
            changed.add((chain, number, icode))
    return [dict(chain=chain, number=number, insertion_code=icode) for chain, number, icode in sorted(changed)]
