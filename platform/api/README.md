# BioModStack Control Platform API

This FastAPI service is the control plane for BioModStack. It is not just a job
submission wrapper: it owns orchestration state, result metadata, lineage,
analysis scheduling, file access, sequence operations, runtime administration,
and hardware proxying.

## Entry point

- [main.py](main.py)

On startup the API initializes the database, starts the analysis worker, and
registers the router surface used by the web UI, Electron shell, local tooling,
and linked hardware clients. The GPU workflow orchestrator starts only when the
runtime is allowed to own workflow launches; guarded core-runtime mode skips that
scheduler ownership and relies on the host workflow adapter instead.

## Runtime role

In the current workstation architecture the API normally runs inside the
containerized core runtime, while Nextflow job ownership remains host-native via
`biomodstack-workflow-adapter.service`.

That means:

- API/web runtime is usually containerized
- job launch/cancel/running-job execution still crosses the workflow-adapter
  boundary
- browser, Electron, GTK, and optional mobile shells all talk to the same API
  contract

## Run locally

From `platform/api` in repo-first dev mode:

```bash
uv run uvicorn main:app --reload --port 8000
```

Or from the repo root through the shared launcher:

```bash
./start_ui.sh start
```

## Major router surface

The API currently includes routers for:

- models and input schemas
- jobs and queue/orchestration controls
- designs and persisted analyses
- files and artifact browsing
- analytics
- system and GPU status
- install-profile/runtime-state inspection
- workflow-adapter launch/cancel/running-jobs bridge routes
- framework lookup and MSA helpers
- nucleotide sequence storage and mol bio operations
- RCSB and Ribocentre helpers
- the compact BioXP profile/status/connection/protocol/job/command control plane
- mobile UI update/feed endpoints for optional phone shells

Router registration lives in [main.py](main.py).

## Notable local-admin/runtime routes

Important runtime/admin surfaces include:

- `/api/system/runtime-state`
- `/api/system/install-profile`
- `/api/workflow-adapter/health`
- `/api/workflow-adapter/launch`
- `/api/workflow-adapter/cancel`
- `/api/workflow-adapter/running-jobs`
- `/api/mobile-ui/channels/{channel}/manifest`
- `/api/mobile-ui/bundles/{channel}/{version}.zip`
- `/api/mobile-ui/files/{channel}/{version}/{asset_path}`

The system/install-profile routes are intended for localhost/testclient-scoped
administration, not general remote multi-tenant API exposure.

## Pathing and data roots

Path resolution is centralized in [paths.py](paths.py).

Important functions/roots include:

- code root:
  `get_code_root()`
- data root:
  `get_data_root()`
- results:
  `get_results_dir()`
- work:
  `get_work_dir()`
- analysis cache:
  `get_analysis_cache_dir()`
- containers:
  `get_container_dir()`
- weights:
  `get_weights_root()`
- database:
  `get_db_path()` / `get_db_url()`
- mobile-update payloads:
  `get_mobile_ui_updates_dir()`

Priority for database location is:

1. `DATABASE_URL`
2. `BMS_DB_PATH`
3. install profile
4. `${BMS_DATA}/biomodstack.db`
5. repo-local fallback

## Core runtime env vars

Common env vars include:

- `BMS_HOME`
- `BMS_DATA`
- `BMS_INPUTS`
- `BMS_DB_PATH`
- `DATABASE_URL`
- `BMS_WEIGHTS`
- `BMS_CONTAINER_DIR`
- `BMS_MSA_CACHE`
- `BMS_COLABFOLD_DB`
- `BMS_SABDAB_CACHE`
- `BMS_WORKFLOW_ADAPTER_URL`
- `BMS_MOBILE_UI_UPDATES_DIR`
- `BMS_FAN_CONTROL_BACKEND`
- `CORS_ORIGINS`

BioXP-specific env vars are documented in
[../../docs/Lab_Automation_MolBio_and_Sequencing.md](../../docs/Lab_Automation_MolBio_and_Sequencing.md).

## Operational responsibilities

The API is responsible for:

- normalizing launch params
- mapping UI model definitions to workflow runtime behavior
- persisting `Job`, `Design`, and `AnalysisRun` records
- tracking stage outputs and lineage
- serving artifacts back to the frontend
- scheduling post-hoc analyses and review refresh flows
- exposing runtime/install-profile state to local control surfaces
- brokering host-native workflow execution through the workflow adapter
- serving optional mobile shell update metadata/assets
- enforcing the bounded BioXP control-plane and mutation-admission contract

The BioXP router is intentionally compact and is not a generic robot proxy. It
contains bounded profile, status, explicit connection, BMS-local logs, offline
protocol, durable local job, typed command, and emergency-delivery routes.
Hardware-family, arbitrary-path, host-lifecycle, shell, and remote-log routes are
absent. See [../../docs/BioXP_Compact_Control_Plane.md](../../docs/BioXP_Compact_Control_Plane.md).

## Related docs

- [../../docs/README.md](../../docs/README.md)
- [../../docs/Platform_Overview.md](../../docs/Platform_Overview.md)
- [../../docs/Desktop_Runtime_and_Shell_Architecture.md](../../docs/Desktop_Runtime_and_Shell_Architecture.md)
- [../../docs/Results_and_Analysis.md](../../docs/Results_and_Analysis.md)
