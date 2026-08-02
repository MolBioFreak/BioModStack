# Fold-CP Context-Map Runtime Breakdown and Required Refactor Plan

> **For Hermes:** Use this as the current-state truth document for `boltz_cp_experimental`. Do not claim true context-map-driven execution is implemented until the active Fold-CP runtime consumes bundle geometry and DRAM tile-store state directly. Keep native `boltz2` and legacy CP behavior unchanged outside the experimental path.

**Goal:** Document exactly what the experimental BioModStack / Fold-CP path does today, why it is still GPU-topology-driven in execution semantics, and what core runtime/model changes are required to make logical shard geometry authoritative while physical GPUs act only as workers.

**Architecture:** Today the control plane is partially real: the UI exposes logical shard plans, the backend records plan metadata, the coordinator/spawn/wait/finalize Nextflow path exists, and distributed OOM fail-fast handling is implemented in the Fold-CP fork. The data plane is not yet real: the active child compute path still goes through the legacy `size_cp` / `world_size` DTensor runtime, and the separate `large_protein` DRAM-store runtime scaffold is not wired into the prediction entrypoint. The required refactor is to make the experimental path launch bundle-scoped worker jobs against a shared DRAM-first tile store, with logical plan geometry coming from the manifest rather than selected GPU count.

**Tech Stack:**
- `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/structurePredictionUiState.ts`
- `/home/dalab/biomodstack/biomodstack/platform/api/services/nextflow.py`
- `/home/dalab/biomodstack/biomodstack/scripts/spawn_boltz_cp_children.py`
- `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/predict.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/manager.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/layers/pair_averaging.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`
- `/home/dalab/tmp/boltz-cp/regression_tests/test_boltz2_oom_failfast.py`

---

## 1. Executive summary

Blunt version:

1. `boltz_cp_experimental` now has real control-plane scaffolding.
   - Valid logical shard plans are exposed in the UI/backend.
   - Coordinator/child orchestration exists.
   - The distributed OOM path has been upgraded from silent skip behavior to explicit fail-fast failure in the checked-out Fold-CP fork.

2. `boltz_cp_experimental` does **not** yet have true context-map-driven execution.
   - The launcher still derives runtime `size_cp` from selected GPU count.
   - The active child compute path still launches the standard Fold-CP distributed prediction flow.
   - Bundle metadata (`bundle_id`, row/col ranges, plan manifest path) is not consumed by the active Fold-CP runtime.
   - The separate DRAM tile-store runtime scaffold exists, but the active CLI / prediction path does not call it.

3. The real missing work is data-plane surgery, not more contract wiring.
   - Logical plan must become execution-authoritative.
   - GPUs must become worker resources only.
   - Child jobs must execute bundle-scoped work from plan-manifest / row-range / col-range inputs.
   - DRAM tile-store lifecycle must be integrated into the active experimental run path.

4. A safe path exists.
   - Keep legacy distributed CP runtime intact for standard `predict` / `size_cp` use.
   - Add a separate experimental tiled-worker runtime path for `boltz_cp_experimental`.
   - Preserve fail-fast failure semantics as a non-negotiable invariant.

---

## 2. Target architecture summary

The target architecture remains:

- Replace the GPU-count-coupled launch path with an experimental large-protein workflow that plans a logical shard grid independent of physical GPU count.
- Persist shared state in a DRAM-first tile store and treat physical GPUs as worker resources.
- Define logical sharding correctness by a shard plan, not selected GPU count.
- Let physical GPUs affect throughput/scheduling, not mathematical validity.
- Abort the whole run when a worker OOMs.
- Support sequential and 1..N-worker execution without requiring `world_size == logical_shards`.

The current gap remains implementation reality, not target architecture clarity.

---

## 3. What actually happens today end-to-end

### 3.1 UI: logical plan exists, but launcher still computes a GPU-count-based runtime bridge

