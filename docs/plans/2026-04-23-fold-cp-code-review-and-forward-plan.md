# Fold-CP Code Review and Forward Plan vs Tiled Context Worker-Pool Spec

Date: 2026-04-23

## Scope

Christian asked for:

1. Review original Fold-CP.
2. Review our modified Fold-CP / BioModStack integration.
3. Compare both against the clarified `tiled-context-worker-pool` spec.
4. Report a practical plan forward.

Reviewed checkouts:

- Clean/original Fold-CP:
  - path: `/home/dalab/code/vendor/hermes-agent/tmp/boltz-cp`
  - remote: `https://github.com/NVIDIA-Digital-Bio/boltz-cp.git`
  - commit: `f76f37a77c854b56b6250b426af8c2d63b501f7f`
  - local status: clean
- Our modified Fold-CP:
  - path: `/home/dalab/tmp/boltz-cp`
  - remote: `https://github.com/NVIDIA-Digital-Bio/boltz-cp.git`
  - base commit: `f76f37a77c854b56b6250b426af8c2d63b501f7f`
  - local changes include modified distributed files plus untracked `src/boltz/distributed/large_protein/`, `regression_tests/`, and `uv.lock`
- BioModStack integration:
  - path: `/home/dalab/biomodstack/biomodstack`
- Spec baseline:
  - `docs/plans/2026-04-23-tiled-context-worker-pool-runtime-spec.md`
- Prior reassessment:
  - `docs/plans/2026-04-23-true-distributed-cp-reassessment.md`

Lightweight verification run:

```bash
python -m py_compile \
  /home/dalab/code/vendor/hermes-agent/tmp/boltz-cp/src/boltz/distributed/predict.py \
  /home/dalab/code/vendor/hermes-agent/tmp/boltz-cp/src/boltz/distributed/comm.py \
  /home/dalab/code/vendor/hermes-agent/tmp/boltz-cp/src/boltz/distributed/model/layers/triangular_attention.py \
  /home/dalab/code/vendor/hermes-agent/tmp/boltz-cp/src/boltz/distributed/model/layers/triangular_mult.py \
  /home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py \
  /home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/plan.py \
  /home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py \
  /home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/tile_store.py \
  /home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py
```

Result: syntax compile completed without errors.

No runtime/regression suite was run for this review.

## Executive verdict

There are three different architectures being conflated:

1. Original Fold-CP / NVIDIA `boltz-cp`
   - Real distributed context parallelism.
   - GPU-resident DTensor/ring/collective implementation.
   - Requires simultaneous ranks.
   - Requires `size_cp` to be a perfect square.
   - `world_size == size_dp * size_cp`.
   - Not out-of-core.
   - Not user-defined logical tile scheduling.
   - Not DRAM/SSD-backed context execution.

2. Our current BioModStack `large-protein` rework
   - Useful control-plane scaffolding.
   - Real plan manifests, bundle metadata, parent/child orchestration, store/finalize flow.
   - But the data-plane path is shared-cache serial output tiling: one full serial `boltz.main predict`, then post-hoc tile slicing.
   - Not true distributed context-parallel execution.
   - Not memory scaling.
   - Not the final target.

3. Christian's clarified target: `tiled-context-worker-pool`
   - User-defined logical context plan first.
   - GPU discovery does not define shard geometry.
   - Pair/context state lives outside VRAM in tiered DRAM/NVMe/SSD storage.
   - GPUs are scheduled workers over tile/phase leases.
   - Same logical plan should run with 1, 2, 3, or 4 workers, just slower/faster.
   - This is an out-of-core tiled tensor runtime for Boltz-style pairformer computation, not a `torchrun` wrapper.

Blunt conclusion:

- Original Fold-CP gives us real CP math and communication patterns to mine.
- Our modified path gives us BioModStack orchestration scaffolding to reuse.
- Neither currently implements Christian's actual goal.
- The next tranche should not be another shared-cache 4x4 run. It should be a contract split, user-defined plan schema, tile-store v0, and fake-kernel worker-pool proof.

## Review 1 — Original Fold-CP

### What original Fold-CP does well

Original Fold-CP is a genuine context-parallel implementation for Boltz-2-style inference/training. It is not merely throughput parallelism.

Evidence:

- README describes a 2D CP mesh combined with data parallelism.
- Docs describe distributed inference/training using DTensor context parallelism.
- `src/boltz/distributed/predict.py` validates `world_size == size_dp * size_cp` and requires `size_cp` to be a perfect square.
- `src/boltz/distributed/predict.py` builds a DP/CP device mesh with CP axes derived from `sqrt(size_cp)`.
- `src/boltz/distributed/comm.py` contains communication classes such as `TransposeComm`, `Ring2DComm`, `AttentionPairBiasComm`, and `Ring2DCommTriAttn` that assume 2D group layouts.
- `src/boltz/distributed/model/layers/triangular_attention.py` and `triangular_mult.py` contain real distributed triangle operation patterns.
- `src/boltz/distributed/model/modules/confidencev2.py` documents placements for `s`, `z`, `d`, coordinates, and scalar outputs.

This is valuable. It proves that Boltz-style pair/context computation can be distributed in a mathematically meaningful way.

### Original Fold-CP hard constraints

Original Fold-CP is not GPU-count-independent.

Its launch model is:

```text
selected ranks -> world_size -> size_dp * size_cp -> square CP mesh
```

The hard constraints are:

- every rank participates simultaneously
- `size_cp` must be a perfect square: 1, 4, 9, 16, ...
- rank count and logical CP geometry are coupled
- the weak/smallest GPU can become the practical memory cap in a homogeneous mesh
- excluding the 16 GB 5060 Ti leaves 3 GPUs, which is not a valid square CP size for current native Fold-CP

This makes original Fold-CP an honest `native-fold-cp-square-mesh` backend, not the final out-of-core worker-pool backend.

### Original Fold-CP is not out-of-core

The state model is DTensor/GPU-resident, not persisted tile-store state.

Evidence from code review:

- Pair/single/confidence state is represented as DTensors with placements.
- Triangular attention and multiplication call into local tensor chunks and use ring communication, then reconstruct DTensors.
- The hot path assumes local GPU shards and synchronous communication.
- There is no persistent context tile store with per-tile metadata, checksums, leases, barriers, tier placement, or SSD/NVMe backing.
- CPU offload in the repo is activation-checkpointing oriented, not a persistent pair/context map store.
- `max_parallel_samples` and related knobs reduce diffusion/sample multiplicity pressure, not N x N pair/context residency.

So original Fold-CP can reduce per-GPU memory by sharding across GPUs, but it does not let a single 5090 stream a 500 GB effective context through DRAM/SSD.

### Original Fold-CP failure model is not worker-pool resilient

Original Fold-CP is synchronous collective compute. It can tolerate slow ranks in the sense that fast ranks wait, but it is not fault-tolerant in the worker-pool sense.

Observed/important properties:

- OOM paths can catch/skip/return exception-style results, but a rank that stops participating correctly can desynchronize collectives.
- Docs expose distributed timeouts, not tile-level retries.
- There is no tile lease table, work stealing, idempotent tile write protocol, stale worker recovery, or whole-plan restart from tile checkpoints.

For Christian's target, that failure model is insufficient.

### Original Fold-CP pieces worth reusing

Use original Fold-CP as the math/reference implementation, not as the scheduler/runtime shell.

High-value reusable pieces:

1. Pair/context sharding mental model
   - Which tensors are `single` vs `pair/context` vs coordinates/confidence.
   - Which axes need row/column/2D treatment.

2. Triangular operation algorithms
   - `triangular_attention.py`
   - `triangular_mult.py`
   - ring/chunk/double-buffer patterns
   - online/tiled softmax accumulation

3. Placement validation discipline
   - Strict expected layout checks are a good pattern for our tile metadata validation.

4. Gather/final output semantics
   - Useful reference for when state is local, distributed, or ready to publish.

5. Native backend proof path
   - It should remain a separate `native-fold-cp-square-mesh` backend for honest true-CP demonstration.

Do not try to mutate original Fold-CP directly into the final out-of-core backend in one step. Its assumptions are too different.

## Review 2 — Our modified Fold-CP and BioModStack integration

### What our path does well

The modified path adds useful control-plane pieces that original Fold-CP does not have:

- `large-protein init-plan`
- `large-protein run-bundle`
- `large-protein finalize`
- plan manifests
- bundle IDs
- row/column bundle ranges
- store roots
- bundle status markers
- parent/child BioModStack job spawning
- finalize summarization
- publication artifact copying
- separation of logical shard plan from physical GPU IDs in some places

Useful files:

- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/plan.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/tile_store.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py`
- `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- `/home/dalab/biomodstack/biomodstack/workflows/boltz_cp_experimental.nf`
- `/home/dalab/biomodstack/biomodstack/scripts/spawn_boltz_cp_children.py`
- `/home/dalab/biomodstack/biomodstack/platform/api/services/nextflow.py`
- `/home/dalab/biomodstack/biomodstack/platform/api/services/boltz_cp_shard_plans.py`

These are not wasted. They are a useful scaffold for the target runtime.

### Critical issue — current `large_protein` worker is serial shared-cache tiling

This is the central finding.

Current path:

```text
bundle worker -> shared_cache_executor -> _ensure_shared_prediction -> one locked serial boltz.main predict -> shared/prediction_manifest.json -> bundle workers slice artifacts
```

Evidence:

- `large_protein/worker.py` selects `shared_cache_executor` when `input_path` exists.
- `shared_cache_executor()` calls `_ensure_shared_prediction()` and `_publish_bundle_tiles()`.
- `_ensure_shared_prediction()` manages a single shared prediction manifest and lock.
- `_run_shared_prediction_once()` launches `python -m boltz.main predict --model boltz2`.
- shared manifest/result metadata reports `backend: serial-boltz2`.
- `_publish_bundle_tiles()` slices full artifacts (`embeddings`, `pae`, `pde`, `plddt`, structure/token output) into bundle artifacts.

This is useful for debug/publication, but it does not reduce the peak memory requirement of the serial prediction. The full prediction still has to fit first.

### Critical issue — bundle geometry is not model-state authority

`large_protein/plan.py` supports only canonical plans:

- `1x1`
- `2x2`
- `4x4`

It partitions row/column ranges and creates bundle IDs. That is real logical metadata.

But in the current worker, those row/column ranges are used after the full prediction exists. They do not assign live pair/context state computation.

Target behavior must be:

```text
claim tile/phase -> load needed global state tiles -> compute update -> write updated global state tile -> release/advance barrier
```

Current behavior is:

```text
wait for full serial prediction -> slice final tensors -> mark bundle complete
```

### Critical issue — store root is not a real tiered context store

Current `tile_store.py` provides directories, markers, shared prediction paths, bundle outputs, and publication paths. It does not provide the target tile-store semantics.

Missing:

- per-tile metadata for global model state
- dtype/shape/state-kind validation per tile
- tile checksums
- atomic temp-write/rename/finalize protocol for every state tile
- phase/layer/recycle indices
- memory-tier field for every tile
- DRAM -> SSD promotion/demotion
- lease table
- barrier table
- stale lease detection
- quota accounting
- resumable checkpoint protocol
- storage format optimized for hundreds of GB

`select_store_root()` and the current `configured_ram_root` behavior are not enough. They choose a root; they do not implement an out-of-core runtime.

### BioModStack has the right start, but still contains GPU-derived behavior

Good:

- `boltz_cp_shard_plans.py` starts to separate logical plans from physical GPUs.
- `nextflow.py` carries `bcp_shard_plan_id`, `bcp_gpu_ids`, `bcp_size_cp`, and plan metadata.
- `workflows/boltz_cp_experimental.nf` has a coordinator branch for logical multi-bundle jobs.
- `spawn_boltz_cp_children.py` creates child jobs with bundle metadata and assigned GPUs.
- children currently get `bcp_size_cp = 1`, which avoids forcing every child through native square mesh.

Bad / incomplete:

- `nextflow.py` still derives physical launch settings from selected/pinned GPU count in the native path.
- frontend state mirrors GPU-derived launch sizing for the existing CP UI surface.
- `modules/boltz_cp_experimental.nf` still has native `torch.distributed.run --nproc_per_node $NPROC --size_cp $SIZE_CP` behavior for native CP.
- child jobs are static bundles, not long-lived/polling tile/phase workers.
- round-robin GPU assignment is not a scheduler resource contract.
- no parent-level context-plan reservation.
- no per-device `{gpu_id, max_vram_mb, max_concurrent_tiles, weight}` worker-resource object.
- no per-tier memory reservation.
- no tile lease/barrier tables.
- no live phase/tile/GPU attribution.
- no whole-plan fail/cancel semantics around tile work.

## Spec comparison

### Spec requirement: user-defined logical context plan

Status:

- Original Fold-CP: fails for target. Logical CP plan is derived from `size_cp`/world size and must be square.
- Our modified path: partial. Has `1x1`, `2x2`, `4x4` logical plans, but catalog is tiny and bundle geometry is not compute-authoritative.
- Target: user/runtime defines tile shape/grid, dtype, state kinds, phase DAG, memory policy, and worker resources.

### Spec requirement: GPU discovery must not define shard geometry

Status:

- Original Fold-CP: fails. GPU/rank world defines CP mesh.
- Our modified path: partial. BioModStack has logical-vs-physical split in coordinator mode, but native launch logic still derives `size_cp` from GPUs and UI surfaces still reflect this.
- Target: selected GPUs are worker resources only. They determine concurrency, not plan shape.

### Spec requirement: pair/context state persisted outside VRAM

Status:

- Original Fold-CP: fails. GPU-resident DTensors and collective communication.
- Our modified path: fails for live model state. It persists post-hoc output tiles, not intermediate pair/context state.
- Target: pair/context tile store in DRAM/NVMe with explicit staging to VRAM.

### Spec requirement: tile/phase leases and barriers

Status:

- Original Fold-CP: fails. Synchronous collectives, no worker leases.
- Our modified path: partial. Bundle pending/running/complete/failed exists, but not tile/phase leases, phase DAG, barriers, stale recovery, or retry semantics.
- Target: scheduler-visible tile/phase work queue with leases and barriers.

### Spec requirement: same logical plan runs with fewer workers

Status:

- Original Fold-CP: fails. World size and CP mesh are part of plan validity.
- Our modified path: partial only for output-bundle orchestration, not for real compute.
- Target: same logical tile plan can run on one GPU or many GPUs, slower/faster.

### Spec requirement: final publication after global state completion

Status:

- Original Fold-CP: publishes after distributed run completes.
- Our modified path: publishes after bundle outputs/finalize, but those are shared-cache output tiles, not global state phases.
- Target: publish only after every phase/tile in the model-state DAG completes.

## Recommended backend split

Implement these as explicit backends and make the UI/API impossible to misread:

1. `shared-cache-serial-output-tiling`
   - current large-protein worker behavior
   - one serial prediction, output slicing
   - debug/publication utility only
   - fail closed if user asks for true/tiled CP

2. `native-fold-cp-square-mesh`
   - original Fold-CP true CP path
   - simultaneous `torch.distributed.run` ranks
   - square `size_cp`
   - useful honest demo / baseline
   - not out-of-core

3. `tiled-context-worker-pool`
   - Christian's real target
   - user-defined logical tile plan
   - explicit DRAM/NVMe/SSD tiered context store
   - BioModStack scheduler dispatches tile/phase workers
   - same plan can run with variable worker count

## Forward plan

### Phase 0 — Contract split and truthful naming

Goal:
Stop the current ambiguity.

Actions:

- Add explicit `execution_backend` everywhere.
- Values:
  - `shared-cache-serial-output-tiling`
  - `native-fold-cp-square-mesh`
  - `tiled-context-worker-pool`
- Current large-protein shared-cache worker must report itself as shared-cache serial tiling.
- Any request for true/tiled CP must fail before launch if it resolves to shared-cache serial tiling.
- UI copy must distinguish the three modes.

Touchpoints:

- `platform/api/config/models/boltz_cp_experimental.yaml`
- `platform/api/config/templates/boltz_cp_experimental.yaml`
- `platform/frontend/src/components/structurePredictionUiState.ts`
- `platform/api/services/nextflow.py`
- `modules/boltz_cp_experimental.nf`
- `workflows/boltz_cp_experimental.nf`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`

Acceptance tests:

- API normalization preserves requested backend.
- UI shows distinct backend semantics.
- shared-cache serial backend metadata cannot be mistaken for true CP.
- true/tiled request with shared-cache backend fails before launch.

### Phase 1 — User-defined plan schema and dry-run estimator

Goal:
Make the user's plan the authority.

Actions:

- Add a plan object with:
  - tile shape or grid shape
  - dtype
  - state kinds
  - phase DAG identifier
  - DRAM cache root/quota
  - primary SSD cache root/quota
  - secondary SSD cache roots/quotas
  - worker resources `{gpu_id, max_vram_gb, max_concurrent_tiles, weight}`
  - scheduler policy `{lease_seconds, retry_limit, barrier_timeout_seconds}`
- Preserve that plan through frontend -> API -> Nextflow -> Fold-CP runtime.
- Add dry-run estimates:
  - token count
  - pair/context byte estimate
  - tile byte estimate
  - temporary buffer safety factor
  - checkpoint safety factor
  - DRAM quota need
  - SSD quota need
  - per-worker VRAM need
- Reject invalid plans; do not silently replace with GPU-count-derived `size_cp`.
- Keep largest-square divisor logic only for `native-fold-cp-square-mesh`.

Touchpoints:

- `platform/api/config/models/boltz_cp_experimental.yaml`
- `platform/api/services/boltz_cp_shard_plans.py`
- `platform/api/services/nextflow.py`
- `platform/api/routers/jobs.py`
- `platform/frontend/src/components/structurePredictionUiState.ts`
- frontend launch/retry/reorchestration settings surfaces
- new API tests in/near `platform/api/tests/test_boltz_cp_experimental.py`

Acceptance tests:

- Same logical plan survives normalization with 1, 2, 3, and 4 selected GPUs.
- Changing GPU list changes worker resources only, not logical tile grid.
- Invalid memory roots/quotas fail preflight.
- Estimated 500 GB-class state points to SSD/NVMe backing, not `/dev/shm` only.

### Phase 2 — Tile-store v0 plus fake-kernel runtime

Goal:
Prove the runtime shape without Boltz math.

Actions:

- Add a real context store, likely new module:
  - `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/context_store.py`
- Extend or replace current `tile_store.py` for model-state tiles, not output tiles only.
- Implement per-tile metadata:
  - global tensor name
  - state kind
  - global shape
  - tile coordinates
  - dtype
  - phase/layer/recycle index
  - storage tier
  - checksum
  - status
- Implement atomic writes:
  - write temp file
  - fsync/close
  - rename
  - write/advance marker
- Implement tile/phase leases:
  - pending
  - claimed/running
  - complete
  - failed
  - stale
- Implement barriers:
  - per phase
  - per layer/recycle later
- Implement fake deterministic global `N x N x C` pair/context state updates.

Touchpoints:

- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/plan.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/tile_store.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- new `context_store.py`
- `regression_tests/test_large_protein_runtime.py`
- `regression_tests/test_large_protein_worker.py`
- new fake-kernel tests

Acceptance tests:

- One logical plan completes with one worker.
- Same logical plan completes with multiple workers.
- Final fake state is identical for 1-worker and N-worker execution.
- Worker crash/retry works or fails the whole plan coherently.
- Missing/corrupt tile fails validation.
- Metadata records DRAM/SSD tier decisions.

### Phase 3 — BioModStack scheduler integration for tile workers

Goal:
Move from one child per static output bundle to scheduled tile/phase workers.

Actions:

- Parent job creates context plan and work queue.
- Child worker jobs poll/claim tile/phase leases from shared store.
- GPU count controls concurrency only.
- Add live status:
  - active phase
  - tile coordinates
  - worker GPU
  - lease age
  - retries
  - barrier progress
- Add parent cancel/fail propagation.
- Add stale worker detection.
- Add memory-tier reservation checks.

Touchpoints:

- `scripts/spawn_boltz_cp_children.py`
- `scripts/wait_for_children.py`
- `modules/boltz_cp_experimental.nf`
- `workflows/boltz_cp_experimental.nf`
- `platform/api/services/nextflow.py`
- scheduler / GPU lock surfaces used by structure prediction jobs
- job status API and frontend progress display

Acceptance tests:

- Parent creates queue for fixed logical plan.
- 1 GPU and 3 GPUs run the same plan with different concurrency.
- UI/API reports active GPU/tile/phase attribution.
- Parent cancellation invalidates leases and children stop.
- Fatal tile failure blocks publication.

### Phase 4 — Native Fold-CP proof track, kept separate

Goal:
Keep a real true-CP demo path without confusing it with out-of-core runtime.

Actions:

- Add `native-fold-cp-square-mesh` as explicit backend.
- Launch original distributed predict path directly with `torch.distributed.run`.
- Keep `world_size == size_dp * size_cp` and perfect-square validation.
- Do not route through `large-protein run-bundle`.

Touchpoints:

- `modules/boltz_cp_experimental.nf`
- `workflows/boltz_cp_experimental.nf`
- `platform/api/services/nextflow.py`
- `platform/api/config/models/boltz_cp_experimental.yaml`

Acceptance tests:

- 4-GPU small run reports `native-fold-cp-square-mesh`.
- logs show distributed predict with `cp=4` and all ranks alive.
- 3-GPU native 4x4 request fails clearly.
- output is produced by `boltz.distributed.predict`, not serial `boltz.main predict`.
- rank failure fails the whole run.

### Phase 5 — Port one real Fold-CP operation into tiled runtime

Goal:
Begin real data-plane migration without attempting all of Boltz at once.

Candidate first operation:

- triangle multiplication, or
- triangular attention

Actions:

- Read required state tiles from context store.
- Stage only active working set into VRAM.
- Run one operation on local tile/chunk inputs.
- Write output tile back with metadata/checksum.
- Compare tiny outputs against serial/native reference.

Reference files:

- original `/src/boltz/distributed/model/layers/triangular_mult.py`
- original `/src/boltz/distributed/model/layers/triangular_attention.py`
- original `/src/boltz/distributed/comm.py`
- original `/src/boltz/distributed/utils.py`

Acceptance tests:

- One real op matches reference within tolerance on tiny tensors.
- State is not fully resident in VRAM.
- 1-worker and multi-worker outputs match.
- Tile read/write/barrier metrics are recorded.

### Phase 6 — Full experimental out-of-core path

Goal:
Incrementally expand from one operation to a complete experimental pairformer path.

Actions:

- Expand phase DAG across pairformer/recycling.
- Add checkpoint/restart.
- Add DRAM/NVMe promotion/demotion.
- Evaluate better storage formats:
  - Zarr
  - mmap/raw shards
  - safetensors shards
  - chunked binary plus SQLite/JSON metadata
- Optimize tile size and batching.
- Add compression only after correctness.
- Gate final publication on complete global state DAG.

Acceptance tests:

- fake or real case whose full state exceeds single-GPU VRAM completes using DRAM/SSD backing.
- metadata proves pair/context state persisted outside VRAM.
- restart from checkpoint works.
- final artifacts publish only after all barriers complete.

## Risks and design warnings

1. Do not let convincing tiled artifacts fool us.
   - Current shared-cache path can produce bundle artifacts while still requiring one serial full prediction.

2. Do not start with ragged/weighted tiles.
   - Start with equal logical tiles.
   - Express heterogeneity through dispatch cadence and worker caps first.
   - Let 5090 claim more work; weaker cards claim less.

3. Do not hardcode `/dev/shm`.
   - `/dev/shm` is only about 63 GiB here.
   - `/mnt/BioModStack` is the natural primary NVMe spill tier.
   - secondary roots should be configurable.

4. Do not start by rewriting all pairformer math.
   - Build fake-kernel runtime first.
   - Then port one operation.

5. Beware many-small-file `.npz` designs.
   - For hundreds of GB, storage format and access pattern matter.
   - `.npz` per tile is acceptable for early fake tests, not a guaranteed final format.

6. Native Fold-CP remains useful but separate.
   - It is an honest true-CP path.
   - It is not the out-of-core path.

7. Scheduler correctness is as hard as kernel correctness.
   - Leases, stale workers, barriers, retries, and partial publication are all failure-prone.

## Immediate next coding tranche

Recommended next tranche order:

1. Add backend enum and fail-closed behavior.
2. Add user-defined plan schema and dry-run estimator.
3. Add context-store/tile-store v0 with fake global state.
4. Add fake-kernel worker loop and 1-worker vs N-worker equivalence tests.
5. Rewire BioModStack children from static bundle jobs to tile/phase worker jobs.
6. Only then port one real Fold-CP operation.

Definition of success for the next tranche:

- user-defined logical plan survives through the stack unchanged
- selected GPU count only affects worker concurrency
- context-store writes real state tiles with metadata/checksums
- fake global pair/context state can be updated out-of-core
- same logical plan works with one worker and multiple workers
- publication is gated on phase completion
- current shared-cache serial tiling is clearly labeled and cannot be mistaken for true CP

## Final recommendation

Keep three tracks separate:

1. Truthful current utility:
   - preserve `shared-cache-serial-output-tiling` only as debug/publication/scaffold.

2. Honest true-CP proof:
   - wire `native-fold-cp-square-mesh` directly to original Fold-CP distributed predict.

3. Christian's actual target:
   - build `tiled-context-worker-pool` as a new user-defined, DRAM/NVMe-backed tile/phase runtime.

The third track is the real product direction. The first two are support tracks.
