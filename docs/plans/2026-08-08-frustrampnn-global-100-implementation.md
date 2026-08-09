# Global FrustraMPNN 100% implementation plan

**Status:** implementation-ready plan

**Date:** 2026-08-08

**Planning source pin:** `2431ac3775abc5688159bec1c91b92a957a83f9f`

**Target:** Development branch `test`; `main` remains outside this tranche

**Controlling specification:** `docs/specs/frustrampnn-global-configuration-analysis-workbench.md`

## 1. Outcome

Complete one global FrustraMPNN plane that owns the full path from typed settings to persisted, reviewable results.

Completion requires all of these outcomes on one exact Development revision:

1. The installed FrustraMPNN inference capability has a source-backed, machine-readable inventory.
2. Every relevant scientific or inference setting has one typed contract.
3. The human UI and the standard API expose the same settings, defaults, validation, and effective values.
4. System-owned paths, runtime identities, scheduler resources, GPU assignment, and output locations remain server-controlled.
5. Effective settings reach the pinned executable without silent removal, substitution, or fallback.
6. Requests, effective settings, runtime identity, normalized structures, residue maps, exact substitution rows, summaries, statistics, and artifacts remain immutable and queryable.
7. One reusable workbench supplies structure-linked exploration, statistics, comparison, saved review state, capture, and export.
8. Uploaded structures and existing integrated parent results open the same result surface.
9. One current Development job proves the new contract through the real scheduler and GPU path.

The tranche is complete only when gates G0 through G6 pass. Percent averages cannot close a failed gate.

## 2. Exact scope boundary

### In scope

- The pinned `predict` capability in `/mnt/BioModStack/apptainer/frustrampnn.sif`.
- Global settings metadata and validation.
- Uploaded-structure launch and persisted reanalysis.
- The minimum Structure Prediction settings transport needed to prove that one embedded producer can use the global contract.
- Canonical scheduler execution.
- Result persistence, statistics, comparison compatibility, visualization, capture, export, and review state.
- Historical read compatibility.

### Outside this tranche

- The Structure Prediction mutation editor and mutated-child loop.
- RFD3, Shape Blueprint, Region Redesign, or local-redesign guidance execution.
- Conformational Mapping contract migration.
- De novo nanobody work.
- An internal LLM, campaign, proposal, or autonomous iteration system.
- A generic model-runner framework.
- Training, benchmark evaluation, or model fine-tuning.
- Production promotion to `main`.

## 3. Source-backed capability inventory

The installed `frustrampnn predict --help` surface contains these inputs.

| Installed input | Ownership | Product control | Requirement |
|---|---|---|---|
| PDB path | Workflow/source | Governed source selector or upload | Snapshot and hash before scheduling. Do not expose a host path. |
| Checkpoint path | System | Read-only model/checkpoint identity | Pin path, file hash, image hash, and checkpoint ID. |
| Output directory | System | None | Allocate under the scheduler-owned job root. |
| `--chains` | Scientific/operator | Typed entity or chain multi-selector | Resolve stable source identities to normalized model chains. |
| `--positions` | Scientific/operator | Typed residue selector | Resolve stable residue identities to zero-based positions per normalized chain. |
| `--device` | Scheduler | None | Use task-visible `cuda:0` after scheduler assignment. |
| `--config` | System compatibility | None for the pinned MegaScale checkpoint | Retain only if the pinned runtime requires it. Record its hash when present. |
| `--quiet` | System diagnostics | None | Keep fixed. It does not alter scientific output. |

The integrated analysis lane also has three BMS scientific interpretation inputs:

| BMS input | Product control | Requirement |
|---|---|---|
| Source model number | Integer/model selector | Populate from source metadata. Default to model 1. Reject unavailable models. |
| Alternate-location choice | Dropdown | Populate observed alternate-location IDs. Preserve the current blank-or-explicit policy as the default. |
| Classification thresholds | Canonical/custom typed policy | Preserve raw scores. Default to `high_max=-1.0` and `minimal_min=0.58`. Require finite values and `high_max < minimal_min`. |

### G0 artifact

Create:

- `schemas/frustrampnn/capability_inventory_v1.schema.json`
- `platform/api/config/models/frustrampnn_capability_inventory_v1.json`
- `docs/reports/frustrampnn-pinned-capability-inventory.md`

