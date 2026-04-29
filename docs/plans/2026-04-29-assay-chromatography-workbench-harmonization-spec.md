# Assay Analytics Chromatography Workbench Harmonization Spec

Date: 2026-04-29
Status: implementation-ready plan
Scope: BioModStack `/bms/assay` chromatography workbench only, with qPCR/statistics checked for shared assay-shell consistency.

## 1. Ground truth inspected

### Live surfaces

- Stable hosted BMS web is live at `/bms/` on the core-runtime web port.
- Vite dev and stable hosted surfaces are both open on this workstation; stable `/bms/assay` was used for product-visible evidence.
- `bms-api` and `bms-web` containers were healthy when inspected.
- Browser console on `/bms/assay` had no JavaScript errors during chromatography tab navigation and sample analyses.

### Source files inspected

Frontend:

- `platform/frontend/src/components/AssayAnalytics.tsx`
- `platform/frontend/src/components/assay/AssayWorkbenchPrimitives.tsx`
- `platform/frontend/src/components/hplc/index.tsx`
- `platform/frontend/src/components/hplc/EmpowerImport.tsx`
- `platform/frontend/src/components/hplc/ChromatogramAnalysis.tsx`
- `platform/frontend/src/api/client.ts`

Backend:

- `platform/api/routers/assay_analytics.py`
- `platform/api/services/assay_tool_integrations.py` via the live `/tools` and `/capabilities` responses

Rest-of-stack comparison screens:

- Dashboard: dense telemetry cards, compact controls, KPI-first visual hierarchy.
- Job Launcher: page title/subtitle, strong local segmented tabs, uniform workflow cards, badges/chips, one obvious launch CTA.
- Results Viewer: import/open dual-path data hub, preview-before-import, recent workflow cards, clear status pills.
- Molecular Biology Toolkit: left source shelf, central canvas, right control/inspector, explicit empty state.
- NGS Toolkit: staged scientific workflow form, required markers, segmented choices, helper text, clear submit hierarchy.
- BioXP Cockpit: local tabs, status/action/evidence cards, semantic badges, disabled interlocks, raw-payload panels.

## 2. Live API evidence

All probes hit the live BMS API under `/api/assay-analytics`.

- `/capabilities`: 200. Source of truth is BMS API `/api/assay-analytics`; legacy standalone parser is explicitly not used. Exposes qPCR, Waters/Empower chromatography, plasmid isoform, DOE/statistics, and Plotly surfaces.
- `/tools`: 200. 26 tools registered; 26 runtime-available. Categories include chromatography, qPCR, import, and DOE/statistics.
- `/datasets`: 200 `[]`. No fake built-in assay datasets are served.
- `/datasets/1`: 404 with explicit real-data/import-first message.
- `/analysis/hplc/analyze` with synthetic two-peak chromatogram and `baseline_method=mocca2_flatfit`: 200, engine `MOCCA2`, package `mocca2`, 2 peaks, 3 Plotly traces.
- `/analysis/hplc/analyze` with `baseline_method=snip`: 200, engine `scipy.signal`, package `scipy`, 2 peaks, 3 Plotly traces. This exposes a UI/backend semantics mismatch: the UI label says SNIP, but the backend treats `snip` as the generic non-MOCCA2/scipy path.
- `/analysis/hplc/calibration-curve`: 200, returns slope/intercept/R²/points but no `plotly_json`.
- `/analysis/hplc/quantify`: 200, returns sample concentrations and `plotly_json`.
- `/analysis/hplc/empower/plasmid-isoforms`: 200, returns explicit isoform windows, assigned area, and percent area.
- `/analysis/hplc/empower/import` with real CSV-shaped peak table: 200, `import_engine=empower_cdf_arw_csv`, 3 injections, SST groups, QC plot traces, composition plot traces, peak table rows, no errors. CSV-only import correctly has no chromatogram overlay traces because it contains peak rows, not raw chromatogram arrays.
- `/analysis/hplc/empower/import` with native database extension: 400, hard-fails with export-first guidance.
- `/analysis/hplc/empower/sst` and export routes work for the in-memory import session.
- `/openapi.json`: 32 assay routes under `/api/assay-analytics`.

## 3. Screenshot vs live drift

