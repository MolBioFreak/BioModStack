# RFantibody Modifiable-AA pLDDT Scope Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Ensure BioModStack reports, ranks, plots, and Mol*/Data Viewer-colors RFantibody/RFA pLDDT over only the residues the workflow marked as modifiable/designed, falling back to whole-structure pLDDT only when no modifiable-residue scope exists.

**Architecture:** Extend the existing backend-owned RFantibody metadata path into a reusable “modifiable residue scope” contract shared by ingestion, API payloads, result metrics, plots, and viewer coloring. Compute scoped pLDDT from the raw `.trb["plddt"]` vector using PDB residue mapping, preserve whole-structure pLDDT as an explicitly labeled background metric, and make frontend metric/viewer helpers consume the scoped contract. The MolstarViewer should remain a generic PDBe Mol* adapter using explicit `residueColors`; the fix is to pass it a residue color map filtered upstream to the modifiable scope, never the whole structure, when the selected workflow requested subset design.

**Reuse/scaling rule:** Do not add RFA-only viewer plumbing when a common backend/API/frontend contract can express the same concept. New work should extend shared contracts/helpers (`rfantibody_metadata.py` scope extraction, `Design` API aliases, `structureViewerSemantics.ts` color-map semantics, `MolstarViewer` explicit residue-color adapter) so future subset-design workflows can reuse the same modifiable-residue confidence scope instead of accumulating one-off Mol* or Plotly paths.

**Tech Stack:** Python/FastAPI/SQLAlchemy/Pydantic backend, RFantibody `.trb` pickle parsing, React/TypeScript, PDBe Mol*/MolstarViewer explicit residue selection coloring, Node `node:test` frontend harness.

---

## Problem statement

The current RFA path stores and exposes all-residue `.trb["plddt"]` as `plddt_overall` / `rfa_plddt_final`. In the verified 50-backbone TdT/RFA job, that all-residue mean is dominated by unchanged target/framework residues:

- `plddt_overall`: mean ~99.58
- target-chain mean: mean ~99.90
- antibody non-designed/framework mean: mean ~99.45
- selected/designed-loop mean: mean ~93.51

This makes result cards, Plotly metrics, sorting, and pLDDT coloring semantically wrong for workflows that only modified selected residues.

## Required semantics

1. **Primary RFA design confidence = modifiable-residue pLDDT.**
   - If the workflow requested specific modifiable residues/loops, compute pLDDT only on that scope.
   - For RFantibody CDR jobs, this means selected loops such as `H1`, `H2`, `H3`, `L1`, `L2`, `L3` from the workflow/TRB config.
   - For future workflows, the same contract must support arbitrary chain/residue ranges if those are the modifiable AA set.

2. **Whole-structure pLDDT remains available but not the default.**
   - Keep all-residue value as explicitly named background metric, e.g. `rfa_plddt_all_residue` / `rfa_plddt_final_all_residue`.
   - Do not label it generic `pLDDT Overall` on RFA cards/charts when a modifiable scope exists.

3. **Mol*/Data Viewer mask must respect the same modifiable scope.**
   - If RFA requested selected/modifiable residues, pLDDT color mode colors only those residues.
   - Non-selected residues should be grey/dim/neutral via `nonSelectedColor`, not pLDDT-colored.
   - If no modifiable residue scope exists, existing whole-structure pLDDT coloring may remain.

4. **No fake/demo placeholders.**
   - If the `.trb`/PDB cannot be mapped to modifiable residues, expose a scoped-confidence status/error and do not synthesize a fake scoped score.

---

## Proposed data contract

Add a canonical scope object to design rows/API payloads. Prefer storing it in existing JSON/provenance first unless a migration already exists for new Design columns.

