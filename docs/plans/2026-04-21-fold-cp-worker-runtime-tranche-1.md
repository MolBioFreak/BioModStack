# Fold-CP Experimental Worker Runtime Tranche 1

> **For Hermes:** This is the approved next slice for `boltz_cp_experimental`. Do not start with `pair_averaging.py`. Do not rewrite the legacy `predict` CP path first. Move semantic authority from `size_cp/world_size` to plan/store/bundle metadata, but keep the legacy path intact for non-experimental usage.

**Goal:** Land the first real authority-transfer slice for `boltz_cp_experimental`: a dedicated Fold-CP worker runtime that is driven by the plan manifest and bundle geometry, plus BioModStack child-job wiring that uses that worker surface instead of `torch.distributed.run` as the experimental child contract.

**Architecture:** Keep the legacy Fold-CP `predict` entrypoint untouched for standard CP usage. Add a separate `large-protein` CLI/runtime surface in Fold-CP, make the plan store explicitly track running/completed/failed bundles, add a bundle-worker execution contract, and rewire BioModStack experimental child jobs to call that worker path with one assigned GPU per child. In this tranche, `size_cp` may remain as a compatibility field in the outer launch contract, but it must stop being the execution authority for the experimental child path.

**Tech Stack / Primary Files:**
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/tile_store.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/plan.py`
- `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_runtime.py`
- new `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_cli.py`
- new `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_worker.py`
- new `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/bundle_inputs.py`
- new `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- `/home/dalab/biomodstack/biomodstack/workflows/boltz_cp_experimental.nf`
- `/home/dalab/biomodstack/biomodstack/scripts/spawn_boltz_cp_children.py`
- new `/home/dalab/biomodstack/biomodstack/scripts/test_spawn_boltz_cp_children.py`
- `/home/dalab/biomodstack/biomodstack/platform/api/tests/test_boltz_cp_experimental.py`

---

## 0. Baseline that must stay green before and after every PR-sized step

Verified on 2026-04-21 15:13:48 CDT:

### Fold-CP regression baseline
Run from `/home/dalab/tmp/boltz-cp`:

```bash
uv run --extra test python -m pytest \
  regression_tests/test_large_protein_runtime.py \
  regression_tests/test_boltz2_oom_failfast.py \
  regression_tests/test_manager_subgroup_layout.py -q
```

Observed result:
- `9 passed, 14 warnings in 5.22s`

### BioModStack API baseline
Run from `/home/dalab/biomodstack/biomodstack/platform/api`:

```bash
uv run --group dev python -m pytest tests/test_boltz_cp_experimental.py -q
```

Observed result:
- `19 passed, 8 warnings in 4.49s`

Rule:
- Re-run the Fold-CP baseline after every Fold-CP change.
- Re-run the BioModStack API baseline after every BioModStack change.
- Do not batch unrelated edits together.

---

## 1. Tranche scope: what this slice does and does not do

### This tranche DOES
1. Add a dedicated `large-protein` CLI surface in Fold-CP.
2. Make plan-store lifecycle explicit: pending -> running -> complete / failed.
3. Add a bundle execution context that is loaded from `store_root + bundle_id`.
4. Add a worker execution wrapper that records success/failure against the plan store.
5. Rewire BioModStack experimental child jobs to call the worker path instead of `torch.distributed.run ... main.py predict`.
6. Preserve legacy `predict` and legacy CP internals unchanged.
7. Prove that the experimental child contract is bundle/store-driven rather than `size_cp/world_size`-driven.

### This tranche explicitly DOES NOT
1. Rewrite `src/boltz/distributed/model/layers/pair_averaging.py`.
2. Rewrite `src/boltz/distributed/manager.py` mesh semantics.
3. Rewrite the legacy `src/boltz/distributed/predict.py` execution model beyond tiny helper extraction if absolutely unavoidable.
4. Remove `size_cp` from the frontend/API contract yet.
5. Add SSD spill tiers.
6. Add intra-bundle multi-GPU CP.
7. Claim that final biological/model math is done for the large-protein worker path if the worker is still using a scaffolded executor.

This is an authority-transfer tranche, not the final optimization tranche.

---

## 2. Definition of done for this tranche

Do not call this tranche complete until all of the following are true:

1. Fold-CP exposes a `large-protein` CLI surface separate from legacy `predict`.
2. A plan store can represent running, completed, and failed bundles explicitly.
3. A worker loads bundle geometry from the store/manifest instead of deriving it from `size_cp`.
4. BioModStack experimental child jobs launch the worker path instead of `torch.distributed.run`.
5. Experimental child launches no longer require perfect-square or divisibility checks for the worker branch.
6. The coordinator/finalize path fails visibly on missing or failed bundles.
7. Existing Fold-CP OOM fail-fast regressions remain green.
8. Legacy `predict` behavior remains unchanged.

---

## 3. Exact implementation order (PR-sized steps)

## Step 1 — Add Fold-CP `large-protein` CLI surface

**Objective:** Create a stable entrypoint for the experimental worker runtime without touching the legacy `predict` path.

**Files:**
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py`
- Create: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_cli.py`

**Required commands:**
- `large-protein init-plan`
- `large-protein run-bundle`
- `large-protein finalize`

**Implementation notes:**
- Add a nested click group under the existing `cli()` in `main.py`.
- Keep the current `predict` command byte-for-byte behaviorally unchanged.
- `init-plan` should wrap the existing store/bootstrap path in `large_protein/runtime.py`.
- `run-bundle` should call the worker-wrapper entrypoint (added in Step 3), not the old `runtime.run_bundle(...)` helper directly.
- `finalize` should call the plan-store finalize path.

**RED tests first:**
- New test file should verify:
  - the `large-protein` group exists
  - `init-plan` returns a store path
  - `run-bundle` resolves a known bundle ID
  - `finalize` prints JSON and exits non-zero on incomplete plans

**Suggested test command:**
```bash
uv run --extra test python -m pytest regression_tests/test_large_protein_cli.py -q
```

**Green gate for Step 1:**
```bash
uv run --extra test python -m pytest \
  regression_tests/test_large_protein_cli.py \
  regression_tests/test_large_protein_runtime.py \
  regression_tests/test_boltz2_oom_failfast.py \
  regression_tests/test_manager_subgroup_layout.py -q
```

**Do not do in Step 1:**
- do not add experimental flags to legacy `predict`
- do not touch `pair_averaging.py`

---

## Step 2 — Make the plan store failure-aware

**Objective:** Ensure the worker runtime can express running/failed/completed state explicitly and that finalize refuses partial or failed runs.

**Files:**
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/tile_store.py`
- Modify: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_runtime.py`
- Optional create: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/failure.py`

**Required data-model changes:**
- Add explicit bundle states:
  - `pending`
  - `running`
  - `complete`
  - `failed`
- Add a failure marker or failure record path in the store layout.
- Add helpers such as:
  - `mark_bundle_running(...)`
  - `record_bundle_failure(...)`
  - `record_bundle_completion(...)`
- Make `finalize_plan(...)` fail when:
  - any bundle is missing completion
  - any bundle is marked failed

**RED tests first:**
Add tests for:
- failed bundle writes a failure artifact with error payload
- finalize rejects a failed bundle even if some bundles are complete
- bundle manifest status transitions from `pending -> running -> complete`
- finalize summary does not claim success on partial completion

**Suggested test command:**
```bash
uv run --extra test python -m pytest regression_tests/test_large_protein_runtime.py -q
```

**Green gate for Step 2:**
```bash
uv run --extra test python -m pytest \
  regression_tests/test_large_protein_runtime.py \
  regression_tests/test_large_protein_cli.py \
  regression_tests/test_boltz2_oom_failfast.py \
  regression_tests/test_manager_subgroup_layout.py -q
```

**Design rule:**
- do not silently treat a missing bundle as “not finished yet” in finalize; that is a hard failure for batch completion

---

## Step 3 — Add bundle input resolution and worker execution wrapper

**Objective:** Make bundle geometry execution-authoritative by introducing a worker entrypoint that loads `bundle_id` from the plan/store.

**Files:**
- Create: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/bundle_inputs.py`
- Create: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- Create: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_worker.py`
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py`

**Required new types/functions:**
- `BundleExecutionContext` dataclass with at least:
  - `store_root`
  - `plan_id`
  - `bundle_id`
  - `row_index`
  - `col_index`
  - `row_range`
  - `col_range`
  - `bundle_manifest_path`
  - `bundle_result_path`
  - `assigned_gpu` (optional)
- `load_bundle_execution_context(store_root, bundle_id, assigned_gpu=None)`
- `execute_bundle_worker(..., executor=...)`

