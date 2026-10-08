# Conformational Mapping Orchestrator — Contract-First Phases 0–13 Implementation Specification

**Status:** Corrected document draft; implementation remains **STOPPED before Phase 0** pending independent review and separate operator authorization.
**Canonical model/profile:** `conformational_mapping`
**Canonical workflow:** `workflows/conformational_mapping.nf`
**Legacy compatibility lane:** `confornets_experimental` remains operational and behaviorally unchanged.
**Mol*:** Phase 13 only, separately authorized, and non-gating for Phases 0–12.

> **Authorization boundary for this correction pass:** Editing this plan authorizes no implementation or repository-file change other than this plan, no Phase 0 probe or test execution, no workflow or service execution/restart, no GPU job, no staging or commit, and no Mol* inspection or work. Phase 0 requires a separate recorded operator approval before any probe, test, or file creation. Every later phase requires its own separate recorded operator approval and phase-local `GO`; approval never carries forward.

---

## 0. Decision, authority, evidence, and source classification

### 0.1 Start/no-start decision

Implementation is `STOP`. Phase 0 may begin only after the separate start authorization defined in Section 5.1; once authorized, Phase 0 mirrors that start decision into `docs/reviews/conformational_mapping/phase_0_spec_check.json`. Creation of the review JSON cannot bootstrap its own authorization. Phase 0 approves runtime facts, human-readable schema definitions, and contract test vectors; it does **not** require executable schemas or passing schema tests that do not yet exist. Phase 1 creates executable schemas, validators, and characterization tests from the approved definitions/vectors.

No phase is proof that another phase exists. Each phase needs its own review JSON, named independent reviewers, explicit operator approval, and `GO`. A later phase may not inherit an intentionally failing RED test.

### 0.2 Correction-pass identity

This correction was based on:

- repository: `/home/dalab/biomodstack/biomodstack`
- branch: `test`
- HEAD observed before correction: `a69711f7e55786f3867e3952b546b3d6b8c48c11`
- target state: untracked
- required and verified starting plan SHA-256: `d3213ceadd9d465d891ba674d08dbb469734f698968b6fed579050ac271bf958`
- audited current Protenix source: `modules/protenix.nf`, SHA-256 `850ce140bdb326f4afd39708239822600567df2d7080484465287a031218057e`
- audited current FrustraMPNN source after concurrent worktree drift: `modules/frustrampnn.nf`, SHA-256 `caa56432847909e84950d86ede50cc3837167df9d50c4e91ec40e20b7a51b2ac`
- repaired Protenix source anchors: seed/sample/cycle/step and feature controls at `modules/protenix.nf:397-418`; CLI forwarding at `modules/protenix.nf:628-643`

These identities describe the document-repair snapshot, not a Phase 0 pass. A dirty shared worktree can drift; every phase must recapture its own evidence.

### 0.3 Claim-classification vocabulary

Every material requirement below is classified as one of:

- **[UG] upstream guidance:** a statement from a pinned upstream repository/document or the official USAlign help.
- **[LO] local audited observation:** behavior observed in the identified BioModStack source or, after Phase 0, authenticated local runtime evidence.
- **[BP] BioModStack policy:** a design, safety, product, or acceptance decision made here; it is not attributed to upstream authors.

An item with two tags translates upstream guidance or local evidence into a BMS requirement. Unless a material normative statement is explicitly tagged `[UG]` or `[LO]`, every schema rule, formula, phase path, threshold, test, gate, safety constraint, and acceptance decision in this document is `[BP]`. Runtime probe results may add `[LO]`; this document does not claim any probe has succeeded.

### 0.4 Immutable upstream register

