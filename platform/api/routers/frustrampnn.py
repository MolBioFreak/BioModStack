"""
FrustraMPNN API Router - Energetic Frustration Analysis

Provides endpoint for running FrustraMPNN on PDB structures.
Returns per-residue frustration profiles for all amino acid mutations.
"""

import base64
import copy
import hashlib
import json
import logging
import os
import stat
import tempfile
from io import BytesIO
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image, UnidentifiedImageError

from database import (
    ConformationalMappingArtifact,
    ConformationalMappingRequest,
    FrustraMPNNArtifact,
    FrustraMPNNComparison,
    FrustraMPNNComparisonRow,
    FrustraMPNNExport,
    FrustraMPNNGuidancePlan,
    FrustraMPNNLandscapeRow,
    FrustraMPNNResult,
    FrustraMPNNStatisticsAnalysis,
    FrustraMPNNReview,
    FrustraMPNNReviewArtifact,
    Job,
    get_session,
)
from services.scientific_artifacts import resolve_json_value
from services.frustrampnn.analytics import multidimensional_points, parse_dataset_ids
from services.frustrampnn.comparison import (
    ComparisonCompatibilityError,
    ComparisonValidationError,
    compare_landscape_set,
    compare_landscapes,
    comparison_compatibility,
    comparison_set_compatibility,
)
from services.frustrampnn.derived import (
    DerivedPersistenceError,
    load_persisted_landscape,
    persist_comparison,
    persist_guidance_plan,
)
from services.frustrampnn.persistence import landscape_page as persisted_landscape_page
from services.frustrampnn.statistics_jobs import (
    FrustraMPNNStatisticsJobError,
    retry_statistics_child,
)
from services.frustrampnn.guidance import GuidanceValidationError, build_guidance_plan
from services.frustrampnn.contracts import (
    ContractValidationError,
    canonical_json_bytes,
    canonical_json_loads,
    canonical_sha256,
    load_schema,
    validate_schema,
)
from services.conformational_mapping.contracts import validate_schema as validate_cm_schema
from services.frustrampnn.configuration import (
    FrustraMPNNExecutionConfigurationV2,
    FrustraMPNNExecutionConfigurationV3,
    execution_configuration,
)
from services.frustrampnn.jobs import (
    FrustraMPNNChildError,
    child_receipt,
    create_child_job,
    create_reanalysis_child,
    design_selections,
    discard_uncommitted_child_artifacts,
    handoff_selection,
    upload_selection,
    workflow_selection,
)
from services.structure_dataset_fanout import (
    FANOUT_SCHEMA,
    StructureDatasetBatch,
    StructureDatasetFanoutError,
    StructureDatasetMember,
    fan_out_structure_dataset,
)
from services import stage_reporting
from services.frustrampnn.settings import (
    FrustraMPNNEffectiveSettings,
    FrustraMPNNRequestedSettings,
    RequestedSettingsPayloadError,
    SourceResolutionError,
    default_settings,
    inspect_structure_map,
    resolve_effective_settings,
    validate_complete_requested_settings,
)
from services.frustrampnn.structure import (
    StructureNormalizationError,
    inspect_and_normalize_structure_bytes,
    read_structure_bytes,
)
from routers.viewer_resources import _principal

router = APIRouter(prefix="/api/frustrampnn", tags=["frustrampnn"])
logger = logging.getLogger(__name__)
_MAX_MULTIPART_STRUCTURE_BYTES = 64 * 1024 * 1024
_MULTIPART_READ_CHUNK_BYTES = 1024 * 1024
_MAX_MULTIPART_METADATA_CHARS = 64 * 1024
_MAX_MULTIPART_IDENTITY_CHARS = 255

class DesignSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AnalyzeDesignsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: list[DesignSelectionRequest] = Field(min_length=1)
    frustrampnn_settings: FrustraMPNNRequestedSettings = Field(
        default_factory=default_settings
    )

    @field_validator("frustrampnn_settings", mode="before")
    @classmethod
    def _complete_settings(cls, value: Any) -> FrustraMPNNRequestedSettings:
        return validate_complete_requested_settings(value)


class ReanalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frustrampnn_settings: FrustraMPNNRequestedSettings | None = None

    @field_validator("frustrampnn_settings", mode="before")
    @classmethod
    def _complete_replacement(cls, value: Any) -> FrustraMPNNRequestedSettings | None:
        if value is None:
            return None
        return validate_complete_requested_settings(value)


class FrustraMPNNReviewResultReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_job_id: str = Field(min_length=1, max_length=36)
    invocation_id: str = Field(min_length=1, max_length=128)


class FrustraMPNNReviewResidue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_asym_id: str = Field(min_length=1, max_length=128)
    auth_seq_id: str = Field(min_length=1, max_length=64)
    insertion_code: str = Field(default="", max_length=16)


class FrustraMPNNReviewCamera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["perspective", "orthographic"]
    target: tuple[float, float, float] | None = None
    position: tuple[float, float, float] | None = None
    up: tuple[float, float, float] | None = None
    radius: float | None = Field(default=None, gt=0)


class FrustraMPNNReviewRepresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    representationId: str = Field(min_length=1, max_length=128)
    documentId: str = Field(min_length=1, max_length=128)
    kind: Literal["cartoon", "surface", "ball-and-stick", "spacefill", "line", "gaussian-surface"]
    visible: bool
    opacity: float = Field(ge=0, le=1)
    selectionSetId: str | None = Field(default=None, min_length=1, max_length=128)


class FrustraMPNNReviewLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layerId: str = Field(min_length=1, max_length=128)
    metricId: str | None = Field(default=None, min_length=1, max_length=128)
    selectionSetId: str | None = Field(default=None, min_length=1, max_length=128)
    visible: bool
    opacity: float = Field(ge=0, le=1)
    order: int = Field(ge=0, le=10000)
    palette: str | None = Field(default=None, min_length=1, max_length=128)


class FrustraMPNNReviewViewState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_metric_id: str = Field(max_length=128)
    landscape_offset: int = Field(ge=0)
    metric_workbench_open: bool
    chart_x_axis: str = Field(default="sequence_index", max_length=128)
    chart_y_axis: str = Field(default="score", max_length=128)
    structure_camera: FrustraMPNNReviewCamera | None = None
    structure_representations: list[FrustraMPNNReviewRepresentation] = Field(default_factory=list, max_length=100)
    structure_layers: list[FrustraMPNNReviewLayer] = Field(default_factory=list, max_length=100)


class FrustraMPNNSavedReviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    notes: str = Field(default="", max_length=20000)
    result_references: list[FrustraMPNNReviewResultReference] = Field(min_length=1, max_length=1)
    selected_residues: list[FrustraMPNNReviewResidue] = Field(default_factory=list, max_length=1000)
    filters: dict[str, JsonValue] = Field(default_factory=dict)
    viewer_state: FrustraMPNNReviewViewState
    tags: list[str] = Field(default_factory=list, max_length=50)
    supersedes_review_id: str | None = Field(default=None, max_length=36)

    @field_validator("filters")
    @classmethod
    def _bounded_scalar_state(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(value) > 100 or any(len(key) > 128 for key in value):
            raise ValueError("review state exceeds bounded key limits")
        if any(item is not None and not isinstance(item, (str, int, float, bool)) for item in value.values()):
            raise ValueError("review state values must be scalar")
        if len(canonical_json_bytes(value)) > 16 * 1024:
            raise ValueError("review state must contain at most 16 KiB")
        return value

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 64 for value in normalized):
            raise ValueError("tags must contain non-empty values of at most 64 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("tags must be unique")
        return normalized


class FrustraMPNNExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str = Field(min_length=1, max_length=128)
    review_id: str = Field(min_length=1, max_length=36)
    format: Literal["json", "csv"]
    limit: int = Field(default=10_000, ge=1, le=10_000)
    auth_asym_id: str | None = Field(default=None, max_length=128)
    mutation_aa: str | None = Field(default=None, pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    status: str | None = Field(default=None, max_length=32)


class FrustraMPNNGpuProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    physical_device_id: str
    task_visible_device_index: int | None = Field(default=None, ge=0)


class FrustraMPNNReceiptProducerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    producer_stage: str | None = None
    producer_id: str | None = None
    source_stage_family: str | None = None
    source_stage_mode: str | None = None
    artifact_class: str | None = None
    parent_job_id: str | None = None
    parent_invocation_id: str | None = None
    parent_landscape_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    guidance_id: str | None = None
    protein_sequence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FrustraMPNNChildCandidateReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection_ordinal: int | None = Field(default=None, ge=0)
    design_id: str | None = None
    source_job_id: str | None = None
    candidate_id: str | None = None
    invocation_id: str | None = None
    source_artifact_id: str | None = None
    source_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    component_request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_pdb_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    structure_map_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    settings_value_origin: Literal["bms_default", "operator_request"] | None = None
    requested_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effective_settings: FrustraMPNNEffectiveSettings | None = None
    effective_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    capability_inventory_byte_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    classification_policy_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    execution_configuration_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_identity_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    producer: FrustraMPNNReceiptProducerResponse | None = None


class FrustraMPNNChildResultReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    design_id: str | None = None
    source_artifact_id: str | None = None
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    invocation_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["succeeded", "failed", "not_run"] | None = None
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gpu_provenance: FrustraMPNNGpuProvenanceResponse | None = None


class FrustraMPNNBatchManifestReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: Literal["bms_frustrampnn_scheduler_batch"]
    schema_version: Literal[3]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    expected_cardinality: int = Field(ge=1)
    ordered_candidate_ids: list[str] = Field(min_length=1)
    ordered_invocation_ids: list[str] = Field(min_length=1)


class FrustraMPNNBatchTerminalRecordResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ordinal: int = Field(ge=0)
    candidate_id: str
    invocation_id: str
    pdb_stem: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: str
    terminal_at: str
    status: Literal["succeeded", "failed"]
    failure_code: str | None
    diagnostic: str | None = Field(default=None, max_length=1024)
    row_count: int | None = Field(default=None, ge=0)
    output_csv: str | None
    output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FrustraMPNNGroupedTerminalArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    records: list[FrustraMPNNBatchTerminalRecordResponse] = Field(min_length=1)


class FrustraMPNNChildReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    child_job_id: str
    result_job_id: str
    name: str
    parent_job_id: str | None = None
    source_parent_job_id: str | None = None
    trigger: str
    status: str
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    settings_value_origin: Literal["bms_default", "operator_request"]
    requested_settings: FrustraMPNNRequestedSettings
    requested_settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    batch_manifest: FrustraMPNNBatchManifestReceiptResponse
    grouped_terminal_artifact: FrustraMPNNGroupedTerminalArtifactResponse | None = None
    candidates: list[FrustraMPNNChildCandidateReceiptResponse]
    results: list[FrustraMPNNChildResultReceiptResponse]


class FrustraMPNNFanoutChildResponse(FrustraMPNNChildReceiptResponse):
    structure_count: int = Field(ge=1)


class FrustraMPNNStructureDatasetFanoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["bms.structure-dataset-fanout.v1"]
    fanout_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_job_id: str
    selected_structure_count: int = Field(ge=1)
    structures_per_job: int = Field(ge=1)
    effective_structures_per_job: int = Field(ge=1)
    replayed: bool
    child_jobs: list[FrustraMPNNFanoutChildResponse] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_canonical_partition(self):
        full_groups, remainder = divmod(
            self.selected_structure_count, self.effective_structures_per_job
        )
        expected = [self.effective_structures_per_job] * full_groups
        if remainder:
            expected.append(remainder)
        observed = [child.structure_count for child in self.child_jobs]
        if observed != expected:
            raise ValueError("child_jobs do not form the canonical partition")
        return self


class FrustraMPNNHandoffMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_candidate_id: str
    guidance_id: str | None = None
    producer_id: str


class FrustraMPNNHandoffResponse(FrustraMPNNChildReceiptResponse):
    handoff: FrustraMPNNHandoffMetadataResponse


class ComparisonTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_job_id: str = Field(min_length=1)
    reference_invocation_id: str = Field(min_length=1)
    target_job_id: str = Field(min_length=1)
    target_invocation_id: str = Field(min_length=1)


class ComparisonCreateRequest(ComparisonTargetRequest):
    allow_incompatible: bool = False


class MultiComparisonCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_job_id: str = Field(min_length=1)
    reference_invocation_id: str = Field(min_length=1)
    targets: list[ComparisonTargetRequest] = Field(min_length=1, max_length=8)
    allow_incompatible: bool = False

    @model_validator(mode="after")
    def validate_redundant_reference_and_target_identity(
        self,
    ) -> "MultiComparisonCreateRequest":
        reference = (self.reference_job_id, self.reference_invocation_id)
        target_identities: list[tuple[str, str]] = []
        for target in self.targets:
            if (
                target.reference_job_id,
                target.reference_invocation_id,
            ) != reference:
                raise ValueError(
                    "each target reference identity must exactly match the top-level reference"
                )
            identity = (target.target_job_id, target.target_invocation_id)
            if identity == reference:
                raise ValueError("a comparison target must not equal the reference")
            target_identities.append(identity)
        if len(target_identities) != len(set(target_identities)):
            raise ValueError("comparison targets must be unique")
        return self


Phase4Field = Literal[
    "settings_sha256",
    "effective_settings_sha256",
    "effective_settings_json",
    "capability_inventory_sha256",
    "statistics_sha256",
    "statistics_json",
    "comparison_compatibility_id",
]


class FrustraMPNNStatisticsDocument(RootModel[dict[str, JsonValue]]):
    """Exact historical-v1 or current-v2 persisted statistics authority."""

    @model_validator(mode="before")
    @classmethod
    def validate_statistics_schema(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            raise ValueError("FrustraMPNN statistics document must be an object")
        schema_version = value.get("schema_version")
        if schema_version == 1:
            schema_id = "frustrampnn_statistics_v1"
        elif schema_version == 2:
            schema_id = "frustrampnn_statistics_v2"
        else:
            raise ValueError("FrustraMPNN statistics schema generation is unsupported")
        validate_schema(schema_id, value)
        return value

    @classmethod
    def __get_pydantic_json_schema__(
        cls, _core_schema: Any, _handler: Any
    ) -> dict[str, Any]:
        return {
            "oneOf": [
                load_schema("frustrampnn_statistics_v1"),
                load_schema("frustrampnn_statistics_v2"),
            ]
        }


class FrustraMPNNSummaryV2Document(RootModel[dict[str, JsonValue]]):
    """Exact current result summary authority validated by its canonical schema."""

    @model_validator(mode="before")
    @classmethod
    def validate_summary_schema(cls, value: Any) -> Any:
        validate_schema("frustrampnn_summary_v2", value)
        return value

    @classmethod
    def __get_pydantic_json_schema__(
        cls, _core_schema: Any, _handler: Any
    ) -> dict[str, Any]:
        return load_schema("frustrampnn_summary_v2")


class FrustraMPNNSummaryV3Document(RootModel[dict[str, JsonValue]]):
    """Exact current core summary authority validated by its canonical schema."""

    @model_validator(mode="before")
    @classmethod
    def validate_summary_schema(cls, value: Any) -> Any:
        validate_schema("frustrampnn_summary_v3", value)
        return value

    @classmethod
    def __get_pydantic_json_schema__(
        cls, _core_schema: Any, _handler: Any
    ) -> dict[str, Any]:
        return load_schema("frustrampnn_summary_v3")


class FrustraMPNNHistoricalSummaryV1Document(RootModel[dict[str, JsonValue]]):
    """Exact historical summary authority retained for safe legacy reads."""

    @model_validator(mode="before")
    @classmethod
    def validate_summary_schema(cls, value: Any) -> Any:
        try:
            validate_schema("frustrampnn_summary_v1", value)
            return value
        except ContractValidationError:
            if not isinstance(value, Mapping):
                raise
            threshold_policy = value.get("threshold_policy")
            if not isinstance(threshold_policy, Mapping):
                raise
            if (
                threshold_policy.get("id") != "frustrampnn_threshold_v1"
                or threshold_policy.get("high_max") != -1.0
                or threshold_policy.get("minimal_min") != 0.58
            ):
                raise
            canonicalized_policy = {
                **dict(threshold_policy),
                "id": "frustrampnn_class_v1",
            }
            canonicalized = dict(value)
            canonicalized["threshold_policy"] = canonicalized_policy
            canonicalized["threshold_policy_sha256"] = canonical_sha256(canonicalized_policy)
            validate_schema("frustrampnn_summary_v1", canonicalized)
            return value

    @classmethod
    def __get_pydantic_json_schema__(
        cls, _core_schema: Any, _handler: Any
    ) -> dict[str, Any]:
        return load_schema("frustrampnn_summary_v1")


class FrustraMPNNStatisticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    parent_job_id: str
    candidate_id: str
    invocation_id: str
    authority_version: Literal["v3", "v2", "historical_v1"]
    availability: bool
    missing_fields: list[Phase4Field]
    settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effective_settings_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    effective_settings_json: FrustraMPNNEffectiveSettings | None = None
    capability_inventory_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    statistics_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    statistics_json: FrustraMPNNStatisticsDocument | None = None
    comparison_compatibility_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    statistics: FrustraMPNNStatisticsDocument | None


class StatisticsDatasetReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_job_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)


class StatisticsDenominator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1)
    count: int = Field(ge=0)


class StatisticsDistributionDenominators(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: StatisticsDenominator
    mean: StatisticsDenominator
    median: StatisticsDenominator
    sample_sd: StatisticsDenominator
    min: StatisticsDenominator
    max: StatisticsDenominator
    q1: StatisticsDenominator
    q3: StatisticsDenominator
    iqr: StatisticsDenominator


class StatisticsDistributionMissingness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: str | None
    mean: str | None
    median: str | None
    sample_sd: str | None
    min: str | None
    max: str | None
    q1: str | None
    q3: str | None
    iqr: str | None


class StatisticsDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0)
    mean: float | None
    median: float | None
    sample_sd: float | None
    min: float | None
    max: float | None
    q1: float | None
    q3: float | None
    iqr: float | None
    denominators: StatisticsDistributionDenominators
    missingness_reasons: StatisticsDistributionMissingness


class StatisticsClassCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high: int = Field(ge=0)
    neutral: int = Field(ge=0)
    minimal: int = Field(ge=0)


class StatisticsClassFractions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    high: float | None = Field(ge=0, le=1)
    neutral: float | None = Field(ge=0, le=1)
    minimal: float | None = Field(ge=0, le=1)


class StatisticsClassBurden(BaseModel):
    model_config = ConfigDict(extra="forbid")

    support_count: int = Field(ge=0)
    counts: StatisticsClassCounts
    fractions: StatisticsClassFractions
    denominator: StatisticsDenominator
    missingness_reason: str | None


class StatisticsFractionMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None = Field(ge=0, le=1)
    denominator: StatisticsDenominator
    missingness_reason: str | None


class StatisticsResidueFractions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: StatisticsFractionMetric
    observed: StatisticsFractionMetric
    scoreable: StatisticsFractionMetric
    excluded: StatisticsFractionMetric
    missing: StatisticsFractionMetric
    selected_missing: StatisticsFractionMetric


class StatisticsSlotFractions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed: StatisticsFractionMetric
    scoreable: StatisticsFractionMetric
    excluded: StatisticsFractionMetric
    missing: StatisticsFractionMetric
    selected_missing: StatisticsFractionMetric


class StatisticsReasonCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authority: Literal["structure_map_row", "excluded_record", "landscape"]
    status: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    count: int = Field(ge=1)


class StatisticsOverviewSupport(BaseModel):
    """Closed projection of persisted support; fields are optional for historical v2 fixtures."""

    model_config = ConfigDict(extra="forbid")

    source_residue_count: int | None = Field(default=None, ge=0)
    selected_residue_count: int | None = Field(default=None, ge=0)
    observed_residue_count: int | None = Field(default=None, ge=0)
    scoreable_residue_count: int | None = Field(default=None, ge=0)
    excluded_residue_count: int | None = Field(default=None, ge=0)
    missing_residue_count: int | None = Field(default=None, ge=0)
    mapping_missing_residue_count: int | None = Field(default=None, ge=0)
    selected_missing_residue_count: int | None = Field(default=None, ge=0)
    fully_scoreable_residue_count: int | None = Field(default=None, ge=0)
    partially_scoreable_residue_count: int | None = Field(default=None, ge=0)
    expected_slot_count: int | None = Field(default=None, ge=0)
    observed_slot_count: int | None = Field(default=None, ge=0)
    scoreable_slot_count: int | None = Field(default=None, ge=0)
    excluded_slot_count: int | None = Field(default=None, ge=0)
    mapping_missing_slot_count: int | None = Field(default=None, ge=0)
    missing_slot_count: int | None = Field(default=None, ge=0)
    residue_fractions: StatisticsResidueFractions | None = None
    slot_fractions: StatisticsSlotFractions | None = None
    exclusion_reasons: list[StatisticsReasonCount] | None = None
    missing_reasons: list[StatisticsReasonCount] | None = None


class StatisticsGroupSupport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_residue_count: int | None = Field(default=None, ge=0)
    observed_residue_count: int | None = Field(default=None, ge=0)
    fully_scoreable_residue_count: int | None = Field(default=None, ge=0)
    expected_slot_count: int | None = Field(default=None, ge=0)
    observed_slot_count: int | None = Field(default=None, ge=0)
    scoreable_slot_count: int | None = Field(default=None, ge=0)


class StatisticsOverviewFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatisticsResidueFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str | None = None
    source_entity_id: str | None = None
    label_asym_id: str | None = None
    auth_asym_id: str | None = None
    auth_seq_id: int | None = None
    insertion_code: str | None = Field(default=None, max_length=1)
    sequence_index: int | None = Field(default=None, ge=1)
    wt: str | None = Field(default=None, pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    pdb_chain_id: str | None = None
    model_position: int | None = Field(default=None, ge=0)


class StatisticsMutationAAFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mutation_aa: str | None = Field(
        default=None, pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$"
    )


class StatisticsChainFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str | None = None
    source_entity_id: str | None = None
    label_asym_id: str | None = None
    auth_asym_id: str | None = None
    pdb_chain_id: str | None = None


class StatisticsEntityFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str | None = None
    source_entity_id: str | None = None
    label_asym_id: str | None = None


class _StatisticsQueryRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datasets: list[StatisticsDatasetReference] = Field(min_length=1, max_length=50)
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0, le=1_000_000)


class StatisticsOverviewQueryRequest(_StatisticsQueryRequestBase):
    level: Literal["overview"]
    filters: StatisticsOverviewFilters = Field(default_factory=StatisticsOverviewFilters)


class StatisticsResidueQueryRequest(_StatisticsQueryRequestBase):
    level: Literal["residue"]
    filters: StatisticsResidueFilters = Field(default_factory=StatisticsResidueFilters)


class StatisticsMutationAAQueryRequest(_StatisticsQueryRequestBase):
    level: Literal["mutation_aa"]
    filters: StatisticsMutationAAFilters = Field(default_factory=StatisticsMutationAAFilters)


class StatisticsChainQueryRequest(_StatisticsQueryRequestBase):
    level: Literal["chain"]
    filters: StatisticsChainFilters = Field(default_factory=StatisticsChainFilters)


class StatisticsEntityQueryRequest(_StatisticsQueryRequestBase):
    level: Literal["entity"]
    filters: StatisticsEntityFilters = Field(default_factory=StatisticsEntityFilters)


StatisticsQueryRequest = Annotated[
    StatisticsOverviewQueryRequest
    | StatisticsResidueQueryRequest
    | StatisticsMutationAAQueryRequest
    | StatisticsChainQueryRequest
    | StatisticsEntityQueryRequest,
    Field(discriminator="level"),
]


class StatisticsOverviewKey(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatisticsResidueKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str
    source_entity_id: str | None
    label_asym_id: str | None
    auth_asym_id: str
    auth_seq_id: int
    insertion_code: str
    sequence_index: int
    wt: str
    pdb_chain_id: str
    model_position: int


class StatisticsMutationAAKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mutation_aa: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")


class StatisticsChainKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str
    source_entity_id: str | None
    label_asym_id: str | None
    auth_asym_id: str
    pdb_chain_id: str


class StatisticsEntityKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str
    source_entity_id: str | None
    label_asym_id: str | None


class _StatisticsQueryRowBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: StatisticsDatasetReference
    availability: bool
    unavailable_reason: str | None = None
    distribution: StatisticsDistribution | None = None
    native_distribution: StatisticsDistribution | None = None
    non_native_distribution: StatisticsDistribution | None = None
    class_burden: StatisticsClassBurden | None = None
    native_score: float | None = None
    native_class: Literal["high", "neutral", "minimal"] | None = None


class StatisticsOverviewQueryRow(_StatisticsQueryRowBase):
    level: Literal["overview"]
    key: StatisticsOverviewKey
    support: StatisticsOverviewSupport | None = None


class StatisticsResidueQueryRow(_StatisticsQueryRowBase):
    level: Literal["residue"]
    key: StatisticsResidueKey | None
    support: None = None


class StatisticsMutationAAQueryRow(_StatisticsQueryRowBase):
    level: Literal["mutation_aa"]
    key: StatisticsMutationAAKey | None
    support: None = None


class StatisticsChainQueryRow(_StatisticsQueryRowBase):
    level: Literal["chain"]
    key: StatisticsChainKey | None
    support: StatisticsGroupSupport | None = None


class StatisticsEntityQueryRow(_StatisticsQueryRowBase):
    level: Literal["entity"]
    key: StatisticsEntityKey | None
    support: StatisticsGroupSupport | None = None


StatisticsQueryRow = Annotated[
    StatisticsOverviewQueryRow
    | StatisticsResidueQueryRow
    | StatisticsMutationAAQueryRow
    | StatisticsChainQueryRow
    | StatisticsEntityQueryRow,
    Field(discriminator="level"),
]


class StatisticsQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[StatisticsQueryRow]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class FrustraMPNNRuntimeIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime_id: str | None = None
    sif_name: str | None = None
    sif_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    image_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    executable_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    checkpoint_id: str | None = None
    checkpoint_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    package_version: str | None = None
    source_commit: str | None = None
    python_version: str | None = None
    pytorch_version: str | None = None
    image_version: str | None = None
    runtime_identity_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FrustraMPNNResultSourceArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    media_type: str | None = None
    producer_stage: str | None = None


class FrustraMPNNResultPayloadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str | None = None
    schema_version: int | None = Field(default=None, ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FrustraMPNNResultCardinalityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    count: int = Field(ge=0)


class FrustraMPNNTerminalArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str | None = None
    schema_name: str | None = None
    schema_version: int | None = Field(default=None, ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    cardinality: FrustraMPNNResultCardinalityResponse | None = None


class FrustraMPNNTerminalResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str | None = None
    schema_version: int | None = Field(default=None, ge=1)
    request_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    invocation_id: str | None = None
    component_id: str | None = None
    component_contract_version: str | None = None
    candidate_id: str | None = None
    parent_job_id: str | None = None
    parent_workflow_id: str | None = None
    status: Literal["succeeded", "failed", "not_run"] | None = None
    failure_class: str | None = None
    source_artifact: FrustraMPNNResultSourceArtifactResponse | None = None
    runtime_identity: FrustraMPNNRuntimeIdentityResponse | None = None
    artifacts: list[FrustraMPNNTerminalArtifactResponse] = Field(default_factory=list)
    result_payload: FrustraMPNNResultPayloadResponse | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    gpu_provenance: FrustraMPNNGpuProvenanceResponse | None = None


class FrustraMPNNExecutionReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str | None = None
    schema_version: int | None = Field(default=None, ge=1)
    invocation_id: str
    execution_configuration_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    requested_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effective_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_identity_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    structure_map_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    normalized_pdb_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    command_count: int | None = Field(default=None, ge=0)
    merged_raw_csv_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    landscape_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    summary_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    gpu_provenance: FrustraMPNNGpuProvenanceResponse | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)


class FrustraMPNNResultSourceIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    design_id: str | None
    artifact_id: str | None
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str


class FrustraMPNNResultItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invocation_id: str
    parent_job_id: str
    parent_workflow_id: str
    candidate_id: str
    operator_label: str = Field(min_length=1, max_length=160)
    source_identity: FrustraMPNNResultSourceIdentityResponse
    design_id: str | None
    requiredness: str
    source_artifact_id: str | None
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    authority_version: Literal["v3", "v2", "historical_v1"]
    availability: bool
    statistics_available: bool
    missing_fields: list[Phase4Field]
    settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effective_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effective_settings_json: FrustraMPNNEffectiveSettings | None = None
    capability_inventory_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    statistics_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    statistics_json: FrustraMPNNStatisticsDocument | None = None
    comparison_compatibility_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["succeeded", "failed", "not_run"]
    component_contract_version: Literal["1.0", "2.0", "3.0"]
    runtime_identity: FrustraMPNNRuntimeIdentityResponse
    runtime_identity_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    gpu_provenance: FrustraMPNNGpuProvenanceResponse | None = None
    failure_class: str | None
    reopen_destination: dict[str, Any]


class FrustraMPNNResultDetailResponse(FrustraMPNNResultItemResponse):
    summary: (
        FrustraMPNNSummaryV3Document
        | FrustraMPNNSummaryV2Document
        | FrustraMPNNHistoricalSummaryV1Document
    )
    terminal_result: FrustraMPNNTerminalResultResponse
    execution_receipt: FrustraMPNNExecutionReceiptResponse | None


class FrustraMPNNResultListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[FrustraMPNNResultItemResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class FrustraMPNNStatisticsAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: str
    parent_job_id: str
    invocation_id: str
    state: Literal["queued", "running", "completed", "failed"]
    attempt_count: int = Field(ge=0)
    core_artifact_id: str
    core_landscape_sha256: str
    core_manifest_sha256: str
    formula_version: str
    policy_version: str
    package_version: str
    schema_version: Literal[1]
    artifact_sha256: str | None
    statistics_sha256: str | None
    diagnostic: str | None


class FrustraMPNNLandscapeRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    invocation_id: str
    candidate_id: str
    target_id: str
    entity_instance_id: str
    source_entity_id: str | None
    label_asym_id: str | None
    auth_asym_id: str
    auth_seq_id: int
    insertion_code: str
    sequence_index: int
    pdb_chain_id: str | None
    model_position: int | None = Field(default=None, ge=0)
    wt: str
    mutation_aa: str
    score: float | None = None
    score_class: Literal["high", "neutral", "minimal"] | None
    class_: Literal["high", "neutral", "minimal"] | None = Field(alias="class")
    scoreable: bool
    status: Literal["ok", "missing"]
    reason: str | None = None
    native: bool
    provenance: "FrustraMPNNLandscapeProvenanceResponse"
    residue: "FrustraMPNNLandscapeResidueResponse | None"


class FrustraMPNNThresholdPolicyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    mode: Literal["canonical", "custom"] | None = None
    high_max: float
    minimal_min: float


class FrustraMPNNLandscapeProvenanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str | None = None
    schema_version: int | None = Field(default=None, ge=1)
    landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_map_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_pdb_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_csv_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    threshold_policy: FrustraMPNNThresholdPolicyResponse
    threshold_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_configuration_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    requested_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effective_settings_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_identity_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    threshold_policy_id: str | None = None


class FrustraMPNNLandscapeResidueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_instance_id: str
    source_entity_id: str | None = None
    label_asym_id: str | None = None
    auth_asym_id: str
    label_seq_id: int | None = None
    auth_seq_id: int
    insertion_code: str
    sequence_index: int
    pdb_chain_id: str
    pdb_residue_id: int | None = None
    pdb_insertion_code: str | None = None
    model_position: int
    residue_name: str | None = None
    wt: str | None = None


class FrustraMPNNLandscapePageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    items: list[FrustraMPNNLandscapeRowResponse]


class FrustraMPNNArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    role: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    schema_name: str | None
    schema_version: int | None
    cardinality: FrustraMPNNResultCardinalityResponse | None
    download_url: str


class FrustraMPNNArtifactListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[FrustraMPNNArtifactResponse]
    total: int = Field(ge=0)


class AnalyticsDimensionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["identifier", "category", "number", "fraction", "count", "boolean"]
    description: str | None = None
    unit: str | None = None
    formula: str | None = None


class _AnalyticsPointBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    workflow_family: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    design_id: str | None
    candidate_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    configuration_id: str | None
    configuration_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    threshold_policy_id: str | None


class ResultAnalyticsMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mean_score: float | None
    native_score: float | None
    high_fraction: float | None = Field(ge=0, le=1)
    minimal_fraction: float | None = Field(ge=0, le=1)
    scoreable_fraction: float | None = Field(ge=0, le=1)
    slot_count: int = Field(ge=0)
    residue_count: int = Field(ge=0)


class ResultAnalyticsPointResponse(_AnalyticsPointBase):
    metrics: ResultAnalyticsMetricsResponse


class ResidueAnalyticsMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    native_score: float | None
    alternative_mean_score: float | None
    best_alternative_delta: float | None
    worst_alternative_delta: float | None
    high_alternative_fraction: float | None = Field(ge=0, le=1)
    minimal_alternative_fraction: float | None = Field(ge=0, le=1)
    alternative_count: int = Field(ge=0)


class ResidueAnalyticsPointResponse(_AnalyticsPointBase):
    target_id: str
    entity_instance_id: str
    auth_asym_id: str
    auth_seq_id: str
    insertion_code: str
    sequence_index: int
    wt: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    metrics: ResidueAnalyticsMetricsResponse


class MutationAnalyticsMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float | None
    scoreable: bool


class MutationAnalyticsPointResponse(_AnalyticsPointBase):
    target_id: str
    entity_instance_id: str
    auth_asym_id: str
    auth_seq_id: str
    insertion_code: str
    sequence_index: int
    wt: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    mutation_aa: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    score_class: Literal["high", "neutral", "minimal"] | None
    status: str
    reason: str | None
    metrics: MutationAnalyticsMetricsResponse


class _AnalyticsPageBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["frustrampnn_multidimensional_v1"]
    dimensions: list[AnalyticsDimensionResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=5000)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class ResultAnalyticsPageResponse(_AnalyticsPageBase):
    level: Literal["result"]
    items: list[ResultAnalyticsPointResponse]


class ResidueAnalyticsPageResponse(_AnalyticsPageBase):
    level: Literal["residue"]
    items: list[ResidueAnalyticsPointResponse]


class MutationAnalyticsPageResponse(_AnalyticsPageBase):
    level: Literal["mutation"]
    items: list[MutationAnalyticsPointResponse]


class ComparisonDifferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str = Field(min_length=1)
    left: JsonValue
    right: JsonValue


class ComparisonIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str
    source_entity_id: str
    label_asym_id: str
    auth_asym_id: str
    auth_seq_id: int
    insertion_code: str
    sequence_index: int
    wt: str


class ComparisonIdentityDifferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["reference_only", "target_only"]
    identity: ComparisonIdentityResponse


class ComparisonRawDomainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["compatible", "hard_incompatible", "unknown"]
    reasons: list[str]
    differences: list[ComparisonDifferenceResponse]


class ComparisonClassificationDomainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["compatible", "policy_different", "unknown"]
    reasons: list[str]
    differences: list[ComparisonDifferenceResponse]


class ComparisonIdentityAlignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["exact", "partial", "none"]
    reasons: list[str]
    differences: list[ComparisonIdentityDifferenceResponse]
    reference_identity_count: int = Field(ge=0)
    target_identity_count: int = Field(ge=0)
    aligned_identity_count: int = Field(ge=0)


class ComparisonCompatibilityDomainsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_score: ComparisonRawDomainResponse
    classification: ComparisonClassificationDomainResponse
    identity_alignment: ComparisonIdentityAlignmentResponse


class ComparisonCompatibilityMetadataResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compatibility_status: Literal["compatible", "incompatible", "unknown"]
    left_comparison_compatibility_id: str | None
    right_comparison_compatibility_id: str | None
    override_used: bool
    compatibility_differences: list[ComparisonDifferenceResponse]


class PairCompatibilityResponse(ComparisonCompatibilityMetadataResponse):
    target_label: str = Field(pattern=r"^target-[0-9]{4}$")
    target_id: str | None
    target_landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_configuration_sha256: str | None
    compatibility_domains: ComparisonCompatibilityDomainsResponse


class ComparisonComparabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["comparable", "incompatible"]
    reasons: list[str]
    reference_configuration_id: str | None = None
    target_configuration_id: str | None = None
    reference_configuration_sha256: str | None = None
    target_configuration_sha256: str | None = None


class ComparisonResultReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_job_id: str
    invocation_id: str


class ComparisonResidueKeyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str
    auth_asym_id: str
    auth_seq_id: int
    insertion_code: str


class ComparisonScoreProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    sequence_index: int | None
    auth_seq_id: int | None
    score: float | None
    class_: str | None = Field(alias="class")
    scoreable: bool
    status: str


class PairComparisonRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    residue_key: ComparisonResidueKeyResponse
    sequence_index: int | None
    mutation_aa: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    wt: str | None
    mapping_state: Literal["mapped", "unmapped"]
    missingness_state: Literal[
        "none",
        "reference_unmapped",
        "target_unmapped",
        "both_unmapped",
        "reference_missing",
        "target_missing",
        "both_missing",
    ]
    biological_status: Literal["biologically_scored", "incompatible", "unmapped", "missing"]
    reference: ComparisonScoreProjectionResponse
    target: ComparisonScoreProjectionResponse
    raw_score_delta: float | None
    classification_transition: str | None


class MultiComparisonRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    residue_key: ComparisonResidueKeyResponse
    sequence_index: int | None
    mutation_aa: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    mapping_state: Literal["mapped", "unmapped"]
    missingness_state: Literal["none", "per_target"]
    missingness_by_target: list[str] = Field(min_length=1, max_length=8)
    biological_status: Literal[
        "biologically_scored", "partially_scored", "incompatible", "missing", "unmapped"
    ]
    reference: ComparisonScoreProjectionResponse | None
    targets: list[ComparisonScoreProjectionResponse | None] = Field(min_length=1, max_length=8)
    raw_score_deltas: list[float | None] = Field(min_length=1, max_length=8)
    classification_transitions: list[str | None] = Field(min_length=1, max_length=8)


class PairComparisonSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_rows: int = Field(ge=0)
    biologically_scored: int = Field(ge=0)
    incompatible: int = Field(ge=0)
    unmapped: int = Field(ge=0)
    missing_reference: int = Field(ge=0)
    missing_target: int = Field(ge=0)
    missing_both: int = Field(ge=0)
    transitions: int = Field(ge=0)


class MultiComparisonSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_count: int = Field(ge=1, le=8)
    total_rows: int = Field(ge=0)
    biologically_scored: int = Field(ge=0)
    partially_scored: int = Field(ge=0)
    missing: int = Field(ge=0)
    unmapped: int = Field(ge=0)
    incompatible: int = Field(ge=0)
    transitions: int = Field(ge=0)


class ComparisonSourceResultReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["reference", "target"]
    target_label: str | None
    parent_job_id: str
    invocation_id: str
    landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_sha256: str | None


class ComparisonCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["frustrampnn_comparison"]
    schema_version: Literal[1]
    comparison_id: str
    comparison_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_id: str | None
    configuration_sha256: str | None
    reference_configuration_sha256: str | None
    target_configuration_sha256: str | None
    comparability: ComparisonComparabilityResponse
    compatibility_domains: ComparisonCompatibilityDomainsResponse
    summary: PairComparisonSummaryResponse
    rows: list[PairComparisonRowResponse]
    persisted: Literal[True]
    created_at: datetime
    reference: ComparisonResultReferenceResponse
    target: ComparisonResultReferenceResponse
    compatibility_status: Literal["compatible", "incompatible", "unknown"]
    left_comparison_compatibility_id: str | None
    right_comparison_compatibility_id: str | None
    override_used: bool
    compatibility_differences: list[ComparisonDifferenceResponse]


class MultiComparisonComparabilityResponse(ComparisonCompatibilityMetadataResponse):
    status: Literal["comparable", "incompatible"]
    reasons: list[str]
    target_count: int = Field(ge=1, le=8)
    pair_compatibility: list[PairCompatibilityResponse] = Field(min_length=1, max_length=8)


class MultiComparisonCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["frustrampnn_multistate_comparison"]
    schema_version: Literal[1]
    comparison_mode: Literal["multi_state"]
    comparison_id: str
    comparison_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_landscape_sha256s: list[str] = Field(min_length=1, max_length=8)
    target_labels: list[str] = Field(min_length=1, max_length=8)
    configuration_id: str | None
    configuration_sha256: str | None
    reference_configuration_sha256: str | None
    target_configuration_sha256s: list[str | None] = Field(min_length=1, max_length=8)
    pair_compatibility: list[PairCompatibilityResponse] = Field(min_length=1, max_length=8)
    source_result_references: list[ComparisonSourceResultReferenceResponse] = Field(
        min_length=2, max_length=9
    )
    comparability: MultiComparisonComparabilityResponse
    summary: MultiComparisonSummaryResponse
    rows: list[MultiComparisonRowResponse]
    persisted: Literal[True]
    created_at: datetime
    reference: ComparisonResultReferenceResponse
    target: ComparisonResultReferenceResponse
    compatibility_status: Literal["compatible", "incompatible", "unknown"]
    left_comparison_compatibility_id: str | None
    right_comparison_compatibility_id: str | None
    override_used: bool
    compatibility_differences: list[ComparisonDifferenceResponse]


class ComparisonRowsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_id: str
    items: list[PairComparisonRowResponse | MultiComparisonRowResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=5000)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class GuidanceResidueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_instance_id: str | None = None
    auth_asym_id: str = Field(min_length=1)
    auth_seq_id: int
    insertion_code: str = ""


class GuidanceResidueSetRegionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_type: Literal["residue_set"]
    residues: list[GuidanceResidueRequest] = Field(min_length=1)


class GuidanceSequenceSpanRegionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_type: Literal["sequence_span"]
    start: int = Field(ge=1)
    end: int = Field(ge=1)
    auth_asym_id: str | None = None

    @model_validator(mode="after")
    def validate_span(self) -> "GuidanceSequenceSpanRegionRequest":
        if self.start > self.end:
            raise ValueError("sequence_span start must not exceed end")
        return self


class GuidanceStructuralRegionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_type: Literal["pocket", "interface", "contact_set", "loop", "domain", "mapped_region"]
    residues: list[GuidanceResidueRequest] = Field(min_length=1)
    mapping_method: str = Field(min_length=1)
    source_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mapping_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_mapping_provenance(self) -> "GuidanceStructuralRegionRequest":
        if self.source_artifact_sha256 is None and self.mapping_artifact_sha256 is None:
            raise ValueError("structural guidance regions require source or mapping artifact SHA-256")
        return self


GuidanceRegionRequest = Annotated[
    GuidanceResidueSetRegionRequest
    | GuidanceSequenceSpanRegionRequest
    | GuidanceStructuralRegionRequest,
    Field(discriminator="region_type"),
]


class GuidanceScoreAggregateObjectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_type: Literal["score_aggregate"]
    direction: Literal["higher_is_better", "lower_is_better"]
    aggregation: Literal["mean", "median", "min", "max"] = "mean"
    target_class: Literal["high", "neutral", "minimal"] | None = None
    reference_class: Literal["high", "neutral", "minimal"] | None = None


class GuidanceClassObjectiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_type: Literal["class_count", "class_transition"]
    direction: Literal["higher_is_better", "lower_is_better"]
    aggregation: Literal["mean", "median", "min", "max"] = "mean"
    target_class: Literal["high", "neutral", "minimal"]
    reference_class: Literal["high", "neutral", "minimal"] | None = None


GuidanceObjectiveRequest = Annotated[
    GuidanceScoreAggregateObjectiveRequest | GuidanceClassObjectiveRequest,
    Field(discriminator="objective_type"),
]


class GuidanceConstraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prohibited_mutations: list[str] = Field(default_factory=list)


class GuidanceRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["lexicographic"] = "lexicographic"
    tie_break: Literal["sequence_index_then_mutation"] | None = None


class GuidanceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_job_id: str = Field(min_length=1)
    source_invocation_id: str = Field(min_length=1)
    region: GuidanceRegionRequest
    objective: GuidanceObjectiveRequest
    constraints: GuidanceConstraints = Field(default_factory=GuidanceConstraints)
    ranking: GuidanceRanking = Field(default_factory=GuidanceRanking)
    rationale: str = Field(min_length=1)
    guidance_id: str | None = Field(default=None, min_length=1)


class GuidanceResolvedResidueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auth_asym_id: str
    auth_seq_id: int
    insertion_code: str


class GuidanceRegionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_type: Literal[
        "residue_set", "sequence_span", "pocket", "interface", "contact_set", "loop", "domain", "mapped_region"
    ]
    requested_residues: list[GuidanceResidueRequest]
    resolved_residues: list[GuidanceResolvedResidueResponse]
    unresolved_residues: list[GuidanceResolvedResidueResponse]
    region_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_method: str | None = None
    source_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mapping_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    start: int | None = None
    end: int | None = None


class GuidanceObjectiveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective_type: Literal["score_aggregate", "class_count", "class_transition"]
    direction: Literal["higher_is_better", "lower_is_better"]
    aggregation: Literal["mean", "median", "min", "max"]
    target_class: Literal["high", "neutral", "minimal"] | None
    reference_class: Literal["high", "neutral", "minimal"] | None


class GuidanceRankedSlotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    entity_instance_id: str | None
    auth_asym_id: str
    auth_seq_id: int
    insertion_code: str
    sequence_index: int
    wt: str | None
    mutation_aa: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    score: float
    class_: Literal["high", "neutral", "minimal"] | None = Field(alias="class")
    scoreable: Literal[True]
    rationale: str
    rank: int = Field(ge=1)


class GuidanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_name: Literal["frustrampnn_guidance"]
    schema_version: Literal[1]
    guidance_id: str
    guidance_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_landscape_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_id: str
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    region: GuidanceRegionResponse
    objective: GuidanceObjectiveResponse
    constraints: GuidanceConstraints
    ranking: GuidanceRanking
    ranked_slots: list[GuidanceRankedSlotResponse]
    rationale: str = Field(min_length=1)
    decision_support_only: Literal[True]
    instrument_control: Literal[False]
    observed_outcome: None
    persisted: Literal[True]
    created_at: datetime


class FrustraMPNNOwnedSourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    job_id: str = Field(min_length=1)
    invocation_id: str = Field(min_length=1)


class FrustraMPNNOwnedSourceInspectionRequest(FrustraMPNNOwnedSourceReference):
    selected_model_number: int = Field(ge=1)
    preferred_altloc: str = Field(pattern=r"^(?:|[A-Za-z0-9])$")


class FrustraMPNNOwnedSettingsValidateRequest(FrustraMPNNOwnedSourceReference):
    settings: FrustraMPNNRequestedSettings

    @field_validator("settings", mode="before")
    @classmethod
    def _complete_settings(cls, value: Any) -> FrustraMPNNRequestedSettings:
        try:
            return validate_complete_requested_settings(value)
        except RequestedSettingsPayloadError as exc:
            raise ValidationError.from_exception_data(
                "FrustraMPNNRequestedSettings",
                [{
                    "type": "value_error",
                    "loc": exc.location,
                    "input": value,
                    "ctx": {"error": exc},
                }],
            ) from exc


class FrustraMPNNValidationHashes(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_inventory_byte_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_map_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FrustraMPNNSafeRuntimeProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    sif_name: str
    sif_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    executable_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_id: str
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_version: str
    source_commit: str
    python_version: str
    pytorch_version: str
    image_version: str


class FrustraMPNNSafeExecutionConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    configuration_id: Literal[
        "frustrampnn_execution_configuration_v2",
        "frustrampnn_execution_configuration_v3",
    ]
    schema_name: Literal["frustrampnn_execution_configuration"]
    schema_version: Literal[2, 3]
    tool_id: Literal["frustrampnn"]
    tool_version: Literal["MegaScale"]
    effective_settings: FrustraMPNNEffectiveSettings
    settings_value_origin: Literal["bms_default", "operator_request"]
    requested_settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_settings_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_inventory_byte_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    classification_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime: FrustraMPNNSafeRuntimeProjection
    runtime_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_policy_id: Literal["frustrampnn_structure_normalizer"]
    normalization_policy_version: Literal[1]
    threshold_policy_id: Literal["frustrampnn_class_v1"]
    source_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_map_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_pdb_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _configuration_generation_is_paired(
        self,
    ) -> "FrustraMPNNSafeExecutionConfiguration":
        if (self.configuration_id, self.schema_version) not in {
            ("frustrampnn_execution_configuration_v2", 2),
            ("frustrampnn_execution_configuration_v3", 3),
        }:
            raise ValueError("execution configuration generation is inconsistent")
        return self


class FrustraMPNNSettingsValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    validation_scope: Literal["preview_only"] = "preview_only"
    queue_resolution_requirement: Literal[
        "submission_must_re_resolve_governed_source"
    ] = "submission_must_re_resolve_governed_source"
    normalized_requested_settings: FrustraMPNNRequestedSettings
    effective_settings: FrustraMPNNEffectiveSettings
    execution_configuration: FrustraMPNNSafeExecutionConfiguration
    hashes: FrustraMPNNValidationHashes


class FrustraMPNNInspectableEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    entity_instance_id: str = Field(min_length=1)
    source_entity_id: str | None
    label_asym_id: str | None
    auth_asym_id: str = Field(min_length=1)
    pdb_chain_id: str = Field(min_length=1, max_length=1)


class FrustraMPNNInspectableResidue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    entity_instance_id: str = Field(min_length=1)
    source_entity_id: str | None
    label_asym_id: str | None
    auth_asym_id: str = Field(min_length=1)
    auth_seq_id: int
    insertion_code: str = Field(max_length=1)
    sequence_index: int = Field(ge=1)
    wt: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")


class FrustraMPNNSourceInspectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_models: list[int]
    selected_source_model: int = Field(ge=1)
    observed_altlocs: list[str]
    selected_altloc: str = Field(pattern=r"^(?:|[A-Za-z0-9])$")
    protein_entities: list[FrustraMPNNInspectableEntity]
    mapped_residues: list[FrustraMPNNInspectableResidue]


def _source_resolution_http_error(
    exc: SourceResolutionError,
    *,
    settings_request: bool,
) -> HTTPException:
    location = ("body", "settings", *exc.location) if settings_request else (
        "body", "source",
    )
    return HTTPException(
        status_code=422,
        detail=[{
            "type": "value_error.source_resolution",
            "loc": list(location),
            "msg": str(exc),
        }],
    )


async def _child_job_receipt(session: AsyncSession, child: Job) -> dict[str, Any]:
    """Serialize a child that the service has already committed atomically."""
    return await child_receipt(session, child=child)


def _frustrampnn_consumer_workflow(parent: Job) -> str:
    """Resolve the supported consumer while the generic API owner keeps fan-out."""
    model_id = str(parent.model_id or "").strip().lower()
    mode = str(parent.mode or "").strip().lower()
    parent_params = getattr(parent, "params", None)
    params = parent_params if isinstance(parent_params, Mapping) else {}
    if model_id == "conformational_mapping" or mode == "map":
        return "conformational_mapping"
    if "antibody" in model_id or "antibody" in mode:
        return "antibody_denovo"
    if mode == "complex_prediction" or any(
        params.get(key)
        for key in ("complex_components", "complex_json_path", "complex_batch_dir")
    ):
        return "complex_prediction"
    if mode == "structure_prediction" or (model_id == "boltz2" and mode == "predict"):
        return "structure_prediction"
    # The supported entrypoint resolver sends all remaining design modes to
    # workflows/protein_design.nf. Preserve that compatibility boundary here.
    return "protein_design"


async def _fanout_design_selections(
    session: AsyncSession,
    *,
    parent: Job,
    selections: list[Any],
    requested_settings: FrustraMPNNRequestedSettings,
    trigger: str,
    require_running_parent: bool = False,
    workflow_capability_digest: str | None = None,
):
    members = [
        StructureDatasetMember(
            structure_id=str(
                selection.design_id
                or selection.producer_coordinates.get("candidate_id")
                or _candidate_id
            ),
            lineage={
                "design_id": selection.design_id,
                "source_job_id": selection.source_job_id,
                "source_sha256": selection.source_sha256,
                "producer_stage": selection.producer_stage,
                "producer_coordinates": selection.producer_coordinates,
            },
            payload=selection,
        )
        for _candidate_id, selection in enumerate(selections, 1)
    ]

    async def create_batch(batch: StructureDatasetBatch[Any]) -> Job:
        return await create_child_job(
            session,
            selections=[member.payload for member in batch.members],
            source_parent=parent,
            trigger=trigger,
            requested_settings=requested_settings,
            preallocated_job_id=batch.child_job_id,
            commit=False,
        )

    return await fan_out_structure_dataset(
        session,
        workflow_id=f"{_frustrampnn_consumer_workflow(parent)}.frustrampnn.v1",
        parent_job=parent,
        members=members,
        batching_enabled=requested_settings.batching_enabled,
        structures_per_job=requested_settings.structures_per_job,
        request_identity={
            "requested_settings": requested_settings.model_dump(mode="json", exclude_none=False),
            "trigger": trigger,
        },
        create_child=create_batch,
        discard_child=discard_uncommitted_child_artifacts,
        require_running_parent=require_running_parent,
        workflow_capability_digest=workflow_capability_digest,
    )


async def _fanout_receipt(session: AsyncSession, fanout: Any) -> dict[str, Any]:
    children: list[dict[str, Any]] = []
    for child in fanout.child_jobs:
        receipt = await _child_job_receipt(session, child)
        receipt["structure_count"] = len(receipt["candidates"])
        children.append(receipt)
    return {
        "schema_name": FANOUT_SCHEMA,
        "fanout_id": fanout.fanout_id,
        "parent_job_id": fanout.parent_job_id,
        "selected_structure_count": fanout.selected_structure_count,
        "structures_per_job": fanout.structures_per_job,
        "effective_structures_per_job": fanout.effective_structures_per_job,
        "replayed": fanout.replayed,
        "child_jobs": children,
    }


def _multipart_requested_settings(form: Any) -> FrustraMPNNRequestedSettings:
    raw = form.get("frustrampnn_settings")
    if raw is None:
        return default_settings()
    if not isinstance(raw, str):
        raise HTTPException(
            422,
            "frustrampnn_settings must be a JSON object multipart metadata field",
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "frustrampnn_settings must be valid JSON") from exc
    try:
        return validate_complete_requested_settings(payload)
    except (RequestedSettingsPayloadError, ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"invalid frustrampnn_settings: {exc}") from exc


async def _read_bounded_upload(
    upload: UploadFile,
    *,
    max_bytes: int = _MAX_MULTIPART_STRUCTURE_BYTES,
    chunk_size: int = _MULTIPART_READ_CHUNK_BYTES,
) -> bytes:
    """Read at most max_bytes + 1 so oversized multipart bodies fail early."""

    if max_bytes < 0 or chunk_size < 1:
        raise ValueError("multipart read bounds must be positive")
    payload = bytearray()
    while True:
        remaining_probe = max_bytes + 1 - len(payload)
        if remaining_probe <= 0:
            raise HTTPException(413, "uploaded structure exceeds the 64 MiB limit")
        chunk = await upload.read(min(chunk_size, remaining_probe))
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > max_bytes:
            raise HTTPException(413, "uploaded structure exceeds the 64 MiB limit")


def _safe_relative_artifact(root: Path, relative_path: Any) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("artifact relative path is unavailable")
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != relative_path
    ):
        raise ValueError("artifact relative path is unsafe")
    return root / relative


def _owned_manifest_artifact_path(
    owned_root: Path,
    storage_path: Any,
    relative_path: Any,
) -> Path:
    """Bind a persisted artifact location to its manifest path inside the owned job root."""

    _safe_relative_artifact(Path("owned"), relative_path)
    if not isinstance(storage_path, str) or not storage_path:
        raise ValueError("artifact storage authority is unavailable")
    storage = Path(storage_path)
    if (
        not storage.is_absolute()
        or storage.as_posix() != storage_path
        or any(part in {"", ".", ".."} for part in storage.parts[1:])
    ):
        raise ValueError("artifact storage authority is unsafe")
    relative = Path(relative_path)
    bundle_root = storage
    for _ in relative.parts:
        bundle_root = bundle_root.parent
    if bundle_root / relative != storage:
        raise ValueError("artifact storage path does not match its manifest path")
    lexical_owned_root = Path(os.path.abspath(owned_root))
    lexical_bundle_root = Path(os.path.abspath(bundle_root))
    try:
        lexical_bundle_root.relative_to(lexical_owned_root)
    except ValueError as exc:
        raise ValueError("artifact storage escapes the owned job root") from exc
    return storage


def _verified_bytes(
    path: Path,
    *,
    expected_sha256: Any,
    expected_size: Any,
    max_bytes: int,
) -> bytes:
    if not isinstance(expected_size, int) or expected_size < 0 or expected_size > max_bytes:
        raise ValueError("artifact size authority is invalid")
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError("artifact digest authority is invalid")
    payload = read_structure_bytes(path, max_bytes=max_bytes)
    if len(payload) != expected_size or hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("artifact byte identity does not match persisted authority")
    return payload


def _source_descriptor(filename: str | None, media_type: str | None) -> tuple[str, str]:
    suffix = Path(filename or "").suffix.lower()
    accepted = {
        ".pdb": {"chemical/x-pdb", "chemical/pdb", "application/octet-stream"},
        ".cif": {"chemical/x-cif", "chemical/x-mmcif", "application/mmcif", "application/octet-stream"},
        ".mmcif": {"chemical/x-cif", "chemical/x-mmcif", "application/mmcif", "application/octet-stream"},
    }
    if suffix not in accepted:
        raise HTTPException(422, "structure source must have a .pdb, .cif, or .mmcif suffix")
    observed_media = (media_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    if observed_media not in accepted[suffix]:
        raise HTTPException(422, "structure source media type does not match its suffix")
    return suffix, observed_media


async def _cm_owned_source_bytes(
    *,
    job: Job,
    result: FrustraMPNNResult,
    invocation_id: str,
    manifest: Mapping[str, Any],
    session: AsyncSession,
) -> tuple[bytes, str]:
    """Load a CM-owned retry source through its persisted v2 result closure."""

    if (
        job.model_id != "conformational_mapping"
        or not isinstance(job.output_dir, str)
        or job.status != "completed"
        or job.queue_status != "completed"
        or job.current_stage != "Complete"
        or (job.params or {}).get("run_frustrampnn") is not True
    ):
        raise HTTPException(409, "CM result source lineage authority is unavailable")
    root = Path(job.output_dir)

    typed_request_rows = (
        await session.execute(
            select(ConformationalMappingRequest).where(
                ConformationalMappingRequest.job_id == job.id,
            )
        )
    ).scalars().all()
    if len(typed_request_rows) != 1:
        raise HTTPException(409, "CM typed request authority is unavailable")
    typed_request = typed_request_rows[0]
    if typed_request.status != "completed" or typed_request.backend != "confornets":
        raise HTTPException(409, "CM typed request is not a completed ConforNets request")
    cm_request_path_value = (job.params or {}).get("cm_request_path")
    if not isinstance(cm_request_path_value, str):
        raise HTTPException(409, "CM typed request path authority is unavailable")

    request_rows = (
        await session.execute(
            select(FrustraMPNNArtifact).where(
                FrustraMPNNArtifact.parent_job_id == job.id,
                FrustraMPNNArtifact.invocation_id == invocation_id,
                FrustraMPNNArtifact.role == "component_request",
            )
        )
    ).scalars().all()
    authority_rows = (
        await session.execute(
            select(FrustraMPNNArtifact).where(
                FrustraMPNNArtifact.parent_job_id == job.id,
                FrustraMPNNArtifact.invocation_id == invocation_id,
                FrustraMPNNArtifact.role == "identity_authority",
            )
        )
    ).scalars().all()
    map_rows = (
        await session.execute(
            select(FrustraMPNNArtifact).where(
                FrustraMPNNArtifact.parent_job_id == job.id,
                FrustraMPNNArtifact.invocation_id == invocation_id,
                FrustraMPNNArtifact.role == "structure_map",
            )
        )
    ).scalars().all()
    if len(request_rows) != 1 or len(authority_rows) != 1 or len(map_rows) != 1:
        raise HTTPException(409, "CM result authority artifact closure is unavailable")
    request_artifact = request_rows[0]
    authority_artifact = authority_rows[0]
    map_artifact = map_rows[0]

    source_registry_rows = (
        await session.execute(
            select(ConformationalMappingArtifact).where(
                ConformationalMappingArtifact.request_id == typed_request.request_id,
                ConformationalMappingArtifact.candidate_id == result.candidate_id,
                ConformationalMappingArtifact.role == "authoritative_cif",
            )
        )
    ).scalars().all()
    if len(source_registry_rows) != 1:
        raise HTTPException(409, "CM authoritative source registry row is unavailable")
    source_registry = source_registry_rows[0]

    def manifest_record(artifact: FrustraMPNNArtifact) -> dict[str, Any]:
        records = [
            record
            for record in manifest.get("artifacts", [])
            if isinstance(record, dict)
            and record.get("relative_path") == artifact.relative_path
        ]
        if len(records) != 1:
            raise ValueError("CM artifact is not manifest-attested")
        record = records[0]
        if (
            record.get("sha256") != artifact.content_sha256
            or record.get("bytes") != artifact.size_bytes
        ):
            raise ValueError("CM artifact identity is inconsistent")
        return record

    try:
        request_record = manifest_record(request_artifact)
        authority_record = manifest_record(authority_artifact)
        map_record = manifest_record(map_artifact)
        request_path = _owned_manifest_artifact_path(
            root,
            request_artifact.storage_path,
            request_artifact.relative_path,
        )
        authority_path = _owned_manifest_artifact_path(
            root,
            authority_artifact.storage_path,
            authority_artifact.relative_path,
        )
        map_path = _owned_manifest_artifact_path(
            root,
            map_artifact.storage_path,
            map_artifact.relative_path,
        )
        request_bytes = _verified_bytes(
            request_path,
            expected_sha256=request_artifact.content_sha256,
            expected_size=request_artifact.size_bytes,
            max_bytes=4 * 1024 * 1024,
        )
        request_payload = canonical_json_loads(request_bytes)
        if canonical_json_bytes(request_payload) != request_bytes:
            raise ValueError("CM component request is not canonical JSON")
        if (
            request_payload.get("parent_job_id") != str(job.id)
            or request_payload.get("invocation_id") != invocation_id
            or request_payload.get("candidate_id") != result.candidate_id
            or result.request_sha256 != request_artifact.content_sha256
        ):
            raise ValueError("CM component request identity is not cross-bound")

        cm_request_path = Path(cm_request_path_value)
        if (
            not cm_request_path.is_absolute()
            or cm_request_path != root / "cm_request_v1.json"
        ):
            raise ValueError("CM typed request path is not bound to the job root")
        cm_request_bytes = read_structure_bytes(
            cm_request_path,
            max_bytes=4 * 1024 * 1024,
        )
        cm_request_payload = canonical_json_loads(cm_request_bytes)
        if canonical_json_bytes(cm_request_payload) != cm_request_bytes:
            raise ValueError("CM typed request is not canonical JSON")
        validate_cm_schema("cm_request_v1", cm_request_payload)
        persisted_cm_request = typed_request.request_json
        if isinstance(persisted_cm_request, str):
            persisted_cm_request = canonical_json_loads(persisted_cm_request.encode())
        if (
            canonical_json_bytes(persisted_cm_request) != cm_request_bytes
            or cm_request_payload.get("request_id") != typed_request.request_id
            or cm_request_payload.get("request_sha256") != typed_request.request_sha256
        ):
            raise ValueError("CM typed request bytes are not cross-bound")

        identity_payload = request_payload.get("identity_authority_artifact")
        if not isinstance(identity_payload, dict):
            raise ValueError("CM identity authority envelope is unavailable")
        encoded_authority = identity_payload.get("canonical_json_base64")
        if not isinstance(encoded_authority, str):
            raise ValueError("CM identity authority bytes are unavailable")
        authority_bytes = base64.b64decode(encoded_authority, validate=True)
        if (
            not authority_bytes
            or canonical_json_bytes(canonical_json_loads(authority_bytes)) != authority_bytes
            or hashlib.sha256(authority_bytes).hexdigest() != identity_payload.get("sha256")
            or identity_payload.get("relative_path") != authority_artifact.relative_path
            or identity_payload.get("media_type") != "application/json"
            or len(authority_bytes) != authority_artifact.size_bytes
            or authority_artifact.content_sha256 != identity_payload.get("sha256")
        ):
            raise ValueError("CM identity authority bytes are not cross-bound")
        persisted_authority_bytes = _verified_bytes(
            authority_path,
            expected_sha256=authority_artifact.content_sha256,
            expected_size=authority_artifact.size_bytes,
            max_bytes=4 * 1024 * 1024,
        )
        if persisted_authority_bytes != authority_bytes:
            raise ValueError("CM identity authority artifact differs from request bytes")

        try:
            validate_schema("workflow_component_request_v2", request_payload)
        except ContractValidationError as exc:
            legacy_reseal = (
                identity_payload.get("bytes") is None
                and str(exc)
                == "workflow_component_request_v2 rejected $['identity_authority_artifact']: 'bytes' is a required property"
                and manifest.get("artifact_count") == 11
                and len(manifest.get("artifacts", [])) == 11
                and authority_artifact.relative_path == "authority_artifact_v1.json"
                and persisted_authority_bytes == authority_bytes
            )
            if not legacy_reseal:
                raise
            request_for_validation = copy.deepcopy(request_payload)
            request_for_validation["identity_authority_artifact"]["bytes"] = len(authority_bytes)
            validate_schema("workflow_component_request_v2", request_for_validation)

        source_authority = request_payload.get("source_artifact")
        if not isinstance(source_authority, dict):
            raise ValueError("CM original source authority is unavailable")
        source_relative = source_authority.get("relative_path")
        source_sha256 = source_authority.get("sha256")
        if (
            source_authority.get("artifact_id") != result.candidate_id
            or source_authority.get("producer_stage") != "conformational_mapping:confornets"
            or source_sha256 != result.source_artifact_sha256
            or source_sha256 != request_payload.get("source_artifact", {}).get("sha256")
            or result.source_artifact_id != source_registry.candidate_id
            or source_authority.get("artifact_id") != source_registry.candidate_id
            or source_authority.get("relative_path") != source_registry.relative_path
            or source_sha256 != source_registry.content_sha256
            or source_authority.get("media_type") != source_registry.media_type
        ):
            raise ValueError("CM original source authority is not cross-bound")
        suffix, _ = _source_descriptor(
            source_relative,
            source_authority.get("media_type"),
        )
        source_path = _owned_manifest_artifact_path(
            root,
            source_registry.storage_path,
            source_registry.relative_path,
        )
        source_bytes = _verified_bytes(
            source_path,
            expected_sha256=source_registry.content_sha256,
            expected_size=source_registry.size_bytes,
            max_bytes=_MAX_MULTIPART_STRUCTURE_BYTES,
        )
        if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
            raise ValueError("CM original source bytes do not match persisted authority")

        map_bytes = _verified_bytes(
            map_path,
            expected_sha256=map_artifact.content_sha256,
            expected_size=map_artifact.size_bytes,
            max_bytes=16 * 1024 * 1024,
        )
        structure_map = canonical_json_loads(map_bytes)
        if canonical_json_bytes(structure_map) != map_bytes:
            raise ValueError("CM structure map is not canonical JSON")
        validate_schema("frustrampnn_structure_map_v1", structure_map)
        if (
            structure_map.get("parent_job_id") != str(job.id)
            or structure_map.get("candidate_id") != result.candidate_id
            or structure_map.get("source_sha256") != source_sha256
            or structure_map.get("source_sha256") != hashlib.sha256(source_bytes).hexdigest()
            or structure_map.get("identity_authority") != "producer_manifest_v1"
            or structure_map.get("authority_artifact_sha256") != authority_artifact.content_sha256
            or canonical_sha256(structure_map) != request_payload.get("structure_map_sha256")
        ):
            raise ValueError("CM structure map authority is not cross-bound")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(409, "CM owned source artifact byte identity is unavailable") from exc
    return source_bytes, suffix


async def _owned_source_bytes(
    *,
    job_id: str,
    invocation_id: str,
    session: AsyncSession,
) -> tuple[bytes, str]:
    """Load one persisted v2/v3 original source after validating its attestation chain."""

    result = await _scoped_result(invocation_id, job_id, session)
    manifest = result.manifest_json or {}
    manifest_generation = manifest.get("schema_version")
    if manifest_generation not in (2, 3) or result.effective_settings_json is None:
        raise HTTPException(
            409,
            "historical v1 result has no governed original-source authority",
        )
    try:
        validate_schema(f"frustrampnn_result_manifest_v{manifest_generation}", manifest)
    except Exception as exc:
        raise HTTPException(409, "result manifest authority is unavailable") from exc
    if canonical_sha256(manifest) != result.manifest_sha256:
        raise HTTPException(409, "result manifest byte identity does not match persisted authority")
    if (
        manifest.get("parent_job_id") != job_id
        or manifest.get("invocation_id") != invocation_id
        or manifest.get("candidate_id") != result.candidate_id
        or manifest.get("request_sha256") != result.request_sha256
        or manifest.get("source_artifact_sha256") != result.source_artifact_sha256
    ):
        raise HTTPException(409, "result manifest identity does not match persisted result")

    job = await session.get(Job, job_id)
    if job is not None and job.model_id == "conformational_mapping":
        return await _cm_owned_source_bytes(
            job=job,
            result=result,
            invocation_id=invocation_id,
            manifest=manifest,
            session=session,
        )
    envelope = (job.params or {}).get("_frustrampnn_child_v1") if job else None
    if (
        job is None
        or job.model_id != "frustrampnn"
        or not isinstance(job.output_dir, str)
        or not isinstance(envelope, dict)
        or envelope.get("schema_name") != "bms.frustrampnn.scheduler-child.v1"
        or envelope.get("schema_version") != 1
        or envelope.get("execution_owner_job_id") != job_id
    ):
        raise HTTPException(409, "owned source lineage authority is unavailable")
    root = Path(job.output_dir)

    request_rows = (
        await session.execute(
            select(FrustraMPNNArtifact).where(
                FrustraMPNNArtifact.parent_job_id == job_id,
                FrustraMPNNArtifact.invocation_id == invocation_id,
                FrustraMPNNArtifact.role == "component_request",
            )
        )
    ).scalars().all()
    if len(request_rows) != 1:
        raise HTTPException(409, "exact persisted component request is unavailable")
    request_artifact = request_rows[0]
    manifest_requests = [
        record
        for record in manifest.get("artifacts", [])
        if isinstance(record, dict)
        and record.get("relative_path") == request_artifact.relative_path
    ]
    if len(manifest_requests) != 1:
        raise HTTPException(409, "component request is not manifest-attested")
    manifest_request = manifest_requests[0]
    if (
        manifest_request.get("schema_name") != "workflow_component_request"
        or manifest_request.get("schema_version") != manifest_generation
    ):
        raise HTTPException(409, "component request generation is not manifest-attested")
    try:
        request_path = _owned_manifest_artifact_path(
            root,
            request_artifact.storage_path,
            request_artifact.relative_path,
        )
    except ValueError as exc:
        raise HTTPException(409, "component request storage authority is unavailable") from exc
    if (
        manifest_request.get("sha256") != request_artifact.content_sha256
        or manifest_request.get("bytes") != request_artifact.size_bytes
        or request_artifact.content_sha256 != result.request_sha256
    ):
        raise HTTPException(409, "component request identity is inconsistent")
    try:
        request_bytes = _verified_bytes(
            request_path,
            expected_sha256=request_artifact.content_sha256,
            expected_size=request_artifact.size_bytes,
            max_bytes=4 * 1024 * 1024,
        )
        request_payload = canonical_json_loads(request_bytes)
        if canonical_json_bytes(request_payload) != request_bytes:
            raise ValueError("component request is not canonical JSON")
        if request_payload.get("schema_version") != manifest_generation:
            raise ValueError("component request generation is not cross-bound")
        validate_schema(
            f"workflow_component_request_v{manifest_generation}", request_payload
        )
    except Exception as exc:
        raise HTTPException(409, "component request byte identity is unavailable") from exc
    if (
        request_payload.get("parent_job_id") != job_id
        or request_payload.get("invocation_id") != invocation_id
        or request_payload.get("candidate_id") != result.candidate_id
    ):
        raise HTTPException(409, "component request identity is not cross-bound")

    selections = envelope.get("selection")
    matches = [
        item for item in selections or []
        if isinstance(item, dict) and item.get("invocation_id") == invocation_id
    ]
    if len(matches) != 1:
        raise HTTPException(409, "owned source invocation lineage is unavailable")
    lineage = matches[0]
    try:
        batch_path = _safe_relative_artifact(root, envelope.get("batch_manifest_relative_path"))
        configured_batch = Path((job.params or {}).get("frustrampnn_batch_manifest_path", ""))
        if configured_batch != batch_path.absolute():
            raise ValueError("batch path does not match its owned root")
        batch_bytes = _verified_bytes(
            batch_path,
            expected_sha256=envelope.get("batch_manifest_sha256"),
            expected_size=envelope.get("batch_manifest_size_bytes"),
            max_bytes=16 * 1024 * 1024,
        )
        batch = canonical_json_loads(batch_bytes)
        if canonical_json_bytes(batch) != batch_bytes:
            raise ValueError("batch manifest is not canonical JSON")
        records = batch.get("records") if isinstance(batch, dict) else None
        record_matches = [
            item for item in records or []
            if isinstance(item, dict) and item.get("invocation_id") == invocation_id
        ]
        if len(record_matches) != 1:
            raise ValueError("batch record is unavailable")
        record = record_matches[0]
        if (
            batch.get("schema_name") != "bms_frustrampnn_scheduler_batch"
            or batch.get("schema_version") != manifest_generation
            or batch.get("execution_owner_job_id") != job_id
            or record.get("record_schema_name") != "bms_frustrampnn_scheduler_record"
            or record.get("record_schema_version") != 2
            or record.get("candidate_id") != result.candidate_id
            or record.get("request_sha256") != result.request_sha256
            or record.get("request_size_bytes") != request_artifact.size_bytes
            or lineage.get("component_request_sha256") != result.request_sha256
            or lineage.get("component_request_relative_path") != record.get("request_relative_path")
            or lineage.get("structure_map_relative_path") != record.get("structure_map_relative_path")
            or lineage.get("structure_map_sha256") != record.get("structure_map_sha256")
            or request_payload.get("structure_map_sha256") != record.get("structure_map_sha256")
        ):
            raise ValueError("scheduler source lineage is not cross-bound")

        source_authority = request_payload.get("source_artifact")
        if not isinstance(source_authority, dict):
            raise ValueError("original source authority is absent")
        if (
            source_authority.get("relative_path") != lineage.get("snapshot_relative_path")
            or source_authority.get("sha256") != lineage.get("sha256")
            or result.source_artifact_sha256 != lineage.get("sha256")
        ):
            raise ValueError("original source authority is not cross-bound")
        suffix, _ = _source_descriptor(
            source_authority.get("relative_path"), source_authority.get("media_type")
        )
        expected_format = "pdb" if suffix == ".pdb" else "mmcif"
        if lineage.get("source_format") != expected_format:
            raise ValueError("original source format is inconsistent")
        source_path = _safe_relative_artifact(root, source_authority.get("relative_path"))
        source_bytes = _verified_bytes(
            source_path,
            expected_sha256=lineage.get("sha256"),
            expected_size=lineage.get("size_bytes"),
            max_bytes=_MAX_MULTIPART_STRUCTURE_BYTES,
        )
        map_path = _safe_relative_artifact(root, record.get("structure_map_relative_path"))
        map_bytes = _verified_bytes(
            map_path,
            expected_sha256=record.get("structure_map_sha256"),
            expected_size=record.get("structure_map_size_bytes"),
            max_bytes=16 * 1024 * 1024,
        )
        structure_map = canonical_json_loads(map_bytes)
        if canonical_json_bytes(structure_map) != map_bytes:
            raise ValueError("structure map is not canonical JSON")
        validate_schema("frustrampnn_structure_map_v1", structure_map)
        if structure_map.get("source_sha256") != hashlib.sha256(source_bytes).hexdigest():
            raise ValueError("structure map does not bind the original source")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(409, "owned source artifact byte identity is unavailable") from exc
    return source_bytes, suffix


def _inspect_live_source(
    source_bytes: bytes,
    suffix: str,
    *,
    selected_model: int,
    preferred_altloc: str,
) -> tuple[FrustraMPNNSourceInspectionResponse, dict[str, Any]]:
    try:
        live = inspect_and_normalize_structure_bytes(
            source_bytes=source_bytes,
            source_suffix=suffix,
            selected_model=selected_model,
            preferred_altloc=preferred_altloc,
        )
        projection = inspect_structure_map(live["structure_map"])
    except (StructureNormalizationError, SourceResolutionError) as exc:
        raise HTTPException(422, f"live structure selection is invalid: {exc}") from exc
    projection["source_models"] = live["source_models"]
    projection["observed_altlocs"] = live["observed_altlocs"]
    return FrustraMPNNSourceInspectionResponse.model_validate(projection), live["structure_map"]


def _safe_configuration_projection(
    configuration: FrustraMPNNExecutionConfigurationV2
    | FrustraMPNNExecutionConfigurationV3,
) -> FrustraMPNNSafeExecutionConfiguration:
    payload = configuration.model_dump(mode="json", exclude_none=False)
    runtime = dict(payload["runtime"])
    for field in ("configured_sif_path", "executable_path", "checkpoint_path"):
        runtime.pop(field, None)
    payload["runtime"] = runtime
    return FrustraMPNNSafeExecutionConfiguration.model_validate(payload)


def _settings_validation_preview(
    settings: FrustraMPNNRequestedSettings,
    source_bytes: bytes,
    suffix: str,
) -> FrustraMPNNSettingsValidationResponse:
    _, structure_map = _inspect_live_source(
        source_bytes,
        suffix,
        selected_model=settings.source_structure.selected_model_number,
        preferred_altloc=settings.source_structure.preferred_altloc,
    )
    try:
        effective = resolve_effective_settings(settings, structure_map)
        configuration = execution_configuration(effective)
    except SourceResolutionError as exc:
        raise _source_resolution_http_error(exc, settings_request=True) from exc
    return FrustraMPNNSettingsValidationResponse.model_validate({
        "normalized_requested_settings": settings,
        "effective_settings": effective,
        "execution_configuration": _safe_configuration_projection(configuration),
        "hashes": {
            "settings_sha256": effective.settings_sha256,
            "effective_settings_sha256": effective.effective_settings_sha256,
            "configuration_sha256": configuration.configuration_sha256,
            "capability_inventory_byte_sha256": effective.capability_inventory_byte_sha256,
            "structure_map_sha256": effective.resolution_identity.structure_map_sha256,
        },
    })


def _multipart_settings(raw: str) -> FrustraMPNNRequestedSettings:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("settings must be a JSON object")
        return validate_complete_requested_settings(payload)
    except (json.JSONDecodeError, RequestedSettingsPayloadError, ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"invalid complete FrustraMPNN settings: {exc}") from exc


@router.post(
    "/sources/inspect/owned",
    response_model=FrustraMPNNSourceInspectionResponse,
)
async def inspect_owned_source(
    body: FrustraMPNNOwnedSourceInspectionRequest,
    session: AsyncSession = Depends(get_session),
) -> FrustraMPNNSourceInspectionResponse:
    source_bytes, suffix = await _owned_source_bytes(
        job_id=body.job_id,
        invocation_id=body.invocation_id,
        session=session,
    )
    inspection, _ = _inspect_live_source(
        source_bytes,
        suffix,
        selected_model=body.selected_model_number,
        preferred_altloc=body.preferred_altloc,
    )
    return inspection


@router.post(
    "/sources/inspect/upload",
    response_model=FrustraMPNNSourceInspectionResponse,
)
async def inspect_uploaded_source(
    request: Request,
    structure_file: UploadFile = File(...),
    selected_model_number: int = Form(..., ge=1),
    preferred_altloc: str = Form("", pattern=r"^(?:|[A-Za-z0-9])$"),
) -> FrustraMPNNSourceInspectionResponse:
    form = await request.form()
    if set(form) != {"structure_file", "selected_model_number", "preferred_altloc"}:
        raise HTTPException(422, "multipart source inspection contains unknown or missing fields")
    suffix, _ = _source_descriptor(structure_file.filename, structure_file.content_type)
    source_bytes = await _read_bounded_upload(
        structure_file, max_bytes=_MAX_MULTIPART_STRUCTURE_BYTES
    )
    inspection, _ = _inspect_live_source(
        source_bytes,
        suffix,
        selected_model=selected_model_number,
        preferred_altloc=preferred_altloc,
    )
    return inspection


@router.post(
    "/settings/validate/owned",
    response_model=FrustraMPNNSettingsValidationResponse,
)
async def validate_owned_settings(
    body: FrustraMPNNOwnedSettingsValidateRequest,
    session: AsyncSession = Depends(get_session),
) -> FrustraMPNNSettingsValidationResponse:
    source_bytes, suffix = await _owned_source_bytes(
        job_id=body.job_id,
        invocation_id=body.invocation_id,
        session=session,
    )
    return _settings_validation_preview(body.settings, source_bytes, suffix)


@router.post(
    "/settings/validate/upload",
    response_model=FrustraMPNNSettingsValidationResponse,
)
async def validate_uploaded_settings(
    request: Request,
    structure_file: UploadFile = File(...),
    settings: str = Form(...),
) -> FrustraMPNNSettingsValidationResponse:
    form = await request.form()
    if set(form) != {"structure_file", "settings"}:
        raise HTTPException(422, "multipart settings validation contains unknown or missing fields")
    requested = _multipart_settings(settings)
    suffix, _ = _source_descriptor(structure_file.filename, structure_file.content_type)
    source_bytes = await _read_bounded_upload(
        structure_file, max_bytes=_MAX_MULTIPART_STRUCTURE_BYTES
    )
    return _settings_validation_preview(requested, source_bytes, suffix)


@router.post(
    "/jobs/uploads/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=FrustraMPNNChildReceiptResponse,
)
async def analyze_uploaded_structure(
    request: Request,
    pdb_file: UploadFile = File(
        ...,
        description="PDB or mmCIF structure; maximum accepted size is 64 MiB",
    ),
    frustrampnn_settings: Optional[str] = Form(
        None,
        max_length=_MAX_MULTIPART_METADATA_CHARS,
        description="Complete FrustraMPNN settings JSON object; omitted selects canonical defaults",
    ),
    expected_sha256: Optional[str] = Query(
        None,
        pattern=r"^[0-9a-f]{64}$",
        description="Optional expected SHA-256 of the uploaded structure bytes",
    ),
    session: AsyncSession = Depends(get_session),
):
    """Snapshot an upload and return a persisted scheduler-owned child receipt."""
    unknown_query = set(request.query_params) - {"expected_sha256"}
    form = await request.form()
    unknown_form = set(form) - {"pdb_file", "frustrampnn_settings"}
    if unknown_query or unknown_form:
        raise HTTPException(422, "FrustraMPNN launch overrides/unknown fields are forbidden")
    try:
        selection = upload_selection(
            filename=pdb_file.filename or "",
            payload=await _read_bounded_upload(pdb_file),
            expected_sha256=expected_sha256,
        )
        requested_settings = _multipart_requested_settings(form)
        job = await create_child_job(
            session,
            selections=[selection],
            source_parent=None,
            trigger="upload_analyze",
            requested_settings=requested_settings,
        )
        return await _child_job_receipt(session, job)
    except FrustraMPNNChildError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/jobs/{parent_job_id}/workflow-dataset/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=FrustraMPNNStructureDatasetFanoutResponse,
)
async def analyze_parent_workflow_dataset(
    parent_job_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create ordinary scheduler children from one live parent's terminal dataset."""

    parent = await session.get(Job, parent_job_id)
    if parent is None:
        raise HTTPException(404, "source parent Job not found")
    if parent.status != "running" or parent.queue_status != "running":
        raise HTTPException(409, "source parent Job is not authoritatively running")
    authorization = str(request.headers.get("authorization") or "")
    scheme, _, capability = authorization.partition(" ")
    if scheme.lower() != "bearer" or not stage_reporting.token_is_authorized(
        parent.provenance, capability
    ):
        raise HTTPException(403, "invalid parent workflow capability")
    capability_digest = hashlib.sha256(capability.encode("ascii")).hexdigest()
    form = await request.form()
    if set(form) != {
        "structure_files", "dataset_manifest", "frustrampnn_settings",
        "settings_value_origin", "parent_workflow_id",
    }:
        raise HTTPException(422, "workflow dataset multipart fields are not exact")
    workflow_id = form.get("parent_workflow_id")
    if not isinstance(workflow_id, str) or workflow_id != _frustrampnn_consumer_workflow(parent):
        raise HTTPException(422, "workflow dataset parent identity is invalid")
    origin = form.get("settings_value_origin")
    if origin not in {"bms_default", "operator_request"}:
        raise HTTPException(422, "workflow dataset settings origin is invalid")
    raw_manifest = form.get("dataset_manifest")
    raw_settings = form.get("frustrampnn_settings")
    try:
        manifest = json.loads(raw_manifest) if isinstance(raw_manifest, str) else None
        settings_payload = json.loads(raw_settings) if isinstance(raw_settings, str) else None
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"candidates"}
            or not isinstance(manifest["candidates"], list)
            or not manifest["candidates"]
            or canonical_json_bytes(manifest) != raw_manifest.encode("utf-8")
        ):
            raise ValueError("workflow dataset manifest is not canonical")
        requested_settings = validate_complete_requested_settings(settings_payload).model_copy(
            update={"settings_value_origin": origin}
        )
    except (json.JSONDecodeError, RequestedSettingsPayloadError, ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"invalid workflow dataset authority: {exc}") from exc
    uploads = list(form.getlist("structure_files"))
    if len(uploads) != len(manifest["candidates"]):
        raise HTTPException(422, "workflow dataset file cardinality is invalid")
    try:
        selections = [
            workflow_selection(
                filename=str(getattr(upload, "filename", "") or ""),
                payload=await _read_bounded_upload(upload),
                metadata=metadata,
                parent_job_id=parent_job_id,
                parent_workflow_id=workflow_id,
            )
            for metadata, upload in zip(manifest["candidates"], uploads, strict=True)
        ]
        fanout = await _fanout_design_selections(
            session,
            parent=parent,
            selections=selections,
            requested_settings=requested_settings,
            trigger="parent_workflow_terminal_dataset",
            require_running_parent=True,
            workflow_capability_digest=capability_digest,
        )
        return await _fanout_receipt(session, fanout)
    except (FrustraMPNNChildError, StructureDatasetFanoutError) as exc:
        await session.rollback()
        status_code = 409 if "mutation authority" in str(exc) else 422
        raise HTTPException(status_code, str(exc)) from exc


@router.post(
    "/candidates/handoff",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=FrustraMPNNHandoffResponse,
)
async def handoff_external_candidate(
    request: Request,
    structure_file: UploadFile = File(
        ...,
        description="Externally produced PDB or mmCIF structure; maximum accepted size is 64 MiB",
    ),
    candidate_id: str = Form(
        ...,
        min_length=1,
        max_length=_MAX_MULTIPART_IDENTITY_CHARS,
        description="Producer-assigned candidate identity",
    ),
    producer_id: str = Form(
        ...,
        min_length=1,
        max_length=_MAX_MULTIPART_IDENTITY_CHARS,
        description="Identity of the producer that emitted the candidate",
    ),
    parent_job_id: str = Form(
        ...,
        min_length=1,
        max_length=_MAX_MULTIPART_IDENTITY_CHARS,
        description="Persisted parent FrustraMPNN job identity",
    ),
    parent_invocation_id: str = Form(
        ...,
        min_length=1,
        max_length=_MAX_MULTIPART_IDENTITY_CHARS,
        description="Persisted parent FrustraMPNN invocation identity",
    ),
    parent_landscape_sha256: str = Form(
        ...,
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 of the persisted parent landscape authority",
    ),
    guidance_id: Optional[str] = Form(
        None,
        min_length=1,
        max_length=_MAX_MULTIPART_IDENTITY_CHARS,
        description="Optional persisted guidance identity that produced this candidate",
    ),
    nucleotide_edit_set: Optional[str] = Form(
        None,
        max_length=_MAX_MULTIPART_METADATA_CHARS,
        description="Optional JSON array of nucleotide edit objects; omitted means an empty edit set",
    ),
    protein_sequence_sha256: Optional[str] = Form(
        None,
        pattern=r"^[0-9a-f]{64}$",
        description="Optional SHA-256 of the producer's protein sequence",
    ),
    expected_structure_sha256: Optional[str] = Form(
        None,
        pattern=r"^[0-9a-f]{64}$",
        description="Optional expected SHA-256 of the uploaded structure bytes",
    ),
    frustrampnn_settings: Optional[str] = Form(
        None,
        max_length=_MAX_MULTIPART_METADATA_CHARS,
        description="Complete FrustraMPNN settings JSON object; omitted selects canonical defaults",
    ),
    session: AsyncSession = Depends(get_session),
):
    """Accept one producer-owned candidate and queue a fresh FrustraMPNN child."""
    allowed = {
        "structure_file", "candidate_id", "producer_id", "parent_job_id", "parent_invocation_id",
        "parent_landscape_sha256", "guidance_id", "nucleotide_edit_set", "protein_sequence_sha256",
        "expected_structure_sha256", "frustrampnn_settings",
    }
    form = await request.form()
    if set(form) - allowed or set(request.query_params):
        raise HTTPException(422, "FrustraMPNN handoff overrides/unknown fields are forbidden")
    def _form_value(name: str, *, required: bool = True) -> str | None:
        value = form.get(name)
        if value is None or not isinstance(value, str) or (required and not value):
            if required:
                raise HTTPException(422, f"handoff field {name} is required")
            return None
        return value
    try:
        parent_job_identity = _form_value("parent_job_id") or ""
        parent_invocation_identity = _form_value("parent_invocation_id") or ""
        parent_job = await session.get(Job, parent_job_identity)
        if parent_job is None:
            raise HTTPException(404, "handoff parent Job not found")
        parent_result = await _scoped_result(
            parent_invocation_identity,
            parent_job_identity,
            session,
        )
        submitted_parent_landscape_sha256 = _form_value("parent_landscape_sha256") or ""
        try:
            parent_landscape = await load_persisted_landscape(session, parent_result)
        except DerivedPersistenceError as exc:
            raise HTTPException(
                409, "handoff parent landscape authority is unavailable"
            ) from exc
        verified_parent_landscape_sha256 = str(
            parent_landscape["landscape_sha256"]
        )
        if submitted_parent_landscape_sha256 != verified_parent_landscape_sha256:
            raise HTTPException(
                409,
                "handoff parent landscape SHA-256 does not match persisted parent authority",
            )
        submitted_guidance_id = _form_value("guidance_id", required=False)
        verified_guidance_id: str | None = None
        if submitted_guidance_id is not None:
            guidance = await session.get(
                FrustraMPNNGuidancePlan, submitted_guidance_id
            )
            if guidance is None or (
                guidance.source_parent_job_id != parent_job_identity
                or guidance.source_invocation_id != parent_invocation_identity
                or guidance.source_landscape_sha256
                != verified_parent_landscape_sha256
            ):
                raise HTTPException(
                    409,
                    "handoff guidance does not match persisted parent authority",
                )
            verified_guidance_id = guidance.guidance_id
        edit_text = _form_value("nucleotide_edit_set", required=False) or "[]"
        try:
            edit_set = json.loads(edit_text)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, "nucleotide_edit_set must be JSON") from exc
        if not isinstance(edit_set, list) or any(not isinstance(item, dict) for item in edit_set):
            raise HTTPException(422, "nucleotide_edit_set must be a JSON list of objects")
        payload = await _read_bounded_upload(structure_file)
        expected = _form_value("expected_structure_sha256", required=False)
        if expected is not None and hashlib.sha256(payload).hexdigest() != expected:
            raise HTTPException(422, "uploaded handoff structure SHA-256 does not match expected_structure_sha256")
        selection = handoff_selection(
            candidate_id=_form_value("candidate_id") or "",
            producer_id=_form_value("producer_id") or "",
            payload=payload,
            filename=structure_file.filename or "",
            parent_job_id=parent_job_identity,
            parent_invocation_id=parent_invocation_identity,
            parent_landscape_sha256=verified_parent_landscape_sha256,
            guidance_id=verified_guidance_id,
            nucleotide_edit_set=edit_set,
            protein_sequence_sha256=_form_value("protein_sequence_sha256", required=False),
        )
        requested_settings = _multipart_requested_settings(form)
        child = await create_child_job(
            session,
            selections=[selection],
            source_parent=parent_job,
            trigger="external_candidate_handoff",
            requested_settings=requested_settings,
        )
        receipt = await _child_job_receipt(session, child)
        receipt["handoff"] = {
            "parent_landscape_sha256": verified_parent_landscape_sha256,
            "parent_candidate_id": parent_result.candidate_id,
            "guidance_id": verified_guidance_id,
            "producer_id": _form_value("producer_id") or "",
        }
        return receipt
    except HTTPException:
        await session.rollback()
        raise
    except FrustraMPNNChildError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/jobs/{parent_job_id}/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=FrustraMPNNStructureDatasetFanoutResponse,
)
async def analyze_designs(
    parent_job_id: str,
    body: AnalyzeDesignsRequest,
    session: AsyncSession = Depends(get_session),
):
    """Fan out immutable selected Designs as scheduler-visible child Jobs."""
    parent = await session.get(Job, parent_job_id)
    if parent is None:
        raise HTTPException(404, "source parent Job not found")
    expected = {item.design_id: item.source_sha256 for item in body.selections}
    design_ids = [item.design_id for item in body.selections]
    try:
        selections = await design_selections(
            session,
            source_parent=parent,
            design_ids=design_ids,
            expected_sha256=expected,
        )
        fanout = await _fanout_design_selections(
            session,
            parent=parent,
            selections=selections,
            requested_settings=body.frustrampnn_settings,
            trigger="design_analyze",
        )
        return await _fanout_receipt(session, fanout)
    except (FrustraMPNNChildError, StructureDatasetFanoutError) as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/jobs/{child_job_id}/reanalyze",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=FrustraMPNNChildReceiptResponse,
)
async def reanalyze_child(
    child_job_id: str,
    body: ReanalyzeRequest,
    session: AsyncSession = Depends(get_session),
):
    prior = await session.get(Job, child_job_id)
    if prior is None:
        raise HTTPException(404, "FrustraMPNN child Job not found")
    try:
        job = await create_reanalysis_child(
            session,
            prior_child=prior,
            replacement_settings=body.frustrampnn_settings,
        )
        return await _child_job_receipt(session, job)
    except FrustraMPNNChildError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get(
    "/jobs/{child_job_id}/receipt",
    response_model=FrustraMPNNChildReceiptResponse,
)
async def get_child_receipt(
    child_job_id: str,
    session: AsyncSession = Depends(get_session),
):
    child = await session.get(Job, child_job_id)
    if child is None:
        raise HTTPException(404, "FrustraMPNN child Job not found")
    try:
        return await child_receipt(session, child=child)
    except FrustraMPNNChildError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/health")
