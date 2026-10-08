# True Distributed Context Parallelism Reassessment (2026-04-23)

> For Hermes / BioModStack maintainers: this document is the corrective reassessment after recognizing that the current `large-protein` path is not true distributed context parallelism. It is a useful control-plane/shared-cache workflow around one full serial Boltz prediction, followed by tile slicing/publication. Do not describe it as true distributed CP.

## Bottom line

Christian is right: if the goal is true distributed context parallelism, the current rework missed the central data-plane problem.

What exists today:
- logical plan manifests (`1x1`, `2x2`, `4x4`)
- bundle row/column metadata
- child spawning and finalize semantics
- shared store / marker / failure tracking
- output tile slicing from a single shared prediction

What does not exist today:
- sharded live model state across workers
- bundle workers owning pair/context tensor computation
- distributed pairformer / triangle-op execution through the `large-protein` worker path
- GPU-count-independent execution of one prediction
- scheduler support for one job reserving and coordinating a GPU set
- acceptance tests proving a serial-OOM target succeeds because context state was distributed

Therefore the current `large-protein` backend should be called:

`shared-cache-serial-output-tiling`

not:

`distributed-context-parallel`

## Evidence from current code

### 1. The `large-protein` worker runs one serial Boltz prediction, then slices artifacts

File: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`

Key evidence:
- lines 50-52: `shared_cache_executor(...)` calls `_ensure_shared_prediction(...)` and `_publish_bundle_tiles(...)`.
- lines 67-68: this executor is selected whenever the context has `input_path`.
- lines 96-109: `_ensure_shared_prediction(...)` either returns an existing shared manifest, acquires one shared prediction lock, or waits.
- lines 147-226: `_run_shared_prediction_once(...)` launches `python -m boltz.main predict ... --model boltz2`, not `boltz.distributed.main predict`.
- lines 266-268: collected manifest is explicitly tagged `backend: serial-boltz2`.
- lines 325-396: `_publish_bundle_tiles(...)` loads full shared artifacts (`embeddings`, `pae`, `pde`, `plddt`) and writes bundle-local slices.

Interpretation:
- This proves output tiling.
- It does not prove distributed inference.
- It does not reduce the peak memory requirement of the underlying Boltz prediction, because the full serial prediction must still fit first.

### 2. The plan manifest is real, but it is not execution-authoritative for model compute

File: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/plan.py`

Key evidence:
- lines 50-54: only `1x1`, `2x2`, `4x4` square logical plans are supported.
- lines 82-113: `build_plan_manifest(...)` partitions sequence length into bundle row/column ranges.

Interpretation:
- The logical geometry is real.
- But in the current worker, row/column ranges are only used after the serial prediction finishes, to slice already-computed output tensors.
- They do not determine which worker computes which part of the pair/context state during inference.

### 3. BioModStack child orchestration is bundle-aware, but the child compute backend is still shared-cache serial

Files:
- `/home/dalab/biomodstack/biomodstack/scripts/spawn_boltz_cp_children.py`
- `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`

Key evidence:
- `spawn_boltz_cp_children.py` lines 153-174: each child gets `bcp_bundle_id`, row/col metadata, `bcp_store_root`, and `bcp_size_cp: 1`.
- `modules/boltz_cp_experimental.nf` lines 156-164: child jobs call `boltz.distributed.main large-protein run-bundle --store-root ... --bundle-id ... --assigned-gpu ...`.

Interpretation:
- The child contract is now bundle/store-driven.
- But the called worker path currently resolves to `shared_cache_executor`, so each bundle is a consumer of the same serial shared prediction.

### 4. The true Fold-CP runtime exists separately and is still square-mesh / simultaneous-rank based

