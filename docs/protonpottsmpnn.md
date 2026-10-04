# ProtonPottsMPNN native redesign

Public identity: `protonpottsmpnn`, mode `redesign`, workflow
`protonpottsmpnn_design`, native contract `protonpottsmpnn_design.v1`.
Source is pinned to `09682abfa7d20e0abcdeea0490b7a4b1c190aee3`.

## Authorities

- `scripts/lib/protonpottsmpnn_contract.py`: stdlib canonical normalization,
  shared by API and native runner. No optimization/scoring implementation.
- `schemas/protonpottsmpnn_parameters.v1.json`: closed public science schema
  returned by existing model discovery, including nested UI metadata.
- `schemas/protonpottsmpnn_request.v1.json`: portable native request envelope.
- `schemas/protonpottsmpnn_default_request.v1.json`: editable shipped v6 example
  profile (illustrative source path/digest must be replaced during preparation).
- `schemas/protonpottsmpnn_results.v1.json`: native result members, not a
  prediction/structure surrogate.
- `platform/api/config/models/protonpottsmpnn.yaml`: model discovery/defaults.

`criteria` is a list of full native PHDesignCriteria, not a single reduced
objective. Individual missing fields retain native dataclass defaults; the
initial default list uses the upstream shipped v6 example, including HIS-S
rather than historical HID/HIE. Schema metadata distinguishes native defaults
from example-profile defaults. No pH affinity/calibration or acceptance threshold
is inferred from the native selective-energy objective.

## Full native criteria inventory