Evidence:
- `platform/frontend/src/components/structurePredictionUiState.ts:106-124`
  - UI definitions expose `1x1`, `2x2`, and `4x4` logical plans with descriptions that correctly say the logical plan does not change with GPU count.
- `platform/frontend/src/components/structurePredictionUiState.ts:227-238`
  - `deriveBoltzCpGpuLaunchSettings(...)` parses selected GPUs and returns:
    - `gpuIds`
    - `sizeCp: getLargestSquareDivisor(resolvedGpuIds.length, requestedSizeCp)`
- `platform/frontend/src/components/structurePredictionUiState.ts:241-257`
  - `getBoltzCpRuntimeBridgeSummary(...)` explicitly says:
    - “GPU count only affects the current runtime bridge”
    - and shows `resolvedGpuLabel -> launch size_cp {sizeCp}`

Current truth:
- The UI contract is logical-plan-first.
- The runtime bridge is still GPU-count-derived.

Implication:
- The current UX is honest, but it is still describing a transitional bridge rather than the target architecture.

### 3.2 API/backend: runtime launch settings are still derived from selected GPUs

Evidence:
- `platform/api/services/nextflow.py:457-470`
  - `_derive_boltz_cp_gpu_launch_settings(...)` parses the GPU list and returns:
    - joined `gpu_ids`
    - `_largest_square_divisor(len(parsed_gpu_ids), requested_size_cp)`

Current truth:
- Backend launch settings still encode the legacy assumption that the physical GPU pool determines the admissible runtime `size_cp`.

Implication:
- The API is still translating a logical plan into a physical CP mesh request.
- This is incompatible with the final design where the logical plan should remain fixed even when the worker count changes.

### 3.3 Child spawning: bundle metadata is attached, but children are forced into `bcp_size_cp=1`

Evidence:
- `scripts/spawn_boltz_cp_children.py:123-143`
  - Child params include:
    - `bcp_plan_manifest_path`
    - `bcp_bundle_id`
    - `bcp_bundle_row_index`
    - `bcp_bundle_col_index`
    - `bcp_bundle_row_range`
    - `bcp_bundle_col_range`
  - The same block hard-codes:
    - `"bcp_size_cp": 1`

Current truth:
- Bundle metadata exists and is passed into child jobs.
- Children are launched as single-CP-rank jobs regardless of logical shard geometry.

Implication:
- The orchestration layer knows about bundles.
- The active compute layer is not using bundle geometry as the execution contract.

### 3.4 Workflow module: active run path still launches the legacy distributed predictor

Evidence:
- `modules/boltz_cp_experimental.nf:25-28`
  - `nproc` is derived from `bcp_gpu_ids`
  - `sizeCp` comes from `bcp_size_cp`
  - `sizeDp = max(nproc / sizeCp, 1)`
- `modules/boltz_cp_experimental.nf:119-125`
  - `bcp_size_cp` must be a perfect square and must divide `NPROC`
- `modules/boltz_cp_experimental.nf:287-293`
  - The active execution path launches:
    - `python3 -m torch.distributed.run --standalone --nnodes 1 --nproc_per_node $NPROC src/boltz/distributed/main.py predict ... --size_dp $SIZE_DP --size_cp $SIZE_CP`
- `modules/boltz_cp_experimental.nf:343-360`
  - The current child `stub:` block writes synthetic outputs and a minimal processed manifest containing `bundle_id` and `plan_manifest_path`, but does not use row/col ranges to scope real compute.

Current truth:
- The module still treats execution as a distributed world-size / CP-size problem.
- The child wrapper can record bundle metadata, but it is not converting that metadata into scoped compute.

Implication:
- The current workflow is a control-plane scaffold around the old compute model.
- Real bundle-scoped execution has not happened yet.

### 3.5 Fold-CP CLI/runtime: active path is still the legacy `size_cp` world-topology path

