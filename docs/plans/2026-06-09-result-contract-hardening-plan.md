# Result/Data Viewer Contract Hardening Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Finish the Results/Data Viewer final spec so adding new models is opt-in, isolated, fail-closed, and cannot silently break existing model data streams.

**Architecture:** Move from loose result-set inference toward an explicit backend result-contract registry that declares model/stage/artifact compatibility, required artifacts/metrics, supported analyzers, and viewer capabilities. Frontend panels and filters should render from API-provided capabilities instead of metric-name guessing. Legacy heuristics remain only as backfill and must be marked as inferred/lower-confidence.

**Tech Stack:** Python/FastAPI/SQLAlchemy/Pydantic API, TypeScript/React frontend, Node source-inspection contract tests, pytest backend tests, existing BioModStack Nextflow/workflow artifacts.

---

## Non-negotiable spec rules

1. Unknown model or unknown contract must fail closed:
   - `analysis_contract_id: null`
   - `supported_analyzers: []`
   - no specialized viewer panels/actions
   - generic metadata/artifact rows only
2. Shared analyzers are explicit opt-in only. Similar field names are not enough.
3. Adding a model must not reclassify or break existing streams:
   - RFA/backbone
   - FA-MPNN / ProteinMPNN / AntiFold / Caliby sequence design
   - PPIFlow maturation candidates/passed/rejected
   - Boltz/Protenix/ESMFold2 structure validation/imports
   - ConforNets monomer outputs
   - imported datasets
4. Metric claims must stay honest:
   - FA-MPNN pSCE = sidechain QC, not binding evidence
   - BMS PPIFlow objective = local heuristic, not paper rank
   - Rosetta InterfaceAnalyzer score = raw REU/sign convention explicit
   - DockQ/template-free refold = optional completeness input, not faked
   - AF3Score is not planned because it is bugged/unreliable

---

## Phase 0: Freeze current baseline and avoid scope drift

### Task 0.1: Confirm clean active baseline before edits

**Objective:** Prevent mixing unrelated dirty work into contract hardening.

**Files:** none

**Steps:**

```bash
cd /home/dalab/biomodstack/biomodstack
git status -sb --untracked-files=all
git log --oneline --decorate -8
```

**Expected:** no dirty result/data-viewer source files before starting; only explicitly unrelated docs may be present.

**Commit:** none.

---

## Phase 1: Replace ad-hoc contract resolver with explicit registry

### Task 1.1: Add typed contract registry models

**Objective:** Make model/result contracts data-driven and inspectable.

**Files:**
- Modify: `platform/api/services/result_contracts.py`
- Test: `platform/api/tests/test_result_contracts.py`

**Test first:** add assertions that each registry entry exposes:
- `model_ids`
- `stage_families`
- `artifact_classes`
- `result_sets`
- `analysis_contract_id`
- `supported_analyzers`
- `viewer_capabilities`
- `required_fields`
- `required_artifacts`
- `schema_version`

**Implementation shape:**

```python
class ResultContractDefinition(BaseModel):
    contract_id: str
    schema_version: int = 1
    model_ids: list[str] = Field(default_factory=list)
    stage_families: list[str] = Field(default_factory=list)
    stage_modes: list[str] = Field(default_factory=list)
    artifact_classes: list[str] = Field(default_factory=list)
    result_sets: list[str] = Field(default_factory=list)
    supported_analyzers: list[str] = Field(default_factory=list)
    viewer_capabilities: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    required_artifacts: list[str] = Field(default_factory=list)
    notes: str = ""
```

**Run:**

```bash
uv run --directory platform/api python -m pytest tests/test_result_contracts.py -q
```

**Expected:** new tests fail before implementation, pass after.

**Commit:** `feat(results): define explicit result contract registry`

### Task 1.2: Populate contracts for current known families

**Objective:** Encode current supported streams without adding new behavior.

**Files:**
- Modify: `platform/api/services/result_contracts.py`
- Test: `platform/api/tests/test_result_contracts.py`

**Contracts to add:**
- `antibody_backbone_v1`
  - result sets: `rfantibody_backbones`
  - analyzer: `antibody_backbone_v1`
