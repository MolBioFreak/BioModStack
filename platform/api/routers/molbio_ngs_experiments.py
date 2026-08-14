"""Typed HTTP contract for MolBio/NGS global-keyed scientific state."""
from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_database import get_experiment_session
from database import get_session
from molbio_database import get_molbio_session
from molbio_ngs_database import get_molbio_ngs_session
from molbio_ngs_models import (
    MolBioNGSDomainState,
    MolBioNGSDomainStateRevision,
    MolBioNGSEvidenceAssessment,
    MolBioNGSReferenceResource,
    MolBioNGSReferenceRevision,
    MolBioNGSSample,
    MolBioNGSSampleRevision,
)
from molbio_ngs_services import (
    DomainStateAlreadyExists,
    DomainStateNotFound,
    GlobalAdapterUnavailable,
    GlobalBindingError,
    IdempotencyConflict,
    RevisionConflict,
    StateMember,
    StateIntegrityError,
    StateValidationError,
    acknowledge_global_binding,
    append_sample_revision,
    create_sample,
    get_domain_experiment_view,
    get_domain_state,
    get_project_domain_summary,
    get_sample,
    get_sample_revision,
    get_state_revision,
    initialize_domain_state,
    list_domain_states,
    list_project_domain_experiments,
    list_sample_revisions,
    list_samples,
    list_state_revisions,
    save_state_revision,
    verify_global_domain_binding,
    verify_state_revision_integrity,
)
from services.molbio_ngs_member_receipts import (
    ExternalMemberReceipt,
    persist_member_receipt,
    resolve_approved_comparison_panel_receipt,
    resolve_molecular_operation_receipt,
    resolve_molecular_revision_receipt,

    resolve_pcr_experiment_revision_receipt,
    resolve_primer_revision_receipt,
    resolve_sample_revision_receipt,
    resolve_state_revision_receipt,
    serialize_external_member_receipt,
)
from services.molbio_ngs_references import (
    append_reference_revision,
    archive_reference,
    create_reference,
    create_reference_from_molbio_revision,
    get_reference_resource,
    get_reference_revision,
    import_browser_entry,
    list_reference_revisions,
    list_references,
    resolve_ngs_reference_revision_receipt,
)
from services.molbio_ngs_evidence import (
    attach_instrument_run_evidence,
    attach_job_evidence,
    create_evidence_assessment,
    get_evidence_assessment,
    list_evidence_assessments,
    resolve_evidence_assessment_receipt,
)


router = APIRouter(prefix="/api/molbio-ngs", tags=["molbio-ngs-experiments"])


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )


class SampleSourcePayload(StrictModel):
    organism: str | None = Field(default=None, max_length=255)
    strain: str | None = Field(default=None, max_length=255)
    external_ids: list[str] = Field(max_length=100)


class SamplePreparationPayload(StrictModel):
    method: str = Field(min_length=1, max_length=255)
    batch_id: str | None = Field(default=None, max_length=255)
    prepared_at: str | None = Field(default=None, max_length=255)


class SampleLabelsPayload(StrictModel):
    container_label: str | None = Field(default=None, max_length=255)
    barcode: str | None = Field(default=None, max_length=255)
    minknow_sample_id: str | None = Field(default=None, max_length=255)


class SampleRevisionPayload(StrictModel):
    schema_: Literal["bms.molbio-ngs.sample-revision.v1"] = Field(alias="schema")
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(max_length=4000)
    sample_kind: str = Field(min_length=1, max_length=128)
    source: SampleSourcePayload
    preparation: SamplePreparationPayload
    labels: SampleLabelsPayload
    notes: str = Field(max_length=4000)


class CreateSampleRequest(StrictModel):
    payload: SampleRevisionPayload
    idempotency_key: str = Field(min_length=1, max_length=255)


class CreateSampleRevisionRequest(StrictModel):
    payload: SampleRevisionPayload
    expected_head_generation: int = Field(ge=1)
    parent_revision_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)


class SampleReopenParams(StrictModel):
    global_domain_experiment_id: str
    sample_id: str


class SampleRevisionReopenParams(SampleReopenParams):
    revision_id: str


class SampleReopenDestination(StrictModel):
    surface: Literal["molbio-ngs-sample"]
    params: SampleReopenParams


class SampleRevisionReopenDestination(StrictModel):
    surface: Literal["molbio-ngs-sample-revision"]
    params: SampleRevisionReopenParams


class SampleResponse(StrictModel):
    id: str
    global_domain_experiment_id: str
    current_revision_id: str | None
    head_generation: int
    archived_at: str | None
    created_at: str
    updated_at: str
    reopen_destination: SampleReopenDestination


class SampleRevisionResponse(StrictModel):
    id: str
    sample_id: str
    global_domain_experiment_id: str
    revision_number: int
    parent_revision_id: str | None
    schema_name: str
    schema_version: str
    payload: SampleRevisionPayload
    payload_sha256: str
    created_at: str
    created_by: str | None
    reopen_destination: SampleRevisionReopenDestination


ReferenceMoleculeType = Literal["dna", "rna"]
ReferenceTopology = Literal["linear", "circular", "mixed", "unknown"]


class CreateReferenceRequest(StrictModel):
    global_domain_experiment_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    fasta: str = Field(min_length=1)
    molecule_type: ReferenceMoleculeType
    topology: ReferenceTopology
    coordinate_contract: str = Field(min_length=1, max_length=128)
    source_provenance: dict[str, object]
    idempotency_key: str = Field(min_length=1, max_length=255)


class CreateReferenceRevisionRequest(StrictModel):
    fasta: str = Field(min_length=1)
    molecule_type: ReferenceMoleculeType
    topology: ReferenceTopology
    coordinate_contract: str = Field(min_length=1, max_length=128)
    source_provenance: dict[str, object]
    expected_head_generation: int = Field(ge=1)
    parent_revision_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)


