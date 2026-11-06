"""End operations (fill-in, overhang removal, 5' phosphate state) and restriction-cloning proposals."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from molbio_database import get_molbio_session
from molbio_models import MolecularDocument, MolecularOperation, MolecularRevision
from routers.molbio_ops import AssemblyFragmentEndSchema
from services.assembly.end_operations import EndOperationResult, apply_end_operation
from services.assembly.physical_ends import read_physical_ends, write_physical_ends
from services.assembly.restriction_cloning import suggest_restriction_cloning
from services.assembly.types import AssemblyError, FragmentEnd
from services.molbio_persistence import (
    add_operation_edges, begin_immediate_molbio_write, canonical_request_fingerprint, create_operation, sha256_text,
)

router = APIRouter(prefix="/api/molbio", tags=["Molecular Biology"])


class RevisionSource(BaseModel):
    kind: Literal["molecular_revision"]
    revision_id: str = Field(min_length=1, max_length=128)


class InlineSource(BaseModel):
    kind: Literal["inline"]
    name: str = "Fragment"
    sequence: str
    left_end: Optional[AssemblyFragmentEndSchema] = None
    right_end: Optional[AssemblyFragmentEndSchema] = None
    features: Optional[list[dict[str, Any]]] = None


class EndOperationRequest(BaseModel):
    source: Union[RevisionSource, InlineSource] = Field(discriminator="kind")
    operation: Literal["fill_in", "remove_3_overhang", "remove_5_overhang_nuclease", "phosphorylate", "dephosphorylate"]
    end: Literal["left", "right", "both"] = "both"
    new_name: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, max_length=255)


def _end_model(end: FragmentEnd | None) -> AssemblyFragmentEndSchema:
    end = end or FragmentEnd(type="blunt")
    return AssemblyFragmentEndSchema(type=end.type, overhang=end.overhang, label=end.label,
                                     protruding_strand=end.protruding_strand,
                                     five_prime_phosphate=end.five_prime_phosphate)


def _to_end(value: AssemblyFragmentEndSchema | None) -> FragmentEnd | None:
    if value is None:
        return None
    return FragmentEnd(type=value.type, overhang=value.overhang, label=value.label,
                       protruding_strand=value.protruding_strand, five_prime_phosphate=value.five_prime_phosphate)


async def _resolve_source(request: EndOperationRequest, session: AsyncSession):
    source = request.source
    if isinstance(source, InlineSource):
        return None, source.name, source.sequence, _to_end(source.left_end), _to_end(source.right_end), source.features or []
    revision = await session.get(MolecularRevision, source.revision_id)
    if revision is None:
        raise HTTPException(404, "Source molecular revision was not found")
    snapshot = revision.snapshot if isinstance(revision.snapshot, dict) else {}
    if snapshot.get("is_circular"):
        raise HTTPException(422, "End operations need a linear fragment; cut the circular molecule first")
    left, right = read_physical_ends(revision.provenance if isinstance(revision.provenance, dict) else {})
    return revision, str(snapshot.get("name") or "Fragment"), str(snapshot.get("sequence") or ""), left, right, \
        list(snapshot.get("features") or [])


def _compute(request: EndOperationRequest, name: str, sequence: str, left, right, features) -> EndOperationResult:
    try:
        return apply_end_operation(sequence, left, right, operation=request.operation, end=request.end, features=features)
    except AssemblyError as exc:
        raise HTTPException(422, str(exc)) from exc


def _response(result: EndOperationResult) -> dict[str, Any]:
    return {
        "sequence": result.sequence, "topology": "linear",
        "left_end": _end_model(result.left_end).model_dump(),
        "right_end": _end_model(result.right_end).model_dump(),
        "features": result.features,
        "changed": {"bases_added": result.bases_added, "bases_removed": result.bases_removed},
        "notes": result.notes,
    }


@router.post("/end-operations/preview")
async def preview_end_operation(request: EndOperationRequest, session: AsyncSession = Depends(get_molbio_session)):
    _revision, name, sequence, left, right, features = await _resolve_source(request, session)
    result = await run_in_threadpool(_compute, request, name, sequence, left, right, features)
    return _response(result)


def _fingerprint(request: EndOperationRequest) -> str:
    return canonical_request_fingerprint({"kind": "end_operation", **request.model_dump(mode="json", exclude={"idempotency_key"})})


async def _existing_response(session: AsyncSession, operation: MolecularOperation) -> dict[str, Any]:
    revision = (await session.execute(select(MolecularRevision).where(MolecularRevision.operation_id == operation.id))).scalars().first()
    if revision is None:
        raise HTTPException(409, "Idempotency key is bound to an incomplete operation")
    snapshot = revision.snapshot
    left, right = read_physical_ends(revision.provenance)
    stored = operation.parameters.get("result", {})
    return {
        "sequence": snapshot["sequence"], "topology": "linear",
        "left_end": _end_model(left).model_dump(), "right_end": _end_model(right).model_dump(),
        "features": snapshot.get("features") or [],
        "changed": stored.get("changed", {"bases_added": 0, "bases_removed": 0}),
        "notes": stored.get("notes", []),
        "operation_id": operation.id, "document_id": revision.document_id,
        "revision_id": revision.id, "sequence_id": None,
    }


@router.post("/end-operations/apply")
async def apply_end_operation_route(request: EndOperationRequest, session: AsyncSession = Depends(get_molbio_session)):
    fingerprint = _fingerprint(request)
    if request.idempotency_key:
        existing = (await session.execute(select(MolecularOperation).where(
            MolecularOperation.idempotency_key == request.idempotency_key))).scalar_one_or_none()
        if existing is not None:
            if existing.operation_kind != "end_operation" or existing.request_fingerprint != fingerprint:
                raise HTTPException(409, "Idempotency key is bound to another request")
            return await _existing_response(session, existing)
    source_revision, name, sequence, left, right, features = await _resolve_source(request, session)
    result = await run_in_threadpool(_compute, request, name, sequence, left, right, features)
    response = _response(result)

    await begin_immediate_molbio_write(session)
    child_name = (request.new_name or "").strip() or f"{name} ({result.label})"
    document_id, revision_id = str(uuid.uuid4()), str(uuid.uuid4())
    operation = await create_operation(
        session, operation_kind="end_operation", implementation="services.assembly.end_operations.apply_end_operation",
        parameters={"operation": request.operation, "end": request.end, "source_revision_id": source_revision.id if source_revision else None,
                    "result": {"changed": response["changed"], "notes": result.notes}},
        warnings=list(result.notes), provenance={"source": "api", "operation": request.operation},
        idempotency_key=request.idempotency_key, request_fingerprint=fingerprint,
    )
    session.add(MolecularDocument(id=document_id, document_kind="dna", name=child_name, current_revision_id=None))
    await session.flush()
    revision = MolecularRevision(
        id=revision_id, document_id=document_id, revision_number=1, change_kind="end_operation",
        content_sha256=sha256_text(result.sequence), content_length=len(result.sequence),
        snapshot={"sequence_type": "dna", "sequence": result.sequence, "is_circular": False, "topology": "linear",
                  "name": child_name, "features": result.features},
        provenance=write_physical_ends(
            {"source_revision_id": source_revision.id if source_revision else None, "operation_id": operation.id,
             "end_operation": request.operation, "end": request.end},
            result.left_end, result.right_end, "end_operation"),
        operation_id=operation.id,
    )
    session.add(revision)
    await session.flush()
    document = await session.get(MolecularDocument, document_id)
    document.current_revision_id = revision_id
    await add_operation_edges(
        session, operation,
        input_revisions=[(source_revision, "end_operation_source", {"document_id": source_revision.document_id})] if source_revision else [],
        output_revisions=[(revision, "end_operation_product", {"document_id": document_id})],
    )
    await session.commit()
    return {**response, "operation_id": operation.id, "document_id": document_id, "revision_id": revision_id, "sequence_id": None}


class RestrictionCloningRequest(BaseModel):
    backbone_sequence: str
    backbone_topology: Literal["linear", "circular"] = "circular"
    insert_sequence: str
    insert_topology: Literal["linear", "circular"] = "linear"
    # Enzyme IDs/names to consider; empty means every commercial double-strand enzyme in the catalog.
    enzymes: list[str] = Field(default_factory=list)
    # Backbone interval (zero-based, half-open) where the insert goes; None = anywhere.
    backbone_region_start: Optional[int] = None
    backbone_region_end: Optional[int] = None
    # Insert payload; default the whole sequence.
    insert_start: Optional[int] = None
    insert_end: Optional[int] = None
    vector_five_prime_phosphate: Literal["unknown", "phosphorylated", "unphosphorylated"] = "unknown"
    limit: int = Field(default=3, ge=1, le=10)


@router.post("/assembly/restriction-cloning/suggest")
async def suggest_cloning(request: RestrictionCloningRequest):
    try:
        return await run_in_threadpool(suggest_restriction_cloning, request.model_dump())
    except AssemblyError as exc:
        raise HTTPException(422, str(exc)) from exc
