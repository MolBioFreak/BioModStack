# Conformational Mapping Phase 0 Contract Definitions v1

**Document ID:** `cm_contract_definitions_v1`
**Status:** Phase 0 human-readable definitions; not an executable schema.
**Authority:** BioModStack policy unless a requirement is explicitly tagged `[UG]` or `[LO]`.
**Runtime status:** All runtime-dependent statements are **unmeasured** until authenticated Phase 0 evidence exists.

This document freezes human-readable definitions corresponding to Sections 3.1–3.13 of the conformational-mapping orchestrator plan. It does not claim that Protenix, ConforNets, FrustraMPNN, USAlign, a container, a checkpoint, or any baseline command has succeeded. `[UG]` means pinned upstream guidance, `[LO]` means a plan-anchored local source observation, and `[BP]` means planned BioModStack policy.

## Canonical bytes and hash binding `[BP]`

1. A file hash is lowercase SHA-256 of the file's exact bytes.
2. Phase 0 JSON canonicalization is the deterministic stdlib profile: UTF-8; objects sorted recursively by key; no insignificant whitespace; separators `,` and `:`; Unicode emitted directly; arrays retain order; duplicate JSON object keys and non-finite numbers are forbidden. The Python rendering is `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")` after duplicate-key detection.
3. `registry_sha256` is SHA-256 of the canonical registry object with only `registry_sha256` omitted.
4. `definitions.sha256` binds this exact Markdown file byte-for-byte. Every fixture record binds a repository-relative fixture path and exact byte SHA-256.
5. Phase 1 may replace this restricted profile with a complete RFC 8785 implementation only through a reviewed version change; no hash is silently reinterpreted.

## 3.1 Request envelope — `cm_request_v1`

A request has `schema_name=cm_request`, integer `schema_version=1`, UUID `request_id`, one backend from `protenix_v2_ensemble`, `confornets`, or `external_import`, ordered targets, ordered seeds, samples per seed, feature/runtime/analysis policies, source, creator/principal, and `request_sha256`. `[BP]`

`ordered_seeds` is nonempty and contains ordered, unique signed 32-bit integers. The approved default candidate vector is exactly five ordered unique values `[101,202,303,404,505]`; five is `[BP]`, not an upstream recommendation. `samples_per_seed` is a positive integer and the default candidate is five. API, generated JSON, and CLI seeds must agree byte-for-byte in value and order or admission rejects before scheduling. Equal seed/sample coordinates are matched inference controls, not physical states or a bitwise-determinism claim. `[UG][BP]`

## 3.2 Complete-complex snapshot — `cm_complex_snapshot_v1`

A snapshot preserves original source bytes, normalized JSON, hashes, target ID/order, token/atom admission counts, unsupported-field report, and ordered entities. Each entity has `entity_type`, immutable nonempty `source_entity_id`, positive integer `count`, and `ordered_instance_ids`. Instance IDs are nonempty and unique across the target, and their cardinality equals `count`. `[UG][BP]`

Supported contract representations cover protein, DNA, RNA, CCD ligand, SMILES ligand, ion, protein modification, and covalent bond records. Unknown entity types, unknown safety-relevant fields, unsupported modifications/bonds, malformed sequences, dangling 1-based bond references, lossy conversion, token-limit rejection, or ambiguous entity ordering reject explicitly. No coercion, omission, deduplication, order reconstruction, or count collapse is permitted. `[UG][BP]`

Every instance maps bijectively through `(source_entity_id, source_instance_id) -> (runtime_target_id, runtime_entity_id, runtime_instance_id, runtime_order) -> (candidate_id, output_label_asym_id, output_auth_asym_id, output_entity_order)`. Repeated proteins and ligands use one instance ID per copy. Identical sequences with distinct source IDs remain distinct entities. Composition comparison includes type, order, count, instance IDs, representation, modifications, bonds, and mapping cardinality. `[BP]`

## 3.3 Backend coordinates and candidate IDs

`candidate_meta.backend_coordinates` is a discriminated union. IDs include backend prefix, target identity, and a 20-hex-character prefix of SHA-256 over the complete canonical coordinate object; collisions compare both ID and full coordinates. `[BP]`

* Protenix coordinate: `(target_id, ordered_seed, zero_based_sample_index)`; `cm_ptx_<target_slug>_<digest20>`. Shared-layout cardinality is `targets × seeds × samples`; otherwise sum each target's seed count times samples.
* ConforNets coordinate: `(target_id, task, test_case_id, reference_id_or_none, run_index, saved_step, confornet_index, sample_index)`; `cm_cn_<target_slug>_<digest20>`. Group cardinality is `runs × saved_steps × confornets × samples`, summed over groups.
* Import coordinate: `(target_id, zero_based_staged_index, source_content_sha256, staged_receipt_sha256)`; `cm_imp_<target_slug>_<index6>_<source_digest16>`. Duplicate content in a receipt rejects.

Exactly one authoritative structure and every mandatory sidecar bind each expected coordinate. Missing, duplicate, extra, or shared coordinates reject finalization.

