# FAMPNN Constraint Mode Update — Summary

Date: 2026-01-21

## Overview
This update adds a **FAMPNN constraint mode selector** so workflows can choose between:
- **generic** (no fixed residues)
- **antibody** (CDR-aware constraints)

It also adds a generic constraints CSV generator and wires the new parameter through the antibody workflow, UI, schema, and configs.

## Why this was done
- Existing FAMPNN prep always used antibody CDR constraints, which is incorrect for non-antibody workflows.
- We need a clean, explicit switch for antibody vs non-antibody usage.

## Changes made (code + config + docs)

### Core workflow / module changes
- `modules/fampnn.nf`
  - Added `fampnn_constraint_mode` support.
  - Prep stage now chooses:
    - `prep_antibody_constraints.py` if mode is antibody/CDR
    - `prep_fampnn_constraints_generic.py` if mode is generic

- `workflows/antibody_denovo.nf`
  - Default `fampnn_constraint_mode = 'antibody'` for this pipeline.
  - Passed `fampnn_constraint_mode` to FAMPNN child spawns.

- `scripts/prep_fampnn_constraints_generic.py`
  - New script. Generates empty constraints CSV for generic FAMPNN.

- `scripts/spawn_fampnn_children.py`
  - Resume logic updated to handle API payloads shaped like `{ jobs, total }`.

### UI updates
- `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`
  - Added UI toggle for FAMPNN constraints when FAMPNN is selected.
  - Default set to **antibody** in the antibody workflow.
  - Includes `fampnn_constraint_mode` in job submission params.

### Config + schema updates
- `nextflow.config`
  - Added `fampnn_constraint_mode = 'generic'` (global default for non‑antibody flows).

- `nextflow_schema.json`
  - Added `fampnn_constraint_mode` enum (`generic`, `antibody`).

- `platform/api/config/models/fampnn.yaml`
  - Added `fampnn_constraint_mode` param with enum.

- `platform/api/config/models/fampnn_child.yaml`
  - Added `fampnn_constraint_mode` param with enum.
  - **Note:** this file is currently git‑ignored (see Git issues below).

- `platform/api/config/models/antibody_denovo.yaml`
  - Added `fampnn_constraint_mode` param (default `antibody`).

### Documentation
- `docs/parameters.md`
  - Documented `fampnn_constraint_mode`.

- `workflows/WORKFLOWS_REVIEW.md`
  - Logged the new FAMPNN constraint mode and wiring changes.

## Current Git / repo issues
There are **two blockers** for git status/commit/push from this environment:

1) **Skip‑worktree flags on key files**
   These files show an `H` flag (skip‑worktree) and are hidden from `git status`:
   - `modules/fampnn.nf`
   - `workflows/antibody_denovo.nf`
   - `scripts/spawn_fampnn_children.py`
   - `scripts/prep_fampnn_constraints_generic.py`

   Attempted to clear with:
   - `git update-index --no-skip-worktree ...`

   But the flags remained, likely due to repo configuration or restricted `.git` access.

2) **.git index permission block**
   Normal git commands cannot create `.git/index.lock` from this environment, indicating file permission constraints.
   Escalated calls can access `.git`, but skip‑worktree flags still persist.

3) **Ignored file**
   - `platform/api/config/models/fampnn_child.yaml` is ignored because `.gitignore` contains `models/`.
   - This means it will **never appear in git status** unless you change `.gitignore`.

## What you need to run locally (recommended)
If you want these changes tracked and pushed:

1) Clear skip‑worktree flags (run locally with full git permissions):
```
git update-index --no-skip-worktree modules/fampnn.nf \
  workflows/antibody_denovo.nf \
  scripts/spawn_fampnn_children.py \
  scripts/prep_fampnn_constraints_generic.py
```

2) Decide if `platform/api/config/models/fampnn_child.yaml` should be tracked:
- If yes, remove `models/` from `.gitignore` or add an exception.

3) Then:
```
git status
git add <files>
git commit -m "Add FAMPNN constraint mode selector"
git push
```

## Tests
No automated tests were run.

## Notes on “selectors everywhere FAMPNN is available”
- The antibody workflow now has the selector.
- Manual model mode already uses `fampnn.yaml` params, so the enum is now available there automatically.
- If you want explicit UI selectors in other templates, point me to those workflows.
