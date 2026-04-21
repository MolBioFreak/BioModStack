# BioModStack Fold-CP Large-Protein Sharding and DRAM Tile Runtime Plan

> **For Hermes:** Use this as the implementation spec for the experimental Fold-CP workflow. Keep the native `structure_prediction` launcher and all non-CP workflows unchanged. The experimental path should be explicitly centered on very large proteins, user-selectable but algorithmically valid shard plans, DRAM-first shared tile storage, and hard fail-fast semantics.

**Goal:** Replace the current GPU-count-coupled Fold-CP launch path with an experimental large-protein workflow that plans a logical shard grid independent of physical GPU count, executes shard bundles as mostly normal single-GPU child jobs, persists shared state in a DRAM-first tile store, and fails cleanly on any worker error.

**Architecture:** The BioModStack experimental workflow becomes a planner/coordinator surface rather than a thin wrapper around `torchrun --nproc_per_node`. The UI asks the backend for admissible shard-plan options, the API materializes a shard manifest, Nextflow runs a planner/spawn/wait/finalize workflow, and the checked-out Fold-CP fork executes bundle jobs against a shared tile store. Physical GPUs are workers. Logical shard geometry is determined by the plan, not by the number of selected GPUs.

**Tech Stack:** `/home/dalab/biomodstack/biomodstack/platform/api/config/models/boltz_cp_experimental.yaml`, `/home/dalab/biomodstack/biomodstack/platform/api/config/templates/boltz_cp_experimental.yaml`, `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/StructurePredictionTemplate.tsx`, `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/structurePredictionUiState.ts`, `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/JobSubmission.tsx`, `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/dashboard/reorchestrateStructureSettings.ts`, `/home/dalab/biomodstack/biomodstack/platform/frontend/tests/structurePredictionUiState.test.ts`, `/home/dalab/biomodstack/biomodstack/platform/api/routers/jobs.py`, `/home/dalab/biomodstack/biomodstack/platform/api/services/nextflow.py`, `/home/dalab/biomodstack/biomodstack/platform/api/services/gpu_orchestrator.py`, `/home/dalab/biomodstack/biomodstack/platform/api/database.py`, `/home/dalab/biomodstack/biomodstack/workflows/boltz_cp_experimental.nf`, `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`, and the checked-out Fold-CP fork at `/home/dalab/tmp/boltz-cp/src/boltz/distributed/...` plus its test suite under `/home/dalab/tmp/boltz-cp/tests/`.

---

## 1. Repo-grounded current state

### 1.1 The experimental workflow is already isolated enough to evolve safely

There is already a distinct experimental surface for Fold-CP:

- `platform/api/config/templates/boltz_cp_experimental.yaml`
- `platform/api/config/models/boltz_cp_experimental.yaml`
- `platform/frontend/src/components/structurePredictionUiState.ts`
- `platform/frontend/src/components/JobSubmission.tsx`
- `platform/frontend/src/components/StructurePredictionTemplate.tsx`
- `workflows/boltz_cp_experimental.nf`
- `modules/boltz_cp_experimental.nf`

The current frontend helper already treats this path as a separate launch variant:

- `structurePredictionUiState.ts:142-154` returns a dedicated launch config for `boltz_cp_experimental`
- `resolveStructureSubmitTarget(...)` at `structurePredictionUiState.ts:183-204` routes the experimental flow to its own model/mode
- `JobSubmission.tsx` already keeps `boltz_cp_experimental` out of the normal model dropdown and surfaces it as its own experimental card

This is the correct isolation boundary to preserve.

### 1.2 The current CP path is still hard-coupled to selected GPU count

Today the Fold-CP experimental path is not “logical shards scheduled onto workers.” It is still “pick GPUs, then derive the CP mesh from that count.” Evidence:

- Frontend helper:
  - `structurePredictionUiState.ts:169-180` computes `gpuIds` and `sizeCp` from selected/pinned GPUs
  - `structurePredictionUiState.ts:120-140` implements `getLargestSquareDivisor(...)`
