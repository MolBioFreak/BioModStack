# BioModStack stats/assay tooling inventory for isolation

> **Historical / superseded:** This report records the former BioModStack-owned assay/statistics runtime. P1 retired that ownership from core; this is evidence, not current architecture or implementation guidance.

Date: 2026-05-05
Repo: `/home/dalab/biomodstack/biomodstack`

## Verdict

The stats/assay tool surface is broader than R. The clean split should be a **stats/assay tools module** rather than an **R-only sidecar**.

Recommended boundary:

- Keep the main `bms-api` responsible for auth/session/routing, dataset bookkeeping, analytical-store CRUD, lightweight validation, and very small synchronous calculations.
- Move heavyweight or specialized scientific tooling into a dedicated module/service/image, tentatively `bms-stats-tools` / `biomodstack-stats-tools`.
- Include both R and selected Python scientific packages in that module when they are primarily assay/statistics engines rather than core BMS control-plane dependencies.

This avoids only solving the current R build-OOM problem while leaving the API image bloated with chromatography, qPCR importer, DOE, modeling, and visualization-engine dependencies.

## Current code evidence

Primary source files inspected:

- `platform/api/pyproject.toml`
- `platform/api/services/assay_tool_integrations.py`
- `platform/api/routers/assay_analytics.py`
- `docker/api.Dockerfile`
- `docker/install_assay_r_packages.R`
- `platform/frontend/package.json`

The current registry is in `platform/api/services/assay_tool_integrations.py` as `_TOOL_DEFINITIONS`. It explicitly models external tools by category and adapter type.

## Tool inventory by domain

### 1. Chromatography / HPLC / Empower / plasmid isoforms

**Python/runtime tools**

- `mocca2`
  - Category: chromatography
  - Current install: `platform/api/pyproject.toml`
  - Current use: `assay_analytics.py` imports it inside `/analysis/hplc/analyze` when `baseline_method` starts with `mocca2`; used for baseline estimation and peak picking.
  - Split recommendation: move to `bms-stats-tools`.
  - Reason: specialized chromatography signal-processing engine; not core control plane.

- `scipy.signal`
  - Category: chromatography/statistics numerical core
  - Current use: peak finding, peak widths, Savitzky-Golay smoothing, generic HPLC path.
  - Split recommendation: mixed.
    - Keep `scipy` in API if other non-assay features require it.
    - But route chromatography-heavy signal processing through `bms-stats-tools`.

- `scipy.io.netcdf_file`
  - Category: instrument import
  - Current use: Empower/AIA `.cdf` parser in `assay_analytics.py`.
  - Split recommendation: move CDF parsing into `bms-stats-tools` or `bms-assay-importers` submodule.
  - Reason: instrument-parser blast radius and file-format dependencies belong with assay tooling.

- In-house ARW/CDF grouping and Empower parsing glue
  - Category: import/adapter glue
  - Current use: `assay_analytics.py` handles CDF + ARW grouping, metadata extraction, SST/plasmid review payloads.
  - Split recommendation: move parser/analysis implementation; keep API endpoint contracts and persistence bookkeeping in main API.

**R tools**

- `chromConverter`
  - Category: chromatography import/format conversion
  - Current install: `docker/install_assay_r_packages.R`
  - Current registry: `adapter_type=r_package`
  - Split recommendation: move to `bms-stats-tools`.

### 2. qPCR / QuantStudio / StepOnePlus / RDML / EDS

**Python/runtime tools**

- `qslib`
  - Category: import
  - Current install: `platform/api/pyproject.toml`
  - Current use: QuantStudio `.eds` importer path in `/analysis/qpcr/upload-eds`, with BMS ZIP/XML fallback when qslib rejects schema.
  - Split recommendation: move to `bms-stats-tools` or an `assay-importers` layer inside that image.
  - Reason: specialized instrument file parser.

- `openpyxl`
  - Category: import
  - Current use: `.xlsx` qPCR/QuantStudio/StepOnePlus workbook parsing.
  - Split recommendation: can remain in API if used broadly, but qPCR-specific parsing should move with the stats/import worker.

- `xlrd`
  - Category: import
  - Current use: legacy `.xls` qPCR workbook parsing.
  - Split recommendation: move to stats/import worker unless other core API surfaces need legacy Excel.

- Python `qpcr`
  - Category: qPCR analysis
  - Current install: `platform/api/pyproject.toml`
  - Current registry: Python qPCR delta/delta-delta Ct workflow package.
  - Current use appears more capability/registry-oriented than central endpoint execution; core qPCR math is mostly in-house in `assay_analytics.py`.
  - Split recommendation: move to `bms-stats-tools` when wired for actual analysis outputs.

- In-house QuantStudio EDS ZIP/XML parser
  - Category: qPCR importer + curve analysis
  - Current use: fallback parser for `plate_setup.xml`, `multicomponentdata.xml`, `analysis_protocol.xml`, threshold/baseline Cq estimation.
  - Split recommendation: move to `bms-stats-tools` implementation layer; keep API endpoint and DB persistence contract in main API.

