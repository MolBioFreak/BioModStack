# BioModStack Nextflow Workflow-by-Workflow Migration Strategy

> **For Hermes:** Use `biomodstack-nextflow-bounded-context-refactor`, `test-driven-development`, and `subagent-driven-development` to implement this plan one workflow tranche at a time.

**Goal:** Replace the fragile global `main.nf` switchboard with workflow-specific entrypoints, named workflows, subworkflows, modules, config/schema contracts, and per-workflow tests without breaking currently good BMS workflows.

**Architecture:** Each independently launchable parent workflow gets a finished migration tranche: contract tests, direct entrypoint, backend routing, static/Nextflow verification, result-ingestion preservation, and a clean rollback boundary. Shared engines such as Boltz2, RF3, Protenix, PPIFlow, FAMPNN, RFantibody, and Fold-CP are not migrated as single products; they are preserved as reusable modules/subworkflows used by multiple product workflows.

**Tech Stack:** Nextflow DSL2, BioModStack API routing in `platform/api/services/nextflow.py`, pytest routing/static tests, future `nf-test`/Nextflow smoke tests, `nextflow.config` profiles, eventual `conf/*.config` and `nextflow_schema.json`.

---

## Executive rule

Do not do a mass file shuffle. Each tranche must fully finish one launchable workflow/product boundary before moving to the next.

A tranche is not done until:

- the workflow no longer depends on `main.nf` for launch selection;
- fresh and resume API launches route to the workflow-specific entrypoint;
- `main.nf` has no branch/includes for that parent workflow;
- child jobs, if any, are either untouched and regression-tested or intentionally moved with their parent;
- result ingestion/output paths still match existing BMS expectations;
- static include/symbol/brace checks pass;
- targeted pytest passes;
- real Nextflow preview/smoke is run when the environment supports it;
- the commit is independently revertible.

## Current baseline from live repo

- `main.nf`: 1206 lines, 35 includes, 314 `params.*` references.
- API profile mapping lives in `platform/api/services/nextflow.py` around `model_mode_to_profile` and `workflow_entrypoints`.
- Already routed away from `main.nf`:
  - `nanopore_methylation -> ngs.nf`
  - Experimental parents:
    - `protein_local_redesign -> workflows/protein_local_redesign.nf`
    - `protein_cad_experimental -> workflows/protein_cad_experimental.nf`
    - `caliby_experimental -> workflows/caliby_experimental.nf`
    - `protein_hunter_experimental -> workflows/protein_hunter_experimental.nf`
    - `boltz_cp_experimental -> workflows/boltz_cp_experimental.nf`
    - `confornets_experimental -> workflows/confornets_experimental.nf`
- Still in `main.nf` and should be migrated by tranche:
  - `oligo_design`
  - `ppiflow_generator`
  - `bindcraft`
  - `structure_prediction` / `inverse_folding` / `stability_prediction` / `de_novo` family
  - antibody/nanobody denovo/refinement parents
  - core/default protein-design pipeline
  - child job control branches only after parent workflows are stable

## Commit-count reality

Christian's 15-20 commit estimate is a floor. If each workflow is actually finished and tested, expect:

- Minimum coarse-grained implementation commits: ~20-25.
- Safer granular commits: ~35-50.
- PR/tranche count: ~8-10 workflow/product tranches, each containing several commits.

The rest of this plan is ordered by workflow/product tranche.

---

# Tranche 0: Baseline harness and routing registry

**Order:** first, before any more workflow movement.

**Objective:** Create the shared safety harness so every later workflow migration follows the same test/route/smoke pattern.

**Why first:** Without this, every migration can silently break resume routes, result ingestion, or stale `main.nf` branches.

**Files:**

- Modify: `platform/api/services/nextflow.py`
- Create or modify: `platform/api/tests/test_nextflow_entrypoint_registry.py`
- Create: `scripts/nextflow_static_inventory.py` or `platform/api/tests/test_nextflow_static_contracts.py`
- Optional later: `docs/nextflow-migration-routing-inventory.md`

**Commit 0.1: Inventory current launch routes**

- Extract the inline `workflow_entrypoints` mapping into a small named helper or constant.
- Include effective profile, entrypoint path, and whether the workflow is migrated.
- Do not change behavior yet.

