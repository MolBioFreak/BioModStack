"""Classify supplied Golden Gate parts as bare DNA or already carrying Type IIS sites.

A bare part has no usable site pair: the planner adds removable site flanks (order sequence or PCR tails).
A pre-configured part already carries the enzyme sites: a real digest finds the one fragment with two
enzyme-created cohesive ends, whose overhangs become fixed junction labels and whose sequence is used
exactly as supplied. Everything here is an observation or a selection aid; sites inside a payload,
a missing site or an unmatched overhang are reported, never turned into a new gate.
"""
from __future__ import annotations

from typing import Literal

from .common import reverse_complement
from .feature_types import Closed, Material
from .golden_gate_design import _digest, _scan
from .golden_gate_design_types import DesignMaterial, Geometry
from .types import AssemblyError

PartKind = Literal["bare", "prepared", "ambiguous", "invalid"]


class PartCandidate(Closed):
    fragment_index: int
    length: int
    left_overhang: str          # top-axis label of the junction on the fragment's left
    right_overhang: str         # top-axis label of the junction on the fragment's right


class PartReport(Closed):
    index: int
    name: str
    length: int
    topology: str
    enzyme_id: str
    enzyme_name: str
    requested_kind: str
    kind: PartKind
    site_count: int
    site_positions: list[int]
    left_overhang: str | None = None
    right_overhang: str | None = None
    retained_fragment_index: int | None = None
    payload_length: int | None = None
    candidates: list[PartCandidate] = []
    message: str


class PreparedInfo(Closed):
    retained_fragment_index: int
    removed_fragment_indices: list[int]
    payload: str
    left: str
    right: str


def _cohesive(fragment, width: int) -> bool:
    return (fragment.topology == "linear"
            and fragment.left_end.kind == "five_prime_overhang" and fragment.right_end.kind == "five_prime_overhang"
            and fragment.left_end.enzyme_created and fragment.right_end.enzyme_created
            and fragment.left_end.length_nt == width == fragment.right_end.length_nt)


def _candidate(fragment) -> PartCandidate:
    return PartCandidate(fragment_index=fragment.fragment_index, length=len(fragment.top_strand_sequence),
                         left_overhang=fragment.left_end.overhang_sequence_5to3.upper(),
                         right_overhang=reverse_complement(fragment.right_end.overhang_sequence_5to3).upper())


def classify_part(*, index: int, name: str, material: Material, enzyme, enzyme_id: str, geometry: Geometry,
                  requested: str = "auto", retained_index: int | None = None) -> tuple[PartReport, PreparedInfo | None]:
    width = geometry.overhang_length
    positions = sorted(o.site_start for o in _scan(material.sequence, material.topology, enzyme).occurrences)
    base = dict(index=index, name=name, length=len(material.sequence), topology=material.topology,
                enzyme_id=enzyme_id, enzyme_name=enzyme.name, requested_kind=requested,
                site_count=len(positions), site_positions=positions)

    def bare(message: str):
        return PartReport(**base, kind="bare", message=message), None

    def invalid(message: str):
        return PartReport(**base, kind="invalid", message=message), None

    if requested == "bare":
        return bare(f"Treated as bare DNA: {enzyme.name} flanks and overhangs will be added." if not positions else
                    f"Treated as bare DNA, but {len(positions)} {enzyme.name} site(s) are already inside it and will cut during assembly.")
    if geometry.polarity != "five_prime":
        message = f"{enzyme.name} leaves 3' overhangs; pre-configured parts are read for 5'-overhang enzymes only."
        return invalid(message + " Supply bare DNA.") if requested == "prepared" else bare(message + " Treated as bare DNA.")
    if not positions:
        return invalid(f"No {enzyme.name} site found, so this cannot be a pre-configured part.") if requested == "prepared" else \
            bare(f"No {enzyme.name} site: bare DNA. Overhangs and flanks will be added.")
    fragments = _digest(DesignMaterial(id="part", stage="source", transformation="source",
                                       sequence=material.sequence, topology=material.topology), enzyme).fragments
    candidates = [f for f in fragments if _cohesive(f, width)]
    shown = [_candidate(f) for f in candidates]
    chosen = None
    if retained_index is not None:
        chosen = next((f for f in candidates if f.fragment_index == retained_index), None)
        if chosen is None:
            return invalid(f"Fragment {retained_index} is not a {enzyme.name} fragment with two cohesive ends.")
    elif len(candidates) == 1:
        chosen = candidates[0]
    elif not candidates:
        message = (f"{len(positions)} {enzyme.name} site(s) found, but no digest fragment has two {enzyme.name}-cut "
                   f"cohesive ends (a single site, wrong orientation or sites facing outward).")
        return invalid(message) if requested == "prepared" else bare(message + " Treated as bare DNA.")
    if chosen is None:
        listing = "; ".join(f"fragment {c.fragment_index}: {c.length} bp, {c.left_overhang}…{c.right_overhang}" for c in shown)
        return PartReport(**base, kind="ambiguous", candidates=shown,
                          message=f"{len(shown)} fragments could be kept ({listing}). Choose which one to use."), None
    candidate = _candidate(chosen)
    info = PreparedInfo(retained_fragment_index=chosen.fragment_index,
                        removed_fragment_indices=[f.fragment_index for f in fragments if f.fragment_index != chosen.fragment_index],
                        payload=chosen.top_strand_sequence[width:], left=candidate.left_overhang, right=candidate.right_overhang)
    report = PartReport(**base, kind="prepared", left_overhang=candidate.left_overhang, right_overhang=candidate.right_overhang,
                        retained_fragment_index=chosen.fragment_index, payload_length=len(info.payload), candidates=shown,
                        message=f"Pre-configured: {enzyme.name} releases a {len(info.payload)} bp payload with overhangs "
                                f"{candidate.left_overhang} and {candidate.right_overhang}. Used exactly as supplied.")
    return report, info


