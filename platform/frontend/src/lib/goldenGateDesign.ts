/** Native leaf contracts, checked against golden_gate_design_types.py and native schemas.
 * All default-bearing fields are explicit in controlled drafts; no hydration defaults.
 * This is NOT an HTTP workflow envelope. */
export type DonorPreparation = {
  kind: "donor";
  retained_fragment_index: number;
  removed_fragment_indices: (number)[];
};

export type EnzymeBinding = {
  enzyme_id: string;
  catalog_id: string;
  catalog_sha256: string;
};

export type Feature = {
  id: string;
  type: string;
  name: string;
  segments: (Region)[];
  strand: -1 | 0 | 1;
  qualifiers: Record<string, unknown>;
  codon_start: 1 | 2 | 3 | null;
  status: "intact" | "truncated" | "disrupted";
  frame_preserved: boolean | null;
};

export type FragmentEnd = {
  type: "blunt" | "sticky_5" | "sticky_3";
  overhang: string;
  label: string | null;
  protruding_strand: "top" | "bottom" | null;
};

export type InlineSource = {
  sequence: string;
  topology: "linear" | "circular";
  features: (Feature)[];
  kind: "inline";
};

export type PCRPreparation = {
  kind: "pcr";
  region: Region;
  left: Tail;
  right: Tail;
  forward_anneal_length: number;
  reverse_anneal_length: number;
  qc_min_binding_anneal_length: number;
  removed_fragment_indices: (number)[];
};

export type Part = {
  id: string;
  source_id: string;
  name: string;
  role: string | null;
  slot: string | null;
  orientation: "forward" | "reverse";
  preparation: PCRPreparation | SynthesisPreparation | DonorPreparation | PreparedPreparation;
};

export type PreparedPreparation = {
  kind: "prepared";
  left_end: FragmentEnd;
  right_end: FragmentEnd;
};

export type Region = {
  start: number;
  end: number;
  wraps_origin: boolean;
};

export type RevisionSource = {
  kind: "molecular_revision";
  revision_id: string;
};

export type Source = {
  id: string;
  source: InlineSource | RevisionSource;
};

export type SynthesisPreparation = {
  kind: "synthesis";
  region: Region;
  left: Tail;
  right: Tail;
  removed_fragment_indices: (number)[];
};

export type Tail = {
  clamp: string;
  spacer: string;
  fusion: string;
};

export type Target = {
  topology: "linear" | "circular";
  display_origin: number;
  exact_sequence: string | null;
};

export type Thermodynamics = {
  algorithm: string;
  salt_correction: string;
  primer_concentration_nM: number;
  template_concentration_nM: number;
  na_mM: number;
  k_mM: number;
  tris_mM: number;
  mg_mM: number;
  dntps_mM: number;
  dmso_percent: number;
  formamide_percent: number;
  self_complementary: boolean;
};

export type GoldenGateDesignRequest = {
  schema_version: "bms.golden-gate-design.v1";
  task: "assemble_parts";
  sources: (Source)[];
  parts: (Part)[];
  target: Target;
  enzyme: EnzymeBinding;
  primer_settings: Thermodynamics;
};

export type AnalysisOccurrence = {
  occurrence_id: string;
  occurrence_ordinal: number;
  enzyme_id: string;
  canonical_name: string;
  orientation: "forward" | "reverse";
  certainty: "definite" | "possible";
  recognition_pattern: string;
  site_start: number;
  site_end_unwrapped: number;
  site_segments: ([number, number])[];
  wraps_origin: boolean;
  matched_reference_sequence: string;
  double_strand_events: (DoubleStrandEvent)[];
  nicks: (NickEvent)[];
  limitations: (string)[];
  activity_assessment: "not_evaluated";
  methylation_context: "unknown";
};

export type AssemblyJunction = {
  left_fragment_id: string;
  right_fragment_id: string;
  left_fragment_name: string;
  right_fragment_name: string;
  mode: "ligation" | "gibson" | "golden_gate";
  left_end_type: "blunt" | "sticky_5" | "sticky_3" | null;
  right_end_type: "blunt" | "sticky_5" | "sticky_3" | null;
  overhang_sequence: string | null;
  overlap_sequence: string | null;
  overlap_length: number;
  junction_sequence: string;
  validation: string;
  notes: (string)[];
};

