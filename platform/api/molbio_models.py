"""Owned SQLAlchemy models for the canonical Mol Bio SQLite store.

The legacy-shaped ``nucleotide_sequences`` and ``primers`` tables are mutable
projections used by the existing API. Scientific history lives in append-only
revision and lineage tables in the same Mol Bio-owned database.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship


MolBioBase = declarative_base()


def _utcnow() -> datetime:
    return datetime.utcnow()


class MolBioSchemaMigration(MolBioBase):
    __tablename__ = "molbio_schema_migrations"

    version = Column(String(64), primary_key=True)
    description = Column(String(255), nullable=False)
    applied_at = Column(DateTime, nullable=False, default=_utcnow)


class NucleotideSequence(MolBioBase):
    """Current sequence projection; immutable history is MolecularRevision."""

    __tablename__ = "nucleotide_sequences"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    sequence = Column(Text, nullable=False)
    sequence_type = Column(String(10), nullable=False, default="dna")
    molecule_strandedness = Column(String(16), nullable=False, default="unknown")
    molecule_orientation = Column(String(24), nullable=False, default="unknown")
    is_circular = Column(Boolean, nullable=False, default=False)
    length = Column(Integer, nullable=False)
    features = Column(JSON, nullable=True)
    primers = Column(JSON, nullable=True)
    analysis_tracks = Column(JSON, nullable=True)
    organism = Column(String(255), nullable=True)
    accession = Column(String(100), nullable=True)
    source_file = Column(String(255), nullable=True)
    parent_id = Column(String(36), nullable=True)
    operation = Column(String(50), nullable=True)
    operation_params = Column(JSON, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    gc_content = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=_utcnow)


class Primer(MolBioBase):
    """Current primer projection; immutable history is PrimerRevision."""

    __tablename__ = "primers"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    sequence = Column(String(500), nullable=False)
    sequence_type = Column(String(10), nullable=False, default="dna")
    length = Column(Integer, nullable=False)
    tm = Column(Float, nullable=True)
    gc_percent = Column(Float, nullable=True)
    tm_algorithm = Column(String(100), nullable=True)
    tm_salt_correction = Column(String(100), nullable=True)
    tm_settings = Column(JSON, nullable=True)
    primer_type = Column(String(50), nullable=True, default="general")
    description = Column(Text, nullable=True)
    target_sequence_id = Column(
        String(36),
        ForeignKey("nucleotide_sequences.id", ondelete="RESTRICT"),
        nullable=True,
    )
    binding_start = Column(Integer, nullable=True)
    binding_end = Column(Integer, nullable=True)
    binding_strand = Column(Integer, nullable=True, default=1)
    tags = Column(JSON, nullable=True)
    is_favorite = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=_utcnow)
    deleted_at = Column(DateTime, nullable=True)

    target_sequence = relationship("NucleotideSequence", foreign_keys=[target_sequence_id])


class MolecularDocument(MolBioBase):
    """Stable identity and mutable head pointer for a molecular document."""

    __tablename__ = "molecular_documents"

    id = Column(String(36), primary_key=True)
    document_kind = Column(String(32), nullable=False)
    name = Column(String(255), nullable=False)
    current_revision_id = Column(
        String(36),
        ForeignKey("molecular_revisions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    deleted_at = Column(DateTime, nullable=True)


class MolecularRevision(MolBioBase):
    """Immutable complete snapshot of a molecular document revision."""

    __tablename__ = "molecular_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_number", name="uq_molecular_revision_number"),
        Index("ix_molecular_revisions_document_created", "document_id", "created_at"),
    )

    id = Column(String(36), primary_key=True)
    document_id = Column(
        String(36),
        ForeignKey("molecular_documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number = Column(Integer, nullable=False)
    change_kind = Column(String(32), nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    content_length = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    provenance = Column(JSON, nullable=False, default=dict)
    operation_id = Column(
        String(36),
        ForeignKey("molecular_operations.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    created_by = Column(String(255), nullable=True)


class PrimerRevision(MolBioBase):
    """Immutable primer snapshot including the Tm calculation provenance."""

    __tablename__ = "primer_revisions"
    __table_args__ = (
        UniqueConstraint("primer_id", "revision_number", name="uq_primer_revision_number"),
    )

    id = Column(String(36), primary_key=True)
    primer_id = Column(String(36), ForeignKey("primers.id", ondelete="RESTRICT"), nullable=False)
    revision_number = Column(Integer, nullable=False)
    change_kind = Column(String(32), nullable=False)
    sequence_sha256 = Column(String(64), nullable=False)
    snapshot = Column(JSON, nullable=False)
    provenance = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    created_by = Column(String(255), nullable=True)


class MolecularOperation(MolBioBase):
    """Append-only operation event whose input/output edges carry lineage."""

    __tablename__ = "molecular_operations"

    id = Column(String(36), primary_key=True)
    operation_kind = Column(String(64), nullable=False)
    implementation = Column(String(255), nullable=False)
    implementation_version = Column(String(128), nullable=True)
    status = Column(String(32), nullable=False, default="completed")
    parameters = Column(JSON, nullable=False, default=dict)
    warnings = Column(JSON, nullable=False, default=list)
    provenance = Column(JSON, nullable=False, default=dict)
    idempotency_key = Column(String(255), nullable=True, unique=True)
    request_fingerprint = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    created_by = Column(String(255), nullable=True)


class MolecularOperationInput(MolBioBase):
    __tablename__ = "molecular_operation_inputs"
    __table_args__ = (
        UniqueConstraint("operation_id", "position", name="uq_molecular_operation_input_position"),
    )

    id = Column(String(36), primary_key=True)
    operation_id = Column(
        String(36), ForeignKey("molecular_operations.id", ondelete="RESTRICT"), nullable=False
    )
    revision_id = Column(
        String(36), ForeignKey("molecular_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    role = Column(String(64), nullable=False)
    position = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False, default=dict)


class MolecularOperationOutput(MolBioBase):
    __tablename__ = "molecular_operation_outputs"
    __table_args__ = (
        UniqueConstraint("operation_id", "position", name="uq_molecular_operation_output_position"),
    )

    id = Column(String(36), primary_key=True)
    operation_id = Column(
        String(36), ForeignKey("molecular_operations.id", ondelete="RESTRICT"), nullable=False
    )
    revision_id = Column(
        String(36), ForeignKey("molecular_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    role = Column(String(64), nullable=False)
    position = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False, default=dict)


class TmModel(MolBioBase):
    __tablename__ = "tm_models"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    current_revision_id = Column(
        String(36), ForeignKey("tm_model_revisions.id", ondelete="RESTRICT", use_alter=True), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    deprecated_at = Column(DateTime, nullable=True)


class TmModelRevision(MolBioBase):
    __tablename__ = "tm_model_revisions"
    __table_args__ = (
        UniqueConstraint("model_id", "revision_number", name="uq_tm_model_revision_number"),
        UniqueConstraint("model_id", "implementation_version", name="uq_tm_model_implementation"),
    )

    id = Column(String(36), primary_key=True)
    model_id = Column(String(36), ForeignKey("tm_models.id", ondelete="RESTRICT"), nullable=False)
    revision_number = Column(Integer, nullable=False)
    implementation = Column(String(255), nullable=False)
    implementation_version = Column(String(128), nullable=False)
    parameter_schema = Column(JSON, nullable=False, default=dict)
    source = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    deprecated_at = Column(DateTime, nullable=True)


class PolymerasePreset(MolBioBase):
    __tablename__ = "polymerase_presets"

    id = Column(String(36), primary_key=True)
    vendor = Column(String(255), nullable=False)
    product_name = Column(String(255), nullable=False)
    catalog_number = Column(String(255), nullable=True)
    current_revision_id = Column(
        String(36),
        ForeignKey("polymerase_preset_revisions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    deprecated_at = Column(DateTime, nullable=True)


class PolymerasePresetRevision(MolBioBase):
    __tablename__ = "polymerase_preset_revisions"
    __table_args__ = (
        UniqueConstraint("preset_id", "revision_number", name="uq_polymerase_preset_revision_number"),
    )

    id = Column(String(36), primary_key=True)
    preset_id = Column(
        String(36), ForeignKey("polymerase_presets.id", ondelete="RESTRICT"), nullable=False
    )
    revision_number = Column(Integer, nullable=False)
    values = Column(JSON, nullable=False)
    source = Column(JSON, nullable=False)
    source_sha256 = Column(String(64), nullable=False)
    effective_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    deprecated_at = Column(DateTime, nullable=True)


class PCRExperiment(MolBioBase):
    """Stable experiment identity and review/head state."""

    __tablename__ = "pcr_experiments"

    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    current_revision_id = Column(
        String(36),
        ForeignKey("pcr_experiment_revisions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    review_state = Column(String(32), nullable=False, default="draft")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=_utcnow)


class PCRExperimentRevision(MolBioBase):
    """Immutable, scientifically reviewable PCR run snapshot."""

    __tablename__ = "pcr_experiment_revisions"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "revision_number",
            name="uq_molbio_pcr_experiment_revision_number",
        ),
        Index("ix_pcr_revision_template", "template_document_id", "created_at"),
    )

    id = Column(String(36), primary_key=True)
    experiment_id = Column(
        String(36), ForeignKey("pcr_experiments.id", ondelete="RESTRICT"), nullable=False
    )
    revision_number = Column(Integer, nullable=False)
    operation_id = Column(
        String(36), ForeignKey("molecular_operations.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key = Column(String(255), nullable=True, unique=True)
    request_fingerprint = Column(String(64), nullable=True)
    template_document_id = Column(
        String(36), ForeignKey("molecular_documents.id", ondelete="RESTRICT"), nullable=True
    )
    template_revision_id = Column(
        String(36), ForeignKey("molecular_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    template_sha256 = Column(String(64), nullable=False)
    template_snapshot = Column(JSON, nullable=False)
    forward_primer_snapshot = Column(JSON, nullable=False)
    reverse_primer_snapshot = Column(JSON, nullable=False)
    tm_model_revision_id = Column(
        String(36), ForeignKey("tm_model_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    tm_snapshot = Column(JSON, nullable=False)
    polymerase_preset_revision_id = Column(
        String(36), ForeignKey("polymerase_preset_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    polymerase_snapshot = Column(JSON, nullable=True)
    reaction_settings = Column(JSON, nullable=False, default=dict)
    cycling_assumptions = Column(JSON, nullable=False, default=dict)
    product_document_id = Column(
        String(36), ForeignKey("molecular_documents.id", ondelete="RESTRICT"), nullable=True
    )
    product_revision_id = Column(
        String(36), ForeignKey("molecular_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    product_snapshot = Column(JSON, nullable=False)
    warnings = Column(JSON, nullable=False, default=list)
    notes = Column(Text, nullable=True)
    review_state = Column(String(32), nullable=False, default="draft")
    provenance = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    created_by = Column(String(255), nullable=True)


class MolBioAuditEvent(MolBioBase):
    __tablename__ = "molbio_audit_events"

    id = Column(String(36), primary_key=True)
    entity_kind = Column(String(64), nullable=False)
    entity_id = Column(String(64), nullable=False)
    event_kind = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    actor = Column(String(255), nullable=True)
    occurred_at = Column(DateTime, nullable=False, default=_utcnow)


class MolBioOutboxEvent(MolBioBase):
    """Immutable event body; consumers use event ID as their idempotency key."""

    __tablename__ = "molbio_outbox_events"

    id = Column(String(36), primary_key=True)
    aggregate_kind = Column(String(64), nullable=False)
    aggregate_id = Column(String(64), nullable=False)
    event_kind = Column(String(128), nullable=False)
    payload = Column(JSON, nullable=False)
    occurred_at = Column(DateTime, nullable=False, default=_utcnow)


IMMUTABLE_TABLES = (
    "molecular_revisions",
    "primer_revisions",
    "molecular_operations",
    "molecular_operation_inputs",
    "molecular_operation_outputs",
    "tm_model_revisions",
    "polymerase_preset_revisions",
    "pcr_experiment_revisions",
    "molbio_audit_events",
    "molbio_outbox_events",
)