def order_by_overhangs(labels: list[tuple[str, str]], names: list[str], *, circular: bool, start: str | None,
                       end: str | None, flippable: list[bool]) -> tuple[list[tuple[int, bool]], int]:
    """Order pieces so each right label equals the next left label.

    Returns ([(piece_index, flipped)], orders_found capped at 2). A flipped piece is used as its reverse
    complement, which swaps its labels to (rc(right), rc(left)); only pieces marked flippable may flip.
    circular: a closed ring that starts with piece 0 in the given orientation. start/end: labels the chain
    must begin and finish with (destination vector). Otherwise any open chain.
    """
    count = len(labels)
    found: list[list[tuple[int, bool]]] = []

    def labelled(i: int, flipped: bool) -> tuple[str, str]:
        left, right = labels[i]
        return (reverse_complement(right), reverse_complement(left)) if flipped else (left, right)

    def extend(path: list[tuple[int, bool]], used: set[int]) -> None:
        if len(found) >= 2:
            return
        if len(path) == count:
            first, last = labelled(*path[0]), labelled(*path[-1])
            if (last[1] == first[0]) if circular else (end is None or last[1] == end):
                found.append(list(path))
            return
        tail = labelled(*path[-1])[1]
        for j in range(count):
            if j in used:
                continue
            for flipped in ((False, True) if flippable[j] else (False,)):
                if labelled(j, flipped)[0] == tail:
                    extend(path + [(j, flipped)], used | {j})

    if circular:
        starts = [(0, False)]
    else:
        starts = [(i, f) for i in range(count) for f in ((False, True) if flippable[i] else (False,))
                  if start is None or labelled(i, f)[0] == start]
    for first in starts:
        extend([first], {first[0]})
    if not found:
        stuck = [names[i] for i in range(count)
                 if not any(labelled(j, f)[1] == labelled(i, False)[0] for j in range(count) if j != i
                            for f in ((False, True) if flippable[j] else (False,)))]
        hint = f" No part ends with the overhang that starts: {', '.join(stuck)}." if stuck else ""
        raise AssemblyError("The parts do not form a chain by their overhangs." + hint)
    return found[0], len(found)


def order_rows(design, parts, payload_lengths: dict[str, int]) -> list[dict]:
    """One row per part: what to order (bare parts) or what is used as supplied (prepared parts)."""
    materials = {m.id: m for m in design.materials}
    rows = []
    for outcome in design.preparations:
        part = next(p for p in parts if p.id == outcome.part_id)
        digest = next((d for d in design.digests if d.part_id == outcome.part_id), None)
        kept = digest.fragments[digest.retained_fragment_index] if digest and digest.retained_fragment_index is not None else None
        order = materials.get(outcome.prepared_material_id) if part.preparation.kind in ("pcr", "synthesis") else None
        rows.append(dict(part_id=outcome.part_id, name=part.name, preparation=part.preparation.kind,
                         part_kind="prepared" if part.preparation.kind == "donor" else "bare",
                         order_sequence=order.sequence if order else None, tailed_length=len(order.sequence) if order else None,
                         payload_length=payload_lengths.get(outcome.part_id),
                         primers=[p.full_sequence for p in design.primers if p.part_id == outcome.part_id],
                         digested_sequence=kept.top_strand_sequence if kept else None,
                         left_overhang=kept.left_end.overhang_sequence_5to3 if kept else None,
                         right_overhang=kept.right_end.overhang_sequence_5to3 if kept else None))
    return rows


def order_summary(rows: list[dict], method: str) -> dict:
    orderable = [r for r in rows if r.get("order_sequence")]
    return dict(bare_parts=len(orderable), prepared_parts=sum(1 for r in rows if r["part_kind"] == "prepared"),
                order_lengths=[r["tailed_length"] for r in orderable], order_total_bp=sum(r["tailed_length"] for r in orderable),
                method=method)
