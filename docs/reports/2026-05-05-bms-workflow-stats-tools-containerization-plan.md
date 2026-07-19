# BioModStack workflow/statistical tooling deep pass and progressive containerization plan

> **Historical / superseded:** This report records the former BioModStack-owned assay/statistics runtime. P1 retired that ownership from core; this is evidence, not current architecture or implementation guidance.

Date: 2026-05-05
Branch context: `test`
Scope: workflows, modules, scripts, API configuration, assay/statistical tool registry, and core runtime compose surface.

## Executive verdict

BioModStack should split **assay/statistics tools** into a dedicated optional `bms-stats-tools` service/image, but it should **not** try to move the retired antibody workflow stack into that container. The de novo workflow is deeply integrated with Nextflow plus workflow-native Apptainer/GPU containers and should remain workflow-native. The primary API should keep orchestration, auth, metadata, result ingestion, and light synchronous math; the stats-tools service should own heavyweight assay/statistical engines and advanced R-backed analysis.

The safest target is therefore:

- `bms-api`: control plane, routing, validation, job orchestration, metadata/result ingest, lightweight calculations.
- `bms-stats-tools`: optional worker/service for R/CRAN/Bioconductor, MOCCA2/chromatography, qPCR package backends, DOE/advanced stats, longer analytical jobs.
- Nextflow workflow-native containers: RFantibody, FAMPNN, Caliby, PPIFlow, Boltz/Protenix, AntiBERTy, ThermoMPNN, OpenMM, FrustraMPNN, ANARCII, AntiFold, ProteinMPNN, PyRosetta, etc.

This preserves the current on-command workflow behavior while removing the wrong pressure point: stats/R tooling bloat from the primary API image.

## Evidence summary from repo scan

### Runtime/compose surface

`compose.core-runtime.yml` currently defines:

- `bms-analytical-postgres`: PostgreSQL backing assay analytical data.
- `bms-api`: built from `docker/api.Dockerfile`, host-networked, owns `/api/health`, assay env, workflow adapter URL env, and BMS mounted state.
- `bms-cpu-power`: host-networked telemetry collector.
- `bms-web`: built from `docker/web.Dockerfile`, depends on API health.

There is no current `bms-stats-tools` service in compose. That is the natural new optional service boundary.

### Broad stats/scoring/tool scan highlights

The repo contains three different classes of tools that should not be treated as one blob:

1. **Assay/statistical tools**
   - `platform/api/services/assay_tool_integrations.py`
   - `platform/api/routers/assay_analytics.py`
   - `platform/api/pyproject.toml`
   - Tools/packages observed: `mocca2`, `qslib`, `qpcr`, `openpyxl`, `xlrd`, `pyDOE3`, `statsmodels`, `scikit-learn`, `bofire`, plus R-side `RDML`, `qpcR`, `qcc`, `emmeans`, `lme4`, `rsm`, DoE packages.
   - This is the correct split target.

2. **API/core scientific bookkeeping and light math**
   - `platform/api/services/result_ingester.py`
   - `platform/api/services/stage_review.py`
   - `platform/api/services/structure_utils.py`
   - `platform/api/services/cdr_annotator.py`
   - `platform/api/services/ipsae.py`
   - Includes lightweight NumPy/Biotite parsing, confidence metric extraction, CDR annotation, status/capability reporting, and result metadata. This should stay with `bms-api` unless a specific job becomes heavy/long-running.

3. **Workflow-native scientific/model containers**
   - `workflows/antibody_child.nf`
   - `workflows/maturation_child_core.nf`
   - `modules/*.nf`
   - `scripts/*` helpers invoked inside Nextflow tasks.
   - Tools include RFantibody, FAMPNN, Caliby, PPIFlow, ProteinMPNN, AntiFold, ANARCII, Boltz/Protenix, AntiBERTy, ThermoMPNN, OpenMM, FrustraMPNN, PyRosetta, Torch-heavy runtimes, etc.
   - These should remain Nextflow/Apptainer/runtime-native and not be pulled into `bms-stats-tools`.