The JSON inventory must record the image SHA-256, executable path and version evidence, checkpoint ID and SHA-256, supported `predict` arguments, ownership class, UI control kind, API type, default source, validation source, and exclusion reason for non-operator fields.

**G0 exit:** every installed `predict` argument has one recorded disposition. The inventory hash matches the bytes served by the API. No relevant setting remains unclassified.

## 4. Canonical settings contract

### 4.1 Requested settings

Create `platform/api/services/frustrampnn/settings.py` with strict Pydantic models. All models use `extra="forbid"`.

```python
class FrustraMPNNResidueRef(BaseModel):
    entity_instance_id: str
    auth_asym_id: str
    auth_seq_id: str
    insertion_code: str = ""

class FrustraMPNNProteinSelection(BaseModel):
    mode: Literal["all_protein_entities", "selected_entities", "selected_residues"]
    entity_instance_ids: list[str] = Field(default_factory=list)
    residues: list[FrustraMPNNResidueRef] = Field(default_factory=list)

class FrustraMPNNStructurePolicy(BaseModel):
    model_number: int = Field(default=1, ge=1)
    preferred_altloc: str = ""

class FrustraMPNNClassificationPolicy(BaseModel):
    mode: Literal["canonical", "custom"] = "canonical"
    high_max: float = -1.0
    minimal_min: float = 0.58

class FrustraMPNNSettingsV1(BaseModel):
    schema_name: Literal["frustrampnn_settings"]
    schema_version: Literal[1]
    protein_selection: FrustraMPNNProteinSelection
    structure_policy: FrustraMPNNStructurePolicy
    classification_policy: FrustraMPNNClassificationPolicy
```

Validation must enforce:

- selected-entity mode has at least one unique entity and no residue list;
- selected-residue mode has at least one unique complete residue identity and no entity list;
- all-entity mode has empty selectors;
- selected residues belong to selected protein entities after normalization;
- source model and alternate location exist in the source metadata;
- alternate-location IDs match the structure parser's closed identifier grammar;
- threshold values are finite and ordered;
- unknown keys fail;
- selector order is canonicalized before hashing;
- an empty effective protein selection fails before queueing.

### 4.2 Effective settings

Add `FrustraMPNNEffectiveSettingsV1`. It contains:

- the normalized requested settings;
- resolved entity, chain, and residue identities;
- exact per-chain zero-based model positions;
- normalization policy and version;
- threshold policy ID and hash;
- `settings_sha256` over canonical requested settings;
- `effective_settings_sha256` over the resolved settings;
- capability-inventory hash;
- explicit default-source metadata for every field.

Requested and effective values must remain separate. A request may select author residue `A:10`; execution may use normalized chain `A`, position `9`. Both identities must persist.

### 4.3 System configuration

Refactor `platform/api/services/frustrampnn/configuration.py` so it combines:

- immutable runtime identity from `runtime.py`;
- capability-inventory identity;
- validated requested settings;
- normalized source metadata;
- derived effective settings.

Replace the current singleton-only request helper with:

```python
def default_settings() -> FrustraMPNNSettingsV1: ...
def resolve_effective_settings(
    requested: FrustraMPNNSettingsV1,
    structure_map: Mapping[str, Any],
) -> FrustraMPNNEffectiveSettingsV1: ...
def execution_configuration(
    effective: FrustraMPNNEffectiveSettingsV1,
) -> FrustraMPNNExecutionConfigurationV2: ...
```

`configuration_id` must identify the schema generation. Per-request hashes identify actual effective values. Runtime identity gets its own hash. One broad digest must not conceal which compatibility dimension changed.

### 4.4 Schemas

Create:

- `schemas/frustrampnn/settings_v1.schema.json`
- `schemas/frustrampnn/effective_settings_v1.schema.json`
- `schemas/frustrampnn/execution_configuration_v2.schema.json`
- `schemas/frustrampnn/workflow_component_request_v2.schema.json`

Update `platform/api/services/frustrampnn/contracts.py` to validate v2. Keep v1 historical reads. All new execution writes use v2 after the owner-path cutover.

**G1 exit:** one settings object produces one deterministic effective settings object and hashes. UI metadata, API validation, request snapshots, and runtime compilation derive from this authority.

## 5. Human and agent control parity

### 5.1 Capability and validation APIs

Extend the bounded integration response from `platform/api/routers/models.py`:

`GET /api/models/frustrampnn/integration`

Add:

- `capability_inventory`;
- `settings_schema`;
- typed parameter descriptors;
- canonical defaults;
- field ownership;
- control-kind hints;
- compatibility rules;
- capability-inventory SHA-256.

Do not expose host paths, GPU IDs, scheduler labels, temporary directories, or command construction.

Add to `platform/api/routers/frustrampnn.py`:

- `POST /api/frustrampnn/settings/validate`
- `POST /api/frustrampnn/sources/inspect`

`settings/validate` accepts `FrustraMPNNSettingsV1` and source metadata or an owned source reference. It returns normalized requested settings, effective settings, hashes, and field-specific errors.

`sources/inspect` accepts a governed upload or owned artifact reference. It returns only selectable protein entities, chains, source models, alternate locations, and residue identities. Source bytes are size-limited and handled with the existing no-follow and snapshot rules.

### 5.2 Launch APIs

Extend these launch contracts with the same nested `frustrampnn_settings` object:

- `AnalyzeDesignsRequest`
- uploaded-structure analysis multipart metadata;
- external candidate handoff only where it immediately queues FrustraMPNN;
- persisted reanalysis, which defaults to the prior requested settings and permits an explicit complete replacement.

Update `platform/api/services/frustrampnn/jobs.py` so `create_child_job()` receives a validated settings object and snapshots it into every component request. Batch children may share settings only when every candidate resolves successfully. Each candidate retains its own effective settings.

Agent behavior is the normal API behavior. There is no separate agent endpoint or agent-only option.

### 5.3 Frontend controls

Create:

- `platform/frontend/src/components/frustrampnn/FrustraMpnnSettingsPanel.tsx`
- `platform/frontend/src/components/frustrampnn/FrustraMpnnProteinSelectionControl.tsx`
- `platform/frontend/src/components/frustrampnn/FrustraMpnnStructurePolicyControl.tsx`
- `platform/frontend/src/components/frustrampnn/FrustraMpnnClassificationControl.tsx`
- `platform/frontend/src/components/frustrampnn/frustraMpnnSettingsState.ts`

Extend:

- `platform/frontend/src/components/ModelIntegrationControl.tsx`
- `platform/frontend/src/lib/api.ts`
- `platform/frontend/src/lib/frustraMpnnApi.ts`

Behavior:

- disabled state shows the existing capability switch only;
- enabled state shows all relevant controls;
- advanced presentation may collapse fields but cannot omit them;
- selectors use source/entity metadata rather than free-form chain or position strings;
- dynamic source constraints disable unavailable values with a reason;
- the UI shows requested and effective values before submission when source metadata permits resolution;
- default, saved, template, and operator selections preserve existing precedence rules;
- the submitted object is the exact typed API object;

### 5.4 Minimum Structure Prediction adoption

Update only the settings transport required for global acceptance:

- `platform/frontend/src/components/StructurePredictionTemplate.tsx`
- `platform/frontend/src/components/structurePredictionUiState.ts`
- `platform/api/config/templates/structure_prediction.yaml`
- the existing Structure Prediction request validation owner

Submit:

```json
{
  "run_frustrampnn": true,
  "frustrampnn_settings": {"...": "FrustraMPNNSettingsV1"}
}
```

For predicted inputs, selectors use producer component/entity identity and sequence index. The candidate preparer resolves those identities after the structure exists. This avoids assuming final PDB chain labels at submission time.

No mutation editor, redesign action, or new Structure Prediction result logic belongs in this phase.

**G2 exit:** every setting can be discovered, validated, submitted, saved, restored, and inspected through both the typed UI and standard API. The two surfaces serialize the same object.

## 6. Exact execution transport

### 6.1 Candidate preparation

Update:

- `scripts/prepare_frustrampnn_candidate.py`
- `scripts/prepare_persisted_frustrampnn_candidate.py`
- `platform/api/services/frustrampnn/structure.py`

Requirements:

- consume requested settings from the v2 component request;
- apply source model and alternate-location policy during normalization;
- resolve entity and residue selectors against source-authoritative identity;
- write effective settings beside the structure map;
- reject missing, ambiguous, non-protein, or unscoreable selected residues;
- preserve excluded-residue reasons without silently widening the selection.

### 6.2 Runtime compilation

Update:

- `platform/api/services/frustrampnn/runtime.py`
- `scripts/run_frustrampnn_component.py`
- `modules/frustrampnn.nf`

The installed CLI applies one `--positions` list to each named chain. BMS residue selection is chain-specific. Compile it as follows:

- all protein entities: one canonical invocation without `--chains` or `--positions`;
- selected entities: one canonical invocation with the exact resolved chain list;
- selected residues: one executable invocation per chain with that chain's exact zero-based position list, followed by a deterministic raw-row merge.

The per-chain invocation approach preserves the vendor executable and avoids applying one chain's positions to another chain. The adapter must:

- sort chains and positions canonically;
- allocate separate raw output directories;
- record every argv vector and exit code;
- merge only exact vendor-schema rows;
- reject duplicate or missing selected residue slots;
- publish one canonical raw CSV in stable order;
- prove exactly `selected_scoreable_residues × 20` finite rows;
- record the invocation plan and merged-file hash in the execution receipt.

Classification thresholds apply after raw model execution. Raw scores remain unchanged.
Use synchronized numeric inputs for custom thresholds. A slider is unsuitable because the model does not define a scientifically valid finite score range.

### 6.3 Parent workflows

Update `workflows/structure_prediction.nf` only for v2 settings transport. Pass the complete settings object through `PrepareStructurePredictionFrustraMPNNCandidate`. Remove the literal checkpoint authority from workflow metadata. The canonical runtime registry owns checkpoint identity.

Other workflow consumers remain unchanged during this tranche. Their historical and current results must remain readable in the global workbench.

### 6.4 Fail-closed rules

- Unknown settings fail before queueing.
- Failed selector resolution fails the required stage.
- A model exit failure publishes no success manifest.
- Partial per-chain success fails the complete invocation.
- Scheduler GPU assignment remains mandatory.
- No CPU fallback, alternate checkpoint, reduced output, or fabricated slot is permitted.

**G3 exit:** request snapshot, effective settings, invocation plan, command receipt, raw output, landscape, and manifest agree exactly by hash and cardinality.

## 7. Persistence, statistics, and comparison

### 7.1 Database migration

Create a new forward migration. Do not edit the already applied FrustraMPNN migration:

- `platform/api/migrations/add_frustrampnn_global_workbench_v2.py`
- register it in `platform/api/migrations/runner.py`

Add nullable historical-compatible columns to `frustrampnn_results`:

- `settings_sha256`
- `effective_settings_sha256`
- `effective_settings_json`
- `capability_inventory_sha256`
- `statistics_sha256`
- `statistics_json`
- `comparison_compatibility_id`

Create:

- `frustrampnn_reviews`
- `frustrampnn_review_artifacts`

`frustrampnn_reviews` stores immutable review revisions bound to parent job, invocation, landscape hash, effective settings hash, typed view state, optional review text, review hash, creator identity where available, and timestamp.

`frustrampnn_review_artifacts` stores immutable captures and exports bound to a review revision with role, media type, content hash, size, governed storage path, and typed generation metadata.

Update matching SQLAlchemy classes in `platform/api/database.py`.

### 7.2 Ingestion

Update:

- `platform/api/services/result_ingester.py`
- `platform/api/services/frustrampnn/manifests.py`
- the FrustraMPNN persistence service that inserts result and landscape rows

Ingestion must atomically persist:

- requested and effective setting identities;
- effective settings JSON;
- capability inventory identity;
- statistics and hash;
- compatibility identity;
- existing manifest, artifact, runtime, summary, and exact-row evidence.

A duplicate result with differing settings, statistics, or compatibility identity is a conflict.

Historical v1 rows remain readable with explicit `legacy_missing` fields. The system must not infer settings that were not persisted.

### 7.3 Statistical authority

Create `platform/api/services/frustrampnn/statistics.py` and `schemas/frustrampnn/statistics_v1.schema.json`.

Compute deterministic, missingness-aware statistics from exact persisted rows:

- observed, scoreable, excluded, and missing counts;
- native score count and class fractions;
- complete substitution score count and class fractions;
- mean, median, minimum, maximum, sample standard deviation, Q1, Q3, and IQR;
- per-entity and per-chain summaries;
- per-residue substitution summaries;
- per-residue alternative-class burden;
- per-amino-acid substitution distributions and class composition;
- ranked substitutions with stable tie-breaking;
- contiguous region summaries over source-authoritative residue order;
- explicit denominator and missingness reason for every statistic.

