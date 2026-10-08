# BoltzGen De Novo Parity Implementation Spec

Date: 2026-04-13

## Goal

Bring `BoltzGen -> shortlist -> Antibody Refinement` to functional parity with the older RFantibody-first path without reintroducing RF-specific coupling in hidden places.

This pass is explicitly about the layer **before and around refinement**:

- initial de novo cohort identity
- shortlist persistence
- Results Viewer grouping and tables
- Plotly analytics/reporting
- continuation semantics into refinement

The refinement pipeline itself is already largely harmonized through the antibody artifact contract and does not need a redesign in this pass.

## Current State

### What is already sound

- The backend artifact contract exists and is usable:
  - `backbone_complex`
  - `sequence_designed_complex`
  - `validated_complex`
  - `post_validation_refined_complex`
- BoltzGen nanobody outputs already map to `sequence_designed_complex`.
- Refinement compatibility already works off that contract.
- Result ingestion already carries lineage/provenance and artifact metadata on jobs/designs.

### What is still broken

The system still has **RFantibody-era assumptions** in three places:

1. source inference in the frontend
2. paused review / shortlist persistence
3. Results Viewer grouping, labels, and analysis defaults

The main symptom is that BoltzGen generator outputs are currently interpreted as `validation` rather than as a generator cohort.

## Design Principles

1. Do not overload `post_rfantibody` as a generic generator stage.
2. Do not overload `artifact_group == candidate` for BoltzGen cohorts.
3. Use the antibody artifact contract as the compatibility layer.
4. Keep generator identity and artifact class as separate concerns.
5. Centralize source inference in one helper, then have the rest of the UI consume that helper.

## Target State

### Conceptual model

There are two separate axes:

- `artifact_class`
  - what kind of structure artifact this is
  - used for compatibility and refinement routing
- `generator_family`
  - where the upstream de novo cohort came from
  - used for cohort grouping, labels, and first-pass review/reporting

For this pass, `generator_family` should support:

- `rfantibody`
- `boltzgen`

The current generator family can be derived from existing fields initially:

- `stage_family`
- `stage_mode`
- `model_id`
- `mode`
- `provenance`

No new generator table is required.

### Output source model

Update the frontend source/lens model from:

- `all`
- `rfantibody`
- `fampnn`
- `ppiflow`
- `validation`

to:

- `all`
- `rfantibody`
- `boltzgen`
- `fampnn`
- `ppiflow`
- `validation`

And analysis lenses from:

- `validation`
- `rfantibody`
- `fampnn`
- `ppiflow`
- `frustrampnn`
- `protenix`

to:

- `validation`
- `rfantibody`
- `boltzgen`
- `fampnn`
- `ppiflow`
- `frustrampnn`
- `protenix`

`boltzgen` here means:

- a generator-first nanobody cohort
- not a validation stage
- structurally antibody-like enough to use the same binder/CDR viewer affordances

## Functional Gaps To Close

### 1. Frontend contract parity

The backend already exposes artifact metadata, but the frontend types do not consistently use it.

Required changes:

- Add to `Job` in `platform/frontend/src/lib/api.ts`:
  - `selected_input_artifact_class`
  - `selected_input_schema_version`
- Add to `Design` in `platform/frontend/src/lib/api.ts`:
  - `artifact_class`
  - `artifact_schema_version`

This is required before the viewer can stop inferring everything from path strings and stage names.

### 2. Central source inference rewrite

`platform/frontend/src/components/designOutputSource.ts` must become the single source of truth for cohort identity.

Current problems:

- any `stage_family` containing `boltz` is treated as `validation`
- `modelId.includes('boltz')` also maps jobs to `validation`
- the helper does not read `artifact_class`

Required implementation:

- expand `OutputSourceFilter` to include `boltzgen`
- expand `AnalysisLens` to include `boltzgen`
- extend input types to read:
  - `artifact_class`
  - `artifact_schema_version`
  - `provenance`
- use this inference order:

1. explicit `artifact_class` + `stage_family/stage_mode`
2. explicit generator hints from provenance or model/mode
3. legacy `source_stage` / `artifact_group`
4. path heuristics as final fallback

Rules:

- `stage_family == boltzgen` with mode `nanobody_binder` or `antibody_binder` => `boltzgen`
- `artifact_class == sequence_designed_complex` plus `stage_family == boltzgen` => `boltzgen`
- validation requires:
  - `stage_family == validation`, or
  - `artifact_class == validated_complex`, or
  - explicit validation/protenix metrics
- plain `boltz` must not automatically mean validation