async def health_check():
    """Report scheduler integration health without probing runtime paths."""
    return {
        "scheduler_backed": True,
        "model_id": "frustrampnn",
        "mode": "analyze",
        "direct_execution": False,
    }


_PHASE4_FIELDS: tuple[Phase4Field, ...] = (
    "settings_sha256",
    "effective_settings_sha256",
    "effective_settings_json",
    "capability_inventory_sha256",
    "statistics_sha256",
    "statistics_json",
    "comparison_compatibility_id",
)
_RESULT_FIELDS = (
    "invocation_id",
    "parent_job_id",
    "parent_workflow_id",
    "candidate_id",
    "design_id",
    "requiredness",
    "source_artifact_id",
    "source_artifact_sha256",
    "request_sha256",
    "manifest_sha256",
    "summary_sha256",
    "created_at",
)
_LANDSCAPE_FIELDS = (
    "id",
    "invocation_id",
    "target_id",
    "entity_instance_id",
    "auth_asym_id",
    "auth_seq_id",
    "insertion_code",
    "sequence_index",
    "wt",
    "mutation_aa",
    "score",
    "score_class",
    "scoreable",
    "status",
    "reason",
)


def _result_authority(result: FrustraMPNNResult) -> dict[str, Any]:
    terminal = dict(result.terminal_result_json or {})
    component_contract_version = terminal.get("component_contract_version")
    authority_version = (
        "v3" if component_contract_version == "3.0"
        else "v2" if component_contract_version == "2.0"
        else "historical_v1"
    )
    values = {field: getattr(result, field) for field in _PHASE4_FIELDS}
    values["statistics_json"] = resolve_json_value(values["statistics_json"])
    if authority_version == "historical_v1":
        values = {field: None for field in _PHASE4_FIELDS}
        missing_fields = list(_PHASE4_FIELDS)
    else:
        missing_fields = [field for field, value in values.items() if value is None]
    core_fields = _PHASE4_FIELDS[:4]
    statistics_fields = _PHASE4_FIELDS[4:]
    core_available = authority_version in {"v2", "v3"} and not any(
        field in missing_fields for field in core_fields
    )
    statistics_available = core_available and not any(
        field in missing_fields for field in statistics_fields
    )
    available = (
        core_available
        if authority_version == "v3"
        else core_available and statistics_available
    )
    return {
        "authority_version": authority_version,
        "availability": available,
        "statistics_available": statistics_available,
        "missing_fields": missing_fields,
        **values,
    }