```json
{
  "confidence_scope": {
    "metric_family": "rfantibody_plddt",
    "primary_scope": "modifiable_residues",
    "source": "rfantibody_trb_config.antibody.design_loops",
    "modifiable_residues": [
      { "chain_id": "H", "residue_number": 26, "loop_id": "H1" },
      { "chain_id": "H", "residue_number": 27, "loop_id": "H1" }
    ],
    "modifiable_ranges": [
      { "chain_id": "H", "start_residue_number": 26, "end_residue_number": 32, "label": "H1" }
    ],
    "counts": {
      "all_residue_count": 627,
      "modifiable_residue_count": 23,
      "nonmodifiable_residue_count": 604
    },
    "plddt": {
      "primary": 93.51,
      "all_residue": 99.58,
      "modifiable": 93.51,
      "nonmodifiable": 99.84,
      "target": 99.90,
      "framework": 99.45
    },
    "status": "ok"
  }
}
```

Backend field recommendations:

- Keep existing fields for compatibility:
  - `rfa_plddt_final` = current all-residue final mean for now, or migrate carefully.
  - `rfa_plddt_selected` = modifiable-residue mean.
  - `rfa_plddt_nonselected` = currently all non-selected including target; clarify or replace.
- Add clearer API aliases:
  - `rfa_plddt_primary`
  - `rfa_plddt_modifiable`
  - `rfa_plddt_all_residue`
  - `rfa_plddt_nonmodifiable`
  - `rfa_plddt_framework`
  - `rfa_plddt_target`
  - `rfa_modifiable_residues`
  - `rfa_modifiable_ranges`
  - `rfa_confidence_scope`

If avoiding DB migration in first PR, put the new fields under:

- `Design.confidence_metrics.rfantibody.confidence_scope`
- `Design.provenance.rfantibody.confidence_scope`

and expose flattened aliases in `DesignResponse` later.

---

## Task 1: Backend pure helper for modifiable-residue scope

**Objective:** Extract RFantibody `.trb` pLDDT into all-residue, modifiable, target, framework, and nonmodifiable groups from PDB residue labels.

**Files:**
- Modify: `platform/api/services/rfantibody_metadata.py`
- Test: `platform/api/tests/test_rfantibody_metadata.py` or existing RFantibody metadata test file

**Implementation detail:**

Add helper types/functions:

```python
@dataclass(frozen=True)
class ResidueConfidencePoint:
    chain_id: str
    residue_number: int
    insertion_code: str
    plddt: float
    loop_ids: tuple[str, ...] = ()


def _resolve_modifiable_residue_keys(
    residue_order: list[tuple[str, int, str]],
    loop_labels: dict[tuple[str, int], set[str]],
    selected_loops: Optional[list[str]],
) -> set[tuple[str, int, str]]:
    ...
```

Rules:

- Normalize selected loop tokens from TRB/workflow (`H1:7-10`, `H2`, etc.) to loop IDs first.
- Use `REMARK PDBinfo-LABEL:` mapping to identify loop residue keys.
- Return empty scope if no selected loops/residue selectors exist.
- Do **not** include target chain residues in framework/nonselected antibody metrics.
- Compute:
  - `modifiable_mean`
  - `nonmodifiable_mean`
  - `framework_mean` = antibody H/L residues not modifiable
  - `target_mean` = non-H/L residues
  - counts and residue lists/ranges

**Failing tests first:**

- synthetic PDB with chains `T` and `H`, `REMARK PDBinfo-LABEL:` for H1/H2/H3, synthetic pLDDT vector with target/framework at 100 and loops at 90.
- Assert:
  - all-residue mean is high.
  - modifiable mean equals loop-only values.
  - target mean excludes antibody.
  - framework mean excludes target and selected loops.
  - returned `modifiable_residues` contains only H loop residues.

**Verification command:**

```bash
uv run --directory platform/api python -m pytest tests/test_rfantibody_metadata.py -q
```

Expected: new tests pass.

---

## Task 2: Change RFantibody ingestion/API semantics without breaking old rows

**Objective:** Store/expose the scoped confidence contract and make `confidence_metrics.rfantibody.plddt_primary` use modifiable residues when scope exists.

**Files:**
- Modify: `platform/api/services/rfantibody_metadata.py`
- Modify: `platform/api/services/result_ingester.py`
- Modify: `platform/api/services/stage_review.py`
- Modify: `platform/api/routers/designs.py`
- Test: `platform/api/tests/test_api_analysis_ingest_review_regressions.py` and/or RFantibody ingest tests