**Worker behavior contract:**
1. Load the bundle context from the store/manifest.
2. Mark the bundle `running`.
3. Call a supplied executor function with the context.
4. On success, write result metadata and mark the bundle complete.
5. On exception, write a failure artifact, mark the bundle failed, then re-raise.

**Important tranche-1 rule:**
- The default executor may be a metadata-only or synthetic executor for tests, but the worker wrapper itself must be real and must not infer geometry from `size_cp`.

**RED tests first:**
- bundle lookup by ID succeeds and preserves row/col ranges
- unknown bundle ID raises cleanly
- worker marks running before execution
- worker writes failure record on injected exception
- worker writes completion record on success

**Suggested test command:**
```bash
uv run --extra test python -m pytest regression_tests/test_large_protein_worker.py -q
```

**Green gate for Step 3:**
```bash
uv run --extra test python -m pytest \
  regression_tests/test_large_protein_worker.py \
  regression_tests/test_large_protein_runtime.py \
  regression_tests/test_large_protein_cli.py \
  regression_tests/test_boltz2_oom_failfast.py \
  regression_tests/test_manager_subgroup_layout.py -q
```

**Do not do in Step 3:**
- do not make the worker depend on `world_size == logical_shards`
- do not bring back `torch.distributed.run` inside the worker wrapper

---

## Step 4 — Initialize a shared plan store from the BioModStack coordinator

**Objective:** Give the coordinator a shared absolute `store_root` that all children can use.

**Files:**
- Modify: `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- Modify: `/home/dalab/biomodstack/biomodstack/workflows/boltz_cp_experimental.nf`
- Modify: `/home/dalab/biomodstack/biomodstack/scripts/spawn_boltz_cp_children.py`
- Create: `/home/dalab/biomodstack/biomodstack/scripts/test_spawn_boltz_cp_children.py`

**Implementation details:**
- Keep the current sequence-length derivation in the coordinator process.
- Replace the direct `build_plan_manifest(...)` write path with a call to:
  - `python3 -m boltz.distributed.main large-protein init-plan ...`
- Pass a stable fallback store root, not the task sandbox. Use:
  - `${params.out_dir}/run/boltz_cp_experimental_coordinator/store/${parent_job_id}`
- Let Fold-CP pick `/dev/shm` automatically when it has enough space; otherwise fall back to the stable output path.
- Emit two coordinator artifacts:
  - `boltz_cp_plan_manifest.json` (copied from `${store_root}/metadata/plan_manifest.json`)
  - `boltz_cp_plan_store.json` containing at least `store_root`, `plan_manifest_path`, and `plan_id`

**Spawn-script changes:**
- accept a store-root input or coordinator metadata file
- pass these child params:
  - `bcp_store_root`
  - `bcp_plan_manifest_path`
  - `bcp_bundle_id`
  - `bcp_bundle_row_range`
  - `bcp_bundle_col_range`
  - `bcp_assigned_gpu`
- `bcp_size_cp` may remain as a compatibility field if needed by outer plumbing, but it must stop mattering for the child execution path

**RED tests first:**
- new script test verifies `spawn_boltz_cp_children(...)` propagates `bcp_store_root` and assigned GPU correctly
- API regression remains unchanged and green

**Suggested test commands:**
```bash
uv run --group dev python -m pytest tests/test_boltz_cp_experimental.py -q
pytest /home/dalab/biomodstack/biomodstack/scripts/test_spawn_boltz_cp_children.py -q
```

**Green gate for Step 4:**
- existing API test file still passes
- new spawn-script test passes

**Do not do in Step 4:**
- do not change frontend wording yet
- do not remove `size_cp` from the model YAML yet

---

## Step 5 — Switch experimental child execution from `torch.distributed.run` to the worker path

**Objective:** Make the child job contract bundle/store-driven instead of world-size/mesh-driven.

**Files:**
- Modify: `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- Modify: `/home/dalab/biomodstack/biomodstack/workflows/boltz_cp_experimental.nf`
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py`
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`

**Exact change:**
In the experimental child branch, replace:

```bash
python3 -m torch.distributed.run --standalone --nnodes 1 --nproc_per_node $NPROC \
  src/boltz/distributed/main.py predict ... --size_dp $SIZE_DP --size_cp $SIZE_CP
```

with a single-worker invocation similar to:

```bash
CUDA_VISIBLE_DEVICES="$ASSIGNED_GPU" \
python3 -m boltz.distributed.main large-protein run-bundle \
  --store-root "$BCP_STORE_ROOT" \
  --bundle-id "$BCP_BUNDLE_ID" \
  --assigned-gpu "$ASSIGNED_GPU"
```

