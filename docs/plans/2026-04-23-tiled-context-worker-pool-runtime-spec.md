# Tiled Context Worker-Pool Runtime Spec (Christian Clarified Goal, 2026-04-23)

## Why this spec exists

Christian clarified that the target is not automatic GPU detection that picks a `size_cp` from currently visible GPUs.

The target is:

1. User-defined parallelism and memory policy.
2. Context/pair state that can live outside VRAM, first in DRAM and eventually SSD/NVMe cache.
3. GPU(s) treated as scheduled compute workers that stream pieces of one global context state through VRAM.
4. Ability to handle proteins/complexes whose effective pair/context state would require hundreds of GB of VRAM if held monolithically.

This means the desired architecture is not just native Fold-CP square-mesh context parallelism. Native Fold-CP is an honest true-CP proof path, but it is still a simultaneous-rank GPU mesh with square CP constraints. Christian’s actual target is a streamed, user-directed, out-of-core context runtime.

Working backend name:

`tiled-context-worker-pool`

## Non-negotiable design correction

GPU count must not define the logical context plan.

Current bad pattern:

```text
visible GPUs -> derive nproc / size_cp -> infer shard geometry
```

Target pattern:

```text
user/runtime plan -> logical context tile grid + memory tiers + selected worker resources -> scheduler feeds tiles/phases to GPUs
```

In the target design:
- logical shard/tile ownership is defined before GPU assignment
- GPU count affects throughput/concurrency, not whether the logical plan exists
- a 1-GPU, 2-GPU, 3-GPU, or 4-GPU run can execute the same logical tile plan more or less slowly
- a run can intentionally use only one GPU while using DRAM/NVMe as the real state reservoir
- BioModStack scheduler logic controls admission, leases, and work dispatch instead of Fold-CP inferring execution shape from CUDA visibility

## Current host storage/memory reality

Measured on 2026-04-23 and corrected with Christian's storage note:

```text
/dev/shm:              tmpfs, ~63 GiB available
host RAM:              125 GiB total, ~90 GiB available at measurement time
/mnt/BioModStack:      Samsung 990 PRO 4TB NVMe/ext4, 3.6 TiB total, ~1.3-1.4 TiB available; core BMS models/results live here
/root filesystem:      TEAM 2TB NVMe/ext4, ~1.8 TiB total, ~277 GiB available at measurement time; Christian can free more if needed
/media/...21C21...:    Crucial 2TB NVMe/NTFS, ~1.9 TiB total, ~1.5 TiB available at measurement time
nominal local SSD:     ~8 TB decimal across the installed SSD/NVMe devices, not just the root filesystem
swap:                  19 GiB total, ~8.8 GiB free at measurement time
```

Implications:
- `/dev/shm` is useful for small/medium DRAM-backed hot tile tests, but it is not enough for a 500 GB context store.
- For 500 GB-class effective context state, `/mnt/BioModStack` is still the obvious first SSD/NVMe spill/checkpoint tier because it is already the BMS/model/results drive and has >1 TiB free.
- The separate mounted 2TB NVMe has ~1.5 TiB free and can be considered an additional spill/cache tier if pathing/format semantics are acceptable.
- The root 2TB host SSD is not the preferred default cache tier while it only has ~277 GiB free, but Christian can free space if optimal placement requires it.
- DRAM should be treated as a configurable warm cache, not as a mandatory all-state reservoir.
- Linux page cache can help NVMe-backed tile stores, but BioModStack should still expose explicit cache roots and quotas.
- Do not hardcode `/dev/shm`; support configurable `dram_cache_root`, `primary_ssd_cache_root`, optional `secondary_ssd_cache_roots`, and per-tier quotas.

## Runtime contract

A true `tiled-context-worker-pool` run should have an explicit contract like this:

```json
{
  "execution_backend": "tiled-context-worker-pool",
  "logical_context_plan": {
    "plan_id": "user-or-system-named-plan",
    "sequence_length": 12000,
    "tile_shape": [512, 512],
    "grid_shape": [24, 24],
    "dtype": "bf16",
    "state_kinds": ["pair", "single", "masks", "phase_intermediates"],
    "phase_dag": "pairformer-v0-fake-kernel-or-real-op-v1"
  },
  "memory_tiers": {
    "vram_policy": "working-set-only",
    "dram_cache_root": "/dev/shm/bms_context_cache",
    "dram_quota_gb": 48,
    "ssd_cache_root": "/mnt/BioModStack/bms_context_cache",
    "ssd_quota_gb": 800,
    "eviction_policy": "lru-with-phase-pinning"
  },
  "worker_resources": [
    {"gpu_id": 0, "max_vram_gb": 28, "max_concurrent_tiles": 1, "weight": 2.0},
    {"gpu_id": 2, "max_vram_gb": 20, "max_concurrent_tiles": 1, "weight": 1.0},
    {"gpu_id": 3, "max_vram_gb": 20, "max_concurrent_tiles": 1, "weight": 1.0}
  ],
  "scheduler_policy": {
    "gpu_count_is_concurrency_only": true,
    "fail_on_missing_tile": true,
    "lease_seconds": 120,
    "retry_limit": 1,
    "barrier_timeout_seconds": 1800
  }
}
```

This is only a schema sketch, not final API syntax.

The important part is that the user/runtime plan declares the logical tile grid and memory tiers directly. BioModStack may offer an estimator, but it must not silently replace the user’s logical plan with a GPU-count-derived plan.

## What gets sharded / streamed

The thing to shard is not the biology into disconnected mini-folds.

The thing to shard is the global model state:
- pair/context tensor tiles, e.g. `z[row_tile, col_tile, channels]`
- single/token state slices, e.g. `s[row_tile, channels]`
- masks/features required by each operation
- per-layer / per-recycling intermediates
- phase-local temporary tiles
- final gathered outputs only after global state completion

A worker job should not mean “fold this local protein fragment.”

A worker job should mean:

```text
claim tile/phase work item -> load required global-state tiles -> run one model-state update -> write updated global-state tile -> release/advance barrier
```

## Memory-tier model

### VRAM

VRAM is the hot working set only.

A GPU worker should hold:
- the model/kernel code and required weights
- one or a small number of active input tiles
- required neighbor/row/column tiles for the current operation
- temporary buffers
- one active output tile or chunk

VRAM should not be required to hold the whole pair/context map.

### DRAM

DRAM is the warm cache.

Use cases:
- active phase tiles likely to be reused soon
- row/column stripe buffers
- barrier-ready tile sets
- short-lived temporary arrays where NVMe latency would dominate

On this host, true tmpfs capacity is about 63 GiB at `/dev/shm`, while total available RAM at measurement was about 90 GiB. That is useful, but not enough for a 500 GB full-state cache.

### SSD/NVMe

NVMe is the cold persistent tile store and checkpoint tier.

Use cases:
- full global pair/context state
- phase checkpoints
- recovery after worker failure
- very large complexes where state is much larger than DRAM

On this host, `/mnt/BioModStack` has enough free space for a 500 GB-class tile store, but performance will depend heavily on tile size, write amplification, compression, and access pattern.

## Tile-store requirements

A real tile store needs more than `.npz` slices after a serial prediction.

Required primitives:
- manifest for global plan, tile grid, dtype, shape, memory estimates, and backend version
- per-tile metadata: shape, dtype, state kind, phase, layer/recycle index, checksum, storage tier, status
- atomic tile writes: write temp -> fsync/close -> rename -> marker
- tile leases: pending/running/complete/failed/stale
- barrier records per phase/layer
- cache promotion/demotion between SSD and DRAM
- shape validation on every read
- checksum or content-hash validation on every completed tile
- resumability from phase checkpoints
- clear fatal vs retryable failure categories

Possible storage formats to evaluate:
- Zarr or chunked NPY-like layout for random tile access
- safetensors shards for contiguous tensor groups
- raw mmap files with sidecar metadata for maximum control
- compressed chunks only after correctness, not in v0

