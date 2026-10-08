from __future__ import annotations

import hashlib
import json
import re
import copy
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import rfc8785
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_models import ExperimentRevision
from molbio_ngs_models import MolBioNGSGlobalBinding, MolBioNGSSampleRevision
from molbio_ngs_services import (
    MolBioNGSServiceError,
    get_sample_revision,
    get_state_revision,
    verify_state_revision_integrity,
)
from services.molbio_ngs_references import get_reference_revision

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PROVENANCE_HIERARCHY_KEY = "alignment_hierarchy_authority_v1"


class OntNgsHierarchyError(RuntimeError):
    """Raised when persisted NGS hierarchy authority is incomplete or cross-bound."""


@dataclass(frozen=True)
class OntNgsHierarchyAuthority:
    project_id: str
    document: dict[str, Any]
    digest: str


def hierarchy_authority_record(authority: OntNgsHierarchyAuthority) -> dict[str, Any]:
    return {
        "schema": "biomodstack.alignment-hierarchy-authority.v1",
        "digest": authority.digest,
        "document": copy.deepcopy(authority.document),
    }


def capability_hierarchy_matches(job: Any, authority: OntNgsHierarchyAuthority) -> bool:
    provenance = getattr(job, "provenance", None)
    provenance = provenance if isinstance(provenance, Mapping) else {}
    record = provenance.get(PROVENANCE_HIERARCHY_KEY)
    if not isinstance(record, Mapping) or set(record) != {"schema", "digest", "document"}:
        return False
    document = record.get("document")
    digest = record.get("digest")
    if (
        record.get("schema") != "biomodstack.alignment-hierarchy-authority.v1"
        or not isinstance(document, dict)
        or not isinstance(digest, str)
        or document.get("job", {}).get("id") != getattr(job, "id", None)
        or authority.document.get("job", {}).get("id") != getattr(job, "id", None)
    ):
        return False
    try:
        observed_digest = hashlib.sha256(rfc8785.dumps(document)).hexdigest()
    except (TypeError, ValueError):
        return False
    return (
        secrets.compare_digest(digest, observed_digest)
        and secrets.compare_digest(digest, authority.digest)
        and document == authority.document
    )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OntNgsHierarchyError(f"{label} authority is unavailable")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise OntNgsHierarchyError(f"{label} authority is invalid")
    return value


def _sha(value: Any, label: str) -> str:
    text = _text(value, label)
    if _SHA256_RE.fullmatch(text) is None:
        raise OntNgsHierarchyError(f"{label} authority is invalid")
    return text


