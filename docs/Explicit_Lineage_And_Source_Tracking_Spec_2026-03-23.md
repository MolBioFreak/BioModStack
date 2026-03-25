# Explicit Lineage And Source Tracking Spec

Date: 2026-03-23

## Scope

This spec defines the end-to-end revision set needed to make source pathing and parental lineage explicit across:

1. RFantibody review outputs
2. PPIFlow backbone refinement outputs
3. FA-MPNN sequence-design outputs
4. validation outputs
5. any later maturation / repair stages

The goal is simple:

- every downstream job should say exactly what stage it came from
- every downstream design should say exactly which design it came from
- every launch should preserve the exact selected source structures, not just an approximate job relationship
- the viewer should expose this without forcing the user to infer lineage from child job names or raw filenames

This is a control-plane, data-model, ingestion, and viewer revision. It is not just a UI rename pass.

## Why This Revision Exists

Current lineage is partially correct but semantically muddy.

What already works:

- selection manifests preserve `design_id`, `design_name`, `design_job_id`, `source_pdb_path`, and `selection_pdb_path`
- `Design` rows already have:
  - `lineage_root_job_id`
  - `parent_design_id`
  - `origin_design_id`
  - `origin_backbone_design_id`
  - `stage_family`
  - `stage_mode`
  - `selected_loop_scope`
  - `provenance`
- `Job` rows already have:
  - `lineage_root_job_id`
  - `selection_source_type`
  - `selection_source_job_id`
  - `selection_dataset_name`
  - `selected_loop_scope`
  - `provenance`

What still fails in practice:

- legacy param names are misleading
  - example: `rfantibody_input_pdbs` can actually point to selected PPIFlow outputs
- immediate source stage is not explicit
  - the user sees a root lineage and child jobs, but not a clean statement like `launched from 7 selected PPIFlow outputs`
- shard identity is overexposed
  - the user sees `1/2`, `2/2`, `FA-MPNN 1/2`, etc. instead of stage-family lineage first
- the viewer does not present a stable ancestry chain per design
- some codepaths still infer lineage from filenames or job names when a direct source reference should exist

So the system is close, but not explicit enough.

## Product Goal

For any design shown in the viewer, the platform must be able to answer these questions directly:

1. Which job produced this design?
2. Which single design did it come from immediately?
3. Which stage family and stage mode produced it?
4. Which source stage family and source stage mode fed into this stage?
5. Which exact structure file was used as input?
6. Which experiment lineage root does it belong to?
7. Which original RF backbone does it ultimately descend from?
8. Which selected dataset or saved set was used to launch it?

The answer must be DB-backed and API-exposed, not reconstructed from naming conventions.

## Terminology

This spec standardizes four different ancestry concepts that are currently blurred together.

### 1. Lineage Root

The top-level experiment lineage.

Example:

- `RBX1 beta large_resumed`

This remains `lineage_root_job_id`.

### 2. Immediate Parent Design

The single design row directly transformed to create the new design.

Examples:

- RF review backbone -> PPIFlow refined sample
- PPIFlow refined sample -> FA-MPNN sequence design
- FA-MPNN design -> validation prediction

This remains `parent_design_id`.

### 3. Origin Backbone Design

The root-most RF backbone design row that the lineage ultimately descends from.

This remains `origin_backbone_design_id`.

### 4. Source Stage

The stage family and job that the current job launched from.

Examples:

- `source_stage_family = ppiflow`
- `source_stage_mode = backbone_refine`
- `source_stage_job_id = b3b337ec-...`

This is the key concept that is still too implicit today and is the main addition in this spec.

## Current State

### Backend

Already present:

- selection manifests written during launch
- design lineage IDs on ingest
- per-design provenance payloads
- stage-family fields on jobs/designs

Still weak:

- launch params still use legacy field names that imply the wrong source stage
- some lineage resolution still falls back to filename/name matching
- source-stage identity is not a first-class DB field
- selection manifest path is not persisted as a first-class job field

### Viewer

Already present:

- grouped stage-family lineage cards
- `source_design_name` and `source_pdb_path` visible for PPIFlow rows
- parent lineage cards for paused RF and completed downstream runs

Still weak:

- launch source is not rendered as a canonical breadcrumb
- child shard names still dominate many views
- per-design ancestry chain is not rendered explicitly
- “this run came from selected PPIFlow outputs” is not obvious

## Design Principles

1. Use canonical lineage fields, not legacy stage-specific param names.
2. Preserve both job-level and design-level ancestry.
3. Separate `lineage root` from `immediate source stage`.
4. Prefer direct IDs over path/name inference wherever possible.
5. Preserve the exact selected input structure set via manifest path and manifest contents.
6. Make the UI stage-first and design-ancestry-first, not shard-name-first.

## Required Revisions

## 1. Canonical Source Stage Fields

Add explicit source-stage fields to `Job`.

In [platform/api/database.py](../platform/api/database.py) on `Job`, add:

- `source_stage_job_id: String(36), nullable=True, index=True`
- `source_stage_family: String(64), nullable=True, index=True`
- `source_stage_mode: String(64), nullable=True, index=True`
- `source_selection_manifest_path: String(500), nullable=True`
- `source_selection_count: Integer, nullable=True`

Purpose:

- `selection_source_job_id` is not enough because it mixes “where the user clicked from” with “which stage produced the selected structures”
- `source_stage_*` should describe the actual stage identity of the selected inputs
- `source_selection_manifest_path` makes the selected input set auditable and reproducible

Keep existing fields:

- `lineage_root_job_id`
- `selection_source_type`
- `selection_source_job_id`
- `selection_dataset_name`

Those remain useful, but they do not replace explicit source-stage identity.

## 2. Canonical Immediate Source Fields On Design

Add explicit source-stage fields to `Design`.

In [platform/api/database.py](../platform/api/database.py) on `Design`, add:

- `source_stage_job_id: String(36), nullable=True, index=True`
- `source_stage_family: String(64), nullable=True, index=True`
- `source_stage_mode: String(64), nullable=True, index=True`
- `source_pdb_path: String(500), nullable=True`
- `source_design_name: String(255), nullable=True`

Purpose:

- `parent_design_id` answers “which row”
- `source_stage_*` answers “which stage”
- `source_pdb_path` answers “which exact structure file”

This avoids overloading `provenance` for values that are central to filtering, linking, and display.

Keep `provenance` for stage-specific extras only.

## 3. Canonical Selection Manifest Schema

The selection manifest must become the stable contract for downstream launches.

Standard schema for each selected entry:

```json
{
  "design_id": "uuid",
  "design_name": "012_xxx_ppiflow_sample0",
  "design_job_id": "uuid",
  "design_stage_family": "ppiflow",
  "design_stage_mode": "backbone_refine",
  "lineage_root_job_id": "uuid",
  "parent_design_id": "uuid",
  "origin_design_id": "uuid",
  "origin_backbone_design_id": "uuid",
  "source_design_name": "012_xxx_ppiflow_sample0",
  "source_pdb_path": "/abs/path/to/real/input.pdb",
  "selection_pdb_path": "/abs/path/to/link/in/selection_dir.pdb",
  "selection_entry_mode": "symlink",
  "selected_loop_scope": {
    "selected_loops": ["H1", "H2"]
  }
}
```

Required changes:

- use `source_pdb_path` consistently everywhere
- stop mixing `pdb_path` and `source_pdb_path` between codepaths
- include explicit `design_stage_family` and `design_stage_mode`
- include `lineage_root_job_id`

Relevant codepaths:

- [platform/api/routers/jobs.py](../platform/api/routers/jobs.py)

## 4. Stop Using Legacy Stage-Specific Input Param Names

Legacy launch params like:

- `rfantibody_input_pdbs`
- `fampnn_collected_pdbs`

must stop being used as the canonical meaning of source.

Replace with canonical stage-agnostic fields:

- `selected_input_dir`
- `selected_input_manifest`
- `selected_input_stage_family`
- `selected_input_stage_mode`
- `selected_input_source_job_id`

Legacy params may still be accepted for backward compatibility, but they should be treated as aliases only and normalized immediately at job creation time.

Relevant codepaths:

- [platform/api/routers/jobs.py](../platform/api/routers/jobs.py)
- [workflows/antibody_denovo.nf](../workflows/antibody_denovo.nf)
- any child-spawn logic that still threads legacy names into Nextflow params

## 5. Make Source Stage Explicit At Launch Time

Every re-orchestrated job must compute and persist:

- `source_stage_job_id`
- `source_stage_family`
- `source_stage_mode`
- `source_selection_manifest_path`
- `source_selection_count`

Rules:

- if the user launches from selected PPIFlow rows, `source_stage_family = ppiflow`
- if the user launches from selected RF review rows, `source_stage_family = rfantibody`
- if the user launches from a saved dataset, the dataset type still persists in `selection_source_type`, but the actual selected design rows must determine `source_stage_family`

This is where the current system is semantically thin.

## 6. Ingestion Must Prefer Direct Manifest And Direct IDs

In [platform/api/services/result_ingester.py](../platform/api/services/result_ingester.py):

- keep `_resolve_parent_design_lineage(...)`
- change its priority order to:
  1. manifest `design_id`
  2. explicit DB `source_stage_job_id`
  3. explicit `source_pdb_path`
  4. explicit `source_design_name`
  5. filename inference as final fallback only

Required additions:

- write `source_stage_*` and `source_pdb_path` directly to new design columns
- stop relying on `provenance["ppiflow"]["source_*"]` as the primary store
- keep provenance copy for backward compatibility and debugging

## 7. Add Stable Pathing Conventions

Current pathing is serviceable but not explicit enough in the UI.

Add canonical path categories:

- `selected_input_dir`
- `selected_input_manifest_path`
- `published_output_dir`
- `stage_results_dir`
- `collected_output_dir`

Persist them on `Job.provenance` even if they are not promoted to columns.

That lets the viewer show:

- source selection dir
- source manifest
- run output dir
- collected output dir

without guessing from stage family.

