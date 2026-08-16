"""Immutable, receipt-bound scientific evidence for MolBio/NGS experiments."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job
from molbio_ngs_models import (
    MolBioNGSDomainStateMember,
    MolBioNGSEvidenceAssessment,
    MolBioNGSMemberReceipt,
    MolBioNGSSampleRevision,
)
from molbio_ngs_services import (
    DomainStateNotFound,
    StateIntegrityError,
    StateValidationError,
    _audit_and_outbox,
    _canonical,
    _complete_idempotency,
    _digest,
    _id,
    _now,
    _reserve_idempotency,
    get_state_revision,
    verify_state_revision_integrity,
)
from services.molbio_authority import SERVER_OWNED_ACTOR
from services.job_result_roots import resolve_persisted_job_result_root
from services.molbio_ngs_member_receipts import (
    ExternalMemberReceipt,
    build_external_member_receipt,
    parse_canonical_member_receipt,
    persist_member_receipt,
    resolve_approved_comparison_panel_receipt,
    resolve_molecular_revision_receipt,
    resolve_ngs_job_receipt,
    resolve_ngs_result_manifest_receipt,
    resolve_ont_instrument_run_receipt,
)
from services.molbio_ngs_references import (
    get_reference_revision,
    read_reference_artifact_bytes,
    resolve_ngs_reference_revision_receipt,
)
from services.sequence_qc_manifest import (
    VERIFICATION_SCHEMA,
    find_manifest_in_result_root,
    load_sequence_qc_manifest,
)

EVIDENCE_WRAPPER_SCHEMA = "bms.molbio-ngs.ngs-evidence-receipt.v1"
EVIDENCE_WRAPPER_SCHEMA_NAME = "bms.molbio-ngs.ngs-evidence-receipt"
EVIDENCE_WRAPPER_SCHEMA_VERSION = "1"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ID_KEYS = {
    "ngs_job",
    "ngs_result_manifest",
    "ngs_reference_revision",
    "ont_instrument_run",
    "molecular_revision",
    "ngs_comparison_panel",
}
_WRAPPER_KEYS = {
    "schema",
    "evidence_id",
    "global_domain_experiment_id",
    "state_revision_id",
    "sample_revision_id",
    "receipt_ids",
    "assessment_rule_id",
    "requested_assessment",
    "scientific_assessment",
    "job_lifecycle_state",
    "manifest_integrity",
    "raw_manifest_sha256",
    "notes",
    "created_at",
    "created_by",
}


@dataclass(frozen=True)
class AssessmentRule:
    rule_id: str
    pass_manifest_schemas: frozenset[str]
    require_completed_job: bool = True


# This registry is server-owned. Request bodies select an ID; they never supply
# rule predicates, lifecycle assertions, integrity claims, or profile policy.
ASSESSMENT_RULE_REGISTRY: Mapping[str, AssessmentRule] = {
    "server-owned-rule": AssessmentRule(
        rule_id="server-owned-rule",
        pass_manifest_schemas=frozenset({VERIFICATION_SCHEMA}),
    ),
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _receipt_row_authority(row: MolBioNGSMemberReceipt) -> dict[str, Any]:
    """Return canonical authority only after every duplicated column agrees."""

    if _sha256_bytes(row.canonical_receipt.encode("utf-8")) != row.receipt_sha256:
        raise StateIntegrityError("persisted member receipt digest mismatch")
    try:
        authority = parse_canonical_member_receipt(row.canonical_receipt)
        reopen_column = json.loads(row.reopen_destination)
    except (ValueError, json.JSONDecodeError) as exc:
        raise StateIntegrityError("persisted member receipt authority is invalid") from exc
    duplicated = {
        "receipt_id": row.receipt_id,
        "source_store_id": row.source_store_id,
        "entity_kind": row.entity_kind,
        "entity_id": row.entity_id,
        "source_generation_or_revision": row.source_generation_or_revision,
        "content_digest": row.content_digest,
        "availability": row.availability,
        "created_at": row.created_at,
    }
    if (
        any(authority[key] != value for key, value in duplicated.items())
        or authority["reopen_destination"] != reopen_column
        or row.schema_name != "bms.molbio-ngs.external-member-receipt"
        or row.schema_version != "1"
    ):
        raise StateIntegrityError("persisted member receipt duplicated authority mismatch")
    return authority


async def _required_receipt(
    session: AsyncSession,
    receipt_id: str | None,
    entity_kind: str,
) -> tuple[MolBioNGSMemberReceipt, dict[str, Any]]:
    if not isinstance(receipt_id, str) or not receipt_id.strip():
        raise StateValidationError(f"{entity_kind} receipt ID is required")
    row = await session.get(MolBioNGSMemberReceipt, receipt_id)
    if row is None:
        raise DomainStateNotFound(f"{entity_kind} receipt was not found")
    authority = _receipt_row_authority(row)
    if authority["entity_kind"] != entity_kind or authority["availability"] != "available":
        raise StateIntegrityError(f"{entity_kind} receipt has incompatible authority")
    return row, authority


async def _optional_receipt(
    session: AsyncSession,
    receipt_id: str | None,
    entity_kind: str,
) -> tuple[MolBioNGSMemberReceipt, dict[str, Any]] | None:
    if receipt_id is None:
        return None
    return await _required_receipt(session, receipt_id, entity_kind)


def _resolved_authority(receipt: ExternalMemberReceipt) -> dict[str, Any]:
    return {
        "source_store_id": receipt.source_store_id,
        "entity_kind": receipt.entity_kind,
        "entity_id": receipt.entity_id,
        "source_generation_or_revision": receipt.source_generation_or_revision,
        "content_digest": receipt.content_digest,
        "source_schema": receipt.source_schema,
        "availability": receipt.availability,
        "reopen_destination": receipt.reopen_destination,
    }


def _require_same_receipt_authority(
    persisted: Mapping[str, Any],
    resolved: ExternalMemberReceipt,
    *,
    label: str,
) -> None:
    expected = _resolved_authority(resolved)
    actual = {key: persisted[key] for key in expected}
    if actual != expected:
        raise StateIntegrityError(f"{label} receipt no longer matches authoritative source")


def _copy_receipt_with_identity(
    receipt: ExternalMemberReceipt,
    *,
    receipt_id: str,
    created_at: str,
) -> ExternalMemberReceipt:
    return build_external_member_receipt(
        source_store_id=receipt.source_store_id,
        entity_kind=receipt.entity_kind,
        entity_id=receipt.entity_id,
        source_generation_or_revision=receipt.source_generation_or_revision,
        content_digest=receipt.content_digest,
        source_schema=receipt.source_schema,
        availability=receipt.availability,
        reopen_destination=receipt.reopen_destination,
        receipt_id=receipt_id,
        created_at=created_at,
    )


def _derived_receipt_id(primary_receipt_id: str, label: str) -> str:
    return "member_receipt_" + str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"bms.molbio-ngs:{primary_receipt_id}:{label}")
    )


async def attach_job_evidence(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    job_id: str,
    idempotency_key: str,
) -> tuple[MolBioNGSMemberReceipt, MolBioNGSMemberReceipt]:
    """Persist exact job-launch and raw-manifest receipts as one idempotent attachment."""

    if not global_domain_experiment_id.strip() or not job_id.strip() or not idempotency_key.strip():
        raise StateValidationError("domain, job, and idempotency identities are required")
    job = await core_session.get(Job, job_id)
    if job is None or job.model_id != "nanopore":
        raise DomainStateNotFound("core NGS job was not found")
    params = job.params if isinstance(job.params, dict) else {}
    if params.get("global_domain_experiment_id") != global_domain_experiment_id:
        raise StateIntegrityError("core NGS job is not bound to the requested Domain Experiment")
    state_revision_id = params.get("molbio_ngs_state_revision_id")
    if not isinstance(state_revision_id, str) or not state_revision_id:
        raise StateIntegrityError("core NGS job lacks an exact persisted state binding")
    try:
        await get_state_revision(session, global_domain_experiment_id, state_revision_id)
    except DomainStateNotFound as exc:
        raise StateIntegrityError(
            "core NGS job state is not owned by the requested Domain Experiment"
        ) from exc
    resolved_job = await resolve_ngs_job_receipt(core_session, job_id=job_id)
    resolved_manifest = await resolve_ngs_result_manifest_receipt(core_session, job_id=job_id)
    request_sha256 = _digest(
        _canonical(
            {
                "global_domain_experiment_id": global_domain_experiment_id,
                "job_id": job_id,
            }
        )
    )
    scope = f"attach-job-evidence:{global_domain_experiment_id}"
    job_receipt_id = _id("member_receipt")
    replay_id = await _reserve_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=job_receipt_id,
    )
    if replay_id is not None:
        manifest_receipt_id = _derived_receipt_id(replay_id, "result-manifest")
        job_row = await session.get(MolBioNGSMemberReceipt, replay_id)
        manifest_row = await session.get(MolBioNGSMemberReceipt, manifest_receipt_id)
        if job_row is None or manifest_row is None:
            raise StateIntegrityError("completed job-evidence attachment is incomplete")
        _receipt_row_authority(job_row)
        _receipt_row_authority(manifest_row)
        return job_row, manifest_row

    manifest_receipt_id = _derived_receipt_id(job_receipt_id, "result-manifest")
    created_at = _now()
    job_row = await persist_member_receipt(
        session,
        _copy_receipt_with_identity(
            resolved_job, receipt_id=job_receipt_id, created_at=created_at
        ),
    )
    manifest_row = await persist_member_receipt(
        session,
        _copy_receipt_with_identity(
            resolved_manifest, receipt_id=manifest_receipt_id, created_at=created_at
        ),
    )
    await _complete_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=job_receipt_id,
        response={
            "ngs_job_receipt_id": job_row.receipt_id,
            "ngs_result_manifest_receipt_id": manifest_row.receipt_id,
        },
    )
    await session.flush()
    return job_row, manifest_row


async def attach_instrument_run_evidence(
    session: AsyncSession,
    core_session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    state_revision_id: str,
    run_id: str,
    observed_generation: int,
    idempotency_key: str,
) -> MolBioNGSMemberReceipt:
    """Persist one exact immutable ONT run-generation receipt."""

    if (
        not global_domain_experiment_id.strip()
        or not state_revision_id.strip()
        or not run_id.strip()
        or observed_generation < 1
        or not idempotency_key.strip()
    ):
        raise StateValidationError(
            "domain, state, run generation, and idempotency identities are required"
        )
    try:
        await get_state_revision(
            session, global_domain_experiment_id, state_revision_id
        )
    except DomainStateNotFound as exc:
        raise StateIntegrityError(
            "instrument run state is not owned by the requested Domain Experiment"
        ) from exc
    resolved = await resolve_ont_instrument_run_receipt(
        core_session,
        run_id=run_id,
        observed_generation=observed_generation,
    )
    request_sha256 = _digest(
        _canonical(
            {
                "global_domain_experiment_id": global_domain_experiment_id,
                "state_revision_id": state_revision_id,
                "run_id": run_id,
                "observed_generation": observed_generation,
            }
        )
    )
    scope = f"attach-instrument-run-evidence:{global_domain_experiment_id}"
    receipt_id = _id("member_receipt")
    replay_id = await _reserve_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=receipt_id,
    )
    if replay_id is not None:
        row = await session.get(MolBioNGSMemberReceipt, replay_id)
        if row is None:
            raise StateIntegrityError("completed instrument evidence attachment is incomplete")
        _receipt_row_authority(row)
        return row
    row = await persist_member_receipt(
        session,
        _copy_receipt_with_identity(
            resolved, receipt_id=receipt_id, created_at=_now()
        ),
    )
    await _audit_and_outbox(
        session,
        domain_id=global_domain_experiment_id,
        resource_id=row.receipt_id,
        state_revision_id=state_revision_id,
        event_type="molbio_ngs.instrument_run_evidence.attached",
        generation=observed_generation,
        payload={
            "schema": "bms.molbio-ngs.instrument-run-evidence-attached.v1",
            "global_domain_experiment_id": global_domain_experiment_id,
            "state_revision_id": state_revision_id,
            "receipt_id": row.receipt_id,
            "run_id": run_id,
            "observed_generation": observed_generation,
            "observation_sha256": resolved.content_digest,
        },
        created_by=None,
    )
    await _complete_idempotency(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=row.receipt_id,
        response={"ont_instrument_run_receipt_id": row.receipt_id},
    )
    await session.flush()
    return row


def _job_lifecycle(job: Job) -> str:
    values = {
        str(value).strip().lower()
        for value in (job.status, job.queue_status)
        if isinstance(value, str) and value.strip()
    }
    if values & {"failed", "error"}:
        return "failed"
    if values & {"cancelled", "canceled"}:
        return "cancelled"
    if "completed" in values and not values & {"queued", "running", "paused"}:
        return "completed"
    if values & {"running", "paused"}:
        return "running"
    return "queued"


def _binding_value(binding: Any, key: str) -> Any:
    return binding.get(key) if isinstance(binding, dict) else None


async def _verify_optional_receipts(
    domain_session: AsyncSession,
    core_session: AsyncSession,
    molbio_session: AsyncSession,
    *,
    job: Job,
    ont_instrument_run_receipt_id: str | None,
    molecular_revision_receipt_id: str | None,
    ngs_comparison_panel_receipt_id: str | None,
) -> None:
    params = job.params if isinstance(job.params, dict) else {}

    ont = await _optional_receipt(
        domain_session, ont_instrument_run_receipt_id, "ont_instrument_run"
    )
    if ont is not None:
        _row, authority = ont
        try:
            generation = int(authority["source_generation_or_revision"])
        except (TypeError, ValueError) as exc:
            raise StateIntegrityError("ONT receipt generation is invalid") from exc
        resolved = await resolve_ont_instrument_run_receipt(
            core_session,
            run_id=authority["entity_id"],
            observed_generation=generation,
        )
        _require_same_receipt_authority(authority, resolved, label="ONT instrument run")
        source_run_id = params.get("source_instrument_run_id")
        if source_run_id is not None and source_run_id != authority["entity_id"]:
            raise StateIntegrityError("ONT receipt does not match the job source run")
        launch_binding = params.get("ont_instrument_run_binding")
        if not isinstance(launch_binding, dict) or (
            launch_binding.get("run_id") != authority["entity_id"]
            or launch_binding.get("observed_generation") != generation
            or launch_binding.get("observation_sha256") != authority["content_digest"]
        ):
            raise StateIntegrityError("ont receipt does not match the job launch binding")

    molecular = await _optional_receipt(
        domain_session, molecular_revision_receipt_id, "molecular_revision"
    )
    if molecular is not None:
        _row, authority = molecular
        reopen = authority["reopen_destination"]
        reopen_params = reopen.get("params") if isinstance(reopen, dict) else None
        sequence_id = _binding_value(reopen_params, "sequence_id")
        if not isinstance(sequence_id, str):
            raise StateIntegrityError("molecular revision receipt lacks sequence identity")
        resolved = await resolve_molecular_revision_receipt(
            molbio_session,
            sequence_id=sequence_id,
            revision_id=authority["entity_id"],
        )
        _require_same_receipt_authority(authority, resolved, label="molecular revision")
        launch_binding = params.get("molbio_revision_binding")
        if not isinstance(launch_binding, dict) or (
            launch_binding.get("sequence_id") != sequence_id
            or launch_binding.get("revision_id") != authority["entity_id"]
            or launch_binding.get("revision_sha256") != authority["content_digest"]
        ):
            raise StateIntegrityError("molecular receipt does not match the job launch binding")

    panel = await _optional_receipt(
        domain_session, ngs_comparison_panel_receipt_id, "ngs_comparison_panel"
    )
    if panel is not None:
        _row, authority = panel
        try:
            version = int(authority["source_generation_or_revision"])
        except (TypeError, ValueError) as exc:
            raise StateIntegrityError("comparison panel receipt version is invalid") from exc
        resolved = await resolve_approved_comparison_panel_receipt(
            core_session,
            panel_id=authority["entity_id"],
            panel_version=version,
        )
        _require_same_receipt_authority(authority, resolved, label="comparison panel")
        launch_binding = params.get("comparison_panel_binding")
        if not isinstance(launch_binding, dict) or (
            launch_binding.get("panel_id") != authority["entity_id"]
            or launch_binding.get("panel_version") != version
            or launch_binding.get("panel_snapshot_sha256") != authority["content_digest"]
        ):
            raise StateIntegrityError("comparison panel receipt does not match the job launch binding")


def _assessment_result(
    *,
    lifecycle: str,
    manifest_integrity: str,
    manifest: Mapping[str, Any],
    manifest_schema: str,
    rule: AssessmentRule,
) -> str:
    if (
        manifest_integrity != "valid"
        or (rule.require_completed_job and lifecycle != "completed")
        or manifest_schema not in rule.pass_manifest_schemas
    ):
        return "REVIEW"
    verdict = manifest.get("verdict")
    if verdict == "FAIL":
        return "FAIL"
    if verdict == "PASS":
        # load_sequence_qc_manifest preserves PASS only when the server-owned
        # canonical profile registry explicitly authorizes automatic PASS.
        return "PASS"
    return "REVIEW"


async def create_evidence_assessment(
    domain_session: AsyncSession,
    core_session: AsyncSession,
    molbio_session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    state_revision_id: str,
    ngs_job_receipt_id: str,
    ngs_result_manifest_receipt_id: str,
    ngs_reference_revision_receipt_id: str,
    assessment_rule_id: str,
    idempotency_key: str,
    sample_revision_id: str | None = None,
    ont_instrument_run_receipt_id: str | None = None,
    molecular_revision_receipt_id: str | None = None,
    ngs_comparison_panel_receipt_id: str | None = None,
    notes: str | None = None,
) -> MolBioNGSEvidenceAssessment:
    """Create one immutable assessment after re-proving every source authority."""

    if not idempotency_key.strip() or len(notes or "") > 4000:
        raise StateValidationError("evidence idempotency key or notes are invalid")
    rule = ASSESSMENT_RULE_REGISTRY.get(assessment_rule_id)
    if rule is None:
        raise StateValidationError("assessment rule is not registered by the server")

    state_revision = await get_state_revision(
        domain_session, global_domain_experiment_id, state_revision_id
    )
    state_payload, _state_graph = await verify_state_revision_integrity(
        domain_session, state_revision
    )
    assessment_policy = state_payload.get("assessment_policy")
    if (
        not isinstance(assessment_policy, dict)
        or assessment_policy.get("rule_id") != assessment_rule_id
        or assessment_policy.get("completion_is_scientific_pass") is not False
    ):
        raise StateValidationError("state revision does not authorize the assessment rule")

    if sample_revision_id is not None:
        sample = await domain_session.get(MolBioNGSSampleRevision, sample_revision_id)
        declared_samples = state_payload.get("design", {}).get("sample_revision_ids", [])
        if (
            sample is None
            or sample.global_domain_experiment_id != global_domain_experiment_id
            or sample_revision_id not in declared_samples
        ):
            raise StateValidationError("sample revision is not declared by the state revision")

    _job_row, job_authority = await _required_receipt(
        domain_session, ngs_job_receipt_id, "ngs_job"
    )
    _manifest_row, manifest_authority = await _required_receipt(
        domain_session, ngs_result_manifest_receipt_id, "ngs_result_manifest"
    )
    _reference_row, reference_authority = await _required_receipt(
        domain_session,
        ngs_reference_revision_receipt_id,
        "ngs_reference_revision",
    )

    job = await core_session.get(Job, job_authority["entity_id"])
    if job is None or job.model_id != "nanopore":
        raise DomainStateNotFound("receipt-bound core NGS job was not found")
    resolved_job = await resolve_ngs_job_receipt(core_session, job_id=job.id)
    _require_same_receipt_authority(job_authority, resolved_job, label="NGS job")

    expected_manifest_entity = f"{job.id}:sequence-qc-manifest"
    if manifest_authority["entity_id"] != expected_manifest_entity:
        raise StateIntegrityError("result-manifest receipt is not owned by the receipt-bound job")
    resolved_manifest = await resolve_ngs_result_manifest_receipt(core_session, job_id=job.id)
    _require_same_receipt_authority(
        manifest_authority, resolved_manifest, label="NGS result manifest"
    )

    reference_reopen = reference_authority["reopen_destination"]
    reference_params = (
        reference_reopen.get("params") if isinstance(reference_reopen, dict) else None
    )
    reference_id = _binding_value(reference_params, "reference_id")
    revision_id = _binding_value(reference_params, "revision_id")
    if not isinstance(reference_id, str) or revision_id != reference_authority["entity_id"]:
        raise StateIntegrityError("reference receipt lacks exact local revision identity")
    resolved_reference = await resolve_ngs_reference_revision_receipt(
        domain_session,
        global_domain_experiment_id=global_domain_experiment_id,
        reference_id=reference_id,
        revision_id=revision_id,
    )
    _require_same_receipt_authority(
        reference_authority, resolved_reference, label="NGS reference revision"
    )
    reference_revision = await get_reference_revision(
        domain_session, reference_id, revision_id
    )
    reference_bytes = await read_reference_artifact_bytes(
        domain_session, reference_revision
    )
    if _sha256_bytes(reference_bytes) != reference_authority["content_digest"]:
        raise StateIntegrityError("reference receipt digest does not match managed bytes")

    reference_member = (
        await domain_session.execute(
            select(MolBioNGSDomainStateMember).where(
                MolBioNGSDomainStateMember.state_revision_id == state_revision_id,
                MolBioNGSDomainStateMember.receipt_id
                == ngs_reference_revision_receipt_id,
                MolBioNGSDomainStateMember.role == "ngs_reference",
            )
        )
    ).scalar_one_or_none()
    if reference_member is None:
        raise StateValidationError("state revision does not bind the required reference receipt")

    params = job.params if isinstance(job.params, dict) else {}
    if (
        params.get("global_domain_experiment_id") != global_domain_experiment_id
        or params.get("molbio_ngs_state_revision_id") != state_revision_id
        or params.get("ngs_reference_revision_id") != revision_id
        or params.get("expected_reference_fasta_sha256")
        != reference_authority["content_digest"]
    ):
        raise StateIntegrityError("job launch does not match Domain Experiment evidence authority")
    workflow_id = params.get("ont_workflow_id")
    analysis_policy = state_payload.get("analysis_policy")
    if (
        not isinstance(analysis_policy, dict)
        or workflow_id not in analysis_policy.get("allowed_workflow_ids", [])
        or manifest_authority["source_schema"]
        not in analysis_policy.get("required_manifest_schemas", [])
    ):
        raise StateValidationError("job workflow or result schema is ineligible for the state revision")

    result_root = resolve_persisted_job_result_root(job)
    manifest_path = find_manifest_in_result_root(result_root)
    raw_manifest = manifest_path.read_bytes()
    raw_manifest_sha256 = _sha256_bytes(raw_manifest)
    if raw_manifest_sha256 != manifest_authority["content_digest"]:
        raise StateIntegrityError("raw result manifest bytes do not match their receipt")
    manifest = load_sequence_qc_manifest(manifest_path, raw_bytes=raw_manifest)
    if manifest.get("job_id") != job.id:
        raise StateIntegrityError("result manifest does not name the receipt-bound job")
    if "MALFORMED_VERIFICATION_MANIFEST" in manifest.get("reason_codes", []):
        raise StateIntegrityError("result manifest is malformed")
    manifest_integrity = "valid"

    await _verify_optional_receipts(
        domain_session,
        core_session,
        molbio_session,
        job=job,
        ont_instrument_run_receipt_id=ont_instrument_run_receipt_id,
        molecular_revision_receipt_id=molecular_revision_receipt_id,
        ngs_comparison_panel_receipt_id=ngs_comparison_panel_receipt_id,
    )

    lifecycle = _job_lifecycle(job)
    scientific_assessment = _assessment_result(
        lifecycle=lifecycle,
        manifest_integrity=manifest_integrity,
        manifest=manifest,
        manifest_schema=manifest_authority["source_schema"],
        rule=rule,
    )
    request_payload = {
        "global_domain_experiment_id": global_domain_experiment_id,
        "state_revision_id": state_revision_id,
        "sample_revision_id": sample_revision_id,
        "receipt_ids": {
            "ngs_job": ngs_job_receipt_id,
            "ngs_result_manifest": ngs_result_manifest_receipt_id,
            "ngs_reference_revision": ngs_reference_revision_receipt_id,
            "ont_instrument_run": ont_instrument_run_receipt_id,
            "molecular_revision": molecular_revision_receipt_id,
            "ngs_comparison_panel": ngs_comparison_panel_receipt_id,
        },
        "assessment_rule_id": assessment_rule_id,
        # Compatibility alias only; caller verdicts never enter authority.
        "requested_assessment": scientific_assessment,
        "notes": notes,
        "created_by": SERVER_OWNED_ACTOR,
    }
    request_sha256 = _digest(_canonical(request_payload))
    scope = f"create-evidence-assessment:{global_domain_experiment_id}"
    evidence_id = _id("molbio_ngs_evidence")
    replay_id = await _reserve_idempotency(
        domain_session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=evidence_id,
    )
    if replay_id is not None:
        return await get_evidence_assessment(
            domain_session, global_domain_experiment_id, replay_id
        )

    created_at = _now()
    wrapper = {
        "schema": EVIDENCE_WRAPPER_SCHEMA,
        "evidence_id": evidence_id,
        **request_payload,
        "scientific_assessment": scientific_assessment,
        "job_lifecycle_state": lifecycle,
        "manifest_integrity": manifest_integrity,
        "raw_manifest_sha256": raw_manifest_sha256,
        "created_at": created_at,
    }
    canonical_wrapper = _canonical(wrapper)
    assessment = MolBioNGSEvidenceAssessment(
        evidence_id=evidence_id,
        global_domain_experiment_id=global_domain_experiment_id,
        state_revision_id=state_revision_id,
        sample_revision_id=sample_revision_id,
        ngs_job_receipt_id=ngs_job_receipt_id,
        ngs_result_manifest_receipt_id=ngs_result_manifest_receipt_id,
        ngs_reference_revision_receipt_id=ngs_reference_revision_receipt_id,
        ont_instrument_run_receipt_id=ont_instrument_run_receipt_id,
        molecular_revision_receipt_id=molecular_revision_receipt_id,
        ngs_comparison_panel_receipt_id=ngs_comparison_panel_receipt_id,
        assessment_rule_id=assessment_rule_id,
        requested_assessment=scientific_assessment,
        scientific_assessment=scientific_assessment,
        job_lifecycle_state=lifecycle,
        manifest_integrity=manifest_integrity,
        raw_manifest_sha256=raw_manifest_sha256,
        notes=notes,
        canonical_wrapper=canonical_wrapper,
        wrapper_sha256=_digest(canonical_wrapper),
        created_at=created_at,
        created_by=SERVER_OWNED_ACTOR,
    )
    domain_session.add(assessment)
    await domain_session.flush([assessment])
    await _audit_and_outbox(
        domain_session,
        domain_id=global_domain_experiment_id,
        resource_id=evidence_id,
        state_revision_id=state_revision_id,
        event_type="molbio_ngs.evidence.assessed",
        generation=1,
        payload={
            "schema": "bms.molbio-ngs.evidence-assessed.v1",
            "evidence_id": evidence_id,
            "wrapper_sha256": assessment.wrapper_sha256,
            "scientific_assessment": scientific_assessment,
        },
        created_by=SERVER_OWNED_ACTOR,
    )
    await domain_session.flush()
    await _complete_idempotency(
        domain_session,
        scope=scope,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        result_resource_id=evidence_id,
        response={
            "evidence_id": evidence_id,
            "wrapper_sha256": assessment.wrapper_sha256,
        },
    )
    await domain_session.flush()
    return assessment


def verify_evidence_assessment_integrity(
    assessment: MolBioNGSEvidenceAssessment,
) -> dict[str, Any]:
    if (
        not isinstance(assessment.canonical_wrapper, str)
        or _digest(assessment.canonical_wrapper) != assessment.wrapper_sha256
    ):
        raise StateIntegrityError("evidence assessment wrapper digest mismatch")
    try:
        wrapper = json.loads(assessment.canonical_wrapper)
    except json.JSONDecodeError as exc:
        raise StateIntegrityError("evidence assessment wrapper is invalid JSON") from exc
    if (
        not isinstance(wrapper, dict)
        or set(wrapper) != _WRAPPER_KEYS
        or _canonical(wrapper) != assessment.canonical_wrapper
        or wrapper.get("schema") != EVIDENCE_WRAPPER_SCHEMA
        or not isinstance(wrapper.get("receipt_ids"), dict)
        or set(wrapper["receipt_ids"]) != _RECEIPT_ID_KEYS
        or not isinstance(wrapper.get("raw_manifest_sha256"), str)
        or not _DIGEST.fullmatch(wrapper["raw_manifest_sha256"])
    ):
        raise StateIntegrityError("evidence assessment wrapper shape is invalid")
    expected = {
        "evidence_id": assessment.evidence_id,
        "global_domain_experiment_id": assessment.global_domain_experiment_id,
        "state_revision_id": assessment.state_revision_id,
        "sample_revision_id": assessment.sample_revision_id,
        "receipt_ids": {
            "ngs_job": assessment.ngs_job_receipt_id,
            "ngs_result_manifest": assessment.ngs_result_manifest_receipt_id,
            "ngs_reference_revision": assessment.ngs_reference_revision_receipt_id,
            "ont_instrument_run": assessment.ont_instrument_run_receipt_id,
            "molecular_revision": assessment.molecular_revision_receipt_id,
            "ngs_comparison_panel": assessment.ngs_comparison_panel_receipt_id,
        },
        "assessment_rule_id": assessment.assessment_rule_id,
        "requested_assessment": assessment.requested_assessment,
        "scientific_assessment": assessment.scientific_assessment,
        "job_lifecycle_state": assessment.job_lifecycle_state,
        "manifest_integrity": assessment.manifest_integrity,
        "raw_manifest_sha256": assessment.raw_manifest_sha256,
        "notes": assessment.notes,
        "created_at": assessment.created_at,
        "created_by": assessment.created_by,
    }
    if any(wrapper[key] != value for key, value in expected.items()):
        raise StateIntegrityError("evidence assessment wrapper authority mismatch")
    return wrapper


async def get_evidence_assessment(
    session: AsyncSession,
    global_domain_experiment_id: str,
    evidence_id: str,
) -> MolBioNGSEvidenceAssessment:
    assessment = await session.get(MolBioNGSEvidenceAssessment, evidence_id)
    if (
        assessment is None
        or assessment.global_domain_experiment_id != global_domain_experiment_id
    ):
        raise DomainStateNotFound("MolBio/NGS evidence assessment was not found")
    verify_evidence_assessment_integrity(assessment)
    return assessment


async def list_evidence_assessments(
    session: AsyncSession,
    global_domain_experiment_id: str,
) -> list[MolBioNGSEvidenceAssessment]:
    rows = list(
        (
            await session.execute(
                select(MolBioNGSEvidenceAssessment)
                .where(
                    MolBioNGSEvidenceAssessment.global_domain_experiment_id
                    == global_domain_experiment_id
                )
                .order_by(
                    MolBioNGSEvidenceAssessment.created_at,
                    MolBioNGSEvidenceAssessment.evidence_id,
                )
            )
        ).scalars()
    )
    for row in rows:
        verify_evidence_assessment_integrity(row)
    return rows


async def resolve_evidence_assessment_receipt(
    session: AsyncSession,
    *,
    global_domain_experiment_id: str,
    evidence_id: str,
) -> ExternalMemberReceipt:
    assessment = await get_evidence_assessment(
        session, global_domain_experiment_id, evidence_id
    )
    return build_external_member_receipt(
        source_store_id="molbio-ngs-domain",
        entity_kind="ngs_evidence_assessment",
        entity_id=assessment.evidence_id,
        source_generation_or_revision="1",
        content_digest=assessment.wrapper_sha256,
        source_schema=EVIDENCE_WRAPPER_SCHEMA,
        availability="available",
        reopen_destination={
            "surface": "molbio-ngs-evidence-assessment",
            "params": {
                "global_domain_experiment_id": global_domain_experiment_id,
                "evidence_id": assessment.evidence_id,
            },
        },
    )


__all__ = [
    "ASSESSMENT_RULE_REGISTRY",
    "EVIDENCE_WRAPPER_SCHEMA",
    "attach_instrument_run_evidence",
    "attach_job_evidence",
    "create_evidence_assessment",
    "get_evidence_assessment",
    "list_evidence_assessments",
    "resolve_evidence_assessment_receipt",
    "verify_evidence_assessment_integrity",
]
