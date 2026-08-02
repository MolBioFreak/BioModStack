# Fold-CP Experimental Gap Spec and Readiness Audit (2026-04-23)

> **For Hermes:** Use this as the current truth document for `boltz_cp_experimental` after the worker-runtime tranche landed and post-fix smokes were re-run. Do not describe the current path as true distributed context parallelism, true DRAM-first execution, or GPU-count-agnostic execution authority. The control plane is materially more real than it was on 2026-04-21, but the data plane is still a transitional serial shared-prediction path.

**Goal:** Specify the actual remaining architecture and implementation gaps between the public BioModStack contract (`1x1` / `2x2` / `4x4` logical plans independent of GPU count) and the live Fold-CP experimental runtime, using current code and run artifacts.

**Architecture:** Today the system has real logical-plan manifests, bundle manifests, child spawning, plan-store finalization, and successful small-smoke publication. However, bundle workers still converge on a single shared `boltz.main predict` execution that writes one serial prediction manifest and then slices tiles from it. This means the public logical sharding contract is partially implemented in control-plane metadata, but not yet in execution semantics.

**Tech Stack / Primary Files:**
- `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/structurePredictionUiState.ts`
- `/home/dalab/biomodstack/biomodstack/platform/api/config/models/boltz_cp_experimental.yaml`
- `/home/dalab/biomodstack/biomodstack/platform/api/services/boltz_cp_shard_plans.py`
- `/home/dalab/biomodstack/biomodstack/platform/api/services/nextflow.py`
- `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- `/home/dalab/biomodstack/biomodstack/scripts/spawn_boltz_cp_children.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/plan.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/bundle_inputs.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/tile_store.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- Failed run artifacts under `/mnt/BioModStack/bms_results/context parallelism manual test 3-GPU_20260423_032615/...`
- Successful smoke artifacts under `/mnt/BioModStack/bms_results/cp-smoke-2x2-postfix-20260422-202520_20260423_012520/...`

## Patch-tranche update (same day, post-audit)

Three requested seam fixes are now landed in code:
- shared-manifest race hardening in `worker.py`
  - stale lock recovery now exists (`_try_acquire_prediction_lock(...)`, `_remove_stale_prediction_lock(...)` at `src/boltz/distributed/large_protein/worker.py:484-563`)
  - waiters can opportunistically reacquire and materialize the shared prediction under lock (`worker.py:484-503`)
- hard per-worker GPU clamp in the shared prediction subprocess env
  - `CUDA_VISIBLE_DEVICES` and `BCP_ASSIGNED_GPU` are set from `assigned_gpu` in `worker.py:205-226`
- coordinator RAM-root wiring no longer forces the disk fallback root to masquerade as the configured RAM root
  - `modules/boltz_cp_experimental.nf:495-512` now carries `BCP_CONFIGURED_RAM_ROOT`
  - `modules/boltz_cp_experimental.nf:650-660` always passes `--fallback-root "$BCP_STORE_ROOT"` and only appends `--configured-ram-root "$BCP_CONFIGURED_RAM_ROOT"` when explicitly provided

Regression evidence after the seam patch tranche:
- Fold-CP:
  - `uv run --extra test python -m pytest regression_tests/test_large_protein_runtime.py regression_tests/test_large_protein_worker.py regression_tests/test_large_protein_cli.py regression_tests/test_boltz2_oom_failfast.py regression_tests/test_manager_subgroup_layout.py -q`
  - result: `33 passed, 14 warnings in 4.39s`
- BioModStack targeted:
  - `uv run --group dev python -m pytest tests/test_boltz_cp_experimental.py tests/test_boltz_cp_experimental_workflow_contract.py ../../scripts/test_spawn_boltz_cp_children.py -q`
  - result: `26 passed, 8 warnings in 28.64s`
- Focused workflow/spawn subset:
  - `uv run --group dev python -m pytest tests/test_boltz_cp_experimental_workflow_contract.py ../../scripts/test_spawn_boltz_cp_children.py -q`
  - result: `6 passed in 24.79s`

What is still not done even after those patches:
- no supervised post-patch live `4x4` rerun has yet cleared the earlier `11/16 complete + 5 shared-manifest timeout` failure class
- no live successful run has yet demonstrated tmpfs/DRAM selection in the shared store path
- no live device-level evidence has yet shown that the assigned GPU clamp is the actual compute device used by each worker
- worker runtime still advertises `backend: serial-boltz2`; the data plane is still a shared serial prediction plus tile slicing, not true distributed CP execution