**Worker-branch rules:**
- No perfect-square validation in the worker branch.
- No `NPROC % SIZE_CP == 0` check in the worker branch.
- One child job == one assigned worker slot == one bundle execution attempt.

**Keep unchanged:**
- parent/coordinator structure
- child stage name
- existing queue/re-orchestration behavior

**Truthfulness rule for this step:**
- If the worker still produces metadata-only outputs, write and publish those as receipts; do not fabricate final structure outputs.
- If a real compute adapter is plugged into `worker.py`, then publish only the artifacts it actually produced.

**Minimum green gate:**
- child branch no longer calls `torch.distributed.run`
- child branch succeeds with a single assigned GPU and a valid `bundle_id`
- failed worker writes a failure record and the job exits non-zero

---

## Step 6 — Finalize from the shared store, then aggregate child outputs

**Objective:** Make final success/failure come from the authoritative store summary rather than just “all child jobs finished”.

**Files:**
- Modify: `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- Modify: `/home/dalab/biomodstack/biomodstack/workflows/boltz_cp_experimental.nf`

**Implementation details:**
- In `FinalizeBoltzCPExperimentalChildren`, call:
  - `python3 -m boltz.distributed.main large-protein finalize --store-root "$BCP_STORE_ROOT"`
- Persist the emitted summary JSON as a published coordinator artifact.
- Fail the finalize process if the store summary indicates any failed or missing bundles.
- Continue aggregation of child outputs only after the store summary succeeds.

**Acceptance rule:**
- “All children finished” is not sufficient.
- The shared store summary is the authoritative completion gate.

---

## Step 7 — Proof run matrix for this tranche

Run these proofs only after Steps 1-6 are green.

### Proof A — one logical plan, one worker GPU
- logical plan: `2x2`
- assigned GPU pool: one GPU
- expected behavior:
  - same 4 bundles exist
  - one child/worker executes at a time across the plan
  - finalize summary is complete
  - no `world_size == logical_shards` requirement appears anywhere in the worker path

### Proof B — same logical plan, multiple worker GPUs
- logical plan: same exact `2x2`
- assigned GPU pool: 2 or 4 GPUs
- expected behavior:
  - same bundle IDs and same row/col ranges
  - only concurrency changes
  - final semantics remain identical

### Proof C — injected worker failure
- make one worker raise an injected exception
- expected behavior:
  - failure record written for that bundle
  - finalize fails visibly
  - parent/coordinator does not report success

### Proof D — OOM non-regression
Run from `/home/dalab/tmp/boltz-cp`:

```bash
uv run --extra test python -m pytest regression_tests/test_boltz2_oom_failfast.py -q
```

Expected:
- existing fail-fast OOM regression remains green

---

## 4. Files deliberately deferred to the next tranche

Do not touch these unless Step 5 proves impossible without a tiny helper extraction:
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/predict.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/layers/pair_averaging.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/manager.py`
- `/home/dalab/biomodstack/biomodstack/platform/frontend/src/components/structurePredictionUiState.ts`
- `/home/dalab/biomodstack/biomodstack/platform/api/config/models/boltz_cp_experimental.yaml`

Reason:
- these are legacy CP math/mesh surfaces or UX cleanup surfaces, not the first authority-transfer seam

---

## 5. Fast sanity checklist before merging this tranche

- [ ] `main.py` exposes `large-protein` commands and legacy `predict` still works unchanged
- [ ] plan store can represent failed bundles explicitly
- [ ] worker bundle context is loaded from `store_root + bundle_id`
- [ ] BioModStack child path no longer shells out to `torch.distributed.run` for the experimental worker branch
- [ ] finalize uses shared store truth, not just child completion count
- [ ] Fold-CP baseline regressions remain green
- [ ] BioModStack API regressions remain green
- [ ] no claims are made yet about pair_averaging or intra-bundle CP optimization

---

## 6. The next tranche after this one

Only after this tranche is green should the next tranche start:
1. remove or demote `size_cp` as a semantic field in API/frontend
2. decide whether the worker should stay single-GPU or gain optional intra-bundle CP
3. only then revisit `predict.py`, `manager.py`, and `pair_averaging.py` as subordinate implementation details

That ordering is intentional: first make the plan authoritative, then optimize how a bundle executes.