export type CleavageContributor = {
  enzyme_id: string;
  occurrence_id: string;
  event_ordinal: number;
  orientation: "forward" | "reverse";
};

export type DesignMaterial = {
  sequence: string;
  topology: "linear" | "circular";
  features: (Feature)[];
  id: string;
  stage: "source" | "prepared" | "digest";
  parent_id: string | null;
  transformation: "source" | "pcr" | "synthesis" | "donor" | "prepared" | "digest";
  mappings: (Mapping)[];
  left_end: FragmentEnd | null;
  right_end: FragmentEnd | null;
};

export type DigestEnd = {
  kind: "natural" | "blunt" | "five_prime_overhang" | "three_prime_overhang" | "no_cut_circular";
  enzyme_created: boolean;
  side: "left" | "right";
  protruding_strand: "top" | "bottom" | null;
  overhang_sequence_5to3: string | null;
  length_nt: number;
  top_boundary: number | null;
  bottom_boundary: number | null;
  top_boundary_unwrapped: number | null;
  bottom_boundary_unwrapped: number | null;
  top_winding: number | null;
  bottom_winding: number | null;
  contributing_enzyme_ids: (string)[];
  contributors: (CleavageContributor)[];
  contributor_group_id: string | null;
};

export type DigestFragment = {
  fragment_index: number;
  topology: "linear" | "circular";
  top_strand_sequence: string;
  reference_span_bp: number;
  source_segments: ([number, number])[];
  top_start_boundary: number;
  top_end_boundary: number;
  bottom_start_boundary: number;
  bottom_end_boundary: number;
  top_start_boundary_normalized: number;
  top_end_boundary_normalized: number;
  bottom_start_boundary_normalized: number;
  bottom_end_boundary_normalized: number;
  top_start_winding: number;
  top_end_winding: number;
  bottom_start_winding: number;
  bottom_end_winding: number;
  wraps_origin: boolean;
  left_end: DigestEnd;
  right_end: DigestEnd;
  lineage_cleavage_group_ids: (string)[];
  contributing_enzyme_ids: (string)[];
};

export type DigestOutcome = {
  part_id: string;
  input_material_id: string;
  fragment_material_ids: (string)[];
  fragments: (DigestFragment)[];
  occurrences: (AnalysisOccurrence)[];
  cleavages: (PhysicalCleavage)[];
  retained_fragment_index: number | null;
  removed_fragment_indices: (number)[];
  background_fragment_indices: (number)[];
  warnings: (string)[];
};

export type DoubleStrandEvent = {
  enzyme_id: string;
  occurrence_id: string;
  event_ordinal: number;
  orientation: "forward" | "reverse";
  status: "complete" | "geometry_out_of_bounds";
  top_boundary: number | null;
  bottom_boundary: number | null;
  top_boundary_unwrapped: number;
  bottom_boundary_unwrapped: number;
  top_winding: number;
  bottom_winding: number;
  overhang_kind: "blunt" | "five_prime" | "three_prime";
  overhang_length_nt: number;
  overhang_sequence_5to3: string | null;
  overhang_source_strand: "top" | "bottom" | null;
  protruding_strand: "top" | "bottom" | null;
  contributor_group_id: string;
  activity_assessment: "not_evaluated";
  methylation_context: "unknown";
};

export type Geometry = {
  site: string;
  top_offset: number;
  bottom_offset: number;
  spacer_length: number;
  overhang_length: number;
  polarity: "five_prime";
};

export type Mapping = {
  output_start: number;
  output_end: number;
  parent_id: string;
  parent_start: number;
  parent_end: number;
  strand: -1 | 1;
};

export type NickEvent = {
  enzyme_id: string;
  occurrence_id: string;
  event_ordinal: number;
  orientation: "forward" | "reverse";
  strand: "top" | "bottom";
  status: "complete" | "geometry_out_of_bounds";
  boundary: number | null;
  boundary_unwrapped: number;
  winding: number;
  contributor_group_id: string;
  activity_assessment: "not_evaluated";
};