---

## 1. Executive summary

Blunt status:

1. The experimental control plane is real enough to launch and publish successful small smokes.
   - `2x2` successfully produced published artifacts after the latest fixes.
   - The plan store, bundle manifests, child orchestration, and finalize path are functioning.

2. The experimental data plane is still not what the UI/API contract implies.
   - Workers do not perform bundle-native compute.
   - They either acquire or wait on one shared prediction lock, run one serial `boltz.main predict`, and then slice bundle-local tiles from that shared result.
   - Runtime metadata explicitly labels the backend `serial-boltz2`.

3. The biggest remaining architecture gap is now the data plane, not the three seam fixes Christian explicitly asked for.
   - `4x4` still means 16 logical bundles in the manifest.
   - Shared-manifest hardening, configured-RAM-root wiring, and per-worker GPU env clamp are now patched and regression-tested.
   - But the live runtime still does not make those 16 bundles execution-authoritative in a GPU-count-agnostic way, and the earlier failed 3-GPU `4x4` run remains the only larger-run evidence until we do a supervised post-patch rerun.

4. DRAM-first behavior is no longer explicitly defeated by the coordinator contract, but it is still not proven in a live successful run.
   - The coordinator now always passes `BCP_STORE_ROOT` as the fallback root and only appends `--configured-ram-root` when `BCP_CONFIGURED_RAM_ROOT` is explicitly set.
   - `select_store_root(...)` can now make a real choice again instead of being force-fed the disk path as the configured RAM root.
   - Current archived successful runs still store under `/mnt/BioModStack/...`, i.e. NVMe-backed storage, not tmpfs.

5. There is currently no predefined larger bundle-size / length-class mechanism.
   - The only supported plan surface is `1x1`, `2x2`, `4x4`.
   - Sequence length is used to estimate required bytes and partition row/col ranges, not to choose predefined bundle-size classes.

Bottom line:
- Ready for: experimental orchestration smokes and honest control-plane validation.
- Not ready for: claiming true distributed CP execution, true DRAM-first shared storage, or recommending larger production-style `4x4`/EcDRT3-scale runs as if the runtime architecture were complete.

---

## 2. What the public contract says today

### 2.1 Frontend contract is logical-plan-first

Evidence:
- `platform/frontend/src/components/structurePredictionUiState.ts:98-123`
  - Defines `1x1 -> 1`, `2x2 -> 4`, `4x4 -> 16`.
  - Descriptions for `2x2` and `4x4` explicitly say: “The selected logical plan does not change with GPU count.”
- `platform/frontend/src/components/structurePredictionUiState.ts:240-257`
  - Runtime bridge summary text says the logical plan stays fixed and GPU count only affects the “current runtime bridge”.

Interpretation:
- The public UX contract is unambiguous: choosing `2x2` means 4 logical shards; choosing `4x4` means 16 logical shards.
- That contract is good and should remain the user-facing abstraction.

### 2.2 API contract also exposes only logical plans

Evidence:
- `platform/api/config/models/boltz_cp_experimental.yaml:29-39`
  - User-facing params are `input_path`, `shard_plan_id`, `gpu_ids`, etc.; there is no direct user-facing `size_cp` parameter.
- `platform/api/config/models/boltz_cp_experimental.yaml:53-57`
  - `shard_plan_id` is described as `1x1`, `2x2`, or `4x4`.
- `platform/api/services/boltz_cp_shard_plans.py:5-27`
  - API shard-plan catalog only defines `1x1`, `2x2`, and `4x4`.

Interpretation:
- Publicly, BioModStack is already presenting logical plans as the authority.
- This is the right contract to preserve.

---

## 3. What the live implementation actually does

### 3.1 Logical plans are real in manifests and bundle geometry

Evidence:
- `src/boltz/distributed/large_protein/plan.py:50-54`
  - Fold-CP only supports canonical square plans `1x1`, `2x2`, `4x4`.
- `src/boltz/distributed/large_protein/plan.py:82-113`
  - `build_plan_manifest(...)` partitions the sequence into real row/col ranges and emits one bundle per logical grid cell.
- Failed `4x4` run manifest:
  - `/mnt/BioModStack/.../metadata/plan_manifest.json:2-15, 220-268`
  - Shows `bundle_count: 16`, `sequence_length: 1065`, `physical_gpu_ids: ["0","2","3"]`, `physical_launch_size_cp: 1`, and `shard_plan.name: "4x4"`.