**Implementation detail:**

In `load_rfantibody_trb_summary(...)`, return:

```python
"rfa_plddt_all_residue": final_mean,
"rfa_plddt_modifiable": scoped.modifiable_mean,
"rfa_plddt_primary": scoped.modifiable_mean if scoped.modifiable_count else final_mean,
"rfa_plddt_nonmodifiable": scoped.nonmodifiable_mean,
"rfa_plddt_framework": scoped.framework_mean,
"rfa_plddt_target": scoped.target_mean,
"rfa_modifiable_residues": scoped.modifiable_residues,
"rfa_modifiable_ranges": scoped.modifiable_ranges,
"rfa_confidence_scope": scoped.as_dict(),
```

For backward compatibility:

- Keep `rfa_plddt_selected = rfa_plddt_modifiable`.
- Keep `rfa_plddt_final = final_mean` until a migration/rename is done.
- In `confidence_metrics.rfantibody`, add:
  - `plddt_primary`
  - `plddt_modifiable`
  - `plddt_all_residue`
  - `plddt_nonmodifiable`
  - `plddt_framework`
  - `plddt_target`
  - `confidence_scope`

**Important:** Do not set generic top-level `plddt_overall` to all-residue pLDDT for RFA if a modifiable scope exists. Either:

- set `plddt_overall = rfa_plddt_primary` for RFA rows, or
- leave `plddt_overall` as is but make every RFA UI/analytics surface resolve primary confidence from `confidence_metrics.rfantibody.plddt_primary` and label all-residue separately.

Preferred durable direction: **set RFA `plddt_overall` to primary scoped pLDDT only when scope exists**, and expose `rfa_plddt_all_residue` for audit/background. This makes generic sort/filter safer.

**Failing tests first:**

- Ingest synthetic/fixture RFA design where target/framework pLDDT = 100 and designed loops = 90.
- Assert API row exposes:
  - `rfa_plddt_primary == 90`
  - `rfa_plddt_modifiable == 90`
  - `rfa_plddt_all_residue > 95`
  - `confidence_metrics.rfantibody.confidence_scope.primary_scope == "modifiable_residues"`
  - generic/default pLDDT used for ranking is not the all-residue mean when scope exists.

**Verification command:**

```bash
uv run --directory platform/api python -m pytest tests/test_api_analysis_ingest_review_regressions.py tests/test_designs_router.py -q
```

Expected: targeted backend tests pass.

---

## Task 3: Plotly/analysis metric resolver uses scoped RFA pLDDT

**Objective:** Ensure Plotly/data-analysis surfaces do not plot RFA all-residue pLDDT as generic `pLDDT Overall` when modifiable scope exists.

**Files:**
- Modify: `platform/api/routers/designs.py`
- Test: relevant Plotly metrics router tests, or add `platform/api/tests/test_design_plotly_metrics.py`

**Implementation detail:**

Add a resolver used by `/api/designs/by-job/{job_id}/plotly-metrics` and any analytics metadata builder:

```python
def resolve_primary_plddt_for_plotly(design: Design) -> tuple[Optional[float], str, str]:
    rfa = (design.confidence_metrics or {}).get("rfantibody") if isinstance(design.confidence_metrics, dict) else None
    scoped = safe_float((rfa or {}).get("plddt_primary") or (rfa or {}).get("plddt_modifiable"))
    if scoped is not None and (rfa or {}).get("confidence_scope", {}).get("primary_scope") == "modifiable_residues":
        return scoped, "RFA modifiable-residue pLDDT", "rfantibody_modifiable_residues"
    return safe_float(design.plddt_overall), "pLDDT Overall", "all_residues_or_model_default"
```

Expose separate metric keys:

- `plddt_primary` / label `Primary pLDDT`
- `rfa_plddt_modifiable` / label `RFA Modifiable pLDDT`
- `rfa_plddt_all_residue` / label `RFA All-Residue pLDDT`