export type PhysicalCleavage = {
  cleavage_index: number;
  contributor_group_id: string;
  top_boundary: number;
  bottom_boundary: number;
  top_boundary_unwrapped: number;
  bottom_boundary_unwrapped: number;
  top_winding: number;
  bottom_winding: number;
  overhang_kind: "blunt" | "five_prime" | "three_prime";
  overhang_length_nt: number;
  contributing_enzyme_ids: (string)[];
  contributors: (CleavageContributor)[];
};

export type PreparationOutcome = {
  part_id: string;
  source_material_id: string;
  prepared_material_id: string;
  retained_material_id: string | null;
  primer_ids: (string)[];
  pcr_verification: "not_applicable" | "verified" | "ambiguous" | "mismatch";
  pcr_diagnostics: (string)[];
  pair_qc: PrimerPairQcMetrics | null;
};

export type Primer = {
  id: string;
  part_id: string;
  direction: "forward" | "reverse";
  full_sequence: string;
  clamp: string;
  recognition_site: string;
  recognition_orientation: "forward";
  spacer: string;
  fusion: string;
  annealing_sequence: string;
  tm: PrimerTmResult;
  qc: PrimerQcMetrics;
  qc_template_orientation: "forward" | "reverse";
  footprint_mappings: (Mapping)[];
};

export type PrimerPairQcMetrics = {
  heterodimer_complement: number;
  three_prime_heterodimer: number;
  warnings: (string)[];
};

export type PrimerQcMetrics = {
  sequence: string;
  sequence_type: "dna" | "rna";
  length: number;
  gc_percent: number;
  max_self_complement: number;
  three_prime_self_complement: number;
  max_hairpin_stem: number;
  hairpin_loop_size: number | null;
  binding_site_count: number | null;
  off_target_site_count: number | null;
  binding_positions: (Record<string, number | boolean>)[];
  warnings: (string)[];
};

export type PrimerTmResult = {
  id: string | null;
  name: string | null;
  sequence: string;
  sequence_type: string;
  length: number;
  gc_percent: number;
  tm: number | null;
  algorithm: string;
  algorithm_label: string;
  salt_correction: string;
  salt_correction_label: string;
  polymer_pairing: string;
  warnings: (string)[];
};

export type Solution = {
  id: string;
  sequence: string;
  topology: "linear" | "circular";
  display_origin: number;
  part_material_ids: (string)[];
  mappings: (Mapping)[];
  features: (Feature)[];
  junctions: (AssemblyJunction)[];
  occurrences: (AnalysisOccurrence)[];
  exact_target_match: boolean | null;
  warnings: (string)[];
};

export type SourceScreen = {
  source_material_id: string;
  occurrences: (AnalysisOccurrence)[];
  warnings: (string)[];
};

export type GoldenGateDesignResult = {
  schema_version: "bms.golden-gate-design-result.v1";
  enzyme: EnzymeBinding;
  geometry: Geometry;
  primer_settings: Thermodynamics;
  materials: (DesignMaterial)[];
  source_screens: (SourceScreen)[];
  preparations: (PreparationOutcome)[];
  primers: (Primer)[];
  digests: (DigestOutcome)[];
  solutions: (Solution)[];
  selected_solution_id: string | null;
  search_scope: "fixed_order_and_explicit_preparations_only";
  diagnostics: (string)[];
  limitations: (string)[];
};

export type CDSConstraint = {
  feature_id: string;
  region: EditRegion;
  strand: 1 | -1;
  frame: 0 | 1 | 2;
  genetic_code: number;
  initiation: "ordinary" | "preserve" | "allowed";
  allowed_start_codons: (string)[];
  stop_policy: "preserve" | "synonymous";
};

export type EditRegion = {
  start: number;
  end: number;
  wraps_origin: boolean;
};

export type UnwantedSite = {
  enzyme_id: string;
  recognition_sequence: string;
  starts: (number)[] | null;
};

export type DomesticationSettings = {
  schema_version: "golden-gate-domestication/v1";
  enabled: boolean;
  editable_regions: (EditRegion)[];
  protected_regions: (EditRegion)[];
  cds: (CDSConstraint)[];
  unwanted_sites: (UnwantedSite)[];
  algorithm: "minimal_substitutions";
  candidate_budget: number;
  max_edits: number | null;
};

