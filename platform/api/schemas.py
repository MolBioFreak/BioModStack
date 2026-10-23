"""
Pydantic schemas for API request/response validation.
"""

from pydantic import BaseModel, Field, ConfigDict, model_validator, model_serializer
from typing import Optional, List, Any, Literal
from datetime import datetime
from enum import Enum
from services.fampnn_policy_admission import FampnnAnalysisOverrides
from services.binder_source_materialization import StructureSourceRequest
from services.caliby_native import ConformerSettings as CalibyConformerSettings


def serialize_datetime(dt: datetime) -> str:
    """Serialize datetime to ISO format with Z suffix for UTC."""
    if dt is None:
        return None
    # Ensure UTC times have Z suffix so JavaScript parses correctly
    return dt.isoformat() + 'Z'


class BinderNavigationJob(BaseModel):
    """Read-only linkage evidence, not a full Job or scientific receipt."""

    id: str
    name: str
    model_id: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None
    mode: Optional[str] = None
    stage_family: Optional[str] = None
    parent_job_id: Optional[str] = None
    lineage_root_job_id: Optional[str] = None
    selection_source_job_id: Optional[str] = None
    source_stage_job_id: Optional[str] = None
    # Closed projection is performed by the owner; raw JSON leaves deliberately
    # retain malformed/legacy evidence rather than coercing classification.
    params: Any = None
    provenance: Any = None

    from pydantic import field_serializer

    @field_serializer('created_at')
    @classmethod
    def serialize_created_at(cls, dt: Optional[datetime]) -> Optional[str]:
        return None if dt is None else serialize_datetime(dt)


class BinderNavigationJobList(BaseModel):
    jobs: List[BinderNavigationJob]
    total: int


class BC2NavigationStructure(BaseModel):
    artifact_id: str
    target_state: Optional[str] = None
    binder_chains: str | list | None = None
    target_chains: str | list | None = None


class BC2NavigationTrajectory(BaseModel):
    design: str
    trajectory: Optional[str] = None


class BC2NavigationDraw(BaseModel):
    design: str
    arm: Optional[str] = None
    trajectory_design: Optional[str] = None
    sequence: Optional[str] = None
    structures: List[BC2NavigationStructure]


class BC2NavigationResponse(BaseModel):
    job_id: str
    arm: Optional[str] = None
    trajectories: List[BC2NavigationTrajectory]
    draws: List[BC2NavigationDraw]
    total: int


