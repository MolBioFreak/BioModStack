"""Reusable children of a saved Gibson design: amplicons and supplied ready
fragments retain their own document + revision (no shelf projection)."""
from __future__ import annotations

import uuid
from typing import Any, Literal

from sqlalchemy import func, select

from molbio_models import (
    MolecularDocument, MolecularOperation, MolecularOperationOutput, MolecularRevision, NucleotideSequence,
)
from services.molbio_authority import SERVER_OWNED_ACTOR
from services.molbio_persistence import sequence_snapshot, sha256_text

from .feature_mapping import map_stored_features
from .physical_ends import with_unphosphorylated_blunt_ends, write_physical_ends
from .product_content import inline_original_length, oriented_coordinates


def _core_positions(fragment, source_length: int, circular: bool) -> list[int]:
    start, end = fragment.source_start, fragment.source_end
    if start is None or end is None:
        positions = list(range(source_length))
    elif fragment.source_wraps_origin:
        positions = list(range(start, source_length)) + list(range(end))
    else:
        positions = list(range(start, end))
    return positions[::-1] if fragment.orientation == "reverse" else positions


async def save_designed_fragments(session, operation_id: str, result, fragments, source_revisions) -> list[str]:
    """``fragments`` are oriented product fragments; ``source_revisions`` maps
    (source_sequence_id, revision_number) to the resolved MolecularRevision."""
    operation = await session.get(MolecularOperation, operation_id)
    if operation is None:
        return []
    position = (await session.execute(
        select(func.count()).select_from(MolecularOperationOutput).where(MolecularOperationOutput.operation_id == operation_id)
    )).scalar_one()
    by_id = {f.id: f for f in fragments}
    primers = {p.id: p for p in result.primers}
    created, outputs = [], []
    for designed in result.designed_fragments:
        if designed.id not in by_id:
            continue
        source = by_id[designed.id]
        features: list[dict[str, Any]] = []
        revision = source_revisions.get((source.source_sequence_id, source.source_revision)) if source.source_sequence_id else None
        if revision is not None and isinstance(revision.snapshot, dict):
            snap = revision.snapshot
            stored = snap.get("features") or []
            core = _core_positions(source, len(snap.get("sequence", "")), bool(snap.get("is_circular")))
            strand: Literal[-1, 1] = -1 if source.orientation == "reverse" else 1
        else:
            length = inline_original_length(source)
            snap = {"sequence": "N" * length, "is_circular": False}
            stored = source.features or []
            core, direction = oriented_coordinates(source, list(range(length)))
            strand = -1 if direction == -1 else 1
        if stored:
            ids = designed.primer_ids
            ftail = len(primers[ids[0]].tail_sequence) if len(ids) == 2 else 0
            rtail = len(primers[ids[1]].tail_sequence) if len(ids) == 2 else 0
            positions = [None] * ftail + core + [None] * rtail
            if len(positions) == len(designed.sequence):
                features = map_stored_features(stored, snap["sequence"], "circular" if snap.get("is_circular") else "linear",
                                               positions, strand)
        label = "PCR amplicon" if designed.preparation == "pcr" else "ready fragment"
        provenance = {"designed_fragment_id": designed.id, "preparation": designed.preparation,
                      "primer_ids": designed.primer_ids}
        provenance = (with_unphosphorylated_blunt_ends(provenance, "pcr_product")
                      if designed.preparation == "pcr" else
                      write_physical_ends(provenance, source.left_end, source.right_end, "ready_fragment"))
        row = NucleotideSequence(
            id=str(uuid.uuid4()), name=f"{designed.name} ({label})", description=None,
            sequence=designed.sequence, sequence_type="dna", is_circular=False,
            length=len(designed.sequence), features=features, primers=[],
            gc_content=round(100 * (designed.sequence.count("G") + designed.sequence.count("C")) / max(len(designed.sequence), 1), 2),
            version=1,
        )  # transient: never added to the session, so no shelf projection exists
        document = MolecularDocument(id=row.id, document_kind="dna", name=row.name)
        session.add(document)
        await session.flush()
        revision_row = MolecularRevision(
            id=str(uuid.uuid4()), document_id=row.id, revision_number=1,
            change_kind="operation_intermediate", content_sha256=sha256_text(row.sequence),
            content_length=len(row.sequence), snapshot=sequence_snapshot(row),
            provenance=provenance,
            operation_id=operation_id, created_by=SERVER_OWNED_ACTOR,
        )
        session.add(revision_row)
        await session.flush()
        document.current_revision_id = revision_row.id
        session.add(MolecularOperationOutput(
            id=str(uuid.uuid4()), operation_id=operation_id, revision_id=revision_row.id,
            role="designed_fragment", position=position + len(created),
            snapshot={"document_id": row.id, "fragment_id": designed.id}))
        created.append(row.id)
    return created
