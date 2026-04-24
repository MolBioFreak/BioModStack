# DRAM→VRAM tiled runtime proof plan for heterogeneous Fold-CP/Boltz2

Date: 2026-04-24

Status: active engineering pivot. SSD/NVMe is out of the active-state path for now. NVMe may remain useful for checkpoints/results, but the proof target is DRAM-backed global state with GPU workers staging tiles into VRAM.

## Decision

The BioModStack large-protein/Fold-CP work should now focus on proving a DRAM→VRAM tiled worker runtime, not on trying to make native Fold-CP's equal square GPU mesh fit this mixed workstation by default.

Reasons:

- Current Fold-CP is true synchronous context-parallel execution, but it assumes a fixed square mesh with equal-rank participation.
- This workstation is heterogeneous: RTX 5090, 2x RTX 3090, RTX 5060 Ti.
- Local topology has no NVLink and no usable CUDA P2P pairs.
- NCCL probes showed same-node traffic using SHM transport rather than direct CUDA peer memory.
- Therefore the project needs explicit state ownership and scheduling, not blind `torchrun size_cp` derivation.

## Target architecture

```text
user-defined logical tile plan
        ↓
DRAM-resident global pair/context state
        ↓
BioModStack scheduler leases phase/tile work
        ↓
GPU workers copy tile windows DRAM→VRAM, compute, copy updates VRAM→DRAM
        ↓
barriers/versioning advance global state phase by phase
        ↓
final assembly/publication after global state completion
```

This is not independent mini-folding. The state being tiled is the live global pair/context state.

## Non-goals for this tranche

- Do not use SSD/NVMe as the active intermediary for every tile update.
- Do not pretend output slicing after one serial prediction is distributed CP.
- Do not depend on direct GPU↔GPU peer memory.
- Do not start with ragged mathematical tile geometry. Use equal logical tiles first; express heterogeneity through dispatch cadence and per-GPU caps.

## What must be shown

A claim that DRAM intermediary works requires evidence at four levels:

1. Transport feasibility
   - multiple selected GPUs can stream pinned DRAM tiles into VRAM and back concurrently;
   - measured per-GPU and aggregate bandwidth is recorded;
   - weak links/cards are visible, not hidden.

2. Persistent DRAM tile-store feasibility
   - global tile state exists outside VRAM between worker leases;
   - workers load only their assigned tile/window into VRAM;
   - workers write updated state back to DRAM;
   - metadata records tile version, phase, worker, bytes, and elapsed time.

3. Deterministic fake-kernel correctness
   - tiny full-state reference implementation exists;
   - tiled DRAM runtime produces the same result with one worker;
   - tiled DRAM runtime produces the same result with multiple workers;
   - the same logical plan runs with different worker counts, only changing speed.

4. Real Fold-CP operation proof
   - port exactly one real operation first, preferably triangle multiplication before full attention;
   - compare against tiny native/reference output within tolerance;
   - only after that attempt a larger pairformer slice.

## First artifact created

Script:

```text
scripts/dram_vram_tile_probe.py
```

Tests:

```text
scripts/test_dram_vram_tile_probe.py
```

Verified:

```bash
python3 -m pytest scripts/test_dram_vram_tile_probe.py -q
python3 -m py_compile scripts/dram_vram_tile_probe.py scripts/test_dram_vram_tile_probe.py
```

Result:

```text
4 passed
```

The script currently provides:

- `plan`: tile-count and weighted GPU assignment estimate.
- `probe`: concurrent pinned-DRAM→VRAM→pinned-DRAM copy/update/copyback probe using PyTorch CUDA multiprocessing.

Important caveat: this first probe proves transfer mechanics and heterogeneity visibility. It is not yet a persistent shared DRAM tile store and not yet Boltz math.

## First local measurements

Environment: Fold-CP uv environment with PyTorch/CUDA/NCCL available.

Command shape:

```bash
uv run python /home/dalab/biomodstack/biomodstack/scripts/dram_vram_tile_probe.py probe \
  --gpus 0,1,2,3 \
  --tile-mb 256 \
  --iterations 6 \
  --warmup 2 \
  --output /tmp/dram_vram_probe_4gpu_256mb.json
```

### 256 MiB tile, GPU0 only

```text
GPU0 RTX 5090:
  effective transfer GB/s: 53.56
  per iteration: 256 MiB H2D + 256 MiB D2H + tiny fake update
```

### 256 MiB tile, GPU1+GPU2 only

```text
GPU1 RTX 3090: 6.64 GB/s
GPU2 RTX 3090: 6.64 GB/s
Aggregate worker-window GB/s: 13.29
```

### 256 MiB tile, all four GPUs

