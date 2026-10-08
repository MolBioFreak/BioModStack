# De Novo Nanobody Toolkit: Remaining Work Commit Plan

Date: 2026-04-13

## Goal

Finish parity between the original RFantibody-first de novo workflow and the newer multi-generator toolkit, with BoltzGen as a first-class initial generator that can:

- run as a generator-only batch
- pause for manual filtering in interactive mode
- save/reload shortlists
- reopen selected designs into Antibody Refinement with correct artifact semantics
- present the same operational clarity in dashboard/results/review UX as the older RFA path

This plan intentionally groups the remaining work into a small number of commits with low overlap, so we do not create hidden regressions across review, resume, runtime, and viewer behavior.

## Commit 1

### Title

`antibody: finalize post_boltzgen review and resume semantics`

### Purpose

Make `post_boltzgen` a real first-class review stage everywhere the system currently assumes only:

- `post_rfantibody`
- `post_fampnn`
- `post_structure_validation`

This commit is the main backend correctness pass.

### Files

- `platform/api/routers/jobs.py`
- `platform/api/services/stage_review.py`
- `platform/api/routers/designs.py`
- `platform/api/antibody_pipeline_contract.py`
- `platform/frontend/src/constants/displayNames.ts`
- `platform/frontend/src/components/Dashboard.tsx`
- `platform/api/tests/test_review_payload_and_fampnn_ingest.py`
- `platform/api/tests/test_resume_identity.py`

### Changes

1. Extend review-stage normalization to accept `post_boltzgen`.
2. Treat `post_boltzgen` as a `boltzgen` review family that produces `sequence_designed_complex`.
3. Update `resume_job()` so paused `post_boltzgen` jobs populate:
   - `selected_input_dir`
   - `selected_input_manifest`
   - `selected_input_artifact_class=sequence_designed_complex`
   - `fampnn_collected_pdbs`
   - `interactive_gate_continue=true`
4. Map `post_boltzgen` to `from_stage="fampnn"` for resume hints, because the next downstream semantics are sequence-conditioned refinement, not backbone generation.
5. Ensure `refresh_gate_payload()` prefers filtered BoltzGen candidates over raw candidates, exactly like the FAMPNN gate prefers filtered candidates.
6. Ensure `ensure_stage_review_rows()` writes BoltzGen review rows with:
   - `stage_family=boltzgen`
   - `stage_mode=post_boltzgen`
   - BoltzGen confidence/interface metrics when present
7. Update dashboard resume labelling so the user sees `BoltzGen Review`, not `auto`.

### Acceptance

- A paused BoltzGen interactive job shows `awaiting_stage=post_boltzgen`.
- Review rows materialize in `/api/designs` for the paused parent.
- Resume from the paused BoltzGen gate launches a refinement-compatible follow-on job without manual param surgery.
- Saved review datasets survive the gate payload refresh.

### Do Not Mix In

- runtime/container fixes
- major Results Viewer analytics work
- clustering

## Commit 2

### Title

`boltzgen: harden runtime and publish stable gate inputs`

### Purpose

Make the standalone BoltzGen workflow operationally reliable so the new interactive gate is not built on fragile temp/workdir assumptions.

### Files

- `workflows/boltzgen_design.nf`
- `modules/boltzgen.nf`
- `apptainer/boltzgen.def`
- `apptainer/pyrosetta_tools.def`
- optional: `scripts/run_boltzgen_wrapper.py`

### Changes

1. Fix the micromamba environment bootstrap issue by explicitly exporting `MAMBA_ROOT_PREFIX=/opt/conda` before `micromamba activate pyrosetta`, matching the working pattern already used in:
   - `modules/af2.nf`
   - `modules/boltz.nf`
   - `modules/antibody_batch.nf`
2. Initialize the BoltzGen workflow params that currently emit undefined warnings:
   - `interactive_swa`
   - `interactive_gating`
   - `interactive_gate_stage`
   - `interactive_gate_continue`
   - `boltzgen_protocol`
   - `boltzgen_nanobody_framework`
   - `boltzgen_secondary_structure`
   - `boltzgen_covalent_bonds`
   - `boltzgen_cdr_h1_length`
   - `boltzgen_cdr_h2_length`
   - `boltzgen_cdr_h3_length`
   - `boltzgen_nanobody_scaffold_specs`
3. Keep raw and filtered BoltzGen outputs in stable published directories:
   - `collected/boltzgen_raw`
   - `collected/boltzgen_filtered`
4. Ensure confidence and affinity sidecars are preserved alongside the structures used for review.
5. Ensure stage reporting for `boltzgen` happens after the candidate cohort is fully materialized.
6. Confirm the workflow can reach the gate with the same output layout in:
   - single-run mode
   - orchestrator child-job mode

### Acceptance

- `nextflow run workflows/boltzgen_design.nf ...` no longer dies in `PrepBoltzGenInput` due to `MAMBA_ROOT_PREFIX`.
- Interactive BoltzGen runs emit a `gate_post_boltzgen.json` under the stable output dir.
- The gate payload points to published directories, not ephemeral work paths.

### Do Not Mix In

- Results Viewer behavior
- shortlist UX

## Commit 3

### Title

`frontend: complete BoltzGen review parity across launch and dashboard`

### Purpose