- Backend normalization:
  - `platform/api/routers/jobs.py:1188-1204` derives `gpu_ids` and clamps `size_cp` from the number of selected GPUs
  - `platform/api/services/nextflow.py:450-476` uses `_largest_square_divisor(...)` and `_derive_boltz_cp_gpu_launch_settings(...)`
- Workflow/module layer:
  - `workflows/boltz_cp_experimental.nf:12-16` still validates `bcp_size_cp` as a perfect square
  - `modules/boltz_cp_experimental.nf:25-28` computes `nproc` directly from selected GPU IDs and `sizeDp = nproc / sizeCp`
  - `modules/boltz_cp_experimental.nf:119-121` requires `bcp_size_cp` to divide the number of selected GPUs
- Fold-CP runtime:
  - `/home/dalab/tmp/boltz-cp/src/boltz/distributed/predict.py:257-262` requires `world_size == size_dp * size_cp` and requires `size_cp` to be a perfect square
  - `/home/dalab/tmp/boltz-cp/src/boltz/distributed/manager.py:729-756` consumes `LOCAL_RANK` from the environment directly

This is the exact coupling that must be broken for the large-protein tiled design.

### 1.3 The silent-skip defect is real and must be removed first

Current Fold-CP error behavior is unsafe for production experimentation:

- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/models/boltz2.py:1281-1286` catches OOM and returns `{"exception": True}`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/comm.py:205-231` requires pending communications to finish cleanly before proceeding
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/comm.py:586-604` explicitly warns about NCCL hangs/deadlocks from asymmetric communication patterns and square-layout assumptions

So the present runtime can locally “skip” while peer ranks still expect symmetric participation. That is incompatible with a reliable experimental workflow and explains the no-artifact/alive-but-stalled failure mode.

### 1.4 BioModStack scheduler hooks are already good enough to reuse for child shard jobs

The current scheduler already has several useful primitives:

- `gpu_orchestrator.py:1375-1483` supports:
  - `pinned_gpus` allowlists
  - batch lock precedence
  - GPU disabled/busy/cooldown checks
  - per-GPU max concurrent jobs
  - per-GPU safety margin and target fill
  - per-job VRAM reservation
- `gpu_orchestrator.py:1743-1859` exposes similar diagnostics and candidate selection logic for queue inspection
- `gpu_orchestrator.py:1253-1281` already has single-GPU batch lock helpers
- `gpu_orchestrator.py:118-119` shows `HEAVY_MODELS` and `PROTENIX_MODELS`; notably, `boltz_cp_experimental` is not currently hard-blocked as a heavy-only model, which means shard jobs can rely on reservation sizing and allowlists instead of being globally banned from weaker GPUs
- `platform/api/database.py:51-52, 91-102` already has `batch_id`, `parent_job_id`, `child_stage`, `child_output_dir`, and `child_design_count`
- `main.nf:22` already shows an existing spawn/wait/aggregate pattern for child workflows via the BoltzGen modules

This is enough to build “throw all the shard bundles as jobs and let the existing scheduler place them,” provided the shard jobs stay single-GPU.

### 1.5 Host storage facts support DRAM-first now and SSD spill later

Live host facts captured during this planning session:

- `free -h` showed:
  - `125 GiB` total RAM
  - about `93 GiB` available at capture time
- `df -h /dev/shm` showed:
  - `63 GiB` tmpfs available
- `df -h /mnt/BioModStack` showed:
  - `1.4 TiB` free on NVMe-backed storage

Implication:

- DRAM-backed tile storage is immediately practical
- `/dev/shm` is a good default only for moderate budgets; it is too small to be the only DRAM strategy for the largest jobs
- a configurable tmpfs-backed store or dedicated RAM-backed mount is the right phase-1 target
- hybrid SSD spill is plausible and should reuse the same tile-store abstraction rather than being treated as fantasy

---

## 2. Product decision

1. `boltz_cp_experimental` becomes explicitly a large-protein experimental workflow.
2. Logical sharding correctness is defined by a shard plan, not by the number of selected GPUs.
3. Physical GPUs are workers. They affect throughput, scheduling, and bundle sizing, not mathematical validity.
4. The user should select from backend-generated admissible plans rather than typing a freeform `size_cp` integer.
5. Shared state is the thing being sharded. Shard jobs operate on one global folded state via tiles; they are not independent mini-folds with post-hoc biological stitching.
6. Any worker OOM or runtime failure aborts the whole run. No silent local skip.
7. DRAM-backed caching is phase 1. SSD spill is phase 2 behind the same tile-store interface.
8. All changes remain isolated to the experimental BioModStack path and the checked-out Fold-CP fork. Native `boltz2`, `protenix`, `rf3`, and standard `structure_prediction` behavior must not drift.
9. Keep a hidden or developer-only `native_cp_legacy` backend for parity testing during the migration, but do not make it the primary user-facing workflow mode.

---

## 3. Non-regression rules

These are hard constraints.

1. Native `structure_prediction` semantics must remain unchanged.
2. `boltz_cp_experimental` must remain a separate workflow identity from native `boltz2`.
3. Experimental UI behavior must be gated on `structure_launch_variant == 'boltz_cp_experimental'`.
4. Existing `gpu_locks` semantics for other workflows must not be broken by CP-specific work.
5. No broad rewrite of BioModStack scheduling from “single-GPU jobs” to “arbitrary multi-GPU jobs” is required; shard jobs should remain single-GPU so the current scheduler can mostly keep doing what it already does.
6. The Fold-CP fork must no longer swallow OOM and limp forward.
7. The new tiled engine must not require physical GPU count to match logical shard count.
8. Re-orchestration/retry for the experimental workflow must remain truthful to the same shard-plan semantics the fresh launcher uses.

---

## 4. User-facing workflow contract

## 4.1 What the experimental launcher should show

The experimental workflow should stop looking like “native CP with a couple extra fields” and instead look like “large-protein sharded inference.”

Primary controls:

- Worker GPUs
  - multi-select allowlist of candidate GPUs
- Optional per-GPU caps
  - per-worker max usable memory budget
  - per-worker max bundles in flight (default 1)
- Storage tier
  - `DRAM`
  - `Hybrid SSD spill`
- Shard plan
  - selectable cards or dropdown entries generated by the backend
  - each option must already be mathematically valid
- Advanced developer toggle
  - hidden/advanced only: `native_cp_legacy` backend for comparison runs

Normal users should not see a raw `size_cp` textbox in the primary flow.

## 4.2 Backend-generated plan options, not arbitrary integers

Add a dedicated preview/plan endpoint, for example:

- `POST /api/experimental/fold-cp/plan-options`

Inputs:

- canonical staged input summary or parsed target summary
- worker GPU allowlist
- optional per-GPU caps
- requested storage tier
- optional quality/recycling settings if they materially affect state size

Output:

An ordered list of valid plans such as:

- `plan_id`
- `strategy_id`
- `logical_grid_side`
- `logical_shards`
- `tile_shape`
- `bundle_class`
- `estimated_dram_gb`
- `estimated_spill_gb`
- `min_worker_vram_mb`
- `recommended_worker_count`
- `notes`
- `warnings`

Example labels shown to the user:

- `3x3 logical grid • DRAM 18 GiB • min worker 10 GiB • good fit for 5090+3090+3090+5060 Ti`
- `4x4 logical grid • DRAM 34 GiB • min worker 12 GiB • better max-fit, slower on weak workers`
- `6x6 logical grid • DRAM 58 GiB • requires DRAM tier or hybrid spill`

The user is still selecting the plan, but only from algorithmically valid options.

## 4.3 GPU count becomes a throughput control, not a validity control

The same valid plan should be runnable on:

- 1 GPU: sequential worker execution
- 2 GPUs: parallel worker execution
- 3 GPUs: parallel worker execution
- 4 GPUs: parallel worker execution

The runtime gets faster or slower depending on workers, but the plan remains mathematically the same.

---

## 5. Shard-plan manifest contract

The backend should materialize a single plan manifest and pass only the manifest path into the workflow/runtime. This avoids param explosion and keeps the experimental logic self-contained.

Suggested manifest path:

- under the job output/control directory, for example `.../control/foldcp_plan.json`

Suggested manifest shape:

```json
{
  "version": 1,
  "workflow_id": "boltz_cp_experimental",
  "engine": "tiled_large_protein_v1",
  "input": {
    "staged_input_path": "...",
    "input_format": "config_files",
    "sequence_summary": {
      "token_count": 0,
      "chain_count": 0
    }
  },
  "plan": {
    "strategy_id": "square_tiled_2d",
    "logical_grid_side": 4,
    "logical_shards": 16,
    "tile_shape": {
      "tokens_i": 512,
      "tokens_j": 512,
      "token_multiple": 8
    },
    "bundle_policy": {
      "bundle_class": "medium",
      "max_tiles_per_bundle": 4
    }
  },
  "workers": [
    {
      "gpu_id": 0,
      "max_vram_mb": 28000,
      "max_bundles_in_flight": 1
    },
    {
      "gpu_id": 1,
      "max_vram_mb": 10000,
      "max_bundles_in_flight": 1
    }
  ],
  "storage": {
    "tier": "dram",
    "root": "/path/to/tile-store",
    "dram_budget_gb": 48,
    "spill_root": null
  },
  "scheduler": {
    "reservation_mode": "opportunistic",
    "pinned_gpus": [0, 1, 2, 3],
    "batch_id": "uuid"
  },
  "failure_policy": {
    "abort_on_worker_oom": true,
    "abort_on_bundle_error": true,
    "split_oversized_bundle_once": true
  }
}
```

The plan manifest is the canonical runtime contract. UI params, API normalization, and workflow arguments all collapse into this file.

---

## 6. BioModStack execution model

## 6.1 Top-level workflow stages

Refactor `workflows/boltz_cp_experimental.nf` and `modules/boltz_cp_experimental.nf` around these stages:

1. `PlanFoldCpLargeProtein`
   - validate inputs
   - call the shard planner
   - emit `foldcp_plan.json`
2. `InitFoldCpTileStore`
   - create/store tile metadata and initial state containers
3. `SpawnFoldCpBundleJobs`
   - create child jobs for the first wave of bundles
4. `WaitForFoldCpWave`
   - block until current wave completes or fails
5. `AdvanceFoldCpWave`
   - compute next eligible bundle set and spawn the next wave
6. `FinalizeFoldCpLargeProtein`
   - assemble final outputs into standard BMS result folders

This should reuse the existing child-job orchestration pattern already present elsewhere in the repo instead of inventing a totally separate supervisor model.

## 6.2 What a shard child job should look like

Each shard child job should be a normal single-GPU BioModStack job with params such as:

- `bcp_plan_path`
- `bcp_bundle_id`
- `bcp_phase_id`
- `bcp_wave_id`
- `bcp_worker_class`
- `bcp_tile_store_root`

Queue-facing properties:

- `batch_id` set to the parent shard batch
- `parent_job_id` set to the coordinator/planner job
- `child_stage` set to something explicit like `foldcp_bundle`
- `scheduler_reservation_mb` set from the bundle class
- `pinned_gpus` allowlist set from selected worker GPUs

This keeps `assigned_gpu` semantics unchanged because each shard bundle still runs on exactly one GPU.

## 6.3 Scheduler behavior to reuse as-is

The current scheduler already gives the experimental shard jobs most of what they need:

- GPU allowlist filtering via `pinned_gpus`
- per-job VRAM fit checks via `scheduler_reservation_mb` / `vram_estimate_mb`
- per-GPU safety margins
- per-GPU target fill
- per-GPU max concurrent jobs
- queue priority scoring
- job diagnostics and queue blockers

That is why the design should keep bundle jobs single-GPU.

## 6.4 Scheduler additions that are actually needed

A few additions are still required, but they should be CP-specific and isolated.

### A. Dynamic batch GPU lease set

Current `gpu_locks` are a single-batch-to-single-GPU mapping. That is too coarse for shard workflows.

Add a narrow CP-specific lease mechanism, for example a small runtime-scoped helper/service such as:

- `platform/api/services/gpu_batch_leases.py`

Semantics:

- lease mode `opportunistic`
  - child jobs compete normally within their allowlist
- lease mode `reserve_set`
  - the batch reserves a set of worker GPUs for the duration of the run

Do not overload the existing static/config-like `gpu_locks` map for dynamic multi-GPU lease state.

### B. Wave/barrier awareness

The scheduler itself does not need to understand Fold-CP math, but the coordinator must understand:

- which bundle jobs belong to the active phase/wave
- when a wave is complete
- when no further bundles may be launched because the wave failed

Keep this orchestration state in the experimental workflow/coordinator layer, not as a new general scheduler abstraction.

### C. Tail-straggler handling

For heterogeneous hardware, the final few bundles in a wave can dominate makespan.

Add a CP-specific policy:

- if a wave tail is much slower than expected, allow the coordinator to split one large remaining bundle into smaller bundles and requeue them
- this should be explicit, bounded, and logged
- it must not silently change the mathematical plan

Prefer this over trying to make the scheduler itself understand weighted shard math.

---

## 7. Fold-CP runtime rewrite spec

## 7.1 Keep legacy distributed code intact; add a new tiled runtime package

Do not jam the new large-protein engine directly into the current `torchrun`-coupled code path.

Create a new package in the Fold-CP fork, for example:

- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/plan.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/tile_store.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/coordinator.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/failure.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`

