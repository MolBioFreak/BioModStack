/** Native PHDesignCriteria at 09682ab; lists on sweep axes remain lists. */
export type Sweep<T> = T | T[];
export interface PHDesignCriteria {
    backend: Sweep<"potts" | "mpnn">;
    selective_source: Sweep<"potts" | "decoder">;
    center_protonation_types: string[];
    dep_map: Record<string, string[]>;
    topk_sites: number;
    placement_seq_masked: Sweep<boolean>;
    method: Sweep<"autoregressive" | "converged_mcmc" | "two_phase" | "converged_mcmc_combined" | "block_descent" | "greedy_energy_block" | "gibbs" | "mpnn_sample">;
    selective: Sweep<boolean>;
    combined_lambda: Sweep<number>;
    two_phase_frac: Sweep<number>;
    temperature: Sweep<number>;
    samples_per_site: Sweep<number>;
    cv_patience: Sweep<number>;
    cv_max: Sweep<number>;
    block_size: Sweep<number>;
    global_weight: Sweep<number>;
    rank_normalize: boolean;
    zscale_mode: Sweep<"block" | "single_mutation">;
    adjacent_repeat_weight: Sweep<number>;
    repetitive_window_weight: Sweep<number>;
    repetitive_window_radius: Sweep<number>;
    repetitive_window_parents: string[];
    repetitive_window_gate_types: string[];
    self_weight: Sweep<number>;
    block_max_rounds: Sweep<number>;
    seed_source: "inverse" | "native";
    forbidden_tokens: string[];
    num_designs: number;
    record_trajectory: boolean;
    center_count: Sweep<number>;
    explicit_centers: { res_id: number; protonation_type: string }[];
    center_types: string[];
    placement_region: string[];
    placement_by: Sweep<"random" | "scan_potts" | "scan_mpnn">;
    placement_label: string;
    candidate_pool: Sweep<number>;
    n_plan_samples: Sweep<number>;
    max_plans_per_seed: Sweep<number>;
    infill_scope: Sweep<"neighbourhood" | "chain">;
    sweep_order: "position" | "knn" | "energy";
    neighbour_k: Sweep<number>;
    max_mutations: Sweep<number>;
}
export const criteriaFields = [
    {"name": "backend", "type": "str", "default": "potts", "sweep": true, "choices": ["potts", "mpnn"], "description": "\"potts\" (energy head) | \"mpnn\" (decoder field)"},
    {"name": "selective_source", "type": "str", "default": "potts", "sweep": true, "choices": ["potts", "decoder"], "description": "Where the AUTOREGRESSIVE (method=autoregressive) designer takes its SELECTIVITY signal from: \"potts\"   — (default; current behaviour) the Potts centre gap Σ(e_P−e_D), z-scaled against the decoder naturalness term:  J = (1−λ)·z(−log p) + λ·z(Σ(e_P−e_D)). \"decoder\" — a PURE-decoder two-state probability contrast, no Potts and no z-scaling: R(a) = p(a | all centers = TARGET) − λ · p(a | all centers = OFF) where both are the decoder's own softmax(log_probs) distributions on one simplex (target = pin at prot_idx; off = pin at dep_idxs, averaged over tautomers e.g. HID/HIE). λ=0 recovers the plain target-state decode (\"no selective objective\"). Valid ONLY for method=autoregressive (block descent has no pluggable [V] row)."},
    {"name": "center_protonation_types", "type": "List[str]", "default": ["HIS-P", "ASP-P", "GLU-P"], "sweep": false, "description": "Which protonated microstate to introduce at the (designed) centre, then redesign around."},
    {"name": "dep_map", "type": "Dict[str, List[str]]", "default": {"HIS-P": ["HID", "HIE"], "ASP-P": ["ASP-D"], "GLU-P": ["GLU-D"]}, "sweep": false, "description": "Native PHDesignCriteria."},
    {"name": "topk_sites", "type": "int", "default": 2, "sweep": false, "description": "Native PHDesignCriteria."},
    {"name": "placement_seq_masked", "type": "bool", "default": false, "sweep": true, "description": "Placement-site selection (which binder position to introduce RES-P at): False (default) — score candidate sites against the current binder sequence (initial_sequence). True  — \"structure-only\" placement: mask ALL binder design positions to UNK first, so each site's selective score LL(RES-P) - LL(best RES-D) depends on the backbone (+ fixed target) and NOT the current binder sequence. With backend=mpnn this score is the decoder log-likelihood. Pair with method=autoregressive + backend=mpnn to \"place the protonation by structure-only LL, then resample the site's neighbourhood with the MPNN head, the mutated site held fixed\". (selective=True is required for the gap.)"},
    {"name": "method", "type": "str", "default": "converged_mcmc", "sweep": true, "choices": ["autoregressive", "converged_mcmc", "two_phase", "converged_mcmc_combined", "block_descent", "greedy_energy_block", "gibbs", "mpnn_sample"], "description": "Native PHDesignCriteria."},
    {"name": "selective", "type": "bool", "default": true, "sweep": true, "description": "Native PHDesignCriteria."},
    {"name": "combined_lambda", "type": "float", "default": 1.0, "sweep": true, "description": "Native PHDesignCriteria."},
    {"name": "two_phase_frac", "type": "float", "default": 0.5, "sweep": true, "description": "Native PHDesignCriteria."},
    {"name": "temperature", "type": "float", "default": 0.1, "sweep": true, "description": "Native PHDesignCriteria."},
    {"name": "samples_per_site", "type": "int", "default": 4, "sweep": true, "description": "placement methods: designs per (centre type, site, seed)"},
    {"name": "cv_patience", "type": "int", "default": 3, "sweep": true, "description": "converged: stop after patience*n_neigh no-change"},
    {"name": "cv_max", "type": "int", "default": 50, "sweep": true, "description": "converged: hard cap = cv_max*n_neigh moves"},
    {"name": "block_size", "type": "int", "default": 2, "sweep": true, "description": "block_descent (deterministic / sampled, near-exact MAP): each block = one neighbour + its (block_size-1) closest coupled partners; enumerate ALL V**block_size joint assignments and score them by the Z-SCALED combined objective — the MANUSCRIPT Eq (6): J = (1-combined_lambda)*z(H) + combined_lambda*z(selective) + global_weight*z(global) where z(x) = (x - mean)/std of that term's single-mutation deltas (computed ONCE over the design positions and frozen). Z-scaling puts stability and selectivity on a common spread, so combined_lambda is a true RELATIVE weight (0 -> pure stability, 1 -> pure selectivity, 0.5 -> balanced in std units). Readout temperature `temperature`: 0 -> argmin (deterministic), >0 -> sample the block ~ softmax(-J/temperature) (Boltzmann, like sampling). block_size=1 -> greedy ICM; 2 -> pairwise; 3 -> triples; larger -> closer to the joint optimum. Only STABILITY has within-block pairwise terms (selective+global are exactly unary), so increasing block_size changes the answer ONLY through stability coupling."},
    {"name": "global_weight", "type": "float", "default": 0.0, "sweep": true, "description": "extra z-scaled global term (0 = report-only)"},
    {"name": "rank_normalize", "type": "bool", "default": false, "sweep": false, "description": "use percentile (rank) scaling instead of z-score"},
    {"name": "zscale_mode", "type": "str", "default": "block", "sweep": true, "choices": ["block", "single_mutation"], "description": "z-scale reference distribution for the frozen combined objective (block_descent): \"block\" (default) — std of the BLOCK (V**block_size) joint spread, pooled WITHIN-block; captures the within-block pairwise stability variance (the honest scale for the objective). \"single_mutation\" — legacy cheap proxy: std of single-position deltas over the design set (kept to compare against / fall back to; note it re-calibrates λ vs block mode)."},
    {"name": "adjacent_repeat_weight", "type": "float", "default": 0.0, "sweep": true, "description": "Adjacent-repeat bias: +adjacent_repeat_weight per SEQUENCE-ADJACENT (i,i+1) pair that ends up the SAME amino acid (canonical; ASP-P/ASP-D both = Asp). 0 = off. Discourages i,i+1 homopolymer repeats; added raw to the (z-scaled) block objective, so it is a knob in J's z-units. See _block_descent."},
    {"name": "repetitive_window_weight", "type": "float", "default": 0.0, "sweep": true, "description": "Windowed repetitive-density penalty (anti-collapse; block_descent only, 0 = off). Penalises clustering of a residue class: +weight per class residue within ±radius sequence positions. ``repetitive_window_parents`` = 3-letter canonical parents to disperse, e.g. [\"ASP\",\"GLU\"], [\"ARG\",\"LYS\"], [\"HIS\"]. ``repetitive_window_gate_types`` limits it to a pinned centre type (EMPTY = always on when weight>0). See _block_descent / _parent_vocab_mask."},
    {"name": "repetitive_window_radius", "type": "int", "default": 2, "sweep": true, "description": "Native PHDesignCriteria."},
    {"name": "repetitive_window_parents", "type": "List[str]", "default": [], "sweep": false, "description": "Native PHDesignCriteria."},
    {"name": "repetitive_window_gate_types", "type": "List[str]", "default": [], "sweep": false, "description": "Native PHDesignCriteria."},
    {"name": "self_weight", "type": "float", "default": 1.0, "sweep": true, "description": "Relative weight of the Potts SELF-fields (single-site h_i) vs the pairwise couplings J_ij in the STABILITY term: H_stab = self_weight·Σh_i + Σ J_ij (pairwise fixed at 1). 1.0 = current behaviour. <1 down-weights the self bias (context-aware design; tests whether poly-acidic collapse is a self-field artefact), >1 amplifies it. Scope is stability ONLY — selective/global/reported energies stay on the true model (the self-field cancels in e_P−e_D anyway). Because H_stab is z-scored in J, only the self:pair RATIO matters (uniform scale is divided out), so self_weight==1 is an exact no-op. See _block_descent / _PottsScorer.reweighted.", "minimum": 0},
    {"name": "block_max_rounds", "type": "int", "default": 10, "sweep": true, "description": "sweeps over all neighbours before giving up"},
    {"name": "seed_source", "type": "str", "default": "inverse", "sweep": false, "choices": ["inverse", "native"], "description": "Where the placement redesign starts from (the \"initial_sequence\" per the 05 scripts): \"inverse\" — externally supplied seeds = the inverse stage's sequences (ProteinMPNN, or the model's own MPNN sampling); passed into run_ph_redesign as initial_sequences. This is the decoupled \"run the inverse stage, then only optimize\" path. \"native\"  — the single native (input-PDB / RFD3) sequence. MPNN-sampled seeds are produced by the INVERSE stage, never re-sampled inside the optimizer."},
    {"name": "forbidden_tokens", "type": "List[str]", "default": ["HIS-A", "ASP-A", "GLU-A", "HIS-D"], "sweep": false, "description": "Native PHDesignCriteria."},
    {"name": "num_designs", "type": "int", "default": 8, "sweep": false, "description": "whole-chain (gibbs/mpnn_sample) count / run"},
    {"name": "record_trajectory", "type": "bool", "default": false, "sweep": false, "description": "Record the per-step energy trajectory (selective energy, global protonation ΔH, total Potts H) of the placement redesign so you can see the optimization \"under the hood\". OFF by default: each recorded step adds ~selective + 3×H_of + 1×H_of Potts evals, so it ~doubles the (already CPU-heavy) per-move cost — use it with a small config (few seeds, samples_per_site=1)."},
    {"name": "center_count", "type": "int", "default": 1, "sweep": true, "description": "--- multi-center placement (pin a SET of protonated centers, redesign the union of their neighbourhoods). center_count == 1 reproduces the single-center behavior. ---"},
    {"name": "explicit_centers", "type": "List[Dict]", "default": [], "sweep": false, "description": "explicit_centers: [{res_id, protonation_type}, ...] -> exactly ONE plan with those pins, bypassing the random site search (mix-and-match by hand). Its length overrides center_count."},
    {"name": "center_types", "type": "List[str]", "default": [], "sweep": false, "description": "center_types: an EXACT protonation-type multiset e.g. [\"ASP-P\",\"ASP-P\",\"HIS-P\"] to place. Unlike center_protonation_types (a type ALPHABET the combo logic mixes freely), this pins the exact COMPOSITION while letting placement (placement_by/region) CHOOSE the positions — one distinct, best-ranked site per type. Produces exactly ONE plan. [] = off (use the pool/combo logic)."},
    {"name": "placement_region", "type": "List[str]", "default": ["all"], "sweep": false, "description": "placement region(s) candidate centers are drawn from (union): subset of {interface, core, surface, all}. Region masks come from RASA + target-contact (mirrors src/rewards/property_calculators.py). \"all\" == all free binder positions."},
    {"name": "placement_by", "type": "str", "default": "scan_potts", "sweep": true, "choices": ["random", "scan_potts", "scan_mpnn"], "description": "How candidate center positions are chosen within the region (decoupled from the redesign method): \"random\"     — uniform over the region. \"scan_potts\" — the existing _placement_sites ranked by Potts dE_p - dE_d. \"scan_mpnn\"  — the same finder ranked by the PottsMPNN decoder log-lik(prot) - log-lik(deprot)."},
    {"name": "placement_label", "type": "str", "default": "", "sweep": false, "description": "Informational label for the drawn placement TYPE (e.g. \"core\"/\"interface\"/\"random\"/\"scan\"), set by sample_criteria so each design carries one clean placement-attribution column. \"\" = unset."},
    {"name": "candidate_pool", "type": "int", "default": 12, "sweep": true, "description": "Candidate center positions come from the existing selective ranking (`_placement_sites`, generalized over the region): the potts backend ranks by dE_p - dE_d; PottsMPNN's MPNN backend ranks by log-lik(protonated) - log-lik(deprotonated). candidate_pool = top-ranked positions per protonation type that feed the multi-center combinations."},
    {"name": "n_plan_samples", "type": "int", "default": 64, "sweep": true, "description": "cap on center combinations evaluated (random subsample if larger)"},
    {"name": "max_plans_per_seed", "type": "int", "default": 8, "sweep": true, "description": "plans kept after ranking combos by locked Σ_centers (e_P - e_D)"},
    {"name": "infill_scope", "type": "str", "default": "neighbourhood", "sweep": true, "choices": ["neighbourhood", "chain"], "description": "\"neighbourhood\" (∪ pin kNN) | \"chain\" (whole free binder)"},
    {"name": "sweep_order", "type": "str", "default": "position", "sweep": false, "choices": ["position", "knn", "energy"], "description": "sweep_order: order block descent visits designable positions (greedy → order matters). \"position\" — ascending residue index; \"knn\" — closest-coupled first; \"energy\" — largest contribution to the centres' selective gap (e_P − e_D) first."},
    {"name": "neighbour_k", "type": "int", "default": 0, "sweep": true, "description": "neighbour_k: per-centre cap on how many coupled kNN neighbours become designable — controls the redesign extent / mutation load. 0 = full neighbourhood (default). Ignored when infill_scope==\"chain\"."},
    {"name": "max_mutations", "type": "int", "default": 0, "sweep": true, "description": "HARD cap on the TOTAL number of designable positions (across all centres) → a direct MAX-MUTATIONS budget (block descent redesigns the designable set, so n_mutations <= max_mutations). Unlike neighbour_k (a PER-centre kNN cap), this bounds the whole redesign; when the union exceeds it, keep the max_mutations positions CLOSEST-coupled to the centres (by _knn_rank). 0 = no cap (default no-op). Sweepable (see _axis_expand / the sampling block)."},
] as const;
export const defaultCriteria = (): PHDesignCriteria => Object.fromEntries(criteriaFields.map(field => [field.name, structuredClone(field.default)])) as unknown as PHDesignCriteria;
export interface ProtonEngineOptions { extended_vocab: string; field_source: string; etab_source: string | null; etab_hidden: number[] | null; field_hidden: number[] | null }
export interface ProtonPottsParams { target_pdb: string; binder_chain: string; criteria: PHDesignCriteria[]; seed: number; initial_sequences?: string[] | null; engine_options: ProtonEngineOptions; write_fasta: boolean; write_structures: boolean }
