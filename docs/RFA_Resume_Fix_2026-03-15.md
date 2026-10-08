# RFA Resume Fix - 2026-03-15

## Problem

The antibody orchestrator resume path was not a true resume.

- Parent resume created a new job id and a new output directory.
- Child batch discovery was keyed off the new parent id, so resumed parents did not rediscover last run's RFantibody/FAMPNN/maturation/validation children.
- Child spawners only handled "all completed" or "still running" cases. Partial failure and failed-child resume were effectively unsupported.
- Creating a fresh `out_dir` on resume changed `params.out_dir` and `publishDir` paths, which reduced Nextflow cache reuse even when `-resume` and the original `.nextflow` cache were present.

## Fix Set

### 1. Stable resume lineage

- Preserve a stable logical child batch key in job params via `batch_name`.
- Preserve `resume_root_job_id` across resumes.
- Ensure orchestrated jobs keep `job_name` in params so child names and batch keys remain stable.

### 2. True execution-directory reuse

- Resume now keeps the original `output_dir` instead of minting a new directory.
- Child retries that carry `resume_source_dir` also reuse that same directory.
- This keeps `params.out_dir` stable and improves Nextflow cache hits.

### 3. Child-status reconciliation across attempts

- Child status lookup now includes both:
  - children attached to the current parent id
  - children attached to the preserved logical `batch_name`
- Multiple attempts for the same logical child slot are deduped to the latest attempt.

### 4. Slot-level child resume

- Child spawners now decide per logical child slot:
  - `completed`: reuse
  - `queued/pending/running/awaiting_input`: leave in place
  - `failed/cancelled`: create a new attempt with `resume_job_id`, `resume_source_dir`, and `resume_work_dir=work`
- This replaces the previous all-or-nothing behavior.

### 5. Same-parent retry safety

- Child creation no longer blindly reuses an existing same-named child if the latest attempt is already `failed` or `cancelled`.
- A new attempt is created instead.

## Files

- `platform/api/routers/jobs.py`
- `workflows/antibody_denovo.nf`
- `workflows/boltzgen_design.nf`
- `modules/boltzgen.nf`
- `scripts/child_job_utils.py`
- `scripts/spawn_rfantibody_children.py`
- `scripts/spawn_fampnn_children.py`
- `scripts/spawn_maturation_children.py`
- `scripts/spawn_antibody_children.py`
- `scripts/spawn_boltzgen_children.py`
- `platform/api/tests/test_resume_identity.py`

## Expected Behavior After Fix

- Resuming an antibody orchestrator run should reuse completed child slots and only relaunch failed child slots.
- Relaunched child slots should use Nextflow `-resume` against their original child output directories.
- Parent Nextflow should keep the original run directory, improving cache reuse instead of behaving like a fresh launch.