Files:
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/main.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/predict.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/comm.py`

Key evidence:
- `main.py` lines 320-355: `predict` calls `boltz.distributed.predict.run_predict(...)` with `size_cp`.
- `predict.py` lines 257-262: requires `world_size == size_dp * size_cp` and `size_cp` must be a perfect square.
- `predict.py` lines 291-294: creates a `dp x cp` grid where `cp` is `(sqrt(size_cp), sqrt(size_cp))`.
- `comm.py` lines 279-292, 330-399, 489-512, 541-562: distributed comm classes require a 2D square group layout.

Interpretation:
- There is a real context-parallel code path in the Fold-CP fork.
- The current `large-protein` workflow does not use it for bundle compute.
- That native CP path is true distributed CP, but it is not GPU-count-agnostic worker-pool execution; it is a simultaneous torch/distributed mesh with square CP requirements.

### 5. Archived run artifacts confirm the current backend is serial shared-cache

Examples:
- `/mnt/BioModStack/bms_results/cp-smoke-2x2-postfix-20260422-202520_20260423_012520/.../summary.json`
  - complete 4/4 bundles
  - every result shows `backend: serial-boltz2`, `executor: shared-cache`, and `shared_manifest_path`.
- `/mnt/BioModStack/bms_results/context parallelism manual test 3-GPU_20260423_032615/.../plan_manifest.json`
  - `shard_plan_id: 4x4`, `bundle_count: 16`, `physical_gpu_ids: [0,2,3]`, `physical_launch_size_cp: 1`.
- same run `summary.json`
  - 11/16 bundles complete, 5 failed waiting for shared prediction manifest.
  - completed bundle results still show `backend: serial-boltz2`.
- same run `shared/prediction_manifest.json`
  - `backend: serial-boltz2`.

Interpretation:
- The artifacts validate the diagnosis: logical plans and tiles exist, but the prediction backend is serial.

## What we missed

### Miss 1 — We treated bundle lifecycle as if it were the hard part

The bundle lifecycle is necessary, but not sufficient.

Real distributed CP requires workers to participate in one evolving model state. For Boltz-style models, the hard object is the pair/context state, especially `z`-like `[tokens, tokens, channels]` state and the row/column-coupled pairformer/triangle operations.

Current bundle jobs do not own live parts of that state. They wait for a full prediction and slice its outputs.

### Miss 2 — We decoupled logical plans from UI/API, but not from compute semantics

The UI/API can say `4x4 means 16 logical shards`, but the runtime still does not compute those 16 shards as pieces of one distributed inference.

Today `4x4` means:
1. build 16 bundle manifests
2. run one serial Boltz prediction if none exists
3. slice outputs into 16 artifact bundles

The target meaning is:
1. build a logical decomposition of the pair/context computation
2. schedule workers over that decomposition
3. advance one global model state through pairformer/diffusion/confidence phases
4. gather/publish only after the global state has completed

### Miss 3 — The current child-job model is wrong for native NCCL CP

Native Fold-CP `predict` needs simultaneous ranks in one distributed process group.

BioModStack child jobs are currently independent single-GPU Nextflow jobs. That model is good for queueable worker-pool tasks, but it does not directly satisfy torch/distributed rendezvous semantics.

So there are two different architectures being mixed:
- native Fold-CP square mesh: one launch, N simultaneous ranks, NCCL/Gloo collectives
- BioModStack worker-pool tiles: many child jobs, shared store, explicit barriers/checkpoints

Both can be useful, but they cannot be treated as the same thing.

### Miss 4 — We did not add a fail-closed true-CP contract

Right now a run can look like a context-parallel run while silently using `serial-boltz2` under the hood.

For the true-CP goal, that is unacceptable. The runtime needs an explicit backend choice and a fail-closed guard.

Recommended backend names:
- `shared-cache-serial-output-tiling` — current path
- `native-fold-cp-square-mesh` — existing true CP `torchrun`/DTensor path
- `tiled-context-worker-pool` — future GPU-count-agnostic tile-store runtime

If the user requests true distributed context parallelism, the current shared-cache serial backend should hard-fail rather than silently run.

### Miss 5 — Scheduler placement is too weak for one multi-GPU inference

Current BioModStack scheduling surfaces mostly describe one assigned GPU per job or a flat pinned GPU list.

True CP needs one of:
- atomic reservation of a GPU set for one distributed process group; or
- a worker-pool placement contract with per-device capacities, task leases, barriers, and failure propagation.

Missing scheduler objects:
- parent-level multi-GPU reservation
- per-device shard/worker caps, e.g. `{gpu_id, max_vram_mb, weight}`
- rank-to-device mapping for native CP
- worker lease renewal / stale worker recovery
- whole-plan cancellation on any fatal shard failure
- live attribution of which rank/worker owns which tile/phase

### Miss 6 — Hardware reality makes the current native CP path awkward on this machine

Live host inventory:
- GPU0: RTX 5090, 32 GiB
- GPU1: RTX 5060 Ti, 16 GiB
- GPU2: RTX 3090, 24 GiB
- GPU3: RTX 3090, 24 GiB
- topology: no NVLink; links are NODE/PHB PCIe-class paths.

Implications:
- Native Fold-CP square CP can do `size_cp=4`, but that likely includes the 16 GiB 5060 Ti unless we redesign rank/device mapping.
- Excluding the 5060 Ti leaves 3 GPUs, which is invalid for the current square CP mesh.
- A future worker-pool/tiled executor is more attractive for this workstation because it can let strong GPUs do more work and weak GPUs do less, but that is a real runtime redesign, not a config tweak.

## Corrected architecture decision

Christian clarified the actual target after this reassessment: the mechanism of parallelism should be user-defined, not derived automatically from detected GPUs; and the long-term runtime should be able to hold the global context/pair state in DRAM and eventually SSD/NVMe cache, then feed GPU workers through BioModStack scheduling. In other words, GPU count should affect concurrency, not define the logical context plan.

A dedicated spec for that target now lives at:

`docs/plans/2026-04-23-tiled-context-worker-pool-runtime-spec.md`

We should split the work into two tracks instead of pretending the current `large-protein` path is already the distributed runtime.

### Track A — Immediate truthful true-CP proof: native Fold-CP square mesh

Purpose:
- prove actual distributed context parallelism on the real Fold-CP code path.

Approach:
- add an explicit BioModStack backend/mode: `native-fold-cp-square-mesh`
- launch `python -m torch.distributed.run ... boltz.distributed.main predict --size_cp 4 ...` from a single Nextflow process that owns all selected GPUs
- keep `world_size == size_dp * size_cp`
- keep perfect-square validation
- label this mode honestly as native square-mesh CP, not GPU-count-agnostic logical tiling

Acceptance criteria:
- shared manifest/result metadata says `backend: native-fold-cp-square-mesh`, not `serial-boltz2`
- logs show `Boltz-2 distributed inference: ... cp=4`
- all 4 ranks are alive concurrently
- output is produced by distributed `boltz.distributed.predict`, not serial `boltz.main predict`
- rank OOM fails the whole run, not a single output tile

Limitations to display:
- requires square CP size
- requires simultaneous ranks
- weak GPU can cap feasibility
- no 3-GPU `4x4` native CP on the current runtime
- not the final worker-pool architecture

### Track B — Actual target: tiled context worker-pool runtime

Purpose:
- make logical context/tile plans execution-authoritative and GPU-count-independent.

Approach:
- treat GPUs as workers over a global pair/context tile store
- treat bundles as units of model-state work, not output slices
- execute pairformer/triangle phases over tiles with explicit barriers
- allow 1, 2, 3, or 4 workers to advance the same logical plan at different speeds
- eventually support heterogeneous placement by dispatch cadence/capacity, not by pretending the square DTensor mesh is weighted

Core data-plane requirements:
- define persistent tensor/state layout for at least:
  - token/single representations
  - pair/context tiles
  - masks/features needed by pairformer operations
  - per-layer/recycling intermediate state
  - final gather/publication state
- define operation DAG/phases:
  - feature/init phase
  - pairformer layer N row/column attention/multiplication phases
  - barrier/communication phases
  - diffusion/confidence phases, with clear centralization vs sharding choices
- define worker contract:
  - claim tile/phase lease
  - load required neighbor/row/column tiles
  - run kernel/update
  - write tile result atomically
  - report metrics/failure
- define correctness gates:
  - no final publication until every required phase/tile is complete
  - fatal worker failure marks the whole plan failed unless the phase is restartable
  - tile checksums/shapes recorded for reproducibility

This is the real project. It is much larger than the current control-plane tranche.

## Concrete next implementation order

### Phase 0 — Stop overclaiming immediately

Changes:
- add explicit runtime backend metadata everywhere:
  - `shared-cache-serial-output-tiling`
  - `native-fold-cp-square-mesh`
  - future `tiled-context-worker-pool`
- if a request asks for true distributed CP, reject `shared-cache-serial-output-tiling` unless explicitly overridden for debugging.
- update frontend/API copy so `large-protein` does not imply true CP.

Files:
- `platform/api/config/models/boltz_cp_experimental.yaml`
- `platform/frontend/src/components/structurePredictionUiState.ts`
- `platform/api/services/nextflow.py`
- `modules/boltz_cp_experimental.nf`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`