## retired antibody workflow pass

### What is integrated

`workflows/antibody_child.nf` includes and coordinates a broad stack:

- RFantibody backbone generation: `modules/rfantibody`
- AntiFold / ANARCII sequence/numbering surfaces: `modules/antifold`, `modules/utils/anarci`
- FAMPNN and ProteinMPNN sequence design: `modules/fampnn`, `modules/proteinmpnn`
- Caliby sequence design/filtering: `modules/caliby`
- PPIFlow maturation/backbone refinement: child-spawn paths plus `workflows/maturation_child_core.nf` and `modules/ppiflow.nf`
- Boltz/Protenix validation: `modules/boltz`, `modules/structure_prediction`, `modules/antibody_batch`
- OpenMM relaxation/scoring: `modules/openmm`
- FrustraMPNN QC: `modules/frustrampnn`
- AntiBERTy immunogenicity/naturalness: `modules/antiberty`
- ThermoMPNN stability: `modules/thermompnn`

This is not “stats tooling” in the assay sense. It is a staged de novo design and validation workflow, with model containers, GPU tasks, and Nextflow fan-out/collect behavior. It should remain workflow-native.

### Actual PPIFlow/maturation objective path

The de novo PPIFlow maturation path is now explicit enough to classify:

- Parent workflow spawns maturation children (`SpawnMaturationJobs`) from `antibody_child.nf`.
- Parent payload includes objective semantics:
  - `ppiflow_objective_mode: paramValueOrDefault(params, 'ppiflow_objective_mode', null)`
  - `ppiflow_objective_threshold: paramValueOrDefault(params, 'ppiflow_objective_threshold', null)`
- Child core workflow `workflows/maturation_child_core.nf` runs:
  - `IdentifyAnchorResidues`
  - `RunPartialFlow`
  - optional ANARCII loop lookup
  - `PrepMaturationRedesign`
  - `RunMaturationFAMPNN`
  - `ScoreMaturationImprovement`
  - `FilterByMaturation`
- `scripts/score_maturation.py` computes local objective metrics and emits:
  - `selected_delta_interface_score`
  - `objective_score`
  - `selection_direction: lower_is_better`
  - `af3score_used: False`
- `scripts/filter_maturation.py` gates/ranks by `selected_delta_interface_score` for selected-interface mode or `objective_score` for other objective modes, with lower-is-better semantics.

This confirms the de novo maturation scoring path is **local workflow scoring**, not assay stats and not AF3Score.

### Validator confidence metrics are review/ingest, not inner-loop optimization

`platform/api/services/result_ingester.py` stores confidence metrics such as `ranking_score`, `ptm`, `iptm`, `protein_iptm`, `ligand_iptm`, chain PTM/iPTM, and also ingests PPIFlow maturation fields such as `ppiflow_objective_mode`, `ppiflow_objective_score`, and selected delta interface values.

That should stay in API/core because it is result ingestion/review metadata. It does **not** mean those validator metrics belong in `bms-stats-tools`, nor that AF3Score is used in the inner PPIFlow loop.

## Classification: where each class should live

### Keep in API/core runtime

These are control-plane or lightweight synchronous functions and should remain in `bms-api`:

- FastAPI routers, auth, validation, request normalization.
- Workflow launch orchestration and adapter client/control seams.
- Job DB, queue/status, result browser, result ingester/indexer.
- Analytical-store connection and durable metadata rows.
- Lightweight qPCR/DOE/stat calculations when they are cheap and already Python-native.
- Tool/capability registry endpoints that report stats-tools availability.
- Result metric extraction from Boltz/Protenix/RF3/PPIFlow outputs.
- CDR/structure parsing that is needed to present/review workflow outputs.
- Degraded-mode behavior when stats-tools is stopped.

Rationale: the API is the product control plane. Moving small math or metadata parsing over the network adds fragility without solving the build/OOM problem.