### 3. Results Viewer source/filter parity

`platform/frontend/src/components/ResultsViewer.tsx` currently hardcodes only four antibody source buckets.

Required implementation:

- add `boltzgen` to:
  - source filter state normalization
  - grouping maps
  - selector optgroups
  - source chips
  - lineage group selection
  - badge colors
  - table source labels
- add `boltzgen` to `LINEAGE_GROUP_ORDER`
- add `boltzgen` to `ANALYSIS_LENS_LABELS`
- update `getLineageFamily()` so `stage_family == boltzgen` becomes `boltzgen`, not `validation`
- update `getFriendlyDesignName()` to format BoltzGen rows as generator candidates instead of leaving raw filenames

Expected user-visible result:

- a BoltzGen nanobody batch appears as its own first-pass cohort
- the user can filter just BoltzGen outputs
- lineage grouping shows generator outputs separately from validation outputs

### 4. Viewer defaults for BoltzGen

BoltzGen generator designs should behave more like antibody designs than like validation-only outputs.

Required implementation:

- when `selectedDesignLens === boltzgen`, do not force validation-style defaults
- preserve antibody overlays and CDR-friendly defaults where annotations exist
- in `StructureViewerPane.tsx`, add a `boltzgen` lens path where RF-only labels are not shown but antibody overlays remain available

Desired behavior:

- BoltzGen generator outputs open with binder-aware visualization defaults
- no `RFantibody Screen Metrics` title for BoltzGen rows
- no validation-only pLDDT lens assumptions

### 5. Shortlist persistence outside paused review

RF review datasets are currently stored only under `job.awaiting_payload.review_filter_sets`, which makes them unavailable for completed non-paused BoltzGen jobs.

This is the main parity gap on the control-plane side.

Required implementation:

- add a dedicated job-level store for reusable shortlist/filter sets
- recommended DB change:
  - `jobs.saved_selection_sets = Column(JSON, default=list)`

Why a new column:

- shortlist state is not lineage provenance
- shortlist state should work for both paused review jobs and completed generator batches
- keeping it out of `awaiting_payload` removes stage-gate coupling

Compatibility plan:

- read from `saved_selection_sets` first
- fall back to `awaiting_payload.review_filter_sets`
- continue writing paused-review saves to both locations during migration

Required backend surfaces:

- generalize `_iter_saved_review_filter_sets()` to read from the new field
- generalize save/delete endpoints so they work for completed generator jobs, not just paused review jobs

Result:

- BoltzGen batches can persist top-N selections the same way older RF review workflows could
- refinement launches can consume saved shortlists without requiring a fake pause stage

### 6. BoltzGen ingestion completeness

BoltzGen result ingestion already captures many interface metrics, but it is still leaving useful cohort metadata on the floor.

Required implementation in `platform/api/services/result_ingester.py`:

- persist `binder_length` for BoltzGen generator rows
- persist `antibody_type` when inferable from mode/framework
- always persist resolved role fields:
  - `detected_antibody_chains`
  - `detected_target_chain`
  even when full geometry scoring is skipped
- add generator provenance hints:
  - `provenance.generator_family = "boltzgen"`
  - `provenance.generator_mode = job_params["boltzgen_mode"]`

Important:

- do not assign `artifact_group = candidate` to BoltzGen generator rows
- do not make them look like FAMPNN rows

### 7. `/api/designs` filtering parity

Client-side filtering is doing too much.

Required implementation in `platform/api/routers/designs.py`:

- add optional filters:
  - `artifact_class`
  - `stage_family`
  - `source_stage_family`
- keep `artifact_group` for review rows, but stop treating it as the only source filter dimension

This is not strictly required for the first visual fix, but it is the right backend support for large jobs and avoids more UI heuristics later.

### 8. Plotly analytics parity

The Plotly metric backend is already generic enough. The main issue is selection context and presets.

Required implementation:

- keep the dynamic metric backend as-is
- add BoltzGen-relevant preset defaults in `ExperimentalAnalyticsPane.tsx`, for example:
  - `conf_score vs iptm`
  - `affinity_score vs binder_probability`
  - `binder_length vs conf_score`
  - `complex_iplddt vs iptm`
- ensure BoltzGen cohorts are not labeled as validation cohorts in the analytics wrapper and job lens selection

No schema change is required for this part.

### 9. Review/gate semantics

Do not attempt to get parity by pretending BoltzGen has `post_rfantibody`.

Recommended approach for this pass:

- do not add a fake generator review stage yet
- keep BoltzGen generator batches as completed jobs
- use saved shortlist sets plus refinement handoff for parity

