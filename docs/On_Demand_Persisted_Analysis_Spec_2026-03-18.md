# On-Demand Persisted Analysis Spec

Date: 2026-03-18
Status: proposed

## Source Review

This spec is based on the current repository guidance and implementation in:

- `platform/api/README.md`
- `docs/ai_guidance/Database_Instructions.md`
- `docs/ai_guidance/Centralization_and_Standardization.md`
- `docs/RFA_Interactive_SWA_Review_2026-03-06.md`
- `docs/Plotly_Analytics_Revision_2026-03-14.md`
- `platform/api/routers/designs.py`
- `platform/api/routers/analytics.py`
- `platform/frontend/src/components/ResultsViewer.tsx`
- `platform/api/main.py`
- `platform/api/services/gpu_orchestrator.py`

## Problem

The current viewer mixes three different analysis models:

1. Some metrics are pipeline-time and persisted into `designs`.
2. Some metrics are lazy-computed and then persisted on first access.
3. Some metrics are recomputed directly inside request handlers every time a user opens a tab or requests an analysis view.

That inconsistency causes two concrete problems:

- the UI pays heavy compute cost interactively
- the same heavy analysis can be recomputed multiple times without durable reuse

The worst current patterns are:

- `GET /api/designs/{id}/structure-analysis` recomputes from structure files on every request
- `GET /api/designs/{id}/contact-map` recomputes from structure files on every request
- several job-level analytics endpoints load all designs and recompute aggregates per request
- the viewer auto-fetches analysis endpoints when tabs become active
- the main design-list payload is broad enough that it can carry more JSON than the table actually needs

The result is viewer lag, duplicate work, and CPU-heavy binder inspection running in the worst possible place: the interactive request path.

## Design Principle

Do not do expensive binder analysis in request handlers.

Instead:

1. make heavy analysis explicit and user-invoked
2. run it asynchronously outside the request path
3. persist results with cache keys and input fingerprints
4. reuse completed results until inputs or code semantics change
5. keep pipeline-critical screening separate from viewer-only exploratory analysis

This follows the same general pattern already recommended in the stage-gate documentation:

- compute to a stable checkpoint
- persist artifacts and metadata
- expose them through API/UI
- avoid keeping orchestration coupled to a live interactive request

## Goals

- Heavy binder analysis runs only on command.
- Results persist and are reused across refreshes, sessions, and users.
- Duplicate requests for the same subject and params coalesce to one run.
- CPU-heavy analysis can use the full configured CPU budget without blocking FastAPI request handling.
- The main viewer becomes cheap by default.
- Existing pipeline-generated gating metrics remain queryable and sortable in the main `designs` surface.

## Non-Goals

- Replacing existing Nextflow pipeline stages that are part of generation, validation, or gating
- Converting every persisted scalar metric into an analysis run
- Reusing the GPU scheduler as-is for CPU-exclusive viewer analysis
- Storing every heavy artifact directly in SQLite JSON columns

## Analysis Classification

The platform should treat analysis in three classes.

### 1. Pipeline-Critical Analysis

These remain automatic and persisted during the workflow because they are part of ranking, filtering, or stage gating.

Examples:

- RFantibody screening outputs
- per-loop RF screening summaries used for first-pass triage
- validator outputs already produced by Boltz or Protenix
- ingestion-derived scalar fields needed for table sort/filter

Persistence target:

- typed `Design` columns for stable sortable fields
- namespaced JSON on `Design` or `Job` for richer but still gate-relevant detail

### 2. Design-Scoped Viewer Analysis

These should be user-triggered, persisted, and reusable.

Examples:

- structure summary
- contact map
- deeper binder-target geometry
- chain annotation overlays
- per-loop diagnostic reports that are not needed for initial screening
- expensive derived comparison outputs

Persistence target:

- dedicated analysis-run tables plus artifact files on disk

### 3. Job-Scoped Analysis Snapshots

These should also be user-triggered and persisted.

Examples:

- correlation matrices
- AA composition and CDR logo packs
- binder clustering or structural family summaries
- chart packs for large selected sets

Persistence target:

- dedicated analysis-run tables plus summary JSON and optional artifacts

