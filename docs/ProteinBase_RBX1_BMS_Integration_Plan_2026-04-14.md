# ProteinBase RBX1 Import + Visualization Implementation Plan

> For Hermes: use subagent-driven-development if this gets delegated later. Keep the first pass narrow: import the 322 selected RBX1 designs as a synthetic completed job so the existing BMS job/design/analytics surfaces can render them without inventing a separate visualization stack.

Goal: Load the ProteinBase RBX1 selected-submissions bundle into BioModStack as a completed dataset-backed job whose designs can be filtered, charted, and compared inside the existing design analytics UI.

Architecture: Add a small backend importer that turns `selected_submissions.jsonl` rows into one `Job` plus 322 `Design` records, materializes per-design JSON + structure artifacts under `bms_results/imports/`, and maps nested `evaluations` metrics into existing `Design` columns plus raw `confidence_metrics`. Then make tiny frontend changes so imported Boltz2/ESMFold metrics land in the right analytics families and default metric pickers.

Tech Stack: FastAPI, SQLAlchemy async models, existing `Job`/`Design` tables, React + React Query + Plotly analytics dashboard, local JSONL bundle at `/home/dalab/Desktop/proteinbase_rbx1_selected_322_bundle`.

---

## Current facts already verified

- Bundle path: `/home/dalab/Desktop/proteinbase_rbx1_selected_322_bundle/selected_submissions.jsonl`
- Row shape: top-level keys are only `author`, `designMethod`, `evaluations`, `id`, `length_aa`, `name`, `protein_url`, `sequence`
- Metrics live inside `evaluations[]`, not at top level
- Coverage from direct parsing:
  - `boltz2_*` metrics are present for 101 rows
  - `esmfold_plddt` is present for 317 rows
- Existing analytics path already supports dynamic numeric metrics via:
  - backend: `platform/api/routers/designs.py::_build_plotly_metrics()`
  - frontend: `platform/frontend/src/components/AnalyticsDashboard.tsx`
- Existing job-scoped analytics endpoint already exists:
  - `GET/POST /api/designs/by-job/{job_id}/plotly-metrics`
- Existing analytics UI already merges backend `metric_keys` into custom plot selectors
- Sample ProteinBase structure URL is live (`curl -I` returned HTTP 200 for a Boltz2 CIF), so first-pass import should download real structure files instead of storing empty placeholders

---

## Design decisions

1. Import as a synthetic completed job, not a new one-off dashboard.
   - Reason: the existing Jobs page, Designs table, and AnalyticsDashboard already know how to render any populated `Job` + `Design` rows.
   - Outcome: minimal frontend work and no new visualization surface to maintain.

2. Preserve raw ProteinBase metrics in `confidence_metrics`, but also map the important ones into first-class `Design` columns.
   - Direct columns power existing filters and default charts.
   - Raw metrics preserve provenance and let the dynamic plotly-metrics path surface additional fields automatically.

3. Materialize imported artifacts under BMS-managed storage.
   - Root: `get_results_dir() / "imports" / "proteinbase_rbx1_selected_322"`
   - This keeps paths under existing allowed roots and makes structure/json assets browseable by current APIs.

4. Treat imported RBX1 designs as validation-family outputs.
   - Set `stage_family="validation"`
   - Set `stage_mode="proteinbase_import"`
   - This makes imported designs fall into the existing validation analytics lens without pretending they were created by RFantibody/FAMPNN/PPIFlow.

5. Normalize metric scales deliberately.
   - `Design.plddt_overall` should be 0-100 for UI consistency:
     - use `boltz2_plddt * 100` when available
     - otherwise fall back to `esmfold_plddt` as-is
   - Keep raw `boltz2_plddt` in `confidence_metrics` at its source 0-1 scale
   - Keep `ptm`, `iptm`, `ipsae`, `complex_iplddt`, `complex_ipde`, `lis`, `min_ipsae` in their native numeric scale

---

## File-level implementation plan

### Task 1: Create the importer service

Objective: Add one backend service that parses the JSONL bundle, downloads available structure files, writes per-design JSON sidecars, and builds normalized metric payloads.

Files:
- Create: `platform/api/services/proteinbase_importer.py`
- Test: `platform/api/tests/test_proteinbase_importer.py`

Implementation details:
- Add a small typed importer surface, e.g.:
  - `import_proteinbase_selected_bundle(bundle_dir: Path, session: AsyncSession, *, dataset_name: str = "proteinbase_rbx1_selected_322") -> Job`