class MolBioReferenceImportRequest(StrictModel):
    global_domain_experiment_id: str = Field(min_length=1, max_length=128)
    sequence_id: str = Field(min_length=1, max_length=128)
    molecular_revision_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    molecule_type: ReferenceMoleculeType
    topology: ReferenceTopology
    coordinate_contract: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)


class LegacyBrowserReferenceEntry(StrictModel):
    id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    source: Literal["fasta", "path"]
    fasta: str | None = None
    path: str | None = None
    created_at: str = Field(alias="createdAt", min_length=1, max_length=255)
    updated_at: str = Field(alias="updatedAt", min_length=1, max_length=255)


class LegacyBrowserReferenceImportRequest(StrictModel):
    global_domain_experiment_id: str = Field(min_length=1, max_length=128)
    entry: LegacyBrowserReferenceEntry
    name: str = Field(min_length=1, max_length=255)
    molecule_type: ReferenceMoleculeType
    topology: ReferenceTopology
    coordinate_contract: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)


class ArchiveReferenceRequest(StrictModel):
    expected_head_generation: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class ReferenceRevisionReceiptRequest(StrictModel):
    reference_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)


class ReferenceReopenParams(StrictModel):
    reference_id: str


class ReferenceRevisionReopenParams(ReferenceReopenParams):
    revision_id: str


class ReferenceReopenDestination(StrictModel):
    surface: Literal["molbio-ngs-reference"]
    params: ReferenceReopenParams


class ReferenceRevisionReopenDestination(StrictModel):
    surface: Literal["molbio-ngs-reference-revision"]
    params: ReferenceRevisionReopenParams


class ReferenceResponse(StrictModel):
    id: str
    global_domain_experiment_id: str
    name: str
    current_revision_id: str | None
    head_generation: int
    archived_at: str | None
    created_at: str
    updated_at: str
    reopen_destination: ReferenceReopenDestination


class ReferenceFastaDescriptor(StrictModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    media_type: Literal["text/x-fasta; charset=us-ascii"]


class ReferenceContigDescriptor(StrictModel):
    name: str = Field(min_length=1)
    length: int = Field(gt=0)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReferenceRevisionPayload(StrictModel):
    schema_: Literal["bms.molbio-ngs.reference-revision.v1"] = Field(alias="schema")
    reference_id: str
    revision_number: int = Field(gt=0)
    parent_revision_id: str | None
    head_generation: int = Field(gt=0)
    canonical_fasta: ReferenceFastaDescriptor
    contigs: list[ReferenceContigDescriptor] = Field(min_length=1)
    contig_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sequence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    molecule_type: ReferenceMoleculeType
    topology: ReferenceTopology
    coordinate_contract: str = Field(min_length=1, max_length=128)
    source_provenance: dict[str, object]


class ReferenceRevisionResponse(StrictModel):
    id: str
    reference_id: str
    global_domain_experiment_id: str
    revision_number: int
    parent_revision_id: str | None
    schema_name: str
    schema_version: str
    payload: ReferenceRevisionPayload
    payload_sha256: str
    canonical_fasta_sha256: str
    canonical_fasta_size_bytes: int
    contig_manifest_sha256: str
    normalized_sequence_sha256: str | None
    molecule_type: ReferenceMoleculeType
    topology: ReferenceTopology
    coordinate_contract: str
    created_at: str
    created_by: str | None
    reopen_destination: ReferenceRevisionReopenDestination


class InitializeStateRequest(StrictModel):
    global_domain_experiment_revision_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)


class DomainStateResponse(StrictModel):
    global_domain_experiment_id: str
    current_state_revision_id: str | None
    head_generation: int
    created_at: str
    updated_at: str


class LocalDomainCountsResponse(StrictModel):
    samples: int = Field(ge=0)
    references: int = Field(ge=0)
    evidence_assessments: int = Field(ge=0)


class DomainViewAvailabilityResponse(StrictModel):
    local_state: Literal["available"]
    persisted_global_binding: Literal["acknowledged"]
    global_adapter: Literal["available"]


class DomainViewReopenParams(StrictModel):
    domain_experiment_id: str


class DomainViewReopenDestination(StrictModel):
    surface: Literal["molbio-ngs-domain-experiment"]
    params: DomainViewReopenParams


class DomainExperimentViewResponse(StrictModel):
    project_id: str
    global_experiment_id: str
    domain_experiment_id: str
    global_domain_experiment_revision_id: str
    local_state_revision_id: str | None
    local_state_head_generation: int = Field(ge=0)
    local_counts: LocalDomainCountsResponse
    availability: DomainViewAvailabilityResponse
    created_at: str
    updated_at: str
    reopen_destination: DomainViewReopenDestination


class ProjectSummaryAvailabilityResponse(StrictModel):
    persisted_global_bindings: Literal["acknowledged_only"]
    global_adapter: Literal["available"]


class ProjectSummaryReopenParams(StrictModel):
    project_id: str


class ProjectSummaryReopenDestination(StrictModel):
    surface: Literal["molbio-ngs-project-summary"]
    params: ProjectSummaryReopenParams


class ProjectDomainSummaryResponse(StrictModel):
    project_id: str
    domain_experiment_count: int = Field(ge=0)
    local_totals: LocalDomainCountsResponse
    availability: ProjectSummaryAvailabilityResponse
    reopen_destination: ProjectSummaryReopenDestination


StateMemberRole = Literal[
    "molecular_expected_construct",
    "molecular_input_fragment",
    "molecular_assembly_product",
    "molecular_pcr_template",
    "molecular_pcr_product",
    "molecular_primer_forward",
    "molecular_primer_reverse",
    "molecular_operation",
    "molecular_pcr_experiment",
    "ngs_reference",
    "ngs_comparison_panel",
    "ngs_instrument_run",
    "ngs_analysis_job",
    "ngs_analysis_result_manifest",
    "ngs_verification_assessment",
]


class StateDesignPayload(StrictModel):
    sample_revision_ids: list[str]
    conditions: list[dict]
    replicates: list[dict]
    expected_molecule_roles: list[StateMemberRole]


