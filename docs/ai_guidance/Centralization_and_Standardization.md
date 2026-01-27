Centralization and Standardization
==================================

Purpose
-------
Keep configuration, pathing, and runtime behavior centralized so the
platform is portable and consistent across machines.

Single Sources of Truth
-----------------------
- Pathing: use `platform/api/paths.py` helpers (`get_*`) for any host path.
- Nextflow params: treat `nextflow.config` as the canonical default for
  workflow settings. Avoid redefining defaults inside modules/workflows.
- API base URL: use `params.api_url` (Nextflow) or `API_BASE_URL` (scripts).
- GPU container flags: prefer the shared `gpuPrefix(...)` helper in
  `nextflow.config`.

Pathing Rules
-------------
- Never hardcode user paths (e.g., `/home/...`).
- Prefer environment variables:
  - `BMS_HOME`, `BMS_DATA`, `BMS_WEIGHTS`
  - `BMS_COLABFOLD_DB`, `BMS_MSA_CACHE`, `BMS_SABDAB_CACHE`
  - `BMS_DB_PATH`, `DATABASE_URL`
- For cache mounts in containers, use `XDG_CACHE_HOME` or `${HOME}/.cache`.
- If a fallback is required, document it as a **last resort**.

Workflow Standardization
------------------------
- Use `params.api_url` for spawn/wait/ingest scripts in workflows.
- Centralize orchestration decisions via `params.parallel_mode`.
- Avoid per-module defaults that override `nextflow.config`.

Scripts and CLIs
---------------
- Use `API_BASE_URL` as the default API host.
- Reuse helper functions from `platform/api/services/` when possible
  instead of duplicating logic in scripts.
- If a script needs path resolution, import `paths.py`.

Frontend UI Defaults
--------------------
- Avoid hardcoded host paths in placeholders or defaults.
- Use env-style placeholders like `BMS_WEIGHTS/ppiflow` or
  `BMS_DATA/results/...` to indicate configurable locations.
- The UI base path is `/bms/` (see `platform/frontend/vite.config.ts`).
  If changed, update any reverse proxy or launcher links.

Documentation
-------------
- When a path is mentioned, use env variable form (e.g., `BMS_WEIGHTS/ppiflow`).
- Keep DB guidance in `docs/ai_guidance/Database_Instructions.md`.