Do not start with a pile of independent `.npz` files as the long-term format if we expect hundreds of GB and many phase rewrites.

## Scheduler semantics

BioModStack scheduler should become the authority for worker dispatch.

Current scheduler concepts that can be reused:
- selected/pinned GPU IDs
- per-GPU capability flags
- per-GPU thresholds / safety margins
- max concurrent jobs
- parent/child job lineage
- batch/job status reporting

Missing objects that must be added for this backend:
- parent-level context-plan reservation
- per-device worker resource object: `{gpu_id, max_vram_mb, max_concurrent_tiles, weight}`
- per-tier memory reservation: `{tier, root, quota_bytes}`
- tile/phase lease table
- barrier table
- stale worker detection
- whole-plan cancellation / failure propagation
- live attribution: which GPU is working on which phase/tile

Important: selected GPUs are worker resources, not the definition of the context map.

## User-facing controls

The UI/API should expose this as user-defined runtime planning, not magical auto-detect.

Recommended controls:
- execution backend:
  - `shared-cache-serial-output-tiling` for debug/publishing only
  - `native-fold-cp-square-mesh` for immediate true-CP proof
  - `tiled-context-worker-pool` for out-of-core user-defined context runtime
- logical context plan:
  - tile size or grid size
  - estimated state size
  - dtype / precision
  - phase/kernel mode: fake-kernel, one-real-op, full experimental
- memory tiers:
  - DRAM cache root and quota
  - SSD cache root and quota
  - eviction policy
- worker resources:
  - selected GPU IDs
  - per-GPU VRAM cap
  - per-GPU worker concurrency
  - optional worker weights
- validation mode:
  - dry-run estimate only
  - run fake-kernel equivalence test
  - run real-op experimental path

BioModStack may provide an “estimate/recommend” button, but final launch should show the resolved plan and require explicit user acceptance.

## Admission / preflight

Before launching, BioModStack should estimate:
- token count and pair/context dimensions
- tile count and tile byte size
- number of state copies needed per phase
- temporary buffer overhead
- checkpoint overhead
- DRAM quota needed for hot/warm working set
- SSD quota needed for complete state + checkpoint safety factor
- per-GPU VRAM needed for one tile work item

Fail before launch if:
- SSD quota is below required state/checkpoint estimate
- DRAM quota is below configured minimum for chosen tile size
- any selected GPU cap is below one tile work item plus model/temp overhead
- tile size would produce too many tiny work items for scheduler overhead
- user selected native Fold-CP semantics but the plan is not a valid square mesh

For 500 GB-class state, expect SSD/NVMe to be mandatory on this host.

## Execution flow

1. Plan build
   - parse input target
   - estimate context-state size
   - resolve user-defined logical tile grid
   - resolve memory-tier roots and quotas
   - resolve worker resource caps
   - write immutable `context_plan_manifest.json`

2. Store init
   - create tile-store directory
   - write metadata DB / manifest
   - allocate or lazily create tile containers
   - create phase/barrier tables

3. Feature/init phase
   - build initial single/pair state tiles
   - write state tiles to DRAM/SSD according to cache policy
   - validate all required initial tiles exist

4. Worker loop
   - scheduler creates tile/phase work items
   - child worker claims lease
   - worker stages tiles from SSD -> DRAM -> VRAM as needed
   - worker runs update kernel
   - worker writes output tile atomically
   - worker records metrics and releases lease

5. Barrier
   - coordinator verifies phase completion
   - promotes/demotes cache tiers for next phase
   - advances DAG to next phase

6. Finalization
   - gather only after global state is complete
   - publish final structure/confidence/artifacts
   - retain enough state metadata to reproduce/debug

## Development order

### Phase 0 — Contract split

Stop calling current shared-cache serial tiling true CP.

Add explicit backends:
- `shared-cache-serial-output-tiling`
- `native-fold-cp-square-mesh`
- `tiled-context-worker-pool`