Raw scores, native-slot summaries, and all-substitution summaries remain separate.
This tranche provides descriptive statistics only. It offers no inferential test, so a multiple-comparison correction is not applicable. A later inferential method must define its uncertainty and multiplicity policy before release.

Add bounded endpoints:

- `GET /api/frustrampnn/jobs/{job_id}/results/{invocation_id}/statistics`
- `POST /api/frustrampnn/statistics/query`

The query body uses typed dataset references, level, grouping, metric, filters, and page bounds. It cannot accept expressions, SQL, Python, or arbitrary code.

Retain `/analytics/points` for bounded machine-readable point retrieval. Add settings and compatibility fields where absent.

### 7.4 Comparison compatibility

Update `platform/api/services/frustrampnn/comparison.py`.

Return field-level compatibility:

- raw-score compatibility depends on checkpoint hash, output schema, normalization semantics, and selection identity coverage;
- class compatibility also requires the threshold-policy hash;
- residue alignment requires exact source-authoritative identity mapping;
- incompatible fields are listed by name and hash;
- class transitions are omitted when class policies differ;
- raw-score deltas may remain available only when raw-score compatibility passes.

Never hide a mismatch behind a single opaque configuration digest.

**G4 exit:** new results are reconstructable from database records and immutable artifacts. Statistics match exact row denominators. Comparisons state what is compatible and why.

## 8. Reusable result workbench

### 8.1 Component boundary

Refactor without duplicating numerical authority:

- `platform/frontend/src/components/FrustraMpnnResultsViewer.tsx` becomes the global workbench shell;
- `FrustraMpnnPlotlyAnalytics.tsx` owns charts only;
- `FrustraMpnnComparisonSurface.tsx` owns persisted comparison presentation;
- `FrustraMpnnCrossDatasetExplorer.tsx` consumes the typed statistics/points APIs;
- `FrustraMpnnCandidateHandoffPanel.tsx` remains a handoff display and does not gain mutation execution in this tranche.

Create:

- `platform/frontend/src/components/frustrampnn/FrustraMpnnWorkbench.tsx`
- `FrustraMpnnStatisticsPanel.tsx`
- `FrustraMpnnReviewPanel.tsx`
- `FrustraMpnnExportPanel.tsx`
- `frustraMpnnViewState.ts`

### 8.2 Required workbench behavior

The same shell must provide:

- data identity, source workflow, candidate, runtime, checkpoint, settings, and provenance header;
- exact residue table and 20-amino-acid substitution matrix;
- Mol* residue selection and score coloring;
- linked table, matrix, chart, and structure selection;
- native and complete-landscape statistics;
- missingness and excluded-residue diagnostics;
- per-chain, region, and cross-result exploration;
- persisted pairwise and multi-result comparisons;
- typed filters and deterministic sort order;
- saved review state;
- review annotations;
- PNG/SVG chart capture;
- structure-view capture through the existing governed viewer capture capability where available;
- CSV and JSON exports generated from persisted server authority;
- visible effective settings and compatibility identities.

Frontend code must not recompute classification thresholds or scientific statistics.

### 8.3 Review and artifact APIs

Add:

- `POST /api/frustrampnn/reviews`
- `GET /api/frustrampnn/reviews/{review_id}`
- `GET /api/frustrampnn/jobs/{job_id}/reviews`
- `POST /api/frustrampnn/reviews/{review_id}/captures`
- `POST /api/frustrampnn/reviews/{review_id}/exports`
- `GET /api/frustrampnn/reviews/{review_id}/artifacts/{artifact_id}`

The view-state schema includes selected result IDs, residue identities, mutation identities, filters, sorting, chart axes, structure coloring, comparison IDs, and workbench section state. Unknown fields fail.

Capture uploads require expected SHA-256 and bounded media types. Server-generated exports bind query, row count, source result hashes, effective settings hashes, and content hash.

Clone and replay use persisted typed settings. Replay creates a new immutable child with a new invocation identity. A caller may supply one complete replacement settings object. Partial override objects are rejected.

### 8.4 Producer-neutral entry

`ResultsViewer.tsx` must open `FrustraMpnnWorkbench` using persisted result identities. It must not branch into reduced workflow-specific FrustraMPNN viewers.

Acceptance in this tranche covers:

- one uploaded-structure child result;
- one existing integrated Structure Prediction result rendered through the same component.

RFD3 and Conformational Mapping adoption occur in later priorities.

**G5 exit:** a saved review can be restored and reproduces the same data selection, structure focus, chart state, settings identity, and export authority for either accepted producer type.

## 9. Succinct test denominator

Tests are phase gates. Run only owners changed in that phase.

### G0 and G1

Backend tests:

- capability inventory accounts for every installed `predict` argument;
- settings schema accepts canonical defaults and one valid non-default object;
- unknown, contradictory, non-finite, unavailable, and empty selections fail;
- canonical ordering produces stable hashes;
- v1 reads remain valid and v2 is required for new writes.

Files:

- `platform/api/tests/test_frustrampnn_capability_inventory.py`
- `platform/api/tests/test_frustrampnn_settings.py`
- existing global configuration and request-contract tests, updated rather than duplicated.

### G2

API tests:

- integration projection and settings validation use the same defaults and schema;
- upload, design, and reanalysis routes accept the same typed settings;
- infrastructure fields and unknown fields fail;
- round-trip serialization retains exact values.

Frontend tests:

- every capability-inventory operator field maps to one visible control when enabled;
- disabled hides settings;
- saved explicit values survive delayed metadata;
- UI submission equals the API fixture object;

Use existing Node tests and helpers. Do not add a frontend test framework.

### G3

Adapter and contract tests:

- all-entity request emits no chain or position override;
- selected entities compile exact `--chains`;
- chain-specific residue selection creates the expected per-chain argv plan;
- merged raw CSV has exact canonical order and `N × 20` rows;
- partial, duplicate, non-finite, stale-map, or wrong-chain output fails;
- receipt argv, hashes, and effective settings agree.

Use fixture CSV data. Do not run model inference during unit tests.

Run the smallest Nextflow static or stub contract only when `.nf` files change.

### G4

Persistence and statistics tests:

- forward migration preserves a legacy database and is idempotent;
- result, settings, statistics, and rows commit atomically;
- deterministic statistics match one small hand-calculated fixture;
- native and complete-substitution denominators remain separate;
- missingness propagates;
- comparison compatibility differs correctly for threshold-only and checkpoint mismatches.

### G5

Frontend/API tests:

- uploaded and integrated results choose the same workbench;
- linked residue selection uses full residue identity;
- API statistics render without frontend recomputation;
- saved view round-trip is exact;
- export rows and hashes match persisted authority;
- capture rejects a wrong hash or media type.

### Explicit exclusions

Do not run:

- the full backend suite;
- the full frontend suite;
- a browser matrix;
- the former ten-case campaign;
- mutation, RFD3, nanobody, or Conformational Mapping scientific jobs;
- repeated stochastic qualification.

## 10. Development acceptance

Run acceptance only after the exact candidate is committed to `test`, deployed to Development, and the deployed SHA, source tree, process owners, listeners, and database are proven.

### One enabled scientific job

Use the governed uploaded 1UBQ structure path with an explicit non-default residue selection that spans at least two residues. Keep the canonical checkpoint. Use explicit canonical thresholds or one bounded custom policy if the selected values have been reviewed.

Required evidence:

1. API capability-inventory hash and settings schema.
2. Submitted requested settings.
3. Pre-queue validated effective settings.
4. Scheduler parent/child identity and lineage.
5. Assigned physical GPU and task-visible `cuda:0` receipt.
6. Exact image, executable, checkpoint, source, normalized structure, request, raw CSV, landscape, summary, statistics, and manifest hashes.
7. Exact per-chain argv plan.
8. Exactly `selected scoreable residues × 20` unique finite substitution rows.
9. One native slot per selected residue.
10. Persisted statistics with matching denominators.
11. Workbench table, matrix, chart, and Mol* selection linked to one exact residue.
12. One saved review restored after page reload.
13. One CSV or JSON export with persisted hash.
14. One chart or structure capture with persisted hash.
15. One machine-readable acceptance receipt and one browser screenshot.

### Disabled path

Submit one governed request-level Structure Prediction opt-out:

- `run_frustrampnn: false`;
- zero FrustraMPNN task;
- paired `not_requested` and empty stage output;
- parent completion remains valid.

This request schedules no FrustraMPNN inference and remains the only disabled-path acceptance request.

### Failure evidence