### Phase 1 — Add native Fold-CP backend as the honest true-CP proof path

Changes:
- introduce `bcp_execution_backend=native-fold-cp-square-mesh`
- for that backend, bypass `large-protein run-bundle`
- launch native distributed `predict` in one Nextflow process with selected GPUs
- require valid `size_cp` square and `nproc == size_dp * size_cp`
- emit metadata proving distributed backend, rank count, size_cp, and CUDA device mapping

Files:
- `platform/api/config/models/boltz_cp_experimental.yaml`
- `platform/api/services/nextflow.py`
- `workflows/boltz_cp_experimental.nf`
- `modules/boltz_cp_experimental.nf`
- frontend summary/retry surfaces
- API/workflow contract tests

Acceptance test:
- a small `size_cp=4` run on 4 GPUs emits `backend=native-fold-cp-square-mesh` and distributed logs.

### Phase 2 — Preserve current shared-cache path, but demote it to debug/publishing utility

Use cases:
- validate plan manifests
- validate child orchestration/finalization
- generate tile artifacts from a serial prediction for downstream visualization/debugging

Non-use cases:
- memory scaling
- true CP claims
- production large-protein inference

### Phase 3 — Write the tiled-context runtime spec before more coding

The spec must define:
- tensor state ownership
- tile/phase DAG
- barriers and failure semantics
- local GPU memory budget model
- RAM/NVMe tile-store format
- worker lease protocol
- integration boundary with existing Fold-CP kernels
- which parts of Boltz2 stay centralized initially