- `sequence_design_v1`
  - result sets: `sequence_designs`
  - model/stage families: `fampnn`, `proteinmpnn`, `antifold`, `frustrampnn`, `caliby`, `boltzgen` only if intentionally supported
  - analyzer: `sequence_design_v1`
- `ppiflow_maturation_v1`
  - result sets: `ppiflow_candidates`, `ppiflow_passed`, `ppiflow_rejected`
  - analyzer: `ppiflow_maturation_v1`
- `structure_prediction_v1`
  - families/model ids: `boltz2`, `protenix`, `esmfold2`, existing validation rows
  - analyzer: `structure_prediction_v1`
- `confornets_monomer_v1`
  - supported analyzers initially empty unless we intentionally add one

**Run:**

```bash
uv run --directory platform/api python -m pytest tests/test_result_contracts.py tests/test_design_import_metadata.py -q
```

**Commit:** `feat(results): register known model result contracts`

### Task 1.3: Mark legacy inference as backfill only

**Objective:** Preserve old rows while making inference provenance explicit.

**Files:**
- Modify: `platform/api/routers/designs.py`
- Modify: `platform/api/services/result_contracts.py`
- Test: `platform/api/tests/test_design_import_metadata.py`

**Behavior:**
- If row has explicit contract metadata, use it.
- If row is classified by `_infer_design_result_set`, include something like:
  - `result_contract_source: "legacy_inferred"`
  - or inside `metric_completeness` / response metadata, `contract_source: "legacy_inferred"`
- Unknown rows stay unsupported.

**Run:**

```bash
uv run --directory platform/api python -m pytest tests/test_design_import_metadata.py tests/test_result_contracts.py -q
```

**Commit:** `fix(results): label legacy inferred result contracts`

---

## Phase 2: Expand fail-closed and non-disruption regression matrix

### Task 2.1: Add unknown-model misleading-field regression tests

**Objective:** Prove metric-shaped payloads do not activate unsupported analyzers.

**Files:**
- Modify: `platform/api/tests/test_result_contracts.py`
- Modify: `platform/api/tests/test_design_import_metadata.py`

**Cases:** unknown model row containing misleading fields:
- `plddt`
- `ptm`
- `fampnn_psce`
- `ppiflow_objective_score`
- `rmsd`
- `score`

**Expected:**
- `analysis_contract_id is None`
- `supported_analyzers == []`
- no result set unless explicit known contract matches

**Run:**

```bash
uv run --directory platform/api python -m pytest tests/test_result_contracts.py tests/test_design_import_metadata.py -q
```

**Commit:** `test(results): fail closed for unknown metric-shaped models`

### Task 2.2: Add known-stream non-regression fixtures

**Objective:** Prove adding new contracts does not reclassify existing streams.

**Files:**
- Modify: `platform/api/tests/test_result_contracts.py`
- Modify: `platform/api/tests/test_design_import_metadata.py`

**Fixtures:** create minimal `Design(...)` rows for:
- RFA/backbone
- FA-MPNN sequence design
- ProteinMPNN sequence design
- AntiFold sequence design
- Caliby sequence design
- PPIFlow candidate
- PPIFlow passed
- PPIFlow rejected
- Boltz/Protenix/ESMFold2 validation/import
- ConforNets monomer
- external/imported unknown dataset

**Expected:** exact `result_set`, `analysis_contract_id`, `supported_analyzers` are pinned.

**Run:**

```bash
uv run --directory platform/api python -m pytest tests/test_result_contracts.py tests/test_design_import_metadata.py -q
```

**Commit:** `test(results): pin known model stream contracts`

---

## Phase 3: Make frontend capability-driven

### Task 3.1: Extend TypeScript API contract fields

**Objective:** Type all contract metadata frontend needs.

**Files:**
- Modify: `platform/frontend/src/lib/api.ts`
- Test: existing TypeScript compile

**Fields:** ensure `Design` includes:
- `analysis_contract_id?: string | null`
- `supported_analyzers?: string[]`
- `result_contract_source?: string | null` if added
- `metric_completeness?: ...`
- `metric_provenance?: ...`