Interpretation:
- The control plane is genuinely creating the right logical geometry.
- Christian’s expectation that `4x4` means 16 bundles is satisfied at the manifest layer.

### 3.2 Backend still collapses runtime launch width from GPU count

Evidence:
- `platform/frontend/src/components/structurePredictionUiState.ts:177-197`
  - `getLargestSquareDivisor(...)` computes the physical launch width from GPU count.
- `platform/frontend/src/components/structurePredictionUiState.ts:226-237`
  - `deriveBoltzCpGpuLaunchSettings(...)` returns `sizeCp = getLargestSquareDivisor(resolvedGpuIds.length, requestedSizeCp)`.
- `platform/api/services/nextflow.py:461-470`
  - `_derive_boltz_cp_gpu_launch_settings(...)` does the same on the backend.
- `platform/api/services/nextflow.py:2897-2917`
  - Backend infers `shard_plan_id`, computes `requested_size_cp`, derives `(gpu_ids, size_cp)` from the GPU list, and writes `params['bcp_size_cp'] = derived_size_cp`.
- `platform/api/services/boltz_cp_shard_plans.py:88-103`
  - Catalog still exposes “physical_gpu_resolutions” by applying `largest_square_divisor(...)` to each GPU count.

Interpretation:
- Public contract says “logical plan first”.
- Internal launch bridge still says “available GPU count constrains physical launch width”.
- This is exactly the contract mismatch Christian called out.

### 3.3 Child jobs are bundle-scoped only at the metadata/orchestration layer

Evidence:
- `scripts/spawn_boltz_cp_children.py:153-174`
  - Each child gets bundle identifiers, row/col indices, row/col ranges, and `bcp_size_cp: 1`.
- `scripts/spawn_boltz_cp_children.py:206-223`
  - Each child also gets `bcp_assigned_gpu`, `bcp_gpu_ids = assigned_gpu`, and `pinned_gpus = [assigned_gpu]`.
- `modules/boltz_cp_experimental.nf:156-170`
  - Child branch runs `python -m boltz.distributed.main large-protein run-bundle --store-root ... --bundle-id ... [--assigned-gpu ...]`.

Interpretation:
- The child process contract is now bundle/store-driven instead of the old `torch.distributed.run` path.
- That is real progress and should not be undersold.
- But it is still only an authority-transfer seam unless the worker compute path is also bundle-authoritative.

### 3.4 Worker execution is still a single shared serial predictor plus tile slicing

Evidence:
- `src/boltz/distributed/large_protein/worker.py:50-52`
  - `shared_cache_executor(...)` calls `_ensure_shared_prediction(...)` and then `_publish_bundle_tiles(...)`.
- `src/boltz/distributed/large_protein/worker.py:67-69`
  - Worker uses `shared_cache_executor` whenever `_can_execute_shared_prediction(context)` is true.
- `src/boltz/distributed/large_protein/worker.py:90-92`
  - `_can_execute_shared_prediction(...)` returns true whenever `input_path` exists.
- `src/boltz/distributed/large_protein/worker.py:96-135`
  - `_ensure_shared_prediction(...)` acquires a shared lock, runs the prediction once if needed, otherwise waits for the manifest.
- `src/boltz/distributed/large_protein/worker.py:116-120`
  - Shared manifest is explicitly labeled with `backend: serial-boltz2`.
- `src/boltz/distributed/large_protein/worker.py:139-183, 202-220`
  - The actual shared run is a single subprocess: `python -m boltz.main predict ... --model boltz2 ...`.
- `src/boltz/distributed/large_protein/worker.py:254-268`
  - Collected shared manifest records one set of shared artifacts and `backend: serial-boltz2`.
- Successful `2x2` smoke result:
  - `/mnt/BioModStack/.../bundle-r00-c00/result.json:9-10, 30, 50-63`
  - Shows `backend: serial-boltz2`, `executor: shared-cache`, and references one shared prediction manifest.
- Failed larger run shared manifest:
  - `/mnt/BioModStack/.../shared/prediction_manifest.json:13-15`
  - Also shows `backend: serial-boltz2`.

Interpretation:
- Bundles are currently consumers of a shared serial Boltz prediction, not independent compute units.
- Current “parallelism” is mostly orchestration parallelism plus post-hoc tile extraction from one shared result.

### 3.5 Shared-manifest race was patched in code, but end-to-end live clearance is still pending