Expected shape:

```python
WORKFLOW_ENTRYPOINTS = {
    "nanopore_methylation": "ngs.nf",
    "protein_local_redesign": "workflows/protein_local_redesign.nf",
    "protein_cad_experimental": "workflows/protein_cad_experimental.nf",
    "caliby_experimental": "workflows/caliby_experimental.nf",
    "protein_hunter_experimental": "workflows/protein_hunter_experimental.nf",
    "boltz_cp_experimental": "workflows/boltz_cp_experimental.nf",
    "confornets_experimental": "workflows/confornets_experimental.nf",
}
```

**Commit 0.2: Add route-registry contract tests**

Tests must assert:

- every mapped entrypoint exists;
- fresh launch and resume launch both use the same entrypoint helper;
- fallback to `main.nf` is intentional, not accidental;
- no aggregate `experimental.nf` exists;
- NGS terms stay out of `main.nf`.

**Commit 0.3: Add static Nextflow contract test**

Add a Python test/helper that checks:

- all local `include { X } from './path'` targets resolve with `.nf` fallback;
- included symbols exist in targets;
- braces are balanced;
- migrated workflow files have unnamed `workflow {}` entrypoints;
- `main.nf` has no forbidden terms for already-migrated domains.

**Acceptance gate:**

- `uv run --directory platform/api python -m pytest tests/test_nextflow_entrypoint_registry.py tests/test_experimental_nextflow_entrypoint.py tests/test_nanopore_nextflow.py -q`
- static include/symbol scan passes;
- `git diff --check` clean;
- if `nextflow` exists, `nextflow -version` captured; otherwise blocker documented.

---

# Tranche 1: ONT/NGS platform foundation, not methylation-only

**Order:** first actual workflow tranche.

**Objective:** Establish NGS as a standalone ONT service family and workflow boundary that can grow beyond methylation into device-adjacent ONT runs, onboard CUDA Dorado basecalling, DNA/RNA workflows, plasmid/construct screening, methylation analysis, read QC, and multiple quality modes.

**Why first:** NGS is scientifically and operationally unrelated to protein design/folding. It needs its own service semantics, artifact contract, workflow registry, and hardware/runtime assumptions before individual workflows are promoted. Treat the current methylation pipeline as seed material, not the whole product.

**Current state:**

- API routes `nanopore_methylation -> ngs.nf`.
- `ngs.nf` is a tiny NGS-only wrapper.
- `workflows/ngs/nanopore_methylation.nf` contains named workflow `NANOPORE_METHYLATION` but no unnamed direct entrypoint.
- NGS process files already exist under `modules/ngs/` for Dorado basecall/align, BAM prep, modkit pileup/summary, FASTQ align, FASTQ plasmid QC, FASTQ dimer QC, and clone validation.
- Existing docs/reports describe broader ONT/plasmid/QC goals; do not mistake current methylation-era wiring for full ONT platform readiness.

**Files:**

- Modify/create: `workflows/ngs/*.nf`
- Modify/create: `subworkflows/ngs/*.nf`
- Modify: `modules/ngs/*.nf`
- Modify: `platform/api/services/nextflow.py`
- Modify/create: `platform/api/services/sequence_qc_manifest.py`
- Modify: `platform/api/routers/sequence_qc.py`
- Modify: `platform/api/tests/test_nanopore_nextflow.py`
- Modify: `platform/api/tests/test_sequence_qc_manifest.py`
- Optional keep/compat: `ngs.nf`

**Commit 1.1: Define the ONT/NGS workflow registry and artifact contract**

- Add or document canonical NGS workflow IDs before moving launch routes:
  - `ont_basecall_dna`
  - `ont_basecall_rna`
  - `ont_plasmid_qc`
  - `ont_construct_screening`
  - `ont_methylation_analysis`
  - `ont_fastq_qc`
  - future `ont_live_run` / device-control-adjacent mode if MinKNOW integration is implemented.
- Define a manifest-first contract (`qc_manifest.json`) for reads, references, alignments, per-base support, modified-base summaries, QC metrics, and unavailable/optional artifacts.
- Explicitly distinguish raw device/run orchestration from offline/batch Nextflow analysis.

**Commit 1.2: Split hardware/device control from Nextflow analysis**

