# NGS Workflow Completion Spec — Plasmid QC First

> **For Hermes:** Use `subagent-driven-development` for implementation after this spec is approved. Work phase-by-phase, with real Nextflow/runtime proof before calling any workflow complete.

**Goal:** Finish BioModStack's ONT/NGS workflow family so plasmid QC is complete first, then basecalling, methylation, construct screening, dimer/multimer QC, and RNA paths are made truthful and testable.

**Architecture:** Keep the Phase 1 direction: standalone DSL2 workflow entrypoints under `workflows/ngs/`, direct module inclusion from `modules/ngs/`, no `nanopore_methylation.nf` compatibility wrapper. Device/MinKNOW control stays in API/host-agent surfaces; Nextflow owns reproducible file-based analysis. Every workflow contract must match actual artifacts emitted at runtime.

**Tech Stack:** Nextflow DSL2, Dorado, minimap2, samtools, modkit, EPI2ME wf-clone-validation, BioModStack FastAPI service registry, typed `sequence_qc` manifests, pytest.

---

## Current checkout evidence

- Repo: `/home/dalab/biomodstack/biomodstack`
- HEAD at audit time: `595654d`
- Worktree from container view: clean
- Monolith status: `workflows/ngs/nanopore_methylation.nf` is absent
- Current static NGS tests: `65 passed` for:
  - `platform/api/tests/test_ont_ngs_contract.py`
  - `platform/api/tests/test_ont_ngs_workflow_products.py`
  - `platform/api/tests/test_nanopore_nextflow.py`

## Current workflow inventory

| Workflow | Lines | Role | Current status |
|---|---:|---|---|
| `workflows/ngs/ont_fastq_qc.nf` | 96 | FASTQ + reference plasmid QC | Close for reference-backed FASTQ; no reference-free mode; no dimer QC |
| `workflows/ngs/ont_plasmid_qc.nf` | 221 | POD5/BAM/FASTQ plasmid QC | Has arity bug for POD5/BAM `FastqPlasmidQC` calls; no dimer QC |
| `workflows/ngs/wf_clone_validation.nf` | 217 | Full plasmid QC / wf-clone path | Runs CloneValidation; missing dimer QC; duplicates construct screening |
| `workflows/ngs/ont_construct_screening.nf` | 216 | Construct screening | Functionally duplicate of `wf_clone_validation`; semantics not differentiated |
| `workflows/ngs/ont_methylation_analysis.nf` | 155 | POD5/BAM modified-base workflow | Structurally present; needs MM/ML tag validation and honest FASTQ contract alignment |
| `workflows/ngs/ont_basecall_dna.nf` | 87 | DNA POD5 basecalling | Seed complete; needs model/runtime proof |
| `workflows/ngs/ont_basecall_rna.nf` | 87 | RNA POD5 basecalling | Workflow text says RNA, but shared model default can still be generic `sup` |

## Current module inventory

| Module | Lines | Role | Current status |
|---|---:|---|---|
| `modules/ngs/dorado_basecall.nf` | 56 | POD5 → BAM | CLI shape OK; model path mounted via config; must preflight weights exist |
| `modules/ngs/dorado_align.nf` | 57 | BAM + reference → aligned BAM | Needs already-aligned BAM handling / explicit force realign policy |
| `modules/ngs/bam_prepare.nf` | 90 | Sort/index/reference prep/validate | `ValidateMappedBam` should not silently pass through with no threshold |
| `modules/ngs/fastq_align.nf` | 41 | FASTQ + reference → aligned BAM | Good seed; needs execution fixture |
| `modules/ngs/fastq_plasmid_qc.nf` | 453 | Alignment/coverage/consensus/manifest | Real implementation, but input contract assumes FASTQ and breaks BAM/POD5 callers |
| `modules/ngs/fastq_dimer_qc.nf` | 1426 | Dimer/multimer/junction QC | Implemented but not called anywhere; too much inline bash/awk |
| `modules/ngs/clone_validation.nf` | 203 | Nested EPI2ME wf-clone-validation | Real wrapper; must be treated as full plasmid QC assembly/construct stage |
| `modules/ngs/modkit_pileup.nf` | 32 | methylation BED | Needs MM/ML upstream validation |
| `modules/ngs/modkit_summary.nf` | 26 | modkit summary | Needs MM/ML upstream validation |

---

# Definition of Done

A workflow is **complete** only when all are true:

