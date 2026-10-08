"""Receiving contract for local Golden Gate tasks; leaf science remains authoritative."""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import Field, TypeAdapter, create_model, JsonValue
from routers.molbio_ops import PrimerDesignRequest, SequenceInput
from services.assembly.golden_gate_design_types import (
    Closed, GoldenGateDesignRequest, GoldenGateDesignResult, EnzymeBinding,
    Material, Source, Tail, Thermodynamics,
)
from services.assembly.golden_gate_domestication import DomesticationSettings, DomesticationResult
from services.assembly.golden_gate_reaction import ReactionRequest, ReactionResult

# Inherit native field definitions (including defaults), never fork chemistry.
PrimerSelectionSettings = create_model(
    'PrimerSelectionSettings', __base__=Closed,
    **{name: (field.annotation, field) for name, field in PrimerDesignRequest.model_fields.items()
       if name not in set(SequenceInput.model_fields) | {'target_start', 'target_end', 'overhang_forward', 'overhang_reverse', 'tm_settings'}},
)

class AutomaticPrimerSelection(Closed):
    part_id: str
    settings: PrimerSelectionSettings = Field(default_factory=PrimerSelectionSettings)
    pair_rank: int = Field(default=1, ge=1)

class FidelitySettings(Closed):
    dataset_id: str | None = None
    condition_use: Literal['reference', 'explicit_proxy'] = 'reference'
    include_pair_observations: bool = False

class SearchControls(Closed):
    seed: int = 0
    evaluation_budget: int = Field(default=10000, ge=0)
    restarts: int = Field(default=8, ge=1)
    exact_limit: int = Field(default=10000, ge=0)
    alternatives: int = Field(default=5, ge=1)
    unique_classes: bool = True
    exclude_palindromes: bool = True
    ranking_mode: Literal['empirical', 'lexicographic'] = 'empirical'

class EndInventory(Closed):
    instance_id: str
    sequence: str | None
    role: str
    intended_junction_id: str | None = None
    polarity: str | None = None
    removed: bool = False
    phosphorylation: str | None = None

class EditRequest(Closed):
    source_id: str
    settings: DomesticationSettings = Field(default_factory=DomesticationSettings)
    accepted_sequence: str | None = None

class AssembleTask(GoldenGateDesignRequest):
    fidelity: FidelitySettings = Field(default_factory=FidelitySettings)
    domestication: list[EditRequest] = Field(default_factory=list)
    automatic_primers: list[AutomaticPrimerSelection] = Field(default_factory=list)
    reaction: ReactionRequest | None = None

class EvaluateTask(Closed):
    schema_version: Literal['bms.golden-gate-design.v1'] = 'bms.golden-gate-design.v1'
    task: Literal['evaluate_overhangs']
    junctions: list[str]
    fidelity: FidelitySettings = Field(default_factory=FidelitySettings)
    inventory: list[EndInventory] = Field(default_factory=list)
    inventory_complete: bool = False

class OptimizeTask(Closed):
    schema_version: Literal['bms.golden-gate-design.v1'] = 'bms.golden-gate-design.v1'
    task: Literal['optimize_overhangs']
    candidate_domain: list[str]
    junction_count: int = Field(ge=1)
    end_length: Literal[3, 4]
    fixed: list[str] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    fidelity: FidelitySettings = Field(default_factory=FidelitySettings)
    search: SearchControls = Field(default_factory=SearchControls)

class CutWindow(Closed):
    start: int = Field(ge=0)
    end: int = Field(ge=0)

class FrameConstraint(CutWindow):
    origin: int = Field(ge=0)
    phase: Literal[0, 1, 2]

class SplitPreparation(Closed):
    kind: Literal['synthesis', 'pcr'] = 'synthesis'
    clamp: str
    spacer: str
    forward_anneal_length: int = Field(default=20, ge=8)
    reverse_anneal_length: int = Field(default=20, ge=8)
    qc_min_binding_anneal_length: int = Field(default=12, ge=1)

class SplitTask(Closed):
    schema_version: Literal['bms.golden-gate-design.v1'] = 'bms.golden-gate-design.v1'
    task: Literal['split_target']
    target: Source
    enzyme: EnzymeBinding
    windows: list[CutWindow]
    fixed_positions: list[int | None] = Field(default_factory=list)
    fixed_overhangs: list[str] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    protected_regions: list[CutWindow] = Field(default_factory=list)
    frame_constraints: list[FrameConstraint] = Field(default_factory=list)
    min_fragment_length: int = Field(default=1, ge=1)
    max_fragment_length: int | None = Field(default=None, ge=1)
    target_fragment_length: int | None = Field(default=None, ge=1)
    display_origin: int = Field(default=0, ge=0)
    preparation: SplitPreparation
    terminal_right_fusion: str | None = None
    primer_settings: Thermodynamics = Field(default_factory=Thermodynamics)
    fidelity: FidelitySettings = Field(default_factory=FidelitySettings)
    search: SearchControls = Field(default_factory=SearchControls)
    reaction: ReactionRequest | None = None

WorkflowRequest = Annotated[AssembleTask | SplitTask | EvaluateTask | OptimizeTask, Field(discriminator='task')]
REQUEST_ADAPTER = TypeAdapter(WorkflowRequest)

class EditOutcome(Closed):
    source_id: str
    proposal: DomesticationResult
    accepted: bool
    original: Material

class WorkflowCandidate(Closed):
    id: str
    fixed_request: GoldenGateDesignRequest
    design: GoldenGateDesignResult
    # Native result evidence is retained losslessly; it is not a settings escape hatch.
    fidelity: dict[str, JsonValue]
    reaction: ReactionResult | None = None

class WorkflowResult(Closed):
    schema_version: Literal['bms.golden-gate-workflow-result.v1'] = 'bms.golden-gate-workflow-result.v1'
    requested: WorkflowRequest
    solutions: list[WorkflowCandidate] = Field(default_factory=list)
    selected_solution_id: str | None = None
    evaluation: dict[str, JsonValue] | None = None
    search_result: dict[str, JsonValue] | None = None
    edits: list[EditOutcome] = Field(default_factory=list)
    edit_evidence_authority: Literal['native_preview', 'operator_supplied_frozen'] = 'native_preview'
    reaction: ReactionResult | None = None
    diagnostics: list[str] = Field(default_factory=list)

class FrozenSelection(Closed):
    solution_id: str = 'fixed'
    edit_evidence: list[EditOutcome] = Field(default_factory=list)
    authored_request: WorkflowRequest | None = None
    request: GoldenGateDesignRequest
    fidelity: FidelitySettings = Field(default_factory=FidelitySettings)
    reaction: ReactionRequest | None = None
    # Applied edits remain explicit; original sources are never replaced by identity fiction.
    original_sources: list[Source] = Field(default_factory=list)
    accepted_edits: list[EditRequest] = Field(default_factory=list)

class SaveDesignRequest(Closed):
    schema_version: Literal['bms.golden-gate-save.v1'] = 'bms.golden-gate-save.v1'
    selection: FrozenSelection
    name: str = Field(min_length=1)
    description: str | None = None
    idempotency_key: str = Field(min_length=1)

class SavedDesign(Closed):
    schema_version: Literal['bms.golden-gate-saved.v1'] = 'bms.golden-gate-saved.v1'
    operation_id: str
    product_document_id: str
    product_revision_id: str
    selection: FrozenSelection
    result: WorkflowResult