def _comparison_authority(result: FrustraMPNNResult) -> dict[str, Any]:
    statistics = resolve_json_value(result.statistics_json)
    basis = (
        statistics.get("comparison_compatibility_basis")
        if isinstance(statistics, dict)
        else None
    )
    return {
        "comparison_compatibility_id": result.comparison_compatibility_id,
        "comparison_compatibility_basis": basis,
    }


def _comparison_landscape(
    result: FrustraMPNNResult, landscape: dict[str, Any]
) -> dict[str, Any]:
    return {**landscape, **_comparison_authority(result)}


def _comparison_source_reference(
    result: FrustraMPNNResult,
    landscape: dict[str, Any],
    *,
    role: Literal["reference", "target"],
    target_label: str | None,
) -> dict[str, Any]:
    return {
        "role": role,
        "target_label": target_label,
        "parent_job_id": result.parent_job_id,
        "invocation_id": result.invocation_id,
        "landscape_sha256": landscape["landscape_sha256"],
        "configuration_sha256": (
            landscape.get("execution_configuration_sha256")
            or landscape.get("configuration_sha256")
        ),
    }


def _with_compatibility_metadata(
    payload: dict[str, Any], metadata: dict[str, Any]
) -> dict[str, Any]:
    enriched = dict(payload)
    enriched.pop("comparison_sha256", None)
    enriched.update(metadata)
    enriched["comparison_sha256"] = canonical_sha256(enriched)
    return enriched