This gives users the operational loop they want:

- batch generate
- inspect / filter / analyze
- save top-N
- reopen in refinement

without forcing new workflow pauses into the standalone BoltzGen workflow immediately.

Later, if a true paused generator gate is still needed, it should be introduced as a new generic stage such as `post_generation`, not by reusing `post_rfantibody`.

## DB Changes

### Required

- Add `jobs.saved_selection_sets` JSON column

### Not required in this pass

- no new `designs` columns
- no new generator lookup tables
- no `generator_family` DB column yet

### Optional later

If cross-generator reporting becomes a primary workflow, add:

- `jobs.generator_family`
- `designs.generator_family`

But this should not block the current parity pass.

## File-Level Change Set

### Backend

- `platform/api/services/result_ingester.py`
  - complete BoltzGen cohort ingestion fields
  - persist generator provenance hints
- `platform/api/routers/designs.py`
  - add filter support for `artifact_class` / `stage_family`
- `platform/api/database.py`
  - add `saved_selection_sets`
- `platform/api/migrations/...`
  - migration for `saved_selection_sets`
- `platform/api/routers/jobs.py`
  - generalize shortlist read/write helpers
  - stop tying reusable saved selection sets to paused review only

### Frontend

- `platform/frontend/src/lib/api.ts`
  - add artifact contract fields to job/design types
- `platform/frontend/src/components/designOutputSource.ts`
  - central source/lens rewrite
- `platform/frontend/src/components/ResultsViewer.tsx`
  - add `boltzgen` source bucket and lineage parity
- `platform/frontend/src/components/StructureViewerPane.tsx`
  - add `boltzgen` lens handling
- `platform/frontend/src/components/ExperimentalAnalyticsPane.tsx`
  - add BoltzGen-centered presets

## Ordered Implementation Sequence

### Phase 1: Central semantics

1. update frontend API types
2. rewrite `designOutputSource.ts`
3. update Results Viewer filters/grouping/badges
4. update Structure Viewer lens handling

This phase fixes the largest user-visible parity bug quickly.

### Phase 2: Control-plane shortlist parity

1. add `jobs.saved_selection_sets`
2. migrate shortlist helpers off `awaiting_payload`-only assumptions
3. allow save/load/delete shortlist sets on completed BoltzGen jobs

This phase gives BoltzGen cohorts parity with RFA review datasets.

### Phase 3: Ingestion completeness

1. fill missing BoltzGen cohort fields
2. persist generator provenance hints
3. ensure chain-role fields survive even without full geometry scoring

This phase improves tables, filters, analytics, and downstream inspection quality.

### Phase 4: Backend filtering and analytics polish

1. add source/artifact filters to `/api/designs`
2. add BoltzGen Plotly presets
3. tune default viewer lens behavior for BoltzGen cohorts

## Acceptance Criteria

The pass is done when all of the following are true:

1. A completed BoltzGen `nanobody_binder` job appears in Results Viewer as a BoltzGen generator cohort, not validation.
2. The antibody tab and table filters can isolate BoltzGen outputs directly.
3. A BoltzGen job can save and reload a shortlist/top-N dataset without needing a paused review gate.
4. `Open Antibody Refinement` launched from a saved or ad hoc BoltzGen shortlist behaves the same as from a live selection.
5. BoltzGen cohort rows expose binder length, chain-role metadata, and the expected interface/confidence metrics in tables and analytics.
6. RFantibody review behavior remains unchanged.

## Tests

### Backend

- update `platform/api/tests/test_review_payload_and_fampnn_ingest.py`
  - BoltzGen cohort ingestion completeness
  - saved shortlist persistence on completed BoltzGen jobs
- add router tests for `/api/designs` new filters

### Frontend

- `npm run build`
- manual smoke:
  1. launch BoltzGen nanobody batch
  2. open Results Viewer and confirm BoltzGen cohort grouping
  3. save shortlist
  4. reload shortlist
  5. launch Antibody Refinement from shortlist
  6. confirm refinement input semantics are unchanged

## Non-Goals For This Pass

- adding a true paused `post_generation` stage to standalone BoltzGen workflow
- making every future generator first-class in the same pass
- redesigning the refinement pipeline itself

## Recommended Start Point

Implementation should start with:

1. `platform/frontend/src/lib/api.ts`
2. `platform/frontend/src/components/designOutputSource.ts`
3. `platform/frontend/src/components/ResultsViewer.tsx`

That is the safest first slice because it fixes the largest parity failure without forcing a workflow rewrite.