The user-provided screenshot is not the current stable hosted quantification panel.

Evidence:

- Screenshot shows the older flat quantification form labels: `Calibration Conc.`, `Calibration Areas`, `Sample Areas`, `Sample IDs`, one unit field, and a generic empty state.
- Current live stable `/bms/assay` shows the newer grouped panel: `Calibration standards`, `Unknown samples`, `Output unit`, `Quantification Results`, and explicit helper text.
- The current built `/bms/assets/index-*.js` bundle contains `Calibration standards`, `Quantification Results`, and the workbench-layout marker, but does not contain `Calibration Conc.`.

Interpretation:

- The screenshot is useful as a record of the previous/harsher card-first layout, but product decisions should be based on current source and live stable `/bms/assay`.
- The current source already began harmonizing the outer assay shell and sample-quantification panel, but the actual chromatography import/analysis/calibration subpanels are still mostly pre-harmonization.

## 4. Current chromatography UI state

### Working

- Top-level Assay shell uses shared primitives: `AssayPageShell`, `AssayPageHeader`, `AssayStatusStrip`, `AssayModeTabs`, `AssayPanel`.
- qPCR, chromatography, and statistics panes stay mounted via `hidden={...}`, preserving tab state.
- Chromatography tab has the right BMS-only framing and no seeded fake rows.
- Empower import accepts CSV/TXT/CDF/ARW/ZIP in the UI and hard-rejects native database-style containers client-side and server-side.
- Empower import can display import summary, QC plot, composition plot, SST table, peak region summary, flattened peak table, and injection review table when data exists.
- Empower import has browser-cache restore/clear behavior using `assayPersistence.ts`.
- Chromatogram analysis can render a Plotly chromatogram analysis plot and peak table after pasted time/signal data.
- Sample quantification now uses grouped input cards and displays fit stats plus quantified sample rows.

### Not yet harmonized

- `EmpowerImport.tsx` still uses many square one-off `border border-border-primary bg-bg-secondary` blocks instead of the newer rounded/soft shared assay primitives.
- `ChromatogramAnalysis.tsx` is mixed: `HplcQuantification` uses the new primitives, but `ChromatogramAnalysis` and `CalibrationCurve` still use the older square card/form styling.
- `EmpowerImport.tsx` has 29 old `border border-border-primary` occurrences and no `AssayPanel` / `AssayInputCard` / `AssayOutputCard` usage.
- `ChromatogramAnalysis.tsx` still has 19 old `border border-border-primary` occurrences and multiple pre-primitive blocks.
- The current first viewport still feels like a landing/marketing page more than a task-first workbench: hero, status strip, mode cards, workbench intro, status strip, subnav cards, then work area.
- Internal implementation language is exposed in normal product UI, e.g. source-of-truth route callouts and the sample-quantification line saying the API contract remains `/analysis/hplc/quantify`.

## 5. Concrete functional gaps

### 5.1 Cleanup gaps

- Remove old card classes from chromatography subpanels and replace them with shared assay primitives.
- Normalize all chromatography labels and copy:
  - Use `Empower 3` consistently, not mixed `Empower3` / `Empower 3`.
  - Use `Cq` consistently in qPCR, not mixed Ct/Cq unless reflecting imported instrument headers.
  - Use `concentration`, `area`, `retention time`, and `sample ID` labels consistently.
- Move developer route/source-of-truth text behind a debug/details affordance or convert it to user-safe copy.
- Replace bare error boxes/buttons with `AssayErrorNotice`, `AssayPrimaryButton`, and matching secondary/destructive button primitives.

### 5.2 Selector gaps

Current Empower import state is global and table-driven. It lacks explicit selection state.

Needed:

- `selectedInjectionId`
- `selectedPeakKey` or `(injection_id, peak_id)`
- selected isoform window
- selected SST group
- selected plot trace visibility/filter state
- selected report scope: all injections, selected group, selected injection, selected samples

UX target:

- Selecting a row highlights the corresponding chromatogram trace and peak markers.
- Selecting a trace/peak in Plotly updates the right-side inspector.
- Selecting an SST group filters/highlights relevant injections.
- Selection state persists across sub-tabs and survives local browser cache restore.