- Treat MK1B/MK1D/MinKNOW device control as a service/API concern, not as a long-running Nextflow process unless there is a proven runtime need.
- Nextflow owns reproducible analysis stages: Dorado basecalling, alignment, modkit, plasmid/construct QC, read QC, reports.
- Add tests/docs proving the NGS workflow layer can accept existing FAST5/POD5/FASTQ/BAM inputs without requiring live device hardware.

**Commit 1.3: Create reusable NGS subworkflow layers**

Create or formalize subworkflows around existing modules:

- `subworkflows/ngs/ont_basecall.nf` for Dorado DNA/RNA/simplex/duplex/sup/hac/fast model selection.
- `subworkflows/ngs/ont_align.nf` for minimap2/Dorado align defaults, with runtime-safe preset choices.
- `subworkflows/ngs/modified_bases.nf` for modkit pileup/summary when input/basecalling supports it.
- `subworkflows/ngs/plasmid_construct_qc.nf` for plasmid screening, expected-construct comparison, per-base support, variant candidate summaries.
- `subworkflows/ngs/read_qc.nf` for read-length, Q-score, yield, N50, barcode/sample summaries.

**Commit 1.4: Promote methylation as one workflow under the NGS family**

- Add unnamed direct entrypoint to `workflows/ngs/nanopore_methylation.nf` or rename/copy to `workflows/ngs/ont_methylation_analysis.nf` with compatibility preserved.
- Route `nanopore_methylation` / `ont_methylation_analysis` directly to the workflow-specific file.
- Keep `ngs.nf` only as a tiny compatibility/family wrapper if useful; do not make it a new monolith.

**Commit 1.5: Add first non-methylation workflow: FASTQ/plasmid QC**

- Promote existing FASTQ/plasmid QC and clone-validation modules into a direct workflow such as `workflows/ngs/ont_plasmid_qc.nf` or `workflows/ngs/ont_fastq_construct_qc.nf`.
- Emit the same typed manifest contract as methylation workflows, with modified-base fields marked unavailable when not applicable.
- Do not claim Plasmidsaurus/Geneious parity unless per-base support, variant calls, consensus, maps/reports, and evidence tracks are truly generated.

**Commit 1.6: Add CUDA/runtime quality-mode contract**

- Encode quality modes as explicit launch params/config, not UI prose:
  - DNA vs RNA
  - simplex vs duplex when supported
  - fast/hac/sup model family
  - modified-base model selection
  - barcode/sample-sheet mode
  - onboard CUDA GPU selection and fallbacks.
- Add tests that stale frontend defaults cannot override backend-safe runtime defaults.

**Commit 1.7: NGS regression tests and minimal runtime smoke**

Tests assert:

- `main.nf` contains no NGS/Nanopore/Dorado/Modkit terms;
- each promoted NGS workflow has a direct entrypoint;
- API fresh/resume launch uses the workflow-specific NGS file;
- `ngs.nf`, if retained, is only compatibility and does not import `main.nf`;
- manifest APIs report old/missing artifacts truthfully instead of fabricating paths;
- modified-base unavailable state is not treated as workflow failure for FASTQ-only/plasmid QC inputs.

**Acceptance gate:**

- targeted pytest passes;
- static include/symbol scan passes;
- minimal valid FASTQ+reference/plasmid smoke emits `qc_manifest.json`, alignment artifacts when requested, and per-base support when appropriate;
- minimal valid POD5/FAST5-to-Dorado smoke only when device/data/runtime is actually available;
- no fake BAM/FASTQ/POD5 outputs as runtime proof;
- UI/API clearly separates device-run status, basecalling status, analysis status, and report readiness.

**Not in this tranche:**

- Full real-time MinKNOW replacement.
- Promising MK1B/MK1D live-control proof without actual hardware/API validation.
- Production UI parity claims for Plasmidsaurus/Geneious-class deliverables before typed artifacts and evidence tracks exist.

---

# Tranche 2: Fold-CP / Boltz-CP experimental workflow

**Order:** second.

**Objective:** Fully harden the Fold-CP/Boltz-CP experimental workflow as a workflow-specific entrypoint and isolate it from both `main.nf` and generic Boltz2 use cases.