Evidence:
- `src/boltz/distributed/main.py:79-83`
  - CLI only exposes `--size_cp` for prediction; no `large_protein` / `run_bundle` / `finalize_plan` runtime entrypoint is wired here.
- `src/boltz/distributed/predict.py:257-263`
  - Runtime requires `world_size == size_dp * size_cp`
  - `size_cp` must be a perfect square
- `src/boltz/distributed/predict.py:291-294`
  - Builds grid groups as `("dp", size_dp), ("cp", (size_cp_axis, size_cp_axis))`
- `src/boltz/distributed/predict.py:392-407`
  - Builds `Boltz2InferenceDataModuleDTensor` from `processed.manifest`, `processed.targets_dir`, and `processed.msa_dir`
- `src/boltz/distributed/predict.py:468`
  - Trainer devices logic still branches on `size_cp == 1`

Current truth:
- The active prediction runtime still constructs the normal DP/CP mesh.
- It still consumes the normal processed manifest and target/MSA directories.
- It does not accept bundle geometry or tile-store state as primary execution inputs.

Implication:
- Even if BioModStack hands a child a `bundle_id` and row/col ranges, the active predictor does not use them.

### 3.6 Core CP internals: square 2D mesh assumptions remain embedded in the active path

Evidence:
- `src/boltz/distributed/model/layers/pair_averaging.py:58-60`
  - `group_layout` “must represent a square grid”
- `src/boltz/distributed/model/layers/pair_averaging.py:105-106`
  - Raises when `group_layout.shape` is not square
- `src/boltz/distributed/manager.py:508-510`
  - `create_grid_group(...)` remains the mesh-construction primitive used by the active runtime

Current truth:
- The active distributed CP model path is built around a square 2D rank layout.

Implication:
- This is fine for legacy CP.
- It is the wrong execution substrate for the desired worker-pool / bundle-queue architecture unless it is treated as an optional inner optimization rather than the global source of shard geometry.

### 3.7 Large-protein runtime scaffold exists, but it is not wired into active execution

Evidence:
- `src/boltz/distributed/large_protein/runtime.py:13-15`
  - `init_plan_store(...)`
- `src/boltz/distributed/large_protein/runtime.py:50-70`
  - `run_bundle(...)` marks bundle completion and writes result metadata/markers
- `src/boltz/distributed/large_protein/runtime.py:75-95`
  - `finalize_plan(...)` verifies completion markers and writes a summary
- `src/boltz/distributed/main.py`
  - no `large_protein` import or subcommand wiring
- direct repo scan
  - `bcp_bundle_row_range`, `bcp_bundle_col_range`, `bcp_plan_manifest_path`, and `bcp_bundle_id` have no hits inside `/home/dalab/tmp/boltz-cp`

Current truth:
- The DRAM/tile-store lifecycle helpers exist.
- The active Fold-CP entrypoint does not call them.
- Bundle-specific BioModStack params do not propagate into the Fold-CP codebase today.

Implication:
- The most important missing work is runtime integration, not more metadata threading.

### 3.8 Failure semantics: the fail-fast OOM fix is real and should be preserved

Evidence:
- The checked-out Fold-CP fork contains an explicit fail-fast distributed OOM path in `src/boltz/distributed/model/models/boltz2.py`.
- Regression coverage exists in `regression_tests/test_boltz2_oom_failfast.py`.
- Recent validation preserved passing results for the OOM regression and subgroup-layout tests.

Current truth:
- One important runtime hardening slice is already done.

Implication:
- The tiled runtime must inherit the same philosophy: any worker OOM or fatal bundle error must fail the whole experimental run quickly and visibly.

---

## 4. Bottom-line diagnosis

Today’s system is:
- real logical-plan contract
- real coordinator/spawn/wait/finalize scaffolding
- real OOM fail-fast hardening
- not yet real bundle-scoped compute
- not yet real DRAM tile-store execution
- still physically bridged through GPU-derived `size_cp`

Short version:
- control plane: partly done
- data plane: not done

---

