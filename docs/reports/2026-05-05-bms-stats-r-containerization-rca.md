# BMS stats/R container build RCA and isolation spec

Date: 2026-05-05
Repo: `/home/dalab/biomodstack/biomodstack`
Branch observed: `test`

## Executive verdict

Yes: the R/statistics stack should be isolated from the main `bms-api` image.

This is **not** because every stats endpoint needs a separate microservice today. Most live analytics endpoints are synchronous Python/FastAPI code and are not individually heavy at runtime. The real problem is that the main API image currently carries a broad R + CRAN/Bioconductor build toolchain just to advertise/check optional assay tooling. That makes unrelated container rebuilds fragile, slow, and huge.

Recommended boundary:

1. **Short-term hardening:** cap R install parallelism and stop using host core count during Docker builds.
2. **Next tranche:** split R/assay-stat packages into a dedicated `bms-stats-tools` image/service with a small HTTP contract and/or one-shot job runner.
3. **Then:** move Python stats packages that are assay-only (`mocca2`, `pyDOE3`, `statsmodels`, `scikit-learn`, `bofire`, `qpcr`, `qslib`, spreadsheet import deps) behind a stats-tools boundary only if API image size/rebuild time remains painful after R is removed.

Do **not** split low-level native math helpers (`numpy`, `scipy`, small control-chart/capability calculations) prematurely; those are cheap enough and coupled to current request/response code.

## Current failure/RCA

### Observed runtime/build facts

- Current `docker/api.Dockerfile` installs R into the main API image:
  - `r-base`, `r-base-dev`
  - many heavy Debian R packages: `r-cran-tidyverse`, `r-cran-lme4`, `r-cran-emmeans`, `r-cran-igraph`, `r-cran-coin`, etc.
  - then runs `Rscript /app/docker/install_assay_r_packages.R`.
- Current image is large:
  - `biomodstack-core-runtime-bms-api:latest`: ~5.53 GB.
- Docker history shows heavy layers:
  - apt/R/system dependencies: ~1.14 GB
  - `uv sync --frozen --no-dev`: ~2.45 GB
  - `Rscript install_assay_r_packages.R`: ~279 MB
  - late `chown -R /app`: ~1.39 GB copy-up layer
- Docker build cache is heavily polluted by repeated multi-GB API image layers.
- Host Docker daemon has 125 GiB total RAM, but live free memory at inspection was much lower (`~39 GiB available`) and the R install script uses host CPU count as its parallelism source.

### Why error 137 is plausible here

Exit 137 means the process was SIGKILLed. In Docker builds this is usually either:

- kernel/container OOM killer,
- manual kill/timeout escalation,
- parent process killed by orchestration.

The R path has a direct OOM mechanism:

```r
Ncpus = max(1, parallel::detectCores() - 1)
```

On this workstation that resolves to ~47 parallel compile/install workers. That is fine for tiny packages and terrible for source-heavy R dependency chains. The script pins/compiles old `Matrix`, may compile archived `GGally`, installs CRAN packages one-by-one with dependency resolution, then calls Bioconductor for `HTqPCR`. Parallel C/C++/Fortran compilation across `Matrix`, `lme4`-adjacent deps, and tidyverse/Bioc deps can spike memory. If this runs during a normal BMS rebuild while GPUs/folding/Hermes/containers are active, a 137 is expected.

Even when the R layer succeeds, it is a poor rebuild dependency: every unrelated API code change goes through an image that contains R and assay-stat binaries.

### Secondary build-design issues

- **R install is in the API image, not an assay module.** Any API rebuild risks R dependency churn.
- **R availability is only used by status/registry checks today.** There is no production code path invoking R for actual analysis results in the router.
- **Runtime registry checks shell out to `Rscript` per package.** Cached after first hit, but cold `/tools`/`/capabilities` can spend seconds enumerating packages and can timeout under load.
- **`bms-cpu-power` builds from the same API Dockerfile.** That tiny helper inherits the full API/R/stats image even though it only needs a small Python script.
- **Late recursive `chown -R /app` creates a huge layer.** The current Dockerfile copies and builds first, then chowns, causing copy-up bloat.

## How stats/assay tooling is currently used

### Backend source of truth

Primary file:

- `platform/api/routers/assay_analytics.py`

Tool registry/status file:

- `platform/api/services/assay_tool_integrations.py`

Install/build files:

- `docker/api.Dockerfile`
- `docker/install_assay_r_packages.R`
- `platform/api/pyproject.toml`

### Live API surfaces

Assay analytics is exposed under `/api/assay-analytics`.

qPCR endpoints:

- `POST /analysis/qpcr/standard-curve`
- `POST /analysis/qpcr/quantify`
- `POST /analysis/qpcr/delta-cq`
- `POST /analysis/qpcr/delta-delta-cq`
- `POST /analysis/qpcr/anova-dunnett`
- `POST /analysis/qpcr/upload-csv`
- `POST /analysis/qpcr/upload-excel`
- `POST /analysis/qpcr/upload-eds`
- `GET /analysis/qpcr/imports`
- `GET /analysis/qpcr/imports/{analytical_import_id}`

HPLC/chromatography endpoints:

- `POST /analysis/hplc/analyze`
- `POST /analysis/hplc/quick-analyze`
- `POST /analysis/hplc/calibration-curve`
- `POST /analysis/hplc/quantify`
- `POST /analysis/hplc/plasmid/isoforms`
- `POST /analysis/hplc/empower/plasmid-isoforms`
- `POST /analysis/hplc/empower/import`
- `GET /analysis/hplc/empower/sst`
- `PUT /analysis/hplc/empower/injections/{injection_id}`
- export routes for SST/plasmid tracking

JMP-like/statistics endpoints:

- `POST /analysis/control-chart`
- `POST /analysis/capability`
- `POST /analysis/doe/design`
- `POST /analysis/doe/rsm`
- `POST /analysis/hypothesis/t-test/one-sample`
- `POST /analysis/hypothesis/t-test/two-sample`
- `POST /analysis/hypothesis/t-test/paired`
- `POST /analysis/hypothesis/anova`
- `POST /analysis/regression/simple`

Metadata/status endpoints:

- `GET /tools`
- `GET /capabilities`
- `GET /analytical-store/status`
- dataset/import listing endpoints

### Actual compute engines used today

Python/native in current router:

- Basic qPCR standard curves, absolute quantification, delta-Cq/ddCq: Python + `numpy` + `scipy.stats`.
- qPCR ANOVA/Dunnett-style comparison: `scipy.stats` plus simplified Welch t-tests, not true R `emmeans`/Dunnett machinery.
- HPLC peak analysis: Python + `scipy.signal`; optionally labels MOCCA2 path and imports/uses `mocca2` for baseline/peak engine semantics where available.
- DOE design: `pyDOE3`.
- RSM/regression: `statsmodels.OLS`.
- Control charts/capability: in-house Python formulas.
- Hypothesis tests: `scipy.stats`.
- qPCR import parsing: in-router CSV/XLSX/EDS parsing plus `qslib`/spreadsheet deps as package promises.

R packages in current code:

- `chromConverter`, `RDML`, `qpcR`, `chipPCR`, `qPCRtools`, `RQdeltaCT`, `tidyqpcr`, `HTqPCR`, `DoE.base`, `FrF2`, `rsm`, `AlgDesign`, `DoE.wrapper`, `qcc`, `emmeans`, `lme4`, `desirability` are registered as integrated tools.
- Current Python API code does **not** call those packages for normal analysis outputs.
- `services/assay_tool_integrations.py` only checks whether `Rscript` and packages are installed, using `requireNamespace()`.

### Frontend consumers

Main client:

- `platform/frontend/src/api/client.ts`

Frontend areas:

- `platform/frontend/src/components/statistics/index.tsx`
- `platform/frontend/src/components/qpcr/*`
- `platform/frontend/src/components/hplc/*`
- `platform/frontend/src/components/assay/*`
- broader `AssayAnalytics.tsx` shell/navigation

The frontend calls `/api/assay-analytics` directly. It does not care whether implementation is in-process, an internal service, or an async job, as long as response schemas remain stable.

## Pushback: what would be dumb to split

Splitting every tiny formula endpoint into a network service would be overkill.

Keep these in-process initially:

- simple Cq/delta-Cq calculations,
- simple standard curve fits,
- small control chart/capability calculations,
- small `scipy.stats` t-tests/ANOVA,
- simple HPLC CSV normalization/persistence.

These are not what is breaking builds. The build failure is from installing and compiling the R ecosystem into the monolithic API image.

## Proposed target architecture

### Services

Add one dedicated service:

- service name: `bms-stats-tools`
- container name: `biomodstack-stats-tools`
- image: built from `docker/stats-tools.Dockerfile`
- network: host or private compose network; for current host-network BMS simplicity use `127.0.0.1:${BMS_STATS_TOOLS_PORT:-8012}`
- health: `GET /health`
- capabilities: `GET /capabilities`

Main API gets:

- `BMS_STATS_TOOLS_URL=http://127.0.0.1:8012`
- feature flag: `BMS_STATS_TOOLS_MODE=auto|required|disabled`

### API contract

Minimal HTTP contract:

- `GET /health`
  - returns package/runtime status and image build metadata.
- `GET /capabilities`
  - returns R/Python tool availability, versions, and supported operation IDs.
- `POST /tools/r/package-status`
  - request: `{ "packages": ["qpcR", "rsm", ...] }`
  - response: per-package available/version/error.
- `POST /analysis/qpcr/r-efficiency-fit`
  - R-backed qPCR model fitting for cases that need `qpcR`/`chipPCR`, not current simple ddCq math.
- `POST /analysis/doe/r-design`
  - R-backed `DoE.base`/`FrF2`/`rsm`/`AlgDesign` designs when Python `pyDOE3` is insufficient.
- `POST /analysis/stats/mixed-effects`
  - R `lme4` + `emmeans` for batch/plate/operator mixed effects and post-hoc estimates.
- `POST /analysis/spc/qcc`
  - R `qcc` calculations where we need validated SPC parity.

Main BMS API should remain the public surface:

- `/api/assay-analytics/...` stays stable.
- The API routes either use local Python implementation or proxy selected advanced operations to `bms-stats-tools`.
- `/tools` and `/capabilities` should aggregate local Python package status plus stats-service package status.

### Image strategy

Recommended split:

1. `docker/api.Dockerfile`
   - remove `r-base`, `r-base-dev`, R CRAN apt packages, and `Rscript install_assay_r_packages.R`.
   - keep Python runtime deps needed by main API.
   - optionally move assay-only Python packages later.
2. `docker/stats-tools.Dockerfile`
   - base on Rocker image, e.g. `rocker/r-ver:4.3` or `rocker/r2u` if Ubuntu binary R packages are desired.
   - install R packages with pinned versions and low parallelism.
   - install a minimal Python/FastAPI shim or plumber R API.
   - set `BMS_R_INSTALL_NCPUS=2` by default.
3. `docker/cpu-power.Dockerfile`
   - tiny Python image for CPU-power collector, not full API image.

### Build hardening requirements

- Never use `parallel::detectCores() - 1` unbounded in Docker builds.
- Add build arg/env:

```dockerfile
ARG BMS_R_INSTALL_NCPUS=2
ENV BMS_R_INSTALL_NCPUS=${BMS_R_INSTALL_NCPUS}
```

and in R:

```r
ncpus <- as.integer(Sys.getenv("BMS_R_INSTALL_NCPUS", "2"))
ncpus <- max(1, min(ncpus, 4))
```

- Prefer binary R packages where possible.
- Use `renv.lock`, `pak`, or r2u/pinned apt packages so package solver drift does not break unrelated BMS builds.
- Put R package install before copying the whole repo if possible, so cache invalidation does not reinstall R on every source edit.
- Avoid late recursive `chown -R /app`; use `COPY --chown` or chown only mutable dirs.
- Add a build-time smoke:

```bash
Rscript -e 'pkgs <- c("qpcR","rsm","qcc","lme4","emmeans"); stopifnot(all(pkgs %in% rownames(installed.packages())))'
```

### Current implemented hardening

As of the 2026-05-08 rebuild proof pass, the first isolation step is implemented as a multi-stage split inside `docker/api.Dockerfile`:

- `api-runtime`: normal `bms-api` / `bms-cpu-power` target, intentionally before the stats target and free of the R install layer.
- `stats-tools-runtime`: optional `bms-stats-tools` target containing the R package install path.
- `compose.core-runtime.yml` pins `bms-api` and `bms-cpu-power` to `api-runtime`, and pins `bms-stats-tools` to `stats-tools-runtime`.
- `.dockerignore` excludes local `.env` / `.env.*` so accidental runtime secrets do not enter build context.
- `scripts/bms_api_image_proof.py` and `./scripts/bms api-image preflight|plan` provide a repeatable, non-destructive contract check and exact rebuild/recreate plan for the API image path.