## Current State Summary

### Good Existing Patterns

- DB pathing is centralized through `platform/api/paths.py`.
- Background work already exists in the platform via `GPUOrchestrator`.
- Review gates already persist stage payloads and artifacts.
- Some API routes already follow lazy-compute-then-persist:
  - chain metrics
  - antibody annotation

### Current Gaps

- no first-class persisted analysis-run abstraction
- no stable cache key or input signature for viewer analysis
- no dedicated CPU analysis worker
- no API distinction between "get cached analysis" and "start new analysis"
- large list/detail responses still blur table data and deep inspection data

## Proposed Architecture

Add a new persisted analysis subsystem with four parts:

1. analysis registry
2. analysis run records
3. analysis worker service
4. on-demand API and UI integration

### 1. Analysis Registry

Add a registry of analysis types, similar in spirit to model integration metadata, but specific to viewer and reporting analysis.

Each analysis type declares:

- `analysis_type`
- `subject_kind`
  - `design`
  - `job`
  - `design_set`
- `version`
- `resource_class`
  - `light`
  - `cpu_heavy`
  - `gpu_heavy` if ever needed later
- `default_cpu_policy`
  - `shared`
  - `exclusive`
- `params_schema`
- `dependencies`
  - `structure`
  - `screening`
  - `confidence`
  - `annotation`
  - `design_set_membership`
- `runner`
  - Python callable or script entrypoint
- `result_contract`
  - summary keys
  - artifact kinds
  - inline payload size expectations

Recommended initial analysis types:

- `structure_summary`
- `contact_map`
- `binder_geometry`
- `antibody_annotation_pack`
- `job_correlation_matrix`
- `job_cdr_logo_pack`
- `job_chart_pack`

## Data Model

Add new tables instead of overloading `jobs` or further bloating `designs`.

### `analysis_runs`

Suggested fields:

- `id`
- `subject_kind`
- `subject_id`
- `analysis_type`
- `status`
  - `queued`
  - `running`
  - `completed`
  - `failed`
  - `cancelled`
  - `stale`
- `resource_class`
- `params_json`
- `params_hash`
- `input_signature`
- `code_version`
- `cache_key`
- `summary_json`
- `result_inline_json`
- `artifact_manifest`
- `error_message`
- `requested_by`
- `reuse_count`
- `supersedes_run_id`
- `queued_at`
- `started_at`
- `completed_at`
- `last_accessed_at`

Indexes:

- `(subject_kind, subject_id, analysis_type, status)`
- unique or effectively unique lookup by `cache_key` for reusable completed runs
- `(status, resource_class, queued_at)` for worker polling

### `analysis_artifacts`

Suggested fields:

- `id`
- `run_id`
- `artifact_kind`
- `relative_path`
- `content_type`
- `size_bytes`
- `sha256`
- `created_at`

This can be deferred if `artifact_manifest` on `analysis_runs` is sufficient for phase 1.

## Filesystem Layout

Add a canonical analysis cache root via `paths.py`.

Recommended helper:

- `get_analysis_cache_dir() -> get_data_root() / "analysis_cache"`

Recommended layout:

```text
${BMS_DATA}/analysis_cache/
  design/
    <design_id>/
      <analysis_type>/
        <cache_key>/
          manifest.json
          summary.json
          result.json
          stdout.log
          stderr.log
          artifacts/
            ...
  job/
    <job_id>/
      <analysis_type>/
        <cache_key>/
          ...
```

Rules:

- artifact paths returned to clients should use allowed relative paths
- heavy matrices, image outputs, CSVs, and NPZ files should stay on disk
- SQLite should only hold summary/index data, not large binary payloads

## Cache Key and Invalidation

The cache key must represent both semantic inputs and code semantics.

Recommended key material:

- `subject_kind`
- `subject_id`
- `analysis_type`
- normalized `params_json`
- `code_version`
- `input_signature`

### `code_version`

Use an explicit version constant per analysis type, not only git SHA.

Reason:

- analysis semantics can change independently of deployment state
- old cached outputs should not silently satisfy new logic

### `input_signature`

Each analysis type computes this from its declared dependencies.