class StateReferencePolicy(StrictModel):
    required_roles: list[StateMemberRole]
    coordinate_policy: Literal["exact_revision"]


class StateAcquisitionPolicy(StrictModel):
    platform: Literal["ont", "external", "none"]
    required_terminal_manifest: bool


class StateAnalysisPolicy(StrictModel):
    allowed_workflow_ids: list[str]
    required_manifest_schemas: list[str]


class StateAssessmentPolicy(StrictModel):
    rule_id: str = Field(min_length=1, max_length=128)
    completion_is_scientific_pass: Literal[False]


class StateRevisionPayload(StrictModel):
    schema_: Literal["bms.molbio-ngs.domain-state-revision.v1"] = Field(alias="schema")
    design: StateDesignPayload
    reference_policy: StateReferencePolicy
    acquisition_policy: StateAcquisitionPolicy
    analysis_policy: StateAnalysisPolicy
    assessment_policy: StateAssessmentPolicy
    notes: str = Field(max_length=4000)


class StateMemberInput(StrictModel):
    receipt_id: str = Field(min_length=1, max_length=128)
    role: StateMemberRole
    ordinal: int = Field(ge=0)
    sample_revision_id: str | None = Field(default=None, max_length=128)


class SaveStateRevisionRequest(StrictModel):
    global_domain_experiment_revision_id: str = Field(min_length=1, max_length=128)
    expected_head_generation: int = Field(ge=0)
    parent_revision_id: str | None = Field(default=None, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)
    payload: StateRevisionPayload
    members: list[StateMemberInput]


class StateMemberResponse(StrictModel):
    receipt_id: str
    role: StateMemberRole
    ordinal: int
    sample_revision_id: str | None
    source_store_id: str
    entity_kind: str
    entity_id: str
    source_generation_or_revision: str
    content_digest: str
    source_schema: str
    availability: str
    reopen_destination: dict
    receipt_sha256: str


class StateRevisionResponse(StrictModel):
    id: str
    global_domain_experiment_id: str
    global_domain_experiment_revision_id: str
    revision_number: int
    parent_revision_id: str | None
    schema_name: str
    schema_version: str
    payload: StateRevisionPayload
    payload_sha256: str
    membership_graph_sha256: str
    members: list[StateMemberResponse]
    created_at: str
    created_by: str | None


class MolecularRevisionReceiptRequest(StrictModel):
    sequence_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)


class PrimerRevisionReceiptRequest(StrictModel):
    primer_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)


class PCRRevisionReceiptRequest(StrictModel):
    experiment_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)


class MolecularOperationReceiptRequest(StrictModel):
    operation_id: str = Field(min_length=1, max_length=128)


class ComparisonPanelReceiptRequest(StrictModel):
    panel_id: str = Field(min_length=1, max_length=128)
    panel_version: int = Field(ge=1)


class AttachJobEvidenceRequest(StrictModel):
    job_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)


class AttachInstrumentRunEvidenceRequest(StrictModel):
    state_revision_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    observed_generation: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=255)


class EvidenceAssessmentRequest(StrictModel):
    state_revision_id: str = Field(min_length=1, max_length=128)
    sample_revision_id: str | None = Field(default=None, max_length=128)
    ngs_job_receipt_id: str = Field(min_length=1, max_length=128)
    ngs_result_manifest_receipt_id: str = Field(min_length=1, max_length=128)
    ngs_reference_revision_receipt_id: str = Field(min_length=1, max_length=128)
    ont_instrument_run_receipt_id: str | None = Field(default=None, max_length=128)
    molecular_revision_receipt_id: str | None = Field(default=None, max_length=128)
    ngs_comparison_panel_receipt_id: str | None = Field(default=None, max_length=128)
    assessment_rule_id: str = Field(min_length=1, max_length=128)
    notes: str | None = Field(default=None, max_length=4000)
    idempotency_key: str = Field(min_length=1, max_length=255)


class EvidenceAssessmentReceiptRequest(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=128)


class SampleRevisionReceiptRequest(StrictModel):
    sample_id: str = Field(min_length=1, max_length=128)
    sample_revision_id: str = Field(min_length=1, max_length=128)


class StateRevisionReceiptRequest(StrictModel):
    state_revision_id: str = Field(min_length=1, max_length=128)


class ExternalMemberReceiptResponse(StrictModel):
    schema_: Literal["bms.molbio-ngs.external-member-receipt.v1"] = Field(alias="schema")
    receipt_id: str
    source_store_id: str
    entity_kind: str
    entity_id: str
    source_generation_or_revision: str
    content_digest: str
    source_schema: str
    availability: Literal["available", "archived", "unavailable"]
    reopen_destination: dict
    created_at: str


class AttachJobEvidenceResponse(StrictModel):
    ngs_job: ExternalMemberReceiptResponse
    ngs_result_manifest: ExternalMemberReceiptResponse


class EvidenceReceiptIdsResponse(StrictModel):
    ngs_job: str
    ngs_result_manifest: str
    ngs_reference_revision: str
    ont_instrument_run: str | None
    molecular_revision: str | None
    ngs_comparison_panel: str | None


class EvidenceReopenParams(StrictModel):
    global_domain_experiment_id: str
    evidence_id: str


class EvidenceReopenDestination(StrictModel):
    surface: Literal["molbio-ngs-evidence-assessment"]
    params: EvidenceReopenParams


class EvidenceAssessmentResponse(StrictModel):
    evidence_id: str
    global_domain_experiment_id: str
    state_revision_id: str
    sample_revision_id: str | None
    receipt_ids: EvidenceReceiptIdsResponse
    assessment_rule_id: str
    scientific_assessment: Literal["PASS", "FAIL", "REVIEW"]
    job_lifecycle_state: Literal["queued", "running", "completed", "failed", "cancelled"]
    manifest_integrity: Literal["valid", "invalid", "unavailable"]
    raw_manifest_sha256: str
    notes: str | None
    wrapper_sha256: str
    created_at: str
    created_by: str | None
    reopen_destination: EvidenceReopenDestination