```text
GPU0 RTX 5090:     47.61 GB/s
GPU1 RTX 3090:      6.64 GB/s
GPU2 RTX 3090:      6.64 GB/s
GPU3 RTX 5060 Ti:  25.85 GB/s
Aggregate worker-window GB/s: 26.54
```

Interpretation:

- Concurrent DRAM↔VRAM streaming works.
- The machine is highly heterogeneous in effective copy behavior.
- The 3090 path is much slower than the 5090 and 5060 Ti in this simple probe, so weighted scheduling is mandatory.
- The all-GPU aggregate is bounded by the slow workers if every phase has a global barrier; work assignment must avoid forcing equal tile counts at equal phase cadence.

## Planning example

Command:

```bash
uv run python scripts/dram_vram_tile_probe.py plan \
  --sequence-length 8192 \
  --tile-tokens 512 \
  --channels 128 \
  --state-copies 2 \
  --gpus 0,1,2,3
```

Output summary:

```text
8192 tokens, 512-token tiles -> 16x16 = 256 tiles
Estimated tile state: 128 MiB per tile for [512,512,128] fp16 with 2 state copies
First-pass VRAM-capacity weighted assignment:
  GPU0 RTX 5090:     96 tiles
  GPU1 RTX 3090:     60 tiles
  GPU2 RTX 3090:     60 tiles
  GPU3 RTX 5060 Ti:  40 tiles
```

This is only a first-pass assignment. Real scheduling should use measured bandwidth, available VRAM cap, and per-operation compute intensity, not VRAM capacity alone.

## Next implementation tranche

### Phase 1: make the probe a real DRAM tile-store micro-runtime

Add:

- DRAM tile root under tmpfs or anonymous shared memory.
- tile manifest: `tile_id`, `row_range`, `col_range`, `phase`, `version`, `dtype`, `shape`, `bytes`, `checksum`.
- worker lease table.
- one deterministic fake update, e.g. `tile = alpha * tile + beta * row_bias + gamma * col_bias`.
- reference full-state implementation for tiny N.
- tests proving one-worker and multi-worker tiled outputs equal reference.

Acceptance:

```text
same logical plan + 1 worker == reference
same logical plan + 2 workers == reference
same logical plan + 4 workers == reference
state leaves VRAM after each lease and survives in DRAM tile store
```

### Phase 2: measure scheduling policy on this machine

Run matrix:

```text
GPU sets:
  0
  0,1
  0,2
  1,2
  0,1,2
  0,1,2,3

Tile sizes:
  64 MiB
  128 MiB
  256 MiB
  512 MiB if safe

Metrics:
  per-worker H2D GB/s
  per-worker D2H GB/s
  aggregate GB/s
  wall time excluding spawn
  worker idle time at barriers
  VRAM peak
  host RAM peak
```

Acceptance:

- derive an explicit default worker weight table for this host;
- identify whether including the 5060 Ti helps or hurts for different tile sizes;
- identify whether 3090s should receive fewer/larger/less frequent tile leases.

### Phase 3: port one Fold-CP operation

Start with triangle multiplication because it is accumulation-friendly:

- native tiny reference using the inspected Fold-CP math pattern;
- DRAM-tiled local implementation;
- compare within tolerance;
- then run with multiple GPU workers.

Only after this works should we port triangular attention with online softmax state.

### Phase 4: BioModStack scheduler integration

Expose a user-defined plan object:

```json
{
  "backend": "tiled-context-worker-pool-dram",
  "sequence_length": 8192,
  "tile_tokens": 512,
  "state_dtype": "float16",
  "state_channels": 128,
  "dram_quota_gb": 96,
  "workers": [
    {"gpu_id": 0, "max_vram_gb": 28, "weight": 4.0},
    {"gpu_id": 1, "max_vram_gb": 20, "weight": 1.0},
    {"gpu_id": 2, "max_vram_gb": 20, "weight": 1.0},
    {"gpu_id": 3, "max_vram_gb": 12, "weight": 2.0}
  ]
}
```

GPU count affects worker concurrency only. It must not redefine logical shard geometry.

## Honest current status

- Native Fold-CP communication facts are understood.
- This workstation lacks the high-bandwidth fabric native Fold-CP wants.
- First DRAM↔VRAM concurrent transfer probe exists and passes tests.
- First transfer measurements are positive but expose strong heterogeneity.
- The real proof is still ahead: persistent DRAM tile store + deterministic fake kernel + one real Fold-CP op.

## Current project priority

Highest priority now:

```text
DRAM-backed persistent tile store + deterministic fake-kernel equivalence tests
```

Not:

```text
SSD active-state offload
native Fold-CP square-mesh launch polishing
serial prediction output tiling
```
