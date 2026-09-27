"""Pure BC2 candidate handoff from producer-stamped, verified native records.

This does not insert Designs, filter native rows or change campaign admission. The
publication owner must bind paths to registered JobArtifacts before persistence.
Unjoined/legacy output remains visible through the native readback unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from services.bindcraft2_native_results import NativePublication


@dataclass(frozen=True)
class CandidateStructure:
    path: str
    sha256: str
    target_state: str
    primary: bool
    variant: str
    binder_chains: str | None
    target_chains: str | None


@dataclass(frozen=True)
class CandidateProjection:
    arm: str | None
    retained_design: str
    scored_design: str
    trajectory_design: str
    attempt_sha256: str
    sequence: str | None
    native_rank: int | None
    primary_structure: CandidateStructure
    structures: tuple[CandidateStructure, ...]


def project_native_candidates(publication: NativePublication) -> tuple[CandidateProjection, ...]:
    """Emit only exact producer-joined primary native CIFs; leave unknowns native.

    CIF metadata, not suffix, rank, sequence or CSV ordering, owns state identity.
    Relaxation is a derivative and can never replace the native primary. A missing
    or contradictory association does not hide failed draws or stop publication.
    """
    projected = []
    for arm in publication.arms:
        for row in arm.retained:
            if not (row.scored_design and row.trajectory_design and row.attempt_sha256):
                continue
            documents = [doc for doc in arm.documents if doc.retained_design == row.design
                         and doc.attempt_sha256 == row.attempt_sha256
                         and doc.format in ("cif", "mmcif") and doc.target_state
                         and doc.primary_target_state and doc.structure_variant == "native"]
            if not documents:
                continue
            primaries = [doc for doc in documents if doc.target_state == doc.primary_target_state]
            # Ambiguous state identity stays observational, not a guessed Design.
            if len(primaries) != 1 or len({doc.target_state for doc in documents}) != len(documents):
                continue
            primary = primaries[0]
            if any(doc.primary_target_state != primary.target_state for doc in documents):
                continue
            structures = tuple(CandidateStructure(doc.path, doc.sha256, doc.target_state or "",
                                                   doc is primary, "native", doc.binder_chains,
                                                   doc.target_chains) for doc in documents)
            projected.append(CandidateProjection(arm.name, row.design, row.scored_design,
                                                 row.trajectory_design, row.attempt_sha256,
                                                 row.sequence, row.rank,
                                                 next(item for item in structures if item.primary),
                                                 structures))
    return tuple(projected)