For RFA jobs, chart suggestions should prefer `rfa_plddt_modifiable` or `plddt_primary`, not `plddt_overall`.

**Failing tests first:**

- Create mocked Design rows with `plddt_overall=99.5`, `confidence_metrics.rfantibody.plddt_modifiable=90.5`.
- Assert Plotly response point metrics use `plddt_primary=90.5` and include `rfa_plddt_all_residue=99.5` separately.
- Assert metadata describes source/semantics correctly.

**Verification command:**

```bash
uv run --directory platform/api python -m pytest tests/test_design_plotly_metrics.py -q
```

---

## Task 4: Frontend type/API contract for RFA confidence scope

**Objective:** Teach the frontend Design type and semantic helpers about RFA primary/modifiable/all-residue pLDDT.

**Files:**
- Modify: `platform/frontend/src/lib/api.ts`
- Modify/Create: `platform/frontend/src/components/rfaConfidenceScope.ts`
- Test: `platform/frontend/tests/rfaConfidenceScope.test.ts`
- Modify: `platform/frontend/tsconfig.tests.json` if new helper is not emitted automatically

**Implementation detail:**

Add types:

```ts
export interface RfaModifiableResidue {
  chain_id: string;
  residue_number: number;
  insertion_code?: string | null;
  label?: string | null;
}

export interface RfaConfidenceScope {
  primary_scope: 'modifiable_residues' | 'all_residues' | 'unknown';
  modifiable_residues?: RfaModifiableResidue[];
  modifiable_ranges?: Array<{ chain_id: string; start_residue_number: number; end_residue_number: number; label?: string | null }>;
  counts?: Record<string, number>;
  plddt?: Record<string, number | null>;
  status?: string;
}
```

Add helper:

```ts
export function resolveDesignPrimaryPlddt(design: Partial<Design>): {
  value: number | null;
  label: string;
  source: 'rfa_modifiable' | 'rfa_all_residue' | 'default';
}
```

Rules:

- If `inferDesignOutputSource(design) === 'rfantibody'` and RFA confidence scope says `modifiable_residues`, return modifiable/primary pLDDT.
- Else return existing `plddt_overall`.

**Failing tests first:**

- RF design with `plddt_overall=99.5`, `rfa_plddt_modifiable=90.5` resolves to 90.5 label `RFA Modifiable pLDDT`.
- RF design without scope resolves to `plddt_overall` label `RF pLDDT` or fallback.
- Non-RFA design resolves to `plddt_overall`.

**Verification command:**

```bash
cd platform/frontend
npx tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/rfaConfidenceScope.test.js
```

---

## Task 5: Data Viewer / Structure Viewer Mol* pLDDT mask uses modifiable residues

**Objective:** Make pLDDT color mode in `StructureViewerPane` pass only modifiable residues to `MolstarViewer` when the RFA workflow requested subset design.

**Files:**
- Modify: `platform/frontend/src/components/structureViewerSemantics.ts`
- Modify: `platform/frontend/src/components/StructureViewerPane.tsx`
- Existing: `platform/frontend/src/components/MolstarViewer.tsx`
- Test: `platform/frontend/tests/structureViewerSemantics.test.ts`

**Implementation detail:**

Extend `buildPlddtResidueColorMap(...)` input:

```ts
export interface PlddtResidueColorMapInput {
  ...
  residueMask?: Array<{ chain_id: string; residue_number: number }> | null;
  maskMode?: 'include_only' | 'none';
}
```

Behavior:

- If `maskMode === 'include_only'` and `residueMask` is non-empty, only add colors for keys in the mask.
- If no mask, preserve current behavior.
- The returned map should contain **only modifiable residue keys** for RFA selected-loop workflows.
- `MolstarViewer` already sends `nonSelectedColor: '#444444'` when `residueColors` is present, so non-selected residues will dim/grey automatically. Keep that behavior.

In `StructureViewerPane.tsx`:

- Resolve `rfaConfidenceScope` from `selectedDesign.confidence_metrics.rfantibody.confidence_scope` or flattened `selectedDesign.rfa_confidence_scope`.
- Build `residueMask` from `modifiable_residues` only when `primary_scope === 'modifiable_residues'`.
- Pass `maskMode='include_only'` into `buildPlddtResidueColorMap` only for RFA/modifiable scope.
- Update labels:
  - quick view label: `RFA Modifiable pLDDT`
  - profile title: `RFA Modifiable pLDDT Profile`
  - legend note: `Only workflow-modifiable residues are colored; unchanged residues are dimmed.`

**Failing tests first:**

Add to `structureViewerSemantics.test.ts`:

```ts
test('RFA pLDDT color map only colors modifiable residue mask', () => {
  const colorMap = buildPlddtResidueColorMap({
    chainMetrics: {
      H: { plddt: [100, 91, 92, 100], residue_numbers: [1, 2, 3, 4] },
      T: { plddt: [100, 100], residue_numbers: [1, 2] },
    },
    residueMask: [
      { chain_id: 'H', residue_number: 2 },
      { chain_id: 'H', residue_number: 3 },
    ],
    maskMode: 'include_only',
    colorForValue: value => ({ r: value, g: 0, b: 0 }),
  });
  assert.deepEqual([...colorMap!.keys()].sort(), ['H:2', 'H:3']);
});
```

Also add a source-level test asserting `StructureViewerPane.tsx` wires RFA scope into `buildPlddtResidueColorMap` and does not rely on all-residue pLDDT for RF modifiable workflows.

**Verification command:**

```bash
cd platform/frontend
npx tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/structureViewerSemantics.test.js
npx tsc -b --pretty false
```

---

## Task 6: Result cards, tables, sorting, and overview metrics prefer primary scoped pLDDT

**Objective:** Stop RFA overview/table/cards from presenting all-residue pLDDT as the headline confidence.

**Files:**
- Modify: `platform/frontend/src/components/ResultsViewer.tsx`
- Modify: `platform/frontend/src/components/DesignComparePane.tsx`
- Modify: `platform/frontend/src/components/DesignBrowser.tsx` if still active
- Modify: `platform/frontend/src/components/ExperimentalAnalyticsPane.tsx` if still active for this route
- Test: source-level frontend tests covering labels/metric resolver use

**Implementation detail:**

- Replace direct display/sort use of `design.plddt_overall` in RFA-specific contexts with `resolveDesignPrimaryPlddt(design)`.
- Keep explicit columns/chips for:
  - `RFA Mod pLDDT`
  - `RFA All-res pLDDT`
- For generic non-RFA jobs, preserve existing pLDDT behavior.
- If a user sorts by `plddt`/`plddt_overall` on an RFA job, sort by primary scoped pLDDT or expose a separate sort option `RFA Mod pLDDT` as default.

**Failing tests first:**

- Source-level tests assert `ResultsViewer.tsx` imports/uses `resolveDesignPrimaryPlddt`.
- Tests assert stale direct `pLDDT` labels are not used for RF selected-loop headline cards.
- If there are helper tests for sorting, add RFA scoped sort case.

**Verification command:**

```bash
cd platform/frontend
npx tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/*.test.js --test-concurrency=1
npx tsc -b --pretty false
```

---

## Task 7: Migration/backfill for existing RFA rows

**Objective:** Update existing completed RFantibody rows so the 50-backbone job immediately shows scoped pLDDT and masked Mol* coloring.

**Files:**
- Create: `platform/api/scripts/backfill_rfa_confidence_scope.py` or management command if scripts convention exists
- Test: backend unit test for dry-run/backfill helper

**Implementation detail:**

- Query designs where `confidence_metrics.rfantibody.trb_path` exists or `rfa_plddt_final IS NOT NULL`.
- Re-run `load_rfantibody_trb_summary(design.pdb_path)`.
- Update:
  - `confidence_metrics.rfantibody.confidence_scope`
  - `confidence_metrics.rfantibody.plddt_primary`
  - `rfa_plddt_selected/modifiable/all_residue/...` as available
  - optionally `plddt_overall` to scoped primary **only if product decision accepts this migration**.