### 5.3 Grouping gaps

Backend groups CDF/ARW loose files by normalized basename and emits one injection per CDF chromatogram. UI does not expose enough grouping control.

Needed:

- Visible import grouping summary: file count, grouped chromatograms, unmatched ARW, unmatched CDF, text-only rows, skipped files.
- Grouping controls for injection batches:
  - by method
  - by run date/window
  - by sample role
  - by explicit user group / SST group
  - by source file / basename
- Editable role/group mapping with bulk operations, not only per-row sample type edits.
- Group badges reused consistently in plots, tables, and reports.

### 5.4 SST gaps

Current SST summary computes basic means/RSDs and export works for the in-memory import session.

Needed for product-level SST:

- Explicit SST criteria editor: area %RSD limit, RT %RSD limit, resolution minimum, plate count minimum, tailing limit, primary-percent bounds.
- Pass/warn/fail badges for each group and injection.
- Trend plots over injection order/time for area, RT, resolution, primary %, and total area.
- Separation between acquisition source-of-truth and BMS review annotations.
- Report generation that includes criteria, failures, excluded injections, notes, and export provenance.

### 5.5 Integration gaps

- The `ChromatogramAnalysis` UI says `SNIP (recommended)`, but the backend only uses MOCCA2 when `baseline_method` starts with `mocca2_`; `snip` currently lands on the scipy/generic path.
- Calibration endpoint returns no `plotly_json`, while the frontend `CalibrationCurve` only renders a Plot if `plotly_json` exists. Result: calibration curve fit produces stats but no calibration plot unless this is fixed backend-side or frontend-side.
- Quantification endpoint returns `plotly_json`, but `HplcQuantification` does not render it. Result: a valid calibration/unknown run shows tables but no fit plot.
- Empower CSV import with only peak tables cannot show chromatogram overlay; UI should say exactly that instead of leaving a missing-plot area ambiguous.
- The Empower in-memory import cache is useful for sessions, but not a durable dataset/review object. Browser local cache is not enough for long-running review/report workflows.

### 5.6 Report/export gaps

Current exports are CSVs for SST master and plasmid tracking from the in-memory session.

Needed:

- Purpose-built review report bundle per import session:
  - import manifest
  - parser/engine metadata
  - source file grouping summary
  - injection table
  - peak table
  - SST criteria and pass/fail table
  - plasmid isoform composition table
  - reviewer annotations/exclusions
  - Plotly/static plot references
- Export buttons should reflect enabled scope and format: CSV, JSON, report bundle, printable summary.
- Reports should be based on explicit real input data; no generated demo rows.

### 5.7 Peak highlighting gaps

- Chromatogram analysis plot returns raw/baseline/corrected traces but no visible peak markers or shaded integration windows.
- Empower chromatogram overlay returns line traces only; peak table rows are separate and not visually linked.

Needed:

- Add peak apex markers.
- Add integration-window shaded regions or vertical spans.
- Color/mark primary peak, pre-primary, post-primary, unassigned peaks.
- Hover text should show sample, injection, peak ID, RT, area, percent area, source, and flags.
- Row hover/click should update plot selection; plot click should update row/inspector selection.

## 6. Harmonized target UX

### 6.1 Page structure

Use rest-of-BMS patterns instead of another long stack of independent cards.

Target hierarchy:

1. Global BMS shell/nav remains unchanged.
2. Page header: concise title and subtitle, no long marketing paragraph.
3. Mode tabs: qPCR / Chromatography / DOE + Statistics using the current shared assay mode tabs, but with shorter descriptions.
4. Chromatography workbench body:
   - Left source/import shelf
   - Center chromatogram/QC/composition viewer
   - Right selected injection/peak/SST inspector
   - Bottom tabbed tables/reports region

### 6.2 Chromatography workbench layout

Left: Source / import shelf

- File picker / drag-drop zone for `.cdf`, `.arw`, `.zip`, `.csv`, `.txt`.
- Explicit unsupported-container notice.
- Parser controls: baseline method, peak prominence, parse mode.
- Import manifest after parse.
- Browser cache restore/clear.
- Recent BMS review sessions if durable persistence is added.

Center: Viewer