- In-house qPCR standard curve / absolute quant / ΔCq / ΔΔCq / ANOVA-Dunnett
  - Category: lightweight stats/qPCR math
  - Current use: synchronous endpoints in `assay_analytics.py`.
  - Split recommendation: not mandatory to move immediately.
  - Reason: cheap Python math can stay in API initially; eventually route through service for consistency if the module becomes the canonical assay-analysis engine.

**R tools**

- `RDML`
  - Purpose: MIQE/RDML import-export; bridge into qpcR/chipPCR workflows.
  - Split recommendation: move.

- `qpcR`
  - Purpose: sigmoidal qPCR model fitting, amplification efficiency, curve-level qPCR analysis.
  - Split recommendation: move.

- `chipPCR`
  - Purpose: raw amplification curve preprocessing and efficiency analysis.
  - Split recommendation: move.

- `qPCRtools`
  - Purpose: standard curve, amplification efficiency, 2^-ddCt workflows.
  - Split recommendation: move.

- `RQdeltaCT`
  - Purpose: relative quantification by ΔCt/ΔΔCt.
  - Split recommendation: move.

- `tidyqpcr`
  - Purpose: tidy plate/Cq qPCR workflows.
  - Split recommendation: move.

- `HTqPCR`
  - Purpose: high-throughput qPCR plate/replicate analysis.
  - Split recommendation: move.

### 3. DOE / JMP-like statistics / RSM / optimization

**Python/runtime tools**

- `pyDOE3`
  - Category: DOE generation
  - Current use: `/analysis/doe/design`; generators include `ff2n`, `ccdesign`, `bbdesign`, `pbdesign`.
  - Split recommendation: move to `bms-stats-tools` for canonical DOE generation, but API can keep a temporary local path until the worker contract is stable.

- `statsmodels`
  - Category: classical statistics/modeling
  - Current use: `/analysis/doe/rsm` and regression endpoints via `statsmodels.OLS`; supports RSM terms and regression diagnostics.
  - Split recommendation: move for advanced stats/RSM; small API local fallback acceptable during migration.

- `scikit-learn`
  - Category: modeling/preprocessing/optimization helpers
  - Current registry: external assay analytics tool; installed in API.
  - Current observed endpoint usage is limited/registry-oriented in this pass.
  - Split recommendation: move when used for assay/modeling workflows. Avoid making `bms-api` own sklearn unless non-assay core depends on it.

- `bofire`
  - Category: experiment design / Bayesian optimization
  - Current registry/install: `platform/api/pyproject.toml`; external tool registry.
  - Split recommendation: move.
  - Reason: heavyweight specialized optimization stack; likely to pull significant dependencies and belongs with design/optimization tooling.

- `scipy.stats`
  - Category: hypothesis tests/capability/control charts
  - Current use: t-tests, ANOVA, capability/control endpoints.
  - Split recommendation: mixed.
    - Keep minimal scipy if generally needed.
    - Prefer stats worker for advanced/JMP-like analytical workloads and batch jobs.

- `numpy` / `pandas`
  - Category: base numerical/dataframe substrate
  - Current install: API pyproject; pandas also used elsewhere per comment for FrustraMPNN API.
  - Split recommendation: do not blindly remove from API. These are likely broad core dependencies. But assay worker will also need them.

**R tools**

- `DoE.base`
  - Purpose: classical DOE base utilities, full factorials, orthogonal arrays, design-quality criteria.
  - Split recommendation: move.

- `FrF2`
  - Purpose: two-level fractional factorial designs and alias structures.
  - Split recommendation: move.

- `rsm`
  - Purpose: response-surface methodology, steepest ascent, canonical analysis, contours/surfaces.
  - Split recommendation: move.

- `AlgDesign`
  - Purpose: D-/A-/I-optimal candidate-list DOE generation.
  - Split recommendation: move.

- `DoE.wrapper`
  - Purpose: wrappers around classical R DOE packages.
  - Split recommendation: move.

- `qcc`
  - Purpose: SPC/control charts and process capability.
  - Split recommendation: move for R-backed SPC; Python simple SPC can temporarily stay local.

- `emmeans`
  - Purpose: estimated marginal means and post-hoc comparisons.
  - Split recommendation: move.

- `lme4`
  - Purpose: mixed-effects models for plate/run/operator/batch random effects.
  - Split recommendation: move.

- `desirability`
  - Purpose: multi-response desirability optimization.
  - Split recommendation: move.

- Debian-installed R support packages currently in `docker/api.Dockerfile`
  - `r-cran-coin`
  - `r-cran-doparallel`
  - `r-cran-dorng`
  - `r-cran-emmeans`
  - `r-cran-ggally`
  - `r-cran-igraph`
  - `r-cran-lme4`
  - `r-cran-matrixmodels`
  - `r-cran-tidyverse`
  - Split recommendation: move all of this out of `bms-api`; trim aggressively in the new stats image to packages that back implemented endpoints.