export type CyclingBlock = {
  repetitions: number;
  steps: (CyclingStep)[];
};

export type CyclingProgram = {
  name: string;
  provenance: string | null;
  blocks: (CyclingBlock)[];
};

export type CyclingStep = {
  temperature_C: number;
  duration_seconds: number;
};

export type DNAAmount = {
  value: number;
  unit: "ng" | "pmol";
};

export type DNAStock = {
  value: number;
  unit: "ng/uL" | "pmol/uL" | "nM" | "uM";
};

export type Dilution = {
  factor: number;
  preparation_volume_uL: number | null;
};

export type ReactionPart = {
  part_id: string;
  length_bp: number | null;
  amount: DNAAmount | null;
  ratio_to_reference: number | null;
  stock: DNAStock | null;
  dilution: Dilution;
  in_mastermix: boolean;
  phosphorylation: "unknown" | "phosphorylated" | "unphosphorylated";
  purification: string | null;
};

export type ReactionReagent = {
  reagent_id: string;
  name: string;
  role: "restriction_enzyme" | "ligase" | "buffer" | "additive" | "other";
  formulation: string | null;
  volume_uL: number | null;
  stock_multiple: number | null;
  final_multiple: number | null;
  in_mastermix: boolean;
};

export type ReactionSettings = {
  schema_version: "golden-gate-reaction/v1";
  mass_basis_g_per_mol_bp: number;
  reference_part_id: string | null;
  total_volume_uL: number | null;
  reaction_count: number;
  mastermix_overage_percent: number;
  water_in_mastermix: boolean;
  rounding_step_uL: number | null;
  minimum_transfer_uL: number | null;
  cycling: CyclingProgram | null;
};

export type ReactionRequest = {
  settings: ReactionSettings;
  parts: (ReactionPart)[];
  reagents: (ReactionReagent)[];
};

// These are exact native callable arguments, retained separately until the
// receiving workflow's final HTTP union is paired. No invented transport body.
export type EndInstance = {
  instance_id: string; sequence: string | null; role: string;
  intended_junction_id: string | null; polarity: string | null;
  removed: boolean; phosphorylation: string | null;
};
export type SearchSettings = {
  seed: number; evaluation_budget: number; restarts: number; exact_limit: number;
  alternatives: number; unique_classes: boolean; exclude_palindromes: boolean;
  ranking_mode: 'empirical' | 'lexicographic';
};
export type FidelitySettings = {
  dataset_id: string | null; condition_use: 'reference' | 'explicit_proxy';
  junctions: string[]; inventory: EndInstance[]; inventory_complete: boolean;
  include_pair_observations: boolean;
};
export type OverhangSearch = {
  candidate_domain: string[]; junction_count: number; end_length: number;
  required: string[]; fixed: string[]; excluded: string[]; settings: SearchSettings;
};
export type WindowSearch = {
  sequence: string; windows: [number, number][]; end_length: number;
  topology: 'linear' | 'circular'; fixed_positions: (number | null)[];
  fixed_overhangs: string[]; required: string[]; excluded: string[];
  protected_regions: [number, number][]; frame_constraints: [number, number, number, number][];
  min_fragment_length: number; max_fragment_length: number | null;
  target_fragment_length: number | null; settings: SearchSettings;
};
export type NativeScienceSettings = {
  fidelity: FidelitySettings; overhang_search: OverhangSearch | null;
  window_search: WindowSearch | null;
  domestication: { source_id: string; settings: DomesticationSettings }[];
  reaction: ReactionRequest | null;
};
export type GoldenGateDraft = { core: GoldenGateDesignRequest; science: NativeScienceSettings };
export type SourceChoice = { label: string; source: Source };
export type Preparation = Part['preparation'];
export type PreparationChoices = Record<Preparation['kind'], Preparation>;
export type DatasetChoice = { id: string; label: string; description: string };

