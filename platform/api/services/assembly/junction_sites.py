"""Observation only: does each joined junction regenerate or destroy a selected enzyme's site?

Runs the existing restriction analysis on the assembled product and reports whether a
recognition site spans each junction. Nothing here decides whether fragments join.
"""
from __future__ import annotations

from typing import Any, Sequence

from services.restriction_analysis import analyze_sequence
from services.restriction_catalog import CatalogView, RestrictionRecord, catalog_authority

from .types import AssemblyError, AssemblyProduct


def resolve_records(enzymes: Sequence[str], catalog: CatalogView | None = None) -> tuple[CatalogView, list[RestrictionRecord]]:
    catalog = catalog or catalog_authority.require()
    records: list[RestrictionRecord] = []
    for name in enzymes:
        record = catalog.by_id.get(name) or catalog.by_name_casefold.get(name.casefold())
        if record is None:
            raise AssemblyError(f"Unknown restriction enzyme '{name}'")
        if record not in records:
            records.append(record)
    return catalog, records


def _spans(site_start: int, site_end: int, junction: int, length: int, circular: bool) -> bool:
    shifts = (0, length) if circular else (0,)
    return any(site_start < junction + shift < site_end for shift in shifts)


def observe_junction_sites(product: AssemblyProduct, enzymes: Sequence[str]) -> list[dict[str, Any]]:
    """One entry per product junction, in junction order.

    ``regenerated``: selected enzymes whose recognition site spans the junction.
    ``destroyed``: selected enzymes whose end made this junction but whose site is not restored.
    ``note`` says "not cleavable by either enzyme" for a hybrid junction.
    """
    catalog, records = resolve_records(enzymes)
    if not records or not product.junctions:
        return []
    length = len(product.sequence)
    analysis = analyze_sequence(sequence=product.sequence, topology="circular" if product.circular else "linear",
                                catalog=catalog, records=records, include_possible_sites=True)
    def end_enzymes(fragment, side: str) -> list[str] | None:
        recorded = (fragment.metadata or {}).get("end_enzymes")
        if not recorded:
            return None
        if fragment.orientation == "reverse":  # a rotated fragment swaps which end faces which neighbour
            side = "right" if side == "left" else "left"
        return list(recorded[side])

    positions: list[int] = []
    cursor = 0
    for fragment in product.fragments[:-1]:
        cursor += len(fragment.sequence)
        positions.append(cursor)
    if product.circular:
        positions.append(0)
    result: list[dict[str, Any]] = []
    fragments = product.fragments
    for index, (junction, position) in enumerate(zip(product.junctions, positions)):
        left_fragment, right_fragment = fragments[index], fragments[(index + 1) % len(fragments)]
        made_by = (end_enzymes(left_fragment, "right") or []) + (end_enzymes(right_fragment, "left") or [])
        regenerated = sorted({
            occurrence.enzyme_id for occurrence in analysis.occurrences
            if _spans(occurrence.site_start, occurrence.site_end_unwrapped, position, length, product.circular)
        })
        names = {record.enzyme_id: record.canonical_name for record in records}
        relevant = set(made_by) & set(names) or set(names)
        destroyed = sorted(relevant - set(regenerated))
        note = ("Junction regenerates " + ", ".join(names[i] for i in regenerated) + " site") if regenerated else \
            ("Junction is not cleavable by " + (" or ".join(names[i] for i in destroyed) if len(destroyed) > 1 else names[destroyed[0]]) +
             (" (hybrid site)" if len(destroyed) > 1 else ""))
        if len(destroyed) > 1 and not regenerated:
            note = "Junction is not cleavable by either enzyme (hybrid site)" if len(destroyed) == 2 else \
                "Junction is not cleavable by any selected enzyme"
        result.append({
            "left_fragment_id": junction.left_fragment_id, "right_fragment_id": junction.right_fragment_id,
            "position": position, "regenerated": regenerated, "destroyed": destroyed, "note": note,
        })
    return result