Reuse current-build focused contract evidence that a required FrustraMPNN failure blocks success. Run a live failure only when this behavior changed or current-build evidence is absent.

**G6 exit:** the exact deployed revision has one real scheduler/GPU result that proves a non-default scientific selection and the complete persistence-to-review path.

## 11. Ordered work packages

### Phase 0: capability lock

Deliver G0 inventory and source report.

**Files:** inventory schema, inventory JSON, report, inventory validation test.

**Exit:** all installed arguments classified. Any undocumented installed argument blocks the phase.

### Phase 1: typed settings authority

Deliver settings models, effective settings resolution, hashes, v2 schemas, and historical v1 reads.

**Files:** `settings.py`, `configuration.py`, `contracts.py`, four schemas, focused tests.

**Exit:** G1 passes. No runtime or UI edits begin before the authority is stable.

### Phase 2: API and UI parity

Deliver capability projection, source inspection, validation, launch DTOs, settings panel, and minimum Structure Prediction transport.

**Files:** model registry/config, model and FrustraMPNN routers, jobs service, API clients, settings components, Structure Prediction request/UI owners.

**Exit:** G2 passes with one shared request fixture used by API and frontend tests.

### Phase 3: exact runtime transport

Deliver source-policy normalization, selector resolution, chain-specific invocation plans, deterministic merge, and receipts.

**Files:** structure service, candidate preparation scripts, runtime, component adapter, canonical Nextflow module, Structure Prediction workflow.

**Exit:** G3 passes. Every requested setting is present in the effective configuration and every model setting has command-level evidence.

### Phase 4: persistence and statistics

Deliver forward migration, database fields/tables, atomic ingestion, statistics service/schema/API, and field-level comparison compatibility.

**Files:** new migration, migration runner, database models, ingester, manifests, statistics, analytics, comparison, router.

**Exit:** G4 passes on fresh and legacy database fixtures.

### Phase 5: global workbench

Deliver the reusable shell, typed statistics panels, linked views, saved reviews, captures, exports, and producer-neutral routing.

**Files:** FrustraMPNN frontend components, view-state helper, API client, review/artifact routes and services.

**Exit:** G5 passes for one uploaded and one integrated persisted fixture.

### Phase 6: exact-tree review and Development acceptance

1. Reconcile against current `origin/test` without overwriting unrelated dirty work.
2. Run only the phase-owner test denominator.
3. Review the exact diff for specification adherence and numerical authority.
4. Commit and push `test` only with Christian's release authorization.
5. Restart only the managed Development API/frontend owners that changed.
6. Prove deployed SHA, tree, process, listener, and database.
7. Run the single enabled job and disabled-path proof.
8. Publish the acceptance receipt.

**Exit:** G6 passes. `main` and production remain unchanged.

## 12. Prompt issue reporting

Stop the affected phase and report immediately when any of these occurs:

- the installed CLI differs from the G0 inventory;
- the image or checkpoint hash drifts;
- a requested setting cannot map deterministically to source or model identity;
- source metadata cannot describe models, alternate locations, entities, chains, or residues without ambiguity;
- the vendor CLI produces partial or non-finite selected rows;
- a schema or migration would require destructive historical rewriting;
- the frontend would need to reproduce scientific calculations;
- a workflow requires a separate FrustraMPNN numerical or viewer authority;
- Development services, scheduler, GPU, or governed fixture are unavailable for G6;
- an issue requires mutation, RFD3, Conformational Mapping, nanobody, LLM, production, or unrelated repository changes.

Rectify a local contract or implementation defect inside the current phase when the fix is bounded and directly required by its gate. Report broader findings as scope deviations before any work.

## 13. Completion ledger

| Gate | Required evidence | Status before implementation |
|---|---|---|
| G0 | Exact installed capability inventory | Partial discovery; artifact absent |
| G1 | Complete typed settings authority | Absent |
| G2 | Human/API parity | Toggle and metadata only |
| G3 | Exact settings-to-runtime proof | Fixed defaults only |
| G4 | Persisted settings and complete statistics | Exact rows and basic summaries exist; closure absent |
| G5 | Reusable saved/captured/exportable workbench | Strong viewer base; persistence closure absent |
| G6 | Current Development acceptance | Historical evidence only |

Existing runtime, normalization, exact-row persistence, comparison, and viewer code should be extended rather than replaced. Completion is the verified outcome across these gates.