1. Runtime behavior matches `platform/api/services/ont_ngs_contract.py`.
2. It directly includes only needed modules; no aggregate/compat wrappers.
3. It has explicit input-mode validation.
4. It emits a `qc_manifest.json` where a manifest is promised.
5. Optional/missing artifacts are represented as unavailable/absent, not fake paths.
6. There is at least one actual execution test or scripted proof producing the advertised artifacts.
7. Static tests and relevant API contract tests pass.
8. For Dorado/modkit paths, model/tag preflights fail cleanly with operator-actionable errors.

---

# Phase 0 — Fix core plasmid-QC blockers

## Task 0.1 — Split `FastqPlasmidQC` input contract so BAM/POD5 paths work

**Objective:** Stop calling a three-input module with two inputs and create a reusable alignment-backed plasmid QC path.

**Files:**
- Modify: `modules/ngs/fastq_plasmid_qc.nf`
- Modify: `workflows/ngs/ont_plasmid_qc.nf`
- Modify: `workflows/ngs/wf_clone_validation.nf`
- Modify: `workflows/ngs/ont_construct_screening.nf`
- Test: `platform/api/tests/test_ont_ngs_workflow_products.py`

**Current evidence:**

`FastqPlasmidQC` requires three inputs:

```nextflow
input:
tuple path(bam), path(bai)
path reference
path fastq
```

But `ont_plasmid_qc` calls it with two inputs at lines 111 and 173.

**Required design:**

Create two explicit module processes or workflow-level wrappers:

1. `AlignmentPlasmidQC`
   - Inputs: `tuple path(bam), path(bai)`, `path reference`
   - Outputs: alignment stats, coverage, per-base support, consensus, IGV tracks, manifest
   - Does not need FASTQ read-length stats.

2. `FastqPlasmidQC`
   - Inputs: `tuple path(bam), path(bai)`, `path reference`, `path fastq`
   - Reuses/extends alignment-backed QC and adds FASTQ read length/yield stats.

**Acceptance:**

- `ont_plasmid_qc` POD5 + reference path calls a two-input alignment-backed QC process.
- `ont_plasmid_qc` BAM + reference path calls a two-input alignment-backed QC process.
- FASTQ path still calls FASTQ-aware QC and still emits read-length stats.
- Static test checks no `FastqPlasmidQC(` call has only two arguments.

## Task 0.2 — Wire `FastqDimerQC` into plasmid workflows

**Objective:** Make dimer/multimer QC part of the actual plasmid QC product, not an unused module.

**Files:**
- Modify: `workflows/ngs/ont_fastq_qc.nf`
- Modify: `workflows/ngs/ont_plasmid_qc.nf`
- Modify: `workflows/ngs/wf_clone_validation.nf`
- Modify: `workflows/ngs/ont_construct_screening.nf` or remove/differentiate it per Phase 1
- Modify: `platform/api/services/ont_ngs_contract.py`
- Modify: `platform/api/services/sequence_qc_manifest.py` if new artifact kinds are needed
- Test: `platform/api/tests/test_ont_ngs_workflow_products.py`
- Test: new `platform/api/tests/test_fastq_dimer_qc_contract.py` or equivalent

**Current evidence:**

`modules/ngs/fastq_dimer_qc.nf` exists but `grep -R "FastqDimerQC" workflows/ngs modules/ngs platform/api/tests` shows no workflow calls.

**Required behavior:**

- FASTQ input paths should run dimer QC directly from `fastq_path` + `reference_fasta`.
- POD5 paths should run dimer QC only if FASTQ/basecalled reads are available. If Dorado only emits BAM, either:
  - generate FASTQ from BAM where safe and documented, or
  - declare dimer QC unavailable for POD5 until basecall FASTQ extraction exists.
- BAM-only paths should not fake FASTQ dimer evidence. If no reads FASTQ exists, mark dimer outputs unavailable.

**Canonical dimer artifacts:**

At minimum, expose:

- `dimer_breakpoint_call.tsv`
- `dimer_evidence_by_position.tsv`
- `dimer_read_events.tsv`
- `dimer_breakpoint_sequences.tsv`
- `dimer_secondary_anomalies.tsv`
- `dimer_secondary_summary.tsv`
- optional `dimer_diagnostics.tar.gz`

**Acceptance:**

- `grep -R "FastqDimerQC" workflows/ngs` shows calls in real workflows.
- Contract includes dimer artifact kinds or explicitly documents them under plasmid QC artifacts.
- Synthetic FASTQ/reference run produces the canonical dimer TSV outputs.

## Task 0.3 — Add first real execution proof for FASTQ plasmid QC

**Objective:** Prove the core FASTQ workflow runs and emits promised files.