Examples:

- `structure_summary`
  - structure file path
  - structure file size
  - structure file mtime
- `binder_geometry`
  - structure fingerprint
  - target/epitope params
  - any selected screening scope
- `job_correlation_matrix`
  - relevant design IDs
  - relevant scalar values or a digest of those values
  - set membership and params, but not favorites/notes unless explicitly requested

Important rule:

Input signatures must ignore UI-only state that does not change scientific content.

Examples:

- table sort order
- selected row in the viewer
- favorites
- freeform notes

### Reuse Semantics

- If a completed run exists for the exact `cache_key`, return it.
- If a queued or running run exists for the exact `cache_key`, return that run instead of creating another.
- If inputs changed, mark old runs `stale` on lookup or simply allow them to remain historical but non-current.
- `force_refresh=true` creates a new run and links `supersedes_run_id`.

## Worker Model

Use a dedicated CPU analysis worker, not request-time `asyncio.to_thread()` and not the GPU scheduler.

### Recommendation

Implement a new `AnalysisWorker` service that:

- polls `analysis_runs` for queued work
- respects CPU resource policy
- launches analysis in subprocesses
- updates DB state asynchronously
- persists artifacts and summaries

### Why Not Reuse `jobs` + GPU Orchestrator

- the existing queue is GPU-centric
- analysis runs are not user-facing generation jobs
- they should not pollute the normal recent-jobs surface
- CPU-exclusive viewer analysis has different scheduling rules

### Why Subprocesses Instead of In-Process Compute

- full CPU utilization without blocking request workers
- better isolation for large NumPy/Biotite workloads
- easier timeouts and cancellation
- cleaner stderr/stdout capture

### Deployment Recommendation

Preferred:

- separate OS process started alongside the API

Acceptable phase 1:

- separate background service in the same codebase, but still spawning subprocesses

Do not:

- run heavy analysis inside request handlers
- rely on threadpool tasks for CPU-saturating analysis

## CPU Policy

The user requirement is that analysis scripts can use the full CPU budget when requested.

Recommended policy model:

- `shared`
  - light analyses can run concurrently with bounded worker count
- `exclusive`
  - one heavy analysis run at a time
  - receives almost all host CPU threads

Recommended settings:

- `BMS_ANALYSIS_MAX_CONCURRENT_LIGHT=2`
- `BMS_ANALYSIS_MAX_CONCURRENT_HEAVY=1`
- `BMS_ANALYSIS_HEAVY_CPUS=all_minus_2` by default
- `BMS_ANALYSIS_LIGHT_CPUS=4` by default

For true "full CPU" behavior:

- heavy analyses should run one at a time by default
- the subprocess should receive explicit thread-count env or CLI flags
- BLAS/OpenMP thread env should be set deliberately

Examples:

- `OMP_NUM_THREADS`
- `OPENBLAS_NUM_THREADS`
- `MKL_NUM_THREADS`
- analysis-specific `--workers`

## API Surface

Use nested trigger endpoints plus generic run lookup endpoints.

### Design-Scoped

- `GET /api/designs/{design_id}/analyses`
  - list available analysis types, current status, latest completed run, staleness
- `POST /api/designs/{design_id}/analyses/{analysis_type}`
  - create or reuse an analysis run
- `GET /api/designs/{design_id}/analyses/{analysis_type}`
  - fetch latest completed result for current params or return current run state

### Job-Scoped

- `GET /api/jobs/{job_id}/analyses`
- `POST /api/jobs/{job_id}/analyses/{analysis_type}`
- `GET /api/jobs/{job_id}/analyses/{analysis_type}`

### Generic

- `GET /api/analyses/{run_id}`
- `POST /api/analyses/{run_id}/cancel`
- `GET /api/analyses/{run_id}/artifacts`

### Request Shape

```json
{
  "params": {
    "scope": "cdr_loops",
    "max_size": 300
  },
  "force_refresh": false
}
```

### Response Shape

```json
{
  "run_id": "uuid",
  "analysis_type": "binder_geometry",
  "subject_kind": "design",
  "subject_id": "uuid",
  "status": "completed",
  "cache_hit": true,
  "stale": false,
  "params": {},
  "summary": {},
  "artifacts": [],
  "queued_at": "...",
  "started_at": "...",
  "completed_at": "..."
}
```