## 3.4 Workflow tuples

Producer input is `tuple val(target_meta), path(request_json), path(staged_assets)`. Producer output is `tuple val(candidate_meta), path(authoritative_cif), path(confidence_json), path(full_data_json), path(native_tree)`. Metadata always carries request, target, backend, full coordinate, settings digest, source snapshot hash, expected coordinates, and manifest relationships. Optional artifacts use explicit nullable status/reason records. `[BP]`

## 3.5 Immutable resume key

The resume descriptor binds request and source hashes, complex snapshot hash where applicable, backend/version/commit, runtime/container/model/checkpoint identities and hashes, feature policy and per-entity hashes, ordered coordinate plan, expected cardinality, manifest contract/roles, and canonical settings/runtime policy. `resume_key=sha256(canonical(resume_descriptor))`. `[BP]`

Resume is admitted only when the descriptor matches and complete native and ensemble manifests validate. Filenames, directories, Nextflow cache, logs, and exit code are never authority. Partial reuse is forbidden; stale/missing/extra/hash-mismatched data requires quarantine and a new identity or explicit rebuild.

## 3.6 Native artifacts — `cm_native_artifacts_v1`

Each file record has contained collision-safe relative path, SHA-256, byte count, media type, semantic role, backend coordinate where applicable, provenance/settings digest, and input/preprocessing/MSA/template/log/runtime relationships. Protenix requires runtime input, every authoritative CIF, confidence/ranking JSON, full-data JSON, feature-policy records, logs, runtime config, and composition audit. ConforNets requires request/preprocess records, native state/loss files, every conformer, explicit computed/not-computed optional analytics, command logs, and runtime provenance. Import requires receipt and every staged structure. `[BP]`

## 3.7 Ensemble — `cm_ensemble_v1`

The ensemble binds request/source snapshots, backend/runtime/container/checkpoint identities, feature-policy hash, expected coordinate plan/cardinality, ordered candidates, native-manifest hash, warnings/omissions, terminal status, timestamps, command provenance, and resume key. Each candidate binds one authoritative structure and mandatory sidecars. Missing, duplicate, extra, shared, basename-colliding, and unreferenced artifacts reject. `[BP]`

## 3.8 Structure map — `cm_structure_map_v1`

Each selected atom/residue row records target/candidate/entity/instance, source model, mmCIF label/auth chain and residue identity, insertion code, residue name, sequence index, normalized PDB identity, N/CA/C/O source atom identity, altloc/model decision, and status/reason. The original CIF hash remains authoritative. Repeated chains, multicharacter IDs, insertion codes, altlocs, multiple models, PDB limits, nonstandard residues, and missing backbone atoms must be explicit; ambiguity rejects. `[BP]`

## 3.9 FrustraMPNN landscape — `cm_frustration_landscape_v1`

The primary key is `(target_id,candidate_id,entity_instance_id,auth_asym_id,auth_seq_id,insertion_code,sequence_index,mutation_aa)`. Canonical amino-acid order is exactly `ACDEFGHIKLMNPQRSTVWY`. Every scoreable mapped residue has exactly 20 unique ordered slots and exactly one native slot. Each slot records WT, substitution, nullable finite score, class, scoreable, status, and reason. Missing/malformed/duplicate/nonfinite data is explicit and never zero-imputed or discarded. Raw CSV and checkpoint/threshold provenance are hashed. `[UG][BP]`

Threshold policy `frustrampnn_class_v1`: high at score `<= -1.0`, neutral for `-1.0 < score < 0.58`, minimally frustrated at `>= 0.58`. The installed checkpoint identity remains unmeasured; no default checkpoint is claimed. FrustraMPNN scores are single-chain backbone-context predictions, not ΔΔG, free energy, affinity, binding/interface energy, calibrated uncertainty, or functional effect. `[UG][BP]`

## 3.10 Analysis — `cm_analysis_v1`

Only finite `status=ok` rows enter arithmetic. Protenix strata are seeds with samples nested within seed; ConforNets strata are full task/test-case/reference/run/step/ConforNet coordinates with sample nested inside; imports are declared singleton strata. Matching uses exact coordinates and invariant runtime/feature/source lineage, never row order. Missing values remain in support denominators and are never imputed. `[BP]`

The contract defines substitution difference, matched context difference, realized self difference, whole-chain signed/absolute/class-transition redistribution, equal-weight hierarchical stratum means/fractions, outer and coordinate support, sign consistency, provenance-valid clash-free fraction, same-universe Spearman rank stability, and total status order `insufficient_support`, then `robust`, else `conditional`. Approved Protenix 5×5 robustness requires at least 4/5 seed strata, 3 samples per valid seed, sign consistency `>=0.80`, clash-free `>=0.90`, and rank stability `>=0.60`; non-5×5 layouts require a separately versioned policy. Results say “FrustraMPNN score difference”; thermodynamic language is forbidden.

## 3.11 Changed-sequence feature policy

Exactly one mode is selected: `[BP]`