### Move to optional `bms-stats-tools`

These are the appropriate split targets:

- R/CRAN/Bioconductor stack:
  - `RDML`, `qpcR`, `chipPCR`, `qPCRtools`, `RQdeltaCT`, `tidyqpcr`, `HTqPCR`
  - `DoE.base`, `FrF2`, `rsm`, `AlgDesign`, `DoE.wrapper`, `qcc`, `emmeans`, `lme4`, `desirability`
- Heavy/advanced assay engines:
  - MOCCA2/chromatography pipelines if they are slow/heavy or drag large dependencies.
  - qslib/qpcr parser backends if they become dependency-heavy or long-running.
  - advanced DOE/optimization jobs using `bofire` or larger `statsmodels`/sklearn fitting flows.
- Plot/stat report generation that may need R, system libraries, or long CPU-bound execution.
- Package-status and capability probes for the advanced stats stack.

The API should call this service only when the requested tool needs it. If the service is disabled, endpoints should return a clear unavailable/degraded response for advanced operations and keep cheap API-local functionality live.

### Keep workflow-native / Nextflow containers

These should **not** be folded into stats-tools:

- RFantibody / RFantibody screening and wrappers.
- FAMPNN and ProteinMPNN sequence design.
- Caliby sequence design/filtering.
- PPIFlow partial-flow and maturation scoring/filtering.
- Boltz, Protenix, RF3/structure validation surfaces.
- AntiBERTy immunogenicity/naturalness scoring.
- ThermoMPNN stability scoring.
- ANARCII/AntiFold where invoked as workflow stages.
- OpenMM relaxation/MMGBSA-like scoring.
- FrustraMPNN QC.
- PyRosetta-heavy helpers inside workflow tasks.
- Torch/GPU model execution and model-checkpoint-bound tools.

Rationale: these are already Nextflow task boundaries with explicit labels/containers and model/data locality. Moving them into a generic stats service would break the current workflow control model and would not reduce the API image’s R/statistics pressure.

## Progressive containerization/control plan

### Phase 0: cap immediate build risk, keep behavior unchanged

- Cap R install parallelism in `docker/install_assay_r_packages.R` via `BMS_R_INSTALL_NCPUS=1` or `2`.
- Remove or defer unused R package groups from the API image if not attached to live endpoints.
- Keep current endpoints and on-command workflows unchanged.
- Add a visible capabilities response that separates:
  - `api_local`
  - `stats_tools_service`
  - `workflow_native`

Acceptance gates:

- `bms-api` builds without OOM/SIGKILL.
- `/api/health` remains 200.
- `/api/assay-analytics/tools` still returns the existing registry shape.
- One cheap API-local stats endpoint still works while stats-tools is absent.

### Phase 1: add dormant optional `bms-stats-tools` service

Add compose service, but do **not** make it required for boot:

```yaml
bms-stats-tools:
  build:
    context: .
    dockerfile: docker/stats-tools.Dockerfile
  container_name: biomodstack-stats-tools
  restart: unless-stopped
  profiles: ["stats-tools"]
  network_mode: host
  environment:
    BMS_STATS_TOOLS_HOST: 127.0.0.1
    BMS_STATS_TOOLS_PORT: ${BMS_STATS_TOOLS_PORT:-8798}
    BMS_ANALYTICAL_DATABASE_URL: ${BMS_ANALYTICAL_DATABASE_URL:-...}
  volumes:
    - type: bind
      source: ${BMS_STATE_DIR:-/mnt/BioModStack}
      target: ${BMS_CONTAINER_STATE_PATH:-/var/lib/biomodstack}
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8798/health', timeout=5).read()"]
```

Add API env knobs:

- `BMS_STATS_TOOLS_ENABLED=0|1`
- `BMS_STATS_TOOLS_URL=http://127.0.0.1:8798`
- `BMS_STATS_TOOLS_REQUIRED=0|1` default `0`
- `BMS_STATS_TOOLS_TIMEOUT_SECONDS=...`