Evidence:
- `src/boltz/distributed/large_protein/worker.py:484-503`
  - `_wait_for_shared_prediction(...)` now checks for a published failure file and can reacquire the lock and materialize the shared prediction under lock when possible.
- `src/boltz/distributed/large_protein/worker.py:532-563`
  - `_try_acquire_prediction_lock(...)` now routes lock conflicts through `_remove_stale_prediction_lock(...)` instead of treating an existing lock as automatically healthy.
- Earlier failed `4x4` run summary remains the baseline larger-run artifact:
  - `/mnt/BioModStack/.../metadata/summary.json:34-75`
  - `completed_bundle_count: 11`, `failed_bundle_count: 5`, `publication_status: not_attempted`.
  - Failed bundles report `Timed out waiting for shared prediction manifest ...`.
- Post-patch regression evidence is green:
  - Fold-CP large-protein tranche: `33 passed`
  - BioModStack targeted tranche: `26 passed`
  - Focused workflow/spawn subset: `6 passed`

Interpretation:
- It is no longer accurate to describe this as an unaddressed code defect.
- It is accurate to say the seam is patched and regression-tested.
- It is still premature to say the timeout class is fully cleared in live orchestration until a supervised post-patch `4x4` rerun is recorded.

### 3.6 Coordinator no longer forces disk-backed store root as configured RAM root, but live DRAM-first selection still needs proof

Evidence:
- `modules/boltz_cp_experimental.nf:495-512`
  - Coordinator now carries a separate `BCP_CONFIGURED_RAM_ROOT` variable.
- `modules/boltz_cp_experimental.nf:650-660`
  - `large-protein init-plan` is always called with `--fallback-root "$BCP_STORE_ROOT"`.
  - `--configured-ram-root "$BCP_CONFIGURED_RAM_ROOT"` is appended only when the configured RAM root is non-empty.
- `src/boltz/distributed/large_protein/tile_store.py:87-101`
  - `select_store_root(...)` still prefers `configured_ram_root` when present, but now the coordinator no longer automatically aliases that to the persistent disk root.
- Archived successful smoke artifacts still live under `/mnt/BioModStack/...`, not tmpfs.

Interpretation:
- The specific contract bug that forced disk as the configured RAM root is fixed.
- That makes DRAM-first selection possible again.
- But we still need a live run plus plan-store/runtime evidence showing what root was actually selected before claiming DRAM-first execution is operationally proven.

### 3.7 Assigned GPU propagation now includes an explicit subprocess clamp, but live device proof is still pending

Evidence:
- `src/boltz/distributed/large_protein/bundle_inputs.py:12-25, 30-57`
  - `assigned_gpu` is loaded into the bundle execution context.
- `src/boltz/distributed/large_protein/worker.py:205-226`
  - Shared prediction subprocess environment now sets `CUDA_VISIBLE_DEVICES` and `BCP_ASSIGNED_GPU` from `assigned_gpu` when present.
- `src/boltz/distributed/large_protein/worker.py:266-280`
  - Shared manifest still reports `backend: serial-boltz2`, so the clamp improves scheduling correctness but does not by itself upgrade the data plane architecture.

Interpretation:
- It is no longer accurate to say strict device clamping is absent from the runtime path.
- It is accurate to say hard clamping is implemented in code.
- We still need live logs or runtime metadata showing which physical device the worker actually used before calling device assignment fully proven end-to-end.

---

## 4. Gap specification

### Gap A — Public logical-plan contract vs physical launch bridge

Current state:
- Public API/UI says plans are `1x1`, `2x2`, `4x4` and do not change with GPU count.
- Backend still converts that plan into a derived physical `size_cp` based on selected GPU count.

Why this matters:
- The user thinks they selected a fixed logical decomposition.
- The runtime still treats worker count as part of execution topology authority.

What must change:
- Keep `shard_plan_id` as the only user-facing authority.
- Treat physical worker count strictly as scheduling/concurrency capacity.
- Stop letting `largest_square_divisor(...)` define the semantics of the experimental runtime.

### Gap B — Bundle manifests are real, but bundle compute is not

Current state:
- Bundle manifests and child jobs are real.
- Worker execution still collapses to one shared serial `boltz.main predict` call.

Why this matters:
- The current system proves orchestration and output publication, not distributed CP math or multi-worker data-plane execution.

What must change:
- At least one runtime slice must make bundle geometry execution-authoritative.
- More than one bundle must perform compute without simply waiting on the same shared manifest.

### Gap C — DRAM-first store semantics are no longer blocked by coordinator wiring, but still need live proof

