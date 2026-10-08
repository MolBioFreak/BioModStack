# RFantibody Workflow Fix Plan (Step-by-Step)

Version: 1.0  
Date: 2026-02-10  
Scope: RFantibody workflow only (job submission, scheduler/orchestrator, RFantibody runtime, container/build, and RFantibody-stage reporting).

## Goal

Restore RFantibody workflow reliability with deterministic runtime behavior, no silent environment drift, and correct parent/child orchestration semantics.

## Implementation Status (2026-02-10)

Completed in code/docs:
- Step 1: Host repo overlay disabled by default; debug-only toggle retained.
- Step 2: RFantibody commit pinned to `8d9d402a99fd63052ad538b3d670e467b800123c` in `apptainer/rfantibody.def`.
- Step 3: DGL install path made deterministic by source build (`v2.5.0`) against pinned torch/cuda.
- Step 4: Torch build pinned (`2.11.0.dev20260204+cu128`) with pinned torchvision/torchaudio.
- Step 5: Added `scripts/check_rfantibody_runtime.py` and preflight execution in `modules/rfantibody.nf`.
- Step 6: `PYTORCH_JIT=0` retained and documented as compatibility setting only.
- Step 7: Child resume/spawn logic now uses status counts and deduplicated child records.
- Step 8: Aggregation route now accepts `batch_name` and matches status lookup scope.
- Step 9: RFantibody stage reporting added for orchestrator spawn-wait-collect path.
- Step 10: Wait logic now handles cancelled children and fails when no completed outputs exist.
- Step 11: RFantibody diffusion-step UX aligned to backend cap (`max=50`) in presets/UI.
- Step 12: Added `rfantibody_design_loops` default in `nextflow.config`.
- Step 13: Detected IMGT loop ranges are now forwarded as full RFantibody loop specs.
- Step 14: Runtime manifest/preflight info emitted to RFantibody task log at task start.
- Step 15: Environment validator now supports workflow-specific required RFantibody container/checkpoint checks.
- Step 16: Workstation/model-integration docs updated for RFantibody build/verification path.

Still pending:
- Step 17: Regression test matrix execution and evidence capture.
- Step 18: Final release gate sign-off after Step 17 evidence.

## Confirmed Failure Pattern

1. Recent RFantibody failures terminate in `get_next_frames` with:
   `ValueError: Non-positive determinant ... rotation matrix ... [[0. 0. 0.], ...]`.
2. Failures reproduce at both `T=200` and `T=50`; this is not solved by timestep tuning alone.
3. Runtime currently uses a mixed stack:
   `torch` CUDA 12.8 + `dgl` CUDA 12.1 wheel lineage.
4. RFantibody process bind-mounts host repo into `/opt/RFantibody`, creating code/runtime skew risk against the container-baked code.

## Execution Order (Do Not Skip)

### Step 1: Stop Host-Repo Overlay in Production Runs

Files:
- `modules/rfantibody.nf`
- `nextflow.config`

Actions:
1. Replace full repo bind `--bind ${weightsRoot}/rfantibody/rfantibody_repo:/opt/RFantibody` with a weights-only bind.
2. Keep container code immutable at runtime.
3. Add an explicit debug-only flag (default `false`) to re-enable full repo bind for investigation only.

Done when:
1. RFantibody tasks run without mounting host source tree over `/opt/RFantibody`.
2. Logs clearly state whether debug repo overlay is enabled or disabled.

### Step 2: Pin RFantibody Source Revision in Container Build

Files:
- `apptainer/rfantibody.def`

Actions:
1. After clone, checkout a fixed RFantibody commit/tag.
2. Record commit hash in container labels or build metadata.
3. Keep the same pinned commit documented in this file.

Done when:
1. `git -C /opt/RFantibody rev-parse HEAD` is deterministic across builds.
2. Build metadata contains the pinned RFantibody revision.

### Step 3: Remove Ambiguous DGL Install Path

Files:
- `apptainer/rfantibody.def`