Make the user-facing workflow consistent. The backend should not understand `post_boltzgen` if the UI still behaves like only RFA and FAMPNN exist.

### Files

- `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`
- `platform/frontend/src/components/Dashboard.tsx`
- `platform/frontend/src/components/ResultsViewer.tsx`
- `platform/frontend/src/components/designOutputSource.ts`
- `platform/frontend/src/constants/displayNames.ts`
- `platform/frontend/src/lib/api.ts`

### Changes

1. Preserve the current de novo shell, but show the correct BoltzGen review gate language:
   - `After BoltzGen`
   - `BoltzGen Review`
2. Keep default execution mode as `Interactive` for the de novo toolkit.
3. Ensure saved/restored target paths are valid launch inputs without re-upload.
4. Ensure the FAMPNN chunking control is wired to `pdbs_per_job`.
5. Update dashboard resume controls to recognize `post_boltzgen`.
6. Ensure Results Viewer source inference prefers:
   - `post_boltzgen -> boltzgen`
   - not `validation`
   - not `fampnn`
7. Ensure paused BoltzGen review jobs expose the same “working set” affordances as paused RFA/FAMPNN jobs.

### Acceptance

- BoltzGen interactive jobs show the correct stage labels in the dashboard and results viewer.
- Resume/settings flows no longer fall back to `auto` for BoltzGen review jobs.
- Loading a saved template or restored launch state does not force a new target upload.

### Do Not Mix In

- clustering
- server-side shortlist logic beyond what is required for the working-set panel

## Commit 4

### Title

`results: add first-class shortlist workflow for generator cohorts`

### Purpose

Turn the paused/completed generator cohort into a usable review surface instead of just a table.

### Files

- `platform/frontend/src/components/ResultsViewer.tsx`
- `platform/api/routers/jobs.py`
- `platform/frontend/src/lib/api.ts`
- `platform/api/tests/test_review_payload_and_fampnn_ingest.py`

### Changes

1. Treat BoltzGen generator cohorts exactly like other reviewable working sets for:
   - `Add Visible Rows`
   - `Select All Filtered`
   - `Select Top N`
   - `Save Dataset`
   - `Load Saved Dataset`
2. Add a direct `Open Antibody Refinement` path from:
   - paused `post_boltzgen` jobs
   - completed BoltzGen generator jobs
   - saved shortlist datasets on those jobs
3. Ensure saved shortlist datasets persist in `saved_selection_sets` and replay correctly even after refresh.
4. Ensure the refinement handoff always uses:
   - `selected_input_artifact_class=sequence_designed_complex`
   - `selected_input_manifest`
   - `source_stage_family=boltzgen`
   - `source_stage_mode=post_boltzgen` or `nanobody_binder` as appropriate

### Acceptance

- A completed or paused BoltzGen cohort can be shortlisted and reopened into Antibody Refinement without manual path entry.
- The shortlist survives reload.
- The launched refinement job gets the correct artifact-class semantics.

### Do Not Mix In

- binder clustering internals
- DB migrations unless absolutely required

## Commit 5

### Title

`analytics: add BoltzGen-first cohort dedup and clustering`

### Purpose

Make the BoltzGen review loop practical at scale. The missing piece after shortlist persistence is fast triage of redundant binders.

### Files

- `platform/api/routers/designs.py`
- `platform/api/services/` new clustering helper if needed
- `platform/frontend/src/components/ResultsViewer.tsx`
- optional DB touch:
  - `platform/api/database.py`
  - `platform/api/migrations/...`

### Changes

1. Add binder-sequence-aware dedup/clustering for selected or filtered cohorts.
2. Start with lightweight clustering:
   - exact sequence dedup
   - sequence identity bins
   - optional CDR-H3-only grouping for VHH-like binders
3. Show group-level summaries in Results Viewer:
   - unique sequences
   - duplicate count
   - cluster size
   - representative design
4. Add BoltzGen overview cards that matter for initial triage:
   - confidence
   - ipTM / complex interface metrics
   - affinity score
   - binder probability
   - binder length
5. If performance requires it, add cached binder-sequence metadata to `Design` in a separate migration. Do not do that unless the client-side or on-demand server-side approach proves too slow.

### Acceptance

- Large BoltzGen cohorts can be reduced by exact-sequence or near-duplicate clustering before refinement.
- Overview cards and tables remain source-correct for BoltzGen.
- The representative chosen from each cluster can be sent directly into refinement.

### Do Not Mix In

- new model integrations
- PXDesign

## Execution Order

1. Commit 1
2. Commit 2
3. Commit 3
4. Commit 4
5. Commit 5

This order matters:

- Commit 1 makes the state model correct.
- Commit 2 makes the runtime produce reliable inputs for that model.
- Commit 3 makes the UI stop lying about that model.
- Commit 4 turns the model into an actual workflow.
- Commit 5 makes the workflow usable at scale.

## Definition Of Done

The de novo toolkit is functionally at parity when:

- RFantibody and BoltzGen can both launch from the same de novo shell
- both can run in interactive mode and pause into a stable review surface
- both support shortlist save/load and direct reopen into Antibody Refinement
- the resumed refinement path uses artifact semantics, not legacy source-name heuristics
- results, dashboard, and review UX all identify BoltzGen cohorts correctly
- BoltzGen batches can be deduplicated or clustered before refinement