Current state:
- Store-selection code can choose `/dev/shm`.
- Coordinator no longer forces `configured_ram_root` to the disk-backed fallback root.
- We still do not have a post-patch run artifact proving what root was selected in practice.

Why this matters:
- The contract bug is fixed, but claims about DRAM-first caching/shared state are still premature until a live run shows tmpfs selection when requested.

What must change:
- Add explicit metadata stating whether store root resolved to tmpfs or disk.
- Capture at least one live run where the configured RAM root is intentionally set and the resulting store-root choice is recorded.
- Keep persistent fallback root and RAM-preference root distinct.

### Gap D — Shared-manifest synchronization was hardened, but the larger-run timeout class still needs a live rerun

Current state:
- Bundle waiters no longer only blind-poll the manifest; they can observe shared failure and attempt lock reacquisition/materialization when appropriate.
- The historical 3-GPU `4x4` failure remains the only larger-run evidence on record.

Why this matters:
- Regression tests show the seam patch is real, but readiness for larger supervised runs still depends on clearing the earlier live timeout signature.

What must change:
- Run the same `4x4`-style scenario again under supervision and compare against the earlier `11/16 complete + 5 waiter timeout` baseline.
- Preserve clear shared-predictor vs bundle-specific failure attribution in summary artifacts.
- Do not rely on unit/regression green alone as the final readiness proof.

### Gap E — Predefined larger bundle-size / length classes do not exist yet

Current state:
- Supported plans are only `1x1`, `2x2`, `4x4`.
- Sequence length is only used for required-byte estimation and equal partitioning.
- The focused scan over frontend/API/modules/scripts/docs/Fold-CP large-protein sources found no existing `bundle_size`, `length_class`, or equivalent predefined-size control surface in the live code paths.

Why this matters:
- Christian explicitly asked about “bigger bundle sizes of predefined lengths”.
- Right now there is no explicit abstraction for that; the system only knows “grid plan + equal slicing”.

What must change:
- Add a separate size-class surface if we want bundle granularity to vary by problem length.
- Do not overload worker count or `size_cp` to approximate that concept.

---

## 5. Recommended implementation order

## Phase 1 — Truthfulness + contract cleanup

Objective:
- Make every user-visible surface honest about what is real today.

Required changes:
- Update `platform/api/config/models/boltz_cp_experimental.yaml` text so it no longer implies completed multi-GPU CP runtime semantics.
- Update frontend summary/help text to keep “logical plan first” while clearly labeling the current physical launch bridge as transitional.
- Add a status field in run metadata that distinguishes:
  - `logical_plan_id`
  - `physical_worker_gpu_ids`
  - `physical_launch_size_cp`
  - `execution_backend` (`serial-boltz2-shared-cache` vs future real tiled backend)

Acceptance gate:
- No UI/API copy should let a user confuse current orchestration success with completed distributed CP execution.

## Phase 2 — Verify and expose store-root selection after the wiring fix

Objective:
- Turn the RAM-root patch into observable runtime evidence.

Required changes:
- Record the selected root kind (`tmpfs`, `disk`, later `ssd-cache`) in plan-store or summary metadata.
- Run at least one supervised job with an intentional RAM-root configuration and one with only fallback-root behavior.
- Preserve the now-correct coordinator contract where fallback root and configured RAM root remain distinct.

Acceptance gate:
- A run artifact can prove when `/dev/shm` was selected and when disk fallback was selected instead.

## Phase 3 — Clear the shared-manifest timeout class in a supervised live rerun

Objective:
- Prove that the seam patch actually fixes the previously observed larger-run failure mode.

Required changes:
- Re-run the same `4x4`-style scenario that previously ended at `11/16 complete + 5 shared-manifest timeouts`.
- Capture the resulting `summary.json`, failed bundle artifacts if any, and finalize/publication status.
- Preserve explicit attribution to shared-predictor failure vs bundle-local failure.

Acceptance gate:
- The rerun no longer fails with the earlier waiter-timeout signature, or it fails in a new, clearly attributable way.

## Phase 4 — Verify live device-assignment evidence on top of the clamp

Objective:
- Move from code-level clamp existence to runtime-level proof.

Required changes:
- Capture logs or metadata showing which worker got which assigned GPU and what device the subprocess actually saw.
- Keep the explicit `CUDA_VISIBLE_DEVICES` / `BCP_ASSIGNED_GPU` clamp in place.
- Avoid overstating the result: this proves scheduling/device isolation, not a completed distributed CP backend.

