/** Native workflow model projection. Generated from the receiving Pydantic schemas. */
import type { DomesticationResult, DomesticationSettings, EnzymeBinding, Feature, GoldenGateDesignRequest, GoldenGateDesignResult, Part, ReactionRequest, ReactionResult, Source, Target, Thermodynamics } from "./goldenGateDesign";

export type AssembleTask = {
  schema_version: "bms.golden-gate-design.v1";
  task: "assemble_parts";
  sources: (Source)[];
  parts: (Part)[];
  target: Target;
  enzyme: EnzymeBinding;
  primer_settings: Thermodynamics;
  fidelity: WorkflowFidelitySettings;
  domestication: (EditRequest)[];
  automatic_primers: (AutomaticPrimerSelection)[];
  reaction: ReactionRequest | null;
};

export type AutomaticPrimerSelection = {
  part_id: string;
  settings: PrimerSelectionSettings;
  pair_rank: number;
};

export type CutWindow = {
  start: number;
  end: number;
};

export type EditRequest = {
  source_id: string;
  settings: DomesticationSettings;
  accepted_sequence: string | null;
};

export type EndInventory = {
  instance_id: string;
  sequence: string | null;
  role: string;
  intended_junction_id: string | null;
  polarity: string | null;
  removed: boolean;
  phosphorylation: string | null;
};

export type EvaluateTask = {
  schema_version: "bms.golden-gate-design.v1";
  task: "evaluate_overhangs";
  junctions: (string)[];
  fidelity: WorkflowFidelitySettings;
  inventory: (EndInventory)[];
  inventory_complete: boolean;
};

export type WorkflowFidelitySettings = {
  dataset_id: string | null;
  condition_use: "reference" | "explicit_proxy";
  include_pair_observations: boolean;
};

export type FrameConstraint = {
  start: number;
  end: number;
  origin: number;
  phase: 0 | 1 | 2;
};

export type OptimizeTask = {
  schema_version: "bms.golden-gate-design.v1";
  task: "optimize_overhangs";
  candidate_domain: (string)[];
  junction_count: number;
  end_length: 3 | 4;
  fixed: (string)[];
  required: (string)[];
  excluded: (string)[];
  fidelity: WorkflowFidelitySettings;
  search: SearchControls;
};

export type PrimerSelectionSettings = {
  primer_min_length: number;
  primer_max_length: number;
  product_min_length: number;
  product_max_length: number;
  flank_search_span: number;
  gc_min_percent: number;
  gc_max_percent: number;
  tm_target_c: number;
  tm_max_delta_c: number;
  gc_clamp_min: number;
  max_poly_x: number;
  max_pairs: number;
};

export type SearchControls = {
  seed: number;
  evaluation_budget: number;
  restarts: number;
  exact_limit: number;
  alternatives: number;
  unique_classes: boolean;
  exclude_palindromes: boolean;
  ranking_mode: "empirical" | "lexicographic";
};

export type SplitPreparation = {
  kind: "synthesis" | "pcr";
  clamp: string;
  spacer: string;
  forward_anneal_length: number;
  reverse_anneal_length: number;
  qc_min_binding_anneal_length: number;
};

export type SplitTask = {
  schema_version: "bms.golden-gate-design.v1";
  task: "split_target";
  target: Source;
  enzyme: EnzymeBinding;
  windows: (CutWindow)[];
  fixed_positions: (number | null)[];
  fixed_overhangs: (string)[];
  required: (string)[];
  excluded: (string)[];
  protected_regions: (CutWindow)[];
  frame_constraints: (FrameConstraint)[];
  min_fragment_length: number;
  max_fragment_length: number | null;
  target_fragment_length: number | null;
  display_origin: number;
  preparation: SplitPreparation;
  terminal_right_fusion: string | null;
  primer_settings: Thermodynamics;
  fidelity: WorkflowFidelitySettings;
  search: SearchControls;
  reaction: ReactionRequest | null;
};

export type BaseEdit = {
  position: number;
  original: string;
  proposed: string;
  affected_feature_ids: (string)[];
};

export type EditOutcome = {
  source_id: string;
  proposal: DomesticationResult;
  accepted: boolean;
  original: Material;
};

export type JsonValue = unknown;

export type Material = {
  sequence: string;
  topology: "linear" | "circular";
  features: (Feature)[];
};

export type TranslationEvidence = {
  feature_id: string;
  original: string;
  proposed: string | null;
  original_start_codon: string | null;
  proposed_start_codon: string | null;
};

export type WorkflowCandidate = {
  id: string;
  fixed_request: GoldenGateDesignRequest;
  design: GoldenGateDesignResult;
  fidelity: Record<string, JsonValue>;
  reaction?: import("./goldenGateDesign").ReactionResult | null;
};

export type WorksheetDiagnostic = {
  code: string;
  component_id: string | null;
  message: string;
};

export type FrozenSelection = {
  solution_id: string;
  edit_evidence: (EditOutcome)[];
  authored_request: AssembleTask | SplitTask | EvaluateTask | OptimizeTask | null;
  request: GoldenGateDesignRequest;
  fidelity: WorkflowFidelitySettings;
  reaction: ReactionRequest | null;
  original_sources: (Source)[];
  accepted_edits: (EditRequest)[];
};

export type WorkflowResult = {
  schema_version: "bms.golden-gate-workflow-result.v1";
  requested: AssembleTask | SplitTask | EvaluateTask | OptimizeTask;
  solutions: (WorkflowCandidate)[];
  selected_solution_id: string | null;
  evaluation: Record<string, JsonValue> | null;
  search_result: Record<string, JsonValue> | null;
  edits: (EditOutcome)[];
  edit_evidence_authority: "native_preview" | "operator_supplied_frozen";
  reaction: ReactionResult | null;
  diagnostics: (string)[];
};

export type WorkflowRequest = AssembleTask | SplitTask | EvaluateTask | OptimizeTask;

export type SaveDesignRequest = {
  schema_version: "bms.golden-gate-save.v1";
  selection: FrozenSelection;
  name: string;
  description: string | null;
  idempotency_key: string;
};

export type SavedDesign = {
  schema_version: "bms.golden-gate-saved.v1";
  operation_id: string;
  product_document_id: string;
  product_revision_id: string;
  selection: FrozenSelection;
  result: WorkflowResult;
};