**Run:**

```bash
cd platform/frontend
./node_modules/.bin/tsc -p tsconfig.tests.json
./node_modules/.bin/tsc -b
```

**Commit:** `feat(frontend): type result contract metadata`

### Task 3.2: Add capability helpers

**Objective:** Centralize panel gating so components do not guess from metric names.

**Files:**
- Create or modify: `platform/frontend/src/lib/resultCapabilities.ts`
- Test: `platform/frontend/tests/resultCapabilities.test.ts`

**Implementation shape:**

```ts
export function supportsAnalyzer(design: Design, analyzer: string): boolean {
  return Array.isArray(design.supported_analyzers) && design.supported_analyzers.includes(analyzer);
}

export function isUnsupportedResult(design: Design): boolean {
  return !design.analysis_contract_id || !design.supported_analyzers?.length;
}
```

**Tests:**
- unknown design with pLDDT/pSCE-looking fields remains unsupported
- PPIFlow design supports only PPIFlow analyzer
- sequence design supports sequence analyzer

**Run:**

```bash
cd platform/frontend
rm -rf node_modules/.tmp/frontend-tests
./node_modules/.bin/tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/resultCapabilities.test.js
```

**Commit:** `feat(frontend): gate result panels by contract capabilities`

### Task 3.3: Wire ResultsViewer section visibility to capabilities

**Objective:** Stop rendering specialized panels/actions based on metric-name guesses.

**Files:**
- Modify: `platform/frontend/src/components/ResultsViewer.tsx`
- Test: `platform/frontend/tests/resultsViewerResultSetContract.test.ts` or new `resultsViewerCapabilitiesContract.test.ts`

**Rules:**
- Unsupported rows show generic metadata/artifact availability only.
- `sequence_design_v1` can show sequence-design summaries.
- `ppiflow_maturation_v1` can show PPIFlow maturation summaries.
- `antibody_backbone_v1` can show RFA/backbone summaries.
- Structure/confidence panels require `structure_prediction_v1` or explicitly compatible analyzer.

**Run:**

```bash
cd platform/frontend
rm -rf node_modules/.tmp/frontend-tests
./node_modules/.bin/tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/resultsViewerResultSetContract.test.js
./node_modules/.bin/tsc -b
```

**Commit:** `fix(frontend): render result analysis from capabilities`

---

## Phase 4: Finish metric honesty and maturation scoring contracts

### Task 4.1: Commit/finish Rosetta InterfaceAnalyzer wiring

**Objective:** Make Rosetta interface score a real explicit metric, not a TODO.

**Files:**
- Modify: `scripts/score_maturation.py`
- Modify: `scripts/test_score_maturation.py`
- Modify: `platform/api/routers/designs.py`
- Modify: `platform/frontend/src/lib/api.ts`
- Modify: `platform/frontend/src/lib/metricRegistry.ts`

**Acceptance:**
- output JSON includes `rosetta_interface_score`, `rosetta_interface_dg`, unit `REU`, direction `more_negative_is_better`, `rosetta_interface_analyzer_used`
- API surfaces those fields
- frontend metric registry labels it as raw Rosetta InterfaceAnalyzerMover dG

**Run:**

```bash
python3 -m pytest scripts/test_score_maturation.py -q
uv run --directory platform/api python -m pytest tests/test_design_metric_provenance.py tests/test_design_import_metadata.py -q
cd platform/frontend && ./node_modules/.bin/tsc -p tsconfig.tests.json && node --test node_modules/.tmp/frontend-tests/tests/metricRegistry.test.js
```

**Commit:** `feat(maturation): expose Rosetta interface analyzer metrics`

### Task 4.2: Decide DockQ/refold semantics before implementation

**Objective:** Avoid fake DockQ support.

**Files:**
- Create or modify: `docs/metrics/ppiflow_metrics.md`
- Optional test: `tests/test_reconcile_ppiflow_ranking.py`

**Decision note:** DockQ is only meaningful when there is a valid reference/native or a defined template-free refold comparison. If we do not have that reference/refold artifact, completeness should keep reporting `ppiflow_dockq_or_template_free_refold` missing.