- Default empty state: `Import Empower exports or paste chromatogram arrays to begin review`.
- After import:
  - chromatogram overlay when raw traces exist
  - QC plot tab
  - composition/isoform plot tab
  - calibration/quantification plot tab when relevant
- Selection-linked peak markers and integration spans.

Right: Inspector

- Selected injection summary: sample, role, injection number, method, run date, source, flags.
- Selected peak summary: RT, area, height, width, resolution, area %, source, primary/isoform classification.
- Editable review fields: role/group, exclude, note, flag.
- SST status card for selected group.
- Save/update action with explicit success/error notice.

Bottom: Tables/reports

- Tabs: Injections, Peaks, SST, Isoforms, Reports.
- Tables support search/filter/sort and selection, not just scrolling.
- Exports are scoped and clearly labeled.

### 6.3 Shared assay primitive expansion

Extend `platform/frontend/src/components/assay/AssayWorkbenchPrimitives.tsx` with reusable pieces before broad restyling:

- `AssaySecondaryButton`
- `AssayDangerButton`
- `AssayNotice` with `info`, `warning`, `success`, `error`
- `AssayMetricCard`
- `AssaySectionHeader`
- `AssayTableShell`
- `AssayInspectorPanel`
- `AssaySourceShelf`
- `AssayPlotPanel`
- `AssayDebugDisclosure`

Then refactor chromatography subpanels to use those primitives rather than one-off class strings.

## 7. Backend/API changes

### 7.1 Fix baseline semantics

Option A: Change frontend option values:

- `mocca2_flatfit`: `MOCCA2 flatfit`
- `mocca2_arpls`: `MOCCA2 arPLS`
- `mocca2_asls`: `MOCCA2 asLS`
- `linear`: `Linear`
- `none`: `None`

Option B: Teach backend `snip` if a real SNIP implementation is intended.

Do not label `snip` as recommended unless the backend actually executes SNIP or a named external package that implements it.

### 7.2 Add calibration plot payload

Update `hplc_calibration_curve` to return `plotly_json`, matching qPCR standard curve style:

- standards scatter
- linear fit line
- optional residual subplot or residual payload
- title and axis labels with analyte/unit

### 7.3 Render quantification plot in frontend

`HplcQuantification` should render backend `plotly_json` above or beside the quantified sample table.

### 7.4 Durable import/review object

Replace or supplement `_EMPOWER_IMPORTS` in-memory storage with a real BMS assay review session model when persistence is desired.

Minimal data model:

- `assay_import_sessions`: id, kind, created_at, label, parser_engine, source_manifest_json, summary_json
- `assay_import_files`: session_id, filename, extension, size, checksum, grouping_key, parse_status, error
- `assay_chromatography_injections`: session_id, injection_id, sample_name, sample_role, method, run_date, source_file, metrics_json, annotation_json
- `assay_chromatography_peaks`: session_id, injection_id, peak_id, rt, area, height, width, area_percent, source, classification, metrics_json
- `assay_chromatography_reports`: session_id, report_type, generated_at, payload_json or artifact path

Keep Empower as the acquisition/integration source of truth; BMS persistence is for review annotations and downstream reports.

## 8. Frontend implementation sequence

Phase 0: Lock contracts and tests

- Add tests for no fake assay data, hidden-mounted panes, and current import/cache behavior.
- Add backend tests for `hplc_calibration_curve.plotly_json`, quantification plot payload, and corrected baseline method names.
- Add frontend source tests that old square-class patterns do not reappear in HPLC subpanels after refactor.

Phase 1: Primitive layer

- Extend assay primitives listed above.
- Add test/source guards for soft surfaces and low-alpha borders so the UI does not regress to harsh white-outline cards.

Phase 2: Refactor `ChromatogramAnalysis.tsx`

- Convert `ChromatogramAnalysis` and `CalibrationCurve` to primitive-based input/output cards.
- Fix baseline options.
- Render calibration plot once backend returns it.
- Add peak marker/integration span support to the analysis plot.

Phase 3: Refactor `HplcQuantification`

- Render `plotly_json`.
- Remove internal API-contract copy from normal UI.
- Add immediate client-side validation for mismatched lengths, empty sample IDs, and non-finite values before submit.

