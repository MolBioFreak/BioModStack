# PPIFlow Dual-Stage Revision Spec

Date: 2026-03-21

## Scope

This spec defines an end-to-end revision set for making PPIFlow selectable in two distinct places in the antibody/nanobody workflow:

1. `post_rfantibody`
2. `post_fampnn`

The design goal is to keep the current data-viewer re-orchestration model, but make PPIFlow stage placement explicit, make partial flow loop-selective, and make lineage/provenance first-class so every downstream design can be traced back to its exact origin.

This is a workflow and control-plane revision, not only a UI change.

## Why This Revision Exists

Current behavior is overloaded:

- The main workflow only runs the primary PPIFlow path on the FA-MPNN branch in [workflows/antibody_denovo.nf](../workflows/antibody_denovo.nf).
- The UI exposes PPIFlow as a single concept even though there are already two logically different uses:
  - pre-validation maturation on FA-MPNN outputs
  - post-validation repair via `run_post_validation_maturation`
- Partial flow is not truly loop-selective today. The redesign constraints can be loop-selective, but the actual PPIFlow `--cdr_position` input is still derived from all detected CDRs in [scripts/identify_anchors.py](../scripts/identify_anchors.py) and passed through [modules/ppiflow.nf](../modules/ppiflow.nf).
- Provenance is too weak. `Job.parent_job_id`, `batch_id`, `child_stage`, and `Design.backbone_id` are useful but not enough to reconstruct exact design ancestry across RFantibody -> FA-MPNN -> PPIFlow -> validation.

## High-Level Product Decision

PPIFlow must become an explicit two-mode stage:

1. `PPIFlow Backbone Refinement`
   - Runs after RFantibody review/screening
   - Input: selected RF backbone complexes
   - Purpose: refine local binder geometry and interface pose before sequence design

2. `PPIFlow Maturation`
   - Runs after FA-MPNN review/selection
   - Input: selected FA-MPNN sequence-designed complexes
   - Purpose: local backbone/interface refinement and optional redesign on a sequence-conditioned complex

Both modes must support:

- selectable CDR loop scope
- persisted provenance linking each child output to its exact source design
- identical re-orchestration from the Results Viewer dataset/review selection flow

The existing post-validation repair path should remain, but should be treated as a third, explicit repair mode rather than the main meaning of "PPIFlow".

## Theory / Model Positioning

### Post-RFantibody PPIFlow

This should be the preferred primary placement when the goal is backbone cleanup.

Reasoning:

- PPIFlow is a local redesign/refinement model that takes fixed positions, CDR positions, hotspots, and `start_t` to preserve and locally move an existing pose.
- RFantibody provides the backbone proposal but not final sequence-conditioned sidechain context.
- If the question is "can I improve local loop pose and interface geometry before sequence design?", PPIFlow belongs here.

Operational consequence:

- This mode should run only on reviewed RF families/backbones, not blindly on all raw RF outputs.
- It should produce refined backbones that then feed FA-MPNN.

### Post-FA-MPNN PPIFlow

This should remain available, but as maturation rather than the default meaning of PPIFlow.

Reasoning:

- FA-MPNN explicitly models sequence identity and sidechain conformation.
- Running PPIFlow after FA-MPNN lets the user refine a concrete sequence-conditioned complex.
- This is useful for late-stage local pose repair, interface tightening, and sequence-aware maturation.

Operational consequence:

- This mode is best used on shortlisted sequence designs, not as the only PPIFlow placement.

### Recommended Final Order

The preferred full order should become:

1. RFantibody
2. RF review / screening / family selection
3. optional `PPIFlow Backbone Refinement`
4. FA-MPNN sequence library
5. FA-MPNN review / filtering / selection
6. optional `PPIFlow Maturation`
7. validation
8. optional `PPIFlow Repair` after validation

## Target UX

### Launcher

Add two separate PPIFlow controls in the antibody launcher:

- `Run PPIFlow After RF Backbones`
- `Run PPIFlow After FA-MPNN`

Both controls must expose:

- enable/disable toggle
- selected loop scope
- PPIFlow sampling controls (`start_t`, `samples_per_target`, retry/config/checkpoint)
- anchor-selection controls
- post-partial-flow redesign controls
- filter controls, defaulting to off

The launcher must not map one toggle to both pre-sequence and post-validation behavior anymore.

### Results Viewer Re-orchestration

The viewer already has the right selection model: choose a source set, choose rows, relaunch.

Extend the iteration actions so the user can explicitly launch:

- `ppiflow_backbone_refine`
- `ppiflow_maturation`

The viewer must pass loop scope and source stage context.

### Lineage Presentation

Lineage should be grouped by stage family, not child job instance.

For a paused or resumed lineage root, the viewer should render groups like:

- `RFantibody Review`
- `PPIFlow Backbone Refinement`
- `FA-MPNN`
- `PPIFlow Maturation`
- `Validation`

Each group should show:

- number of child jobs
- total outputs
- selected loop scope
- stage mode
- link/drilldown to individual child jobs if needed

## Functional Requirements

### R1. Dual PPIFlow Stage Selection

The workflow must allow either or both:

- PPIFlow after RF review
- PPIFlow after FA-MPNN review

These are independent flags. Enabling one must not implicitly enable the other.

### R2. Loop-Selective Partial Flow

Both PPIFlow modes must support selectable loop sets:

- `H1`
- `H2`
- `H3`
- `L1`
- `L2`
- `L3`

Loop selection must affect:

- the PPIFlow `--cdr_position` input
- redesign constraints used after partial flow
- persisted provenance on the output design rows

It is not sufficient to make redesign selective while leaving partial flow itself broad.

### R3. Provenance / Traceability

Every design must be traceable from origin to current stage.

For every derived design, the platform must answer:

- which job produced it
- which single parent design it came from
- which original RF backbone it ultimately came from
- which stage transformed it
- which loops were active
- which parameter subset was used

This must be DB-backed, not inferred from filenames.

### R4. Stage-Appropriate Input Types

- `post_rfantibody` PPIFlow takes RF backbone complexes
- `post_fampnn` PPIFlow takes FA-MPNN-designed complexes
- `post_validation` PPIFlow takes validator-produced structures

The stage mode must be explicit on both `Job` and `Design`.

### R5. Filters Must Be Manual

Manual downstream filters should default off.

This includes:

- FA-MPNN PSCE filter
- PPIFlow maturation threshold/percentile filter
- ThermoMPNN threshold filter
- post-validation RMSD/iPTM filter

The user may opt in per run or per re-orchestrated action.

## Data Model Revision

## Job Model Additions

Modify [platform/api/database.py](../platform/api/database.py) `Job` with:

- `lineage_root_job_id: String(36), nullable=True, index=True`
  - stable root of the experiment lineage
- `stage_family: String(64), nullable=True, index=True`
  - examples: `rfantibody`, `ppiflow_backbone`, `fampnn`, `ppiflow_maturation`, `validation`, `ppiflow_repair`
- `stage_mode: String(64), nullable=True`
  - examples: `backbone_refine`, `maturation`, `repair`
- `upstream_stage_family: String(64), nullable=True`
- `selection_source_type: String(64), nullable=True`
  - examples: `rf_review_raw`, `rf_review_screened`, `saved_dataset`, `lineage_child_outputs`
- `selection_source_job_id: String(36), nullable=True`
- `selection_dataset_name: String(255), nullable=True`
- `selected_loop_scope: JSON, nullable=True`
  - canonical ordered list like `["H2","H3"]`

These should not replace existing `parent_job_id`, `batch_id`, or `child_stage`; they complement them.

## Design Model Additions

Modify [platform/api/database.py](../platform/api/database.py) `Design` with:

- `lineage_root_job_id: String(36), nullable=True, index=True`
- `parent_design_id: String(36), nullable=True, index=True`
- `origin_design_id: String(36), nullable=True, index=True`
  - stable root-most design row
- `origin_job_id: String(36), nullable=True, index=True`
- `origin_backbone_design_id: String(36), nullable=True, index=True`
  - the RF backbone design row this lineage ultimately descends from
- `stage_family: String(64), nullable=True, index=True`
- `stage_mode: String(64), nullable=True`
- `selected_loop_scope: JSON, nullable=True`
- `provenance: JSON, nullable=True`

`provenance` should persist stage-specific metadata that does not belong as individual columns.

Examples:

- for FA-MPNN:
  - source sequence
  - designed sequence
  - chain sequences
  - mutation list
  - PSCE summaries
- for PPIFlow:
  - parent structure path/name
  - anchor residues
  - fixed positions
  - cdr positions used
  - interface score original
  - interface score refined
  - delta interface score
  - RMSD
  - sequence identity
  - clash count
  - `start_t`
  - `samples_per_target`

## Optional Lineage Edge Table

If the team wants a more future-proof representation, add:

- `design_lineage_edges`
  - `id`
  - `parent_design_id`
  - `child_design_id`
  - `source_job_id`
  - `target_job_id`
  - `transition_type`
  - `stage_family`
  - `metadata`

This is optional for the first implementation if `parent_design_id` plus cached root columns is sufficient.

## API / Router Revision

### Iteration Actions

Extend [platform/api/routers/jobs.py](../platform/api/routers/jobs.py) iteration action map:

- add `ppiflow_backbone_refine`
- keep `ppiflow_maturation`
- keep validation actions

`ppiflow_backbone_refine` should:

- skip RF generation
- use `rfantibody_input_pdbs` from the selected review set
- disable FA-MPNN for the first pass if the user is running backbone-only refinement
- set `run_ppiflow_backbone_refine=true`
- set `run_maturation=false`
- set `interactive_gate_stage=post_ppiflow_backbone`

`ppiflow_maturation` should:

- use selected FA-MPNN outputs
- set `run_ppiflow_maturation=true`
- set `run_maturation=true`
- not implicitly enable post-validation repair

### Job Normalization

Update [platform/api/routers/jobs.py](../platform/api/routers/jobs.py) normalization logic to persist:

- `lineage_root_job_id`
- stage family/mode
- loop scope
- source selection context

These should be actual job columns, not only `params`.

## Frontend Revision

### Launcher Changes

Modify:

- [platform/frontend/src/components/AntibodyDenovoTemplate.tsx](../platform/frontend/src/components/AntibodyDenovoTemplate.tsx)
- [platform/frontend/src/components/QualitySettingsPanel.tsx](../platform/frontend/src/components/QualitySettingsPanel.tsx)

Add separate controls:

- `run_ppiflow_backbone_refine`
- `run_ppiflow_maturation`
- `ppiflow_loop_scope`
- `ppiflow_backbone_refine_loop_scope`
- `ppiflow_maturation_loop_scope`

Recommended behavior:

- if only one stage is enabled, reuse the shared PPIFlow settings block
- if both are enabled, show stage-specific loop scope and stage-specific filters, while keeping shared model runtime controls together

### Results Viewer Changes

Modify:

- [platform/frontend/src/components/ResultsViewer.tsx](../platform/frontend/src/components/ResultsViewer.tsx)
- [platform/frontend/src/lib/api.ts](../platform/frontend/src/lib/api.ts)

Changes:

- add iteration action `ppiflow_backbone_refine`
- keep `ppiflow_maturation`
- allow per-action loop selection from the viewer
- lineage grouping by stage family instead of flat child-job list
- show source-to-output chain:
  - `RF Backbone 144 -> PPIFlow Backbone Refine -> FA-MPNN -> PPIFlow Maturation`

## Workflow Revision

### New Stages

Modify [workflows/antibody_denovo.nf](../workflows/antibody_denovo.nf) to support:

1. `post_rfantibody` gate
2. optional `PPIFlow Backbone Refinement`
3. `post_ppiflow_backbone` gate
4. FA-MPNN stage
5. `post_fampnn` gate
6. optional `PPIFlow Maturation`

Required new parameters:

- `run_ppiflow_backbone_refine`
- `run_ppiflow_maturation`
- `ppiflow_backbone_loop_scope`
- `ppiflow_maturation_loop_scope`
- `interactive_gate_stage=post_ppiflow_backbone` support

### Stage Wiring

Current main path in [workflows/antibody_denovo.nf](../workflows/antibody_denovo.nf) runs PPIFlow only after FA-MPNN.

Revise to:

- if `run_ppiflow_backbone_refine=true`, run PPIFlow on the RF-reviewed structures before FA-MPNN
- feed FA-MPNN from refined backbones when present, otherwise from RF backbones
- keep `run_ppiflow_maturation=true` as a second optional branch after FA-MPNN

### Separate Stage Families

Use distinct stage names throughout:

- `ppiflow_backbone`
- `ppiflow_maturation`
- `ppiflow_post_validation`

Do not overload all of them into `maturation`.

This affects:

- `current_stage`
- `completed_stages`
- `child_stage`
- stage reporting
- queue display

## PPIFlow Module Revision

Modify [modules/ppiflow.nf](../modules/ppiflow.nf) and [workflows/maturation_child_core.nf](../workflows/maturation_child_core.nf).

### Required Changes

1. Support stage mode on input metadata
   - backbone refine vs maturation vs repair

2. Support explicit loop scope
   - convert selected loops into actual `cdr_position` spans before calling the PPIFlow entrypoint

3. Persist stage artifacts
   - publish:
     - anchors JSON
     - interface baseline JSON
     - cdr position text/JSON
     - partial-flow score JSON
     - maturation score JSON
     - filter JSON

4. Split score/filter behavior by stage
   - `backbone_refine` can use a permissive pass-through default
   - `maturation` can support optional filtering
   - `repair` can keep its existing validator-focused semantics

### Loop Scope Implementation

Modify [scripts/identify_anchors.py](../scripts/identify_anchors.py):

- accept optional `--selected_loops`
- when present, generate `cdr_positions.txt` from only those loops
- if HLT labels / ANARCII loop identities are available, use those
- fallback to the current full default only when no loop selection was provided

Modify [scripts/prep_antibody_constraints.py](../scripts/prep_antibody_constraints.py):

- no fundamental redesign is needed; it already supports per-loop override structures
- ensure the same loop scope is passed into redesign preparation for PPIFlow-derived structures

## Ingestion Revision

Modify [platform/api/services/result_ingester.py](../platform/api/services/result_ingester.py).

### FA-MPNN

Persist from the existing JSONs produced by [scripts/analyse_fampnn.py](../scripts/analyse_fampnn.py):

- designed sequence
- chain-wise sequence map
- `fampnn_avg_psce`
- `fampnn_max_residue_psce`
- `fampnn_min_residue_psce`
- `chain_avg_psce`

Also derive and persist:

- binder-chain sequence
- CDR sequences
- mutation list versus parent design

### PPIFlow

Persist from PPIFlow artifacts:

- parent design reference
- anchor payload
- interface score original
- interface score refined
- delta interface score
- RMSD
- sequence identity
- clash count
- selected loop scope
- fixed positions
- `start_t`
- `samples_per_target`

If any of these artifacts are missing from the published results directory, change publish behavior so the final result directory contains them.

### Provenance Assignment

When creating downstream designs, always set:

- `parent_design_id`
- `origin_design_id`
- `origin_job_id`
- `origin_backbone_design_id`
- `lineage_root_job_id`
- `stage_family`
- `stage_mode`
- `selected_loop_scope`

## Stage Output / Artifact Policy

For every stage family, the published result directory must contain:

### FA-MPNN