**Why second:** It is already direct-routed, high strategic value, and should establish the pattern for workflows that use shared engines but have distinct orchestration semantics.

**Current state:**

- API routes `boltz_cp_experimental -> workflows/boltz_cp_experimental.nf`.
- Workflow file has unnamed entrypoint.
- Heavy logic lives in `modules/boltz_cp_experimental.nf` (~1533 lines).
- Separate API helpers exist in `platform/api/services/boltz_cp_shard_plans.py`.

**Files:**

- Modify: `workflows/boltz_cp_experimental.nf`
- Modify: `modules/boltz_cp_experimental.nf`
- Modify: `platform/api/tests/test_boltz_cp_experimental.py`
- Modify: `platform/api/tests/test_boltz_cp_experimental_workflow_contract.py`
- Possibly create: `subworkflows/fold_cp/` or `subworkflows/boltz_cp/`

**Commit 2.1: Lock Fold-CP launch/result contract**

- Tests assert entrypoint routing, expected params, shard-plan semantics, and output directory assumptions.
- Distinguish infra SIGTERM/cache/H2D from final/full result claims.

**Commit 2.2: Split workflow wrapper from data-plane orchestration**

- Keep `workflows/boltz_cp_experimental.nf` thin.
- Move multi-step orchestration from giant module into named workflow/subworkflow if it is not a simple process wrapper.

Suggested target:

```text
workflows/boltz_cp_experimental.nf
subworkflows/fold_cp/context_parallel_prediction.nf
modules/boltz_cp_experimental.nf  # only process/tool wrappers after cleanup
```

**Commit 2.3: Config/profile cleanup for Fold-CP**

- Move Fold-CP runtime defaults out of workflow logic when possible.
- Preserve `nextflow.config` profile `boltz_cp_experimental` until broader config split.

**Commit 2.4: Smoke/preview test path**

- Add or document a minimal parse/preview invocation.
- If no real Nextflow binary, mark smoke blocked and keep static/API tests green.

**Acceptance gate:**

- direct route preserved;
- no `main.nf` references;
- tests pass;
- module/subworkflow split reduces fragility without changing product semantics.

---

# Tranche 3: Oligo Designer workflow

**Order:** third.

**Objective:** Extract `oligo_design` from `main.nf` into its own finished workflow-specific entrypoint.

**Why third:** It is relatively bounded and already has `workflows/oligo_design.nf`.

**Current state:**

- `main.nf` include: `include { OLIGO_DESIGNER } from './workflows/oligo_design.nf'`.
- `main.nf` branch: `params.rfd_mode == 'oligo_design' || params.rfdpoly_enabled`.
- `workflows/oligo_design.nf` has named workflow only, no unnamed entrypoint.
- API maps `('oligo_design', 'oligo_design') -> 'oligo_design'`, but entrypoint currently falls back to `main.nf`.

**Files:**

- Modify: `workflows/oligo_design.nf`
- Modify: `main.nf`
- Modify: `platform/api/services/nextflow.py`
- Create: `platform/api/tests/test_oligo_nextflow_entrypoint.py`

**Commit 3.1: Add red tests for direct Oligo routing**

Tests should fail before implementation:

- API fresh/resume uses `workflows/oligo_design.nf`;
- `workflows/oligo_design.nf` has unnamed entry workflow;
- `main.nf` has no `oligo_design` branch after migration.

**Commit 3.2: Add Oligo entrypoint and route API**

- Add unnamed workflow wrapper around `OLIGO_DESIGNER()`.
- Add `"oligo_design": "workflows/oligo_design.nf"` to entrypoint map.

**Commit 3.3: Remove Oligo branch/include from `main.nf` and verify**

- Delete main include/branch.
- Ensure optional Boltz2 validation remains inside the Oligo workflow and does not make Oligo depend on the global structure-prediction branch.

**Acceptance gate:**

- targeted pytest passes;
- static include scan passes;
- no `oligo_design` branch in `main.nf`;
- if available, `nextflow run workflows/oligo_design.nf -preview -profile oligo_design,...` parses.

---

# Tranche 4: Standalone structure prediction workflow: Boltz2 / RF3 / Protenix

**Order:** fourth, before migrating workflows that depend on structure prediction as a validation stage.