**Files:**
- Create: `platform/api/tests/fixtures/ngs/tiny_plasmid/reference.fasta`
- Create: `platform/api/tests/fixtures/ngs/tiny_plasmid/reads.fastq`
- Create: `platform/api/tests/test_ngs_fastq_runtime_contract.py`
- Possibly create: `scripts/run_tiny_ngs_fastq_qc.sh`

**Command target:**

```bash
nextflow run workflows/ngs/ont_fastq_qc.nf \
  -profile ont_fastq_qc \
  --fastq_path platform/api/tests/fixtures/ngs/tiny_plasmid/reads.fastq \
  --reference_fasta platform/api/tests/fixtures/ngs/tiny_plasmid/reference.fasta \
  --out_dir /tmp/bms-ngs-fastq-qc-proof
```

**Expected artifacts:**

- `/tmp/bms-ngs-fastq-qc-proof/align/aligned.bam`
- `/tmp/bms-ngs-fastq-qc-proof/align/aligned.bam.bai`
- `/tmp/bms-ngs-fastq-qc-proof/fastq_qc/qc_manifest.json`
- `/tmp/bms-ngs-fastq-qc-proof/fastq_qc/per_base_support.tsv`
- `/tmp/bms-ngs-fastq-qc-proof/fastq_qc/fastq_consensus.fasta`
- dimer outputs once Task 0.2 is done

**Acceptance:**

- Test skips cleanly only if `nextflow`, `minimap2`, or `samtools` are unavailable.
- On runtime-equipped host/container, test executes the workflow and validates artifact presence and non-empty key tables.

---

# Phase 1 — Separate workflow semantics

## Task 1.1 — Decide and implement `wf_clone_validation` vs `ont_construct_screening` semantics

**Objective:** Remove the current duplicate workflow problem.

**Files:**
- Modify: `workflows/ngs/wf_clone_validation.nf`
- Modify or remove/alias: `workflows/ngs/ont_construct_screening.nf`
- Modify: `platform/api/services/ont_ngs_contract.py`
- Modify: `platform/api/services/nextflow.py`
- Test: `platform/api/tests/test_ont_ngs_workflow_products.py`

**Current evidence:**

`diff -u workflows/ngs/wf_clone_validation.nf workflows/ngs/ont_construct_screening.nf` shows the same pipeline with only names/log messages changed.

**Required choice:**

Option A — make `ont_construct_screening` an alias/profile to `wf_clone_validation`:

- Fewer duplicate files.
- Honest if both mean the same product today.

Option B — differentiate:

- `wf_clone_validation`: full plasmid assembly/QC product.
- `ont_construct_screening`: expected-construct comparison product with explicit required reference/expected sequence and construct pass/fail summary.

**Recommendation:** Option B eventually, but Option A is acceptable as a short-term consolidation if implementation bandwidth is limited.

**Acceptance:**

- No two workflow files carry near-identical logic.
- Contract descriptions match actual differentiation or aliasing.

## Task 1.2 — Make `wf_clone_validation` the canonical full plasmid QC path

**Objective:** Align wf-clone with the spec: full plasmid QC = alignment + assembly/clone validation + dimer/multimer + per-base/consensus evidence.

**Files:**
- Modify: `workflows/ngs/wf_clone_validation.nf`
- Modify: `platform/api/services/ont_ngs_contract.py`
- Modify: manifest artifact schema as needed
- Tests: workflow product tests + runtime fixture tests

**Required behavior by input mode:**

| Input | Required chain |
|---|---|
| FASTQ | `FastqAlign → RunCloneValidation → FastqDimerQC → FastqPlasmidQC/AlignmentPlasmidQC → manifest` |
| BAM | `PrepareBamForAnalysis → ValidateMappedBam → RunCloneValidation → AlignmentPlasmidQC → manifest`; dimer unavailable unless FASTQ extraction exists |
| POD5 | `DoradoBasecall → DoradoAlign/BamPrepare → RunCloneValidation → AlignmentPlasmidQC → manifest`; dimer only if reads FASTQ is available/extracted |

**Acceptance:**

- `wf_clone_validation` produces/declares clone validation report, assembly outputs, plasmid QC evidence, and dimer evidence where available.
- No fallback reference-copy consensus can mark a construct as verified.

---

# Phase 2 — Basecalling correctness and Dorado runtime

## Task 2.1 — Fix RNA Dorado model selection

**Objective:** Ensure RNA workflow uses a real RNA model by default, not generic `sup`.

**Files:**
- Modify: `platform/api/services/ont_ngs_contract.py`
- Modify: `nextflow.config`
- Modify: `workflows/ngs/ont_basecall_rna.nf` if needed
- Test: `platform/api/tests/test_ont_ngs_contract.py`

