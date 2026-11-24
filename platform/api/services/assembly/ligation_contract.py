"""Thin adapter from the registered ligation contract to the native ligation request.

Registered shape (``molbio-assembly-ligation-v1``): ``fragment_revision_ids``, ``orientations``,
``circular``, ``change_summary``. Native shape (``LigationAssemblyRequest``): ``fragments[]``,
``circular``, ``new_name``, ``save_description``. Each revision id becomes one full-source
fragment pinned to ``(document_id, revision_number)`` with its retained physical ends; the
route itself does not change.
"""
from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from molbio_models import MolecularDocument, MolecularRevision

from .physical_ends import read_physical_ends
from .types import FragmentEnd


def _end_dict(end: FragmentEnd | None) -> dict[str, Any] | None:
    if end is None:
        return None
    value: dict[str, Any] = {"type": end.type, "overhang": end.overhang, "label": end.label,
                             "protruding_strand": end.protruding_strand}
    if end.five_prime_phosphate != "unknown":
        value["five_prime_phosphate"] = end.five_prime_phosphate
    return value


async def materialize_ligation_request(session: AsyncSession, parameters: Mapping[str, Any]):
    """Return a native ``LigationAssemblyRequest`` for registered ligation parameters."""
    from routers.molbio_ops import LigationAssemblyRequest

    revision_ids = list(parameters["fragment_revision_ids"])
    orientations = list(parameters["orientations"])
    if len(orientations) != len(revision_ids):
        raise HTTPException(422, "orientations must have one entry per fragment revision")
    fragments: list[dict[str, Any]] = []
    for index, (revision_id, orientation) in enumerate(zip(revision_ids, orientations)):
        revision = await session.get(MolecularRevision, revision_id)
        if revision is None:
            raise HTTPException(404, f"Fragment revision was not found: {revision_id}")
        snapshot = revision.snapshot if isinstance(revision.snapshot, dict) else {}
        document = await session.get(MolecularDocument, revision.document_id)
        left, right = read_physical_ends(revision.provenance if isinstance(revision.provenance, dict) else {})
        name = str(snapshot.get("name") or (document.name if document else "") or f"fragment {index + 1}")
        fragments.append({
            "id": f"f{index + 1}", "name": name, "sequence": str(snapshot.get("sequence") or ""),
            "orientation": orientation, "circular": bool(snapshot.get("is_circular")),
            "source_sequence_id": revision.document_id, "source_name": name,
            "source_revision": revision.revision_number,
            "left_end": _end_dict(left), "right_end": _end_dict(right),
        })
    return LigationAssemblyRequest(
        fragments=fragments, circular=bool(parameters["circular"]),
        new_name=None, save_description=parameters.get("change_summary"),
    )