## 8. Viewer Breadcrumbs And Source Cards

The viewer needs one explicit lineage breadcrumb component.

For a selected design, show:

1. `Lineage Root`
2. `Source Stage`
3. `Immediate Parent Design`
4. `Current Design`

Example:

`RBX1 beta large_resumed -> PPIFlow Backbone Refinement -> 012_de725..._ppiflow_sample0 -> FA-MPNN Seq 7`

Required UI additions:

- add a `Lineage` card in [platform/frontend/src/components/ResultsViewer.tsx](../platform/frontend/src/components/ResultsViewer.tsx)
- add source-stage chips:
  - `RF Review`
  - `PPIFlow Refine`
  - `FA-MPNN`
  - `Validation`
- add direct links/buttons:
  - `Open Source Stage`
  - `Open Immediate Parent`
  - `Open Root Backbone`

## 9. Make Stage-Family Viewer Labels Explicit

Current naming should be revised from shard-centric labels like:

- `FA-MPNN 1/2`
- `Backbone Refine 1/2`

to:

- `FA-MPNN Child Shard 1/2`
- `PPIFlow Backbone Refine Child Shard 1/2`

but only in secondary detail views.

Primary cards should show:

- `7 selected PPIFlow outputs`
- `Produced by 2 FA-MPNN child shards`

The user should never have to infer the source set from raw child names.

## 10. Add Launch-Time Review Summary

Before the job is created, persist a launch summary in job provenance:

- selected count
- source stage family
- source stage mode
- source job ids involved
- selected metric snapshot
  - for PPIFlow: `ΔIface`, `Iface`, `sample`, `source backbone`
  - for RF review: `RF pLDDT`, contacts, distances

This will make it possible to show:

- `Launched from 7 selected PPIFlow outputs`
- `Selection metric range: ΔIface -32.39 to -14.42`

without re-querying the source table every time.

## 11. Backfill Existing RBX1 And Similar Historical Lineages

Add a one-time migration/backfill script:

- infer `source_stage_*` for existing jobs from:
  - `selection_manifest.json`
  - `selection_source_job_id`
  - selected design rows
- backfill design-level `source_stage_*`, `source_pdb_path`, `source_design_name`
- preserve existing `provenance` content

This should cover:

- PPIFlow refine runs already in the DB
- FA-MPNN runs launched from selected PPIFlow outputs
- validation runs launched from selected FA-MPNN outputs

## 12. API Shape Revisions

Expose the new lineage/source fields in:

- `GET /api/jobs`
- `GET /api/jobs/{id}`
- `GET /api/designs`
- `GET /api/designs/{id}`

Minimum additions for jobs:

- `source_stage_job_id`
- `source_stage_family`
- `source_stage_mode`
- `source_selection_manifest_path`
- `source_selection_count`

Minimum additions for designs:

- `source_stage_job_id`
- `source_stage_family`
- `source_stage_mode`
- `source_pdb_path`
- `source_design_name`

## 13. Acceptance Criteria

This revision is complete when all of the following are true:

1. A post-PPIFlow FA-MPNN job clearly shows `source_stage_family = ppiflow`.
2. A selected FA-MPNN output clearly shows its `parent_design_id` pointing to the specific selected PPIFlow design.
3. The viewer can render a readable ancestry chain for any selected design.
4. Legacy params like `rfantibody_input_pdbs` are no longer the canonical source identity.
5. The user can see from the UI whether a launch came from:
   - raw RF review rows
   - screened RF review rows
   - selected PPIFlow outputs
   - selected FA-MPNN outputs
   - saved dataset
6. Historical RBX1 lineages can be backfilled enough to make this visible on existing runs.

## 14. Implementation Order

Recommended order:

1. DB schema additions for `source_stage_*` and source fields
2. selection manifest schema normalization
3. launch param normalization in job creation
4. ingest path priority cleanup in `result_ingester.py`
5. API schema exposure
6. viewer breadcrumb and source card UI
7. historical backfill script

## 15. Concrete File Targets

Primary backend:

- [platform/api/database.py](../platform/api/database.py)
- [platform/api/routers/jobs.py](../platform/api/routers/jobs.py)
- [platform/api/services/result_ingester.py](../platform/api/services/result_ingester.py)
- [platform/api/routers/designs.py](../platform/api/routers/designs.py)

Primary frontend:

- [platform/frontend/src/components/ResultsViewer.tsx](../platform/frontend/src/components/ResultsViewer.tsx)
- [platform/frontend/src/components/designOutputSource.ts](../platform/frontend/src/components/designOutputSource.ts)
- [platform/frontend/src/lib/api.ts](../platform/frontend/src/lib/api.ts)

Migration / recovery:

- new script: `scripts/backfill_lineage_source_tracking.py`

## Bottom Line

This is not a large conceptual rewrite.

The platform already has most of the raw information needed. The remaining work is to:

- promote the right ancestry/source fields to first-class DB/API fields
- normalize manifest and launch param semantics
- stop making the user infer lineage from filenames and child job names

In practice this is one focused revision set, not a broad architectural detour.