Important operational lesson: setting a Docker build `target` was not sufficient by itself in this environment. The lightweight `api-runtime` stage must appear before the heavy `stats-tools-runtime` stage, otherwise legacy/linear builder behavior can still traverse the R layer.

Current proof commands:

```bash
./scripts/bms api-image preflight
./scripts/bms api-image plan
```

`preflight` should report `ok=true`, `api_runtime_stage_before_stats_tools_stage=true`, `api_runtime_prefix_has_r_stack=false`, and no forbidden R markers in the API runtime prefix.

## Migration plan

### Phase 0: stop the bleeding

- Patch `install_assay_r_packages.R` to use bounded `BMS_R_INSTALL_NCPUS`.
- Remove huge `r-cran-tidyverse` from `api.Dockerfile` unless a specific package requires the meta-package.
- Reorder/chown Dockerfile to avoid 1.39 GB chown copy-up.
- Add a build note: rebuild API with no parallel R install if RAM pressure exists.

Validation:

- `docker build -f docker/api.Dockerfile --build-arg BMS_R_INSTALL_NCPUS=2 .`
- inspect `docker history` and image size.
- `/api/assay-analytics/tools` still reports expected packages if R remains in API during Phase 0.

### Phase 1: introduce stats-tools service

Files to add:

- `docker/stats-tools.Dockerfile`
- `platform/stats_tools/app.py` or equivalent
- `platform/stats_tools/requirements.txt` / `pyproject.toml`
- `platform/stats_tools/r/install_packages.R`
- tests under `platform/api/tests/test_stats_tools_client.py` and `platform/stats_tools/tests/`

Compose changes:

- add `bms-stats-tools` service.
- add `BMS_STATS_TOOLS_URL` to `bms-api`.
- make API not hard-fail if stats-tools is down unless mode is `required`.

API changes:

- add `services/stats_tools_client.py`.
- change `assay_tool_integrations.py` so R package status comes from stats-tools when available, not local `Rscript`.
- keep local Python status in main API.
- update `/capabilities` to distinguish:
  - `local_python`
  - `external_stats_tools`
  - `unavailable/degraded`

Validation:

- main API image builds without R.
- stats-tools image builds independently.
- `/api/assay-analytics/tools` returns R package versions through stats-tools.
- API still boots when stats-tools is down and marks R-backed advanced features degraded.

### Phase 2: route real advanced stats to stats-tools

Move only advanced or R-specific operations first:

- true `qpcR` curve fitting,
- `RDML` conversion,
- `chromConverter` conversion,
- `DoE.base`/`FrF2` designs not covered by `pyDOE3`,
- `lme4`/`emmeans` mixed effects/posthoc,
- `qcc` SPC parity.

Leave simple Python calculations local.

### Phase 3: optionally split assay Python tools

Only after measuring API image size/rebuild time:

- consider moving `mocca2`, `bofire`, `qpcr`, `qslib`, and spreadsheet import paths into the same stats-tools service or an `assay-tools` service.
- Do not do this until R is removed and the remaining pain is quantified.

## Tests/contracts to update

Existing tests that hard-code current monolith will need adjustment:

- `platform/api/tests/test_core_runtime_scaffold.py`
  - currently expects services exactly `{bms-api, bms-analytical-postgres, bms-cpu-power, bms-web}`.
  - update to include `bms-stats-tools` and eventually `bms-cpu-power` using its own Dockerfile.
- `platform/api/tests/test_assay_external_tool_integrations.py`
  - currently asserts `docker/api.Dockerfile` contains `r-base` and `Rscript`.
  - should assert `docker/stats-tools.Dockerfile` contains the R toolchain and API Dockerfile does not.
- Add contract tests for degraded mode when stats-tools is unavailable.
- Add live smoke tests for `/tools`, `/capabilities`, and selected advanced endpoints.

## Final recommendation

The move is justified. The current R install path is exactly the kind of hodge-podged monolith that makes BMS container rebuilds flaky. The sane boundary is **not** “all stats over network”; it is “R and advanced assay/stat package ecosystems in a dedicated module, while simple Python calculations remain local.”

Prioritize:

1. cap R build parallelism now,
2. split R into `bms-stats-tools`,
3. remove R from `bms-api`,
4. split CPU-power out of API image,
5. only then consider moving Python assay-stat packages if image size/rebuild metrics still justify it.