## 5. Required changes for true context-map-driven execution

## 5.1 Architectural rule that must become true

For `boltz_cp_experimental`, the following must be enforced:

1. The logical shard plan defines the global context map.
2. The plan manifest defines bundle geometry and execution units.
3. The shared tile store holds global state and bundle artifacts.
4. Child jobs consume bundle IDs / tile ranges / plan-store paths.
5. Selected GPUs only define available workers and scheduling concurrency.
6. Legacy `size_cp` / `world_size` mesh semantics are not the source of truth for experimental large-protein execution.

If this rule is not true, the system is still a control-plane wrapper around the old CP runtime.

---

## 5.2 BioModStack frontend changes required

### Keep
- Logical shard-plan picker.
- Honest runtime-bridge wording during transition.

### Change
1. Stop treating `size_cp` as a user-visible or semantically primary runtime knob.
2. Make the runtime summary worker-pool-centric rather than CP-mesh-centric once the tiled worker path exists.
3. UI should display:
   - selected logical plan
   - estimated bundle count / bundle class
   - selected worker GPU pool
   - storage tier (`DRAM` now, optional SSD later)
4. After the runtime refactor lands, remove or clearly demote any wording of the form:
   - “launch size_cp X”
   - because the worker path should no longer derive correctness from GPU count.

Files:
- `platform/frontend/src/components/structurePredictionUiState.ts`
- relevant launcher panels already wired for shard-plan display

Acceptance signal:
- A `2x2` plan shown with 1 GPU and 4 GPUs should describe the same logical plan, with different worker concurrency only.

---

## 5.3 BioModStack backend / launch bridge changes required

### Keep
- Backend validation of admissible logical plans.
- Existing API contract shape and queue integration where possible.

### Change
1. Split “logical plan” from “worker pool” completely in backend launch settings.
2. Stop using `_derive_boltz_cp_gpu_launch_settings(...)` as the semantic authority for experimental execution.
3. Preserve selected GPU IDs only as:
   - worker allowlist
   - scheduling hints
   - concurrency limits
4. Introduce experimental-worker launch params that are independent of legacy `size_cp`, e.g.:
   - `bcp_store_root`
   - `bcp_plan_manifest_path`
   - `bcp_bundle_id`
   - `bcp_bundle_row_range`
   - `bcp_bundle_col_range`
   - `bcp_assigned_gpu`
   - optional bundle-class / wave metadata

Files:
- `platform/api/services/nextflow.py`
- possibly `platform/api/services/boltz_cp_shard_plans.py`

Acceptance signal:
- Changing GPU selection changes worker availability and throughput estimates, not plan validity or plan identity.

---

## 5.4 Nextflow workflow changes required

### Keep
- Coordinator/child split.
- Existing spawn/wait/finalize orchestration pattern.
- Existing queue/re-orchestration conventions.

### Change
1. Separate the legacy distributed CP launch branch from the experimental tiled-worker branch.
2. In the experimental branch, the child job must no longer be defined by `NPROC`, `SIZE_CP`, and `torch.distributed.run --nproc_per_node $NPROC`.
3. Replace the current child execution contract with a bundle-worker contract:
   - input: one bundle ID plus plan/store metadata
   - output: bundle result + completion marker + failure marker on error
4. Remove perfect-square and `NPROC % SIZE_CP == 0` requirements from the experimental worker path.
   - These checks belong to legacy CP launch, not worker-pool scheduling.
5. Coordinator flow should explicitly perform:
   - plan manifest creation
   - plan-store initialization
   - bundle job spawn
   - worker completion / failure aggregation
   - final summary emission

Files:
- `modules/boltz_cp_experimental.nf`
- `workflows/boltz_cp_experimental.nf`
- `scripts/spawn_boltz_cp_children.py`

Acceptance signal:
- The same logical plan is runnable with a single child GPU or many child GPUs without changing the plan geometry.

---

## 5.5 Fold-CP CLI changes required

