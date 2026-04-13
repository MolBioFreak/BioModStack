# BioModStack Control Platform API

This FastAPI service is the control plane for BioModStack. It is not just a job
submission wrapper: it owns orchestration state, result metadata, lineage,
analysis scheduling, file access, sequence operations, and hardware proxying.

## Entry Point

- [main.py](main.py)

On startup the API:

- initializes the database
- starts the GPU orchestrator
- starts the analysis worker
- exposes model, job, result, file, mol bio, NGS-adjacent, and robotics routes

## Run Locally

From `platform/api`:

```bash
uv run uvicorn main:app --reload --port 8000
```

Or from the repo root:

```bash
./start_ui.sh
```

## Major Router Surface

Included routers currently cover:

- models and input schemas
- jobs and queue/orchestration controls
- designs and analyses
- files and artifact browsing
- analytics
- system and GPU status
- framework lookup
- MSA helpers
- nucleotide sequence storage
- molecular biology operations
- RCSB and Ribocentre helpers
- BioXP robotics linkage/proxy routes

The router registration lives in [main.py](main.py).

## Pathing and Data Roots

Path resolution is centralized in [paths.py](paths.py).

Important functions/roots:

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
- DB:
  `get_db_path()` / `get_db_url()`

Priority for database location:

1. `DATABASE_URL`
2. `BMS_DB_PATH`
3. `${BMS_DATA}/biomodstack.db`
4. repo-local fallback

## Core Runtime Env Vars

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
- `BMS_FAN_CONTROL_BACKEND`
- `CORS_ORIGINS`

BioXP-specific env vars are documented in
[../../docs/Lab_Automation_MolBio_and_Sequencing.md](../../docs/Lab_Automation_MolBio_and_Sequencing.md).

## Operational Role

The API is responsible for:

- normalizing launch params
- mapping UI model definitions to workflow runtime behavior
- tracking stage outputs and lineage
- persisting `Job`, `Design`, and `AnalysisRun` records
- serving artifacts back to the frontend
- running post-hoc analysis and review refresh flows

## Related Docs

- [../../docs/README.md](../../docs/README.md)
- [../../docs/Platform_Overview.md](../../docs/Platform_Overview.md)
- [../../docs/Results_and_Analysis.md](../../docs/Results_and_Analysis.md)
- [../../docs/ai_guidance/Database_Instructions.md](../../docs/ai_guidance/Database_Instructions.md)
- [../../docs/ai_guidance/Centralization_and_Standardization.md](../../docs/ai_guidance/Centralization_and_Standardization.md)