## Frontend Behavior

The viewer should stop auto-triggering heavy analysis.

### Default Rule

Opening a tab should only fetch:

- already persisted scalar fields
- existing analysis statuses
- cached completed results if they already exist

If no cached result exists, show a button.

Examples:

- `Run structure analysis`
- `Generate contact map`
- `Build chart pack`
- `Refresh analysis`

### Persisted UI State

Show:

- last completed time
- analysis version
- whether result is stale
- who requested it if useful
- whether the current response is a cache hit

### Important UI Follow-On

The main design list should become a skinny payload.

Specifically, the default table/list response should stop shipping large blobs such as:

- `confidence_metrics`
- `residue_plddt`
- `chain_metrics`
- `rfa_loop_metrics`
- `rfa_hotspot_metrics`
- `stability_data`

Recommendation:

- keep `/api/designs` optimized for table rows
- fetch deep per-design detail separately
- fetch heavy analyses only through the new analysis endpoints

This is required even if analysis runs are added, because the list payload itself can still lag the viewer.

## Relationship to Existing Endpoints

### Keep As Persisted Scalar Surfaces

- `Design` typed fields used for table filtering and ranking
- stage-review rows materialized by `ensure_stage_review_rows()`
- flattened Plotly scalar metrics derived from persisted fields

### Migrate to Analysis Runs

- `structure-analysis`
- `contact-map`
- job correlation matrix
- job AA composition and logo packs
- any future deep binder geometry pack

### Partial Existing Patterns to Preserve

- antibody annotation currently lazy-computes then persists
- chain metrics currently lazy-compute then persist

These can either:

- stay as-is if they are cheap enough
- or be migrated behind the same analysis-run abstraction for consistency

## Result Contract

Every analysis run should write:

- `manifest.json`
- `summary.json`
- optional `result.json` for small structured payloads
- stdout/stderr logs

Standard summary fields:

- `analysis_type`
- `analysis_version`
- `subject_kind`
- `subject_id`
- `params`
- `input_signature`
- `code_version`
- `started_at`
- `completed_at`
- `duration_seconds`

Optional domain summary fields:

- representative metrics
- counts
- warnings
- redesign suggestions
- provenance notes

## Error Handling

Failures must be persistent and inspectable.

Requirements:

- failed runs stay in DB with `error_message`
- logs are retained under the artifact directory
- the UI can display last failure and allow rerun
- transient crashes should not leave `running` forever

Worker recovery rule:

- if a worker starts and finds a `running` analysis whose process no longer exists, mark it `failed` or `queued` based on restart policy

## Rollout Plan

### Phase 1

- add `analysis_runs`
- add `get_analysis_cache_dir()`
- add worker service with subprocess execution
- migrate `structure-analysis` and `contact-map`
- stop auto-running those in the viewer

### Phase 2

- add job-level analysis runs
- migrate correlation matrix and AA composition/logo endpoints
- slim `/api/designs` table payload
- add cached-status badges in the viewer

### Phase 3

- migrate remaining deep binder-inspection analyses
- optionally unify existing lazy-persist endpoints under the same analysis framework
- add cleanup policies and stale-run management

## Acceptance Criteria

- Opening the main results viewer no longer launches heavy compute implicitly.
- Running the same analysis twice with unchanged inputs reuses the previous result.
- Heavy design analysis survives page reload, API restart, and browser restart.
- Large result files live on disk, not in oversized JSON response bodies.
- The API remains responsive while a heavy CPU analysis is running.
- A user can inspect run status, logs, and completion time.
- Core table sorting and stage-gate metrics remain immediately available without extra analysis runs.

## Recommendation

Implement the analysis-run subsystem as a separate persisted layer, not as an extension of the current `jobs` table.

The key architectural split should be:

- workflow and gate metrics: automatic, persisted during pipeline execution
- deep viewer analysis: explicit, queued, persisted, reusable

That gives the viewer the behavior you want without weakening first-pass screening or overloading interactive requests with compute.