- Read:
  - `selected_submissions.jsonl`
  - optionally `selected_submissions_wide.csv` only for metadata sanity checks, not as source of truth
- For each row:
  - flatten `evaluations[]` into a `metric_map`
  - keep all numeric metrics in `confidence_metrics`
  - preserve non-numeric evaluation items in `provenance["proteinbase"]["evaluations"]`
- Download structure URL preference:
  1. `boltz2_structure_prediction.url`
  2. `esmfold_structure_prediction.url`
- Write files under:
  - `bms_results/imports/proteinbase_rbx1_selected_322/structures/<design_name>.cif`
  - `bms_results/imports/proteinbase_rbx1_selected_322/records/<design_name>.json`
- Store a provenance block per design:
  - `dataset_name`
  - source `proteinbase_id`
  - `author`
  - `design_method`
  - `protein_url`
  - original structure URL
  - raw evaluation count
  - import timestamp

Metric mapping rules for first pass:
- `Design.name` <- `row["name"]`
- `Design.binder_length` <- `row["length_aa"]`
- `Design.pdb_path` <- downloaded CIF path
- `Design.json_path` <- record JSON path
- `Design.plddt_overall` <- `boltz2_plddt * 100` else `esmfold_plddt`
- `Design.ptm` <- `boltz2_ptm`
- `Design.iptm` <- `boltz2_iptm`
- `Design.complex_iplddt` <- `boltz2_complex_iplddt`
- `Design.complex_ipde` <- `boltz2_complex_pde`
- `Design.ipsae` <- `boltz2_ipsae`
- `Design.conf_score` <- `ted_confidence` when present
- `Design.stage_family` <- `"validation"`
- `Design.stage_mode` <- `"proteinbase_import"`
- `Design.artifact_group` <- `"candidate"`
- `Design.artifact_class` <- `"imported_design"`
- `Design.provenance` <- `{"proteinbase": ...}` block
- `Design.confidence_metrics` should additionally include friendly aliases where useful:
  - `min_ipsae` from `boltz2_min_ipsae`
  - `lis` from `boltz2_lis`
  - `complex_pde` from `boltz2_complex_pde`
  - `esmfold_plddt` as-is
  - keep original `boltz2_*` keys too

Test cases:
- importer creates one job and N design rows from a tiny 2-row fixture bundle
- direct metric mapping is correct, including pLDDT scaling to 0-100
- raw `confidence_metrics` retains `boltz2_min_ipsae` and `boltz2_complex_pde`
- structure download helper is mockable and paths are written under `bms_results/imports/...`
- stage family/mode are set so analytics lens inference can classify the designs

Suggested test command:
- `source venv/bin/activate && python -m pytest platform/api/tests/test_proteinbase_importer.py -q`

---

### Task 2: Expose the importer through the existing jobs API

Objective: Make RBX1 import an explicit backend action that returns a normal `JobResponse`.

Files:
- Modify: `platform/api/routers/jobs.py`
- Possibly modify: `platform/api/schemas.py` if a dedicated request schema is cleaner
- Test: extend `platform/api/tests/test_proteinbase_importer.py` or add `platform/api/tests/test_proteinbase_import_router.py`

Implementation details:
- Add a small request model near the jobs router, for example:
  - `bundle_dir: str`
  - `dataset_name: str = "proteinbase_rbx1_selected_322"`
  - `replace_existing: bool = False`
- Add endpoint:
  - `POST /api/jobs/import-proteinbase-rbx1`
- Endpoint behavior:
  - resolve and validate `bundle_dir`
  - call the importer service
  - if `replace_existing=True`, delete older imported jobs for the same dataset before reinserting
  - return a standard `JobResponse`
- Job metadata for the synthetic job:
  - `model_id="proteinbase"`
  - `mode="dataset_import"`
  - `status="completed"`
  - `selection_source_type="saved_dataset"`
  - `selection_dataset_name="proteinbase_rbx1_selected_322"`
  - `output_dir=<import root>`
  - `provenance={"import_kind": "proteinbase_selected_bundle", ...}`

Acceptance check:
- after the endpoint runs, normal job listing returns the imported job
- listing designs for that job returns 322 rows
- `/api/designs/by-job/{job_id}/plotly-metrics` includes the imported metrics