_SAFE_RUNTIME_IDENTITY_FIELDS = (
    "runtime_id",
    "sif_name",
    "sif_sha256",
    "image_sha256",
    "executable_sha256",
    "checkpoint_id",
    "checkpoint_sha256",
    "package_version",
    "source_commit",
    "python_version",
    "pytorch_version",
    "image_version",
    "runtime_identity_sha256",
)


def _safe_gpu_provenance(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    physical = value.get("physical_device_id")
    if physical is None:
        physical = value.get("physical_gpu_id")
    if physical is None:
        physical = value.get("assigned_physical_gpu_id")
    if physical is None:
        return None
    return {
        "physical_device_id": str(physical),
        "task_visible_device_index": value.get("task_visible_device_index"),
    }


def _safe_runtime_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        field: value[field]
        for field in _SAFE_RUNTIME_IDENTITY_FIELDS
        if value.get(field) is not None
    }


def _safe_terminal_artifact(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    cardinality = value.get("cardinality")
    projected_cardinality = (
        {
            "kind": cardinality.get("kind"),
            "count": cardinality.get("count"),
        }
        if isinstance(cardinality, Mapping)
        else None
    )
    return {
        "role": value.get("role"),
        "schema_name": value.get("schema_name"),
        "schema_version": value.get("schema_version"),
        "sha256": value.get("sha256"),
        "bytes": value.get("bytes"),
        "cardinality": projected_cardinality,
    }


def _safe_terminal_result(
    result: FrustraMPNNResult,
    terminal: Mapping[str, Any],
    runtime_identity: Mapping[str, Any],
    gpu_provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    source = terminal.get("source_artifact")
    source_projection = (
        {
            "artifact_id": source.get("artifact_id"),
            "sha256": source.get("sha256"),
            "media_type": source.get("media_type"),
            "producer_stage": source.get("producer_stage"),
        }
        if isinstance(source, Mapping)
        else None
    )
    result_payload = terminal.get("result_payload")
    result_projection = (
        {
            "schema_name": result_payload.get("schema_name"),
            "schema_version": result_payload.get("schema_version"),
            "sha256": result_payload.get("sha256"),
        }
        if isinstance(result_payload, Mapping)
        else None
    )
    artifacts = [
        projected
        for item in terminal.get("artifacts", [])
        if (projected := _safe_terminal_artifact(item)) is not None
    ]
    return {
        "schema_name": terminal.get("schema_name"),
        "schema_version": terminal.get("schema_version"),
        "request_sha256": terminal.get("request_sha256") or result.request_sha256,
        "invocation_id": terminal.get("invocation_id") or result.invocation_id,
        "component_id": terminal.get("component_id"),
        "component_contract_version": terminal.get("component_contract_version"),
        "candidate_id": terminal.get("candidate_id") or result.candidate_id,
        "parent_job_id": terminal.get("parent_job_id") or result.parent_job_id,
        "parent_workflow_id": (
            terminal.get("parent_workflow_id") or result.parent_workflow_id
        ),
        "status": terminal.get("status"),
        "failure_class": terminal.get("failure_class"),
        "source_artifact": source_projection,
        "runtime_identity": dict(runtime_identity),
        "artifacts": artifacts,
        "result_payload": result_projection,
        "started_at": terminal.get("started_at"),
        "ended_at": terminal.get("ended_at"),
        "duration_seconds": terminal.get("duration_seconds"),
        "gpu_provenance": gpu_provenance,
    }


def _safe_execution_receipt(
    result: FrustraMPNNResult,
    receipt: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> dict[str, Any]:
    terminal_source = terminal.get("source_artifact")
    terminal_source_sha256 = (
        terminal_source.get("sha256")
        if isinstance(terminal_source, Mapping)
        else None
    )
    projected = {
        "schema_name": receipt.get("schema_name"),
        "schema_version": receipt.get("schema_version"),
        "invocation_id": receipt.get("invocation_id") or result.invocation_id,
        "execution_configuration_sha256": receipt.get(
            "execution_configuration_sha256"
        ),
        "requested_settings_sha256": (
            receipt.get("requested_settings_sha256") or result.settings_sha256
        ),
        "effective_settings_sha256": (
            receipt.get("effective_settings_sha256")
            or result.effective_settings_sha256
        ),
        "runtime_identity_sha256": receipt.get("runtime_identity_sha256"),
        "source_artifact_sha256": (
            receipt.get("source_artifact_sha256")
            or terminal_source_sha256
            or result.source_artifact_sha256
        ),
        "structure_map_sha256": receipt.get("structure_map_sha256"),
        "normalized_pdb_sha256": receipt.get("normalized_pdb_sha256"),
        "command_count": receipt.get("command_count"),
        "gpu_provenance": _safe_gpu_provenance(receipt),
        "started_at": receipt.get("started_at"),
        "ended_at": receipt.get("ended_at"),
        "duration_seconds": receipt.get("duration_seconds"),
    }
    for field in (
        "merged_raw_csv_sha256",
        "landscape_sha256",
        "summary_sha256",
    ):
        if receipt.get(field) is not None:
            projected[field] = receipt[field]
    return projected


def _result_payload(result: FrustraMPNNResult, *, detail: bool = False) -> dict[str, Any]:
    payload = {name: getattr(result, name) for name in _RESULT_FIELDS}
    parent_metadata = result.parent_metadata_json if isinstance(result.parent_metadata_json, Mapping) else {}
    raw_operator_label = result.candidate_id
    for key in ("operator_label", "display_label", "name"):
        candidate_label = parent_metadata.get(key)
        if isinstance(candidate_label, str) and candidate_label.strip():
            raw_operator_label = candidate_label
            break
    payload["operator_label"] = str(raw_operator_label).strip()[:160]
    payload["source_identity"] = {
        "design_id": result.design_id,
        "artifact_id": result.source_artifact_id,
        "artifact_sha256": result.source_artifact_sha256,
        "candidate_id": result.candidate_id,
    }
    payload.update(_result_authority(result))
    payload["reopen_destination"] = {
        "surface": "frustrampnn-workbench",
        "params": {"job_id": result.parent_job_id, "invocation_id": result.invocation_id},
    }
    terminal = (
        result.terminal_result_json
        if isinstance(result.terminal_result_json, Mapping)
        else {}
    )
    payload["status"] = terminal["status"]
    payload["component_contract_version"] = terminal["component_contract_version"]
    execution_receipt = (
        result.runtime_identity_json
        if isinstance(result.runtime_identity_json, Mapping)
        else {}
    )
    if terminal.get("component_contract_version") == "2.0":
        runtime_identity = _safe_runtime_identity(execution_receipt)
    else:
        runtime_identity = _safe_runtime_identity(terminal.get("runtime_identity"))
    runtime_identity_sha256 = runtime_identity.get("runtime_identity_sha256")
    payload["runtime_identity"] = runtime_identity
    payload["runtime_identity_sha256"] = runtime_identity_sha256
    gpu_provenance = _safe_gpu_provenance(result.assigned_gpu_json)
    payload["gpu_provenance"] = gpu_provenance
    payload["failure_class"] = terminal.get("failure_class")
    if detail:
        payload["summary"] = dict(result.summary_json)
        payload["terminal_result"] = _safe_terminal_result(
            result,
            terminal,
            runtime_identity,
            gpu_provenance,
        )
        payload["execution_receipt"] = (
            _safe_execution_receipt(result, execution_receipt, terminal)
            if terminal.get("component_contract_version") == "2.0"
            else None
        )
    return payload


def _artifact_payload(artifact: FrustraMPNNArtifact) -> dict[str, Any]:
    metadata = dict(artifact.metadata_json)
    return {
        "artifact_id": artifact.artifact_id,
        "role": artifact.role,
        "content_sha256": artifact.content_sha256,
        "size_bytes": artifact.size_bytes,
        "media_type": artifact.media_type,
        "schema_name": metadata.get("schema_name"),
        "schema_version": metadata.get("schema_version"),
        "cardinality": metadata.get("cardinality"),
        "download_url": (
            f"/api/frustrampnn/artifacts/{artifact.artifact_id}"
            f"?job_id={artifact.parent_job_id}"
        ),
    }


async def _scoped_result(
    invocation_id: str,
    job_id: str,
    session: AsyncSession,
) -> FrustraMPNNResult:
    result = await session.get(FrustraMPNNResult, (job_id, invocation_id))
    if result is None:
        raise HTTPException(status_code=404, detail="FrustraMPNN result not found")
    return result


@router.get(
    "/jobs/{job_id}/results",
    response_model=FrustraMPNNResultListResponse,
    response_model_exclude_unset=True,
)
async def list_results(
    job_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    candidate_id: str | None = Query(None),
    design_id: str | None = Query(None),
    parent_workflow_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    filters = [FrustraMPNNResult.parent_job_id == job_id]
    if candidate_id is not None:
        filters.append(FrustraMPNNResult.candidate_id == candidate_id)
    if design_id is not None:
        filters.append(FrustraMPNNResult.design_id == design_id)
    if parent_workflow_id is not None:
        filters.append(FrustraMPNNResult.parent_workflow_id == parent_workflow_id)
    total = int(
        (await session.execute(select(func.count()).select_from(FrustraMPNNResult).where(*filters))).scalar_one()
    )
    rows = (
        await session.execute(
            select(FrustraMPNNResult)
            .where(*filters)
            .order_by(FrustraMPNNResult.created_at.asc(), FrustraMPNNResult.invocation_id.asc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return {
        "items": [_result_payload(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/analytics/points",
    response_model=(
        ResultAnalyticsPageResponse
        | ResidueAnalyticsPageResponse
        | MutationAnalyticsPageResponse
    ),
)
async def analytics_points(
    level: Literal["result", "residue", "mutation"] = Query("result"),
    dataset_ids: Optional[str] = Query(None, max_length=1000),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    """Return bounded machine-described FrustraMPNN points across workflow datasets."""
    return await multidimensional_points(
        session,
        level=level,
        dataset_ids=parse_dataset_ids(dataset_ids),
        limit=limit,
        offset=offset,
    )


def _comparison_payload(model: FrustraMPNNComparison) -> dict[str, Any]:
    payload = dict(resolve_json_value(model.payload_json))
    payload["comparison_id"] = model.comparison_id
    payload["persisted"] = True
    payload["created_at"] = model.created_at
    payload["reference"] = {
        "parent_job_id": model.reference_parent_job_id,
        "invocation_id": model.reference_invocation_id,
    }
    payload["target"] = {
        "parent_job_id": model.target_parent_job_id,
        "invocation_id": model.target_invocation_id,
    }
    return payload


def _guidance_payload(model: FrustraMPNNGuidancePlan) -> dict[str, Any]:
    payload = dict(model.payload_json)
    payload["guidance_id"] = model.guidance_id
    payload["persisted"] = True
    payload["created_at"] = model.created_at
    return payload


@router.post(
    "/comparisons",
    status_code=status.HTTP_201_CREATED,
    response_model=ComparisonCreateResponse,
)
async def create_comparison(
    body: ComparisonCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        reference_result = await _scoped_result(body.reference_invocation_id, body.reference_job_id, session)
        target_result = await _scoped_result(body.target_invocation_id, body.target_job_id, session)
        reference_landscape = _comparison_landscape(
            reference_result,
            await load_persisted_landscape(session, reference_result),
        )
        target_landscape = _comparison_landscape(
            target_result,
            await load_persisted_landscape(session, target_result),
        )
        comparison_id = "cmp-" + canonical_sha256([
            reference_landscape["landscape_sha256"], target_landscape["landscape_sha256"],
        ])[:32]
        metadata = comparison_compatibility(
            reference_landscape,
            target_landscape,
            allow_incompatible=body.allow_incompatible,
        )
        payload = compare_landscapes(
            reference_landscape,
            target_landscape,
            comparison_id=comparison_id,
            allow_incompatible=body.allow_incompatible,
        )
        payload = _with_compatibility_metadata(payload, metadata)
        stored = await persist_comparison(
            session, payload, reference_result=reference_result, target_result=target_result,
        )
        await session.commit()
        return _comparison_payload(stored)
    except ComparisonCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=exc.metadata) from exc
    except (ComparisonValidationError, DerivedPersistenceError) as exc:
        await session.rollback()
        code = 409 if "conflict" in str(exc).lower() else 422
        raise HTTPException(code, str(exc)) from exc


@router.post(
    "/comparisons/multi",
    status_code=status.HTTP_201_CREATED,
    response_model=MultiComparisonCreateResponse,
)
async def create_multi_comparison(
    body: MultiComparisonCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        reference_result = await _scoped_result(body.reference_invocation_id, body.reference_job_id, session)
        target_results = [
            await _scoped_result(item.target_invocation_id, item.target_job_id, session)
            for item in body.targets
        ]
        reference_landscape = _comparison_landscape(
            reference_result,
            await load_persisted_landscape(session, reference_result),
        )
        target_landscapes = [
            _comparison_landscape(
                result,
                await load_persisted_landscape(session, result),
            )
            for result in target_results
        ]
        comparison_id = "cmp-" + canonical_sha256([
            reference_landscape["landscape_sha256"],
            *[landscape["landscape_sha256"] for landscape in target_landscapes],
        ])[:32]
        metadata = comparison_set_compatibility(
            reference_landscape,
            target_landscapes,
            allow_incompatible=body.allow_incompatible,
        )
        payload = compare_landscape_set(
            reference_landscape,
            target_landscapes,
            comparison_id=comparison_id,
            allow_incompatible=body.allow_incompatible,
        )
        payload["source_result_references"] = [
            _comparison_source_reference(
                reference_result,
                reference_landscape,
                role="reference",
                target_label=None,
            ),
            *[
                _comparison_source_reference(
                    result,
                    landscape,
                    role="target",
                    target_label=f"target-{index:04d}",
                )
                for index, (result, landscape) in enumerate(
                    zip(target_results, target_landscapes), start=1
                )
            ],
        ]
        payload = _with_compatibility_metadata(payload, metadata)
        stored = await persist_comparison(
            session, payload, reference_result=reference_result, target_result=target_results[0],
        )
        await session.commit()
        return _comparison_payload(stored)
    except ComparisonCompatibilityError as exc:
        raise HTTPException(status_code=409, detail=exc.metadata) from exc
    except (ComparisonValidationError, DerivedPersistenceError) as exc:
        await session.rollback()
        code = 409 if "conflict" in str(exc).lower() else 422
        raise HTTPException(code, str(exc)) from exc


@router.get(
    "/comparisons/{comparison_id}",
    response_model=ComparisonCreateResponse | MultiComparisonCreateResponse,
)
async def get_comparison(comparison_id: str, session: AsyncSession = Depends(get_session)):
    model = await session.get(FrustraMPNNComparison, comparison_id)
    if model is None:
        raise HTTPException(404, "FrustraMPNN comparison not found")
    return _comparison_payload(model)


@router.get(
    "/comparisons/{comparison_id}/rows",
    response_model=ComparisonRowsResponse,
)
async def comparison_rows(
    comparison_id: str,
    limit: int = Query(200, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    model = await session.get(FrustraMPNNComparison, comparison_id)
    if model is None:
        raise HTTPException(404, "FrustraMPNN comparison not found")
    total = int((await session.execute(
        select(func.count()).select_from(FrustraMPNNComparisonRow).where(
            FrustraMPNNComparisonRow.comparison_id == comparison_id,
        )
    )).scalar_one())
    rows = (await session.execute(
        select(FrustraMPNNComparisonRow)
        .where(FrustraMPNNComparisonRow.comparison_id == comparison_id)
        .order_by(FrustraMPNNComparisonRow.row_index.asc())
        .offset(offset)
        .limit(limit)
    )).scalars().all()
    return {
        "comparison_id": comparison_id,
        "items": [dict(resolve_json_value(row.row_json)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(rows) if offset + len(rows) < total else None,
    }


@router.post(
    "/guidance",
    status_code=status.HTTP_201_CREATED,
    response_model=GuidanceResponse,
)
async def create_guidance(
    body: GuidanceCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        source_result = await _scoped_result(body.source_invocation_id, body.source_job_id, session)
        landscape = await load_persisted_landscape(session, source_result)
        region = body.region.model_dump(mode="json", exclude_none=True, exclude_unset=True)
        objective = body.objective.model_dump(mode="json", exclude_none=True, exclude_unset=True)
        constraints = body.constraints.model_dump(
            mode="json", exclude_none=True, exclude_unset=True
        )
        ranking = body.ranking.model_dump(mode="json", exclude_none=True, exclude_unset=True)
        guidance_id = body.guidance_id or "gdp-" + canonical_sha256({
            "source_landscape_sha256": landscape["landscape_sha256"],
            "region": region,
            "objective": objective,
            "constraints": constraints,
            "ranking": ranking,
            "rationale": body.rationale,
        })[:32]
        payload = build_guidance_plan(
            landscape=landscape,
            region=region,
            objective=objective,
            constraints=constraints,
            ranking=ranking,
            rationale=body.rationale,
            guidance_id=guidance_id,
        )
        stored = await persist_guidance_plan(session, payload, source_result=source_result)
        await session.commit()
        return _guidance_payload(stored)
    except (GuidanceValidationError, DerivedPersistenceError) as exc:
        await session.rollback()
        code = 409 if "conflict" in str(exc).lower() else 422
        raise HTTPException(code, str(exc)) from exc


@router.get("/guidance/{guidance_id}", response_model=GuidanceResponse)
async def get_guidance(guidance_id: str, session: AsyncSession = Depends(get_session)):
    model = await session.get(FrustraMPNNGuidancePlan, guidance_id)
    if model is None:
        raise HTTPException(404, "FrustraMPNN guidance plan not found")
    return _guidance_payload(model)


_STATISTICS_QUERY_ARRAYS = {
    "residue": "per_residue",
    "mutation_aa": "per_mutation_amino_acid",
    "chain": "per_chain",
    "entity": "per_entity",
}
_STATISTICS_QUERY_KEYS = {
    "residue": (
        "entity_instance_id",
        "source_entity_id",
        "label_asym_id",
        "auth_asym_id",
        "auth_seq_id",
        "insertion_code",
        "sequence_index",
        "wt",
        "pdb_chain_id",
        "model_position",
    ),
    "mutation_aa": ("mutation_aa",),
    "chain": (
        "entity_instance_id",
        "source_entity_id",
        "label_asym_id",
        "auth_asym_id",
        "pdb_chain_id",
    ),
    "entity": ("entity_instance_id", "source_entity_id", "label_asym_id"),
}


def _statistics_query_common(
    dataset: dict[str, str], level: str, *, available: bool, reason: str | None
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "level": level,
        "availability": available,
        "unavailable_reason": reason,
        "distribution": None,
        "native_distribution": None,
        "non_native_distribution": None,
        "class_burden": None,
        "native_score": None,
        "native_class": None,
        "support": None,
    }


def _statistics_query_unavailable(
    dataset: dict[str, str], level: str, reason: str
) -> dict[str, Any]:
    return {
        **_statistics_query_common(dataset, level, available=False, reason=reason),
        "key": {} if level == "overview" else None,
    }


def _statistics_query_projection(
    dataset: dict[str, str],
    level: str,
    statistics: dict[str, Any],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    common = _statistics_query_common(dataset, level, available=True, reason=None)
    if level == "overview":
        distributions = statistics.get("distributions")
        burdens = statistics.get("class_burden")
        if not isinstance(distributions, dict) or not isinstance(burdens, dict):
            return [_statistics_query_unavailable(
                dataset, level, "persisted_statistics_projection_unavailable"
            )]
        return [{
            **common,
            "key": {},
            "support": statistics.get("support"),
            "distribution": distributions.get("overall"),
            "native_distribution": distributions.get("native"),
            "non_native_distribution": distributions.get("non_native"),
            "class_burden": burdens.get("all"),
        }]

    persisted_rows = statistics.get(_STATISTICS_QUERY_ARRAYS[level])
    if not isinstance(persisted_rows, list):
        return [_statistics_query_unavailable(
            dataset, level, "persisted_statistics_projection_unavailable"
        )]
    items: list[dict[str, Any]] = []
    for persisted in persisted_rows:
        if not isinstance(persisted, dict):
            continue
        if any(persisted.get(field) != value for field, value in filters.items()):
            continue
        item = {
            **common,
            "key": {
                field: persisted.get(field)
                for field in _STATISTICS_QUERY_KEYS[level]
            },
        }
        if level == "residue":
            item.update({
                "distribution": persisted.get("all"),
                "non_native_distribution": persisted.get("non_native"),
                "class_burden": persisted.get("alternative_class_burden"),
                "native_score": persisted.get("native_score"),
                "native_class": persisted.get("native_class"),
            })
        elif level == "mutation_aa":
            item.update({
                "distribution": persisted.get("distribution"),
                "class_burden": persisted.get("class_composition"),
            })
        else:
            item.update({
                "support": persisted.get("support"),
                "distribution": persisted.get("all"),
                "native_distribution": persisted.get("native"),
                "non_native_distribution": persisted.get("non_native"),
            })
        items.append(item)
    return items


@router.post(
    "/statistics/query",
    response_model=StatisticsQueryResponse,
)
async def statistics_query(
    body: StatisticsQueryRequest,
    session: AsyncSession = Depends(get_session),
) -> StatisticsQueryResponse:
    """Project bounded rows from exact persisted statistics JSON only."""
    expanded: list[dict[str, Any]] = []
    filters = body.filters.model_dump(exclude_none=True)
    for requested in body.datasets:
        dataset = requested.model_dump()
        result = await session.get(
            FrustraMPNNResult,
            (requested.parent_job_id, requested.invocation_id),
        )
        if result is None:
            raise HTTPException(status_code=404, detail="FrustraMPNN result not found")
        authority = _result_authority(result)
        if authority["authority_version"] == "historical_v1":
            expanded.append(_statistics_query_unavailable(
                dataset,
                body.level,
                "historical_v1_statistics_unavailable",
            ))
            continue
        if not authority["availability"] or not isinstance(
            authority["statistics_json"], dict
        ):
            expanded.append(_statistics_query_unavailable(
                dataset,
                body.level,
                "current_statistics_authority_incomplete",
            ))
            continue
        expanded.extend(_statistics_query_projection(
            dataset,
            body.level,
            authority["statistics_json"],
            filters,
        ))

    total = len(expanded)
    items = expanded[body.offset : body.offset + body.limit]
    next_offset = body.offset + len(items)
    return StatisticsQueryResponse.model_validate({
        "items": items,
        "total": total,
        "limit": body.limit,
        "offset": body.offset,
        "next_offset": next_offset if next_offset < total else None,
    })


@router.get(
    "/results/{parent_job_id}/{invocation_id}/statistics/analysis",
    response_model=FrustraMPNNStatisticsAnalysisResponse,
)
async def get_result_statistics_analysis(
    parent_job_id: str,
    invocation_id: str,
    session: AsyncSession = Depends(get_session),
) -> FrustraMPNNStatisticsAnalysisResponse:
    child = (
        await session.execute(
            select(FrustraMPNNStatisticsAnalysis).where(
                FrustraMPNNStatisticsAnalysis.parent_job_id == parent_job_id,
                FrustraMPNNStatisticsAnalysis.invocation_id == invocation_id,
            )
        )
    ).scalar_one_or_none()
    if child is None:
        raise HTTPException(status_code=404, detail="statistics analysis child not found")
    return FrustraMPNNStatisticsAnalysisResponse(
        analysis_id=child.analysis_id,
        parent_job_id=child.parent_job_id,
        invocation_id=child.invocation_id,
        state=child.state,
        attempt_count=child.attempt_count,
        core_artifact_id=child.core_artifact_id,
        core_landscape_sha256=child.core_landscape_sha256,
        core_manifest_sha256=child.core_manifest_sha256,
        formula_version=child.formula_version,
        policy_version=child.policy_version,
        package_version=child.package_version,
        schema_version=child.schema_version,
        artifact_sha256=child.artifact_sha256,
        statistics_sha256=child.statistics_sha256,
        diagnostic=child.diagnostic,
    )


@router.post(
    "/results/{parent_job_id}/{invocation_id}/statistics/retry",
    response_model=FrustraMPNNStatisticsAnalysisResponse,
)
async def retry_result_statistics_analysis(
    parent_job_id: str,
    invocation_id: str,
    session: AsyncSession = Depends(get_session),
) -> FrustraMPNNStatisticsAnalysisResponse:
    child = (
        await session.execute(
            select(FrustraMPNNStatisticsAnalysis).where(
                FrustraMPNNStatisticsAnalysis.parent_job_id == parent_job_id,
                FrustraMPNNStatisticsAnalysis.invocation_id == invocation_id,
            )
        )
    ).scalar_one_or_none()
    if child is None:
        raise HTTPException(status_code=404, detail="statistics analysis child not found")
    try:
        child = await retry_statistics_child(
            session,
            analysis_id=child.analysis_id,
        )
        await session.commit()
    except FrustraMPNNStatisticsJobError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FrustraMPNNStatisticsAnalysisResponse(
        analysis_id=child.analysis_id,
        parent_job_id=child.parent_job_id,
        invocation_id=child.invocation_id,
        state=child.state,
        attempt_count=child.attempt_count,
        core_artifact_id=child.core_artifact_id,
        core_landscape_sha256=child.core_landscape_sha256,
        core_manifest_sha256=child.core_manifest_sha256,
        formula_version=child.formula_version,
        policy_version=child.policy_version,
        package_version=child.package_version,
        schema_version=child.schema_version,
        artifact_sha256=child.artifact_sha256,
        statistics_sha256=child.statistics_sha256,
        diagnostic=child.diagnostic,
    )


@router.get(
    "/results/{invocation_id}/statistics",
    response_model=FrustraMPNNStatisticsResponse,
)
async def result_statistics(
    invocation_id: str,
    job_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> FrustraMPNNStatisticsResponse:
    result = await _scoped_result(invocation_id, job_id, session)
    authority = _result_authority(result)
    available = bool(authority["availability"])
    return FrustraMPNNStatisticsResponse.model_validate(
        {
            "result_id": result.invocation_id,
            "parent_job_id": result.parent_job_id,
            "candidate_id": result.candidate_id,
            "invocation_id": result.invocation_id,
            "authority_version": authority["authority_version"],
            "availability": available,
            "missing_fields": authority["missing_fields"],
            "settings_sha256": authority["settings_sha256"],
            "effective_settings_sha256": authority["effective_settings_sha256"],
            "effective_settings_json": authority["effective_settings_json"],
            "capability_inventory_sha256": authority[
                "capability_inventory_sha256"
            ],
            "statistics_sha256": authority["statistics_sha256"],
            "statistics_json": authority["statistics_json"],
            "comparison_compatibility_id": authority[
                "comparison_compatibility_id"
            ],
            "statistics": authority["statistics_json"] if available else None,
        }
    )


@router.get(
    "/results/{invocation_id}",
    response_model=FrustraMPNNResultDetailResponse,
    response_model_exclude_unset=True,
)
async def result_detail(
    invocation_id: str,
    job_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    return _result_payload(await _scoped_result(invocation_id, job_id, session), detail=True)


@router.get(
    "/results/{invocation_id}/landscape",
    response_model=FrustraMPNNLandscapePageResponse,
    response_model_exclude_unset=True,
)
async def result_landscape(
    invocation_id: str,
    job_id: str = Query(...),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=1_000_000),
    target_id: str | None = Query(None),
    entity_instance_id: str | None = Query(None),
    auth_asym_id: str | None = Query(None),
    auth_seq_id: str | None = Query(None),
    insertion_code: str | None = Query(None),
    sequence_index: int | None = Query(None),
    mutation_aa: str | None = Query(None, min_length=1, max_length=1),
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):
    result = await _scoped_result(invocation_id, job_id, session)
    page = await persisted_landscape_page(
        session,
        job_id,
        invocation_id,
        limit=limit,
        offset=offset,
        target_id=target_id,
        entity_instance_id=entity_instance_id,
        auth_asym_id=auth_asym_id,
        auth_seq_id=auth_seq_id,
        insertion_code=insertion_code,
        sequence_index=sequence_index,
        mutation_aa=mutation_aa,
        status=status,
    )
    total = int(page["total"])
    rows = page["items"]
    items = []
    for row in rows:
        stored = dict(row["row"])
        residue = stored.get("residue")
        residue_identity = residue if isinstance(residue, dict) else {}
        items.append({
            **{name: row[name] for name in _LANDSCAPE_FIELDS},
            "candidate_id": result.candidate_id,
            "source_entity_id": residue_identity.get("source_entity_id"),
            "label_asym_id": residue_identity.get("label_asym_id"),
            "auth_seq_id": int(row["auth_seq_id"]),
            "pdb_chain_id": residue_identity.get("pdb_chain_id"),
            "model_position": residue_identity.get("model_position"),
            "class": row["score_class"],
            "native": row["mutation_aa"] == row["wt"],
            "provenance": dict(row["provenance"]),
            "residue": dict(residue) if isinstance(residue, dict) else None,
        })
    return {
        "items": items,
        "candidate_id": result.candidate_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(items) if offset + len(items) < total else None,
    }


@router.get(
    "/results/{invocation_id}/artifacts",
    response_model=FrustraMPNNArtifactListResponse,
    response_model_exclude_unset=True,
)
async def result_artifacts(
    invocation_id: str,
    job_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    await _scoped_result(invocation_id, job_id, session)
    rows = (
        await session.execute(
            select(FrustraMPNNArtifact)
            .where(
                FrustraMPNNArtifact.parent_job_id == job_id,
                FrustraMPNNArtifact.invocation_id == invocation_id,
            )
            .order_by(FrustraMPNNArtifact.role.asc(), FrustraMPNNArtifact.artifact_id.asc())
        )
    ).scalars().all()
    return {"items": [_artifact_payload(row) for row in rows], "total": len(rows)}


def _artifact_byte_range(value: str | None, size_bytes: int) -> tuple[int, int, int]:
    if not value:
        return (0, size_bytes - 1, 200)
    try:
        unit, bounds = value.split("=", 1)
        left, right = bounds.split("-", 1)
        if unit != "bytes" or "," in bounds or size_bytes <= 0:
            raise ValueError
        if left:
            start = int(left)
            if start < 0 or start >= size_bytes:
                raise ValueError
            end = min(int(right), size_bytes - 1) if right else size_bytes - 1
            if end < start:
                raise ValueError
        else:
            suffix = int(right)
            if suffix <= 0:
                raise ValueError
            start = max(0, size_bytes - suffix)
            end = size_bytes - 1
        return start, end, 206
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=416,
            detail="invalid artifact byte range",
            headers={"Content-Range": f"bytes */{size_bytes}"},
        )


async def _verified_artifact_snapshot(
    artifact: FrustraMPNNArtifact,
    session: AsyncSession,
):
    siblings = (
        await session.execute(
            select(FrustraMPNNArtifact.storage_path).where(
                FrustraMPNNArtifact.parent_job_id == artifact.parent_job_id,
                FrustraMPNNArtifact.invocation_id == artifact.invocation_id,
            )
        )
    ).scalars().all()
    relative = Path(artifact.relative_path)
    storage = Path(artifact.storage_path).absolute()
    roots = {str(Path(path).absolute().parent) for path in siblings}
    if (
        relative.is_absolute()
        or len(relative.parts) != 1
        or len(roots) != 1
        or storage != Path(next(iter(roots))) / relative
    ):
        raise OSError("artifact storage authority is inconsistent")

    root_fd = -1
    descriptor = -1
    snapshot = tempfile.TemporaryFile(mode="w+b")
    try:
        root_fd = os.open(
            next(iter(roots)),
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != artifact.size_bytes:
            raise OSError("artifact is not the registered regular file")
        digest = hashlib.sha256()
        remaining = artifact.size_bytes
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise OSError("artifact is truncated")
            digest.update(chunk)
            snapshot.write(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise OSError("artifact exceeds its registered size")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after or digest.hexdigest() != artifact.content_sha256:
            raise OSError("artifact byte identity changed")
        snapshot.seek(0)
        return snapshot
    except Exception:
        snapshot.close()
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if root_fd >= 0:
            os.close(root_fd)


@router.get("/artifacts/{artifact_id}")
async def download_artifact(
    artifact_id: str,
    request: Request,
    job_id: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    artifact = await session.get(FrustraMPNNArtifact, artifact_id)
    if artifact is None or artifact.parent_job_id != job_id:
        raise HTTPException(status_code=404, detail="FrustraMPNN artifact not found")
    await _scoped_result(artifact.invocation_id, job_id, session)
    try:
        snapshot = await _verified_artifact_snapshot(artifact, session)
    except (OSError, ValueError):
        raise HTTPException(status_code=409, detail="artifact byte identity is unavailable")
    try:
        start, end, status_code = _artifact_byte_range(
            request.headers.get("range"), artifact.size_bytes
        )
    except HTTPException:
        snapshot.close()
        raise
    snapshot.seek(start)
    remaining = max(0, end - start + 1)

    def content():
        nonlocal remaining
        try:
            while remaining:
                chunk = snapshot.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RuntimeError("verified artifact snapshot became truncated")
                remaining -= len(chunk)
                yield chunk
        finally:
            snapshot.close()

    safe_name = Path(artifact.relative_path).name.replace('"', "")
    headers = {
        "ETag": f'"{artifact.content_sha256}"',
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "Content-Length": str(max(0, end - start + 1)),
        "Content-Disposition": f'attachment; filename="{safe_name}"',
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{artifact.size_bytes}"
    return StreamingResponse(
        content(),
        status_code=status_code,
        media_type=artifact.media_type,
        headers=headers,
    )


async def _review_authority(
    job_id: str,
    payload: FrustraMPNNSavedReviewWrite,
    actor: str,
    session: AsyncSession,
) -> tuple[FrustraMPNNResult, FrustraMPNNArtifact, dict[str, Any], str]:
    reference = payload.result_references[0]
    if reference.parent_job_id != job_id:
        raise HTTPException(status_code=422, detail="saved review result reference is not persisted for this job")
    result = await session.get(FrustraMPNNResult, (job_id, reference.invocation_id))
    if result is None:
        raise HTTPException(status_code=422, detail="saved review result reference is not persisted for this job")
    if not result.effective_settings_sha256:
        raise HTTPException(status_code=422, detail="new saved reviews require persisted effective settings identity")
    landscape = (await session.execute(select(FrustraMPNNArtifact).where(
        FrustraMPNNArtifact.parent_job_id == job_id,
        FrustraMPNNArtifact.invocation_id == reference.invocation_id,
        FrustraMPNNArtifact.role == "landscape",
    ))).scalar_one_or_none()
    if landscape is None:
        raise HTTPException(status_code=422, detail="new saved reviews require persisted landscape identity")
    if payload.supersedes_review_id:
        previous = await _saved_review(job_id, payload.supersedes_review_id, actor, session)
        if previous.invocation_id != reference.invocation_id:
            raise HTTPException(status_code=422, detail="review revision must preserve invocation authority")
    authority = {
        "schema_name": "frustrampnn_review_revision",
        "schema_version": 1,
        "parent_job_id": job_id,
        "invocation_id": reference.invocation_id,
        "landscape_sha256": landscape.content_sha256,
        "effective_settings_sha256": result.effective_settings_sha256,
        "supersedes_review_id": payload.supersedes_review_id,
        "title": payload.title,
        "notes": payload.notes,
        "result_references": [reference.model_dump(mode="json")],
        "selected_residues": [item.model_dump(mode="json") for item in payload.selected_residues],
        "filters": payload.filters,
        "viewer_state": payload.viewer_state.model_dump(mode="json", exclude_none=True),
        "tags": payload.tags,
    }
    return result, landscape, authority, hashlib.sha256(canonical_json_bytes(authority)).hexdigest()


def _serialize_saved_review(review: FrustraMPNNReview) -> dict[str, Any]:
    return {
        "schema_name": "frustrampnn_saved_review",
        "schema_version": 1,
        "review_id": review.review_id,
        "parent_job_id": review.parent_job_id,
        "invocation_id": review.invocation_id,
        "landscape_sha256": review.landscape_sha256,
        "effective_settings_sha256": review.effective_settings_sha256,
        "review_sha256": review.review_sha256,
        "supersedes_review_id": review.supersedes_review_id,
        "title": review.title,
        "notes": review.notes,
        "result_references": review.result_references_json,
        "selected_residues": review.selected_residues_json,
        "filters": review.filters_json,
        "viewer_state": review.viewer_state_json,
        "tags": review.tags_json,
        "created_at": review.created_at.isoformat(),
    }


async def _saved_review(job_id: str, review_id: str, actor: str, session: AsyncSession) -> FrustraMPNNReview:
    review = await session.get(FrustraMPNNReview, review_id)
    if review is None or review.parent_job_id != job_id or review.created_by != actor:
        raise HTTPException(status_code=404, detail="FrustraMPNN saved review not found")
    return review


@router.post("/jobs/{job_id}/reviews", status_code=status.HTTP_201_CREATED)
async def create_saved_review(
    job_id: str,
    payload: FrustraMPNNSavedReviewWrite,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    actor = _principal(request)
    _result, landscape, authority, review_sha256 = await _review_authority(job_id, payload, actor, session)
    existing = (await session.execute(select(FrustraMPNNReview).where(
        FrustraMPNNReview.review_sha256 == review_sha256,
        FrustraMPNNReview.created_by == actor,
    ))).scalar_one_or_none()
    if existing is not None:
        return _serialize_saved_review(existing)
    review = FrustraMPNNReview(
        review_id=str(uuid4()), parent_job_id=job_id,
        invocation_id=authority["invocation_id"], landscape_sha256=landscape.content_sha256,
        effective_settings_sha256=authority["effective_settings_sha256"], review_sha256=review_sha256,
        supersedes_review_id=payload.supersedes_review_id, created_by=actor,
        title=payload.title, notes=payload.notes,
        result_references_json=authority["result_references"],
        selected_residues_json=authority["selected_residues"], filters_json=payload.filters,
        viewer_state_json=authority["viewer_state"], tags_json=payload.tags,
        created_at=datetime.utcnow(),
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return _serialize_saved_review(review)


@router.get("/jobs/{job_id}/reviews")
async def list_saved_reviews(
    job_id: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    actor = _principal(request)
    rows = (
        await session.execute(
            select(FrustraMPNNReview)
            .where(
                FrustraMPNNReview.parent_job_id == job_id,
                FrustraMPNNReview.created_by == actor,
            )
            .order_by(FrustraMPNNReview.created_at.desc(), FrustraMPNNReview.review_id.asc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return {
        "schema_name": "frustrampnn_saved_review_list",
        "schema_version": 1,
        "items": [_serialize_saved_review(row) for row in rows],
        "next_offset": offset + len(rows) if len(rows) == limit else None,
    }



_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_CAPTURE_BYTES = 10 * 1024 * 1024


@router.post("/jobs/{job_id}/reviews/{review_id}/captures", status_code=201)
async def create_review_capture(
    job_id: str,
    review_id: str,
    request: Request,
    expected_sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    actor = _principal(request)
    review = await _saved_review(job_id, review_id, actor, session)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "image/png":
        raise HTTPException(status_code=415, detail="FrustraMPNN review capture must be image/png")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="FrustraMPNN review capture content length is invalid") from exc
        if declared_size < 1 or declared_size > _MAX_CAPTURE_BYTES:
            raise HTTPException(status_code=413, detail="FrustraMPNN review capture exceeds the byte limit")
    chunks: list[bytes] = []
    size_bytes = 0
    async for chunk in request.stream():
        size_bytes += len(chunk)
        if size_bytes > _MAX_CAPTURE_BYTES:
            raise HTTPException(status_code=413, detail="FrustraMPNN review capture exceeds the byte limit")
        chunks.append(chunk)
    payload = b"".join(chunks)
    if not payload or not payload.startswith(_PNG_SIGNATURE):
        raise HTTPException(status_code=422, detail="FrustraMPNN review capture bytes are invalid")
    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "PNG":
                raise HTTPException(status_code=422, detail="FrustraMPNN review capture must decode as PNG")
            image.verify()
        with Image.open(BytesIO(payload)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise HTTPException(status_code=422, detail="FrustraMPNN review capture must decode as PNG") from exc
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise HTTPException(status_code=409, detail="FrustraMPNN review capture digest mismatch")
    artifact = FrustraMPNNReviewArtifact(
        artifact_id=str(uuid4()),
        review_id=review.review_id,
        parent_job_id=job_id,
        created_by=actor,
        role="structure_view_capture",
        media_type="image/png",
        content_sha256=actual_sha256,
        size_bytes=len(payload),
        payload_blob=payload,
        generation_json={
            "schema_name": "frustrampnn_review_capture",
            "schema_version": 1,
            "review_id": review.review_id,
            "review_sha256": review.review_sha256,
            "landscape_sha256": review.landscape_sha256,
            "effective_settings_sha256": review.effective_settings_sha256,
            "result_references": review.result_references_json,
            "viewer_state": review.viewer_state_json,
        },
    )
    session.add(artifact)
    await session.commit()
    return {
        "schema_name": "frustrampnn_review_capture_receipt",
        "schema_version": 1,
        "artifact_id": artifact.artifact_id,
        "review_id": artifact.review_id,
        "parent_job_id": artifact.parent_job_id,
        "role": artifact.role,
        "media_type": artifact.media_type,
        "content_sha256": artifact.content_sha256,
        "size_bytes": artifact.size_bytes,
        "download_url": f"/api/frustrampnn/jobs/{job_id}/reviews/{review_id}/captures/{artifact.artifact_id}",
    }


@router.get("/jobs/{job_id}/reviews/{review_id}/captures/{artifact_id}")
async def download_review_capture(
    job_id: str,
    review_id: str,
    artifact_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    actor = _principal(request)
    await _saved_review(job_id, review_id, actor, session)
    artifact = await session.get(FrustraMPNNReviewArtifact, artifact_id)
    if artifact is None or artifact.parent_job_id != job_id or artifact.review_id != review_id or artifact.created_by != actor:
        raise HTTPException(status_code=404, detail="FrustraMPNN review capture not found")
    payload = bytes(artifact.payload_blob)
    if len(payload) != artifact.size_bytes or hashlib.sha256(payload).hexdigest() != artifact.content_sha256:
        raise HTTPException(status_code=409, detail="FrustraMPNN review capture integrity check failed")
    return Response(
        content=payload,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="frustrampnn-review-{review_id}-{artifact_id}.png"',
            "X-Content-SHA256": artifact.content_sha256,
        },
    )


_EXPORT_FIELDS = (
    "target_id", "entity_instance_id", "auth_asym_id", "auth_seq_id",
    "insertion_code", "sequence_index", "wt", "mutation_aa", "score",
    "score_class", "scoreable", "status", "reason",
)


def _csv_safe(value: Any) -> str:
    rendered = "" if value is None else str(value)
    if rendered.lstrip("\t\r\n ").startswith(("=", "+", "-", "@")):
        rendered = "'" + rendered
    return '"' + rendered.replace('"', '""') + '"'


def _export_content(export_payload: dict[str, Any], export_format: str) -> tuple[bytes, str, str]:
    if export_format == "json":
        return canonical_json_bytes(export_payload), "application/json", "json"
    metadata = [
        f"# parent_job_id={export_payload['parent_job_id']}",
        f"# invocation_id={export_payload['invocation_id']}",
        f"# row_count={export_payload['row_count']}",
        f"# total_matching_rows={export_payload['total_matching_rows']}",
        f"# complete={str(export_payload['complete']).lower()}",
        f"# source_artifact_sha256={export_payload['source_artifact_sha256']}",
        f"# effective_settings_sha256={export_payload['effective_settings_sha256'] or 'historical_unavailable'}",
    ]
    fields = list(_EXPORT_FIELDS)
    table = [",".join(_csv_safe(field) for field in fields)]
    table.extend(",".join(_csv_safe(row.get(field)) for field in fields) for row in export_payload["rows"])
    return ("\n".join(metadata + table) + "\n").encode("utf-8"), "text/csv", "csv"


@router.post("/jobs/{job_id}/exports", status_code=status.HTTP_201_CREATED)
async def create_governed_export(
    job_id: str,
    payload: FrustraMPNNExportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    actor = _principal(request)
    review = await _saved_review(job_id, payload.review_id, actor, session)
    if review.invocation_id != payload.invocation_id:
        raise HTTPException(status_code=422, detail="export invocation does not match review authority")
    result = await _scoped_result(payload.invocation_id, job_id, session)
    page = await persisted_landscape_page(
        session,
        job_id,
        payload.invocation_id,
        limit=payload.limit,
        auth_asym_id=payload.auth_asym_id,
        mutation_aa=payload.mutation_aa,
        status=payload.status,
    )
    total = int(page["total"])
    exported_rows = [
        {field: row[field] for field in _EXPORT_FIELDS}
        for row in page["items"]
    ]
    export_payload = {
        "schema_name": "frustrampnn_governed_export",
        "schema_version": 1,
        "parent_job_id": job_id,
        "invocation_id": payload.invocation_id,
        "review_id": review.review_id,
        "review_sha256": review.review_sha256,
        "landscape_sha256": review.landscape_sha256,
        "candidate_id": result.candidate_id,
        "source_artifact_sha256": result.source_artifact_sha256,
        "manifest_sha256": result.manifest_sha256,
        "summary_sha256": result.summary_sha256,
        "effective_settings_sha256": result.effective_settings_sha256,
        "filters": {field: getattr(payload, field) for field in ("auth_asym_id", "mutation_aa", "status") if getattr(payload, field) is not None},
        "row_count": len(exported_rows),
        "total_matching_rows": total,
        "complete": len(exported_rows) == total,
        "rows": exported_rows,
    }
    export_content, _media_type, _suffix = _export_content(export_payload, payload.format)
    content_sha256 = hashlib.sha256(export_content).hexdigest()
    record = FrustraMPNNExport(
        export_id=str(uuid4()), review_id=review.review_id, parent_job_id=job_id, invocation_id=payload.invocation_id,
        created_by=actor, format=payload.format, content_sha256=content_sha256,
        row_count=len(exported_rows), total_matching_rows=total,
        complete=len(exported_rows) == total, payload_json=export_payload,
        created_at=datetime.utcnow(),
    )
    artifact = FrustraMPNNReviewArtifact(
        artifact_id=str(uuid4()), review_id=review.review_id, parent_job_id=job_id,
        created_by=actor, role=f"governed_{payload.format}_export", media_type=_media_type,
        content_sha256=content_sha256, size_bytes=len(export_content), payload_blob=export_content,
        generation_json={
            "schema_name": "frustrampnn_review_export", "schema_version": 1,
            "review_id": review.review_id, "review_sha256": review.review_sha256,
            "landscape_sha256": review.landscape_sha256,
            "effective_settings_sha256": review.effective_settings_sha256,
            "query": export_payload["filters"], "row_count": len(exported_rows),
            "total_matching_rows": total,
        },
    )
    session.add_all([record, artifact])
    await session.commit()
    return {
        "schema_name": "frustrampnn_export_receipt", "schema_version": 1,
        "export_id": record.export_id, "artifact_id": artifact.artifact_id,
        "review_id": review.review_id, "review_sha256": review.review_sha256, "parent_job_id": job_id,
        "invocation_id": record.invocation_id, "format": record.format,
        "content_sha256": content_sha256, "row_count": record.row_count,
        "total_matching_rows": total, "complete": record.complete,
        "download_url": f"/api/frustrampnn/jobs/{job_id}/exports/{record.export_id}",
    }


@router.get("/jobs/{job_id}/exports/{export_id}")
async def download_governed_export(
    job_id: str,
    export_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    actor = _principal(request)
    record = await session.get(FrustraMPNNExport, export_id)
    if record is None or record.parent_job_id != job_id or record.created_by != actor:
        raise HTTPException(status_code=404, detail="FrustraMPNN export not found")
    content, media_type, suffix = _export_content(dict(record.payload_json), record.format)
    if hashlib.sha256(content).hexdigest() != record.content_sha256:
        raise HTTPException(status_code=409, detail="FrustraMPNN export byte identity is unavailable")
    return Response(
        content=content, media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="frustrampnn-{export_id}.{suffix}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-cache",
        },
    )