Phase 4: Refactor `EmpowerImport.tsx` to workbench layout

- Split into focused components:
  - `EmpowerSourceShelf`
  - `EmpowerReviewViewer`
  - `EmpowerInspector`
  - `EmpowerTablesAndReports`
- Preserve cache restore/clear and import API behavior.
- Add selected injection/peak/group state.
- Add explicit plot-empty reason for CSV-only peak-table imports.

Phase 5: Selection/highlighting

- Add plot markers/spans and link table row selection to plot traces.
- Add selected inspector cards.
- Add filters for role, method, run date, group, flags, and excluded rows.

Phase 6: SST/report hardening

- Add SST criteria editor and pass/warn/fail badges.
- Add scoped report/export controls.
- Add durable session persistence if required for real review workflows.

Phase 7: Cross-surface validation

- Dev browser: verify Vite route used for active development.
- Stable hosted `/bms/assay`: rebuild/recreate `bms-web` and verify hashed bundle markers.
- Electron shell: verify hosted surface still loads and route base remains correct.
- Cordova/Android wrapper: rebuild/copy assets and verify no absolute `/bms/` assumptions in mobile bundle.

## 9. Acceptance criteria

### Runtime/API

- `/api/assay-analytics/capabilities` still identifies BMS as source of truth and legacy standalone parser as unused.
- `/api/assay-analytics/tools` still reports expected external tool metadata and runtime availability.
- `/api/assay-analytics/datasets` remains empty unless real stored datasets exist.
- HPLC analyze reports real engine metadata and correct baseline method labels.
- HPLC calibration returns a plot payload.
- HPLC quantification returns and frontend renders a calibration/fit plot.
- Empower import still accepts CSV/TXT/CDF/ARW/ZIP and rejects native database containers.
- CDF+ARW grouping remains one injection per CDF chromatogram with ARW metadata merged.

### UI

- Chromatography first viewport is task-first: import/source shelf and workbench action area appear without requiring the user to parse a long marketing page.
- Interactive controls, info cards, empty states, and result panels are visually distinguishable.
- All HPLC subpanels use shared assay primitives or documented shared BMS primitives.
- Selection state is visible and consistent across plot, inspector, and tables.
- CSV-only imports show a clear `no raw chromatogram traces in this export` state, not a mysterious missing plot.
- No normal-user surface exposes raw route/source-of-truth copy unless inside debug/details.

### Verification commands

Backend:

```bash
uv run --directory platform/api python -m pytest tests/test_assay_external_tool_integrations.py tests/test_assay_analytics_router.py tests/test_assay_upload_limits.py -q
```

Frontend:

```bash
cd platform/frontend
npx tsc -p tsconfig.tests.json
node --test node_modules/.tmp/frontend-tests/tests/*.test.js
npx tsc -b --pretty false
```

Stable runtime after rebuild:

```bash
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml build bms-api bms-web
docker compose -p biomodstack-core-runtime -f compose.core-runtime.yml up -d --no-deps --force-recreate bms-api bms-web
```

Live browser checks:

- Navigate `/bms/assay`.
- Switch qPCR -> Chromatography -> DOE and back; state should persist where intended.
- Import a real or fixture Empower export; verify summary, plots, tables, selection, and exports.
- Run pasted chromatogram analysis; verify Plotly plot, peak markers, and peak table.
- Run calibration and quantification; verify plots and result tables.
- Confirm browser console has zero JS errors.

## 10. Non-goals

- Do not add fake/demo/seeded assay datasets.
- Do not parse proprietary native Empower databases directly unless a real parser/integration is deliberately added.
- Do not make the standalone legacy parser a dependency.
- Do not disrupt `/designer`, NGS, Launcher, Results Viewer, BioXP, Electron, or Cordova shells while restyling Assay.

## 11. Summary decision

The outer Assay shell is partly harmonized, and the screenshot is older than the current live quantification panel. The remaining mismatch is real but narrower: chromatography internals still behave like a pre-harmonization prototype. The right move is not another cosmetic pass over the existing long page; it is a workbench refactor centered on source/import, central chromatogram/QC review, right-side selected-object inspector, and bottom tables/reports, with API contract fixes for baseline naming and missing/misrendered plots.