DomainSession = Annotated[AsyncSession, Depends(get_molbio_ngs_session)]
GlobalSession = Annotated[AsyncSession, Depends(get_experiment_session)]
MolBioSession = Annotated[AsyncSession, Depends(get_molbio_session)]
CoreSession = Annotated[AsyncSession, Depends(get_session)]


def _state_response(state: MolBioNGSDomainState) -> DomainStateResponse:
    return DomainStateResponse(
        global_domain_experiment_id=state.global_domain_experiment_id,
        current_state_revision_id=state.current_state_revision_id,
        head_generation=state.head_generation,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def _sample_response(sample: MolBioNGSSample) -> SampleResponse:
    return SampleResponse(
        id=sample.id,
        global_domain_experiment_id=sample.global_domain_experiment_id,
        current_revision_id=sample.current_revision_id,
        head_generation=sample.head_generation,
        archived_at=sample.archived_at,
        created_at=sample.created_at,
        updated_at=sample.updated_at,
        reopen_destination=SampleReopenDestination(
            surface="molbio-ngs-sample",
            params=SampleReopenParams(
                global_domain_experiment_id=sample.global_domain_experiment_id,
                sample_id=sample.id,
            ),
        ),
    )


def _sample_revision_response(
    revision: MolBioNGSSampleRevision,
) -> SampleRevisionResponse:
    return SampleRevisionResponse(
        id=revision.id,
        sample_id=revision.sample_id,
        global_domain_experiment_id=revision.global_domain_experiment_id,
        revision_number=revision.revision_number,
        parent_revision_id=revision.parent_revision_id,
        schema_name=revision.schema_name,
        schema_version=revision.schema_version,
        payload=SampleRevisionPayload.model_validate(json.loads(revision.canonical_payload)),
        payload_sha256=revision.payload_sha256,
        created_at=revision.created_at,
        created_by=revision.created_by,
        reopen_destination=SampleRevisionReopenDestination(
            surface="molbio-ngs-sample-revision",
            params=SampleRevisionReopenParams(
                global_domain_experiment_id=revision.global_domain_experiment_id,
                sample_id=revision.sample_id,
                revision_id=revision.id,
            ),
        ),
    )


def _reference_response(reference: MolBioNGSReferenceResource) -> ReferenceResponse:
    return ReferenceResponse(
        id=reference.id,
        global_domain_experiment_id=reference.global_domain_experiment_id,
        name=reference.name,
        current_revision_id=reference.current_revision_id,
        head_generation=reference.head_generation,
        archived_at=reference.archived_at,
        created_at=reference.created_at,
        updated_at=reference.updated_at,
        reopen_destination=ReferenceReopenDestination(
            surface="molbio-ngs-reference",
            params=ReferenceReopenParams(reference_id=reference.id),
        ),
    )


def _reference_revision_response(
    revision: MolBioNGSReferenceRevision,
) -> ReferenceRevisionResponse:
    return ReferenceRevisionResponse(
        id=revision.id,
        reference_id=revision.reference_id,
        global_domain_experiment_id=revision.global_domain_experiment_id,
        revision_number=revision.revision_number,
        parent_revision_id=revision.parent_revision_id,
        schema_name=revision.schema_name,
        schema_version=revision.schema_version,
        payload=ReferenceRevisionPayload.model_validate(json.loads(revision.canonical_payload)),
        payload_sha256=revision.payload_sha256,
        canonical_fasta_sha256=revision.canonical_fasta_sha256,
        canonical_fasta_size_bytes=revision.canonical_fasta_size_bytes,
        contig_manifest_sha256=revision.contig_manifest_sha256,
        normalized_sequence_sha256=revision.normalized_sequence_sha256,
        molecule_type=revision.molecule_type,
        topology=revision.topology,
        coordinate_contract=revision.coordinate_contract,
        created_at=revision.created_at,
        created_by=revision.created_by,
        reopen_destination=ReferenceRevisionReopenDestination(
            surface="molbio-ngs-reference-revision",
            params=ReferenceRevisionReopenParams(
                reference_id=revision.reference_id,
                revision_id=revision.id,
            ),
        ),
    )


def _evidence_response(
    assessment: MolBioNGSEvidenceAssessment,
) -> EvidenceAssessmentResponse:
    return EvidenceAssessmentResponse(
        evidence_id=assessment.evidence_id,
        global_domain_experiment_id=assessment.global_domain_experiment_id,
        state_revision_id=assessment.state_revision_id,
        sample_revision_id=assessment.sample_revision_id,
        receipt_ids=EvidenceReceiptIdsResponse(
            ngs_job=assessment.ngs_job_receipt_id,
            ngs_result_manifest=assessment.ngs_result_manifest_receipt_id,
            ngs_reference_revision=assessment.ngs_reference_revision_receipt_id,
            ont_instrument_run=assessment.ont_instrument_run_receipt_id,
            molecular_revision=assessment.molecular_revision_receipt_id,
            ngs_comparison_panel=assessment.ngs_comparison_panel_receipt_id,
        ),
        assessment_rule_id=assessment.assessment_rule_id,
        scientific_assessment=assessment.scientific_assessment,
        job_lifecycle_state=assessment.job_lifecycle_state,
        manifest_integrity=assessment.manifest_integrity,
        raw_manifest_sha256=assessment.raw_manifest_sha256,
        notes=assessment.notes,
        wrapper_sha256=assessment.wrapper_sha256,
        created_at=assessment.created_at,
        created_by=assessment.created_by,
        reopen_destination=EvidenceReopenDestination(
            surface="molbio-ngs-evidence-assessment",
            params=EvidenceReopenParams(
                global_domain_experiment_id=assessment.global_domain_experiment_id,
                evidence_id=assessment.evidence_id,
            ),
        ),
    )


async def _revision_response(
    session: AsyncSession,
    revision: MolBioNGSDomainStateRevision,
) -> StateRevisionResponse:
    payload, membership_graph = await verify_state_revision_integrity(session, revision)
    return StateRevisionResponse(
        id=revision.id,
        global_domain_experiment_id=revision.global_domain_experiment_id,
        global_domain_experiment_revision_id=revision.global_domain_experiment_revision_id,
        revision_number=revision.revision_number,
        parent_revision_id=revision.parent_revision_id,
        schema_name=revision.schema_name,
        schema_version=revision.schema_version,
        payload=StateRevisionPayload.model_validate(payload),
        payload_sha256=revision.payload_sha256,
        membership_graph_sha256=revision.membership_graph_sha256,
        members=[
            StateMemberResponse(
                receipt_id=str(member["receipt_id"]),
                role=member["role"],
                ordinal=int(member["ordinal"]),
                sample_revision_id=(
                    str(member["sample_revision_id"])
                    if member["sample_revision_id"] is not None
                    else None
                ),
                source_store_id=str(member["source_store_id"]),
                entity_kind=str(member["entity_kind"]),
                entity_id=str(member["entity_id"]),
                source_generation_or_revision=str(
                    member["source_generation_or_revision"]
                ),
                content_digest=str(member["content_digest"]),
                source_schema=str(member["source_schema"]),
                availability=str(member["availability"]),
                reopen_destination=member["reopen_destination"],
                receipt_sha256=str(member["receipt_sha256"]),
            )
            for member in membership_graph
        ],
        created_at=revision.created_at,
        created_by=revision.created_by,
    )


def _service_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DomainStateNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, GlobalAdapterUnavailable):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    if isinstance(exc, StateIntegrityError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, OSError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(
        exc,
        (DomainStateAlreadyExists, GlobalBindingError, IdempotencyConflict, RevisionConflict),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, StateValidationError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


async def _persist_resolved_member_receipt(
    session: AsyncSession,
    receipt: ExternalMemberReceipt,
) -> dict:
    try:
        row = await persist_member_receipt(session, receipt)
        await session.commit()
        return serialize_external_member_receipt(row)
    except Exception:
        await session.rollback()
        raise


def _member_receipt_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (KeyError, DomainStateNotFound)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, (ValueError, OSError, StateIntegrityError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post(
    "/experiments/{global_domain_experiment_id}/samples",
    response_model=SampleRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_molbio_ngs_sample(
    global_domain_experiment_id: str,
    request: CreateSampleRequest,
    session: DomainSession,
) -> SampleRevisionResponse:
    try:
        _sample, revision = await create_sample(
            session,
            global_domain_experiment_id=global_domain_experiment_id,
            payload=request.payload.model_dump(mode="json", by_alias=True),
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return _sample_revision_response(revision)
    except (
        DomainStateNotFound,
        IdempotencyConflict,
        RevisionConflict,
        StateIntegrityError,
        StateValidationError,
    ) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{global_domain_experiment_id}/samples",
    response_model=list[SampleResponse],
)
async def list_molbio_ngs_samples(
    global_domain_experiment_id: str,
    session: DomainSession,
) -> list[SampleResponse]:
    return [
        _sample_response(sample)
        for sample in await list_samples(session, global_domain_experiment_id)
    ]


@router.get(
    "/experiments/{global_domain_experiment_id}/samples/{sample_id}",
    response_model=SampleResponse,
)
async def read_molbio_ngs_sample(
    global_domain_experiment_id: str,
    sample_id: str,
    session: DomainSession,
) -> SampleResponse:
    try:
        return _sample_response(
            await get_sample(session, global_domain_experiment_id, sample_id)
        )
    except DomainStateNotFound as exc:
        raise _service_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/samples/{sample_id}/revisions",
    response_model=SampleRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_molbio_ngs_sample_revision(
    global_domain_experiment_id: str,
    sample_id: str,
    request: CreateSampleRevisionRequest,
    session: DomainSession,
) -> SampleRevisionResponse:
    try:
        revision = await append_sample_revision(
            session,
            global_domain_experiment_id=global_domain_experiment_id,
            sample_id=sample_id,
            payload=request.payload.model_dump(mode="json", by_alias=True),
            expected_head_generation=request.expected_head_generation,
            parent_revision_id=request.parent_revision_id,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return _sample_revision_response(revision)
    except (
        DomainStateNotFound,
        IdempotencyConflict,
        RevisionConflict,
        StateIntegrityError,
        StateValidationError,
    ) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{global_domain_experiment_id}/samples/{sample_id}/revisions",
    response_model=list[SampleRevisionResponse],
)
async def read_molbio_ngs_sample_revisions(
    global_domain_experiment_id: str,
    sample_id: str,
    session: DomainSession,
) -> list[SampleRevisionResponse]:
    try:
        return [
            _sample_revision_response(revision)
            for revision in await list_sample_revisions(
                session, global_domain_experiment_id, sample_id
            )
        ]
    except (DomainStateNotFound, StateIntegrityError) as exc:
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{global_domain_experiment_id}/samples/{sample_id}/revisions/{revision_id}",
    response_model=SampleRevisionResponse,
)
async def read_molbio_ngs_sample_revision(
    global_domain_experiment_id: str,
    sample_id: str,
    revision_id: str,
    session: DomainSession,
) -> SampleRevisionResponse:
    try:
        return _sample_revision_response(
            await get_sample_revision(
                session, global_domain_experiment_id, sample_id, revision_id
            )
        )
    except (DomainStateNotFound, StateIntegrityError) as exc:
        raise _service_http_error(exc) from exc


@router.post(
    "/references",
    response_model=ReferenceRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_molbio_ngs_reference(
    request: CreateReferenceRequest,
    session: DomainSession,
) -> ReferenceRevisionResponse:
    try:
        _reference, revision = await create_reference(
            session,
            global_domain_experiment_id=request.global_domain_experiment_id,
            name=request.name,
            raw_fasta=request.fasta.encode("utf-8"),
            molecule_type=request.molecule_type,
            topology=request.topology,
            coordinate_contract=request.coordinate_contract,
            source_provenance=request.source_provenance,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return _reference_revision_response(revision)
    except (
        DomainStateNotFound,
        IdempotencyConflict,
        RevisionConflict,
        StateIntegrityError,
        StateValidationError,
        OSError,
    ) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.get("/references", response_model=list[ReferenceResponse])
async def list_molbio_ngs_references(
    global_domain_experiment_id: str,
    session: DomainSession,
) -> list[ReferenceResponse]:
    return [
        _reference_response(reference)
        for reference in await list_references(session, global_domain_experiment_id)
    ]


@router.get("/references/{reference_id}", response_model=ReferenceResponse)
async def read_molbio_ngs_reference(
    reference_id: str,
    session: DomainSession,
) -> ReferenceResponse:
    try:
        return _reference_response(await get_reference_resource(session, reference_id))
    except DomainStateNotFound as exc:
        raise _service_http_error(exc) from exc


@router.post(
    "/references/{reference_id}/revisions",
    response_model=ReferenceRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_molbio_ngs_reference_revision(
    reference_id: str,
    request: CreateReferenceRevisionRequest,
    session: DomainSession,
) -> ReferenceRevisionResponse:
    try:
        revision = await append_reference_revision(
            session,
            reference_id=reference_id,
            raw_fasta=request.fasta.encode("utf-8"),
            molecule_type=request.molecule_type,
            topology=request.topology,
            coordinate_contract=request.coordinate_contract,
            source_provenance=request.source_provenance,
            expected_head_generation=request.expected_head_generation,
            parent_revision_id=request.parent_revision_id,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return _reference_revision_response(revision)
    except (
        DomainStateNotFound,
        IdempotencyConflict,
        RevisionConflict,
        StateIntegrityError,
        StateValidationError,
        OSError,
    ) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.get(
    "/references/{reference_id}/revisions",
    response_model=list[ReferenceRevisionResponse],
)
async def read_molbio_ngs_reference_revisions(
    reference_id: str,
    session: DomainSession,
) -> list[ReferenceRevisionResponse]:
    try:
        return [
            _reference_revision_response(revision)
            for revision in await list_reference_revisions(session, reference_id)
        ]
    except (DomainStateNotFound, StateIntegrityError) as exc:
        raise _service_http_error(exc) from exc


@router.get(
    "/references/{reference_id}/revisions/{revision_id}",
    response_model=ReferenceRevisionResponse,
)
async def read_molbio_ngs_reference_revision(
    reference_id: str,
    revision_id: str,
    session: DomainSession,
) -> ReferenceRevisionResponse:
    try:
        return _reference_revision_response(
            await get_reference_revision(session, reference_id, revision_id)
        )
    except (DomainStateNotFound, StateIntegrityError) as exc:
        raise _service_http_error(exc) from exc


@router.post(
    "/references/{reference_id}/archive",
    response_model=ReferenceResponse,
)
async def archive_molbio_ngs_reference(
    reference_id: str,
    request: ArchiveReferenceRequest,
    session: DomainSession,
) -> ReferenceResponse:
    try:
        reference = await archive_reference(
            session,
            reference_id=reference_id,
            expected_head_generation=request.expected_head_generation,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return _reference_response(reference)
    except (
        DomainStateNotFound,
        IdempotencyConflict,
        RevisionConflict,
        StateIntegrityError,
        StateValidationError,
    ) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.post(
    "/references/from-molbio-revision",
    response_model=ReferenceRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_molbio_ngs_reference_from_molbio_revision(
    request: MolBioReferenceImportRequest,
    session: DomainSession,
    molbio_session: MolBioSession,
) -> ReferenceRevisionResponse:
    try:
        _reference, revision = await create_reference_from_molbio_revision(
            session,
            molbio_session,
            global_domain_experiment_id=request.global_domain_experiment_id,
            sequence_id=request.sequence_id,
            molecular_revision_id=request.molecular_revision_id,
            name=request.name,
            molecule_type=request.molecule_type,
            topology=request.topology,
            coordinate_contract=request.coordinate_contract,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return _reference_revision_response(revision)
    except (
        DomainStateNotFound,
        IdempotencyConflict,
        RevisionConflict,
        StateIntegrityError,
        StateValidationError,
        OSError,
    ) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.post(
    "/references/import-browser-entry",
    response_model=ReferenceRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_molbio_ngs_browser_reference(
    request: LegacyBrowserReferenceImportRequest,
    session: DomainSession,
) -> ReferenceRevisionResponse:
    try:
        _reference, revision = await import_browser_entry(
            session,
            global_domain_experiment_id=request.global_domain_experiment_id,
            entry=request.entry.model_dump(mode="json", by_alias=True),
            name=request.name,
            molecule_type=request.molecule_type,
            topology=request.topology,
            coordinate_contract=request.coordinate_contract,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return _reference_revision_response(revision)
    except (
        DomainStateNotFound,
        IdempotencyConflict,
        RevisionConflict,
        StateIntegrityError,
        StateValidationError,
        OSError,
    ) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/member-receipts/sample-revisions",
    response_model=ExternalMemberReceiptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_sample_revision_member_receipt(
    global_domain_experiment_id: str,
    request: SampleRevisionReceiptRequest,
    session: DomainSession,
) -> dict:
    try:
        receipt = await resolve_sample_revision_receipt(
            session,
            global_domain_experiment_id=global_domain_experiment_id,
            sample_id=request.sample_id,
            sample_revision_id=request.sample_revision_id,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, DomainStateNotFound, StateIntegrityError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/member-receipts/state-revisions",
    response_model=ExternalMemberReceiptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_state_revision_member_receipt(
    global_domain_experiment_id: str,
    request: StateRevisionReceiptRequest,
    session: DomainSession,
) -> dict:
    try:
        receipt = await resolve_state_revision_receipt(
            session,
            global_domain_experiment_id=global_domain_experiment_id,
            state_revision_id=request.state_revision_id,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, DomainStateNotFound, StateIntegrityError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/member-receipts/ngs-reference-revisions",
    status_code=status.HTTP_201_CREATED,
)
async def issue_ngs_reference_revision_member_receipt(
    global_domain_experiment_id: str,
    request: ReferenceRevisionReceiptRequest,
    session: DomainSession,
) -> dict:
    try:
        receipt = await resolve_ngs_reference_revision_receipt(
            session,
            global_domain_experiment_id=global_domain_experiment_id,
            reference_id=request.reference_id,
            revision_id=request.revision_id,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, OSError, DomainStateNotFound, StateIntegrityError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/member-receipts/molecular-revisions",
    status_code=status.HTTP_201_CREATED,
)
async def issue_molecular_revision_member_receipt(
    request: MolecularRevisionReceiptRequest,
    session: DomainSession,
    molbio_session: MolBioSession,
) -> dict:
    try:
        receipt = await resolve_molecular_revision_receipt(
            molbio_session,
            sequence_id=request.sequence_id,
            revision_id=request.revision_id,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, OSError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/member-receipts/primer-revisions",
    status_code=status.HTTP_201_CREATED,
)
async def issue_primer_revision_member_receipt(
    request: PrimerRevisionReceiptRequest,
    session: DomainSession,
    molbio_session: MolBioSession,
) -> dict:
    try:
        receipt = await resolve_primer_revision_receipt(
            molbio_session,
            primer_id=request.primer_id,
            revision_id=request.revision_id,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, OSError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/member-receipts/pcr-experiment-revisions",
    status_code=status.HTTP_201_CREATED,
)
async def issue_pcr_revision_member_receipt(
    request: PCRRevisionReceiptRequest,
    session: DomainSession,
    molbio_session: MolBioSession,
) -> dict:
    try:
        receipt = await resolve_pcr_experiment_revision_receipt(
            molbio_session,
            experiment_id=request.experiment_id,
            revision_id=request.revision_id,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, OSError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/member-receipts/molecular-operations",
    status_code=status.HTTP_201_CREATED,
)
async def issue_molecular_operation_member_receipt(
    request: MolecularOperationReceiptRequest,
    session: DomainSession,
    molbio_session: MolBioSession,
) -> dict:
    try:
        receipt = await resolve_molecular_operation_receipt(
            molbio_session,
            operation_id=request.operation_id,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, OSError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/member-receipts/ngs-comparison-panels",
    status_code=status.HTTP_201_CREATED,
)
async def issue_comparison_panel_member_receipt(
    request: ComparisonPanelReceiptRequest,
    session: DomainSession,
    core_session: CoreSession,
) -> dict:
    try:
        receipt = await resolve_approved_comparison_panel_receipt(
            core_session,
            panel_id=request.panel_id,
            panel_version=request.panel_version,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, OSError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/member-receipts/ngs-evidence-assessments",
    response_model=ExternalMemberReceiptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_evidence_assessment_member_receipt(
    global_domain_experiment_id: str,
    request: EvidenceAssessmentReceiptRequest,
    session: DomainSession,
) -> dict:
    try:
        receipt = await resolve_evidence_assessment_receipt(
            session,
            global_domain_experiment_id=global_domain_experiment_id,
            evidence_id=request.evidence_id,
        )
        return await _persist_resolved_member_receipt(session, receipt)
    except (KeyError, ValueError, OSError, DomainStateNotFound, StateIntegrityError) as exc:
        raise _member_receipt_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/evidence/attach-job",
    response_model=AttachJobEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def attach_molbio_ngs_job_evidence(
    global_domain_experiment_id: str,
    request: AttachJobEvidenceRequest,
    session: DomainSession,
    core_session: CoreSession,
) -> AttachJobEvidenceResponse:
    try:
        job_receipt, manifest_receipt = await attach_job_evidence(
            session,
            core_session,
            global_domain_experiment_id=global_domain_experiment_id,
            job_id=request.job_id,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return AttachJobEvidenceResponse(
            ngs_job=ExternalMemberReceiptResponse.model_validate(
                serialize_external_member_receipt(job_receipt)
            ),
            ngs_result_manifest=ExternalMemberReceiptResponse.model_validate(
                serialize_external_member_receipt(manifest_receipt)
            ),
        )
    except (KeyError, ValueError, OSError, DomainStateNotFound, IdempotencyConflict, StateIntegrityError, StateValidationError) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/evidence/attach-instrument-run",
    response_model=ExternalMemberReceiptResponse,
    status_code=status.HTTP_201_CREATED,
)
async def attach_molbio_ngs_instrument_run_evidence(
    global_domain_experiment_id: str,
    request: AttachInstrumentRunEvidenceRequest,
    session: DomainSession,
    core_session: CoreSession,
) -> dict:
    try:
        receipt = await attach_instrument_run_evidence(
            session,
            core_session,
            global_domain_experiment_id=global_domain_experiment_id,
            state_revision_id=request.state_revision_id,
            run_id=request.run_id,
            observed_generation=request.observed_generation,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return serialize_external_member_receipt(receipt)
    except (KeyError, ValueError, OSError, DomainStateNotFound, IdempotencyConflict, StateIntegrityError, StateValidationError) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/evidence/assess",
    response_model=EvidenceAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assess_molbio_ngs_evidence(
    global_domain_experiment_id: str,
    request: EvidenceAssessmentRequest,
    session: DomainSession,
    core_session: CoreSession,
    molbio_session: MolBioSession,
) -> EvidenceAssessmentResponse:
    try:
        assessment = await create_evidence_assessment(
            session,
            core_session,
            molbio_session,
            global_domain_experiment_id=global_domain_experiment_id,
            **request.model_dump(),
        )
        await session.commit()
        return _evidence_response(assessment)
    except (KeyError, ValueError, OSError, DomainStateNotFound, IdempotencyConflict, StateIntegrityError, StateValidationError) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{global_domain_experiment_id}/evidence",
    response_model=list[EvidenceAssessmentResponse],
)
async def list_molbio_ngs_evidence(
    global_domain_experiment_id: str,
    session: DomainSession,
) -> list[EvidenceAssessmentResponse]:
    try:
        return [
            _evidence_response(row)
            for row in await list_evidence_assessments(
                session, global_domain_experiment_id
            )
        ]
    except (DomainStateNotFound, StateIntegrityError) as exc:
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{global_domain_experiment_id}/evidence/{evidence_id}",
    response_model=EvidenceAssessmentResponse,
)
async def get_molbio_ngs_evidence(
    global_domain_experiment_id: str,
    evidence_id: str,
    session: DomainSession,
) -> EvidenceAssessmentResponse:
    try:
        return _evidence_response(
            await get_evidence_assessment(
                session, global_domain_experiment_id, evidence_id
            )
        )
    except (DomainStateNotFound, StateIntegrityError) as exc:
        raise _service_http_error(exc) from exc


@router.get("/experiments", response_model=list[DomainStateResponse])
async def list_molbio_ngs_experiments(session: DomainSession) -> list[DomainStateResponse]:
    return [_state_response(state) for state in await list_domain_states(session)]


@router.post(
    "/experiments/{global_domain_experiment_id}/state",
    response_model=DomainStateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def initialize_molbio_ngs_state(
    global_domain_experiment_id: str,
    request: InitializeStateRequest,
    session: DomainSession,
    global_session: GlobalSession,
) -> DomainStateResponse:
    try:
        binding = await verify_global_domain_binding(
            global_session,
            global_domain_experiment_id,
            request.global_domain_experiment_revision_id,
        )
        state = await initialize_domain_state(
            session,
            binding,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return _state_response(state)
    except (DomainStateAlreadyExists, GlobalBindingError, IdempotencyConflict, StateValidationError) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{global_domain_experiment_id}/state",
    response_model=DomainStateResponse,
)
async def read_molbio_ngs_state(
    global_domain_experiment_id: str,
    session: DomainSession,
) -> DomainStateResponse:
    try:
        return _state_response(await get_domain_state(session, global_domain_experiment_id))
    except DomainStateNotFound as exc:
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{global_domain_experiment_id}/state/revisions",
    response_model=list[StateRevisionResponse],
)
async def read_molbio_ngs_state_revisions(
    global_domain_experiment_id: str,
    session: DomainSession,
) -> list[StateRevisionResponse]:
    try:
        revisions = await list_state_revisions(session, global_domain_experiment_id)
        return [await _revision_response(session, revision) for revision in revisions]
    except StateIntegrityError as exc:
        raise _service_http_error(exc) from exc


@router.post(
    "/experiments/{global_domain_experiment_id}/state/revisions",
    response_model=StateRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_molbio_ngs_state_revision(
    global_domain_experiment_id: str,
    request: SaveStateRevisionRequest,
    session: DomainSession,
    core_session: CoreSession,
    global_session: GlobalSession,
) -> StateRevisionResponse:
    try:
        binding = await verify_global_domain_binding(
            global_session,
            global_domain_experiment_id,
            request.global_domain_experiment_revision_id,
        )
        await acknowledge_global_binding(session, binding)
        revision = await save_state_revision(
            session,
            core_session=core_session,
            global_domain_experiment_id=global_domain_experiment_id,
            global_domain_experiment_revision_id=request.global_domain_experiment_revision_id,
            payload=request.payload.model_dump(mode="json", by_alias=True),
            members=[StateMember(**member.model_dump()) for member in request.members],
            expected_head_generation=request.expected_head_generation,
            parent_revision_id=request.parent_revision_id,
            idempotency_key=request.idempotency_key,
        )
        await session.commit()
        return await _revision_response(session, revision)
    except (
        DomainStateNotFound,
        GlobalBindingError,
        IdempotencyConflict,
        RevisionConflict,
        StateValidationError,
    ) as exc:
        await session.rollback()
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{global_domain_experiment_id}/state/revisions/{revision_id}",
    response_model=StateRevisionResponse,
)
async def read_molbio_ngs_state_revision(
    global_domain_experiment_id: str,
    revision_id: str,
    session: DomainSession,
) -> StateRevisionResponse:
    try:
        revision = await get_state_revision(session, global_domain_experiment_id, revision_id)
        return await _revision_response(session, revision)
    except (DomainStateNotFound, StateIntegrityError) as exc:
        raise _service_http_error(exc) from exc


@router.get(
    "/experiments/{domain_experiment_id}",
    response_model=DomainExperimentViewResponse,
)
async def read_domain_experiment_view(
    domain_experiment_id: str,
    session: DomainSession,
) -> DomainExperimentViewResponse:
    try:
        payload = await get_domain_experiment_view(session, domain_experiment_id)
        return DomainExperimentViewResponse.model_validate(payload)
    except (DomainStateNotFound, GlobalBindingError) as exc:
        raise _service_http_error(exc) from exc


@router.get(
    "/projects/{project_id}/experiments",
    response_model=list[DomainExperimentViewResponse],
)
async def read_project_domain_experiments(
    project_id: str,
    session: DomainSession,
) -> list[DomainExperimentViewResponse]:
    try:
        payloads = await list_project_domain_experiments(session, project_id)
        return [DomainExperimentViewResponse.model_validate(payload) for payload in payloads]
    except (DomainStateNotFound, GlobalBindingError, StateValidationError) as exc:
        raise _service_http_error(exc) from exc


@router.get(
    "/projects/{project_id}/summary",
    response_model=ProjectDomainSummaryResponse,
)
async def read_project_domain_summary(
    project_id: str,
    session: DomainSession,
) -> ProjectDomainSummaryResponse:
    try:
        payload = await get_project_domain_summary(session, project_id)
        return ProjectDomainSummaryResponse.model_validate(payload)
    except (DomainStateNotFound, GlobalBindingError, StateValidationError) as exc:
        raise _service_http_error(exc) from exc