This keeps the experimental large-protein runtime separate from current native CP code and minimizes collateral breakage.

## 7.2 Execution semantics

The new tiled engine should work like this:

1. Parse the prepared Fold-CP/Boltz input once.
2. Build a logical square grid and tile plan over the shared pair/context state.
3. Persist that global state in a tile store.
4. Emit work bundles that reference tile coordinates plus phase semantics.
5. Each worker job:
   - loads only the needed tiles into VRAM
   - runs the required computation for its bundle
   - writes updated tiles back to the store
   - marks the bundle complete
6. The coordinator advances only when all bundles in the current wave complete successfully.
7. Final output assembly happens only after the whole global state has advanced through all required phases.

The important rule is: the global state advances. The workflow is not allowed to turn into disconnected local mini-predictions.

## 7.3 Why GPU count no longer matters mathematically

In this design:

- the logical grid is fixed by the plan
- the worker pool is only an execution resource
- one GPU can process all bundles sequentially
- many GPUs can process bundles in parallel

That means non-square physical GPU counts are fine because the square requirement applies to the logical update topology, not to the number of visible CUDA devices.

## 7.4 Heterogeneous GPU handling

Do not start by making the mathematics ragged. Start by making execution heterogeneous.

Recommended v1 rule:

- logical tiles stay shape-consistent and mathematically clean
- different GPUs get different bundle sizes or eligibility classes
- weaker GPUs can be capped or excluded
- stronger GPUs simply drain more bundles over time