/** Shape/syntax checks only. Catalog/geometry/PCR remain native authority. */
export function validateCoreRequest(r: GoldenGateDesignRequest): string[] {
  const errors: string[] = [];
  const dna = (v: string, label: string, empty = false) => {
    if ((!empty && !v) || !/^[ACGTRYSWKMBDHVN]*$/i.test(v)) errors.push(`${label}: use IUPAC DNA letters only`);
  };
  const integer = (v: number, min: number, label: string) => {
    if (!Number.isInteger(v) || v < min) errors.push(`${label}: integer ≥ ${min} required`);
  };
  const unique = (ids: string[], label: string) => {
    if (ids.some(id => !id) || new Set(ids).size !== ids.length) errors.push(`${label}: nonempty unique IDs required`);
  };
  if (r.schema_version !== 'bms.golden-gate-design.v1' || r.task !== 'assemble_parts') errors.push('Unsupported core version/task');
  unique(r.sources.map(s => s.id), 'Sources'); unique(r.parts.map(p => p.id), 'Parts');
  if (!r.parts.length) errors.push('Select at least one part');
  for (const s of r.sources) {
    if (s.source.kind === 'inline') dna(s.source.sequence, s.id);
    else if (!s.source.revision_id) errors.push(`${s.id}: select an immutable revision`);
  }
  for (const p of r.parts) {
    const source = r.sources.find(s => s.id === p.source_id)?.source;
    if (!source) errors.push(`${p.id}: source not found`);
    const prep = p.preparation;
    if (prep.kind === 'pcr' || prep.kind === 'synthesis') {
      const region = prep.region;
      integer(region.start, 0, `${p.id} start`); integer(region.end, 0, `${p.id} end`);
      if (!region.wraps_origin && region.end <= region.start) errors.push(`${p.id}: end must exceed start`);
      if (region.wraps_origin && region.end > region.start) errors.push(`${p.id}: wrapped end must be ≤ start`);
      if (source?.kind === 'inline') {
        if (region.start >= source.sequence.length || region.end > source.sequence.length) errors.push(`${p.id}: region outside source`);
        if (region.wraps_origin && source.topology !== 'circular') errors.push(`${p.id}: wrapping requires a circular source`);
      }
      for (const side of ['left', 'right'] as const) for (const key of ['clamp', 'spacer', 'fusion'] as const) dna(prep[side][key], `${p.id} ${side} ${key}`, true);
      if (prep.kind === 'pcr') {
        integer(prep.forward_anneal_length, 8, 'Forward footprint');
        integer(prep.reverse_anneal_length, 8, 'Reverse footprint');
        integer(prep.qc_min_binding_anneal_length, 1, 'QC minimum binding');
      }
    }
    if (prep.kind === 'donor') integer(prep.retained_fragment_index, 0, 'Retained fragment');
    if ('removed_fragment_indices' in prep) prep.removed_fragment_indices.forEach(i => integer(i, 0, 'Removed fragment'));
    if (prep.kind === 'prepared') for (const end of [prep.left_end, prep.right_end]) dna(end.overhang, 'Physical overhang', true);
  }
  integer(r.target.display_origin, 0, 'Display origin');
  if (r.target.exact_sequence !== null) dna(r.target.exact_sequence, 'Exact target');
  for (const [key, v] of Object.entries(r.primer_settings)) {
    if (typeof v === 'number' && (!Number.isFinite(v) || v < 0)) errors.push(`${key}: finite nonnegative value required`);
  }
  if (r.primer_settings.primer_concentration_nM <= 0) errors.push('Primer concentration must be positive');
  if (r.primer_settings.dmso_percent > 100 || r.primer_settings.formamide_percent > 100) errors.push('Solvent percentages must be ≤ 100');
  if (!r.enzyme.enzyme_id || !r.enzyme.catalog_id || !r.enzyme.catalog_sha256) errors.push('Select a catalog-bound enzyme');
  return errors;
}

/** Digest choices survive only selection/removal edits, never material changes. */
export function reusableDigestChoices(previous: GoldenGateDesignRequest, current: GoldenGateDesignRequest, digests: DigestOutcome[]): DigestOutcome[] {
  if (previous.enzyme !== current.enzyme) return [];
  return digests.filter(d => {
    const before = previous.parts.find(p => p.id === d.part_id);
    const after = current.parts.find(p => p.id === d.part_id);
    if (!before || !after || before.source_id !== after.source_id || before.orientation !== after.orientation) return false;
    const source = previous.sources.find(s => s.id === before.source_id);
    if (source !== current.sources.find(s => s.id === after.source_id)) return false;
    const a = before.preparation; const b = after.preparation;
    if (a.kind === 'donor' && b.kind === 'donor') return true;
    if ((a.kind === 'pcr' || a.kind === 'synthesis') && b.kind === a.kind) {
      if (a.region !== b.region || a.left !== b.left || a.right !== b.right) return false;
      return a.kind !== 'pcr' || (b.kind === 'pcr' && a.forward_anneal_length === b.forward_anneal_length && a.reverse_anneal_length === b.reverse_anneal_length && a.qc_min_binding_anneal_length === b.qc_min_binding_anneal_length);
    }
    return a === b;
  });
}