### Keep
- Legacy `predict` path untouched for standard CP usage.

### Add
1. A dedicated experimental large-protein CLI surface in `src/boltz/distributed/main.py`.
2. Minimal safe shape:
   - `large-protein init-plan`
   - `large-protein run-bundle`
   - `large-protein finalize`
3. The new entrypoints should call the existing `large_protein/runtime.py` helpers first, then expand into real bundle execution logic.
4. Do **not** overload the legacy `predict` CLI with a giant matrix of experimental-only flags if that risks cross-path regressions.

Files:
- `src/boltz/distributed/main.py`
- `src/boltz/distributed/large_protein/runtime.py`
- likely new helper modules under `src/boltz/distributed/large_protein/`

Acceptance signal:
- Experimental child jobs can run bundle-scoped work without pretending that `world_size == logical_shards`.

---

## 5.6 Fold-CP runtime changes required

This is the core missing work.

### Current problem
`predict.py` still does all of the following:
- validates `world_size == size_dp * size_cp`
- requires square `size_cp`
- creates a DP/CP device mesh
- builds a DTensor inference datamodule from the full processed manifest
- runs the existing model path as a standard distributed CP job

### Required new behavior
For the experimental worker path, the runtime must instead:

1. Load the global plan manifest / store metadata.
2. Resolve the current bundle by `bundle_id`.
3. Use bundle row/col ranges as compute scope.
4. Materialize or fetch only the needed global-state tiles from the store.
5. Execute the bundle’s slice of the algorithm.
6. Write bundle result metadata and completion markers back to the store.
7. Abort the overall run immediately on OOM or unrecoverable bundle failure.

### Design rule
Treat legacy CP mesh code as one possible inner implementation detail, not as the top-level authority for sharding semantics.

Concretely, there are two viable paths:

#### Option A: Single-GPU bundle workers first
- Each bundle runs as a true single-GPU worker.
- `size_cp` remains `1` inside the worker runtime.
- Logical shard geometry lives entirely in the plan manifest / bundle coordinates.
- This is the cleanest first proof and matches the canonical plan’s phase ordering.

#### Option B: Optional intra-bundle CP later
- A single bundle may internally use a mini CP mesh as an optimization.
- That mini mesh must remain subordinate to the plan manifest.
- It must not redefine the logical plan or require `world_size == total logical shards`.

Recommendation:
- Implement Option A first and do not block it on intra-bundle CP experiments.

Files most likely to change:
- `src/boltz/distributed/predict.py` (either split or keep legacy-only)
- `src/boltz/distributed/large_protein/runtime.py`
- likely new worker/coordinator helpers under `src/boltz/distributed/large_protein/`

Acceptance signal:
- One GPU can execute all bundles sequentially for a valid logical plan and produce a completed summary without any `size_cp == logical_shards` requirement.

---

## 5.7 Model-layer implications

### Current problem
The active CP model path assumes a square 2D process layout (`pair_averaging.py` and `manager.py`). That is the right assumption for the legacy CP algorithm, but it is the wrong top-level contract for worker-scheduled bundle execution.

### Required rule
Do not rewrite the experimental runtime to “fake” the logical plan by stretching these mesh assumptions across available GPUs.

Instead:
- Keep square-grid logic confined to legacy CP or optional intra-bundle optimizations.
- Make the plan manifest / bundle geometry the authority for experimental execution.
- Introduce explicit translation layers only where mathematically required, not as the main user/runtime contract.

Acceptance signal:
- The experimental path can run the same logical plan with different worker counts without touching the plan geometry.

---

## 5.8 Failure semantics required

This is a hard requirement, not a nice-to-have.

### Must remain true
1. Any worker OOM fails the full experimental run.
2. Any worker fatal error produces a visible failure marker and parent failure.
3. No rank-local silent skip.
4. No partial stall where the UI still reports “running” while progress is dead.
5. No finalize step that silently accepts missing bundle completions.