Suggested test command:
- `source venv/bin/activate && python -m pytest platform/api/tests/test_proteinbase_importer.py -q`

---

### Task 3: Make the analytics dashboard treat imported RBX1 metrics as first-class

Objective: Ensure imported metrics show up under sensible labels/families instead of only as anonymous dynamic fields.

Files:
- Modify: `platform/frontend/src/components/designOutputSource.ts`
- Modify: `platform/frontend/src/components/AnalyticsDashboard.tsx`
- Optional type touch-up: `platform/frontend/src/lib/api.ts`

Implementation details:
- In `designOutputSource.ts`:
  - keep imported designs in the validation lens via `stage_family="validation"`
  - optionally add a tiny helper so `stage_mode === "proteinbase_import"` or provenance markers stay classified as validation even if future imports omit stage family
- In `AnalyticsDashboard.tsx`:
  - extend `inferMetricFamily()` so these keys are not left as generic `dynamic`:
    - keys containing `ipsae`, `iptm`, `ptm`, `plddt`, `complex_pde`, `complex_iplddt`, `lis`, or `boltz2_`
  - map them to `validation` or `protenix` family as appropriate
  - add explicit labels for the most important new metrics:
    - `boltz2_min_ipsae` -> `Min ipSAE`
    - `boltz2_complex_pde` -> `Complex PDE`
    - `boltz2_lis` -> `LIS`
    - `boltz2_complex_plddt` -> `Complex pLDDT`
    - `boltz2_complex_iplddt` -> `Complex Interface pLDDT`
  - add these keys to `CORE_METRICS` only if they materially improve defaults; otherwise let dynamic metrics cover them
- Do not build a second charting component. Reuse the existing custom 2D/3D plot selectors and family sections.

Acceptance check:
- imported job opens in the existing AnalyticsDashboard without code-path forks
- default validation lens still works
- custom plot selectors expose `boltz2_min_ipsae`, `boltz2_complex_pde`, and `boltz2_lis`

Suggested verification command:
- `cd platform/frontend && npm run build`

---

### Task 4: Verify end-to-end against the real RBX1 bundle

Objective: Prove the import and visualization path works with the actual 322-row dataset.

Files:
- No new permanent files required beyond the importer outputs

Verification steps:
1. Run the importer endpoint or service on:
   - `/home/dalab/Desktop/proteinbase_rbx1_selected_322_bundle`
2. Confirm the created job has:
   - `design_count == 322`
   - `selection_dataset_name == "proteinbase_rbx1_selected_322"`
3. Confirm at least these plotly metric keys exist:
   - `plddt_overall`
   - `ipsae`
   - `boltz2_min_ipsae`
   - `boltz2_complex_pde`
   - `boltz2_complex_iplddt`
   - `esmfold_plddt`
4. Confirm counts look sane:
   - 322 total designs
   - about 101 rows with Boltz2 interface/confidence metrics
   - about 317 rows with ESMFold pLDDT
5. Open the imported job in the frontend and verify:
   - validation-family analytics render
   - custom plots can select min ipSAE / complex PDE / complex interface pLDDT
   - structure links/files exist on disk for downloaded rows

Suggested commands:
- `source venv/bin/activate && python -m pytest platform/api/tests/test_proteinbase_importer.py -q`
- `source venv/bin/activate && python -m pytest platform/api/tests/test_api_analysis_ingest_review_regressions.py -q`
- `cd platform/frontend && npm run build`

---

## Notes to preserve during implementation

- Do not read top-level JSONL keys as if metrics are flattened; metrics must be extracted from `evaluations[]`
- Avoid writing outside BMS allowed roots; imported artifacts should live under `bms_results/imports/...`
- Keep the import idempotent enough that repeated runs do not create duplicate dataset jobs unless explicitly requested
- Prefer downloading real CIFs over stub files because sample ProteinBase structure URLs are reachable
- Keep the first pass non-destructive: no migration should be needed because existing `Job` and `Design` columns are sufficient

---

## Definition of done

- A synthetic completed job representing the RBX1 selected dataset can be created from the local bundle
- The job contains 322 `Design` rows with mapped pLDDT/ipSAE-family metrics
- Existing `/api/designs/by-job/{job_id}/plotly-metrics` returns the imported metrics cleanly
- Existing frontend analytics can chart RBX1 metrics without a new dashboard
- Backend targeted tests pass and frontend builds successfully