This is how the runtime becomes hardware-flexible without immediately rewriting the numerical kernels around irregular shard ownership.

### Worker classes

A practical v1 split is:

- small bundle class
- medium bundle class
- large bundle class

Each class has a corresponding scheduler reservation.

Example behavior:

- 5090 can take large bundles
- 3090s can take medium or large bundles depending on cap
- 5060 Ti can be capped to small bundles only or excluded entirely

This is enough to make the experimental workflow large-protein-oriented without solving weighted ragged ownership on day one.

---

## 8. Fail-fast and silent-skip elimination

## 8.1 Immediate patch for the current legacy CP path

Before any large tiled redesign lands, fix the current OOM behavior.

Required change:

- remove the `return {"exception": True}` local-skip behavior from `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/models/boltz2.py:1281-1286`
- replace it with a hard failure path that:
  - records/logs the failure
  - tears down or marks the process group failed
  - returns a non-zero exit to the launcher

If needed, add a tiny failure helper under the Fold-CP fork rather than sprinkling ad hoc exits through the code.

## 8.2 Required behavior for the new tiled engine

For the tiled large-protein runtime:

- any worker OOM must write a failure marker
- the coordinator must stop scheduling further bundles immediately
- the parent job must mark the run failed
- partial wave completion must not be treated as success