- `backend`: "potts" (energy head) | "mpnn" (decoder field) Native default: `"potts"`.
- `selective_source`: Where the AUTOREGRESSIVE (method=autoregressive) designer takes its SELECTIVITY signal from: "potts"   — (default; current behaviour) the Potts centre gap Σ(e_P−e_D), z-scaled against the decoder naturalness term:  J = (1−λ)·z(−log p) + λ·z(Σ(e_P−e_D)). "decoder" — a PURE-decoder two-state probability contrast, no Potts and no z-scaling: R(a) = p(a | all centers = TARGET) − λ · p(a | all centers = OFF) where both are the decoder's own softmax(log_probs) distributions on one simplex (target = pin at prot_idx; off = pin at dep_idxs, averaged over tautomers e.g. HID/HIE). λ=0 recovers the plain target-state decode ("no selective objective"). Valid ONLY for method=autoregressive (block descent has no pluggable [V] row). Native default: `"potts"`.
- `center_protonation_types`: Which protonated microstate to introduce at the (designed) centre, then redesign around. Native default: `["HIS-P", "ASP-P", "GLU-P"]`.
- `dep_map`: Native PHDesignCriteria. Native default: `{"HIS-P": ["HID", "HIE"], "ASP-P": ["ASP-D"], "GLU-P": ["GLU-D"]}`.
- `topk_sites`: Native PHDesignCriteria. Native default: `2`.
- `placement_seq_masked`: Placement-site selection (which binder position to introduce RES-P at): False (default) — score candidate sites against the current binder sequence (initial_sequence). True  — "structure-only" placement: mask ALL binder design positions to UNK first, so each site's selective score LL(RES-P) - LL(best RES-D) depends on the backbone (+ fixed target) and NOT the current binder sequence. With backend=mpnn this score is the decoder log-likelihood. Pair with method=autoregressive + backend=mpnn to "place the protonation by structure-only LL, then resample the site's neighbourhood with the MPNN head, the mutated site held fixed". (selective=True is required for the gap.) Native default: `false`.
- `method`: Native PHDesignCriteria. Native default: `"converged_mcmc"`.
- `selective`: Native PHDesignCriteria. Native default: `true`.
- `combined_lambda`: Native PHDesignCriteria. Native default: `1.0`.
- `two_phase_frac`: Native PHDesignCriteria. Native default: `0.5`.
- `temperature`: Native PHDesignCriteria. Native default: `0.1`.
- `samples_per_site`: placement methods: designs per (centre type, site, seed) Native default: `4`.
- `cv_patience`: converged: stop after patience*n_neigh no-change Native default: `3`.
- `cv_max`: converged: hard cap = cv_max*n_neigh moves Native default: `50`.
- `block_size`: block_descent (deterministic / sampled, near-exact MAP): each block = one neighbour + its (block_size-1) closest coupled partners; enumerate ALL V**block_size joint assignments and score them by the Z-SCALED combined objective — the MANUSCRIPT Eq (6): J = (1-combined_lambda)*z(H) + combined_lambda*z(selective) + global_weight*z(global) where z(x) = (x - mean)/std of that term's single-mutation deltas (computed ONCE over the design positions and frozen). Z-scaling puts stability and selectivity on a common spread, so combined_lambda is a true RELATIVE weight (0 -> pure stability, 1 -> pure selectivity, 0.5 -> balanced in std units). Readout temperature `temperature`: 0 -> argmin (deterministic), >0 -> sample the block ~ softmax(-J/temperature) (Boltzmann, like sampling). block_size=1 -> greedy ICM; 2 -> pairwise; 3 -> triples; larger -> closer to the joint optimum. Only STABILITY has within-block pairwise terms (selective+global are exactly unary), so increasing block_size changes the answer ONLY through stability coupling. Native default: `2`.
- `global_weight`: extra z-scaled global term (0 = report-only) Native default: `0.0`.
- `rank_normalize`: use percentile (rank) scaling instead of z-score Native default: `false`.
- `zscale_mode`: z-scale reference distribution for the frozen combined objective (block_descent): "block" (default) — std of the BLOCK (V**block_size) joint spread, pooled WITHIN-block; captures the within-block pairwise stability variance (the honest scale for the objective). "single_mutation" — legacy cheap proxy: std of single-position deltas over the design set (kept to compare against / fall back to; note it re-calibrates λ vs block mode). Native default: `"block"`.
- `adjacent_repeat_weight`: Adjacent-repeat bias: +adjacent_repeat_weight per SEQUENCE-ADJACENT (i,i+1) pair that ends up the SAME amino acid (canonical; ASP-P/ASP-D both = Asp). 0 = off. Discourages i,i+1 homopolymer repeats; added raw to the (z-scaled) block objective, so it is a knob in J's z-units. See _block_descent. Native default: `0.0`.
- `repetitive_window_weight`: Windowed repetitive-density penalty (anti-collapse; block_descent only, 0 = off). Penalises clustering of a residue class: +weight per class residue within ±radius sequence positions. ``repetitive_window_parents`` = 3-letter canonical parents to disperse, e.g. ["ASP","GLU"], ["ARG","LYS"], ["HIS"]. ``repetitive_window_gate_types`` limits it to a pinned centre type (EMPTY = always on when weight>0). See _block_descent / _parent_vocab_mask. Native default: `0.0`.
- `repetitive_window_radius`: Native PHDesignCriteria. Native default: `2`.
- `repetitive_window_parents`: Native PHDesignCriteria. Native default: `[]`.
- `repetitive_window_gate_types`: Native PHDesignCriteria. Native default: `[]`.
- `self_weight`: Relative weight of the Potts SELF-fields (single-site h_i) vs the pairwise couplings J_ij in the STABILITY term: H_stab = self_weight·Σh_i + Σ J_ij (pairwise fixed at 1). 1.0 = current behaviour. <1 down-weights the self bias (context-aware design; tests whether poly-acidic collapse is a self-field artefact), >1 amplifies it. Scope is stability ONLY — selective/global/reported energies stay on the true model (the self-field cancels in e_P−e_D anyway). Because H_stab is z-scored in J, only the self:pair RATIO matters (uniform scale is divided out), so self_weight==1 is an exact no-op. See _block_descent / _PottsScorer.reweighted. Native default: `1.0`.
- `block_max_rounds`: sweeps over all neighbours before giving up Native default: `10`.
- `seed_source`: Where the placement redesign starts from (the "initial_sequence" per the 05 scripts): "inverse" — externally supplied seeds = the inverse stage's sequences (ProteinMPNN, or the model's own MPNN sampling); passed into run_ph_redesign as initial_sequences. This is the decoupled "run the inverse stage, then only optimize" path. "native"  — the single native (input-PDB / RFD3) sequence. MPNN-sampled seeds are produced by the INVERSE stage, never re-sampled inside the optimizer. Native default: `"inverse"`.
- `forbidden_tokens`: Native PHDesignCriteria. Native default: `["HIS-A", "ASP-A", "GLU-A", "HIS-D"]`.
- `num_designs`: whole-chain (gibbs/mpnn_sample) count / run Native default: `8`.
- `record_trajectory`: Record the per-step energy trajectory (selective energy, global protonation ΔH, total Potts H) of the placement redesign so you can see the optimization "under the hood". OFF by default: each recorded step adds ~selective + 3×H_of + 1×H_of Potts evals, so it ~doubles the (already CPU-heavy) per-move cost — use it with a small config (few seeds, samples_per_site=1). Native default: `false`.
- `center_count`: --- multi-center placement (pin a SET of protonated centers, redesign the union of their neighbourhoods). center_count == 1 reproduces the single-center behavior. --- Native default: `1`.
- `explicit_centers`: explicit_centers: [{res_id, protonation_type}, ...] -> exactly ONE plan with those pins, bypassing the random site search (mix-and-match by hand). Its length overrides center_count. Native default: `[]`.
- `center_types`: center_types: an EXACT protonation-type multiset e.g. ["ASP-P","ASP-P","HIS-P"] to place. Unlike center_protonation_types (a type ALPHABET the combo logic mixes freely), this pins the exact COMPOSITION while letting placement (placement_by/region) CHOOSE the positions — one distinct, best-ranked site per type. Produces exactly ONE plan. [] = off (use the pool/combo logic). Native default: `[]`.
- `placement_region`: placement region(s) candidate centers are drawn from (union): subset of {interface, core, surface, all}. Region masks come from RASA + target-contact (mirrors src/rewards/property_calculators.py). "all" == all free binder positions. Native default: `["all"]`.
- `placement_by`: How candidate center positions are chosen within the region (decoupled from the redesign method): "random"     — uniform over the region. "scan_potts" — the existing _placement_sites ranked by Potts dE_p - dE_d. "scan_mpnn"  — the same finder ranked by the PottsMPNN decoder log-lik(prot) - log-lik(deprot). Native default: `"scan_potts"`.
- `placement_label`: Informational label for the drawn placement TYPE (e.g. "core"/"interface"/"random"/"scan"), set by sample_criteria so each design carries one clean placement-attribution column. "" = unset. Native default: `""`.
- `candidate_pool`: Candidate center positions come from the existing selective ranking (`_placement_sites`, generalized over the region): the potts backend ranks by dE_p - dE_d; PottsMPNN's MPNN backend ranks by log-lik(protonated) - log-lik(deprotonated). candidate_pool = top-ranked positions per protonation type that feed the multi-center combinations. Native default: `12`.
- `n_plan_samples`: cap on center combinations evaluated (random subsample if larger) Native default: `64`.
- `max_plans_per_seed`: plans kept after ranking combos by locked Σ_centers (e_P - e_D) Native default: `8`.
- `infill_scope`: "neighbourhood" (∪ pin kNN) | "chain" (whole free binder) Native default: `"neighbourhood"`.
- `sweep_order`: sweep_order: order block descent visits designable positions (greedy → order matters). "position" — ascending residue index; "knn" — closest-coupled first; "energy" — largest contribution to the centres' selective gap (e_P − e_D) first. Native default: `"position"`.
- `neighbour_k`: neighbour_k: per-centre cap on how many coupled kNN neighbours become designable — controls the redesign extent / mutation load. 0 = full neighbourhood (default). Ignored when infill_scope=="chain". Native default: `0`.
- `max_mutations`: HARD cap on the TOTAL number of designable positions (across all centres) → a direct MAX-MUTATIONS budget (block descent redesigns the designable set, so n_mutations <= max_mutations). Unlike neighbour_k (a PER-centre kNN cap), this bounds the whole redesign; when the union exceeds it, keep the max_mutations positions CLOSEST-coupled to the centres (by _knn_rank). 0 = no cap (default no-op). Sweepable (see _axis_expand / the sampling block). Native default: `0`.