Recommended file:
- `docs/plans/2026-04-23-tiled-context-worker-pool-runtime-spec.md`

### Phase 4 — Implement a tiny fake-kernel tiled runtime testbed

Before touching Boltz2 math, build a deterministic toy state engine:
- global `N x N x C` pair-state tiles
- row/column update phases
- barriers
- 1-worker and N-worker equivalence tests
- worker death / retry / stale lease tests

This proves the worker-pool semantics independently from Boltz.

### Phase 5 — Port one real Fold-CP operation into the tiled runtime

Do not begin with the whole prediction pipeline.

Candidate sequence:
1. pair-state initialization / simple replicated feature load
2. one triangle multiplication or attention update over tiles
3. one pairformer block equivalence test against native Fold-CP/serial for tiny inputs
4. only then expand to recycling/full inference

### Phase 6 — Add heterogeneous scheduling after equal-tile correctness

On this host, do not start with ragged weighted tile geometry.

Prefer:
- regular equal logical tiles
- per-GPU caps and dispatch cadence
- strong GPUs claim more tasks; weak GPUs claim fewer
- keep algebra regular until correctness is proven

Later, if needed:
- ragged weighted tiles
- custom gather/transpose boundaries
- topology-aware placement

## Revised acceptance criteria for “true distributed context parallelism”

A run only counts as true distributed context parallelism if all of these are true:

1. The full serial `boltz.main predict` path is not used as the data-plane backend.
2. More than one GPU participates in the same prediction before final outputs exist.
3. Intermediate model state, not just final output artifacts, is sharded or distributed.
4. Failure of one rank/worker fails or restarts the global prediction coherently.
5. Metadata states the backend clearly and cannot be confused with serial shared-cache tiling.
6. At least one validation case shows lower peak per-GPU memory than serial inference for the same target, or a target that fails serial but succeeds distributed.
7. Output publication happens after distributed state completion, not before/post-hoc slicing.

## What to do next, practically

Do not spend the next tranche on another `4x4` shared-cache run and call it CP readiness. That can validate the shared-manifest race fix, but it cannot validate true CP.

Recommended immediate move:
1. Add/fix the backend contract so `shared-cache-serial-output-tiling` is explicit and fail-closed for true-CP requests.
2. Wire a `native-fold-cp-square-mesh` mode as the shortest honest true-CP proof.
3. Run a small 4-GPU distributed prediction and capture rank/device/memory evidence.
4. In parallel, write the real tiled worker-pool runtime spec before doing deeper code surgery.

This gives us an honest near-term true-CP demonstration while preserving the bigger GPU-count-agnostic worker-pool goal.