**Current issue:**

`ont_basecall_rna.nf` prints `rna004_sup`, but `DoradoBasecall` defaults to `sup`, and the API normalizer sets model from generic quality mode unless explicit `dorado_model` is supplied.

**Required behavior:**

- If `ont_molecule_type == "rna"` and no explicit `dorado_model`, default to a configured RNA model string.
- Keep quality mode (`fast/hac/sup`) separate from full Dorado model name.
- Add model strings to config docs rather than hardcoding everything in UI.

**Acceptance:**

- Unit test proves RNA workflow normalization returns RNA model.
- DNA workflow still defaults to DNA quality/model policy.

## Task 2.2 — Add Dorado model path preflight

**Objective:** Fail before GPU work if Dorado weights are missing.

**Files:**
- Modify: `modules/ngs/dorado_basecall.nf`
- Modify: `nextflow.config` if new params needed
- Test: static test and runtime test if environment available

**Current status:**

Config binds `${params.weights_root}/dorado:/weights/dorado`, but no workflow preflight proves the model exists there.

**Required behavior:**

- Add explicit check for `/weights/dorado` readability.
- If using a named model, run a safe Dorado model-resolution check or document expected Dorado behavior.
- Error should say which host path is expected: `${params.weights_root}/dorado`.

**Acceptance:**

- Missing models produce actionable error before a long GPU run.

## Task 2.3 — Merge or share DNA/RNA basecalling workflow logic

**Objective:** Remove near-duplicate basecall workflows once model selection is correct.

**Files:**
- Modify: `workflows/ngs/ont_basecall_dna.nf`
- Modify: `workflows/ngs/ont_basecall_rna.nf`
- Optional create: `workflows/ngs/lib/basecall_common.nf`

**Acceptance:**

- Either one shared include drives both workflows, or tests ensure parity for common logic.

---

# Phase 3 — Methylation truthfulness

## Task 3.1 — Add MM/ML tag validation before modkit

**Objective:** Prevent silent empty modkit outputs when BAM lacks modified-base tags.

**Files:**
- Modify: `modules/ngs/bam_prepare.nf` or create `modules/ngs/modkit_validate.nf`
- Modify: `workflows/ngs/ont_methylation_analysis.nf`
- Test: new unit/static/runtime fixture tests

**Required behavior:**

- For BAM input, check reads/header for MM/ML tags before `ModkitPileup`/`ModkitSummary`.
- If absent:
  - for explicit methylation workflow, fail with actionable error, or
  - mark modified-base artifacts unavailable if policy chooses graceful degradation.

**Acceptance:**

- BAM without MM/ML does not produce a fake success.
- POD5 + Dorado with `modified_bases != none` remains valid.

## Task 3.2 — Fix methylation contract input modes

**Objective:** Ensure contract does not claim unsupported FASTQ methylation.

**Files:**
- Modify: `platform/api/services/ont_ngs_contract.py`
- Modify: frontend launch options if they expose FASTQ methylation
- Test: `platform/api/tests/test_ont_ngs_contract.py`

**Current mismatch:**

Contract lists `ont_methylation_analysis.input_modes=("pod5", "bam", "fastq")`, but workflow accepts only POD5 or BAM.

**Acceptance:**

- Either remove FASTQ from methylation input modes, or implement a truthful FASTQ path that reports modified bases unavailable due to missing MM/ML tags.

---

# Phase 4 — FASTQ/reference-free mode and manifest truth

## Task 4.1 — Decide `ont_fastq_qc` reference policy

**Objective:** Align description and runtime.

**Files:**
- Modify: `platform/api/services/ont_ngs_contract.py`
- Modify: `workflows/ngs/ont_fastq_qc.nf` if adding reference-free mode
- Test: contract tests and runtime tests

**Current mismatch:**

Contract says alignment-optional, implementation requires `--reference_fasta`.

**Recommendation:**

Short term: rename/describe as reference-required FASTQ plasmid QC.

Long term: add a reference-free FASTQ stats-only process producing:

- read count
- total bases
- read length histogram/summary
- N50
- Q-score stats if qualities are parsed
- no alignment/per-base/consensus artifacts

**Acceptance:**

- Runtime behavior matches contract exactly.

## Task 4.2 — Manifest schema for unavailable artifacts

**Objective:** Represent absent optional outputs without fake paths.

**Files:**
- Modify: `platform/api/services/sequence_qc_manifest.py`
- Modify: `scripts/build_sequence_qc_manifest.py`
- Test: `platform/api/tests/test_sequence_qc_manifest.py`