**Objective:** Create a clean standalone structure prediction product boundary without breaking Boltz2/RF3/Protenix usage inside other workflows.

**Why now:** Boltz2 is used in several places. The fix is not one global Boltz2 workflow; it is a clean shared structure-prediction layer used by multiple parent workflows.

**Current state:**

- API maps:
  - `('boltz2', 'predict') -> boltz`
  - `('boltz2', 'complex') -> boltz`
  - `('rf3', 'predict') -> rf3`
  - `('protenix', 'predict') -> protenix`
  - `('protenix', 'complex') -> protenix`
  - `structure_prediction` mode resolves profile from `pred_method`.
- `main.nf` handles structure prediction branch around line 287+.
- `workflows/structure_prediction.nf` exists and has unnamed/named workflows.
- `modules/structure_prediction.nf` is large (~1278 lines).

**Files:**

- Modify: `workflows/structure_prediction.nf`
- Modify: `modules/structure_prediction.nf`
- Modify: `main.nf`
- Modify: `platform/api/services/nextflow.py`
- Create/modify: `platform/api/tests/test_structure_prediction_entrypoint.py`
- Modify result-ingester tests if output paths are touched.

**Commit 4.1: Characterize standalone vs embedded structure prediction**

- Tests must distinguish:
  - standalone user-launched structure prediction;
  - structure prediction stage inside protein design/Oligo/antibody/etc.
- No embedded workflow should accidentally route away or lose outputs.

**Commit 4.2: Route standalone structure prediction profiles directly**

Map profiles/modes that are standalone to:

```text
workflows/structure_prediction.nf
```

Likely profiles:

- `boltz`
- `rf3`
- `protenix`

But only when they are resolved from standalone model/mode launches, not when used as internal stages of another workflow.

**Commit 4.3: Remove standalone branch from `main.nf`**

- Remove only the branch that corresponds to standalone structure prediction.
- Preserve core protein-design prediction steps still inline in `main.nf` until Tranche 8.

**Commit 4.4: Start module/subworkflow cleanup**

- If `modules/structure_prediction.nf` remains too broad, create:

```text
subworkflows/structure/standalone_prediction.nf
modules/structure/boltz_from_sequence.nf
modules/structure/boltz_from_complex.nf
modules/structure/rf3_from_sequence.nf
modules/structure/protenix_predict.nf
```

Only split enough to stabilize the product boundary; deeper cleanup can follow.

**Acceptance gate:**

- Boltz2 standalone jobs still launch;
- RF3 standalone jobs still launch;
- Protenix standalone jobs still launch;
- embedded Boltz2 use in other workflows is unaffected;
- result ingestion still finds confidence/PAE/artifact outputs.

---

# Tranche 5: PPiFlow generator / nanobody backbone-refine workflow

**Order:** fifth.

**Objective:** Extract and finish the seeded PPIFlow generator workflow, while respecting that PPIFlow also appears inside antibody/nanobody maturation paths.

**Why here:** It is a parent launchable workflow but also touches the nanobody/antibody ecosystem, so it should come after the shared structure-prediction boundary is clearer.

**Current state:**

- API maps `('ppiflow', 'generator_backbone_refine') -> boltz`.
- `main.nf` include: `PPIFLOW_GENERATOR_DESIGN`.
- `main.nf` branch: `params.rfd_mode == 'ppiflow_generator'`.
- `workflows/ppiflow_generator_design.nf` exists, named workflow only.
- `modules/ppiflow.nf` is large (~686 lines).

**Files:**

- Modify: `workflows/ppiflow_generator_design.nf`
- Modify: `modules/ppiflow.nf`
- Modify: `main.nf`
- Modify: `platform/api/services/nextflow.py`
- Modify: `platform/api/services/result_ingester.py` tests if touched
- Create: `platform/api/tests/test_ppiflow_generator_entrypoint.py`

**Commit 5.1: Red tests for PPiFlow direct route and artifact contract**

- assert direct entrypoint;
- assert result family/stage semantics remain `ppiflow`;
- assert maturation-child result-ingestion fallback is not touched.

**Commit 5.2: Add entrypoint and route direct**

- Add unnamed workflow to `workflows/ppiflow_generator_design.nf`.
- Route `ppiflow_generator` or a dedicated profile directly.