Optional bounded recovery:

- if enabled, the coordinator may split the failed oversized bundle once at a smaller bundle class and retry
- if the smaller retry still fails, the run fails
- this is explicit recovery, not silent skip

## 8.3 Success criteria for the fix

The following must stop happening:

- workers still alive but no further artifact growth
- queue/UI still showing “running” after a rank-local OOM
- one-rank exception while peers wait forever in communication barriers

---

## 9. Storage-tier design

## 9.1 DRAM-backed tile store is phase 1

Use a file-backed shared tile store with a configurable root. The default should prefer a tmpfs-backed location, but not blindly hardcode `/dev/shm`.

Recommended behavior:

- if a configured RAM-backed mount exists, use it
- otherwise use `/dev/shm` if the budget fits
- otherwise require an explicit store path or fall back to hybrid mode

Implementation preference:

- memory-mapped chunk files or an equivalent cross-process store format
- predictable tile naming and metadata
- safe concurrent access rules

Why this is the right first step:

- separate worker jobs need cross-process visibility
- memmap-style storage naturally supports DRAM and SSD backends through one interface
- it keeps the runtime decoupled from any single long-lived Python process

## 9.2 Hybrid SSD spill is phase 2, not a different architecture

The same tile-store interface should later support:

- hot tiles in DRAM
- cold tiles on SSD
- promotion/demotion policy based on upcoming wave needs

Recommended first hybrid policy:

- metadata and hottest working-set tiles in DRAM
- cold completed tiles on SSD under `/mnt/BioModStack/...`
- explicit prefetch before bundle launch
- bounded write-back after bundle completion

So SSD -> DRAM -> VRAM is not a pipe dream. It is a phase-2 backend of the same abstraction.

---

## 10. File-by-file phased implementation plan

## Phase 0: Remove silent skip and make failures honest

**Objective:** Make the current runtime fail cleanly on OOM before any larger architecture work.

**Primary files:**

- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/models/boltz2.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/predict.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/comm.py` if a small failure helper or explicit abort path is needed
- `/home/dalab/tmp/boltz-cp/tests/distributed/test_dtensor_predict.py`
- `/home/dalab/biomodstack/biomodstack/platform/api/tests/test_boltz_cp_experimental.py`

**Acceptance gate:** A forced worker OOM yields a failed job and no hang.

## Phase 1: Replace raw CP-size entry with shard-plan selection

**Objective:** Make the experimental UI and API centered on valid large-protein shard plans.

**Primary files:**

- `/home/dalab/biomodstack/biomodstack/platform/api/config/models/boltz_cp_experimental.yaml`
- `/home/dalab/biomodstack/biomodstack/platform/api/config/templates/boltz_cp_experimental.yaml`
- `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/structurePredictionUiState.ts`
- `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/StructurePredictionTemplate.tsx`
- `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/JobSubmission.tsx`
- `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/dashboard/reorchestrateStructureSettings.ts`
- `/home/dalab/biomodstack/biomodstack/platform/frontend/tests/structurePredictionUiState.test.ts`
- `/home/dalab/biomodstack/biomodstack/platform/frontend/tests/reorchestrateStructureSettings.test.ts`
- add a narrow backend plan-preview surface under `platform/api/...`

**Acceptance gate:** The launcher shows valid plan cards/options and no longer asks users to reason in terms of selected GPU count.

## Phase 2: Materialize a plan manifest and coordinator workflow

**Objective:** Turn the experimental workflow into a planner/spawn/wait/finalize workflow.

**Primary files:**

- `/home/dalab/biomodstack/biomodstack/platform/api/routers/jobs.py`
- `/home/dalab/biomodstack/biomodstack/platform/api/services/nextflow.py`
- `/home/dalab/biomodstack/biomodstack/workflows/boltz_cp_experimental.nf`
- `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- `/home/dalab/biomodstack/biomodstack/platform/api/tests/test_boltz_cp_experimental.py`
- `/home/dalab/biomodstack/biomodstack/platform/api/tests/test_nextflow_lint_regressions.py`