**Acceptance:**

- Missing optional artifacts have state `unavailable` or are omitted according to schema.
- Manifest APIs do not expose absolute backend filesystem paths.

---

# Phase 5 — Test and execution gate expansion

## Task 5.1 — Add static tests for known wiring hazards

**Files:**
- Modify: `platform/api/tests/test_ont_ngs_workflow_products.py`

**Tests to add:**

- `FastqDimerQC` is included/called by intended plasmid workflows.
- No two-input `FastqPlasmidQC` calls remain.
- `ont_methylation_analysis` contract input modes match workflow validation.
- RNA normalization selects RNA model.
- `wf_clone_validation` and `ont_construct_screening` are not duplicate unless deliberately aliased.

## Task 5.2 — Add runtime smoke tests behind environment gate

**Files:**
- Create: `platform/api/tests/test_ngs_runtime_smoke.py`

**Required skips:**

Skip if any are unavailable:

- `nextflow`
- `minimap2`
- `samtools`

**Acceptance:**

- FASTQ/reference workflow executes and validates output files.
- Dimer output validation is included after Phase 0.2.

## Task 5.3 — Add API manifest endpoint proof

**Objective:** Verify launched/completed job manifests are retrievable from API layer.

**Files:**
- Modify/add API tests around `/api/sequence-qc/jobs/{job_id}/manifest`

**Acceptance:**

- After a synthetic runtime job, manifest endpoint returns HTTP 200 and usable relative artifact paths.

---

# Workflow-specific remaining requirements checklist

## `ont_fastq_qc.nf`

- [ ] Keep `FastqAlign → FastqPlasmidQC` for reference-backed FASTQ.
- [ ] Add `FastqDimerQC`.
- [ ] Decide whether reference-free FASTQ stats are in this workflow or a separate workflow.
- [ ] Update contract description to match runtime.
- [ ] Add runtime fixture test.

## `ont_plasmid_qc.nf`

- [ ] Fix POD5/BAM `FastqPlasmidQC` call arity.
- [ ] Add alignment-backed QC process for BAM/POD5 paths.
- [ ] Add `FastqDimerQC` where input reads support it.
- [ ] Emit truthful manifest for reference-free mode or require reference for plasmid verification.
- [ ] Add runtime tests for FASTQ and BAM paths; POD5 once Dorado model/runtime is proven.

## `wf_clone_validation.nf`

- [ ] Treat as canonical full plasmid QC pipeline.
- [ ] Add dimer/multimer QC.
- [ ] Add plasmid evidence outputs/manifest for all input modes where data permits.
- [ ] Ensure clone validation outputs are represented in manifest.
- [ ] Runtime test with tiny BAM/FASTQ fixture or controlled skip if nested wf-clone not available.

## `ont_construct_screening.nf`

- [ ] Stop duplicating `wf_clone_validation`, or explicitly differentiate semantics.
- [ ] If differentiated, require expected construct/reference and emit construct screening pass/fail summary.
- [ ] Add test proving the workflow is not just renamed `wf_clone_validation`.

## `ont_methylation_analysis.nf`

- [ ] Add MM/ML modified-base tag preflight.
- [ ] Align contract input modes with workflow support.
- [ ] Make FASTQ behavior explicit: unsupported or unavailable-modified-bases, not fake success.
- [ ] Runtime test using BAM with tags and BAM without tags.

## `ont_basecall_dna.nf`

- [ ] Add Dorado model path preflight.
- [ ] Prove Dorado model resolution on target runtime.
- [ ] Add runtime skip/proof test.
- [ ] Consider shared basecall workflow logic with RNA.

## `ont_basecall_rna.nf`

- [ ] Force RNA Dorado model default through API/config/module path.
- [ ] Add model-selection unit test.
- [ ] Prove runtime model availability before claiming RNA support.
- [ ] Consider shared basecall workflow logic with DNA.

---

# Immediate execution order

1. **Fix `FastqPlasmidQC` arity / alignment-backed QC split.**
2. **Wire `FastqDimerQC` into FASTQ/plasmid/wf-clone paths.**
3. **Add static tests catching those two regressions forever.**
4. **Add tiny FASTQ/reference runtime proof for `ont_fastq_qc`.**
5. **Consolidate or differentiate `wf_clone_validation` and `ont_construct_screening`.**
6. **Fix RNA Dorado model selection.**
7. **Add modkit MM/ML tag validation.**

This order gets the core plasmid QC path back to spec fastest and avoids spending effort on RNA/methylation before the plasmid foundation is actually runnable.
