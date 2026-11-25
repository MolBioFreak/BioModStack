"""Direct hub-receipt binding between experiments.db and molbio_ngs.db (R17).

Replaces the retired cross-database connector (command/outbox/inbox/stream/ack/conflict).
Follows the launch-context pattern: commit the NGS domain state first keyed by the
hub domain ID, then write the hub external_entity_receipt + idempotency_claim.
Retrying an interrupted bind with the same idempotency key links the original domain
state resource exactly once without requiring a background worker.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import (
    ExperimentAggregateHead,
    ExperimentExternalEntityReceipt,
    ExperimentIdempotencyClaim,
    ExperimentRevision,
)
from experiment_operations import register_external_entity_receipt
from experiment_services import (
    IdempotencyConflict,
    NotFound,
    RevisionConflict,
    ValidationFailure,
    canonical_json,
    now,
)
from molbio_ngs_models import MolBioNGSDomainState, MolBioNGSDomainStateRevision

BINDING_ADAPTER_ID = "bms.ngs-molbio.domain-binding.adapter.v1"
SOURCE_STORE_ID = "bms.molbio-ngs.domain-store.v1"
BINDING_ENTITY_KIND = "ngs_molbio_domain_binding"


class BindingError(RuntimeError):
    def __init__(self, message: str, *, code: str = "binding_error") -> None:
        super().__init__(message)
        self.code = code


class BindingConflict(BindingError):
    def __init__(self, message: str, *, code: str = "binding_conflict") -> None:
        super().__init__(message, code=code)


class BindingUnavailable(BindingError):
    def __init__(self, message: str, *, code: str = "binding_unavailable") -> None:
        super().__init__(message, code=code)


ConnectorConflict = BindingConflict
ConnectorUnavailable = BindingUnavailable


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _load_hierarchy(
    global_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
) -> tuple[
    ExperimentAggregateHead,
    ExperimentRevision,
    ExperimentAggregateHead,
    ExperimentRevision,
    ExperimentAggregateHead,
    ExperimentRevision,
]:
    project = await global_session.get(ExperimentAggregateHead, project_id)
    experiment = await global_session.get(ExperimentAggregateHead, global_experiment_id)
    domain = await global_session.get(ExperimentAggregateHead, domain_id)
    if project is None or project.aggregate_kind != "workspace" or not project.current_revision_id:
        raise NotFound("Project was not found")
    if (
        experiment is None
        or experiment.aggregate_kind != "experiment"
        or experiment.parent_id != project_id
        or not experiment.current_revision_id
    ):
        raise NotFound("Global Experiment was not found")
    if (
        domain is None
        or domain.aggregate_kind != "domain_experiment"
        or domain.workspace_id != project_id
        or domain.parent_id != global_experiment_id
        or not domain.current_revision_id
    ):
        raise NotFound("Domain Experiment was not found")
    project_rev = await global_session.get(ExperimentRevision, project.current_revision_id)
    experiment_rev = await global_session.get(ExperimentRevision, experiment.current_revision_id)
    domain_rev = await global_session.get(ExperimentRevision, domain.current_revision_id)
    if project_rev is None or experiment_rev is None or domain_rev is None:
        raise NotFound("Hierarchy revision authority was not found")
    return project, project_rev, experiment, experiment_rev, domain, domain_rev


def _build_binding_receipt(
    *,
    receipt_id: str,
    project: ExperimentAggregateHead,
    project_rev: ExperimentRevision,
    experiment: ExperimentAggregateHead,
    experiment_rev: ExperimentRevision,
    domain: ExperimentAggregateHead,
    domain_rev: ExperimentRevision,
    domain_payload: dict[str, Any],
    verified_at: str,
) -> tuple[str, str]:
    receipt = {
        "schema": "bms.ngs-molbio.global-binding-receipt.v1",
        "receipt_id": receipt_id,
        "adapter_id": BINDING_ADAPTER_ID,
        "verified_at": verified_at,
        "acknowledgement": {"status": "verified"},
        "project": {
            "id": project.aggregate_id,
            "revision_id": project_rev.resource_id,
            "generation": project.head_generation,
            "digest": project_rev.payload_sha256,
            "reopen_destination": f"/projects/{project.aggregate_id}",
        },
        "global_experiment": {
            "id": experiment.aggregate_id,
            "revision_id": experiment_rev.resource_id,
            "generation": experiment.head_generation,
            "digest": experiment_rev.payload_sha256,
            "reopen_destination": f"/projects/{project.aggregate_id}/experiments/{experiment.aggregate_id}",
        },
        "domain_experiment": {
            "id": domain.aggregate_id,
            "revision_id": domain_rev.resource_id,
            "generation": domain.head_generation,
            "digest": domain_rev.payload_sha256,
            "domain_kind": domain_payload.get("domain_kind", "ngs_molbio"),
            "domain_contract_version": str(domain_payload.get("domain_contract_version", "3")),
        },
    }
    raw = canonical_json(receipt)
    return raw, _digest(raw)


def format_binding_status(
    *,
    project_id: str,
    project_revision_id: str,
    global_experiment_id: str,
    global_experiment_revision_id: str,
    domain_id: str,
    domain_revision_id: str,
    state: MolBioNGSDomainState,
    receipt_id: str | None = None,
    receipt_sha256: str | None = None,
) -> dict[str, Any]:
    effective_receipt_id = receipt_id or state.binding_receipt_id
    effective_receipt_sha256 = receipt_sha256 or state.binding_receipt_sha256
    in_sync = (
        state.project_id == project_id
        and state.global_experiment_id == global_experiment_id
        and state.global_domain_experiment_revision_id == domain_revision_id
    )
    return {
        "schema": "bms.ngs-molbio.binding-status.v1",
        "project_id": project_id,
        "project_revision_id": project_revision_id,
        "global_experiment_id": global_experiment_id,
        "global_experiment_revision_id": global_experiment_revision_id,
        "domain_id": domain_id,
        "domain_revision_id": domain_revision_id,
        "binding_revision_id": effective_receipt_id,
        "global_receipt_id": effective_receipt_id,
        "global_receipt_sha256": effective_receipt_sha256,
        "connector_command_id": None,
        "command_state": "applied" if in_sync else "needs_reverification",
        "acknowledgement_id": effective_receipt_id,
        "acknowledgement_sha256": effective_receipt_sha256,
        "local_state_id": state.global_domain_experiment_id,
        "provisioning_state": "ready" if in_sync else "degraded",
        "head_generation": state.head_generation,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


async def bind_domain_to_hub(
    global_session: AsyncSession,
    domain_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    expected_domain_revision_id: str,
    idempotency_key: str,
    operation: str = "bind",
    commit_domain: bool = True,
) -> dict[str, Any]:
    """Commit NGS domain state first, then link it in experiments.db via external_entity_receipt."""
    key = (idempotency_key or "").strip()
    if not key:
        raise ValidationFailure("Idempotency-Key header is required")

    project, project_rev, experiment, experiment_rev, domain, domain_rev = await _load_hierarchy(
        global_session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_id=domain_id,
    )
    if domain.current_revision_id != expected_domain_revision_id:
        raise RevisionConflict("expected_domain_revision_id does not match current Domain revision")

    domain_payload = json.loads(domain_rev.canonical_payload)
    request_scope = f"ngs_molbio_binding:{project_id}:{global_experiment_id}:{domain_id}"
    request_sha256 = _digest(
        canonical_json(
            {
                "operation": operation,
                "project_id": project_id,
                "global_experiment_id": global_experiment_id,
                "domain_id": domain_id,
                "expected_domain_revision_id": expected_domain_revision_id,
            }
        )
    )

    existing_claim = await global_session.scalar(
        select(ExperimentIdempotencyClaim).where(
            ExperimentIdempotencyClaim.scope == request_scope,
            ExperimentIdempotencyClaim.idempotency_key == key,
        )
    )
    if existing_claim is not None:
        if existing_claim.request_sha256 != request_sha256:
            raise IdempotencyConflict("Idempotency-Key was reused with a different binding request")
        state = await domain_session.get(MolBioNGSDomainState, domain_id)
        if state is not None and existing_claim.response_json:
            try:
                cached = json.loads(existing_claim.response_json)
                if isinstance(cached, dict):
                    return cached
            except json.JSONDecodeError:
                pass

    # Step 1: Commit domain state first in molbio_ngs.db (idempotent by domain_id).
    timestamp = _utc_now()
    state = await domain_session.get(MolBioNGSDomainState, domain_id)
    if state is not None:
        if state.project_id and state.project_id != project_id:
            raise BindingConflict("NGS domain state is already bound to another Project")
        if state.global_experiment_id and state.global_experiment_id != global_experiment_id:
            raise BindingConflict("NGS domain state is already bound to another Global Experiment")
        reuse_receipt = (
            state.global_domain_experiment_revision_id == expected_domain_revision_id
            and bool(state.binding_receipt_id)
            and bool(state.binding_receipt_json)
            and bool(state.binding_receipt_sha256)
        )
        if reuse_receipt:
            receipt_id = str(state.binding_receipt_id)
            receipt_json = str(state.binding_receipt_json)
            receipt_sha256 = str(state.binding_receipt_sha256)
        else:
            receipt_id = str(uuid.uuid4())
            receipt_json, receipt_sha256 = _build_binding_receipt(
                receipt_id=receipt_id,
                project=project,
                project_rev=project_rev,
                experiment=experiment,
                experiment_rev=experiment_rev,
                domain=domain,
                domain_rev=domain_rev,
                domain_payload=domain_payload,
                verified_at=timestamp,
            )
            state.project_id = project_id
            state.global_experiment_id = global_experiment_id
            state.global_domain_experiment_revision_id = expected_domain_revision_id
            state.binding_receipt_id = receipt_id
            state.binding_receipt_json = receipt_json
            state.binding_receipt_sha256 = receipt_sha256
            state.updated_at = timestamp
            await domain_session.flush()
    else:
        receipt_id = str(uuid.uuid4())
        receipt_json, receipt_sha256 = _build_binding_receipt(
            receipt_id=receipt_id,
            project=project,
            project_rev=project_rev,
            experiment=experiment,
            experiment_rev=experiment_rev,
            domain=domain,
            domain_rev=domain_rev,
            domain_payload=domain_payload,
            verified_at=timestamp,
        )
        state = MolBioNGSDomainState(
            global_domain_experiment_id=domain_id,
            current_state_revision_id=None,
            project_id=project_id,
            global_experiment_id=global_experiment_id,
            global_domain_experiment_revision_id=expected_domain_revision_id,
            binding_receipt_id=receipt_id,
            binding_receipt_json=receipt_json,
            binding_receipt_sha256=receipt_sha256,
            head_generation=0,
            created_at=timestamp,
            updated_at=timestamp,
        )
        domain_session.add(state)
        await domain_session.flush()

    if commit_domain:
        await domain_session.commit()

    # Step 2: Record the hub link via external_entity_receipt + idempotency_claim.
    hub_receipt = await register_external_entity_receipt(
        global_session,
        workspace_id=project_id,
        store_id=SOURCE_STORE_ID,
        entity_kind=BINDING_ENTITY_KIND,
        entity_id=domain_id,
        generation_or_revision=expected_domain_revision_id,
        content_digest=receipt_sha256,
        availability="available",
        verification_authority=BINDING_ADAPTER_ID,
        acknowledgement={
            "schema": "bms.global.external-entity-receipt.v1",
            "verifier_id": BINDING_ADAPTER_ID,
            "store_id": SOURCE_STORE_ID,
            "entity_kind": BINDING_ENTITY_KIND,
            "entity_id": domain_id,
            "entity_revision_id": expected_domain_revision_id,
            "content_digest": receipt_sha256,
            "contract_digest": receipt_sha256,
            "availability": "available",
            "source_build_revision": "m6-direct-binding",
            "verified_at": timestamp,
            "reopen_uri": f"/molbio-ngs/{domain_id}",
            "binding_receipt": json.loads(receipt_json),
        },
    )
    status_body = format_binding_status(
        project_id=project_id,
        project_revision_id=project_rev.resource_id,
        global_experiment_id=global_experiment_id,
        global_experiment_revision_id=experiment_rev.resource_id,
        domain_id=domain_id,
        domain_revision_id=expected_domain_revision_id,
        state=state,
        receipt_id=hub_receipt.resource_id,
        receipt_sha256=receipt_sha256,
    )
    if existing_claim is None:
        global_session.add(
            ExperimentIdempotencyClaim(
                scope=request_scope,
                idempotency_key=key,
                request_sha256=request_sha256,
                result_resource_id=hub_receipt.resource_id,
                response_json=canonical_json(status_body),
                created_at=now(),
            )
        )
    else:
        existing_claim.result_resource_id = hub_receipt.resource_id
        existing_claim.response_json = canonical_json(status_body)
    await global_session.flush()
    return status_body


async def get_binding_status(
    global_session: AsyncSession,
    domain_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
) -> dict[str, Any]:
    project, project_rev, experiment, experiment_rev, domain, domain_rev = await _load_hierarchy(
        global_session,
        project_id=project_id,
        global_experiment_id=global_experiment_id,
        domain_id=domain_id,
    )
    state = await domain_session.get(MolBioNGSDomainState, domain_id)
    if state is None:
        raise NotFound("NGS/MolBio binding was not found")
    if state.project_id != project_id or state.global_experiment_id != global_experiment_id:
        raise BindingConflict("local binding authority belongs to another Domain Experiment")
    hub_receipt = await global_session.scalar(
        select(ExperimentExternalEntityReceipt)
        .where(
            ExperimentExternalEntityReceipt.workspace_id == project_id,
            ExperimentExternalEntityReceipt.store_id == SOURCE_STORE_ID,
            ExperimentExternalEntityReceipt.entity_kind == BINDING_ENTITY_KIND,
            ExperimentExternalEntityReceipt.entity_id == domain_id,
        )
        .order_by(ExperimentExternalEntityReceipt.created_at.desc())
    )
    return format_binding_status(
        project_id=project_id,
        project_revision_id=project_rev.resource_id,
        global_experiment_id=global_experiment_id,
        global_experiment_revision_id=experiment_rev.resource_id,
        domain_id=domain_id,
        domain_revision_id=domain_rev.resource_id,
        state=state,
        receipt_id=hub_receipt.resource_id if hub_receipt is not None else state.binding_receipt_id,
        receipt_sha256=state.binding_receipt_sha256,
    )


async def exact_local_launch_authority(
    global_session: AsyncSession,
    domain_session: AsyncSession,
    *,
    project_id: str,
    global_experiment_id: str,
    domain_id: str,
    expected_domain_revision_id: str,
) -> dict[str, Any]:
    """Re-read both stores and return one exact accepted local launch proof."""
    try:
        project, project_rev, experiment, experiment_rev, domain, domain_rev = await _load_hierarchy(
            global_session,
            project_id=project_id,
            global_experiment_id=global_experiment_id,
            domain_id=domain_id,
        )
    except NotFound as exc:
        raise ValidationFailure("replacement_preparation_required") from exc
    if (
        project.lifecycle_state == "archived"
        or experiment.lifecycle_state == "archived"
        or domain.lifecycle_state == "archived"
        or domain.current_revision_id != expected_domain_revision_id
    ):
        raise ValidationFailure("replacement_preparation_required")
    local_state = await domain_session.get(MolBioNGSDomainState, domain_id)
    local_revision = await domain_session.get(
        MolBioNGSDomainStateRevision,
        local_state.current_state_revision_id if local_state is not None else "",
    )
    if (
        local_state is None
        or local_revision is None
        or local_state.project_id != project_id
        or local_state.global_experiment_id != global_experiment_id
        or local_state.global_domain_experiment_revision_id != domain.current_revision_id
        or local_state.current_state_revision_id != local_revision.id
        or local_state.head_generation != local_revision.revision_number
        or local_revision.global_domain_experiment_id != domain_id
        or local_revision.global_domain_experiment_revision_id != domain.current_revision_id
        or _digest(local_revision.canonical_payload) != local_revision.payload_sha256
        or not local_revision.membership_graph_sha256
    ):
        raise ValidationFailure("replacement_preparation_required")
    from molbio_ngs_services import StateIntegrityError, verify_state_revision_integrity

    try:
        await verify_state_revision_integrity(domain_session, local_revision)
    except StateIntegrityError as exc:
        raise ValidationFailure("replacement_preparation_required") from exc

    return {
        "project_id": project_id,
        "project_revision_id": project_rev.resource_id,
        "project_revision_generation": project.head_generation,
        "project_revision_sha256": project_rev.payload_sha256,
        "global_experiment_id": global_experiment_id,
        "global_experiment_revision_id": experiment_rev.resource_id,
        "global_experiment_revision_generation": experiment.head_generation,
        "global_experiment_revision_sha256": experiment_rev.payload_sha256,
        "domain_id": domain_id,
        "domain_revision_id": domain.current_revision_id,
        "domain_revision_generation": domain.head_generation,
        "domain_revision_sha256": domain_rev.payload_sha256,
        "global_binding_receipt_id": local_state.binding_receipt_id,
        "global_binding_receipt_sha256": local_state.binding_receipt_sha256,
        "local_state_revision_id": local_revision.id,
        "local_state_generation": local_state.head_generation,
        "local_state_payload_sha256": local_revision.payload_sha256,
        "local_state_membership_graph_sha256": local_revision.membership_graph_sha256,
    }