| System | Immutable identity and documents | Material claims supported | Classification |
|---|---|---|---|
| Protenix | repository commit [`bytedance/Protenix@c3bfc365b3e1341a11935eddfe7bfdc308092147`](https://github.com/bytedance/Protenix/tree/c3bfc365b3e1341a11935eddfe7bfdc308092147); pinned [`docs/infer_json_format.md`](https://github.com/bytedance/Protenix/blob/c3bfc365b3e1341a11935eddfe7bfdc308092147/docs/infer_json_format.md); pinned [`docs/training_inference_instructions.md`](https://github.com/bytedance/Protenix/blob/c3bfc365b3e1341a11935eddfe7bfdc308092147/docs/training_inference_instructions.md) | input entity forms, ID/count rules, modifications/covalent bonds, MSA/template fields, inference controls, and author recommendations | [UG] |
| ConforNets | repository commit [`aqlaboratory/confornets@cba896f556354c2e8ce8090312cc4649185f5612`](https://github.com/aqlaboratory/confornets/tree/cba896f556354c2e8ce8090312cc4649185f5612); pinned [`README.md`](https://github.com/aqlaboratory/confornets/blob/cba896f556354c2e8ce8090312cc4649185f5612/README.md), [`scripts/run_diversity.py`](https://github.com/aqlaboratory/confornets/blob/cba896f556354c2e8ce8090312cc4649185f5612/scripts/run_diversity.py), [`scripts/run_mse_training.py`](https://github.com/aqlaboratory/confornets/blob/cba896f556354c2e8ce8090312cc4649185f5612/scripts/run_mse_training.py), and [`scripts/evaluate.py`](https://github.com/aqlaboratory/confornets/blob/cba896f556354c2e8ce8090312cc4649185f5612/scripts/evaluate.py); paper [`arXiv:2604.18559v1`](https://arxiv.org/abs/2604.18559v1) | supported tasks, run/step/ConforNet/sample dimensions, reference-guided behavior, and evaluation semantics | [UG] |
| FrustraMPNN | repository commit [`schoederlab/frustraMPNN@3a03cdc300bfe24c4bb70e60207118532bc73b3b`](https://github.com/schoederlab/frustraMPNN/tree/3a03cdc300bfe24c4bb70e60207118532bc73b3b); pinned [`README.md`](https://github.com/schoederlab/frustraMPNN/blob/3a03cdc300bfe24c4bb70e60207118532bc73b3b/README.md) | protein/PDB input, single-residue local-frustration scores, checkpoints, and documented class thresholds | [UG] |
| USAlign | reviewed executable version `20240730`; official [`US-align help`](https://zhanggroup.org/US-align/help/) | command and alignment-output semantics; executable hash remains a Phase 0 runtime fact | [UG] for help, [BP] for the required local pin |

Commit-pinned raw retrieval URLs for byte-level evidence are:

- Protenix: [`infer_json_format.md`](https://raw.githubusercontent.com/bytedance/Protenix/c3bfc365b3e1341a11935eddfe7bfdc308092147/docs/infer_json_format.md) and [`training_inference_instructions.md`](https://raw.githubusercontent.com/bytedance/Protenix/c3bfc365b3e1341a11935eddfe7bfdc308092147/docs/training_inference_instructions.md);
- ConforNets: [`README.md`](https://raw.githubusercontent.com/aqlaboratory/confornets/cba896f556354c2e8ce8090312cc4649185f5612/README.md), [`run_diversity.py`](https://raw.githubusercontent.com/aqlaboratory/confornets/cba896f556354c2e8ce8090312cc4649185f5612/scripts/run_diversity.py), [`run_mse_training.py`](https://raw.githubusercontent.com/aqlaboratory/confornets/cba896f556354c2e8ce8090312cc4649185f5612/scripts/run_mse_training.py), and [`evaluate.py`](https://raw.githubusercontent.com/aqlaboratory/confornets/cba896f556354c2e8ce8090312cc4649185f5612/scripts/evaluate.py);
- FrustraMPNN: [`README.md`](https://raw.githubusercontent.com/schoederlab/frustraMPNN/3a03cdc300bfe24c4bb70e60207118532bc73b3b/README.md).

A successful HTTP fetch proves link availability only. It does not prove scientific correctness, installed-runtime parity, or a Phase 0 pass.

### 0.5 Local evidence register

| Material claim | Current evidence | Classification and binding consequence |
|---|---|---|
| Legacy ConforNets is monomer-oriented and validates task/reference requirements | `workflows/confornets_experimental.nf:10-32` | [LO]; [BP] canonical adapter remains single-chain protein only |
| Legacy ConforNets uses a three-process substrate and emits backend paths rather than the canonical coordinate contract | `modules/confornets_experimental.nf:12-180` | [LO]; [BP] wrap, do not rewrite, then canonicalize metadata |
| Protenix conversion handles several entity classes but may coerce/drop unsupported data | `modules/protenix.nf:277-370` | [LO]; [BP] repair and fail closed in Phase 5 |
| Protenix publication flattens selected files to basenames | `modules/protenix.nf:377-394` | [LO]; [BP] preserve native hierarchy and reject collisions |
| Current seed/sample defaults and command forwarding | `modules/protenix.nf:397-418,628-643`, file hash above | [LO], not proof of 5×5 or of upstream-recommended defaults |
| Existing complex parent workflow emits only structures | `workflows/complex_prediction.nf:62-80` | [LO]; [BP] canonical workflow must retain all required channels |
| FrustraMPNN process now accepts PDB or performs inline CIF/mmCIF→PDB conversion, but that local conversion does not emit the required durable source-identity map; current ingestion reduces rows to native summaries | `modules/frustrampnn.nf:1-82`, file hash above; `platform/api/services/result_ingester.py:39-94,145-188` | [LO]; [BP] the canonical workflow supplies Phase 2 normalized PDB plus `cm_structure_map_v1`, and Phase 7 validates/stores complete 20-slot landscapes without treating the inline conversion as the identity authority |
| Generic normalization does not provide the required identity map | `platform/api/services/structure_utils.py:172-239`; `scripts/normalize_target_pdb.py:40-140` | [LO]; [BP] one shared auditable boundary is built in Phase 2 |
| Result contract writes canonical `monomer_conformation`, while historical ingester rows use `conformer` | `platform/api/services/result_contracts.py:85-96`; `platform/api/services/result_ingester.py:2211-2218` | [LO]; exact compatibility policy is defined in Section 3.13 |
| Existing mutation generation can reconstruct a protein-only complex from ATOM rows | `platform/api/routers/jobs.py:2675-2740` | [LO]; [BP] forbidden for complete-complex resampling |

---

## 1. Product objective and scientific boundary

[BP] The first complete nonvisual product slice shall:

1. generate stochastic complete-complex structural hypotheses through the installed Protenix v2 lane;
2. retain backend-native artifacts and the complete seed/sample hierarchy;
3. support upstream-close ConforNets as a single-chain-protein alternative-state lane;
4. securely import authorized staged structures without accepting arbitrary server paths;
5. normalize authoritative CIF/mmCIF structures to traceable PDB analysis inputs;
6. preserve exactly 20 FrustraMPNN substitution slots per scoreable mapped residue, with explicit missingness;
7. compare conformers using executable hierarchical estimands without treating samples as trajectories, equilibrium populations, or thermodynamic states;
8. hand ranked candidates to the existing Mutagenesis Library through a versioned idempotent adapter; and
9. run separately gated matched WT/mutant Protenix resampling while preserving all unchanged complex entities and declared feature controls.

### 1.1 Supported lanes

| Lane | Required input | Contracted output | Explicit exclusions | Class |
|---|---|---|---|---|
| `protenix_v2_ensemble` | versioned complete-complex definition | backend-discriminated seed/sample ensemble and lossless native manifest | no affinity, population, free-energy, or physical-state claim | [UG][BP] |
| `confornets` | one single-chain protein and optional supported protein references | native ConforNets tree plus canonical coordinate manifest | no complexes, ligands, DNA/RNA, calibrated probability, or trajectory claim | [UG][BP] |
| `external_import` | approved upload or authorized registered-artifact ID | immutable staged files, receipt, and deterministic import-coordinate manifest | no raw paths, globs, or unverifiable provenance | [BP] |

### 1.2 FrustraMPNN semantic limit

[UG][BP] FrustraMPNN scores are single-chain backbone-context predictions. They are not ΔΔG, thermodynamic free energy, binding/interface energy, calibrated uncertainty, electrostatics, or functional effect. Partner chains and non-protein entities are not claimed to contribute to current scores. API metadata, result contracts, analysis output, handoff, and eventual UI copy must retain this limitation.

[UG] Threshold policy `frustrampnn_class_v1` is high frustration at `score <= -1.0`, neutral at `-1.0 < score < 0.58`, and minimally frustrated at `score >= 0.58`. [BP] The threshold policy and checkpoint hash are versioned provenance. Phase 0 must authenticate available checkpoints; this document does not declare a default checkpoint.

---

## 2. Non-negotiable invariants

1. **[BP] One workflow identity:** canonical requests route to `conformational_mapping`; backend is request data.
2. **[BP] One seed authority:** the ordered request-envelope list is authoritative; generated JSON and CLI must match it exactly or scheduling is rejected.
3. **[UG][BP] Complete-complex preservation:** unsupported entities or fields fail explicitly; no silent omission, count coercion, ID collapse, or bond loss.
4. **[BP] Metadata survives boundaries:** backend coordinates travel in tuples/records and are never reconstructed from flattened filenames.
5. **[BP] Lossless native retention:** every retained artifact has collision-safe relative path, size, role, and SHA-256.
6. **[BP] Original structure authority:** backend-native CIF/mmCIF is authoritative; normalized PDB is derived and has an identity map.
7. **[BP] Stable scientific identity:** joins use target, entity instance, label/auth identity, insertion code, sequence index, and candidate coordinates.
8. **[UG][BP] Exact-20 landscape:** each scoreable residue has one canonical slot per standard amino acid; malformed or missing rows are explicit.
9. **[BP] Hierarchical weighting:** samples nested in seeds are not flattened as independent observations.
10. **[BP] No silent partial success:** zero, duplicate, missing, shared, extra, or unreferenced mandatory artifacts fail finalization.
11. **[BP] Manifest authority:** a validated immutable manifest, not filenames, directory existence, Nextflow cache, or exit code alone, authorizes resume and downstream use.
12. **[BP] Fail closed:** unknown backend, contract, artifact class, feature mode, or alias receives no analyzer/viewer capabilities.
13. **[LO][BP] Legacy preservation:** `confornets_experimental` behavior and historical rows are preserved.
14. **[BP] No viewer coupling:** Phases 0–12 are complete and releasable without Mol*.
15. **[BP] Dirty-tree integrity:** rollback and staging are exact-path operations; unrelated dirty bytes are not touched.
16. **[BP] Evidence honesty:** focused tests and synthetic fixtures never count as authenticated live-job success.

---

## 3. Contract freeze

Phase 0 approves the definitions and vectors in this section. Phase 1 turns them into JSON Schema draft 2020-12 files and executable validators. Every JSON artifact has `schema_name`, integer `schema_version`, and strict unknown-field behavior where safety depends on it.

### 3.1 Request envelope: `cm_request_v1`

[BP] Required fields include `request_id`, backend, ordered targets, `ordered_seeds`, `samples_per_seed`, `feature_policy`, `runtime_policy`, `analysis_policy`, source, creator/principal, and canonical `request_sha256`.

```json
{
  "schema_name": "cm_request",
  "schema_version": 1,
  "request_id": "uuid",
  "backend": "protenix_v2_ensemble | confornets | external_import",
  "targets": [],
  "ordered_seeds": [101, 202, 303, 404, 505],
  "samples_per_seed": 5,
  "feature_policy": {},
  "runtime_policy": {},
  "analysis_policy": {},
  "source": {},
  "created_by": {},
  "request_sha256": "sha256-of-JCS-object-with-this-field-omitted"
}
```

Seed list is nonempty, ordered, unique signed 32-bit integers. Empty, duplicate, malformed, out-of-range, or conflicting API/generated-JSON/CLI seeds fail with HTTP 422 before scheduling. Order is preserved everywhere. Equal seeds are matched inference controls, not proof of bitwise determinism or paired physical states.

### 3.2 Complete-complex snapshot: `cm_complex_snapshot_v1`

[UG][BP] For each target preserve original bytes, normalized JSON, hashes, target ID/order, and ordered entities. Every entity has:

- `entity_type`, immutable `source_entity_id`, positive integer `count`, and `ordered_instance_ids`;
- `ordered_instance_ids` contains unique nonempty IDs and `len(ordered_instance_ids) == count`;
- sequence or representation, protein/DNA/RNA identity, ligand CCD and/or SMILES, ions, modifications, and covalent bonds;
- source-to-runtime mapping for every source entity and instance, including runtime entity/chain/token identity;
- runtime-to-output mapping for every candidate, including output entity/chain identity;
- 1-based references used by Protenix covalent bonds, token/atom admission counts, and unsupported-field report.

Repeated copies are never represented by one ID plus `count > 1`. The mapping relation is:

```text
(source_entity_id, ordered_instance_ids[i])
  -> (runtime_target_id, runtime_entity_id, runtime_instance_id, runtime_order)
  -> (candidate_id, output_label_asym_id, output_auth_asym_id, output_entity_order)
```

Composition validation compares entity type, order, count, each instance ID, sequence/representation, modifications, bonds, and mapping cardinality. Negative conversion, unsupported modifications/bonds, invalid count, ambiguous identity, or loss at any mapping stage fails closed. Repeated-copy fixtures include two identical protein copies, two same-CCD ligand copies, and a mixed complex where identical sequences have distinct source IDs.

### 3.3 Backend-discriminated candidate coordinates and IDs

[BP] `candidate_meta.backend_coordinates` is a discriminated union. Every stable ID includes a backend prefix, target identity, and a truncated digest of the canonical JSON coordinate object; the complete coordinate object remains persisted. IDs are globally unique within a request and collision detection compares both ID and full coordinates.

**Protenix coordinate**

```text
(target_id, ordered_seed, zero_based_sample_index)
candidate_id = cm_ptx_<target_slug>_<sha256(canonical-coordinate-json)[0:20]>
cardinality = T * S * N only when every target shares S ordered seeds and N samples
otherwise cardinality = sum_target(|ordered_seeds[target]| * samples_per_seed[target])
```

Exactly one authoritative CIF and all mandatory confidence/full-data sidecars bind each coordinate.

**ConforNets coordinate**

```text
(target_id, task, test_case_id, reference_id_or_none, run_index,
 saved_step, confornet_index, sample_index)
candidate_id = cm_cn_<target_slug>_<sha256(canonical-coordinate-json)[0:20]>
```

For each admitted target/task/test-case/reference group `g`, with `R_g` runs, exact saved-step set `K_g`, `C_g` ConforNet indices, and `N_g` samples:

```text
cardinality(g) = R_g * |K_g| * C_g * N_g
cardinality(total) = sum_g(cardinality(g))
```

Task, test case, reference (including explicit `none`), run, saved step, ConforNet index, and sample are mandatory coordinate fields even if a dimension has cardinality one. A missing expected coordinate or an unexpected coordinate fails finalization.

**External-import coordinate**

```text
(target_id, zero_based_staged_index, source_content_sha256, staged_receipt_sha256)
candidate_id = cm_imp_<target_slug>_<index:06d>_<source_content_sha256[0:16]>
cardinality = number of accepted unique receipt items
```

Staged order is the validated receipt order after deterministic request-order preservation; duplicate content in one receipt is rejected rather than silently deduplicated. Index, source hash, receipt hash, and destination relative path make identity reproducible and collision-safe.

### 3.4 Workflow tuple contracts

Canonical producer input:

```text
tuple val(target_meta), path(request_json), path(staged_assets)
```

Canonical producer output:

```text
tuple val(candidate_meta), path(authoritative_cif),
      path(confidence_json), path(full_data_json), path(native_tree)
```

`target_meta` includes request/target/backend identity, expected backend coordinates, settings digest, source snapshot hash, and resume key. `candidate_meta` contains the complete discriminated coordinate, candidate ID, settings digest, source snapshot hash, and manifest relationships. Optional artifacts use explicit nullable records with status/reason; metadata is never dropped.

### 3.5 Immutable resume key and authority

[BP] The resume descriptor is immutable canonical JSON containing:

- `request_sha256`;
- `source_snapshot_sha256` and, for complete complexes, `cm_complex_snapshot_v1` hash;
- backend name/version/commit;
- runtime identity, container digest, model/checkpoint identity and hashes;
- `feature_policy` and `feature_policy_sha256`, including per-entity hashes;
- ordered seeds and samples per seed (or complete backend coordinate plan);
- expected candidate cardinality;
- expected manifest schema/version, required artifact roles, and `expected_manifest_contract_sha256`;
- canonical settings/runtime-policy digest.

```text
resume_key = sha256(JCS(resume_descriptor))
```

Resume is allowed only when the request/resume key matches and the already-written `cm_native_artifacts_v1` plus `cm_ensemble_v1` validate completely against the expected descriptor. The validated manifest is authoritative. Nextflow cache entries, filenames, directory names, logs, and process exit codes are not authority. Any mismatch, stale source, changed runtime/checkpoint/container/features, missing or partial role, extra or unreferenced artifact, duplicate coordinate, hash/size mismatch, or cardinality discrepancy fails closed and requires a new request identity or explicit quarantine/rebuild; partial reuse is forbidden.

### 3.6 Native-artifact manifest: `cm_native_artifacts_v1`

Every file record contains collision-safe relative path, SHA-256, bytes, media type, semantic role, backend coordinates when applicable, settings/provenance digest, and links to input/preprocessing/MSA/template/log/runtime records. Protenix mandatory roles are runtime input, every authoritative CIF, ranking/confidence JSON, full-data JSON, preprocessing/MSA/template records as declared by feature policy, logs, runtime config, and composition audit. ConforNets mandatory roles are request/preprocess records, native state/loss files, every expected conformer, optional confidence/evaluation files with explicit computed/not-computed status, commands/logs, and runtime provenance. Import mandatory roles are immutable receipt and every staged structure.

### 3.7 Ensemble manifest: `cm_ensemble_v1`

Required fields include request/source-snapshot paths and hashes, backend/runtime/container/checkpoint identities, feature policy/hash, expected coordinate plan/cardinality, ordered candidate records, native-manifest path/hash, warnings/omissions, terminal status, timestamps, command/provenance, and resume key. Each candidate binds one authoritative structure and every mandatory sidecar by relative path/hash. Manifest validation rejects missing, duplicate, extra, shared, basename-colliding, or unreferenced artifacts.

### 3.8 Structure map: `cm_structure_map_v1`

Each selected atom/residue mapping row carries target/candidate/entity/instance identity, source model, mmCIF `label_asym_id`, `auth_asym_id`, `label_seq_id`, `auth_seq_id`, insertion code, residue name, sequence index, normalized PDB chain/residue/insertion code, selected N/CA/C/O source atom identity, altloc/model decision, and status/reason. Tests cover repeated chains, multicharacter IDs, insertion codes, alternate locations, multiple models, PDB limits, nonstandard residues, and missing backbone atoms. Original CIF hash is retained. Success/failure without a round-trip map is insufficient.

### 3.9 FrustraMPNN landscape: `cm_frustration_landscape_v1`

Primary key:

```text
(target_id, candidate_id, entity_instance_id, auth_asym_id,
 auth_seq_id, insertion_code, sequence_index, mutation_aa)
```

Canonical amino-acid order is `ACDEFGHIKLMNPQRSTVWY`. Every scoreable mapped residue has exactly 20 unique slots and exactly one native slot. Each slot stores WT, substitution, nullable finite score, class, scoreable, status, and reason. Allowed statuses include `ok`, `unscoreable_residue`, `missing_row`, `duplicate_row`, `malformed_row`, `nonfinite_score`, `mapping_failed`, and `conformer_missing`. Duplicate/malformed/unexpected amino acids fail the conformer landscape; missingness is neither zero-imputed nor discarded. Raw CSV and checkpoint/threshold provenance are retained by hash.

### 3.10 Executable analysis estimands: `cm_analysis_v1`

Let `F(g,c,r,a)` be the FrustraMPNN score for genotype or state `g`, candidate `c`, mapped residue `r`, and amino-acid slot `a`. Let `w(r)` be the validated WT residue. All arithmetic uses only finite `status=ok` rows.

The manifest maps every candidate `c` to an outer stratum `h(c)` and inner sample `n(c)` without dropping a backend coordinate:

- Protenix: `h=ordered_seed`, `n=sample_index`;
- ConforNets: `h=(task,test_case,reference,run,saved_step,confornet_index)`, `n=sample`;
- secure import: `h=staged_index`, `n=0` (a declared singleton stratum).

For a named comparison `q=(g_A,g_B)`, matched pair `p` is the exact common `(h,n)` coordinate, with candidates `c_A(q,p)` and `c_B(q,p)`. A pair must also match model/checkpoint/runtime/container policy, feature-policy mode, partner entities, source-complex lineage, and every declared invariant field. Row order is never used. A missing member or invariant mismatch creates an explicit unmatched record with reason and contributes to no paired estimate.

Per-candidate and per-pair quantities are:

```text
D_sub(g,c,r,a) = F(g,c,r,a) - F(g,c,r,w(r))
D_ctx(q,p,r,a) = F(g_B,c_B(q,p),r,a) - F(g_A,c_A(q,p),r,a)
D_self(q,p,r) = F(g_B,c_B(q,p),r,mut(r)) - F(g_A,c_A(q,p),r,w(r))
```

For each valid matched pair `(q,p)`, define `U_(q,p)` as residues that are not mutated, bijectively mapped in both structures, from the same entity instance with the same WT identity, and have finite native slots in both landscapes. Persist every included residue key and every excluded residue key/reason. Define `class_X(q,p,r)=class(F(g_X,c_X(q,p),r,w(r)))` using the versioned FrustraMPNN thresholds. Whole-chain redistribution is:

```text
R_signed(q,p) = (1 / |U_(q,p)|) * sum_{r in U_(q,p)} [F(g_B,c_B(q,p),r,w(r)) - F(g_A,c_A(q,p),r,w(r))]
R_abs(q,p) = (1 / |U_(q,p)|) * sum_{r in U_(q,p)} abs(F(g_B,c_B(q,p),r,w(r)) - F(g_A,c_A(q,p),r,w(r)))
R_transition(q,p) = (1 / |U_(q,p)|) * sum_{r in U_(q,p)} I[class_B(q,p,r) != class_A(q,p,r)]
```

`|U_(q,p)|=0` yields `insufficient_support`, never zero.

For any scalar `x` defined over expected manifest coordinates `(h,n)`, let `E_h` be the ordered expected inner coordinates for stratum `h`, `V_h` the coordinates with a finite valid value, `H_expected` the ordered expected strata, and `H_valid={h: |V_h|>0}`. Hierarchical aggregation is:

```text
mu_h = (1 / |V_h|) * sum_{n in V_h} x_(h,n)
var_within_h = sample_variance({x_(h,n): n in V_h}) when |V_h| >= 2, else null
mu = (1 / |H_valid|) * sum_{h in H_valid} mu_h
var_between = sample_variance({mu_h: h in H_valid}) when |H_valid| >= 2, else null
outer_support_fraction = |H_valid| / |H_expected|
coordinate_support_fraction = (sum_h |V_h|) / (sum_h |E_h|)
```

`hierarchical_mean(x)` means `mu` above; it never means a flat mean over all candidate rows. Each valid outer stratum has equal weight, and each valid inner sample within a stratum has equal weight. For any required boolean predicate `b` over the same expected coordinates, define `V_h^b` as coordinates with every input needed to evaluate `b` present and valid, `f_h=(1/|V_h^b|)*sum_{n in V_h^b} I[b_(h,n)]`, `H_valid^b={h: |V_h^b|>0}`, and `hierarchical_fraction(b)=(1/|H_valid^b|)*sum_{h in H_valid^b} f_h`. Its outer support is `|H_valid^b|/|H_expected|` and its coordinate support is `(sum_h |V_h^b|)/(sum_h |E_h|)`. A stratum with zero valid values does not enter a hierarchical mean or fraction but remains in both support denominators; it is never assigned zero or silently dropped. Persist `E_h`, each `V_h`/`V_h^b`, unmatched/missing coordinates and reasons, stratum means/fractions, within-stratum variance/range/median, the median/IQR of stratum means, between-stratum variance/range, and all support fractions. A zero expected denominator, zero valid strata, nonfinite required component, undefined required class, or support below the approved outer/inner minima yields `insufficient_support` with an explicit reason.

For genotype `g` and named comparison `q=(g_A,g_B)`, substitution `(r,a)` scores are:

```text
hotspot_score(g,r,a) = coordinate_support_fraction(D_sub) * hierarchical_mean(abs(D_sub(g,c,r,a)))
context_transition_rate(q,r,a) = hierarchical_mean(I[class(F(g_B,c_B(q,p),r,a)) != class(F(g_A,c_A(q,p),r,a))])
switch_score(q,r,a) = coordinate_support_fraction(D_ctx) * context_transition_rate(q,r,a) * abs(hierarchical_mean(D_ctx(q,p,r,a)))
```

The context and switch calculations use only exact matched pairs `p`; no score is emitted when a matched comparison is absent. Hotspot means score sensitivity, not beneficial mutation. Switch means class-changing context sensitivity, not a physical switch or thermodynamic transition.

Class-transition counts use the versioned thresholds. The approved analysis-policy record must also contain `sign_zero_epsilon`, `clash_detector_id`, `clash_detector_version`, outer/inner support minima, sign-consistency minimum, clash-free minimum, rank-stability minimum, and minimum common ranked-universe size. For every finite valid `D_sub` coordinate, `sign_epsilon(x)` is `-1` when `x < -sign_zero_epsilon`, `0` when `abs(x) <= sign_zero_epsilon`, and `+1` when `x > sign_zero_epsilon`. A ranked item's reference sign is `s_ref=sign_epsilon(hierarchical_mean(D_sub))`. If `s_ref=0`, `sign_consistency_fraction=0`; this is a defined failed-consistency value, not missing support. Otherwise `sign_consistency_fraction=hierarchical_fraction(sign_epsilon(D_sub)=s_ref and sign_epsilon(D_sub)!=0)` over finite valid `D_sub` coordinates. Thus each valid stratum first divides matching nonzero signs by all of its valid `D_sub` coordinates, zero-valued coordinates remain in that stratum's denominator and are inconsistent, and the final fraction is the equal-weight mean of those per-stratum fractions. Missing/nonfinite coordinates remain in the expected support denominators, and zero valid strata or support below either approved minimum yields `insufficient_support` rather than a fraction.

`clash_flag(g,c,r,a)` is a required boolean analysis field computed for substitution `(r,a)` on candidate structure `c` by the recorded `clash_detector_id` and `clash_detector_version`; the approved detector policy pins side-chain placement/repacking semantics, steric thresholds, and parameters, and its normalized row records detector-parameter hash and source-structure hash. `clash_free_fraction=hierarchical_fraction(clash_flag=false)` over coordinates for which both the required finite `D_sub` value and a provenance-valid boolean detector row exist. Thus each valid stratum first divides clash-free coordinates by all detector-valid coordinates and the final fraction is the equal-weight mean of those per-stratum fractions. A missing/nonboolean flag, detector/version/parameter mismatch, source-hash mismatch, or unsupported substitution invalidates that coordinate with a reason; it remains in expected support denominators and is never interpreted as clash-free. Zero valid strata or outer/inner support below the approved minima yields `insufficient_support`.

Rank stability is computed separately for a hotspot group `(backend_id,target_id,genotype_id)` and a switch group `(backend_id,target_id,comparison_id)`. The canonical ranked-item key is `k=(target_id,entity_instance_id,auth_asym_id,auth_seq_id,insertion_code,sequence_index,mutation_aa)`; conformation `candidate_id` is deliberately absent because `(h,n)` identifies the sampled conformation supplying a stratum value for the same substitution item. For hotspot group and stratum `h`, the descending scalar score is `rank_score_h^hot(k)=(|V_h^sub(k)|/|E_h|)*mean_{n in V_h^sub(k)} abs(D_sub(g,c(h,n),r,a))`. For switch group and stratum `h`, it is `rank_score_h^switch(k)=(|V_h^ctx(k)|/|E_h|)*mean_{n in V_h^ctx(k)} I[class_B!=class_A]*abs(mean_{n in V_h^ctx(k)} D_ctx(q,p(h,n),r,a))`. Here `V_h^sub(k)` and `V_h^ctx(k)` contain exactly the finite valid inner coordinates for that item and estimand, and an empty set emits no score.

For either group, `H_rank` is the ordered subset of `H_expected` whose stratum has finite scores meeting the approved inner-support minimum for at least the approved minimum common ranked-universe size; excluded strata remain in the outer-support denominator with reasons. `U_common` is the ordered intersection of ranked-item keys with a finite score meeting that inner-support minimum in every `h in H_rank`. Every stratum in `H_rank` ranks exactly `U_common` by its declared scalar score descending. Spearman uses average ranks for equal numeric scores; the final identity tuple is used only to serialize equal-score rows deterministically and never breaks a statistical tie. An item absent from `U_common` gets no synthetic rank, persists every missing-stratum reason, and has status `insufficient_support`. Group rank stability is the median pairwise Spearman correlation across these same-universe rankings and is persisted on every included item. Fewer than the approved outer minimum or three valid rankings (whichever is greater), `|U_common|` below the approved minimum (never below three), a constant-rank vector, an incomplete pairwise-correlation set, or any undefined correlation yields `insufficient_support` for the group.

For an approved Protenix 5×5 default, `robust` requires at least four of five valid seed strata, at least three valid samples per valid seed, `sign_consistency_fraction >= 0.80`, `clash_free_fraction >= 0.90`, and rank stability `>=0.60`. A non-5×5 or non-Protenix layout must use separately Phase-0-approved, versioned thresholds; 4/5 is never applied silently. Status classification is total and ordered: `insufficient_support` applies first when a required component is absent/undefined or support/ranked-universe minima fail; otherwise `robust` applies only when every sign, clash, and stability threshold passes; every remaining candidate is `conditional`, with each failed threshold recorded. All thresholds and the formula version are configurable and versioned.

Missing values are never imputed. Ranking excludes `insufficient_support`. Deterministic order is: status (`robust`, then `conditional`), coordinate support fraction descending, outer support fraction descending, hotspot score descending, switch score descending, absolute hierarchical `D_sub` mean descending, entity-instance ID ascending, sequence index ascending, insertion code ascending, mutation in canonical amino-acid order. Persist every sort key, raw component, threshold-policy hash, sign reference/counts, clash detector fields/counts, `U_common`, per-stratum scores/ranks, pairwise correlations, formula version, source-row key, expected/valid coordinate count, status branch, and exclusion/failure reason so ranking is exactly reconstructable.

All differences are labeled “FrustraMPNN score difference.” ΔΔG and thermodynamic language are forbidden.

### 3.11 Changed-sequence feature policy

[UG] Protenix guidance favors appropriate MSA/template preprocessing. [BP] A mutant never reuses changed-protein MSA feature bytes as if sequence were unchanged. The request chooses exactly one validated mode:

1. `regenerate_mutated_protein_v1` (default candidate, subject to Phase 0 approval): retain WT changed-entity features; regenerate the mutant protein entity MSA with pinned search tool/version, database snapshot hashes, query settings, pairing settings, and cache policy. If templates were disabled for WT, they remain disabled. If enabled, rerun template search/featurization for the mutant under the same pinned database/tool/settings. Unaffected protein, DNA, RNA, ligand, ion, modification, bond, and entity features are byte-identical.
2. `paired_regenerate_changed_protein_v1`: regenerate the changed protein entity features independently for both WT and mutant in the same approved runtime with identical pinned tools/databases/settings; all unaffected entity features remain byte-identical. This mode is required when a stale WT feature source cannot be authenticated.
3. `features_disabled_control_v1`: protein MSA, templates, and RNA MSA are disabled for both WT and mutant. It is an explicit controlled ablation, never described as upstream-recommended accuracy mode.

RNA-MSA settings and bytes cannot change unless RNA itself is a declared mutation target in a future separately specified schema; current point-mutation handoff is protein-only. For every entity, persist source-feature hash, WT hash, mutant hash, tool/database/settings hash, and `declared_difference` from `{byte_identical_unaffected, regenerated_changed_sequence, disabled_both}`. Validation fails on undeclared differences, changed unaffected bytes, a changed sequence with reused changed-entity MSA feature bytes, template-mode drift, or tool/database/settings drift.

### 3.12 Mutagenesis handoff and resampling lineage

`cm_mutagenesis_handoff_v1` includes source ensemble/analysis/complex hashes, target/entity-instance and mapped author identity, validated WT, sequence index/insertion code, substitution and mutation-set ID/string, evidence-row keys, support/missingness, ranking components, warnings, feature/resampling policy, adapter version, and idempotency key:

```text
sha256(source_design_or_complex_digest + canonical_candidate_set + canonical_resampling_settings)
```

The Phase 9 adapter translates mapped author identity to the existing consumer contract, validates WT and source freshness, and transactionally registers a **prepared handoff identity only**. Same-key retry returns the same prepared job/variant identities without duplication; a changed key creates a distinct prepared identity; scheduler launch count remains zero. Phase 10 consumes that prepared identity for a separately authorized transactional launch; same-key retry returns the same launch identity, and no partial launch survives failure.

Matched resampling materializes WT and mutant from the approved complete-complex snapshot, never protein-only ATOM reconstruction. Exactly declared substitutions and declared changed-entity features may differ; all other entities, copies, bonds, order, and controls remain identical.

### 3.13 Result contracts and exact legacy alias behavior

Canonical new result contract IDs are `conformational_mapping_protenix_v1`, `conformational_mapping_confornets_v1`, `conformational_mapping_import_v1`, `conformational_mapping_analysis_v1`, and `conformational_mapping_resampling_v1`.

[BP] Canonical new writes use artifact class **`monomer_conformation`**. Read/resolution compatibility accepts exactly `monomer_conformation` and historical `conformer`, normalizing either in memory to `monomer_conformation`. Existing historical `conformer` rows are not bulk-rewritten. Reingestion does not mutate their stored spelling merely to canonicalize it. Unknown values—including near-miss aliases—fail closed with no analyzer or viewer capability. Tests cover old-row resolution, new canonical writes, idempotent old-row reingestion, and unknown-value rejection. `confornets_experimental` routing, artifacts, and visible behavior remain unchanged.

Persistence/API acceptance is independent of Mol*. Large landscapes require pagination/range reads; summary endpoints may not load a whole matrix.

---

## 4. Architecture and ownership

```text
API authorization/validation/staging
  -> cm_request_v1 + cm_complex_snapshot_v1
  -> workflows/conformational_mapping.nf
       -> static backend dispatch
          -> wrapped existing ConforNets process trio
          -> repaired/reused existing Protenix process graph
          -> secure staged import
       -> backend-discriminated finalizers
          -> cm_native_artifacts_v1 + cm_ensemble_v1
       -> shared structure normalizer
          -> normalized PDB + cm_structure_map_v1
       -> wrapped existing FrustraMPNN process
          -> raw CSV + cm_frustration_landscape_v1
       -> comparison/ranking -> cm_analysis_v1
       -> handoff only -> cm_mutagenesis_handoff_v1
       -> separately gated WT/mutant resampling
  -> backend-aware persistence/result contracts/nonvisual API
  -> authenticated current-run release matrix
  -> separately approved Phase 13 Mol* consumer
```

[BP] API owns authorization, staging, request validation, idempotency, and launch records. Nextflow owns static composition and artifact movement. Python finalizers own schema validation, mapping, hashes, cardinality, and analysis. Backend modules retain native semantics. Result ingestion owns transactional persistence and fail-closed contract resolution. Mol* consumes approved APIs only.

---

## 5. Global authorization, evidence, and dirty-tree-safe gate protocol

### 5.1 Separate approval and review roles

Before a phase starts, the operator grants start authorization outside the repository and the phase-start evidence index records its immutable message/reference, UTC, approver identity, exact phase, and allowlist hash. Once authorized, the phase may create or modify its exact `phase_<N>_spec_check.json` and mirror that start authorization under `operator_approval`; creating or editing the review JSON can never authorize the phase that creates it. Completion `GO` is a later decision. Authorization for Phase `N+1` may be granted only after Phase `N` has reached `GO`, and is never a condition for Phase `N` itself to reach `GO`. No implementer may self-approve. Named independent roles are:

- **workflow/runtime reviewer:** process graph, runtime identity, artifact/cardinality evidence;
- **scientific/data-contract reviewer:** schemas, identity, estimands, missingness, semantic limits;
- **security reviewer:** staging, authorization, path safety, provenance, secret handling;
- **API/persistence reviewer:** transactions, aliases, idempotency, migration and API behavior;
- **frontend/Mol* reviewer:** Phase 13 consumer semantics only;
- **release operator:** authenticates live current-run evidence and makes the operational gate decision.

### 5.2 Phase-start capture outside the repository

Before any RED test, probe, or edit, create an external evidence root:

```text
/home/dalab/biomodstack-phase-evidence/conformational_mapping/phase_<N>/<UTC>/
```

Store: UTC, repo root, branch, HEAD, raw `git status --porcelain=v1 -z`, its SHA-256/count, exact allowlist, hashed phase-start byte copies for every existing allowlisted path, `absent_paths.json` for allowlisted paths absent at start, symlink metadata, hashes of every unrelated dirty regular file, baseline commands/output/exit codes, and process/job inventory. Hash the evidence index. Re-pin all allowlisted hashes immediately before each write; unexpected target drift is `STOP`.

### 5.3 Prohibited and allowed Git behavior

Never use broad `git add`, `git reset`, `git checkout`, `git restore`, `git clean`, or broad `git revert`. Any repository-wide variant or command that discards or rewrites paths outside the phase allowlist is likewise forbidden. Never stage or commit merely because a phase passes. Exact-path staging and commit are allowed only after a **separate explicit authorization** naming every path; use `git add -- <exact paths>` and verify the index contains no other paths.

Because planned/review files may be untracked, `git diff` is not sufficient. Generate scoped diffs from authenticated phase-start copies with `diff -u`/`git diff --no-index`, validate reverse application and whitespace, and record hashes.

### 5.4 Verification and baseline rule

Phase 0 records current pass/fail for these immutable baseline command strings; every Phase 1–13 repeats the same strings and attributes pre-existing failures versus regressions:

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_confornets_experimental.py \
  platform/api/tests/test_experimental_nextflow_entrypoint.py
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_confornets_result_ingester.py \
  platform/api/tests/test_result_contracts.py \
  platform/api/tests/test_nextflow_entrypoint_registry.py
PYTHONPATH=platform/api python -m pytest -q platform/api/tests
pnpm --dir platform/frontend test
```

Changing a baseline string requires a Phase 0 amendment and renewed reviews. Environment-blocked commands remain explicit; they are not silently dropped. A focused pass does not make the broad tree green.

### 5.5 STOP rollback mechanics

On `STOP`:

1. stop only processes/jobs launched by that phase and identified in its start/end process ledger;
2. restore only existing allowlisted files from authenticated phase-start byte copies or an independently verified inverse scoped patch;
3. delete only allowlisted paths proven absent in `absent_paths.json` and created by the phase;
4. do not touch unrelated paths, caches, jobs, services, or user work;
5. rehash all unrelated dirty files byte-for-byte and record concurrent drift separately;
6. keep `STOP` if restoration, process ownership, or unrelated-byte preservation cannot be proved.

Every phase below instantiates its exact allowlist, process scope, rollback targets, review roles, and review JSON path.

---

## 6. Explicit phase gates

### Phase 0 — Runtime truth, baseline, and contract-vector approval (no production changes)

**Separate authorization:** Required before creating probes, vectors, review records, running tests, inspecting containers, or launching any runtime. This correction pass provides none.

**Allowlist — create/modify only:**

- create `docs/specs/conformational_mapping/cm_contract_definitions_v1.md`;
- create `docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json`;
- create `scripts/probes/conformational_mapping/probe_phase0_runtime.py`;
- create `scripts/probes/conformational_mapping/validate_phase0_vectors.py`;
- create under `platform/api/tests/fixtures/conformational_mapping/phase_0_vectors/` only;
- create/modify `docs/reviews/conformational_mapping/phase_0_spec_check.json`.

**Forbidden/out of scope:** All production source/config/workflow/frontend files, executable schemas, API routing, services/restarts, persistent database changes, Mol*, and any claim that a probe succeeded before authenticated evidence exists.

**Probe tests and named vector families (not Phase 1 schema RED):**

- `P0-VECTOR-COMPLEX-001..012`: protein, DNA, RNA, CCD ligand, SMILES ligand, ion, modification, covalent bond, repeated protein copies, repeated ligand copies, mixed identical-sequence distinct entities, and full mixed complex;
- `P0-VECTOR-COMPLEX-NEG-001..010`: invalid count/ID cardinality, duplicate instance ID, unsupported entity/field/modification/bond, dangling bond reference, lossy conversion, token-limit rejection, malformed sequence, and ambiguous entity ordering;
- `P0-PROTENIX-LAYOUT-001`: exact target/seed/sample directory and mandatory sidecar tree for a small protein at proposed 5×5;
- `P0-PROTENIX-COMPOSITION-001..009`: authenticated output composition for every positive entity/control family above, including source→runtime→output mappings;
- `P0-CONFORNETS-LAYOUT-001..003`: diversity, MSE/reference, and transfer-like admitted mode with exact task/test-case/reference/run/saved-step/ConforNet/sample coordinate trees;
- `P0-CONFORNETS-NEG-001..004`: multi-chain, non-protein, too-many-reference, and missing-coordinate rejection;
- `P0-DEFAULTS-001..007`: 5 seeds, 5 samples, `use_default_params`, explicit cycle/step overrides, protein MSA, templates, and RNA MSA;
- `P0-FRUSTRAMPNN-001..004`: checkpoint identities, selected-chain behavior, exact 20 rows, and negative malformed/nonfinite rows;
- `P0-NORMALIZE-001`: insertion code, multicharacter asym ID, altloc, repeated copies, and multiple models;
- `P0-USALIGN-001`: executable version/hash/help and one deterministic parser fixture;
- `P0-BASELINE-001`: all four baseline command strings with attributed results.

**Probe scope:** After approval, inspect local image/container/runtime/checkpoint identities and CLI help. Run the smallest approved inputs needed to measure exact Protenix artifact dimensions/layout/cardinality and composition for proteins, DNA, RNA, ligands, ions, modifications, covalent bonds, and repeated copies. Include explicit negative unsupported-data cases. Measure exact ConforNets dimensions and native layout for every proposed task shape. Probe proposed 5×5, cycles/steps versus `use_default_params`, protein MSA, templates, and RNA-MSA settings before freezing any default. Record wall time and peak CPU/RAM/GPU/storage. Do not alter production code to make a probe pass.

**Default decision ledger:** Each proposed default must contain `value`, `classification`, `source/evidence`, and `approval`. Initial hypotheses—not frozen defaults—are:

| Candidate | Initial classification | Required Phase 0 decision |
|---|---|---|
| five explicit seeds | [BP] proposed exploration/reproducibility budget; [UG] supports explicit seed control, not the number five | authenticate local fan-out and approve/reject; do not attribute five to upstream |
| five samples per seed | [LO] current module fallback is five samples; [BP] proposed per-seed exploration budget | measure layout/resource/cardinality and approve/reject |
| `use_default_params=true` with no manual cycle/step override | [UG] candidate upstream-close mode | prove installed CLI/config behavior |
| explicit `n_cycle=10`, `n_step=200` | [LO] current BMS values, not upstream proof | test only with default params disabled; approve or reject |
| protein MSA enabled | [UG] candidate accuracy guidance | authenticate tool/DB/runtime and recorded hashes |
| templates disabled | [BP] candidate bias/control policy | compare supported mode and approve/reject |
| RNA MSA disabled | [BP] candidate until installed behavior is known | probe RNA input behavior and approve/reject |

Mutually inconsistent parameter modes may not be combined: manual cycle/step overrides require `use_default_params=false`; default-parameter mode omits overrides.

**Exact verification commands/evidence:**

```bash
python scripts/probes/conformational_mapping/validate_phase0_vectors.py \
  --definitions docs/specs/conformational_mapping/cm_contract_definitions_v1.md \
  --vectors docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json
python scripts/probes/conformational_mapping/probe_phase0_runtime.py \
  --vectors docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json \
  --output /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>
python scripts/probes/conformational_mapping/validate_phase0_vectors.py verify-evidence \
  --vectors docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json \
  --evidence-root /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id> \
  --hash-ledger /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>/runtime_evidence_hashes.json
sha256sum \
  /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>/runtime_evidence.json \
  /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>/runtime_evidence_hashes.json
# Then run all four exact baseline command strings from Section 5.4.
```

Evidence must exist at `/mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>/runtime_evidence.json` plus per-vector subdirectories containing commands, input/output hashes, exact artifact trees/counts, runtime identities, resources, and exit status.

**Acceptance criteria:** Definitions and vectors are approved by all reviewers; every required positive/negative probe has authenticated evidence or the supported scope/default is explicitly narrowed; exact Protenix and ConforNets formulas match observed trees; repeated-copy mappings are complete; every default is tagged `[UG]`, `[LO]`, or `[BP]`; baseline results are attributed. No executable schema-test pass is required in Phase 0.

**Dirty-tree-safe rollback:** Stop only `probe_phase0_runtime.py` processes and jobs whose approved run ID appears in the Phase 0 ledger. Restore the four existing allowlisted paths from phase-start copies if any existed; delete only allowlisted files/directories listed absent at start; leave `/mnt/.../<approved_run_id>` quarantined as evidence unless the operator separately approves deletion. Rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_0_spec_check.json`; required independent roles: workflow/runtime, scientific/data-contract, security, and release operator.

**GO:** Recorded operator approval exists; all acceptance items and reviewer decisions are `GO`; no unresolved snapshot drift; Phase 1 definitions/vector hash is frozen.

**STOP:** Missing runtime, unverifiable layout/cardinality/composition, ambiguous contract/default, unsupported positive case without scope correction, un-attributed baseline, reviewer rejection, dirty-tree drift, or any attempt to treat docs as live proof.

### Phase 1 — Executable schemas and characterization

**Separate authorization:** Required after Phase 0 `GO` and exact definition/vector hashes are recorded.

**Allowlist — create/modify only:**

- create `schemas/conformational_mapping/cm_request_v1.schema.json`, `cm_complex_snapshot_v1.schema.json`, `cm_native_artifacts_v1.schema.json`, `cm_ensemble_v1.schema.json`, `cm_structure_map_v1.schema.json`, `cm_frustration_landscape_v1.schema.json`, `cm_analysis_v1.schema.json`, and `cm_mutagenesis_handoff_v1.schema.json`;
- create `platform/api/services/conformational_mapping/__init__.py` and `platform/api/services/conformational_mapping/contracts.py`;
- create `platform/api/tests/test_conformational_mapping_schemas.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/schemas/`;
- modify only dependency declarations proven necessary in `platform/api/pyproject.toml` and `platform/api/uv.lock`;
- create/modify `docs/reviews/conformational_mapping/phase_1_spec_check.json`.

**Forbidden/out of scope:** Workflows/modules, registry/routing, runtime adapters, mutation launch, persistence/database, frontend/Mol*, and changes to Phase 0 evidence.

**RED tests and IDs:** In `platform/api/tests/test_conformational_mapping_schemas.py`: `test_cm001_rejects_seed_conflicts`, `test_cm002_instance_ids_equal_count`, `test_cm003_repeated_copy_mapping_roundtrip`, `test_cm004_backend_coordinates_are_discriminated`, `test_cm005_candidate_ids_do_not_collide`, `test_cm006_resume_descriptor_is_complete`, `test_cm007_manifest_rejects_missing_extra_partial`, `test_cm008_exact_twenty_slots`, `test_cm009_analysis_formula_vectors`, `test_cm010_feature_modes_and_hash_differences`, `test_cm011_handoff_idempotency_vector`, and `test_cm012_unknown_fields_fail_closed`. Fixture families are the approved Phase 0 vectors plus `schemas/positive/` and `schemas/negative/`.

**Implementation scope:** Implement strict JSON schemas, canonical JSON hashing, typed validators, ID/cardinality helpers, and vector-to-schema conformance. Characterize legacy aliases without changing production readers/writers.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_schemas.py
python -m json.tool docs/reviews/conformational_mapping/phase_1_spec_check.json >/dev/null
# Run all four Section 5.4 baselines unchanged.
```

Persist JUnit/output logs in the Phase 1 external evidence root.

**Acceptance criteria:** All approved vectors validate or reject exactly as specified; eight schemas have stable IDs/versions; canonical hashes/IDs/formulas match hand-calculated vectors; legacy characterization is green; all Phase 1 RED tests are green; no production routing exists yet.

**Dirty-tree-safe rollback:** Stop only Phase 1 pytest processes. Restore `platform/api/pyproject.toml`/`uv.lock` only from authenticated start copies if touched; remove only newly absent-at-start schema/package/test/fixture/review paths; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_1_spec_check.json`; required roles: scientific/data-contract, security, and API/persistence reviewers.

**GO:** Phase 0 frozen hashes match, all Phase 1 tests pass, baselines show no target regression, reviewers approve, and the operator records Phase 1 completion `GO`. Phase 2 authorization, if granted, is a later separate decision and is not a condition of this `GO`.

**STOP:** Schema ambiguity, vector drift, alias behavior changed, required dependency not approved, inherited RED failure, or dirty-tree restoration uncertainty.

### Phase 2 — Shared normalization boundary

**Separate authorization:** Required after Phase 1 `GO`.

**Allowlist — create/modify only:**

- create `platform/api/services/conformational_mapping/structure_normalizer.py`;
- create `scripts/normalize_conformational_mapping_structure.py`;
- modify `platform/api/services/structure_utils.py` only for shared parser hooks;
- create `platform/api/tests/test_conformational_mapping_normalization.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/normalization/`;
- create/modify `docs/reviews/conformational_mapping/phase_2_spec_check.json`.

**Forbidden/out of scope:** Backend execution, workflow/module changes, FrustraMPNN scoring, registries, DB/API ingestion, frontend/Mol*, or changes to original fixture CIFs.

**RED tests and IDs:** `test_cm2_001_original_cif_is_authority`, `test_cm2_002_roundtrip_atom_residue_map`, `test_cm2_003_repeated_chain_instances`, `test_cm2_004_multichar_asym_and_insertion`, `test_cm2_005_altloc_policy`, `test_cm2_006_multiple_model_policy`, `test_cm2_007_numbering_overflow_fails`, `test_cm2_008_missing_backbone_is_explicit`, `test_cm2_009_nonstandard_residue_status`, and `test_cm2_010_deterministic_mapping`. Fixtures: `normalization/{repeated_copies,multichar_insertion,altloc,multimodel,overflow,missing_backbone,nonstandard}/`.

**Implementation scope:** Build one deterministic CIF/mmCIF→PDB adapter and `cm_structure_map_v1` sidecar using existing parsers where correct. Preserve original bytes/hash; never duplicate FrustraMPNN modules.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_normalization.py \
  platform/api/tests/test_conformational_mapping_schemas.py
python scripts/normalize_conformational_mapping_structure.py \
  --input platform/api/tests/fixtures/conformational_mapping/normalization/multichar_insertion/input.cif \
  --output /tmp/cm_phase2/output.pdb --map /tmp/cm_phase2/cm_structure_map_v1.json
sha256sum /tmp/cm_phase2/output.pdb /tmp/cm_phase2/cm_structure_map_v1.json
# Run all Section 5.4 baselines.
```

**Acceptance criteria:** Mapping is deterministic and round-trip auditable; repeated copies remain distinct; every selected N/CA/C/O atom joins to source; omissions are explicit; original hash is retained; numbering/identity ambiguity fails closed.

**Dirty-tree-safe rollback:** Stop only Phase 2 pytest/normalizer processes. Restore only `structure_utils.py` and any pre-existing allowlisted paths from start copies; delete only absent-at-start Phase 2 paths; delete `/tmp/cm_phase2` only if created by this phase; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_2_spec_check.json`; roles: scientific/data-contract and API/persistence reviewers.

**GO:** All normalization/schema tests and objective mapping checks pass, baselines have no target regression, reviews approve.

**STOP:** Any scored identity cannot map back, original bytes are altered, repeated copies collapse, ambiguity is tolerated, or unrelated bytes drift without attribution.

### Phase 3 — Canonical registry, API validation, and routing spine

**Separate authorization:** Required after Phase 2 `GO`.

**Allowlist — create/modify only:**

- create `platform/api/config/models/conformational_mapping.yaml`;
- create `platform/api/config/templates/conformational_mapping.yaml`;
- create `platform/api/services/conformational_mapping/request_builder.py`;
- modify `platform/api/routers/jobs.py` and `platform/api/services/nextflow.py` only at canonical validation/routing seams;
- create `workflows/conformational_mapping.nf` with static includes;
- create `platform/api/tests/test_conformational_mapping_routing.py`;
- modify `platform/api/tests/test_nextflow_entrypoint_registry.py` only for canonical/legacy assertions;
- create/modify `docs/reviews/conformational_mapping/phase_3_spec_check.json`.

**Forbidden/out of scope:** Backend adapter internals, `modules/protenix.nf`, ConforNets process internals, import copying, FrustraMPNN, DB ingestion, frontend/Mol*, and legacy ID renames.

**RED tests and IDs:** `test_cm3_001_model_and_template_discoverable`, `test_cm3_002_backend_controls_are_conditional`, `test_cm3_003_matrix_routes_canonical_entrypoint`, `test_cm3_004_cm_namespace_normalization`, `test_cm3_005_seed_conflict_rejected_before_schedule`, `test_cm3_006_unsupported_complex_rejected`, `test_cm3_007_unknown_backend_fails`, and `test_cm3_008_confornets_experimental_unchanged` in the routing test file.

**Implementation scope:** Add canonical registries, request builder, validation, `WORKFLOW_ENTRYPOINTS` mapping, and a static dispatch skeleton that validates tuples but does not claim backend completion. Every visible control maps to one validated request field.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_routing.py \
  platform/api/tests/test_nextflow_entrypoint_registry.py \
  platform/api/tests/test_confornets_experimental.py \
  platform/api/tests/test_experimental_nextflow_entrypoint.py
nextflow config workflows/conformational_mapping.nf -flat > /tmp/cm_phase3.nextflow.config
# Static config parse only; do not run the workflow. Then run Section 5.4 baselines.
```

**Acceptance criteria:** Every admitted backend routes to the canonical static entrypoint; invalid controls fail before scheduling; no filename/profile fallback; legacy ConforNets route/config behavior is byte/behavior compatible; no backend is falsely marked complete.

**Dirty-tree-safe rollback:** Stop only Phase 3 pytest/`nextflow config` processes. Restore `jobs.py`, `nextflow.py`, and the existing registry test from start copies; delete only absent-at-start canonical YAML/service/workflow/test/review paths; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_3_spec_check.json`; roles: workflow/runtime, security, and API/persistence reviewers.

**GO:** Routing/static parse and prior tests pass; no legacy regression; reviewers and operator approve.

**STOP:** Dynamic includes, silent fallback, unvalidated UI field, legacy behavior change, workflow execution required to prove static routing, or scope drift.

### Phase 4 — ConforNets adapter

**Separate authorization:** Required after Phase 3 `GO`.

**Allowlist — create/modify only:**

- create `modules/conformational_mapping_confornets.nf`;
- create `scripts/finalize_confornets_conformational_mapping.py`;
- modify `workflows/conformational_mapping.nf` only for the ConforNets branch;
- create `platform/api/tests/test_conformational_mapping_confornets.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/confornets/`;
- create/modify `docs/reviews/conformational_mapping/phase_4_spec_check.json`.

**Forbidden/out of scope:** Changes to `modules/confornets_experimental.nf`, `workflows/confornets_experimental.nf`, Protenix/import/FrustraMPNN, DB/frontend/Mol*, or support for complexes/non-proteins.

**RED tests and IDs:** `test_cm4_001_single_chain_only`, `test_cm4_002_at_most_two_references`, `test_cm4_003_task_dispatch`, `test_cm4_004_full_coordinate_identity`, `test_cm4_005_dimension_formula`, `test_cm4_006_missing_or_extra_coordinate_fails`, `test_cm4_007_placeholder_bfactor_not_confidence`, `test_cm4_008_native_manifest_complete`, and `test_cm4_009_legacy_artifacts_semantically_preserved`.

**Implementation scope:** Wrap `PrepConforNetsRequest -> RunConforNets -> FinalizeConforNetsOutputs` without modifying those internals. Carry complete coordinates, finalize native and ensemble manifests, and label confidence/evaluation as computed or not computed.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_confornets.py \
  platform/api/tests/test_conformational_mapping_schemas.py \
  platform/api/tests/test_confornets_experimental.py
python scripts/finalize_confornets_conformational_mapping.py \
  --request platform/api/tests/fixtures/conformational_mapping/confornets/complete/request.json \
  --native-root platform/api/tests/fixtures/conformational_mapping/confornets/complete/native \
  --out /tmp/cm_phase4
python -m json.tool /tmp/cm_phase4/cm_ensemble_v1.json >/dev/null
# Run Section 5.4 baselines. Synthetic finalizer evidence is not live inference proof.
```

**Acceptance criteria:** Full ConforNets coordinate cardinality matches the formula; IDs do not collide; missing/extra artifacts fail; native outputs are not flattened; B-factor 50 is not treated as pLDDT; legacy files/route remain unchanged.

**Dirty-tree-safe rollback:** Stop only Phase 4 pytest/finalizer processes; no live ConforNets job is authorized by this phase unless separately added to approval. Restore only the canonical workflow if pre-existing and any pre-existing allowlisted paths; delete absent-at-start adapter/finalizer/test/fixture/review paths and `/tmp/cm_phase4`; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_4_spec_check.json`; roles: workflow/runtime and scientific/data-contract reviewers.

**GO:** Synthetic contract tests pass, legacy baseline is unchanged, and reviewers approve the adapter as ready for later authenticated E2E.

**STOP:** Partial run can look complete, coordinate dimensions are inferred from filenames, backend internals are rewritten, scope expands, or synthetic evidence is called live success.

### Phase 5 — Protenix complete-complex adapter

**Separate authorization:** Required after Phase 4 `GO` and Phase 0 has frozen approved Protenix defaults.

**Allowlist — create/modify only:**

- modify `modules/protenix.nf` only for lossless conversion/output-channel corrections;
- create `modules/conformational_mapping_protenix.nf`;
- create `scripts/finalize_protenix_conformational_mapping.py`;
- modify `workflows/conformational_mapping.nf` only for the Protenix branch;
- create `platform/api/tests/test_conformational_mapping_protenix.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/protenix/`;
- create/modify `docs/reviews/conformational_mapping/phase_5_spec_check.json`.

**Forbidden/out of scope:** Installing/forking another Protenix, mutation handoff/resampling, imports, FrustraMPNN, DB/frontend/Mol*, or changing unrelated Protenix workflows without a separately amended allowlist.

**RED tests and IDs:** `test_cm5_001_instance_ids_equal_count`, `test_cm5_002_repeated_copy_mapping`, `test_cm5_003_all_entity_types_and_bonds`, `test_cm5_004_unsupported_data_fails`, `test_cm5_005_default_mode_is_phase0_frozen`, `test_cm5_006_multi_target_seed_sample_formula`, `test_cm5_007_candidate_ids_include_target`, `test_cm5_008_basename_collision_fails`, `test_cm5_009_missing_extra_partial_sidecars_fail`, `test_cm5_010_composition_audit`, `test_cm5_011_resume_key_and_manifest_authority`, and `test_cm5_012_parent_retains_all_channels`.

**Implementation scope:** Repair/reuse `PrepProtenixComplex` and `ProtenixFromComplex`; preserve ordered instance IDs and source→runtime→output mappings; preserve native hierarchy; carry confidence/full-data/native channels; implement manifest/resume validation. Use only Phase 0-approved default mode. If `use_default_params=true`, omit cycle/step overrides; if manual cycles/steps are approved, set default params false explicitly.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_protenix.py \
  platform/api/tests/test_conformational_mapping_schemas.py \
  platform/api/tests/test_conformational_mapping_routing.py
python scripts/finalize_protenix_conformational_mapping.py \
  --request platform/api/tests/fixtures/conformational_mapping/protenix/complete/request.json \
  --snapshot platform/api/tests/fixtures/conformational_mapping/protenix/complete/cm_complex_snapshot_v1.json \
  --native-root platform/api/tests/fixtures/conformational_mapping/protenix/complete/native \
  --out /tmp/cm_phase5
python -m json.tool /tmp/cm_phase5/cm_ensemble_v1.json >/dev/null
# Run Section 5.4 baselines. No GPU workflow is implied.
```

**Acceptance criteria:** Every supported entity/copy/bond survives with exact ordered mapping; negative unsupported fields fail; for each target exact coordinate/cardinality and sidecars validate; resume rejects stale/extra/partial content; native hierarchy is lossless; current source hash/anchor changes are documented.

**Dirty-tree-safe rollback:** Stop only Phase 5 pytest/finalizer processes; no GPU job unless separately authorized. Restore `modules/protenix.nf` and canonical workflow from authenticated start copies; delete only absent-at-start adapter/finalizer/test/fixtures/review paths and `/tmp/cm_phase5`; rehash all unrelated dirty files, especially other concurrent `modules/protenix.nf` dependencies.

**Review:** `docs/reviews/conformational_mapping/phase_5_spec_check.json`; roles: workflow/runtime, scientific/data-contract, and security reviewers.

**GO:** All complete-complex, cardinality, resume, and baseline gates pass; reviewers approve; the operator records Phase 5 completion `GO`. Phase 6 authorization, if granted, is a later separate decision and is not a condition of this `GO`.

**STOP:** Any entity/copy/bond loss, one-ID-with-count defect, sidecar flattening, unapproved default, partial resume, source drift, or need to alter a forbidden surface.

### Phase 6 — Secure external imports

**Separate authorization:** Required after Phase 5 `GO`.

**Allowlist — create/modify only:**

- create `platform/api/services/conformational_mapping/import_stager.py`;
- create `modules/conformational_mapping_import.nf`;
- modify `platform/api/routers/jobs.py` only for authorized import request handling;
- modify `workflows/conformational_mapping.nf` only for staged-import dispatch;
- create `platform/api/tests/test_conformational_mapping_import_security.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/imports/`;
- create/modify `docs/reviews/conformational_mapping/phase_6_spec_check.json`.

**Forbidden/out of scope:** Arbitrary server paths/globs, frontend upload UI, backend prediction, mutation, persistence ingestion, Mol*, or authorization claims unsupported by an actual principal.

**RED tests and IDs:** `test_cm6_001_rejects_dotdot`, `test_cm6_002_rejects_absolute_path`, `test_cm6_003_rejects_encoded_traversal`, `test_cm6_004_rejects_glob_and_metacharacters`, `test_cm6_005_rejects_symlink_escape`, `test_cm6_006_rejects_symlink_swap_or_retarget`, `test_cm6_007_rejects_registered_artifact_retarget_before_schedule`, `test_cm6_008_regular_file_and_content_match`, `test_cm6_009_rehash_after_copy`, `test_cm6_010_limits_and_collision_safe_names`, `test_cm6_011_authorized_registered_id`, and `test_cm6_012_immutable_receipt_and_import_identity`.

Fixture family `imports/negative/` explicitly contains lexical `../`, absolute path, percent/double-encoded traversal, glob tokens (`*`, `?`, `[]`, `{}`), shell/metacharacters (`;`, `|`, `$()`, backticks, newline), symlink escape, symlink swap/retarget, and registered-artifact retarget between validation and scheduling.

**Implementation scope:** Accept uploads or registered IDs, resolve/authorize principal, use descriptor-safe open/copy where available, canonicalize containment, reject tokenized paths, copy to job-owned storage, rehash/inspect content after copy, revalidate registered source identity immediately before schedule, and emit immutable receipt plus import manifest. Raw source paths never enter Nextflow params.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_import_security.py \
  platform/api/tests/test_conformational_mapping_schemas.py \
  platform/api/tests/test_conformational_mapping_routing.py
python -m json.tool \
  platform/api/tests/fixtures/conformational_mapping/imports/positive/expected_receipt.json >/dev/null
# Run Section 5.4 baselines; preserve security-test logs in Phase 6 evidence root.
```

**Acceptance criteria:** Every named attack rejects before scheduling; retarget races are caught; every accepted regular file has authorized principal, source/staged hashes, limits, deterministic index/ID, immutable receipt, and content/extension agreement; no raw path/glob reaches workflow.

**Dirty-tree-safe rollback:** Stop only Phase 6 pytest processes and phase-tagged staging copies. Restore `jobs.py` and canonical workflow from start copies; delete only absent-at-start import service/module/test/fixture/review paths and job-owned staged directories listed in the Phase 6 ledger; never delete source uploads or registered artifacts; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_6_spec_check.json`; roles: security, workflow/runtime, and API/persistence reviewers.

**GO:** All negative/positive fixtures pass, no TOCTOU gap remains in the approved design, baselines have no target regression, reviewers approve.

**STOP:** Any traversal/metacharacter reaches a filesystem operation, symlink/registered artifact can retarget, authorization is abstract rather than implementable, or source content is mutable after validation.

### Phase 7 — Full FrustraMPNN landscapes

**Separate authorization:** Required after Phase 6 `GO`.

**Allowlist — create/modify only:**

- create `modules/conformational_mapping_frustrampnn.nf`;
- create `scripts/finalize_frustrampnn_landscape.py`;
- modify `workflows/conformational_mapping.nf` only for normalization/scoring channels;
- create `platform/api/tests/test_conformational_mapping_frustrampnn.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/frustrampnn/`;
- create/modify `docs/reviews/conformational_mapping/phase_7_spec_check.json`.

**Forbidden/out of scope:** Rewriting `modules/frustrampnn.nf`, interface/ligand-aware claims, ΔΔG/thermodynamic copy, ranking, DB/frontend/Mol*, or native-only summary as primary data.

**RED tests and IDs:** `test_cm7_001_selected_chain_dispatch`, `test_cm7_002_exact_twenty_unique_slots`, `test_cm7_003_exactly_one_native_slot`, `test_cm7_004_duplicate_malformed_nonfinite_fail`, `test_cm7_005_missingness_statuses`, `test_cm7_006_threshold_boundaries`, `test_cm7_007_raw_csv_retained`, `test_cm7_008_mapping_join_to_source`, and `test_cm7_009_semantic_limit_metadata`.

**Implementation scope:** Wrap existing FrustraMPNN through the Phase 2 normalizer; validate full raw rows before summaries; emit exact-20 landscape with mapping/checkpoint/threshold/raw provenance. Score selected protein chains independently and declare excluded context.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_frustrampnn.py \
  platform/api/tests/test_conformational_mapping_normalization.py \
  platform/api/tests/test_conformational_mapping_schemas.py
python scripts/finalize_frustrampnn_landscape.py \
  --raw platform/api/tests/fixtures/conformational_mapping/frustrampnn/complete/raw.csv \
  --map platform/api/tests/fixtures/conformational_mapping/frustrampnn/complete/cm_structure_map_v1.json \
  --out /tmp/cm_phase7/cm_frustration_landscape_v1.json
# Run Section 5.4 baselines. Fixture output is not a live model result.
```

**Acceptance criteria:** Each scoreable residue has exactly 20 validated slots; unscoreable residues are explicit; duplicate/malformed/nonfinite behavior is fail-closed; every row maps to source identity; raw/provenance hashes exist; forbidden scientific claims are absent.

**Dirty-tree-safe rollback:** Stop only Phase 7 pytest/finalizer processes; no live scoring/GPU job unless separately authorized. Restore canonical workflow if modified; delete only absent-at-start wrapper/finalizer/test/fixtures/review paths and `/tmp/cm_phase7`; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_7_spec_check.json`; roles: scientific/data-contract and workflow/runtime reviewers.

**GO:** Full-landscape/mapping tests pass and reviewers approve semantic/provenance boundaries.

**STOP:** Row-count divisibility substitutes for exact identity, duplicates are averaged, raw position is used as identity, context claims expand, or fixtures are called live success.

### Phase 8 — Comparison and ranking

**Separate authorization:** Required after Phase 7 `GO`.

**Allowlist — create/modify only:**

- create `platform/api/services/conformational_mapping/analysis.py`;
- create `scripts/analyze_conformational_mapping.py`;
- create `platform/api/tests/test_conformational_mapping_analysis.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/analysis/`;
- create/modify `docs/reviews/conformational_mapping/phase_8_spec_check.json`.

**Forbidden/out of scope:** Launching mutations/resampling, workflow/backend changes, DB/API persistence, frontend/Mol*, biological-benefit claims, ΔΔG, or thermodynamic interpretation.

**RED tests and IDs:** `test_cm8_001_matched_pair_by_seed_sample_and_invariants`, `test_cm8_002_unmatched_pairs_are_explicit`, `test_cm8_003_redistribution_included_residues`, `test_cm8_004_hierarchical_weighting`, `test_cm8_005_hotspot_formula`, `test_cm8_006_switch_formula`, `test_cm8_007_support_and_missingness`, `test_cm8_008_rank_stability`, `test_cm8_009_deterministic_tie_break`, `test_cm8_010_persisted_components_reconstruct_rank`, and `test_cm8_011_no_thermodynamic_language`.

**Implementation scope:** Compute exactly Section 3.10 formulas from validated landscapes; persist pair ledgers, residue inclusion/exclusion, within/between-seed statistics, hotspot/switch components, transition counts, support, robustness, sort keys, and source-row hashes.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_analysis.py \
  platform/api/tests/test_conformational_mapping_frustrampnn.py \
  platform/api/tests/test_conformational_mapping_schemas.py
python scripts/analyze_conformational_mapping.py \
  --fixture platform/api/tests/fixtures/conformational_mapping/analysis/hand_calculated/input.json \
  --out /tmp/cm_phase8/cm_analysis_v1.json
python -m json.tool /tmp/cm_phase8/cm_analysis_v1.json >/dev/null
# Run Section 5.4 baselines.
```

**Acceptance criteria:** Hand calculations match every formula/statistic/order; matched pair rules reject invariant drift; no missing-value imputation or 25-way independence; ranks reconstruct exactly from persisted components; insufficient support yields status; semantic-limit string scan passes.

**Dirty-tree-safe rollback:** Stop only Phase 8 pytest/analyzer processes. Restore any pre-existing allowlisted paths; delete absent-at-start analysis service/script/test/fixtures/review paths and `/tmp/cm_phase8`; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_8_spec_check.json`; roles: scientific/data-contract and API/persistence reviewers.

**GO:** Formula fixtures and deterministic ranking pass; reviewers approve estimands and wording.

**STOP:** Pairing by row order, undefined residue population/weighting, unreconstructable score, hidden missingness, or thermodynamic/benefit claim.

### Phase 9 — Mutagenesis Library handoff only

**Separate authorization:** Required after Phase 8 `GO`. It does not authorize Protenix resampling.

**Allowlist — create/modify only:**

- create `platform/api/services/conformational_mapping/mutagenesis_handoff.py`;
- modify `platform/api/routers/jobs.py` only at the existing Mutagenesis Library handoff seam;
- create `platform/api/tests/test_conformational_mapping_handoff.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/handoff/`;
- create/modify `docs/reviews/conformational_mapping/phase_9_spec_check.json`.

**Forbidden/out of scope:** Any workflow/GPU launch, WT/mutant Protenix execution, complete-complex materialization, DB schema changes beyond existing transactional job registration, frontend/Mol*, or protein-only ATOM reconstruction.

**RED tests and IDs:** `test_cm9_001_author_identity_to_sequence_index`, `test_cm9_002_insertion_code_translation`, `test_cm9_003_wt_validation`, `test_cm9_004_stale_source_hash_rejected`, `test_cm9_005_canonical_idempotency_key`, `test_cm9_006_same_retry_same_identities`, `test_cm9_007_changed_key_distinct`, `test_cm9_008_transactional_no_partial_registration`, and `test_cm9_009_handoff_carries_ranking_and_lineage`.

**Implementation scope:** Add only the versioned adapter from approved analysis candidates to existing Mutagenesis Library request/registration. Validate source hashes, WT, mapping, canonical candidate order, idempotency, and atomic registration. Return prepared identities/request, not launched resampling jobs.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_handoff.py \
  platform/api/tests/test_conformational_mapping_analysis.py
python -m json.tool \
  platform/api/tests/fixtures/conformational_mapping/handoff/positive/expected_handoff.json >/dev/null
# Run Section 5.4 baselines. Assert test scheduler spy launch_count == 0.
```

**Acceptance criteria:** No manual residue re-entry; source/WT/mapping validate; same retry returns same identities; changed key is distinct; injected failure leaves no partial registration; prepared handoff contains all lineage/components; scheduler launch count is zero.

**Dirty-tree-safe rollback:** Stop only Phase 9 pytest processes. Roll back phase-created database rows only through the phase test transaction/fixture teardown, never broad DB deletion. Restore `jobs.py` from start copy; delete absent-at-start handoff service/test/fixtures/review paths; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_9_spec_check.json`; roles: scientific/data-contract and API/persistence reviewers.

**GO:** Transaction/idempotency/lineage tests pass, no launch occurred, reviewers approve the handoff contract.

**STOP:** Resampling launches, stale hashes pass, WT translation is ambiguous, retries duplicate, partial registration survives, or ATOM reconstruction is used.

### Phase 10 — Matched WT/mutant Protenix resampling only

**Separate authorization:** Required after Phase 9 `GO`; approval names allowed model/runtime/GPU budget separately from implementation tests.

**Allowlist — create/modify only:**

- create `platform/api/services/conformational_mapping/resampling.py`;
- create `modules/conformational_mapping_resampling.nf`;
- modify `platform/api/routers/jobs.py` only for atomic resampling launch;
- modify `workflows/conformational_mapping.nf` only for resampling dispatch;
- create `platform/api/tests/test_conformational_mapping_resampling.py`;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/resampling/`;
- create/modify `docs/reviews/conformational_mapping/phase_10_spec_check.json`.

**Forbidden/out of scope:** New ranking formulas, handoff contract changes, persistence ingestion, frontend/Mol*, protein-only reconstruction, unmatched runtime policies, or changing non-mutated entities/features.

**RED tests and IDs:** `test_cm10_001_explicit_wt_control`, `test_cm10_002_materialize_from_complex_snapshot`, `test_cm10_003_exact_substitution_only`, `test_cm10_004_preserve_entities_copies_bonds_order`, `test_cm10_005_match_seed_sample_runtime`, `test_cm10_006_mutant_only_regenerate_policy`, `test_cm10_007_paired_regenerate_policy`, `test_cm10_008_features_disabled_control`, `test_cm10_009_unaffected_feature_bytes_identical`, `test_cm10_010_per_entity_hash_differences_declared`, `test_cm10_011_manifest_pairing_and_unmatched_status`, and `test_cm10_012_atomic_no_partial_launch`.

**Implementation scope:** Materialize explicit WT and mutant requests from the approved complex snapshot; apply exactly declared protein substitutions; implement the three feature modes from Section 3.11 with pinned tools/database/settings and per-entity hashes; launch/register transactionally; match outputs only by exact seed/sample and invariants. Focused tests may use scheduler/model fakes; live proof waits for Phase 12.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_resampling.py \
  platform/api/tests/test_conformational_mapping_handoff.py \
  platform/api/tests/test_conformational_mapping_protenix.py
python -m json.tool \
  platform/api/tests/fixtures/conformational_mapping/resampling/positive/expected_pair_manifest.json >/dev/null
# Run Section 5.4 baselines. Synthetic scheduler/model evidence is not live proof.
```

**Acceptance criteria:** Explicit WT control exists; complete complex and all unchanged bytes/identities/bonds/order are preserved; only declared sequence/features differ; feature mode/tool/DB/settings/hashes validate; matched cardinality is exact and unmatched records explicit; injected launch failure is atomic.

**Dirty-tree-safe rollback:** Stop only Phase 10 pytest processes and, if separately authorized, only jobs bearing Phase 10 request IDs in the process/job ledger. Restore `jobs.py` and canonical workflow from start copies; delete absent-at-start resampling service/module/test/fixtures/review paths and only phase-owned unlaunched request bundles; preserve authenticated failed-job artifacts; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_10_spec_check.json`; roles: workflow/runtime, scientific/data-contract, security, and API/persistence reviewers.

**GO:** All materialization/feature/matching/transaction tests pass, no focused/synthetic evidence is labeled live, reviewers approve.

**STOP:** Non-protein context loss, undeclared feature difference, changed-protein feature-byte reuse, template drift, pairing by row order, partial launch, or runtime identity mismatch.

### Phase 11 — Persistence, result contracts, and nonvisual API

**Separate authorization:** Required after Phase 10 `GO`.

**Allowlist — create/modify only:**

- modify `platform/api/services/result_contracts.py` and `platform/api/services/result_ingester.py`;
- create `platform/api/services/conformational_mapping/persistence.py`;
- create `platform/api/routers/conformational_mapping.py`;
- modify `platform/api/main.py` only to register the new router;
- modify `platform/api/database.py` and `platform/api/run_migrations.py` only if an approved additive migration is required;
- modify `platform/api/routers/designs.py` only for nonvisual contract-backed reads;
- create `platform/api/tests/test_conformational_mapping_persistence.py` and `platform/api/tests/test_conformational_mapping_api.py`;
- modify `platform/api/tests/test_result_contracts.py` and `platform/api/tests/test_confornets_result_ingester.py` only for alias/no-regression cases;
- create/modify under `platform/api/tests/fixtures/conformational_mapping/persistence/`;
- create/modify `docs/reviews/conformational_mapping/phase_11_spec_check.json`.

**Forbidden/out of scope:** Frontend/Mol*, workflow/backend changes, historical-row bulk rewrite, new alias spellings, metric-shape/filename inference, destructive migration, or unpaged full-matrix summary loads.

**RED tests and IDs:** `test_cm11_001_backend_contract_resolution`, `test_cm11_002_new_write_is_monomer_conformation`, `test_cm11_003_old_conformer_resolves_without_rewrite`, `test_cm11_004_unknown_alias_fails_closed`, `test_cm11_005_idempotent_ingestion`, `test_cm11_006_manifest_hash_validation`, `test_cm11_007_transaction_rollback`, `test_cm11_008_lineage_queries`, `test_cm11_009_landscape_pagination_and_range`, `test_cm11_010_no_protenix_import_misclassification`, and `test_cm11_011_confornets_experimental_behavior_preserved` across the named test files.

**Implementation scope:** Add additive persistence, five result contracts, strict alias resolver, idempotent transactional ingestion, lineage/manifests/landscape/analysis/handoff/resampling endpoints, pagination/range reads, and minimal nonvisual capabilities. Preserve historical stored spelling and legacy behavior.

**Exact verification commands/evidence:**

```bash
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_persistence.py \
  platform/api/tests/test_conformational_mapping_api.py \
  platform/api/tests/test_result_contracts.py \
  platform/api/tests/test_confornets_result_ingester.py
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_schemas.py \
  platform/api/tests/test_conformational_mapping_analysis.py \
  platform/api/tests/test_conformational_mapping_resampling.py
# Run Section 5.4 baselines. Use an isolated test database only.
```

**Acceptance criteria:** All schemas ingest/query by stable identity; retries do not duplicate; malformed/hash-invalid/unknown contracts fail atomically; canonical writes use `monomer_conformation`; old `conformer` rows resolve without rewrite; unknown alias fails; large matrices are paged; tests pass without frontend changes.

**Dirty-tree-safe rollback:** Stop only Phase 11 pytest/test-API processes. Roll back only the additive Phase 11 migration in an isolated test DB using its named down/reversal procedure; never run broad production DB rollback. Restore all modified allowlisted source/tests from start copies; delete absent-at-start persistence/router/tests/fixtures/review paths; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_11_spec_check.json`; roles: API/persistence, security, and scientific/data-contract reviewers.

**GO:** Alias, transactional, pagination, lineage, and all prior tests pass; migration review is additive/reversible; no frontend dependency; reviewers approve.

**STOP:** Historical rewrite, unknown alias acceptance, partial ingestion, backend inferred from filename/metrics, unbounded matrix load, destructive migration, or need for frontend to pass.

### Phase 12 — Authenticated current-run live E2E and release

**Separate authorization:** Required after Phase 11 `GO`; must name runtime, containers, GPU allocation, result root, service restart permission if any, and release operator. No such permission is granted by this plan.

**Allowlist — create/modify only:**

- create/modify `docs/reviews/conformational_mapping/phase_12_spec_check.json`;
- create under `docs/reviews/conformational_mapping/phase_12_e2e_manifest/` only the hashed summaries/pointers approved for repository review;
- all live runtime artifacts must be written outside the repo under `/mnt/BioModStack/bms_results/conformational_mapping_phase12/<approved_run_id>/`.

**Forbidden/out of scope:** Production source/config/test/frontend/workflow edits, schema changes, Mol*, ad hoc bug fixes, historical/synthetic substitution for a live row, and unapproved service restart. Any defect sends work back to its owning earlier phase with a new approval.

**Live probe tests and IDs:** `E2E12-CN-DIVERSITY`, `E2E12-CN-REFERENCE`, `E2E12-PTX-PROTEIN-5X5`, `E2E12-PTX-COMPLETE-5X5` (protein+DNA+RNA+ligand+ion and approved modification/bond/repeated-copy controls), `E2E12-IMPORT-POSITIVE`, `E2E12-IMPORT-NEGATIVE`, `E2E12-FRUSTRAMPNN-20`, `E2E12-ANALYSIS-HIERARCHY`, `E2E12-HANDOFF-IDEMPOTENT`, `E2E12-RESAMPLE-WT-MUTANT`, `E2E12-PERSISTENCE-ROUNDTRIP`, and `E2E12-LEGACY-CONFORNETS`.

**Implementation/probe scope:** No implementation. Launch fresh authenticated current-run jobs through the approved API/current service, preserve request IDs and API authentication principal, poll to terminal state, validate manifests/hashes/cardinality/composition/resource records, exercise handoff retry and matched resampling, ingest/query nonvisual APIs, and rerun baselines. Historical runs may be cited as context only.

**Exact verification commands/evidence:**

```bash
python scripts/probes/conformational_mapping/probe_phase0_runtime.py \
  --release-matrix docs/reviews/conformational_mapping/phase_12_e2e_manifest/matrix.json \
  --output /mnt/BioModStack/bms_results/conformational_mapping_phase12/<approved_run_id>
PYTHONPATH=platform/api python -m pytest -q \
  platform/api/tests/test_conformational_mapping_schemas.py \
  platform/api/tests/test_conformational_mapping_normalization.py \
  platform/api/tests/test_conformational_mapping_routing.py \
  platform/api/tests/test_conformational_mapping_confornets.py \
  platform/api/tests/test_conformational_mapping_protenix.py \
  platform/api/tests/test_conformational_mapping_import_security.py \
  platform/api/tests/test_conformational_mapping_frustrampnn.py \
  platform/api/tests/test_conformational_mapping_analysis.py \
  platform/api/tests/test_conformational_mapping_handoff.py \
  platform/api/tests/test_conformational_mapping_resampling.py \
  platform/api/tests/test_conformational_mapping_persistence.py \
  platform/api/tests/test_conformational_mapping_api.py
# Run all Section 5.4 baselines unchanged.
```

Each scheduled execution row must point to current authenticated request/status/API captures, zero workflow exit, nonempty structures, exact coordinates/cardinality, valid manifests, hashes, composition/mapping audits, correct DB result contract, resource use, and result-root identity. Each `E2E12-IMPORT-NEGATIVE` rejection row instead must record the exact attack fixture, authenticated principal and request capture, canonical rejection code/message, validation-stage timestamp, and before/after scheduler-ledger, workflow-event, DB-result-count, and result-root inventory/hash evidence proving that no request/job/workflow ID was allocated and no scheduling or result write occurred. A rejection row must not fabricate a workflow exit, structure, manifest, or result contract.

**Acceptance criteria:** Every matrix row passes its declared row-kind schema with current authenticated evidence and no skipped/partial rows. Scheduled execution rows satisfy the execution evidence above; negative import rows satisfy the rejection-only evidence above and prove rejection before scheduling. Protenix complete-complex and resampling rows prove composition/feature differences; handoff same-key retry is idempotent; API roundtrip is current; legacy lane has no regression; broad baseline has no target regression. Focused/synthetic tests are supporting evidence only.

**Dirty-tree-safe rollback:** Stop/cancel only Phase 12 request/job IDs in the ledger and only through approved control APIs; restore no production file because none is allowlisted. Delete only absent-at-start review summary files if the gate is rolled back; retain/quarantine current-run result artifacts and logs for failure analysis unless separately authorized for deletion. Rehash unrelated dirty files and confirm services not launched by this phase remain untouched.

**Review:** `docs/reviews/conformational_mapping/phase_12_spec_check.json`; roles: release operator, workflow/runtime, scientific/data-contract, security, and API/persistence reviewers.

**GO:** All authenticated current-run matrix rows, exact tests, baselines, and independent reviews pass; release operator records `GO` for nonvisual Phases 0–12.

**STOP:** Any skipped, synthetic-only, historical-only, partial, unauthenticated, hash-invalid, composition-invalid, nonterminal, or resource-unrecorded row; any source fix needed; any unapproved restart/job; or any reviewer rejection.

### Phase 13 — Final separately approved Mol* consumer

**Separate authorization:** Required only after Phase 12 nonvisual `GO`, with a frontend owner-approved baseline and explicit permission to inspect/modify Mol*. Phase 13 is not implied by Phase 12 and does not retroactively gate Phases 0–12.

**Allowlist — create/modify only:**

- modify `platform/frontend/src/components/StructureViewerPane.tsx`;
- create `platform/frontend/src/components/conformationalMapping/ConformationalMappingViewer.tsx`;
- create `platform/frontend/src/components/conformationalMapping/conformationalMappingSemantics.ts`;
- create `platform/frontend/src/tests/conformationalMappingSemantics.test.ts`;
- modify `platform/frontend/src/tests/structureViewerSemantics.test.ts` only for approved integration assertions;
- create/modify `docs/reviews/conformational_mapping/phase_13_spec_check.json`.

**Forbidden/out of scope:** Backend schemas, analysis formulas, result ingestion, workflows, API semantics, generation/scoring, historical-row rewrite, or UI compensation for missing backend data.

**RED tests and IDs:** `test_cm13_001_only_approved_contracts_render`, `test_cm13_002_unknown_contract_fails_closed`, `test_cm13_003_candidate_order_and_identity`, `test_cm13_004_mapping_overlay_uses_api_identity`, `test_cm13_005_independent_hypothesis_label`, `test_cm13_006_no_trajectory_or_thermodynamic_copy`, `test_cm13_007_missing_analysis_is_explicit`, and `test_cm13_008_legacy_viewer_no_regression` in the named TypeScript test files.

**Implementation scope:** Consume only Phase 11 approved APIs to browse candidate structures and mapped overlays. Preserve deterministic candidate identity/order, explicit missingness, backend labels, and semantic limits. Do not calculate scientific values in the browser.

**Exact verification commands/evidence:**

```bash
pnpm --dir platform/frontend test
pnpm --dir platform/frontend lint
BMS_FRONTEND_BUILD_OUT_DIR=/tmp/cm_phase13_frontend_dist \
  pnpm --dir platform/frontend build:isolated
# Run Section 5.4 baselines and API contract tests from Phase 11.
```

If a separately approved visual smoke is run, preserve screenshots/API response hashes under the external Phase 13 evidence root; a WebGL limitation is reported, not hidden.

**Acceptance criteria:** Only known result contracts render; unknown/malformed data fails closed; structures/order/overlays match API identity; missing data is explicit; labels say independent generated hypotheses and empirical/post-hoc analysis, never trajectory/equilibrium/free energy/ΔΔG; frontend build/test/lint and legacy viewer assertions pass.

**Dirty-tree-safe rollback:** Stop only Phase 13 build/test processes and any separately approved local preview process recorded in its ledger. Restore the two existing frontend files from phase-start copies; delete only absent-at-start new component/test/review paths and `/tmp/cm_phase13_frontend_dist`; do not touch backend or other frontend work; rehash unrelated dirty files.

**Review:** `docs/reviews/conformational_mapping/phase_13_spec_check.json`; roles: frontend/Mol*, scientific/data-contract, API/persistence, and release operator.

**GO:** Phase 12 remains green, frontend owner/operator authorization exists, all consumer/semantic/no-regression checks pass, reviewers approve.

**STOP:** Backend change is needed, unknown contracts render, identity/order diverges, semantic limits are weakened, unrelated frontend work drifts, or Phase 12 evidence is invalidated. Phases 0–12 remain independently releasable.

---

## 7. Review JSON minimum contract

Every `docs/reviews/conformational_mapping/phase_<N>_spec_check.json` must contain:

```json
{
  "phase": 0,
  "operator_approval": {"approver": "independent operator", "utc": "...", "allowlist_sha256": "...", "decision": "GO|STOP"},
  "repo": {"root": "...", "branch": "...", "head": "...", "start_porcelain_sha256": "...", "end_porcelain_sha256": "..."},
  "allowlist": [],
  "absent_at_start": [],
  "start_hashes": {},
  "end_hashes": {},
  "unrelated_dirty_start_hashes": {},
  "unrelated_dirty_end_hashes": {},
  "red_or_probe_tests": [],
  "commands": [],
  "runtime_artifacts": [],
  "baseline_attribution": [],
  "acceptance": [],
  "rollback_evidence": {},
  "reviewers": [{"role": "...", "identity": "...", "decision": "GO|STOP"}],
  "known_preexisting_failures": [],
  "final_decision": "GO|STOP"
}
```

The actual `phase` equals the phase number. A missing field, self-review, stale hash, unrecorded allowlist path, or unresolved drift forces `STOP`.

---

## 8. Definition of done

The nonvisual orchestrator is done at Phase 12 only when all separately approved Phase 0–12 review records say `GO`, all three backends emit the common outer contract without erasing native semantics, Protenix preserves complete complexes and immutable resume authority, normalization is source-traceable, exact-20 landscapes and executable estimands are persisted, handoff and resampling are separately transactional/idempotent, changed-sequence features obey a declared validated mode, aliases behave exactly as specified, and the authenticated current-run release matrix passes.

Phase 13 is a final optional consumer and is never evidence for backend/scientific correctness. Until a phase passes, report the first failed gate and classify evidence honestly. Source declarations, focused tests, synthetic fixtures, historical results, filenames, and process exit codes are evidence inputs—not substitutes for a validated manifest or authenticated live current-run proof.