### 4. Visualization/report payload tooling

**Frontend/browser tools**

- `plotly.js-dist-min`
  - Current install: `platform/frontend/package.json`
  - Purpose: actual chart rendering.
  - Split recommendation: keep frontend-side. This is not an API-container build issue.

- `react-plotly.js`
  - Current install: `platform/frontend/package.json`
  - Purpose: React Plotly wrapper.
  - Split recommendation: keep frontend-side.

**Backend payload generation**

- In-house Plotly JSON construction in `assay_analytics.py`
  - Current use: qPCR standard curves, qPCR plate heatmap/amplification/standard-curve payloads, HPLC chromatograms/QC/composition, control chart, capability, DOE/RSM plots.
  - Split recommendation: eventually have `bms-stats-tools` return Plotly-compatible JSON for analysis outputs. Keep frontend rendering in `bms-web`.

### 5. Analytical persistence / data-store tools

These are not stats engines but are tightly adjacent to assay analytics.

- `asyncpg` + SQLAlchemy analytical-store models
  - Current use: assay analytical Postgres persistence.
  - Split recommendation: keep ownership in main API for now, or use a strict API-mediated persistence pattern.
  - Reason: source-of-truth dataset IDs, auth, provenance, and cross-run query API belong to BMS control plane. The stats worker should compute/parse and return artifacts; API decides what gets persisted.

- `bms-analytical-postgres`
  - Current compose service: analytical Postgres.
  - Split recommendation: keep as support service, not inside stats worker.

## Recommended split classes

### A. Move into `bms-stats-tools` immediately

These are high-confidence non-core assay/stat engines or instrument importers:

- R runtime / `Rscript`
- `chromConverter`
- `RDML`
- `qpcR`
- `chipPCR`
- `qPCRtools`
- `RQdeltaCT`
- `tidyqpcr`
- `HTqPCR`
- `DoE.base`
- `FrF2`
- `rsm`
- `AlgDesign`
- `DoE.wrapper`
- `qcc`
- `emmeans`
- `lme4`
- `desirability`
- R support stack currently installed into API: `coin`, `doParallel`, `doRNG`, `GGally`, `igraph`, `tidyverse`, `MatrixModels`, etc.
- `mocca2`
- `qslib`
- `pyDOE3`
- `bofire`

### B. Move once service contract exists / keep temporary local fallback

These are used by live endpoints today, so migrate behind a compatibility seam rather than ripping them out first:

- `statsmodels`
- `scipy.signal` HPLC path
- `scipy.stats` hypothesis/control/capability path
- `scipy.io.netcdf_file` Empower CDF parser
- qPCR EDS ZIP/XML parser
- HPLC/Empower parser and review assembly
- Plotly JSON generation for assay outputs

### C. Probably keep in main API too

These have broad utility beyond the assay worker and should not be removed blindly:

- `numpy`
- `pandas`
- `openpyxl` if other non-assay import/export paths use it
- `SQLAlchemy`, `asyncpg`, analytical-store API code
- auth/session/user-facing routing
- dataset metadata/provenance endpoints

## Proposed module/service shape

`bms-stats-tools` should expose a typed HTTP or internal RPC contract, not raw shell execution.

Minimum endpoints:

- `GET /health`
- `GET /capabilities`
- `GET /packages`
- `POST /qpcr/standard-curve`
- `POST /qpcr/relative-quantification`
- `POST /qpcr/import/eds`
- `POST /qpcr/import/excel`
- `POST /chromatography/analyze`
- `POST /chromatography/import/empower`
- `POST /chromatography/plasmid-isoforms`
- `POST /doe/design`
- `POST /doe/rsm`
- `POST /stats/control-chart`
- `POST /stats/capability`
- `POST /stats/hypothesis`
- `POST /stats/regression`

The main API should call this service and attach:

- BMS auth/session context
- dataset IDs
- analytical import IDs
- source file persistence
- status/provenance metadata
- user-visible audit trails

## Migration order

1. Add the service scaffold and capability endpoint.
2. Move R installation out of `docker/api.Dockerfile` first.
3. Move R package availability checks from local `Rscript` to stats-service `/packages`.
4. Move `mocca2` HPLC analysis behind the stats-service, with API fallback only during rollout.
5. Move qPCR EDS/Excel import and qPCR curve analysis.
6. Move DOE/RSM/statistics endpoints.
7. Slim `bms-api` pyproject/Dockerfile after every moved endpoint has tests and live smoke coverage.

## Pushback / caution

Do not turn every tiny formula into a network hop on day one. The right architecture is not “R microservice”; it is **assay/statistics worker owns specialized scientific engines**, while `bms-api` owns product/control-plane state.

The split is justified because the current toolset includes R, qPCR importers, chromatography engines, DOE libraries, Bayesian optimization, scipy/statsmodels modeling, and Plotly payload generation — not just one failing R package install.