Actions:
1. Remove fallback chain that can install varying DGL builds (`cu121` wheel fallback then generic `dgl`).
2. Choose one deterministic install strategy:
   - Preferred: build DGL from source against the pinned PyTorch/CUDA stack.
   - Alternative: pin one exact DGL binary build and fail build if unavailable.
3. Ensure build fails hard on DGL install failure (no `|| true` for core deps).

Done when:
1. Container always reports the same DGL version/build.
2. Build fails if required DGL artifact is not available.

### Step 4: Pin PyTorch (No Floating Nightly)

Files:
- `apptainer/rfantibody.def`

Actions:
1. Pin an explicit PyTorch version/build (or nightly date pin if absolutely required).
2. Remove unconstrained package installs that can silently change ABI compatibility.
3. Keep `requirements.lock` as an auditable artifact in the image.

Done when:
1. `torch.__version__` is fixed for all rebuilt images.
2. Rebuilds no longer drift by date.

### Step 5: Add RFantibody Runtime Preflight Guard

Files:
- `scripts/check_rfantibody_runtime.py` (new)
- `modules/rfantibody.nf`

Actions:
1. Add a preflight script executed before inference that verifies:
   - CUDA availability and device capability.
   - `torch` version and CUDA version string.
   - `dgl` import and version.
   - minimal DGL CUDA op sanity (`graph.edges()`, `dgl.ops.copy_e_sum`, `dgl.ops.copy_e_mean`).
2. Fail fast with explicit error text if any check fails.

Done when:
1. RFantibody jobs abort early with clear diagnostics on bad runtime stacks.
2. No inference starts if preflight fails.

### Step 6: Keep `PYTORCH_JIT=0` Explicit, But Treat as Compatibility Setting Only

Files:
- `apptainer/rfantibody.def`

Actions:
1. Keep `PYTORCH_JIT=0` explicitly set in environment.
2. Document it as a compatibility requirement, not a root-cause fix.

Done when:
1. Environment consistently exports `PYTORCH_JIT=0`.
2. Docs do not claim it resolves numerical corruption by itself.

### Step 7: Fix RFantibody Child Resume/Spawn Duplication Logic

Files:
- `scripts/spawn_rfantibody_children.py`
- `platform/api/routers/jobs.py`

Actions:
1. Stop inferring "existing children" only from non-aggregated output directories.
2. Base resume decisions on full child status counts (`total/completed/running/pending/failed`) regardless of aggregation state.
3. Return deduplicated child records by child job ID.

Done when:
1. Resume does not re-spawn duplicate RFantibody children after aggregation.
2. Existing completed children are detected even with empty `child_output_dirs`.

### Step 8: Align Aggregation Query Semantics

Files:
- `scripts/wait_for_children.py`
- `platform/api/routers/jobs.py`

Actions:
1. Ensure `mark-aggregated` uses the same scope as status lookup (parent and resume key semantics).
2. If `batch_name` is used in status lookup, support equivalent filtering for aggregation updates.

Done when:
1. Resume runs cannot repeatedly collect the same completed children.
2. Aggregation flags match the status query scope.

### Step 9: Add RFantibody Stage Reporting in Orchestrator Path

Files:
- `workflows/antibody_denovo.nf`
- `scripts/stage_reporter.py` (existing utility)

Actions:
1. After `CollectChildOutputs` succeeds, call stage reporter for parent job with RFantibody outputs.
2. Keep behavior consistent with standard (non-orchestrator) path.

Done when:
1. Parent job `completed_stages` and `stage_outputs["rfantibody"]` are correctly populated in both paths.

### Step 10: Treat "All Cancelled" Child State as Failure for RFantibody Stage

Files:
- `scripts/wait_for_children.py`
- `platform/api/routers/jobs.py`

Actions:
1. Add explicit cancelled count handling.
2. Return non-zero from wait script when no completed children exist and jobs are failed/cancelled.

Done when:
1. Parent workflow cannot report success with zero usable RFantibody outputs.

### Step 11: Resolve RFantibody Diffusion-Step UX Mismatch

