# PDJ → BMS Final Migration Proposal

This document proposes the final cleanup to fully de‑reference “ProteinDJ/PDJ” and standardize on **BioModStack (BMS)** across code, config, paths, and docs. It consolidates the Phase 1 edits already completed and details the remaining changes required for a clean, future‑proof migration.

## Goals

- Remove “ProteinDJ/PDJ” branding, paths, and defaults.
- Standardize naming on **BioModStack / BMS**.
- Preserve backwards compatibility where possible.
- Provide a safe rollback path.

## Scope Summary

**Already completed (Phase 1, non‑breaking):**

- Branding and path examples updated in docs and comments.
- Removed hardcoded local `file://` doc links.
- Updated UI placeholder text and legacy comments.

**Remaining (Phase 2, potentially breaking):**

- Output directory defaults, schema descriptions, API fields, UI exclusions.
- Package identity and service metadata.
- Hardcoded absolute paths and environment references.
- Container/model paths referencing `proteinDJ`.
- Legacy archives and old database names.

## Final Target Names

| Concern | Current | Target |
| --- | --- | --- |
| Product name | ProteinDJ / PDJ | BioModStack (BMS) |
| Output dir (default) | `pdj_results` | `bms_results` (recommended) |
| API package name | `proteindj-api` | `biomodstack-api` |
| Repo path references | `/home/.../ProteinDJ_fork/...` | repo‑relative or configurable |
| DB files | `proteindj.db`, `pdj.db` | `biomodstack.db` (single source of truth) |

## Proposed Migration Plan

### Step 0: Preflight and Inventory

- Snapshot current runs and outputs.
- Record current config values and environment paths.
- Decide on the final default output directory name (`bms_results` recommended).
- Decide on migration method: symlink compatibility vs dual‑path support.

### Step 1: Rename Defaults and Schema Metadata

**Affected files:**

- `nextflow.config`
- `nextflow_schema.json`
- `schemas/nextflow_schema_*.json`
- `schemas/mode_parameters.csv`

**Actions:**

- Replace “ProteinDJ” references with “BioModStack”.
- Update default `out_dir` to `bms_results`.
- Update example output directory names in schema defaults.
- Keep a migration note in schema descriptions.

### Step 2: Update Backend Output Paths + Compatibility

**Affected files:**

- `platform/api/config/inputs.yaml`
- `platform/api/routers/jobs.py`
- `platform/api/routers/msa.py`
- `platform/api/routers/system.py`
- `platform/api/routers/files.py`
- `platform/api/services/result_ingester.py`

**Actions:**

- Switch default output directory to `bms_results`.
- Add compatibility lookup: if `bms_results` missing, fall back to `pdj_results`.
- Rename API response fields:
  - `pdj_results_size` → `results_size`
  - `pdj_results_files` → `results_files`
- Preserve old field names for one deprecation cycle (optional).

### Step 3: Update UI and Frontend Paths

**Affected files:**

- `platform/frontend/vite.config.ts`
- Any UI templates or placeholders referencing `pdj_results`.

**Actions:**

- Update ignored path from `**/pdj_results/**` to `**/bms_results/**`.
- Update placeholders and examples.

### Step 4: Package and Service Identity

**Affected files:**

- `platform/api/pyproject.toml`
- Service name strings in scripts and logs.

**Actions:**

- Rename package to `biomodstack-api`.
- Update description strings to BioModStack.
- If published internally, update deployment references (e.g., Docker tags, systemd names).

### Step 5: Remove Hardcoded Absolute Paths

**Affected files:**

- `start_ui.sh`, `start_ui_gui.sh`, `restart_api.sh`, `stop_services.sh`
- `platform/api/config/inputs.yaml`
- Any other absolute path references

**Actions:**

- Replace absolute paths with repo‑relative resolution.
- Allow override via env var (e.g., `BMS_PROJECT_DIR`).

### Step 6: Container/Model Paths

**Affected files:**

- `nextflow.config` (container/model paths containing `proteinDJ`)

**Actions:**

- Rename directory paths to BMS equivalents.
- Provide a migration stanza (or a “legacy path” fallback) to keep old locations working.

### Step 7: Legacy Artifacts and Archive

**Affected files:**

- `archive/proteindj_legacy/*`
- `proteindj.db`, `pdj.db`

**Actions:**

- Mark legacy files as deprecated (or move under `archive/` with a note).
- Consolidate to `biomodstack.db` if possible.
- Document any manual migration required.

## Compatibility and Rollback

**Compatibility:**

- Keep `pdj_results` readable for at least one release cycle.
- Provide a config option to force legacy behavior (e.g., `BMS_LEGACY_RESULTS_DIR=1`).
- Allow dual‑dir ingestion when both exist.

**Rollback:**

- Do not delete or rename existing `pdj_results` directories.
- Keep a reversible commit boundary per step.

## Validation Checklist

- [ ] `nextflow run` creates `bms_results` by default.
- [ ] API can list existing jobs in both old and new directories.
- [ ] UI loads and filters results correctly.
- [ ] Schema metadata and docs no longer mention ProteinDJ.
- [ ] No hardcoded `/home/.../ProteinDJ_fork/...` references remain.
- [ ] Legacy archives clearly labeled and isolated.

## Suggested Order of Execution

1. Defaults + schema metadata
2. Backend path migration + compatibility layer
3. Frontend exclusions and placeholders
4. Package identity changes
5. Absolute paths removal
6. Container/model path cleanup
7. Archive + DB cleanup

---

If desired, this plan can be broken into a series of small, revertible PRs with explicit migration notes and test checkpoints.