1. `regenerate_mutated_protein_v1`: regenerate changed protein MSA and enabled templates; unaffected entities/features are byte-identical.
2. `paired_regenerate_changed_protein_v1`: independently regenerate WT and mutant changed-protein features in one pinned runtime; unaffected data are byte-identical.
3. `features_disabled_control_v1`: disable protein MSA, templates, and RNA MSA for both as a controlled ablation.

Default-parameter mode and manual cycle/step overrides are mutually exclusive: `use_default_params=true` omits `n_cycle` and `n_step`; manual overrides require `use_default_params=false`. RNA MSA may not drift when RNA is unchanged. Every entity records source/WT/mutant/tool/database/settings hashes and one declared difference. Reused changed-sequence MSA, template-mode drift, tool drift, changed unaffected bytes, or undeclared difference rejects. `[UG][BP]`

## 3.12 Mutagenesis handoff and lineage

`cm_mutagenesis_handoff_v1` binds source ensemble/analysis/complex hashes, target/entity instance, mapped author identity, validated WT, sequence/insertion identity, substitutions, mutation-set identity/string, evidence keys, support/missingness/ranking, warnings, policies, adapter version, and idempotency key over source digest, canonical candidate set, and canonical resampling settings. `[BP]`

Phase 9 only prepares identity; Phase 10 separately launches. Same-key retry is idempotent and partial launch is forbidden. WT and mutant derive from the complete-complex snapshot. Only declared substitutions and changed-entity features may differ; partners, entities, copies, bonds, order, and controls remain identical.

## 3.13 Result contracts and legacy alias

Canonical IDs are `conformational_mapping_protenix_v1`, `conformational_mapping_confornets_v1`, `conformational_mapping_import_v1`, `conformational_mapping_analysis_v1`, and `conformational_mapping_resampling_v1`. New writes use `monomer_conformation`. Reads accept exactly `monomer_conformation` and historical `conformer`, normalize in memory, and never bulk-rewrite historical spelling. Near misses and unknown classes fail closed. `confornets_experimental` behavior remains unchanged. Persistence/API acceptance does not depend on Mol*. `[LO][BP]`

## Phase 0 vector and evidence contract

The vector registry contains exactly 53 named vectors. Every vector has one family, one or more classification tags from `[UG]`, `[LO]`, `[BP]`, an expected contract disposition, referenced fixture path/hash/case, exact planned probe command, exact evidence requirements, and `runtime_status="unmeasured"`. Positive vectors expect `accept_contract`; negative vectors expect `reject_contract`. Contract validation does not promote runtime status.

The exact planned runtime probe command is the Phase 0 plan command below. The validator records and validates it but never executes it:

```bash
python scripts/probes/conformational_mapping/probe_phase0_runtime.py --vectors docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json --output /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>
```

Each vector's `probe_vector_id` and `evidence_subdirectory` are its exact vector ID under that approved run root. Required per-vector evidence filenames are `command.json`, `input_hashes.json`, `output_hashes.json`, `artifact_tree.json`, `runtime_identity.json`, `resources.json`, `exit_status.json`, and `disposition.json`. `[BP]`

Runtime evidence, when separately authorized, must reside under `/mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>/`, with `runtime_evidence.json`, a hash ledger, and one per-vector directory containing exact command, input/output hashes, artifact tree/counts, runtime identities, resources, and exit status. Only authenticated evidence may change a runtime status to a measured state; docs, fixtures, validator success, or synthetic outputs are not live success.

For deterministic future evidence verification, `runtime_evidence_hashes.json` has exactly `schema_name="cm_phase0_runtime_evidence_hashes"`, `schema_version=1`, the frozen registry and definitions hashes, and a complete `files` array of contained relative path/lowercase SHA-256 pairs. It inventories every evidence file except itself. `runtime_evidence.json` has exactly `schema_name="cm_phase0_runtime_evidence"`, `schema_version=1`, nonempty `run_id`, the same frozen hashes, and 53 ordered vector results. Each result has its `id`, `runtime_status="measured"`, same-name evidence subdirectory, and an observed disposition of `accept_contract`, `reject_contract`, `runtime_error`, or `unsupported`; measured does not mean successful. Evidence paths and path components may not be symlinks. `[BP]`

The exact future evidence-verification command is:

```bash
python scripts/probes/conformational_mapping/validate_phase0_vectors.py verify-evidence --vectors docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json --evidence-root /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id> --hash-ledger /mnt/BioModStack/bms_results/conformational_mapping_phase0/<approved_run_id>/runtime_evidence_hashes.json
```

The exact validation command is:

```bash
python scripts/probes/conformational_mapping/validate_phase0_vectors.py --definitions docs/specs/conformational_mapping/cm_contract_definitions_v1.md --vectors docs/specs/conformational_mapping/cm_contract_test_vectors_v1.json
```

The validator is stdlib-only, rejects symlinks and escaping/absolute fixture paths, checks fixture bytes and semantic cross-vector invariants, and includes offline self-tests that mutate in-memory/temp-directory copies only.