Files:
- `modules/rfantibody.nf`
- `platform/frontend/src/components/QualitySettingsPanel.tsx`
- `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`

Actions:
1. Choose one policy and enforce it consistently:
   - Policy A (recommended): keep backend cap at 50 and update UI presets to max 50.
   - Policy B: allow >50 only with explicit expert mode and warning text.
2. Ensure submitted values and effective runtime values match user-visible expectations.

Done when:
1. UI presets do not advertise unsupported RFantibody settings.
2. Logs and UI reflect the same effective diffusion steps.

### Step 12: Add Missing RFantibody Param Defaults

Files:
- `nextflow.config`

Actions:
1. Define default for `rfantibody_design_loops` to remove undefined parameter warning.
2. Keep defaults centralized in config (not ad hoc in module code).

Done when:
1. No undefined-param warning for `rfantibody_design_loops`.

### Step 13: Preserve IMGT Loop Range Intent (If User Selected Detected CDRs)

Files:
- `platform/frontend/src/components/AntibodyDenovoTemplate.tsx`
- `modules/rfantibody.nf`

Actions:
1. If detected IMGT ranges exist, pass full RFantibody loop spec (e.g., `[H1:7-10,...]`) rather than names only.
2. Avoid silently replacing detected ranges with hardcoded defaults.

Done when:
1. User-selected detected loop ranges are actually used by RFantibody.

### Step 14: Add RFantibody Runtime Manifest Output

Files:
- `modules/rfantibody.nf`

Actions:
1. At task start, print a short manifest to `rfantibody_<id>.log`:
   - RFantibody commit
   - torch version
   - torch CUDA version
   - dgl version
   - GPU model and capability
2. Include whether host overlay mode is enabled.

Done when:
1. Every RFantibody task log contains full runtime provenance.

### Step 15: Update Environment Validator to Reflect RFantibody as Required for Antibody Modes

Files:
- `scripts/validate_environment.py`

Actions:
1. Promote `rfantibody.sif` from optional to required for RFantibody/antibody workflows.
2. Add validation for RFantibody checkpoint presence at expected path.

Done when:
1. Validator fails early when RFantibody container or checkpoint is missing.

### Step 16: Fix Container Build Documentation Drift

Files:
- `docs/Workstation Set Up and Install Guide.md`
- `docs/ai_guidance/Model_Integrations.md`

Actions:
1. Correct container lists to include RFantibody artifacts where relevant.
2. Remove or update references to missing scripts/commands.
3. Add explicit RFantibody build path and verification commands.

Done when:
1. Documentation matches the actual repo layout and current workflow requirements.

### Step 17: Add RFantibody Regression Test Matrix (Required Before Release)

Files:
- `docs/` (test protocol section in this file or sibling test doc)
- CI/test scripts as appropriate

Actions:
1. Run and store results for:
   - Case A: known previously successful target (`6pax`) with `T=50`.
   - Case B: previously failing `2vsm_chainA` with representative hotspot set.
   - Case C: previously failing `3ln9_imgt`.
2. Run both:
   - Standard mode.
   - `parallel_mode=full_orchestrator`.
3. Record pass/fail and runtime manifest for each case.

Done when:
1. `2vsm_chainA` no longer fails with zero rotation matrices.
2. Orchestrator parent stage state is correct and no duplicate child respawn occurs.

### Step 18: Release Gate (Block Merge Until All True)

Actions:
1. Runtime stack is pinned and reproducible.
2. Host overlay is disabled by default.
3. Preflight catches incompatible stacks before inference.
4. Resume/aggregation logic is duplication-safe.
5. Parent RFantibody stage reporting is correct in both orchestration modes.
6. RFantibody regression matrix passes.

Done when:
1. All steps above are checked complete.
2. This document is updated with final versions/hashes and test evidence.

## Required Deliverables Checklist

1. Code patches for Steps 1-15.
2. Documentation patches for Step 16.
3. Regression test evidence for Step 17.
4. Final sign-off against Step 18 release gate.
