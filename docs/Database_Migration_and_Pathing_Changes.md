Database Migration and Pathing Changes
======================================

Completed: 2026-01-26 18:08 CST

Summary
-------
- Centralized path resolution in `platform/api/paths.py` with env overrides
  (`BMS_HOME`, `BMS_DATA`, `BMS_DB_PATH`, `DATABASE_URL`, `BMS_WEIGHTS`,
  `BMS_COLABFOLD_DB`, `BMS_MSA_CACHE`, `BMS_INPUTS`, `BMS_SABDAB_CACHE`).
- Replaced hardcoded paths across API/services/scripts/Nextflow to use
  the path helpers for portability and consistent DB resolution.
- Set a canonical DB path via `get_db_path()` / `get_db_url()` so the API
  and CLI scripts target the same SQLite file by default.

Database Stability and Migrations
---------------------------------
- Added migration runner (`platform/api/migrations/runner.py`) with
  `schema_migrations` tracking and a CLI entry (`platform/api/run_migrations.py`).
- Updated all migration scripts to enforce SQLite WAL + busy_timeout
  for safer concurrent access.
- Added a `/api/system/db-info` endpoint to expose DB path/size/state.
- Added a lightweight DB audit utility (`scripts/db_audit.py`) to
  report and optionally archive extra `.db` files.
- Removed direct runtime `sqlite3` writes in ANARCII annotation paths;
  DB updates now flow through async SQLAlchemy sessions.

UI / Visibility
---------------
- Control panel and tray UI now display DB health
  (jobs, designs, size, journal mode, busy timeout).
- AI guidance docs updated to include DB migration and audit instructions.

Additional Changes (Same Update Window)
---------------------------------------
- ANARCII annotations now run compute in background but apply DB updates
  through async SQLAlchemy (no runtime `sqlite3` writes).
- Added a shared async annotation task helper:
  `platform/api/services/cdr_annotation_tasks.py`.
- SAbDab / NanoSAbDab search: in-memory VHH summary caching (1h TTL)
  to avoid re-downloading on every search; filtering and sorting applied
  against cached entries.
- SAbDab summary parsing updated to use `heavy_species` and disable
  `cdr_h3_length` from summary rows (field not present in source data).
- Frontend SAbDab search now exposes `sort_by` and `sort_desc` params.
- Nextflow launcher: robust GPU ID parsing, only pins CUDA devices when
  valid, and MSA batch uses `BMS_COLABFOLD_DB`/`BMS_MSA_CACHE` env overrides.
- GPU orchestrator: normalize job params and pinned GPU lists, skip
  scheduling when GPU stats are unavailable, and sanitize pinned GPU values.
- GPU orchestrator now validates allowlists/pins/locks against active GPUs
  and skips jobs targeting inactive devices.
- Nextflow config: `params.gpu_id` defaults to `NXF_DEFAULT_GPU` or 0 and
  light GPU selection defaults to GPU 0 when no override is set.

Workflow and UI Changes (Consolidated)
--------------------------------------
- FrustraMPNN integration: new Nextflow module and container definition,
  optional workflow stage, and build scripts updated to include it.
- Mutagenesis upgrades: expanded library generation controls (N count,
  indels, allow/block lists, excluded positions) and UI toggles for
  pre/post FrustraMPNN usage where applicable.
- MSA generation: per-variant MSA refresh for mutagenesis batches and
  environment-driven ColabFold DB/cache paths in scripts and workflows.
- Workflow API calls now use `params.api_url` instead of hardcoded
  `http://localhost:8000` for portable deployments.
- Standardized orchestration mode via `params.parallel_mode` for BoltzGen
  and BindCraft SWA paths, with legacy flags as fallback.
- ANARCII: auto-annotation after antibody jobs and updated UI copy to
  reflect automatic post-run annotation behavior.
- Re-ingestion: recursive output scanning, include-children support,
  and improved error handling for large/nested outputs.
- Design sorting: added `ptm`, `pae`, and `conf_score` sorting options
  end-to-end (API and frontend).