### Implementation guidance
- Reuse the existing fail-fast OOM classification philosophy already added to `boltz2.py`.
- Extend the plan store format to include explicit failure markers for bundle workers.
- Make coordinator/wait logic stop scheduling and fail immediately on the first fatal worker error.

Acceptance signal:
- Injecting one worker OOM causes immediate visible batch failure and no hang.

---

## 5.9 Tests and proof required before claiming success

The next phase should not be declared complete until the following proofs exist.

### Proof A: one-worker sequential plan execution
- valid plan (for example `2x2`)
- single GPU worker pool
- all bundles execute sequentially
- final summary emitted
- no `world_size == logical_shards` coupling

### Proof B: same plan, more workers
- same exact logical plan
- run with 2+ GPUs / workers
- bundle parallelism increases
- final output semantics remain the same

### Proof C: worker failure behavior
- inject worker OOM or fatal bundle error
- child writes failure marker
- coordinator stops / parent fails
- no hang, no silent partial success

### Proof D: evidence durability
- preserve run artifacts/logs in files
- do not rely on transient terminal output because truncation has already been observed repeatedly

Likely test surfaces:
- `platform/api/tests/test_boltz_cp_experimental.py`
- new orchestration tests around worker-path params
- Fold-CP regression tests for tile-store lifecycle and bundle failure propagation
- end-to-end proof script / fixture for one-worker and multi-worker execution

---

## 6. Minimal implementation order

This is the smallest sane sequence that preserves truthfulness and limits regression risk.

### Phase A: finish the documentation-backed truth layer
- done by this document

### Phase B: wire the experimental CLI/runtime surface
- add explicit `large-protein` subcommands in `main.py`
- keep legacy `predict` path unchanged
- prove `init-plan`, `run-bundle`, and `finalize` work as callable lifecycle steps

### Phase C: make one-GPU bundle execution real
- worker consumes `bundle_id` and plan-store inputs
- executes bundle-scoped work
- writes completion / result markers
- prove one GPU can execute a full plan sequentially

### Phase D: integrate BioModStack child launches with the real worker path
- child jobs call the new worker entrypoint
- stop using `torch.distributed.run --nproc_per_node $NPROC` as the experimental child contract
- stop enforcing perfect-square / divisibility constraints in the worker path

### Phase E: add multi-worker scheduling proof
- same logical plan
- multiple worker GPUs
- same final semantics
- higher throughput only

### Phase F: optional intra-bundle CP / SSD spill later
- only after the worker-path truth is real
- do not mix this into the first proof slice

---

## 7. Honest status as of this document

Completed:
- logical plan contract/UI/backend scaffolding
- coordinator/spawn/wait/finalize scaffolding
- child bundle metadata threading at the BioModStack layer
- fail-fast distributed OOM hardening in the checked-out Fold-CP fork
- tests for the OOM regression and contract wiring

Not completed:
- actual bundle-scoped execution in Fold-CP
- actual DRAM tile-store integration into the active prediction path
- decoupling of execution semantics from GPU-derived `size_cp`
- proof that the same logical plan runs unchanged across different worker counts

Therefore:
- `boltz_cp_experimental` is currently a real experimental control plane plus a real failure-hardening slice,
- but it is **not yet** a true context-map-driven large-protein runtime.

---

## 8. Definition of done for the next implementation slice

Do not call the next slice complete until all of the following are true:

1. Experimental child jobs execute bundle-scoped work using plan/store inputs.
2. The same logical plan runs on one GPU sequentially without `world_size == logical_shards` coupling.
3. The same logical plan runs on multiple GPUs as a worker pool without changing plan geometry.
4. Worker OOM / fatal error fails the batch immediately and visibly.
5. The launcher/runtime summary describes workers and bundles, not a GPU-derived CP-size bridge.
6. Legacy `boltz2` / standard CP behavior remains unchanged.

If any one of these is still false, the runtime is still transitional.