**Commit 5.3: Remove PPiFlow parent branch from `main.nf`**

- Do not remove maturation child branch yet.

**Commit 5.4: Split reusable PPIFlow process logic if necessary**

- Keep parent entrypoint thin.
- Defer antibody maturation-specific cleanup to antibody tranche.

**Acceptance gate:**

- standalone PPIFlow generator no longer uses `main.nf`;
- antibody/nanobody maturation child paths unchanged;
- result ingester still recognizes PPIFlow outputs.

---

# Tranche 6: BindCraft parent workflow

**Order:** sixth.

**Objective:** Migrate BindCraft parent workflow away from `main.nf` without breaking spawned BindCraft child jobs.

**Why here:** Parent file already exists and has an unnamed entrypoint, but child orchestration still depends on API/job routing.

**Current state:**

- API maps:
  - `('bindcraft', 'minibinder') -> binder_denovo`
  - `('bindcraft', 'peptide') -> binder_denovo`
- `main.nf` parent branch: `params.rfd_mode == 'bindcraft'`.
- `main.nf` child branch: `params.rfd_mode == 'bindcraft_child'`.
- `workflows/bindcraft_design.nf` exists and has unnamed entrypoint.
- `scripts/spawn_bindcraft_children.py` creates child jobs with workflow/model concept `bindcraft_child`.

**Files:**

- Modify: `platform/api/services/nextflow.py`
- Modify: `main.nf`
- Modify: `workflows/bindcraft_design.nf` only if needed
- Modify/create: `platform/api/tests/test_bindcraft_entrypoint.py`
- Review: `scripts/spawn_bindcraft_children.py`

**Commit 6.1: Add BindCraft parent/child contract tests**

Tests must assert:

- parent `bindcraft` launches via `workflows/bindcraft_design.nf` after migration;
- child `bindcraft_child` routing remains stable;
- child spawn payload still creates expected mode/model params.

**Commit 6.2: Route BindCraft parent direct**

- Add `binder_denovo` / bindcraft parent mapping only if it can distinguish BindCraft from general binder core modes.
- If `binder_denovo` is also used by core protein design, do not blindly map all `binder_denovo` to BindCraft. Use model/mode-aware entrypoint resolution if needed.

**Commit 6.3: Remove BindCraft parent branch from `main.nf`**

- Keep `bindcraft_child` branch until child migration phase.

**Commit 6.4: Optional child direct-entrypoint stabilization**

- Only if tests prove safe, route child to `workflows/bindcraft_child.nf` or parent-owned child entrypoint.
- Otherwise leave child on `main.nf` with explicit technical debt note.

**Acceptance gate:**

- parent direct route complete;
- child jobs still spawn and are not silently redirected to wrong pipeline;
- no binder/core protein route collision.

---

# Tranche 7: Antibody / nanobody denovo-refinement mega-workflow

**Order:** seventh, after structure prediction, PPiFlow, and BindCraft boundaries are safer.

**Objective:** Migrate the huge antibody/nanobody parent workflow while preserving its child jobs, result lineage, and multi-stage model semantics.

**Why late:** This is the highest-risk existing workflow. It spans RFantibody, Boltz/BoltzGen, FAMPNN/ProteinMPNN/AntiFold, PPIFlow maturation, OpenMM, CDR annotation, and result ingestion.

**Current state:**

- `workflows/antibody_denovo.nf` is ~3288 lines, with 31 process definitions.
- API maps:
  - `('antibody_denovo', antibody_denovo_pipeline) -> boltz`
  - `('antibody_denovo', antibody_refinement_pipeline) -> boltz`
  - same for `template_antibody_denovo`.
- `main.nf` branch handles `antibody_denovo_pipeline` / `antibody_refinement_pipeline` around antibody toolkit block.
- Child scripts:
  - `scripts/spawn_fampnn_children.py`
  - `scripts/spawn_maturation_children.py`
- Result ingestion has antibody/nanobody-specific logic.

**Files:**

