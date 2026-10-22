"""Freeze a scoped, read-only MolBio share bundle from the canonical stores.

The bundle contains exactly the scoped molecular revisions/operations plus the
full records of their lineage ancestors (operation input revisions and
``parent_id`` derivations), resolved at creation time. Nothing in the bundle
refers back to live BMS state; the share server serves these bytes verbatim.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentRevision,
)
from molbio_models import (
    MolecularDocument,
    MolecularOperation,
    MolecularOperationInput,
    MolecularOperationOutput,
    MolecularRevision,
    ProjectPlasmidMetadata,
)
from molbio_ngs_models import (
    MolBioNGSDomainState,
    MolBioNGSDomainStateMember,
    MolBioNGSDomainStateRevision,
    MolBioNGSMemberReceipt,
)
from share_server.grants import BUNDLE_SCHEMA, MAX_BUNDLE_BYTES

MAX_SCOPED_ITEMS = 200
MAX_BUNDLE_REVISIONS = 400
MAX_BUNDLE_OPERATIONS = 400
MAX_PROJECT_DOMAINS = 50

_SEQUENCE_FIELDS = (
    "name", "description", "sequence", "sequence_type", "molecule_strandedness",
    "molecule_orientation", "is_circular", "length", "features", "primers",
    "analysis_tracks", "organism", "accession", "operation", "operation_params",
    "gc_content",
)


class ShareScopeError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat() + ("Z" if value.tzinfo is None else "")
    return str(value)


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


class _Collector:
    def __init__(self, molbio: AsyncSession) -> None:
        self.molbio = molbio
        self.revisions: dict[str, MolecularRevision] = {}
        self.revision_role: dict[str, str] = {}
        self.operations: dict[str, MolecularOperation] = {}
        self.operation_role: dict[str, str] = {}
        self.metadata: dict[str, dict[str, Any]] = {}
        self.documents: dict[str, MolecularDocument] = {}

    def _bound(self) -> None:
        if len(self.revisions) > MAX_BUNDLE_REVISIONS or len(self.operations) > MAX_BUNDLE_OPERATIONS:
            raise ShareScopeError(413, "share_scope_too_large", "share scope with lineage exceeds the bundle limit")

    async def add_revision(self, revision_id: str, role: str) -> MolecularRevision | None:
        if revision_id in self.revisions:
            if role == "scoped":
                self.revision_role[revision_id] = "scoped"
            return self.revisions[revision_id]
        revision = await self.molbio.get(MolecularRevision, revision_id)
        if revision is None:
            return None
        self.revisions[revision_id] = revision
        self.revision_role[revision_id] = role
        self._bound()
        return revision

    async def add_document_head(self, document_id: str, role: str) -> MolecularRevision:
        document = await self.molbio.get(MolecularDocument, document_id)
        if document is None or document.deleted_at is not None or not document.current_revision_id:
            raise ShareScopeError(404, "sequence_not_found", "a selected sequence was not found")
        self.documents[document.id] = document
        revision = await self.add_revision(document.current_revision_id, role)
        if revision is None:
            raise ShareScopeError(404, "sequence_not_found", "a selected sequence revision was not found")
        return revision

    async def add_operation(self, operation_id: str, role: str) -> bool:
        if operation_id in self.operations:
            if role == "scoped":
                self.operation_role[operation_id] = "scoped"
            return True
        operation = await self.molbio.get(MolecularOperation, operation_id)
        if operation is None:
            return False
        self.operations[operation_id] = operation
        self.operation_role[operation_id] = role
        self._bound()
        return True

    async def edges(self, operation_ids: Iterable[str]) -> tuple[list[MolecularOperationInput], list[MolecularOperationOutput]]:
        ids = list(operation_ids)
        if not ids:
            return [], []
        inputs = list((await self.molbio.scalars(
            select(MolecularOperationInput).where(MolecularOperationInput.operation_id.in_(ids))
        )).all())
        outputs = list((await self.molbio.scalars(
            select(MolecularOperationOutput).where(MolecularOperationOutput.operation_id.in_(ids))
        )).all())
        return inputs, outputs

    async def close_lineage(self, *, include_scoped_outputs: bool = True) -> None:
        """Add ancestors until the revision/operation set is closed under lineage."""
        # Item shares select operation results too. Domain-state shares select
        # only attached revisions, not every output of a member operation.
        scoped_ops = [key for key, role in self.operation_role.items() if role == "scoped"]
        inputs, outputs = await self.edges(scoped_ops)
        if include_scoped_outputs:
            for row in outputs:
                await self.add_revision(row.revision_id, "scoped")
        for row in inputs:
            await self.add_revision(row.revision_id, "ancestor")
        pending = list(self.revisions)
        visited: set[str] = set()
        while pending:
            revision_id = pending.pop()
            if revision_id in visited:
                continue
            visited.add(revision_id)
            revision = self.revisions[revision_id]
            if revision.operation_id and await self.add_operation(revision.operation_id, "ancestor"):
                op_inputs, _ = await self.edges([revision.operation_id])
                for row in op_inputs:
                    if await self.add_revision(row.revision_id, "ancestor") is not None:
                        pending.append(row.revision_id)
            snapshot = revision.snapshot if isinstance(revision.snapshot, dict) else {}
            parent_id = snapshot.get("parent_id")
            if isinstance(parent_id, str) and parent_id and parent_id != revision.document_id:
                parent = (await self.molbio.scalars(
                    select(MolecularRevision)
                    .where(MolecularRevision.document_id == parent_id, MolecularRevision.created_at <= revision.created_at)
                    .order_by(MolecularRevision.revision_number.desc()).limit(1)
                )).first()
                if parent is None:
                    document = await self.molbio.get(MolecularDocument, parent_id)
                    if document is not None and document.current_revision_id:
                        parent = await self.molbio.get(MolecularRevision, document.current_revision_id)
                if parent is not None and await self.add_revision(parent.id, "ancestor") is not None:
                    pending.append(parent.id)

    async def serialize(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        inputs, outputs = await self.edges(self.operations)
        sequences = []
        for revision_id, revision in sorted(self.revisions.items(), key=lambda kv: (self.revision_role[kv[0]] != "scoped", kv[0])):
            snapshot = revision.snapshot if isinstance(revision.snapshot, dict) else {}
            record = {field: _json(snapshot.get(field)) for field in _SEQUENCE_FIELDS}
            source_file = snapshot.get("source_file")
            record["source_file"] = str(source_file).replace("\\", "/").rsplit("/", 1)[-1] if source_file else None
            meta = self.metadata.get(revision.document_id) or {}
            meta_applies = meta.get("revision_id") == revision.id
            sequences.append({
                "sequence_id": revision.document_id,
                "revision_id": revision.id,
                "revision_number": revision.revision_number,
                "role": self.revision_role[revision_id],
                "change_kind": revision.change_kind,
                "content_sha256": revision.content_sha256,
                "content_length": revision.content_length,
                "created_at": _iso(revision.created_at),
                "derived_by_operation_id": revision.operation_id if revision.operation_id in self.operations else None,
                "parent_sequence_id": snapshot.get("parent_id") if isinstance(snapshot.get("parent_id"), str) else None,
                "project_tags": [str(tag) for tag in meta.get("tags", [])] if meta_applies else [],
                "project_notes": str(meta.get("notes", "")) if meta_applies else "",
                "record": record,
            })
        operations = []
        for operation_id, operation in sorted(self.operations.items(), key=lambda kv: (self.operation_role[kv[0]] != "scoped", kv[0])):
            params = operation.parameters if isinstance(operation.parameters, dict) else {}
            summary = params.get("summary") if isinstance(params.get("summary"), dict) else {}
            operations.append({
                "operation_id": operation_id,
                "role": self.operation_role[operation_id],
                "operation_kind": operation.operation_kind,
                "title": str(summary.get("title") or operation.operation_kind.replace("_", " ").title()),
                "status": operation.status,
                "implementation": operation.implementation,
                "implementation_version": operation.implementation_version,
                "parameters": _json(params),
                "warnings": _json(operation.warnings or []),
                "created_at": _iso(operation.created_at),
                "inputs": [
                    {"revision_id": row.revision_id, "role": row.role, "position": row.position}
                    for row in sorted(inputs, key=lambda r: r.position) if row.operation_id == operation_id
                    and row.revision_id in self.revisions
                ],
                "outputs": [
                    {"revision_id": row.revision_id, "role": row.role, "position": row.position}
                    for row in sorted(outputs, key=lambda r: r.position) if row.operation_id == operation_id
                    and row.revision_id in self.revisions
                ],
            })
        return sequences, operations


async def _domain_state_revision(native: AsyncSession, domain_id: str, state_revision_id: str | None) -> str:
    state = await native.get(MolBioNGSDomainState, domain_id)
    selected_id = state_revision_id or (state.current_state_revision_id if state is not None else None)
    if not selected_id:
        raise ShareScopeError(404, "domain_state_not_found", "MolBio Domain state was not found")
    selected = await native.get(MolBioNGSDomainStateRevision, selected_id)
    if selected is None or selected.global_domain_experiment_id != domain_id:
        raise ShareScopeError(404, "domain_state_not_found", "exact MolBio Domain state revision was not found")
    return selected.id


async def _collect_domain(
    collector: _Collector, *, experiment: AsyncSession, native: AsyncSession,
    project_id: str, domain_id: str, state_revision_id: str | None,
) -> dict[str, Any]:
    selected_id = await _domain_state_revision(native, domain_id, state_revision_id)
    rows = (await native.execute(
        select(MolBioNGSMemberReceipt.entity_id)
        .join(MolBioNGSDomainStateMember, MolBioNGSDomainStateMember.receipt_id == MolBioNGSMemberReceipt.receipt_id)
        .where(MolBioNGSDomainStateMember.state_revision_id == selected_id,
               MolBioNGSMemberReceipt.entity_kind == "molecular_revision")
        .limit(MAX_SCOPED_ITEMS + 1)
    )).scalars().all()
    if len(rows) > MAX_SCOPED_ITEMS:
        raise ShareScopeError(413, "share_scope_too_large", "share scope exceeds the item limit")
    document_ids: list[str] = []
    for attached_revision_id in rows:
        attached = await collector.molbio.get(MolecularRevision, attached_revision_id)
        if attached is None:
            continue
        document_ids.append(attached.document_id)
        await collector.add_revision(attached.id, "scoped")
    metadata_rows = (await collector.molbio.scalars(select(ProjectPlasmidMetadata).where(
        ProjectPlasmidMetadata.project_id == project_id,
        ProjectPlasmidMetadata.domain_experiment_id == domain_id,
        ProjectPlasmidMetadata.active_state_revision_id == selected_id,
    ))).all()
    for row in metadata_rows:
        collector.metadata[row.sequence_id] = {
            "tags": list(row.tags or []), "notes": str(row.notes or ""), "revision_id": row.molecular_revision_id,
        }
    # Native state membership supports operation receipts. Project/Domain
    # lineage receipts are historical observations, not this state's members.
    operation_ids = (await native.execute(
        select(MolBioNGSMemberReceipt.entity_id).distinct()
        .join(MolBioNGSDomainStateMember, MolBioNGSDomainStateMember.receipt_id == MolBioNGSMemberReceipt.receipt_id)
        .where(MolBioNGSDomainStateMember.state_revision_id == selected_id,
               MolBioNGSMemberReceipt.entity_kind == "molecular_operation")
        .limit(MAX_SCOPED_ITEMS + 1)
    )).scalars().all()
    if len(operation_ids) > MAX_SCOPED_ITEMS:
        raise ShareScopeError(413, "share_scope_too_large", "share scope exceeds the item limit")
    for operation_id in operation_ids:
        await collector.add_operation(operation_id, "scoped")
    return {"domain_experiment_id": domain_id, "state_revision_id": selected_id,
            "sequence_count": len(set(document_ids)), "operation_count": len(operation_ids),
            "sequence_selection": "exact_state_member_revisions",
            "operation_selection": "exact_state_member_operations"}


async def _require_project(experiment: AsyncSession, project_id: str) -> ExperimentAggregateHead:
    project = await experiment.get(ExperimentAggregateHead, project_id)
    if project is None or project.aggregate_kind != "workspace":
        raise ShareScopeError(404, "project_not_found", "Project was not found")
    return project


async def _require_molbio_domain(experiment: AsyncSession, project_id: str, domain_id: str) -> ExperimentAggregateHead:
    domain = await experiment.get(ExperimentAggregateHead, domain_id)
    if domain is None or domain.aggregate_kind != "domain_experiment" or domain.workspace_id != project_id:
        raise ShareScopeError(404, "domain_not_found", "MolBio Domain Experiment was not found in Project")
    revision = await experiment.get(ExperimentRevision, domain.current_revision_id or "")
    payload = json.loads(revision.canonical_payload) if revision is not None else {}
    if payload.get("domain_kind") != "ngs_molbio":
        raise ShareScopeError(422, "unsupported_domain_kind", "only MolBio Domain Experiments can be shared")
    return domain


async def build_share_bundle(
    scope: dict[str, Any], *, molbio: AsyncSession, experiment: AsyncSession, native: AsyncSession,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(bundle_without_grant_fields, scope_summary)``."""
    collector = _Collector(molbio)
    kind = scope.get("kind")
    summary: dict[str, Any] = {"kind": kind}
    title = "Shared MolBio items"
    if kind == "project_domain":
        project = await _require_project(experiment, scope["project_id"])
        domain = await _require_molbio_domain(experiment, project.id, scope["domain_experiment_id"])
        if domain.parent_id != scope["global_experiment_id"]:
            raise ShareScopeError(404, "domain_not_found", "MolBio Domain Experiment was not found in Global Experiment")
        detail = await _collect_domain(
            collector, experiment=experiment, native=native, project_id=project.id,
            domain_id=domain.id, state_revision_id=scope.get("state_revision_id"),
        )
        summary.update({"project_id": project.id, "project_name": project.display_name,
                        "global_experiment_id": scope["global_experiment_id"], "domains": [detail]})
        title = f"{project.display_name} — {domain.display_name}"
    elif kind == "project":
        project = await _require_project(experiment, scope["project_id"])
        domains = (await experiment.scalars(select(ExperimentAggregateHead).where(
            ExperimentAggregateHead.aggregate_kind == "domain_experiment",
            ExperimentAggregateHead.workspace_id == project.id,
        ).limit(MAX_PROJECT_DOMAINS + 1))).all()
        if len(domains) > MAX_PROJECT_DOMAINS:
            raise ShareScopeError(413, "share_scope_too_large", "Project has too many Domain Experiments to share at once")
        details = []
        for domain in domains:
            try:
                await _require_molbio_domain(experiment, project.id, domain.aggregate_id)
                details.append(await _collect_domain(
                    collector, experiment=experiment, native=native, project_id=project.id,
                    domain_id=domain.aggregate_id, state_revision_id=None,
                ))
            except ShareScopeError as exc:
                if exc.code in {"unsupported_domain_kind", "domain_state_not_found"}:
                    continue
                raise
        summary.update({"project_id": project.id, "project_name": project.display_name, "domains": details})
        title = project.display_name
    elif kind == "items":
        sequence_ids = list(dict.fromkeys(scope.get("sequence_ids") or []))
        operation_ids = list(dict.fromkeys(scope.get("operation_ids") or []))
        if not sequence_ids and not operation_ids:
            raise ShareScopeError(422, "empty_share_scope", "select at least one sequence or experiment")
        if len(sequence_ids) + len(operation_ids) > MAX_SCOPED_ITEMS:
            raise ShareScopeError(413, "share_scope_too_large", "share scope exceeds the item limit")
        for document_id in sequence_ids:
            await collector.add_document_head(document_id, "scoped")
        for operation_id in operation_ids:
            if not await collector.add_operation(operation_id, "scoped"):
                raise ShareScopeError(404, "operation_not_found", "a selected experiment was not found")
        summary.update({"sequence_ids": sequence_ids, "operation_ids": operation_ids})
    else:
        raise ShareScopeError(422, "invalid_share_scope", "unknown share scope kind")

    await collector.close_lineage(include_scoped_outputs=kind == "items")
    summary["lineage_disclosure"] = (
        "Full ancestor sequence records and operations are included, including "
        "operation inputs and parent derivations outside the selected scope; "
        "they are labelled ancestor."
    )
    sequences, operations = await collector.serialize()
    if not sequences and not operations:
        raise ShareScopeError(422, "empty_share_scope", "the selected scope contains no shareable items")
    summary["counts"] = {
        "scoped_sequences": sum(1 for s in sequences if s["role"] == "scoped"),
        "ancestor_sequences": sum(1 for s in sequences if s["role"] == "ancestor"),
        "scoped_operations": sum(1 for o in operations if o["role"] == "scoped"),
        "ancestor_operations": sum(1 for o in operations if o["role"] == "ancestor"),
    }
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "title": title,
        "scope": summary,
        "sequences": sequences,
        "operations": operations,
    }
    return bundle, summary


def encode_bundle(bundle: dict[str, Any]) -> bytes:
    """Canonical JSON with HTML-significant characters escaped (defence in depth)."""
    text = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    # These characters can only occur inside JSON strings, so escaping is lossless.
    text = text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    text = text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    data = text.encode("utf-8")
    if len(data) > MAX_BUNDLE_BYTES:
        raise ShareScopeError(413, "share_bundle_too_large", "share bundle exceeds the size limit")
    return data