**Acceptance:** docs and completeness tests state that DockQ/refold is optional and unavailable unless explicitly computed.

**Commit:** `docs(metrics): define DockQ and refold completeness semantics`

---

## Phase 5: Live validation ledger

### Task 5.1: Add local validation script for real persisted jobs

**Objective:** Produce repeatable API evidence for each bucket/model family.

**Files:**
- Create: `scripts/audit_result_contracts.py`
- Test: optional unit test for parser/output shape

**Script behavior:**
- Query local API or directly inspect DB/session if API not reachable.
- Summarize counts by:
  - `result_set`
  - `analysis_contract_id`
  - `supported_analyzers`
  - `stage_family`
  - `artifact_class`
- Print unsupported rows separately.

**Run:**

```bash
python3 scripts/audit_result_contracts.py --job-id <known-job-id> --json
```

**Commit:** `tools(results): add result contract audit script`

### Task 5.2: Browser smoke on stable `/bms/`

**Objective:** Prove live UI uses the contract fields without JS errors.

**Manual/browser acceptance:**
- open known job with RFA/sequence/PPIFlow rows
- verify result-set buttons/counts
- click each bucket
- verify unsupported rows show no specialized panels
- console errors: 0

**Files:**
- Create: `docs/evidence/result-contract-validation-YYYYMMDD.md`

**Commit:** `docs(results): record live result contract validation`

---

## Phase 6: Commit/push hygiene

### Task 6.1: Commit only result-contract work

**Objective:** Avoid mixing unrelated MolBio/BioXP/docs changes.

**Command pattern:**

```bash
cd /home/dalab/biomodstack/biomodstack
git status -sb --untracked-files=all
git diff --name-status
git restore --staged :/
git add -A -- \
  platform/api/services/result_contracts.py \
  platform/api/routers/designs.py \
  platform/api/tests/test_result_contracts.py \
  platform/api/tests/test_design_import_metadata.py \
  platform/api/services/design_metrics.py \
  platform/frontend/src/lib/api.ts \
  platform/frontend/src/lib/resultCapabilities.ts \
  platform/frontend/src/lib/metricRegistry.ts \
  platform/frontend/src/components/ResultsViewer.tsx \
  platform/frontend/tests/resultCapabilities.test.ts \
  platform/frontend/tests/resultsViewerResultSetContract.test.ts \
  scripts/score_maturation.py \
  scripts/test_score_maturation.py \
  docs/metrics/ppiflow_metrics.md \
  docs/plans/2026-06-09-result-contract-hardening-plan.md
git diff --cached --check
git diff --cached --stat
git commit -m "feat(results): harden contract-driven viewer support"
```

**Final verification before push:**

```bash
uv run --directory platform/api python -m pytest \
  tests/test_result_contracts.py \
  tests/test_design_import_metadata.py \
  tests/test_design_metric_provenance.py -q
python3 -m pytest scripts/test_score_maturation.py -q
cd platform/frontend && rm -rf node_modules/.tmp/frontend-tests && ./node_modules/.bin/tsc -p tsconfig.tests.json && node --test node_modules/.tmp/frontend-tests/tests/resultCapabilities.test.js node_modules/.tmp/frontend-tests/tests/resultsViewerResultSetContract.test.js node_modules/.tmp/frontend-tests/tests/metricRegistry.test.js && ./node_modules/.bin/tsc -b
```

---

## Recommended execution order

1. Phase 1 + Phase 2 first: backend registry + regression matrix.
2. Phase 3 second: frontend capability gating.
3. Phase 4 third: Rosetta metric completion and DockQ/refold docs.
4. Phase 5 last: live API/UI validation evidence.
5. Phase 6: narrow commit/push only after all targeted checks pass.

## What not to do

- Do not add AF3Score wiring.
- Do not classify unknown models by matching field names.
- Do not show specialized panels/actions for unsupported contracts.
- Do not split sequence-design subfamilies unless we intentionally decide UI needs separate buttons; grouped `Sequence designs` is acceptable if contract metadata still records the model/stage family.
- Do not commit unrelated MolBio header/CSS or BioXP docs with this work.