- Modify: `workflows/antibody_denovo.nf`
- Modify: `workflows/antibody_design.nf`
- Modify: `main.nf`
- Modify: `platform/api/services/nextflow.py`
- Modify/test: `platform/api/antibody_pipeline_contract.py`
- Modify/test: `platform/api/services/result_ingester.py`
- Review child files:
  - `workflows/antibody_child.nf`
  - `workflows/rfantibody_backbone.nf`
  - `workflows/fampnn_child.nf`
  - `workflows/maturation_child.nf`
  - `workflows/maturation_child_core.nf`

**Commit 7.1: Lock antibody/nanobody contract before movement**

Tests should cover:

- API launch route for parent denovo/refinement;
- child-spawn payload for FAMPNN and maturation;
- stage-family/result-ingester semantics;
- nanobody/VHH vs Fab/scFv detection remains unchanged.

**Commit 7.2: Route antibody parent direct**

- Route parent denovo/refinement profiles to `workflows/antibody_denovo.nf`.
- Do not route child jobs yet.

**Commit 7.3: Remove antibody parent branch from `main.nf`**

- Keep child branches until direct child routing is explicitly handled.

**Commit 7.4: Extract child routing from global `main.nf` only after parent is stable**

Candidate direct child entrypoints:

```text
workflows/antibody_child.nf
workflows/rfantibody_backbone.nf
workflows/fampnn_child.nf
workflows/maturation_child.nf
```

Each child gets its own red-green contract test.

**Commit 7.5: Begin internal decomposition of `antibody_denovo.nf`**

Possible target structure:

```text
subworkflows/antibody/backbone_generation.nf
subworkflows/antibody/sequence_design.nf
subworkflows/antibody/boltz_validation.nf
subworkflows/antibody/ppiflow_maturation.nf
subworkflows/antibody/final_analysis.nf
modules/antibody/*
```

Do not fully rewrite in the same commit as routing movement.

**Acceptance gate:**

- parent direct route works;
- child jobs still spawn;
- nanobody/Fab/scFv result semantics still work;
- result lineage not broken;
- no fake/demo artifact claims.

---

# Tranche 8: Core/default protein design workflow

**Order:** eighth, last major parent extraction.

**Objective:** Move the remaining legacy/default protein design pipeline out of `main.nf` into a dedicated `workflows/protein_design.nf`, then reduce `main.nf` to a thin wrapper.

**Why last:** This is the current core monolith and contains the most cross-stage control flow.

**Current state:**

`main.nf` still orchestrates:

- RFDiffusion
- RFD3
- RF3
- FAMPNN
- ProteinMPNN
- AF2
- Boltz2
- BoltzGen
- DiffDock
- UniDock
- metadata merge
- compression
- analysis
- publishing
- binder/monomer mode branching
- standalone BoltzGen paths

Known hazards:

- binder mode naming drift: short names in config/API vs long names in `main.nf` guards;
- BoltzGen standalone fallthrough risk;
- broad analysis/publish branches depending on shared channel variables;
- many global `params.*` references.

**Files:**

- Create: `workflows/protein_design.nf`
- Modify: `main.nf`
- Possibly create:
  - `subworkflows/protein/generation.nf`
  - `subworkflows/protein/sequence_design.nf`
  - `subworkflows/protein/structure_validation.nf`
  - `subworkflows/protein/docking.nf`
  - `subworkflows/protein/analysis_publish.nf`
- Modify tests:
  - `platform/api/tests/test_protein_design_entrypoint.py`
  - existing structure/BoltzGen tests

**Commit 8.1: Freeze current core protein-design behavior with tests**

- cover major modes:
  - monomer denovo
  - monomer foldcond
  - binder denovo
  - binder foldcond
  - binder motif scaffolding
  - binder partial diffusion
  - BoltzGen standalone if exposed
- explicitly test short/long binder mode names.

**Commit 8.2: Create `workflows/protein_design.nf` with minimal movement**

- Move the core `main.nf` body as-is as much as possible.
- Do not simultaneously refactor all internals.
- `main.nf` becomes a wrapper around `PROTEIN_DESIGN()` or a compatibility entrypoint.

**Commit 8.3: Route core protein profiles direct**

- Profiles:
  - `monomer_denovo`
  - `monomer_foldcond`
  - `monomer_motifscaff`
  - `monomer_partialdiff`
  - `binder_denovo`
  - `binder_foldcond`
  - `binder_motifscaff`
  - `binder_partialdiff`