export function compileCoreRequest(r: GoldenGateDesignRequest): GoldenGateDesignRequest {
  const errors = validateCoreRequest(r);
  if (errors.length) throw new Error(errors.join('; '));
  // Snapshot without normalizing buffers, DNA, source identity or null values.
  return structuredClone(r);
}

export type DomesticationResult = {
  settings: DomesticationSettings;
  status: 'disabled' | 'unchanged' | 'proposal' | 'no_proposal_found';
  proposed_sequence: string | null;
  edits: { position: number; original: string; proposed: string; affected_feature_ids: string[] }[];
  translations: { feature_id: string; original: string; proposed: string | null; original_start_codon: string | null; proposed_start_codon: string | null }[];
  candidates_evaluated: number; search_complete: boolean; minimal_edits_proven: boolean;
  diagnostics: string[]; engine: string | null;
};
/** Read-only projection of actual native fidelity output; never a score engine. */
export type FidelityResult = {
  status: 'available' | 'unavailable'; f_set: number | null;
  dataset_id: string | null; metric_version: string; condition_use: string;
  scope: string; caveats: string[]; reasons: string[]; junctions: string[];
  inventory: EndInstance[]; inventory_complete: boolean; unmodeled_inventory_instance_ids: string[];
  dataset?: { id: string; end_length: number; thermal_profile: string; buffer: string; restriction_enzyme_variant: string; ligation_enzyme: string; doi: string; sha256: string } | null;
  log_f_set?: number | null; exact_zero_observed_correct?: boolean;
  pair_observations?: { a: string; b: string; observations: number; watson_crick: boolean }[];
  repeated_classes?: string[]; palindromes?: string[];
  per_junction?: { representative: string; complement: string; correct_observations: number; total_observations: number; probability: number | null }[];
  joining_bias?: { representative: string; pooled_wc_observations: number; relative_to_dataset_max_pooled_wc: number | null }[];
};
export type ReactionRow = {
  component_id: string; kind: 'dna' | 'reagent' | 'water';
  requested_pmol: number | null; requested_mass_ng: number | null;
  stock_pmol_per_uL: number | null; diluted_stock_pmol_per_uL: number | null;
  stock_ng_per_uL: number | null; diluted_stock_ng_per_uL: number | null;
  exact_volume_uL: number | null; transfer_volume_uL: number | null;
  delivered_pmol: number | null; delivered_mass_ng: number | null;
  delivered_ratio_to_reference: number | null; dilution_stock_uL: number | null;
  dilution_diluent_uL: number | null; batch_volume_uL: number | null; mastermix_volume_uL: number | null;
};
export type ReactionResult = {
  request: ReactionRequest; rows: ReactionRow[]; known_transfer_subtotal_uL: number;
  total_transfer_uL: number | null; water_exact_uL: number | null; mastermix_total_uL: number | null;
  diagnostics: { code: string; component_id: string | null; message: string }[];
  mass_basis_description: string;
};

/** Actual native optimizer output projection (no invented candidate IDs). */
export type SearchSolution = {
  junctions: string[]; choices: number[]; f_set: number | null; log_f_set: number | null;
  cuts?: { position: number; sequence: string }[]; fragment_lengths?: number[]; size_penalty?: number;
};
export type SearchResult = {
  dataset_id: string | null; metric_version: string; condition_use: string; scope: string;
  settings: SearchSettings; diagnostics: string[]; status: string; solutions: SearchSolution[];
  search_scope: { algorithm: string; domain_size: number; attempted: number; scored: number; complete: boolean; optimality_proven: boolean; tie_break: string; objective: string };
};