- Provide `--dry-run`, `--job-id`, and `--apply` flags.
- Print per-job before/after summaries:
  - all-residue mean range
  - modifiable mean range
  - count of rows updated

**Verification command:**

```bash
uv run --directory platform/api python scripts/backfill_rfa_confidence_scope.py --job-id 5642c1bd-c715-4143-959f-114ee87a4f6e --dry-run
uv run --directory platform/api python scripts/backfill_rfa_confidence_scope.py --job-id 5642c1bd-c715-4143-959f-114ee87a4f6e --apply
```

Expected for the known job:

- all-residue/API old mean around 99.58
- modifiable/selected loop mean around 93.51
- 50 rows updated

Do not run `--apply` without explicit operator approval if the DB is live/important.

---

## Task 8: Browser/live verification

**Objective:** Prove the user-visible Data Viewer/Results UI now reflects modifiable-residue pLDDT and Mol* mask.

**Files:**
- No code unless failures found.

**Verification surfaces:**

1. API:

```bash
python3 - <<'PY'
import json, urllib.request
job='5642c1bd-c715-4143-959f-114ee87a4f6e'
data=json.load(urllib.request.urlopen(f'http://127.0.0.1:8000/api/designs/by-job/{job}?limit=3'))
for d in data['designs']:
    rfa=d['confidence_metrics']['rfantibody']
    print(d['name'], rfa.get('plddt_primary'), rfa.get('plddt_modifiable'), rfa.get('plddt_all_residue'))
PY
```

2. Plotly metrics:

- `/api/designs/by-job/{job_id}/plotly-metrics` includes `rfa_plddt_modifiable` and metadata labels it as modifiable scoped.

3. Browser:

- Navigate to Results/Structure for the 50-backbone job.
- Select pLDDT/RFA confidence mode.
- Verify DOM text contains `RFA Modifiable pLDDT` or equivalent compact label.
- Inspect Mol* selection payload by instrumenting/console-wrapping `viewer.viewerInstance.visual.select` if needed; assert `data.length` equals modifiable residue count, not total structure residues.
- Confirm no console errors.

**Expected behavior:**

- Non-designed target/framework residues are dim/neutral, not colored as 99–100 pLDDT.
- Designed loops are colored by their actual pLDDT.
- Cards/charts no longer imply whole structure confidence is the key RFA design-quality metric.

---

## Acceptance criteria

- RFA workflows with explicit modifiable residues/loops use those residues as primary pLDDT scope.
- Whole-structure pLDDT is preserved only as an explicitly labeled background metric.
- Plotly metrics and chart suggestions prefer scoped primary/modifiable pLDDT for RFA jobs.
- Results cards/tables/sort defaults do not headline all-residue pLDDT for scoped RFA rows.
- Structure Viewer/Molstar pLDDT color map includes only modifiable residues when a modifiable scope exists.
- Non-selected residues are dimmed/neutral in Mol* pLDDT mode for scoped RFA rows.
- Tests cover backend scope computation, API exposure, Plotly metric semantics, frontend resolver, and Mol* residue-mask helper behavior.
- Existing non-RFA pLDDT behavior remains unchanged.

## Non-goals

- Do not reinterpret Boltz/Protenix/ESMFold2 pLDDT.
- Do not remove all-residue RFA pLDDT from the system; keep it with a non-primary label.
- Do not add fake scoped pLDDT when modifiable residues cannot be mapped.
- Do not change physical/scientific RFA run parameters.

## Rollback plan

- Backend: revert scoped-primary assignment and continue exposing current `rfa_plddt_final`/`plddt_overall`.
- Frontend: remove `residueMask` argument and resolver use; MolstarViewer itself should not need rollback if the mask is implemented upstream in helper/StructureViewerPane.
- Backfill: make it dry-run-first and DB-backup-first; if applied incorrectly, restore DB backup or run script with previous `plddt_overall` from `rfa_plddt_all_residue`.