Acceptance gate:
- Per-worker runtime evidence demonstrates that assigned GPU and actual visible device match in a live run.

## Phase 5 — Introduce an explicit predefined bundle-size / length-class abstraction

Objective:
- Add the missing control surface Christian asked for without corrupting the logical shard-plan contract.

Recommended design:
- Keep `shard_plan_id` for logical topology (`1x1`, `2x2`, `4x4`).
- Add a separate optional field such as `bundle_size_class` or `target_tokens_per_bundle`.
- Example future classes: `compact`, `balanced`, `large`, `xlarge` (names TBD), each mapping to explicit target token ranges / per-bundle side lengths.

Likely files:
- `platform/frontend/src/components/structurePredictionUiState.ts`
- `platform/api/config/models/boltz_cp_experimental.yaml`
- `platform/api/services/boltz_cp_shard_plans.py`
- `platform/api/services/nextflow.py`
- `modules/boltz_cp_experimental.nf`
- `src/boltz/distributed/large_protein/plan.py`

Acceptance gate:
- The system can describe both “logical topology” and “bundle granularity” explicitly, without abusing physical GPU count as a proxy.

## Phase 6 — Replace the shared serial predictor with real execution-authoritative tiled work

Objective:
- Make logical bundles real compute units, not just post-hoc slices.

Allowed architectural options:
1. True bundle-native worker compute against a shared tile/tensor store.
2. A staged hybrid path where workers own bundle-local compute and shared artifacts are only coordination/caching aids.
3. A deeper Fold-CP native context-map runtime if that becomes necessary.

Non-goal for this phase:
- Do not rewrite legacy standard `predict` behavior for non-experimental usage.

Acceptance gate:
- At least one successful multi-bundle run shows multiple workers doing real compute work that is not reducible to “one serial predictor plus slicing”.

---

## 6. Readiness call

Current readiness, phrased carefully:

### What is ready enough
- Experimental logical-plan UI/API contract.
- Coordinator -> plan manifest -> spawn -> wait -> finalize orchestration.
- The three explicitly requested seam fixes at code level:
  - shared-manifest race hardening
  - configured RAM-root wiring separation
  - hard per-worker GPU env clamp
- Regression-backed confidence for those seam fixes:
  - Fold-CP tranche `33 passed`
  - BioModStack targeted tranche `26 passed`
  - Focused workflow/spawn subset `6 passed`
- Successful small-smoke publication (`2x2`) on the current transitional path.
- Honest debugging/instrumentation of bundle manifests and shared outputs.

### What is not ready enough
- Larger `4x4` confidence runs until we record a supervised post-patch rerun.
- Claims that execution is truly GPU-count-agnostic.
- Claims that DRAM-first shared storage has been proven in a live successful run.
- Claims that current runtime proves genuine multi-GPU CP data-plane behavior.
- Claims that assigned-GPU enforcement is fully proven end-to-end without live device evidence.

### Recommended public wording right now
- “The experimental orchestration path works for small smoke tests, and the immediate seam fixes requested for manifest hardening, RAM-root wiring, and GPU clamping are now landed and regression-tested.”
- “Logical plans and bundle manifests are real.”
- “The current worker runtime still uses a shared serial Boltz prediction backend and slices tiles from that shared result.”
- “We still need one supervised post-patch `4x4` rerun before calling the earlier shared-manifest timeout class cleared in live orchestration.”
- “We still need live evidence before claiming DRAM-first selection or assigned-GPU device usage as operationally proven.”

---

## 7. Concrete next-step recommendation

If only one next step is approved, do this first:

1. Run the supervised post-patch `4x4` validation against the earlier `11/16 complete + 5 waiter timeout` baseline.
2. In that same run, capture plan-store / summary evidence for selected store root.
3. Also capture per-worker device-assignment evidence from logs/runtime metadata.

Reason:
- The code-level seam fixes are already landed and regression-backed.
- The biggest remaining uncertainty is no longer “did we patch the seam?” but “does the live larger-run evidence now clear the old failure class without overclaiming the data plane?”

---

## 8. Relationship to earlier docs

This document supersedes the 2026-04-21 context-breakdown doc on one important point:
- earlier docs described the `large-protein` runtime as scaffolded but not yet the active child path;
- current code now does route experimental child jobs through `large-protein run-bundle`;
- however, the live worker implementation still resolves to a shared serial predictor backend, so the core “data plane not done” conclusion remains unchanged.