Control behavior:

- Normal `core-runtime up` should not require stats-tools.
- `core-runtime up --with-stats-tools` or equivalent should include the compose profile.
- `stats-tools start|stop|restart|status|logs|health` should be first-class in the service manager, like the primary runtime controls.
- API startup should never fail just because optional stats-tools is stopped unless `BMS_STATS_TOOLS_REQUIRED=1`.
- Add a persistent top-right **STATS TOOLS** utility button beside the existing **POWER LIMITS** and **MSA SERVER** controls so the operator does not have to hunt through assay panels to discover why advanced tools are offline.

Top-right UI contract:

- Button label: `STATS TOOLS`.
- Indicator colors: green/healthy, amber/starting-or-degraded, red/unhealthy, slate/stopped-or-unavailable.
- The dropdown must include Start, Stop, Restart, Refresh, Health, and Logs actions.
- The dropdown must show a copyable simple command block for terminal parity, for example:
  - `bms stats-tools status`
  - `bms stats-tools start`
  - `bms stats-tools stop`
  - `bms stats-tools restart`
  - `bms stats-tools logs --tail 120`
- The offline/degraded copy in advanced assay/stat panels should point back to this button: `stats_tools_offline — use STATS TOOLS → Start stats-tools`.
- The button should use the same non-clipped utility-container pattern as Power/MSA: render outside the horizontal nav rail, `overflow-visible`, and a right-anchored dropdown sized to the viewport.

Acceptance gates:

- Core runtime starts with stats-tools stopped.
- Stats-tools can be started after the API is already running.
- `/api/assay-analytics/tools` shows advanced tools as unavailable/degraded when stopped and available when healthy.
- Stopping stats-tools does not kill `bms-api` or Nextflow workflows.
- The top-right STATS TOOLS dropdown can start/stop/restart the service, display health/log summaries, and expose the exact simple commands shown above.

### Phase 2: move R-backed and heavy assay operations behind service calls

Implement a small HTTP contract:

- `GET /health`
- `GET /capabilities`
- `GET /packages`
- `POST /analysis/qpcr/rdml`
- `POST /analysis/qpcr/advanced-fit`
- `POST /analysis/chromatography/mocca2`
- `POST /analysis/statistics/doe`
- `POST /analysis/statistics/model-fit`
- `POST /analysis/reports/render`

API call pattern:

- If operation is cheap/local: run in API.
- If operation needs advanced packages: call stats-tools.
- If stats-tools is unavailable: return a clear 503/424-style error with capability metadata, not fake fallback output.
- Preserve request/result schema so the frontend does not care which runtime produced the calculation.

Acceptance gates:

- R package status comes from `bms-stats-tools`, not `bms-api`.
- Advanced R-backed request succeeds when stats-tools is running.
- Same request fails honestly when stats-tools is stopped.
- Existing frontend panels show a specific “advanced stats worker offline” state, not a blank crash.

### Phase 3: tighten lifecycle control and observability

Add service-manager/UI controls equivalent to runtime controls:

- Start stats-tools.
- Stop stats-tools.
- Restart stats-tools.
- Health/status.
- Recent logs.
- Package/capability summary.
- “Required for advanced assay stats only” wording.

State model:

- `core_runtime.status`: API/web/Postgres/workflow adapter state.
- `stats_tools.status`: stopped/starting/healthy/unhealthy/unavailable.
- `stats_tools.enabled`: configured true/false.
- `stats_tools.required`: false by default.
- `stats_tools.capabilities`: package-backed features.

Acceptance gates:

- Operator can choose not to start stats-tools and still use core BMS.
- Operator can start stats-tools on demand before a heavy assay/stats run.
- Stop/restart does not interrupt de novo/Nextflow jobs.
- Logs distinguish API-local calculations from stats-tools-backed calculations.

### Phase 4: optional queued stats jobs, not required for MVP

Only after the service boundary is working:

- Add async job queue for long analytical runs.
- Persist request/result provenance into analytical Postgres.
- Add cancellation/timeouts/retry policy.
- Add resource limits for stats-tools container.

Do not block the initial split on a full distributed job system.

## Explicit non-goals for this split

- Do not containerize the whole retired antibody workflow into `bms-stats-tools`.
- Do not make stats-tools mandatory for normal BMS launch.
- Do not move BioXP/hardware control into stats-tools.
- Do not move Nextflow ownership into stats-tools.
- Do not fabricate results when stats-tools is stopped.
- Do not use this split to hide current workflow-native scoring semantics.

## De novo workflow-specific hardening recommendations

These are adjacent to the stats split, not part of the stats container itself:

1. Keep objective provenance in PPIFlow score/filter outputs.
   - Current score output already exposes `af3score_used: False` and lower-is-better direction; preserve that.

2. Keep objective propagation tests around `SpawnMaturationJobs`.
   - Parent `antibody_child.nf` now forwards objective mode/threshold to child payloads; protect it.

3. Do not use validator confidence metrics as implicit inner-loop objectives.
   - If AF3/Boltz/Protenix confidence reranking is desired, add it as outer-loop post-validation reranking, not inside every PPIFlow sample iteration by default.

4. Keep workflow-native containers independently versioned.
   - AntiBERTy/ThermoMPNN/OpenMM/FrustraMPNN are scoring/validation tools, but they live in workflow task containers because they depend on model/runtime locality.

## Recommended implementation artifacts

Add or update:

- `docker/stats-tools.Dockerfile`
- `platform/stats_tools/` or `platform/api/stats_tools_app.py` for the worker API.
- `compose.core-runtime.yml` optional `bms-stats-tools` profile.
- `.env.core-runtime.example` stats-tools knobs.
- `scripts/manage_desktop_services.py` stats-tools lifecycle commands.
- `platform/api/services/stats_tools_client.py`
- `platform/api/routers/system.py` or a dedicated runtime router for local-admin stats-tools lifecycle endpoints.
- `platform/frontend/src/components/Layout.tsx` top-right `StatsToolsMenu`, following the existing Power/MSA utility pattern.
- API tests for healthy/offline stats-tools behavior and local-admin lifecycle controls.
- Frontend/source tests proving the stats-tools button is outside the primary nav rail, has Start/Stop/Restart/Logs controls, and contains the simple command block.
- Docs under `docs/` explaining optional stats-tools startup and degradation.

## Minimal verification matrix

Before claiming done:

- Static/source:
  - compose config validates with and without `--profile stats-tools`.
  - API tests cover stats-tools unavailable/healthy routing.
  - Existing assay external tool registry tests still pass.
  - Existing de novo/PPIFlow contract tests still pass.

- Live core-runtime without stats-tools:
  - `bms-api` healthy.
  - `bms-web` healthy.
  - Analytical Postgres healthy.
  - `/api/assay-analytics/tools` reports advanced stats-tools unavailable, not fake-installed.
  - cheap API-local assay/stat path works.
  - Nextflow/de novo launch path remains unchanged.

- Live with stats-tools:
  - stats-tools health 200.
  - `/capabilities` lists installed advanced packages.
  - one R-backed/stat-heavy endpoint returns real package metadata.
  - stopping stats-tools flips capability state without killing API/web.

- Workflow-specific:
  - de novo Nextflow preview/smoke still sees workflow-native containers and child payload objective params.
  - PPIFlow maturation score/filter artifacts retain objective provenance.

## Bottom line

The remaining “stats tools” to isolate are primarily assay/statistical engines, especially R-backed and heavier analytical packages. The retired antibody workflow contains many scoring/model components, but they are not the same boundary: they are workflow-native scientific executors and should stay under Nextflow/Apptainer control.

Build the split as an **optional, controllable `bms-stats-tools` service**. Make it startable/stoppable independently like the primary runtime, but keep normal BMS and on-command de novo workflow function alive when it is off.