- Ensure these do not collide with BindCraft parent routing.

**Commit 8.4: Fix known mode and fallthrough hazards with tests**

- binder mode canonicalization;
- BoltzGen standalone exit/fallthrough;
- skipped-channel initialization;
- analysis/publish broad branch guards.

**Commit 8.5+: Decompose into subworkflows**

Split only after routing is green:

```text
subworkflows/protein/generation.nf
subworkflows/protein/sequence_design.nf
subworkflows/protein/structure_prediction.nf
subworkflows/protein/docking.nf
subworkflows/protein/analysis_publish.nf
```

**Acceptance gate:**

- core protein-design products launch outside `main.nf`;
- `main.nf` is thin and readable;
- all previously migrated workflows still pass their route tests;
- real preview/smoke runs are recorded when possible.

---

# Tranche 9: Child-job de-globalization

**Order:** after parent migrations.

**Objective:** Remove remaining child-job branches from global `main.nf` and make children parent-owned implementation details or direct child entrypoints.

**Child workflows:**

- `antibody_child`
- `bindcraft_child`
- `rfantibody_backbone`
- `fampnn_child`
- `maturation_child`
- `maturation_child_core`

**Files:**

- Modify: `platform/api/services/nextflow.py`
- Modify: child workflow files under `workflows/`
- Modify: spawn scripts under `scripts/`
- Modify: result ingestion tests
- Modify: `main.nf`

**Commits:**

- one commit per child route, with red tests first;
- do not batch all child jobs together.

**Acceptance gate:**

- no child branch remains in `main.nf`;
- parent orchestrators still spawn children;
- child result ingestion still works;
- lineage/parent_job_id behavior preserved.

---

# Tranche 10: Config, schema, and nf-test hardening

**Order:** after routing is sane; may run incrementally per workflow but finish at end.

**Objective:** Move runtime/config complexity out of workflow scripts and make BMS launch contracts explicit.

**Files to create:**

```text
conf/base.config
conf/modules.config
conf/gpu.config
conf/ngs.config
conf/protein.config
conf/antibody.config
conf/test.config
nextflow_schema.json
```

**Commits:**

- schema for NGS/experimental/structure/core params;
- module args into `modules.config` via `task.ext.args` where appropriate;
- profile cleanup and profile de-duplication;
- initial `nf-test` or equivalent Nextflow workflow tests.

**Acceptance gate:**

- launch parameter validation exists;
- workflow files are thinner;
- tests cover schema/required params;
- config changes do not silently change existing runtime defaults.

---

# Final desired state

`main.nf` should either be:

```groovy
#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { PROTEIN_DESIGN } from './workflows/protein_design.nf'

workflow {
    PROTEIN_DESIGN()
}
```

or a compatibility wrapper with minimal routing and clear deprecation comments.

There should be no platform-wide `params.rfd_mode` switchboard in `main.nf`.

## Final workflow order summary

1. Tranche 0: baseline route registry/tests/static harness.
2. Tranche 1: NGS/ONT methylation direct workflow finish.
3. Tranche 2: Fold-CP / Boltz-CP experimental hardening.
4. Tranche 3: Oligo Designer extraction.
5. Tranche 4: Standalone structure prediction: Boltz2/RF3/Protenix.
6. Tranche 5: PPiFlow generator / nanobody backbone-refine.
7. Tranche 6: BindCraft parent.
8. Tranche 7: Antibody/nanobody denovo-refinement mega-workflow.
9. Tranche 8: Core/default protein-design pipeline.
10. Tranche 9: Child-job de-globalization.
11. Tranche 10: config/schema/nf-test hardening.

## Repeated per-workflow implementation template

For every tranche:

1. Write failing route/static tests.
2. Add or verify unnamed workflow-specific entrypoint.
3. Route fresh/resume API launches to exact file.
4. Remove parent include/branch from `main.nf`.
5. Preserve child/internal paths unless this tranche explicitly owns them.
6. Preserve result-ingester and artifact layout.
7. Run targeted pytest.
8. Run static include/symbol/brace scan.
9. Run Nextflow preview/smoke when available.
10. Commit only that workflow's intended files.

This is the lather-rinse-repeat pattern. Do not skip it for the huge workflows.
