"""SQLAlchemy models for the dedicated MolBio/NGS scientific-state store."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base


MolBioNGSBase = declarative_base()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class MolBioNGSDomainState(MolBioNGSBase):
    __tablename__ = "molbio_ngs_domain_states"

    global_domain_experiment_id = Column(String(128), primary_key=True)
    current_state_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_state_revisions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    current_binding_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_global_binding_revisions.binding_revision_id", ondelete="RESTRICT", use_alter=True),
        nullable=False,
    )
    head_generation = Column(Integer, nullable=False, default=0)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class MolBioNGSGlobalBinding(MolBioNGSBase):
    __tablename__ = "molbio_ngs_global_binding_revisions"
    __table_args__ = (
        UniqueConstraint("global_domain_experiment_id", "revision_number"),
    )

    binding_revision_id = Column(String(128), primary_key=True)
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number = Column(Integer, nullable=False)
    supersedes_binding_revision_id = Column(
        String(128), ForeignKey("molbio_ngs_global_binding_revisions.binding_revision_id"), nullable=True
    )
    global_domain_experiment_revision_id = Column(String(128), nullable=False)
    global_domain_experiment_revision_digest = Column(String(64), nullable=False)
    project_id = Column(String(128), nullable=False)
    project_generation = Column(String(128), nullable=False)
    project_digest = Column(String(64), nullable=False)
    project_receipt_id = Column(String(128), nullable=False)
    project_reopen_destination = Column(Text, nullable=False)
    project_acknowledgement = Column(Text, nullable=False, default="{}")
    global_experiment_id = Column(String(128), nullable=False)
    global_experiment_generation = Column(String(128), nullable=False)
    global_experiment_digest = Column(String(64), nullable=False)
    global_experiment_receipt_id = Column(String(128), nullable=False)
    global_experiment_reopen_destination = Column(Text, nullable=False)
    global_experiment_acknowledgement = Column(Text, nullable=False, default="{}")
    global_binding_receipt_id = Column(String(128), nullable=True)
    global_binding_receipt_json = Column(Text, nullable=True)
    global_binding_receipt_sha256 = Column(String(64), nullable=True)
    connector_command_id = Column(String(128), nullable=True, unique=True)
    binding_state = Column(String(32), nullable=False, default="needs_reverification")
    last_verified_at = Column(String(64), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=True)


class MolBioNGSMemberReceipt(MolBioNGSBase):
    """Immutable server-issued authority for one external scientific member."""

    __tablename__ = "molbio_ngs_member_receipts"

    receipt_id = Column(String(128), primary_key=True)
    source_store_id = Column(String(64), nullable=False)
    entity_kind = Column(String(64), nullable=False)
    entity_id = Column(String(255), nullable=False)
    source_generation_or_revision = Column(String(255), nullable=False)
    content_digest = Column(String(64), nullable=False)
    schema_name = Column(String(255), nullable=False)
    schema_version = Column(String(64), nullable=False)
    availability = Column(String(32), nullable=False)
    reopen_destination = Column(Text, nullable=False)
    canonical_receipt = Column(Text, nullable=False)
    receipt_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class MolBioNGSDomainStateRevision(MolBioNGSBase):
    __tablename__ = "molbio_ngs_domain_state_revisions"
    __table_args__ = (
        UniqueConstraint("global_domain_experiment_id", "revision_number"),
        UniqueConstraint(
            "global_domain_experiment_id", "payload_sha256", "membership_graph_sha256"
        ),
        Index(
            "uq_molbio_ngs_state_revision_domain_identity",
            "global_domain_experiment_id",
            "id",
            unique=True,
        ),
    )

    id = Column(String(128), primary_key=True)
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    global_domain_experiment_revision_id = Column(String(128), nullable=False)
    binding_revision_id = Column(
        String(128), ForeignKey("molbio_ngs_global_binding_revisions.binding_revision_id"), nullable=False
    )
    revision_number = Column(Integer, nullable=False)
    parent_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_state_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    schema_name = Column(String(255), nullable=False)
    schema_version = Column(String(64), nullable=False)
    canonical_payload = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    membership_graph_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    created_by = Column(String(255), nullable=True)


class MolBioNGSDomainStateMember(MolBioNGSBase):
    __tablename__ = "molbio_ngs_domain_state_members"

    state_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_state_revisions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    receipt_id = Column(
        String(128),
        ForeignKey("molbio_ngs_member_receipts.receipt_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    role = Column(String(128), primary_key=True)
    ordinal = Column(Integer, nullable=False)
    sample_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_sample_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at = Column(String(64), nullable=False, default=_timestamp)


class MolBioNGSSample(MolBioNGSBase):
    __tablename__ = "molbio_ngs_samples"
    __table_args__ = (UniqueConstraint("global_domain_experiment_id", "id"),)

    id = Column(String(128), primary_key=True)
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_sample_revisions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    head_generation = Column(Integer, nullable=False, default=0)
    archived_at = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class MolBioNGSSampleRevision(MolBioNGSBase):
    __tablename__ = "molbio_ngs_sample_revisions"
    __table_args__ = (
        UniqueConstraint("sample_id", "revision_number"),
        UniqueConstraint("sample_id", "payload_sha256"),
        Index(
            "uq_molbio_ngs_sample_revision_domain_identity",
            "global_domain_experiment_id",
            "id",
            unique=True,
        ),
    )

    id = Column(String(128), primary_key=True)
    sample_id = Column(
        String(128), ForeignKey("molbio_ngs_samples.id", ondelete="RESTRICT"), nullable=False
    )
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number = Column(Integer, nullable=False)
    parent_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_sample_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    schema_name = Column(String(255), nullable=False)
    schema_version = Column(String(64), nullable=False)
    canonical_payload = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    created_by = Column(String(255), nullable=True)


class MolBioNGSReferenceResource(MolBioNGSBase):
    __tablename__ = "molbio_ngs_reference_resources"
    __table_args__ = (UniqueConstraint("global_domain_experiment_id", "id"),)

    id = Column(String(128), primary_key=True)
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    name = Column(String(255), nullable=False)
    current_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_reference_revisions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    head_generation = Column(Integer, nullable=False, default=0)
    archived_at = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class MolBioNGSReferenceArtifact(MolBioNGSBase):
    __tablename__ = "molbio_ngs_reference_artifacts"

    id = Column(String(128), primary_key=True)
    reference_id = Column(
        String(128),
        ForeignKey("molbio_ngs_reference_resources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    managed_relative_path = Column(Text, nullable=False, unique=True)
    media_type = Column(String(255), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)


class MolBioNGSReferenceRevision(MolBioNGSBase):
    __tablename__ = "molbio_ngs_reference_revisions"
    __table_args__ = (
        UniqueConstraint("reference_id", "revision_number"),
        UniqueConstraint("reference_id", "payload_sha256"),
    )

    id = Column(String(128), primary_key=True)
    reference_id = Column(
        String(128),
        ForeignKey("molbio_ngs_reference_resources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number = Column(Integer, nullable=False)
    parent_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_reference_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    artifact_id = Column(
        String(128),
        ForeignKey("molbio_ngs_reference_artifacts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schema_name = Column(String(255), nullable=False)
    schema_version = Column(String(64), nullable=False)
    canonical_payload = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    canonical_fasta_sha256 = Column(String(64), nullable=False)
    canonical_fasta_size_bytes = Column(Integer, nullable=False)
    contig_manifest_sha256 = Column(String(64), nullable=False)
    normalized_sequence_sha256 = Column(String(64), nullable=True)
    molecule_type = Column(String(16), nullable=False)
    topology = Column(String(32), nullable=False)
    coordinate_contract = Column(String(128), nullable=False)
    source_provenance = Column(Text, nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    created_by = Column(String(255), nullable=True)


class MolBioNGSEvidenceAssessment(MolBioNGSBase):
    """One immutable, digest-bound NGS scientific evidence assessment."""

    __tablename__ = "molbio_ngs_evidence_assessments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["global_domain_experiment_id", "state_revision_id"],
            [
                "molbio_ngs_domain_state_revisions.global_domain_experiment_id",
                "molbio_ngs_domain_state_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["global_domain_experiment_id", "sample_revision_id"],
            [
                "molbio_ngs_sample_revisions.global_domain_experiment_id",
                "molbio_ngs_sample_revisions.id",
            ],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_molbio_ngs_evidence_domain_created",
            "global_domain_experiment_id",
            "created_at",
            "evidence_id",
        ),
        Index("ix_molbio_ngs_evidence_state_revision", "state_revision_id"),
        Index("ix_molbio_ngs_evidence_sample_revision", "sample_revision_id"),
    )

    evidence_id = Column(String(128), primary_key=True)
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    state_revision_id = Column(String(128), nullable=False)
    sample_revision_id = Column(String(128), nullable=True)
    ngs_job_receipt_id = Column(
        String(128),
        ForeignKey("molbio_ngs_member_receipts.receipt_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ngs_result_manifest_receipt_id = Column(
        String(128),
        ForeignKey("molbio_ngs_member_receipts.receipt_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ngs_reference_revision_receipt_id = Column(
        String(128),
        ForeignKey("molbio_ngs_member_receipts.receipt_id", ondelete="RESTRICT"),
        nullable=False,
    )
    ont_instrument_run_receipt_id = Column(
        String(128),
        ForeignKey("molbio_ngs_member_receipts.receipt_id", ondelete="RESTRICT"),
        nullable=True,
    )
    molecular_revision_receipt_id = Column(
        String(128),
        ForeignKey("molbio_ngs_member_receipts.receipt_id", ondelete="RESTRICT"),
        nullable=True,
    )
    ngs_comparison_panel_receipt_id = Column(
        String(128),
        ForeignKey("molbio_ngs_member_receipts.receipt_id", ondelete="RESTRICT"),
        nullable=True,
    )
    assessment_rule_id = Column(String(128), nullable=False)
    requested_assessment = Column(String(16), nullable=False)
    scientific_assessment = Column(String(16), nullable=False)
    job_lifecycle_state = Column(String(16), nullable=False)
    manifest_integrity = Column(String(16), nullable=False)
    raw_manifest_sha256 = Column(String(64), nullable=False)
    notes = Column(Text, nullable=True)
    canonical_wrapper = Column(Text, nullable=False)
    wrapper_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    created_by = Column(String(255), nullable=True)


class MolBioNGSIdempotencyClaim(MolBioNGSBase):
    __tablename__ = "molbio_ngs_idempotency_claims"

    scope = Column(String(128), primary_key=True)
    idempotency_key = Column(String(255), primary_key=True)
    status = Column(String(32), nullable=False, default="pending")
    request_sha256 = Column(String(64), nullable=False)
    result_resource_id = Column(String(128), nullable=False)
    response_json = Column(Text, nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    completed_at = Column(String(64), nullable=True)


class MolBioNGSAuditEvent(MolBioNGSBase):
    __tablename__ = "molbio_ngs_audit_events"

    id = Column(String(128), primary_key=True)
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    resource_id = Column(String(128), nullable=False)
    event_type = Column(String(128), nullable=False)
    generation = Column(Integer, nullable=False)
    payload_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    created_by = Column(String(255), nullable=True)


class MolBioNGSOutboxEvent(MolBioNGSBase):
    __tablename__ = "molbio_ngs_outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "global_domain_experiment_id", "binding_revision_id", "event_stream", "stream_generation"
        ),
    )

    id = Column(String(128), primary_key=True)
    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        nullable=False,
    )
    state_revision_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_state_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    binding_revision_id = Column(
        String(128), ForeignKey("molbio_ngs_global_binding_revisions.binding_revision_id"), nullable=False
    )
    event_type = Column(String(128), nullable=False)
    event_stream = Column(String(512), nullable=False)
    stream_generation = Column(Integer, nullable=False)
    source_generation = Column(Integer, nullable=True)
    payload_json = Column(Text, nullable=False)
    payload_sha256 = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    lease_owner = Column(String(255), nullable=True)
    lease_token = Column(String(128), nullable=True)
    lease_expires_at = Column(String(64), nullable=True)
    next_retry_at = Column(String(64), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    acknowledgement_json = Column(Text, nullable=True)
    acknowledgement_sha256 = Column(String(64), nullable=True)
    conflict_json = Column(Text, nullable=True)
    conflict_sha256 = Column(String(64), nullable=True)
    created_at = Column(String(64), nullable=False, default=_timestamp)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class MolBioNGSOutboxStream(MolBioNGSBase):
    __tablename__ = "molbio_ngs_outbox_streams"

    global_domain_experiment_id = Column(
        String(128),
        ForeignKey("molbio_ngs_domain_states.global_domain_experiment_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    binding_revision_id = Column(
        String(128), ForeignKey("molbio_ngs_global_binding_revisions.binding_revision_id"), primary_key=True
    )
    event_stream = Column(String(512), primary_key=True)
    next_stream_generation = Column(Integer, nullable=False, default=1)
    updated_at = Column(String(64), nullable=False, default=_timestamp)


class MolBioNGSConnectorAcknowledgement(MolBioNGSBase):
    __tablename__ = "molbio_ngs_connector_acknowledgements"

    acknowledgement_id = Column(String(128), primary_key=True)
    command_id = Column(String(128), nullable=False, unique=True)
    binding_revision_id = Column(
        String(128), ForeignKey("molbio_ngs_global_binding_revisions.binding_revision_id"), nullable=False
    )
    disposition = Column(String(32), nullable=False)
    acknowledgement_json = Column(Text, nullable=False)
    acknowledgement_sha256 = Column(String(64), nullable=False)
    created_at = Column(String(64), nullable=False, default=_timestamp)