- final PDB
- FA-MPNN JSON
- optional mutation summary JSON

### PPIFlow Backbone Refinement

- refined PDB
- anchor JSON
- interface baseline JSON
- partial-flow score JSON
- filter JSON

### PPIFlow Maturation

- matured/refined PDB
- redesign JSON
- partial-flow score JSON
- maturation score JSON
- filter JSON

The ingester should never need to scrape transient work directories for normal operation.

## Database Migration

Modify [platform/api/database.py](../platform/api/database.py) `_ensure_schema()` support for new nullable columns.

Backfill strategy:

1. For new jobs, write full provenance at creation/ingestion time.
2. For old jobs, backfill only what can be inferred from:
   - `iteration_source_job_id`
   - `iteration_source_root_job_id`
   - filenames
   - batch parent-child structure

Do not block rollout on perfect retroactive lineage.

## Backward Compatibility

Keep existing params as aliases during transition:

- `run_maturation` maps to `run_ppiflow_maturation`
- `run_post_validation_maturation` stays as explicit repair

But the frontend should stop presenting a single overloaded "PPIFlow" switch.

## Review / Testing Checklist

### Unit / Integration

- launcher submits correct stage-specific params
- results viewer can relaunch both `ppiflow_backbone_refine` and `ppiflow_maturation`
- loop scope propagates into actual `--cdr_position`
- lineage root and parent design IDs are persisted
- FA-MPNN metrics ingest correctly
- PPIFlow score/provenance ingest correctly

### Workflow

1. RF review -> PPIFlow backbone refine -> FA-MPNN
2. RF review -> FA-MPNN -> PPIFlow maturation
3. RF review -> PPIFlow backbone refine -> FA-MPNN -> PPIFlow maturation
4. validation -> post-validation PPIFlow repair

### UI

- stage-family grouped lineage display
- design details show exact source design and source stage
- loop scope visible in lineage cards and detail pane

## Implementation Order

Recommended order:

1. schema additions for lineage/provenance
2. launcher + jobs router action split
3. workflow split for `ppiflow_backbone_refine` vs `ppiflow_maturation`
4. loop-selective partial-flow implementation
5. ingest/publish provenance expansion
6. results-viewer lineage grouping and per-stage action UI

## Explicit File Touch List

Backend / schema:

- [platform/api/database.py](../platform/api/database.py)
- [platform/api/routers/jobs.py](../platform/api/routers/jobs.py)
- [platform/api/services/result_ingester.py](../platform/api/services/result_ingester.py)

Workflow / modules:

- [workflows/antibody_denovo.nf](../workflows/antibody_denovo.nf)
- [workflows/maturation_child_core.nf](../workflows/maturation_child_core.nf)
- [modules/ppiflow.nf](../modules/ppiflow.nf)

Scripts:

- [scripts/identify_anchors.py](../scripts/identify_anchors.py)
- [scripts/prep_antibody_constraints.py](../scripts/prep_antibody_constraints.py)
- [scripts/collect_maturation_outputs.py](../scripts/collect_maturation_outputs.py)

Frontend:

- [platform/frontend/src/components/AntibodyDenovoTemplate.tsx](../platform/frontend/src/components/AntibodyDenovoTemplate.tsx)
- [platform/frontend/src/components/QualitySettingsPanel.tsx](../platform/frontend/src/components/QualitySettingsPanel.tsx)
- [platform/frontend/src/components/ResultsViewer.tsx](../platform/frontend/src/components/ResultsViewer.tsx)
- [platform/frontend/src/lib/api.ts](../platform/frontend/src/lib/api.ts)

## Review Tags

- `[P1][Revision]` Split PPIFlow into stage-explicit modes.
- `[P1][Revision]` Make loop-selective partial flow real, not only redesign-selective.
- `[P1][Revision]` Add DB-backed parent/origin lineage for designs.
- `[P1][Revision]` Persist FA-MPNN and PPIFlow provenance as first-class stage outputs.