## Engine and runtime settings

All applicable `potts_mpnn.MPNNInferenceEngine` constructor science settings are exposed
under `engine_options`: `extended_vocab`, `field_source`, `etab_source`,
`etab_hidden`, `field_hidden`. Defaults are v6, self_edge, null, null, null.
`extended_vocab=v6` is fixed by the shipped 30-token checkpoint; field/head
settings remain explicit, with native compatibility/load errors unchanged.
Inherited `write_fasta` and `write_structures` are public output controls;
`out_directory` is owned by workflow publication, `checkpoint_path` by the
shipped image, and `device` by destination scheduling. PHDesignInferenceEngine adds
no constructor controls. The separate legacy ProteinMPNN engine is not an
ancestor of `potts_mpnn.MPNNInferenceEngine`, so model_type/is_legacy_weights are not
invented constructor arguments. `seed=0` and optional `initial_sequences`
are native per-run controls. No scientific `n_jobs` input exists: the runner
receives `--n-jobs ${task.cpus}`.

## Selected candidates, custody, and replay

`POST /api/binder-continuation/{job_id}/launch-selected` accepts operation
`protonpottsmpnn` for any saved structure candidate, not only antibodies.
The existing snapshot-selection and Project child submission owners create
one native redesign child per selected source and preserve root/source Design
lineage. Explicit `execution_target_id=null` stays local instead of inheriting
the source worker. Standalone Jobs use the same normalizer and compiler.