class JobStatus(str, Enum):
    """Job status enum."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    AWAITING_INPUT = "awaiting_input"
    FAILED = "failed"
    CANCELLED = "cancelled"



# --- Job Schemas ---

class ExecutionPolicy(BaseModel):
    """Execution-only successful result return; never authorizes diagnostics."""
    model_config = ConfigDict(extra="forbid")
    remote_result_policy: Literal["manual", "automatic"] = "manual"

    @classmethod
    def from_params(cls, params):
        return cls(remote_result_policy="automatic" if isinstance(params, dict) and params.get("remote_result_policy") == "automatic" else "manual")


class SequenceDesignRequest(BaseModel):
    """Optional general-generation follow-on; settings remain model-owned."""
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    enabled: bool = False
    model_id: Literal['proteinmpnn', 'fampnn', 'caliby_experimental']
    params: dict[str, Any] = Field(default_factory=dict)
    input_settings: dict[str, Any] = Field(default_factory=dict)


class BinderRoundDesign(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: Literal['proteinmpnn', 'fampnn', 'caliby_binder']
    params: dict[str, Any] = Field(default_factory=dict)


class BinderRoundPrediction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: Literal['protenix', 'boltz2', 'esmfold2']
    params: dict[str, Any] = Field(default_factory=dict)


class BinderRoundRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1, 2] = 1
    enabled: bool = True
    sequence_design: BinderRoundDesign | list[BinderRoundDesign]
    prediction: BinderRoundPrediction | list[BinderRoundPrediction]
    binder_chains: list[str] = Field(default_factory=list)
    target_chains: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def versioned_stages(self):
        for role in ('sequence_design', 'prediction'):
            value = getattr(self, role)
            if self.schema_version == 1:
                if isinstance(value, list):
                    raise ValueError(f'Binder round v1 {role} must be a scalar settings document')
            else:
                if not isinstance(value, list) or not value:
                    raise ValueError(f'Binder round v2 {role} must be a nonempty settings array')
                if len({stage.model_id for stage in value}) != len(value):
                    raise ValueError(f'Binder round v2 {role} model IDs must be unique')
        return self

    @property
    def design_stages(self) -> list[BinderRoundDesign]:
        return self.sequence_design if isinstance(self.sequence_design, list) else [self.sequence_design]

    @property
    def prediction_stages(self) -> list[BinderRoundPrediction]:
        return self.prediction if isinstance(self.prediction, list) else [self.prediction]


class CandidateDocumentReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: str = Field(min_length=1)
    target_state: str | None = None
    model: int | None = None


class CandidateRef(BaseModel):
    """Exact existing candidate identity; native sequences are not Designs."""
    model_config = ConfigDict(extra='forbid')
    kind: Literal['design', 'native_document', 'native_sequence', 'native_observation']
    job_id: str
    design_id: str | None = None
    document: CandidateDocumentReference | None = None
    artifact_id: str | None = None
    target_state: str | None = None
    model: int | None = None
    publication_ref: str | None = None
    native_output_id: str | None = None
    native_record_id: str | None = None

    @model_validator(mode='after')
    def identity_fields(self):
        fields = {'design': {'design_id', 'document'},
                  'native_document': {'artifact_id', 'target_state', 'model'},
                  'native_sequence': {'publication_ref', 'native_output_id'},
                  'native_observation': {'publication_ref', 'native_record_id'}}[self.kind]
        required = fields - {'document', 'target_state', 'model'}
        if any(not getattr(self, key) for key in required):
            raise ValueError('Candidate reference is missing its exact identity')
        if any(getattr(self, key) is not None for key in set(type(self).model_fields) - fields - {'kind', 'job_id'}):
            raise ValueError('Candidate reference mixes identity kinds')
        return self


# Resolve the existing source envelope's exact native-candidate alternative.
StructureSourceRequest.model_rebuild(_types_namespace={'CandidateRef': CandidateRef})


class SelectedRoundInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    selected: Literal[True] | None = None
    stage_id: str | None = None
    invocation_id: str | None = Field(default=None, min_length=1)
    scope: Literal['binder', 'whole_composition'] = 'binder'
    chains: list[str] | None = None

    @model_validator(mode='after')
    def one_source(self):
        if (self.selected is not None) == (self.stage_id is not None):
            raise ValueError('Choose selected or an earlier stage')
        if self.invocation_id is not None and self.stage_id is None:
            raise ValueError('Invocation output requires an earlier stage')
        return self


# Share the native mask domain, not a second scientific settings dictionary.
class SelectedRoundState(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state_id: str = Field(min_length=1)
    candidate_ref: CandidateRef
    settings: CalibyConformerSettings | None = None


class SelectedRoundInputGroup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ensemble_id: str = Field(min_length=1)
    # The first state is the native primary conformer; never sort or deduplicate.
    states: list[SelectedRoundState] = Field(min_length=1)


class SelectedRoundModel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    invocation_id: str | None = Field(default=None, min_length=1)
    mode: str | None = Field(default=None, min_length=1)
    execution_target_id: str | None = None
    input_groups: list[SelectedRoundInputGroup] = Field(default_factory=list)
    model_id: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_serializer(mode='wrap')
    def placement_wire(self, handler):
        payload = handler(self)
        # Omitted placement inherits; explicit null means Local, including replay.
        if 'execution_target_id' not in self.model_fields_set:
            payload.pop('execution_target_id', None)
        return payload


class SelectedRoundStage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(min_length=1)
    input: SelectedRoundInput
    kind: Literal['sequence_design', 'maturation', 'prediction']
    models: list[SelectedRoundModel] = Field(default_factory=list)
    model_id: Literal['binder_refinement'] | None = None
    mode: Literal['refine'] | None = None
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def invocation(self):
        if self.kind == 'maturation':
            if self.models or self.model_id != 'binder_refinement' or self.mode != 'refine':
                raise ValueError('Maturation uses binder_refinement/refine')
        else:
            allowed = ({'proteinmpnn', 'fampnn', 'caliby_binder', 'protonpottsmpnn', 'caliby_experimental', 'ligandmpnn'}
                       if self.kind == 'sequence_design' else {'protenix', 'boltz2', 'esmfold2'})
            if not self.models or self.model_id or self.mode or self.params:
                raise ValueError('Design/prediction stages use models')
            if any(m.model_id not in allowed for m in self.models):
                raise ValueError('Stage models must be supported')
        return self


class SelectedRoundPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1, 2] = 1
    stages: list[SelectedRoundStage] = Field(min_length=1)

    @model_serializer(mode='wrap')
    def historical_wire(self, handler):
        payload = handler(self)
        if self.schema_version == 1:
            # Existing retained intent digests include this exact legacy wire.
            # New default fields must not invalidate replay of an old request.
            for stage in payload.get('stages', []):
                for field in ('invocation_id', 'scope', 'chains'):
                    stage.get('input', {}).pop(field, None)
                for model in stage.get('models', []):
                    for field in ('invocation_id', 'mode', 'execution_target_id', 'input_groups'):
                        model.pop(field, None)
        return payload

    @model_validator(mode='after')
    def dependencies(self):
        seen = set()
        calls = {}
        for stage in self.stages:
            models = stage.models
            if self.schema_version == 1:
                if len({m.model_id for m in models}) != len(models):
                    raise ValueError('Legacy stage models must be unique')
                if any(m.model_id in {'caliby_experimental', 'ligandmpnn'} or
                       m.invocation_id is not None or m.mode is not None or m.input_groups or
                       m.execution_target_id is not None for m in models):
                    raise ValueError('Explicit invocation settings require plan v2')
                if stage.input.scope != 'binder' or stage.input.chains is not None or stage.input.invocation_id:
                    raise ValueError('Explicit input scope requires plan v2')
            elif any(not m.invocation_id or not m.mode for m in models) or len({m.invocation_id for m in models}) != len(models):
                raise ValueError('Plan v2 requires unique invocation IDs and native modes per stage')
            if stage.id in seen or (stage.input.stage_id is not None and stage.input.stage_id not in seen):
                raise ValueError('Stage IDs must be unique and dependencies must name an earlier stage')
            if stage.input.invocation_id and stage.input.invocation_id not in calls.get(stage.input.stage_id, set()):
                raise ValueError('Input invocation must name an earlier call')
            calls[stage.id] = {m.invocation_id for m in models}
            seen.add(stage.id)
        return self


class SelectedRoundSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_refs: list[CandidateRef] | None = None
    saved_set_id: str | None = None
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def selector(self):
        if self.candidate_refs is not None:
            if not self.candidate_refs or self.saved_set_id is not None or self.revision is not None:
                raise ValueError('Use exact candidate refs or a saved revision')
        elif not self.saved_set_id or self.revision is None:
            raise ValueError('Saved selection requires its exact revision')
        return self


class SelectedRoundRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation: Literal['round']
    source_job_id: str
    idempotency_key: str = Field(min_length=1, max_length=255)
    selection: SelectedRoundSelection | None = None
    design_ids: list[str] = Field(default_factory=list)
    native_sources: list[dict[str, str]] = Field(default_factory=list)
    candidate_documents: dict[str, dict[str, Any]] = Field(default_factory=dict)
    round_plan: SelectedRoundPlan
    execution_target_id: str | None = None
    launch_context_id: str | None = None
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)


class BinderRoundStepReference(BaseModel):
    """Opaque retained preparation, not caller-authored scientific lineage."""
    model_config = ConfigDict(extra='forbid')
    root_job_id: str
    step_id: str


class JobCreate(BaseModel):
    """Request schema for creating a new job."""
    sequence_design: SequenceDesignRequest | None = None
    binder_round: BinderRoundRequest | None = None
    binder_round_step: BinderRoundStepReference | None = None
    source_structure: StructureSourceRequest | None = None
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    execution_plan_approval: str | None = Field(
        None, pattern=r"^[0-9a-f]{64}$",
        description="Explicit approval of /jobs/execution-plan/preview; required for remote admission.",
    )
    fampnn_analysis_overrides: FampnnAnalysisOverrides | None = None
    name: str = Field(..., min_length=1, max_length=255)
    model_id: str = Field(..., description="ID of the model to use (e.g., rfdiffusion)")
    mode: str = Field(..., description="Mode ID for the selected model")
    params: dict = Field(default_factory=dict)
    pinned_gpu: Optional[int] = Field(None, description="Optional: Pin job to specific GPU (0-3)")
    execution_target_id: Optional[str] = Field(
        None,
        min_length=1,
        max_length=160,
        description="Explicit execution target. Roots omit or use null for local; children inherit only when omitted.",
    )
    # Child job tracking (spawn-wait-collect pattern)
    parent_job_id: Optional[str] = Field(None, description="Parent job ID for child jobs")
    child_stage: Optional[str] = Field(None, description="Stage identifier (rfantibody, fampnn, boltz2)")
    batch_id: Optional[str] = Field(None, description="Batch ID for grouping")
    batch_name: Optional[str] = Field(None, description="Human-readable batch name")
    sequence_length: Optional[int] = Field(None, description="Sequence length for VRAM estimation")
    launch_context_id: Optional[str] = Field(
        None,
        min_length=1,
        max_length=128,
        description="Opaque server-owned Project launch context; hierarchy identity is never accepted here",
    )

    @model_validator(mode="after")
    def validate_msa_search_policy(self):
        from services.msa_policy import apply_msa_policy
        # Validate without mutating requested settings; compilation is visible
        # in preview/admission effective params, while replay retains intent.
        if "remote_result_policy" in self.params or "execution_policy" in self.params:
            raise ValueError("use typed execution_policy, not scientific params")
        if self.model_id == "protenix":
            from scripts.protenix_chemistry import validate_chemistry
            validate_chemistry(self.params)
            from services.protenix_chemistry import validate_component_files
            validate_component_files(self.params)
        apply_msa_policy(self.model_id, self.params)
        return self

    @model_validator(mode="before")
    @classmethod
    def reject_scientific_revision_input(cls, data: Any) -> Any:
        from services.core_protein_scientific_contract import reject_reserved_marker
        reject_reserved_marker(data)
        if isinstance(data, dict) and "remote_result_policy" in data:
            raise ValueError("remote_result_policy belongs in execution_policy")
        return data

    @model_validator(mode="before")
    @classmethod
    def require_native_rfd3_gpu_pin(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        model_id = str(data.get("model_id") or "").strip().lower()
        mode = str(data.get("mode") or "").strip().lower()
        if model_id == "protein_local_redesign" and mode == "local_redesign":
            pinned_gpu = data.get("pinned_gpu")
            if isinstance(pinned_gpu, bool) or not isinstance(pinned_gpu, int) or pinned_gpu < 0:
                raise ValueError("native RFD3 local redesign requires one explicit non-negative integer pinned_gpu")
        return data
    
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "name": "binder_test_001",
                "model_id": "rfdiffusion",
                "mode": "binder_denovo",
                "params": {
                    "rfd_num_designs": 8,
                    "seqs_per_design": 4,
                    "rfd_contigs": "[A17-131/0 60-100]",
                    "rfd_input_pdb": "./benchmarkdata/5o45_pd-l1.pdb"
                },
                "pinned_gpu": 0
            }
        }
    )


class CandidateResultReason(BaseModel):
    code: str
    message: str


class CandidateDisposition(BaseModel):
    candidate_id: str
    disposition: Literal['selected', 'rejected', 'failed', 'unevaluable']
    criterion: Optional[str] = None
    reason_code: Optional[str] = None


class CandidateResultSummary(BaseModel):
    """Observed producer accounting; unknown counts stay null, never row-inferred."""
    stage_id: Optional[str] = None
    state: str = 'unavailable'
    partial: bool = False
    requested_count: Optional[int] = Field(default=None, ge=0, strict=True)
    generated_count: Optional[int] = Field(default=None, ge=0, strict=True)
    rejected_count: Optional[int] = Field(default=None, ge=0, strict=True)
    failed_count: Optional[int] = Field(default=None, ge=0, strict=True)
    unevaluable_count: Optional[int] = Field(default=None, ge=0, strict=True)
    expected_publication_count: Optional[int] = Field(default=None, ge=0, strict=True)
    persisted_count: Optional[int] = Field(default=None, ge=0, strict=True)
    reason: Optional[CandidateResultReason] = None
    dispositions: Optional[List[CandidateDisposition]] = None


class ExecutionStageResponse(BaseModel):
    """An observed or retained-plan stage, never inferred from job success."""

    id: str
    label: str
    state: Literal['planned', 'running', 'completed', 'awaiting_input', 'failed', 'cancelled', 'not_requested', 'skipped', 'unknown']
    source: Literal['plan', 'recorded', 'model']


class JobSummaryResponse(BaseModel):
    """Bounded list projection. Scientific/detail DTO remains JobResponse."""

    execution_stages: List[ExecutionStageResponse] = Field(default_factory=list)
    execution_policy: ExecutionPolicy = Field(default_factory=ExecutionPolicy)
    id: str
    name: str
    status: JobStatus
    model_id: str
    mode: str
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    output_dir: Optional[str] = None
    error_message: Optional[str] = None
    design_count: int = 0
    batch_id: Optional[str] = None
    batch_name: Optional[str] = None
    parent_job_id: Optional[str] = None
    child_stage: Optional[str] = None
    lineage_root_job_id: Optional[str] = None
    stage_family: Optional[str] = None
    stage_mode: Optional[str] = None
    source_stage_job_id: Optional[str] = None
    source_stage_family: Optional[str] = None
    source_stage_mode: Optional[str] = None
    source_selection_count: Optional[int] = None
    selected_input_artifact_class: Optional[str] = None
    selected_input_schema_version: Optional[int] = None
    selection_source_type: Optional[str] = None
    selection_source_job_id: Optional[str] = None
    selection_dataset_name: Optional[str] = None
    pinned_gpu: Optional[int] = None
    execution_target_id: Optional[str] = None
    execution_source_revision: Optional[str] = None
    execution_source_tree: Optional[str] = None
    execution_bundle_sha256: Optional[str] = None
    remote_attempt_id: Optional[str] = None
    remote_state: Optional[str] = None
    # Compact observation for list views that intentionally omit provenance.
    # None on legacy/detail callers preserves receipt-based presentation.
    remote_results_received: Optional[bool] = None
    current_stage: Optional[str] = None
    completed_stages: Optional[List[str]] = None
    awaiting_input: Optional[bool] = None
    awaiting_stage: Optional[str] = None
    frustrampnn_result_count: int = 0
    frustrampnn_reopen_destination: Optional[dict] = None
    conformational_mapping_request_id: Optional[str] = None

    from pydantic import field_serializer

    @field_serializer('created_at', 'started_at', 'completed_at')
    @classmethod
    def serialize_datetime(cls, dt: Optional[datetime]) -> Optional[str]:
        """Serialize datetime with Z suffix for UTC."""
        if dt is None:
            return None
        return dt.isoformat() + 'Z'


class JobResponse(JobSummaryResponse):
    """Response schema for a job."""

    source_structure: StructureSourceRequest | None = None

    @model_validator(mode="before")
    @classmethod
    def project_stored_execution_stages(cls, value):
        from services.job_stage_progress import project_execution_stages
        if isinstance(value, dict):
            if 'execution_stages' not in value:
                value = {**value, 'execution_stages': project_execution_stages(value)}
        else:
            # ORM response paths retain evidence before public fields are trimmed.
            value = {**{key: getattr(value, key) for key in cls.model_fields
                        if hasattr(value, key)},
                     'execution_stages': project_execution_stages(value)}
        return value

    sequence_design: SequenceDesignRequest | None = None
    binder_round: BinderRoundRequest | None = None

    @model_validator(mode="after")
    def expose_orm_execution_policy(self):
        saved_sequence = (self.provenance or {}).get('sequence_design_request')
        if saved_sequence is not None:
            self.sequence_design = SequenceDesignRequest.model_validate(saved_sequence)
        self.source_structure = (self.provenance or {}).get('source_structure')
        if self.source_structure is not None:
            self.source_structure = StructureSourceRequest.model_validate(self.source_structure)
        saved_round = (self.provenance or {}).get('binder_round_request')
        if saved_round is not None:
            self.binder_round = BinderRoundRequest.model_validate(saved_round)
        if "execution_policy" not in self.model_fields_set:
            self.execution_policy = ExecutionPolicy.from_params(self.params)
        self.params = {key: value for key, value in self.params.items() if key != "remote_result_policy"}
        return self

    params: dict
    requested_design_count: Optional[int] = None
    source_selection_manifest_path: Optional[str] = None
    selected_loop_scope: Optional[dict] = None
    provenance: Optional[dict] = None
    saved_selection_sets: Optional[List[dict]] = None
    # GPU assignment
    assigned_gpu: Optional[int] = None
    vram_estimate_mb: Optional[int] = None
    # Stage tracking for multi-stage pipelines
    stage_outputs: Optional[dict] = None
    awaiting_payload: Optional[dict] = None
    decision_history: Optional[List[dict]] = None
    launch_context_id: Optional[str] = None
    launch_context_binding: Optional[dict] = None
    return_uri: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)
    
    from pydantic import computed_field

    @computed_field
    @property
    def result_summary(self) -> CandidateResultSummary:
        provenance = self.provenance or {}
        # Legacy metadata and DB row counts are not producer inventory authority.
        if type(provenance.get('core_protein_scientific_contract')) is not int or provenance.get('core_protein_scientific_contract') != 1:
            return CandidateResultSummary()
        receipt = provenance.get('core_protein_candidate_publication') or {}
        counts = receipt.get('summary') or {}
        integrity = provenance.get('result_integrity') or {}
        values = {key: counts.get(key) for key in (
            'stage_id', 'requested_count', 'generated_count', 'rejected_count',
            'failed_count', 'unevaluable_count', 'expected_publication_count')}
        values['persisted_count'] = integrity.get('result_count') if integrity else counts.get('published_count')
        values['state'] = integrity.get('state', 'pending_validation' if receipt else 'unavailable')
        values['partial'] = integrity.get('partial') is True
        reason = integrity.get('reason')
        if isinstance(reason, dict) and isinstance(reason.get('code'), str):
            values['reason'] = {'code': reason['code'], 'message': str(reason.get('message') or integrity.get('error') or reason['code'])}
        elif integrity.get('error'):
            values['reason'] = {'code': 'result_integrity_failure', 'message': str(integrity['error'])}
        values['dispositions'] = receipt.get('dispositions')
        return CandidateResultSummary.model_validate(values)


class JobList(BaseModel):
    """Response schema for job list."""
    jobs: List[JobResponse]
    total: int


class JobSummaryList(BaseModel):
    jobs: List[JobSummaryResponse]
    total: int


class LaunchContextCreateRequest(BaseModel):
    """Request a server-owned launcher handoff within the hierarchy in the URL."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    workflow_revision_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    return_uri: str = Field(min_length=1, max_length=1000)


class LaunchContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_name: Literal["bms.launch-context.v1", "bms.launch-context.v2"] = Field(
        alias="schema", serialization_alias="schema"
    )
    launch_context_id: str
    project_id: str
    global_experiment_id: str
    domain_experiment_id: str
    workflow_id: Optional[str]
    workflow_revision_id: Optional[str]
    preparation_id: Optional[str] = None
    run_attempt_id: Optional[str] = None
    normalized_request_sha256: Optional[str] = None
    validation_receipt_id: Optional[str] = None
    validation_receipt_sha256: Optional[str] = None
    pinned_gpu: Optional[int]
    return_uri: str
    source_receipt_id: str
    state: Literal["issued", "reserved", "claimed", "consumed"]
    canonical_job_id: Optional[str] = None
    recovery_job_id: Optional[str] = None
    binding_receipt: Optional[dict] = None
    pinned_scheduler: Optional[dict] = None
    issued_at: datetime
    expires_at: datetime


class BoltzApiComplexComponent(BaseModel):
    """Strict provider-native complex entity; UI aliases never cross this boundary."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["protein", "dna", "rna", "ligand_ccd", "ligand_smiles"]
    value: str = Field(min_length=1, max_length=100000)
    chain_ids: List[str] = Field(min_length=1, max_length=8)


class BoltzApiStructureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    client_request_id: str = Field(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    model: str = Field(default="boltz-2.1", max_length=64)
    sequence: str = Field(..., min_length=1, max_length=100000)
    primary_chain_id: str = Field(default="A", min_length=1, max_length=8)
    complex_components: List[BoltzApiComplexComponent] = Field(default_factory=list, max_length=64)
    num_samples: int = Field(default=1, ge=1, le=10)
    use_msa: bool = True


class BoltzApiEstimateResponse(BaseModel):
    model: str
    provider_input: dict
    estimate: dict
    estimate_fingerprint: str


class BoltzApiSubmitRequest(BoltzApiStructureRequest):
    approved_estimate_fingerprint: str = Field(..., min_length=64, max_length=64)


class BoltzApiEntitiesCapability(BaseModel):
    status: Literal["supported"]
    types: List[Literal["protein", "dna", "rna", "ligand_ccd", "ligand_smiles"]]


class BoltzApiMsaCapability(BaseModel):
    status: Literal["supported"]
    provider_default: Literal["omit"]
    disable_value: dict[Literal["type"], Literal["empty"]]


class BoltzApiSampleRangeCapability(BaseModel):
    status: Literal["supported"]
    minimum: int = Field(ge=1)
    maximum: int = Field(ge=1)


class BoltzApiTemplatesCapability(BaseModel):
    status: Literal["unavailable_pending_schema_verification"]


class BoltzApiUnsupportedLocalControls(BaseModel):
    diffusion_sampling_steps: Literal["unsupported"]
    recycling_steps: Literal["unsupported"]
    potentials: Literal["unsupported"]
    denoiser_chunking: Literal["unsupported"]
    gpu_pinning: Literal["unsupported"]
    parallelism: Literal["unsupported"]
    oom_retry: Literal["unsupported"]
    conditioning: Literal["unsupported"]


class BoltzApiProviderCapabilities(BaseModel):
    contract_version: Literal["bms.boltz_api.capabilities.v1"]
    entities: BoltzApiEntitiesCapability
    msa: BoltzApiMsaCapability
    num_samples: BoltzApiSampleRangeCapability
    templates: BoltzApiTemplatesCapability
    unsupported_local_controls: BoltzApiUnsupportedLocalControls


class BoltzApiCliUpdateStatus(BaseModel):
    check_status: Literal[
        "current", "update_available", "unavailable", "unavailable_pending_official_feed_verification"
    ]
    installed_version: Optional[str] = None
    latest_version: Optional[str] = None
    source: Literal["boltz_api_static_cli"]
    release_feed_url: Optional[str] = None
    release_url: Optional[str] = None
    checked_at: Optional[datetime] = None


class BoltzApiProviderStatusResponse(BaseModel):
    available: bool
    cli_available: bool
    credential_configured: bool
    model: str
    message: str
    capabilities: BoltzApiProviderCapabilities
    cli_update: BoltzApiCliUpdateStatus


class ExternalImportPreviewRequest(BaseModel):
    source_path: str = Field(..., min_length=1, max_length=1000)
    provider_hint: str = Field(default="boltz_api", max_length=64)


class ExternalImportCreateRequest(BaseModel):
    source_path: str = Field(..., min_length=1, max_length=1000)
    provider: str = Field(default="boltz_api", max_length=64)
    preview_fingerprint: str = Field(..., min_length=64, max_length=64)
    dataset_name: str = Field(..., min_length=1, max_length=255)
    job_name: Optional[str] = Field(default=None, max_length=255)


class ExternalImportPreviewResponse(BaseModel):
    provider: str
    resource_type: str
    provider_job_id: str
    model: Optional[str] = None
    model_version: Optional[str] = None
    status: str
    sample_count: int
    entities: List[dict]
    source_fingerprint: str
    run_metadata_sha256: str
    archive_sha256: Optional[str] = None
    importable: bool
    error_code: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    provider_metadata: dict = Field(default_factory=dict)


class ExternalImportResponse(BaseModel):
    id: str
    provider_id: str
    resource_type: str
    provider_job_id: str
    state: str
    source_fingerprint: str
    bms_job_id: Optional[str] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    imported_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


# --- Design Schemas ---

class DesignResponse(BaseModel):
    """Response schema for a design."""
    id: str
    job_id: str
    name: str
    pdb_path: str
    
    # Metrics
    plddt_overall: Optional[float] = None
    plddt_binder: Optional[float] = None
    pae_interaction: Optional[float] = None
    rmsd_binder: Optional[float] = None
    conf_score: Optional[float] = None
    mpnn_score: Optional[float] = None
    ligand_iptm: Optional[float] = None
    affinity_score: Optional[float] = None
    binder_probability: Optional[float] = None

    # Antibody Specific
    cdr_h1: Optional[str] = None
    cdr_h2: Optional[str] = None
    cdr_h3: Optional[str] = None
    cdr_l1: Optional[str] = None
    cdr_l2: Optional[str] = None
    cdr_l3: Optional[str] = None
    
    # CDR Loop Lengths (for sorting/filtering)
    binder_length: Optional[int] = None
    cdr_h1_length: Optional[int] = None
    cdr_h2_length: Optional[int] = None
    cdr_h3_length: Optional[int] = None
    cdr_l1_length: Optional[int] = None  # NULL for VHH/nanobodies
    cdr_l2_length: Optional[int] = None
    cdr_l3_length: Optional[int] = None
    antibody_type: Optional[str] = None  # vhh, fab, scfv
    
    humanness_score: Optional[float] = None
    stability_data: Optional[Any] = None  # JSON blob for heatmaps
        
    # User data
    is_favorite: bool = False
    notes: Optional[str] = None

    # Lineage / provenance
    lineage_root_job_id: Optional[str] = None
    parent_design_id: Optional[str] = None
    origin_design_id: Optional[str] = None
    origin_job_id: Optional[str] = None
    origin_backbone_design_id: Optional[str] = None
    stage_family: Optional[str] = None
    stage_mode: Optional[str] = None
    source_stage_job_id: Optional[str] = None
    source_stage_family: Optional[str] = None
    source_stage_mode: Optional[str] = None
    source_pdb_path: Optional[str] = None
    source_design_name: Optional[str] = None
    artifact_class: Optional[str] = None
    artifact_schema_version: Optional[int] = None
    review_profile_id: Optional[str] = None
    review_contract_version: Optional[int] = None
    review_contract_source: Optional[str] = None
    review_artifact_manifest: Optional[Any] = None
    review_role_map: Optional[Any] = None
    selected_loop_scope: Optional[Any] = None
    provenance: Optional[Any] = None
    
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class DesignUpdate(BaseModel):
    """Request schema for updating a design."""
    is_favorite: Optional[bool] = None
    notes: Optional[str] = None


class DesignList(BaseModel):
    """Response schema for design list."""
    designs: List[DesignResponse]
    total: int


# --- GPU Schemas ---

class GPUStatus(BaseModel):
    """Status of a single GPU."""
    index: int
    name: str
    utilization: int  # percentage
    memory_used_mb: int
    memory_total_mb: int
    temperature: int  # celsius
    current_task: Optional[str] = None


class GPUStatusResponse(BaseModel):
    """Response schema for GPU status."""
    gpus: List[GPUStatus]
    timestamp: datetime


# --- File Schemas ---

class DirectoryEntry(BaseModel):
    """Entry in a directory listing."""
    name: str
    path: str
    is_directory: bool
    size_bytes: Optional[int] = None
    modified_at: Optional[datetime] = None


class DirectoryListing(BaseModel):
    """Response schema for directory listing."""
    path: str
    entries: List[DirectoryEntry]