def _bounded_document(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024 * 1024:
        raise OntNgsHierarchyError(f"{label} authority is invalid")
    return value


def _generation(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise OntNgsHierarchyError(f"{label} generation authority is invalid")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise OntNgsHierarchyError(f"{label} generation authority is invalid") from exc
    if parsed < 0:
        raise OntNgsHierarchyError(f"{label} generation authority is invalid")
    return parsed


def _canonical_payload(record: Mapping[str, Any], label: str) -> str:
    raw = _bounded_document(record.get("canonical_payload"), f"{label} payload")
    expected = _sha(record.get("payload_sha256"), f"{label} digest")
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected:
        raise OntNgsHierarchyError(f"{label} digest mismatch")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OntNgsHierarchyError(f"{label} payload is invalid") from exc
    if rfc8785.dumps(parsed).decode("utf-8") != raw:
        raise OntNgsHierarchyError(f"{label} payload is not canonical")
    return expected


def _revision_identity(record: Mapping[str, Any], subject_id: str, label: str) -> tuple[str, str]:
    revision_id = _text(record.get("resource_id"), f"{label} revision")
    if record.get("subject_id") != subject_id:
        raise OntNgsHierarchyError(f"{label} revision is cross-bound")
    return revision_id, _sha(record.get("payload_sha256"), f"{label} revision digest")


def build_ont_ngs_hierarchy_authority(
    *,
    job_id: str,
    model_id: str,
    params: Mapping[str, Any],
    state_revision: Mapping[str, Any],
    membership_graph: Sequence[Mapping[str, Any]],
    binding: Mapping[str, Any],
    project_revision: Mapping[str, Any],
    global_revision: Mapping[str, Any],
    domain_revision: Mapping[str, Any],
    sample_revision: Mapping[str, Any],
    reference_revision: Mapping[str, Any],
) -> OntNgsHierarchyAuthority:
    if _JOB_ID_RE.fullmatch(job_id) is None or model_id != "nanopore":
        raise OntNgsHierarchyError("Job authority is invalid")
    params = _mapping(params, "Job parameters")
    workflow_id = params.get("ont_workflow_id") or params.get("workflow_id")
    input_mode = params.get("ont_input_mode") or params.get("input_mode")
    if workflow_id != "ont_fastq_qc" or input_mode != "fastq":
        raise OntNgsHierarchyError("Job workflow authority is invalid")

    domain_id = _text(params.get("global_domain_experiment_id"), "Domain Experiment")
    state_id = _text(params.get("molbio_ngs_state_revision_id"), "state revision")
    member_id = _text(params.get("state_membership_receipt_id"), "member receipt")
    reference_id = _text(params.get("ngs_reference_revision_id"), "reference revision")
    normalized_reference_sha = _sha(params.get("reference_sequence_sha256"), "normalized reference digest")
    canonical_reference_sha = _sha(
        params.get("managed_reference_snapshot_sha256"),
        "canonical reference digest",
    )
    canonical_reference_size = params.get("managed_reference_snapshot_size_bytes")
    if not isinstance(canonical_reference_size, int) or isinstance(canonical_reference_size, bool) or canonical_reference_size <= 0:
        raise OntNgsHierarchyError("canonical reference size authority is invalid")

    state_revision = _mapping(state_revision, "state revision")
    if state_revision.get("id") != state_id or state_revision.get("global_domain_experiment_id") != domain_id:
        raise OntNgsHierarchyError("state revision is cross-bound")
    state_payload_sha = _canonical_payload(state_revision, "state revision")
    state_membership_sha = _sha(state_revision.get("membership_graph_sha256"), "membership graph digest")
    graph = [dict(_mapping(item, "membership graph member")) for item in membership_graph]
    if hashlib.sha256(rfc8785.dumps(graph)).hexdigest() != state_membership_sha:
        raise OntNgsHierarchyError("membership graph digest mismatch")
    members = [item for item in graph if item.get("receipt_id") == member_id]
    if len(members) != 1:
        raise OntNgsHierarchyError("member receipt is not in the frozen state revision")
    member = members[0]
    if (
        member.get("role") != "ngs_reference"
        or member.get("entity_kind") != "ngs_reference_revision"
        or member.get("entity_id") != reference_id
        or member.get("availability") != "available"
        or member.get("content_digest") != canonical_reference_sha
    ):
        raise OntNgsHierarchyError("member receipt authority is cross-bound")
    member_receipt_sha = _sha(member.get("receipt_sha256"), "member receipt digest")
    sample_revision_id = _text(member.get("sample_revision_id"), "sample revision")

    sample_revision = _mapping(sample_revision, "sample revision")
    if sample_revision.get("id") != sample_revision_id or sample_revision.get("global_domain_experiment_id") != domain_id:
        raise OntNgsHierarchyError("sample revision is cross-bound")
    sample_payload_sha = _canonical_payload(sample_revision, "sample revision")

    reference_revision = _mapping(reference_revision, "reference revision")
    if reference_revision.get("id") != reference_id or reference_revision.get("global_domain_experiment_id") != domain_id:
        raise OntNgsHierarchyError("reference revision is cross-bound")
    reference_payload_sha = _canonical_payload(reference_revision, "reference revision")
    if (
        reference_revision.get("canonical_fasta_sha256") != canonical_reference_sha
        or reference_revision.get("canonical_fasta_size_bytes") != canonical_reference_size
        or reference_revision.get("normalized_sequence_sha256") != normalized_reference_sha
    ):
        raise OntNgsHierarchyError("reference revision digest authority is cross-bound")

    binding = _mapping(binding, "binding")
    binding_id = _text(binding.get("binding_revision_id"), "binding revision")
    if state_revision.get("binding_revision_id") != binding_id:
        raise OntNgsHierarchyError("state revision binding is stale or cross-bound")
    if binding.get("binding_state") != "acknowledged" or binding.get("global_domain_experiment_id") != domain_id:
        raise OntNgsHierarchyError("binding authority is unavailable")
    binding_json = _bounded_document(binding.get("global_binding_receipt_json"), "binding receipt")
    binding_sha = _sha(binding.get("global_binding_receipt_sha256"), "binding receipt digest")
    if hashlib.sha256(binding_json.encode("utf-8")).hexdigest() != binding_sha:
        raise OntNgsHierarchyError("binding receipt digest mismatch")
    try:
        binding_receipt = json.loads(binding_json)
    except json.JSONDecodeError as exc:
        raise OntNgsHierarchyError("binding receipt is invalid") from exc
    if rfc8785.dumps(binding_receipt).decode("utf-8") != binding_json:
        raise OntNgsHierarchyError("binding receipt is not canonical")
    if (
        binding_receipt.get("schema") != "bms.ngs-molbio.global-binding-receipt.v1"
        or _mapping(binding_receipt.get("acknowledgement"), "binding acknowledgement").get("status") != "verified"
        or binding.get("global_binding_receipt_id") != binding_receipt.get("receipt_id")
    ):
        raise OntNgsHierarchyError("binding receipt authority is invalid")

    project = _mapping(binding_receipt.get("project"), "Project binding")
    global_experiment = _mapping(binding_receipt.get("global_experiment"), "Global Experiment binding")
    domain_experiment = _mapping(binding_receipt.get("domain_experiment"), "Domain Experiment binding")
    project_id = _text(project.get("id"), "Project")
    global_id = _text(global_experiment.get("id"), "Global Experiment")
    if (
        binding.get("project_id") != project_id
        or str(binding.get("project_generation")) != str(project.get("generation"))
        or binding.get("project_digest") != project.get("digest")
        or binding.get("global_experiment_id") != global_id
        or str(binding.get("global_experiment_generation")) != str(global_experiment.get("generation"))
        or binding.get("global_experiment_digest") != global_experiment.get("digest")
        or domain_experiment.get("id") != domain_id
        or binding.get("global_domain_experiment_revision_id") != domain_experiment.get("revision_id")
        or binding.get("global_domain_experiment_revision_digest") != domain_experiment.get("digest")
        or state_revision.get("global_domain_experiment_revision_id") != domain_experiment.get("revision_id")
        or domain_experiment.get("domain_kind") != "ngs_molbio"
    ):
        raise OntNgsHierarchyError("binding receipt duplicates disagree")

    project_revision_id, project_digest = _revision_identity(project_revision, project_id, "Project")
    global_revision_id, global_digest = _revision_identity(global_revision, global_id, "Global Experiment")
    domain_revision_id, domain_digest = _revision_identity(domain_revision, domain_id, "Domain Experiment")
    if (
        project_revision_id != project.get("revision_id")
        or project_digest != project.get("digest")
        or global_revision_id != global_experiment.get("revision_id")
        or global_digest != global_experiment.get("digest")
        or domain_revision_id != domain_experiment.get("revision_id")
        or domain_digest != domain_experiment.get("digest")
    ):
        raise OntNgsHierarchyError("frozen global revision authority is cross-bound")

    document = {
        "schema": "biomodstack.ont-fastq-qc-hierarchy-authority.v1",
        "job": {"id": job_id, "workflow_id": "ont_fastq_qc", "input_mode": "fastq"},
        "project": {
            "id": project_id,
            "revision_id": project_revision_id,
            "digest": project_digest,
            "generation": _generation(project.get("generation"), "Project"),
        },
        "global_experiment": {
            "id": global_id,
            "revision_id": global_revision_id,
            "digest": global_digest,
            "generation": _generation(global_experiment.get("generation"), "Global Experiment"),
        },
        "domain_experiment": {
            "id": domain_id,
            "revision_id": domain_revision_id,
            "digest": domain_digest,
            "state_revision_id": state_id,
            "state_payload_sha256": state_payload_sha,
            "membership_graph_sha256": state_membership_sha,
            "binding_revision_id": binding_id,
        },
        "binding": {
            "receipt_id": _text(binding_receipt.get("receipt_id"), "binding receipt ID"),
            "receipt_sha256": binding_sha,
        },
        "member": {
            "receipt_id": member_id,
            "receipt_sha256": member_receipt_sha,
            "role": "ngs_reference",
        },
        "sample": {"revision_id": sample_revision_id, "payload_sha256": sample_payload_sha},
        "reference": {
            "revision_id": reference_id,
            "payload_sha256": reference_payload_sha,
            "canonical_fasta_sha256": canonical_reference_sha,
            "canonical_fasta_size_bytes": canonical_reference_size,
            "normalized_sequence_sha256": normalized_reference_sha,
        },
    }
    digest = hashlib.sha256(rfc8785.dumps(document)).hexdigest()
    return OntNgsHierarchyAuthority(project_id=project_id, document=document, digest=digest)


def bind_ont_ngs_hierarchy_source_authority(
    authority: OntNgsHierarchyAuthority,
    *,
    source_fastq_sha256: Any,
    artifact_set_sha256: Any,
    sequence_qc_manifest_sha256: Any,
    verification_manifest_sha256: Any,
    reference_sequence_sha256: Any,
) -> OntNgsHierarchyAuthority:
    expected_reference = authority.document.get("reference", {}).get("normalized_sequence_sha256")
    if _sha(reference_sequence_sha256, "result reference authority") != expected_reference:
        raise OntNgsHierarchyError("source FASTQ result authority is cross-bound")
    source_authority = {
        "sha256": _sha(source_fastq_sha256, "source FASTQ result authority"),
        "artifact_set_sha256": _sha(artifact_set_sha256, "artifact-set result authority"),
        "sequence_qc_manifest_sha256": _sha(
            sequence_qc_manifest_sha256,
            "sequence-QC manifest result authority",
        ),
        "verification_manifest_sha256": _sha(
            verification_manifest_sha256,
            "verification manifest result authority",
        ),
    }
    existing = authority.document.get("source_fastq")
    if existing is not None and existing != source_authority:
        raise OntNgsHierarchyError("source FASTQ result authority is cross-bound")
    document = {**authority.document, "source_fastq": source_authority}
    digest = hashlib.sha256(rfc8785.dumps(document)).hexdigest()
    return OntNgsHierarchyAuthority(
        project_id=authority.project_id,
        document=document,
        digest=digest,
    )


def _bind_persisted_result_source_authority(
    job: Any,
    authority: OntNgsHierarchyAuthority,
) -> OntNgsHierarchyAuthority:
    provenance = job.provenance if isinstance(getattr(job, "provenance", None), dict) else {}
    fresh = provenance.get("result_integrity")
    historical = provenance.get("ont_fastq_qc_reconciliation_v1")
    verification_key: str
    record: Mapping[str, Any]
    if isinstance(fresh, dict) and fresh.get("result_kind") == "ngs_sequence_qc":
        record = fresh
        verification_key = "construct_verification_manifest_sha256"
    elif (
        isinstance(historical, dict)
        and historical.get("schema") == "bms.ont-fastq-qc-reconciliation.v1"
    ):
        record = historical
        verification_key = "verification_manifest_sha256"
    else:
        raise OntNgsHierarchyError("source FASTQ result authority is unavailable")
    return bind_ont_ngs_hierarchy_source_authority(
        authority,
        source_fastq_sha256=record.get("source_fastq_sha256"),
        artifact_set_sha256=record.get("artifact_set_sha256"),
        sequence_qc_manifest_sha256=record.get("sequence_qc_manifest_sha256"),
        verification_manifest_sha256=record.get(verification_key),
        reference_sequence_sha256=record.get("reference_sequence_sha256"),
    )


async def _resolve_ont_ngs_hierarchy_base(
    job: Any,
    domain_session: AsyncSession,
    experiment_session: AsyncSession,
) -> OntNgsHierarchyAuthority:
    from services.ont_ngs_completion import OntNgsCompletionError, is_ont_fastq_qc_job
    from services.ont_ngs_hierarchy_loader import (
        OntNgsHierarchyLoadError,
        resolve_hierarchy_records,
    )

    try:
        if not is_ont_fastq_qc_job(job):
            raise OntNgsHierarchyError("job is not canonical ONT FASTQ-QC")
    except OntNgsCompletionError as exc:
        raise OntNgsHierarchyError(str(exc)) from exc
    try:
        return await resolve_hierarchy_records(
            job,
            domain_session,
            experiment_session,
            build_authority=build_ont_ngs_hierarchy_authority,
            get_state_revision_fn=get_state_revision,
            verify_state_revision_fn=verify_state_revision_integrity,
            get_reference_revision_fn=get_reference_revision,
            get_sample_revision_fn=get_sample_revision,
        )
    except (MolBioNGSServiceError, OntNgsHierarchyLoadError) as exc:
        raise OntNgsHierarchyError("persisted NGS hierarchy authority is unavailable") from exc


async def resolve_ont_ngs_hierarchy_authority(
    job: Any,
    domain_session: AsyncSession,
    experiment_session: AsyncSession,
) -> OntNgsHierarchyAuthority:
    hierarchy = await _resolve_ont_ngs_hierarchy_base(job, domain_session, experiment_session)
    return _bind_persisted_result_source_authority(job, hierarchy)


async def resolve_ont_ngs_hierarchy_authority_for_reconciliation(
    job: Any,
    domain_session: AsyncSession,
    experiment_session: AsyncSession,
    *,
    source_fastq_sha256: Any,
    artifact_set_sha256: Any,
    sequence_qc_manifest_sha256: Any,
    verification_manifest_sha256: Any,
    reference_sequence_sha256: Any,
) -> OntNgsHierarchyAuthority:
    hierarchy = await _resolve_ont_ngs_hierarchy_base(job, domain_session, experiment_session)
    return bind_ont_ngs_hierarchy_source_authority(
        hierarchy,
        source_fastq_sha256=source_fastq_sha256,
        artifact_set_sha256=artifact_set_sha256,
        sequence_qc_manifest_sha256=sequence_qc_manifest_sha256,
        verification_manifest_sha256=verification_manifest_sha256,
        reference_sequence_sha256=reference_sequence_sha256,
    )