Preparation copies the selected PDB/mmCIF bytes into owned input custody and
writes `{contract,options,source:{requested_path,sha256}}`. Saved/retry/clone
requests retain all criteria, engine controls, nulls, seed and output controls.
When retained request/source are present, archived requested_path and selection
metadata are provenance, not files to reopen. Existing portable input discovery
copies and rewrites only the owned request/source pair. Native components list
only this image/runner/contract; there are no predictor/MSA assets.

Image selector: `protonpottsmpnn.sif` maps to
`protonpottsmpnn_container_path` / `BMS_PROTONPOTTSMPNN_CONTAINER_PATH` through
the existing remote image owner. Existing remote return/result contract and
JobArtifact publication own `protonpottsmpnn_design/manifest.json` and native
artifacts; no new coordinator or result store is introduced.

New remote requests use CUDA and the existing single-GPU allocator; local requests
remain CPU by default. Explicit Local resets a copied remote device selection.
`BMS_PROTONPOTTSMPNN_DEVICE=cuda` can enable local GPU placement later without
changing scientific options. The runtime-only selection is retained separately
from the native request. CUDA uses `--nv`, the scheduler's physical visibility,
and native `--device cuda:0`; CPU clears GPU visibility and passes `--device cpu`.
Native CUDA execution stays on its CUDA context; the upstream process-pool
parallel solver is used only for CPU contexts. The scheduler-assigned CPU count
is passed unchanged, without rewriting native GPU concurrency or science.
The CUDA reservation is a scheduling estimate, not a measured VRAM claim.

Absent a selected installation release, the image owner reads the immutable
filename/SHA from `apptainer/protonpottsmpnn-runtime.lock.json`. Selected managed
releases still take precedence. This one self-contained image is in the selected
stage's dependency closure and uses the existing HF delivery/cache owner; no
separate checkpoint or unrelated prediction/MSA download is needed. The pinned
image is CUDA 13.0-capable and CPU-compatible; its real CPU example/sweep match
the CPU reference exactly. Actual GPU inference has not been exercised on this
host. Remote drivers must be compatible with the image's CUDA 13 build.

## Results and optional follow-on prediction

`GET /api/jobs/{id}/protonpottsmpnn/results` returns the native manifest.
It retains full `dataclasses.asdict(PHDesignOutput)` per row, source/request
identity, criteria index, unique per-source/criteria/sample design_id, original
native_design_id, seed energies, runtime identity and relative artifacts.
Native metrics retain native names and semantics, including protonation states,
selective energies and optional trajectory. No structureless row becomes a
PDB-backed `Design`, and refolding is not required for viewing/selecting output.

Parent-owned sequence prediction adapter reuses existing prediction/round and
Project submission owners. Helpers: async
`services.protonpottsmpnn_design.read_result(session,job_id)` and
`prepared_source_path(job)` (returns owned retained Path, never archived input).
Native unique IDs are sequence names, not original Design IDs.

## Verification boundary

Backend tests exercise owning normalization/routes, actual SQLite Job/Project
child insertion, offline-original clone/compiler and remote portable-input
roundtrip, native JobArtifact publication and exact endpoint payload shape.
`tests/test_protonpottsmpnn_execution.py` runs real Nextflow/Java staging,
CPU/CUDA binding with an explicit command fixture, and actual checkpoint inference
when `BMS_PROTON_RUNTIME_IMAGE`, `BMS_PROTON_RUNTIME_INPUT`, and
`BMS_PROTON_RUNTIME_REQUEST` are supplied. The real manifests also pass the BMS
reader and JobArtifact publication/readback. Command-fixture GPU success is not
GPU scientific execution. No rental, driver repair, push or deployment is performed.