**Acceptance gate:** Launching the experimental workflow creates a plan manifest, initializes a coordinator path, and spawns bundle jobs as child jobs instead of a single `torchrun` world-size-coupled run.

## Phase 3: Add the DRAM tile store and single-worker proof

**Objective:** Prove the large-protein engine works with one GPU and a DRAM-backed tile store before adding multi-worker complexity.

**Primary files:**

- new files under `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/`
- `/home/dalab/tmp/boltz-cp/tests/distributed/`
- possibly lightweight CLI entrypoints/scripts inside the Fold-CP fork for bundle execution

**Acceptance gate:** One GPU can execute a full valid plan sequentially through tiles and produce final outputs without needing `world_size == logical_shards`.

## Phase 4: Multi-worker scheduling over existing BMS queue logic

**Objective:** Let the existing scheduler place bundle jobs across multiple heterogeneous workers.

**Primary files:**

- `/home/dalab/biomodstack/biomodstack/platform/api/services/gpu_orchestrator.py`
- add a narrow lease helper such as `/home/dalab/biomodstack/biomodstack/platform/api/services/gpu_batch_leases.py`
- optionally `/home/dalab/biomodstack/biomodstack/platform/api/database.py` only if a narrow persistent lease record is clearly better than a runtime sidecar

**Acceptance gate:** The same logical plan runs on 1, 2, 3, or 4 workers; GPUs affect throughput and bundle assignment, not correctness.

## Phase 5: Hybrid SSD spill

**Objective:** Add SSD spill without changing the planning model.

**Primary files:**

- tile-store backend files under the Fold-CP fork
- experimental workflow/backend config surfaces in BioModStack
- tests for spill promotion/demotion and restart safety

**Acceptance gate:** The same plan can exceed DRAM budget by using hybrid spill, with a bounded performance penalty but unchanged semantics.

---

## 11. Validation matrix

At minimum, validate these cases.

### A. Non-regression

- native `boltz2` structure prediction still launches and validates as before
- native `protenix` still launches and validates as before
- native `rf3` predict-only semantics remain unchanged
- experimental workflow still appears only through the experimental card/surface

### B. Failure semantics

- injected worker OOM in legacy CP path fails immediately
- injected worker OOM in tiled path fails the batch and coordinator immediately
- no silent partial success

### C. Scheduling

- 1-worker run
- 2-worker run
- 3-worker run
- 4-worker run on `5090 + 5060 Ti + 3090 + 3090`
- weaker worker capped or excluded

### D. Storage

- DRAM-only store within a chosen budget
- hybrid spill run with SSD-enabled store root
- restart/resume behavior if partial state exists

### E. UI/API truthfulness

- plan options shown by UI match backend validation
- re-orchestrated jobs reproduce the same plan semantics
- job detail surfaces show selected plan, store tier, and worker pool clearly

---

## 12. Immediate execution order

This is the recommended order of attack.

1. Kill the silent skip and make failures hard.
2. Replace raw `size_cp` thinking with server-generated shard-plan options.
3. Add the plan manifest and coordinator workflow.
4. Build the DRAM tile store and prove single-GPU sequential correctness.
5. Hook the bundle jobs into the existing scheduler.
6. Add hybrid spill.
7. Only after the above is stable, consider more ambitious weighted/ragged shard ownership.

That order matches the user goal:

- large-protein first
- sharding defined by math-valid plans
- GPU count made secondary
- DRAM caching first
- optional SSD spill later
- all of it self-contained under the experimental Fold-CP path