True CP requests must fail closed if they select the shared-cache serial backend.

### Phase 1 — User-defined plan schema + dry-run estimator

Implement plan schema and dry-run estimates without running Boltz math.

Touchpoints:
- `platform/api/config/models/boltz_cp_experimental.yaml`
- `platform/api/services/boltz_cp_shard_plans.py`
- `platform/api/services/nextflow.py`
- frontend structure-prediction settings surfaces
- new API tests for user-defined plan preservation

Acceptance:
- user-provided tile/memory/worker plan survives normalization unchanged
- API may reject invalid plans, but must not silently replace the logical plan based on GPU count

### Phase 2 — Tile-store v0 + fake-kernel runtime

Build a deterministic fake state engine before porting Boltz kernels.

Touchpoints in Fold-CP fork:
- `src/boltz/distributed/large_protein/plan.py`
- `src/boltz/distributed/large_protein/runtime.py`
- `src/boltz/distributed/large_protein/tile_store.py`
- `src/boltz/distributed/large_protein/worker.py`
- new `src/boltz/distributed/large_protein/context_store.py`
- new regression tests for 1-worker vs N-worker equivalence

Acceptance:
- fake global `N x N x C` state is updated through row/column phases
- same final state from 1 GPU worker and multiple GPU workers
- same logical plan can run with different worker counts
- worker failure can be retried or fails the whole plan coherently

### Phase 3 — BioModStack scheduler integration

Make BioModStack schedule tile work, not just child output bundles.

Touchpoints:
- `scripts/spawn_boltz_cp_children.py`
- `modules/boltz_cp_experimental.nf`
- `workflows/boltz_cp_experimental.nf`
- `platform/api/services/nextflow.py`
- scheduler/gpu-lock surfaces used by structure prediction jobs
- job status API / frontend progress display

Acceptance:
- parent job creates a tile work queue
- children claim work from shared store
- GPU count controls number of workers only
- UI shows active phase/tile/GPU attribution

### Phase 4 — Port one real Fold-CP operation

Do not port full Boltz2 first.

Candidate sequence:
1. initial pair/single state tiling
2. one triangle multiplication or triangular attention update
3. tiny-input equivalence against native/serial reference
4. then expand across pairformer blocks/recycling

Acceptance:
- one real operation reads/writes tile-store state
- result matches reference within tolerance for tiny inputs
- memory profile shows state is not fully resident in VRAM

### Phase 5 — Full experimental out-of-core path

Only after fake-kernel and one-real-op proofs:
- expand operation DAG
- add checkpoint/restart
- optimize tile sizes
- add DRAM/NVMe cache promotion
- evaluate compression/mmap/Zarr/safetensors options
- harden telemetry and failure handling

## Acceptance criteria for Christian’s actual goal

A run counts as aligned with this goal only if:

1. The user-defined logical context plan is preserved and displayed.
2. GPU discovery does not define the logical shard geometry.
3. GPU count affects concurrency/throughput only.
4. Pair/context state is persisted outside VRAM during model-state computation.
5. At least one worker can stage a subset of global state into VRAM, update it, and write it back.
6. The same logical plan can run with fewer workers, just slower.
7. DRAM/SSD tier usage is explicit in metadata.
8. A serial-VRAM-impossible state can complete a fake-kernel or real-op test using DRAM/SSD as backing store.
9. Publication occurs only after the global state has completed all required phases.
10. Metadata distinguishes this backend from native square-mesh CP and shared-cache serial output tiling.

## Important warning

This is a substantial runtime project, not a flag flip on Fold-CP.

Native Fold-CP already contains valuable distributed math patterns, especially blockwise/ring accumulation in triangular attention/multiplication. Those are good references. But Christian’s target requires changing the state-residency model so the global context state can live in DRAM/NVMe and be streamed through GPU workers under BioModStack scheduling.

That is closer to an out-of-core tiled tensor execution engine for Boltz-style pairformer computation than to ordinary multi-GPU inference launch plumbing.
