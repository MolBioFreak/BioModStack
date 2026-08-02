# DRAM→VRAM tiled runtime proof plan for heterogeneous Fold-CP/Boltz2

Date: 2026-04-24

Status: active engineering pivot. SSD/NVMe is out of the active-state path for now. NVMe may remain useful for checkpoints/results, but the proof target is DRAM-backed global state with GPU workers staging tiles into VRAM.

## Decision

The BioModStack large-protein/Fold-CP work should now focus on proving a DRAM→VRAM tiled worker runtime, not on trying to make native Fold-CP's equal square GPU mesh fit this mixed workstation by default.

The first acceptable proof can be single-GPU out-of-core execution: the RTX 5090 is the main workhorse, while live pair/context state spills to and from DRAM through bounded tile windows. The rest of the logical context does not need to fit in VRAM at once. Multi-GPU heterogeneous scheduling remains important, but it is an extension of the same state-store/scheduler model, not a prerequisite for proving the core mechanism.

Logical sharding is allowed to follow the math rather than the hardware. If correctness or memory geometry requires breaking the context into 4, 16, 64, or another explicit shard/tile count, that is acceptable. The key rule is that shard count describes the logical decomposition of global state, while worker count describes how many CUDA executors are currently chewing through that decomposition.

Reasons:

- Current Fold-CP is true synchronous context-parallel execution, but it assumes a fixed square mesh with equal-rank participation.
- This workstation is heterogeneous: RTX 5090, 2x RTX 3090, RTX 5060 Ti.
- Local topology has no NVLink and no usable CUDA P2P pairs.
- NCCL probes showed same-node traffic using SHM transport rather than direct CUDA peer memory.
- A single-GPU out-of-core tiled path is sufficient to prove the key missing property: live global model state can exceed VRAM while only the active window is staged through CUDA.
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

## Architecture-preservation guardrails

The DRAM workhorse path must preserve Fold-CP/Boltz runtime semantics. It must not become an ad hoc chunking system that merely processes independent fragments and stitches outputs together.

Required constraints:

- Use the existing context-parallel decomposition as the mathematical reference: pair/context state remains one live global state with explicit row/column tile ownership, phase ordering, barriers, and accumulation semantics.
- Treat DRAM spillover as a memory-tier substitution for unavailable VRAM residency, not as a different inference algorithm.
- A tile/window is an execution window over live state, not an independent prediction and not a post-hoc output slice.
- Shard/tile geometry must preserve global pairformer dependencies. If an operation needs rows, columns, K/V blocks, bias/mask chunks, ring-style accumulation, or online-softmax state, those dependencies must be represented in the phase DAG and tile-store metadata.
- The single-5090 path is allowed to serialize work that native Fold-CP would distribute, but it must serialize the same algebra with the same live-context dependencies.
- Any fake-kernel proof is only a contract scaffold. Before claiming architectural fidelity, port one real Fold-CP operation using the same dependency structure and compare against the native/full reference.

Failure modes to reject:

- splitting the protein into independent mini-runs;
- running serial Boltz once and slicing artifacts afterward;
- updating tiles without the required row/column/global context dependencies;
- hiding stale-state or phase-order violations behind scheduler metadata;
- treating 4/16/etc. shards as output packaging rather than live-state decomposition.

## Non-goals for this tranche

- Do not use SSD/NVMe as the active intermediary for every tile update.
- Do not pretend output slicing after one serial prediction is distributed CP.
- Do not depend on direct GPU↔GPU peer memory.
- Do not start with ragged mathematical tile geometry. Use equal logical tiles first; express heterogeneity through dispatch cadence and per-GPU caps.
- Do not equate shard count with GPU count. A 16-shard logical plan may run sequentially on one 5090, concurrently on several GPUs, or in some hybrid cadence; the math and memory plan choose shard geometry, not hardware enumeration.

## What must be shown

A claim that DRAM intermediary works requires evidence at four levels:

0. Single-GPU out-of-core viability
   - a logical state larger than the active VRAM window can live in DRAM;
   - one GPU can repeatedly stage tile windows, compute, and write back without ever requiring the full live state in VRAM;
   - the proof does not depend on multiple GPUs, NCCL, or P2P.

1. Transport feasibility
   - selected GPUs can stream pinned DRAM tiles into VRAM and back;
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
   - the same logical plan runs with different worker counts, only changing speed;
   - fake-kernel metadata proves phase ordering, tile ownership, and writeback lifecycle, but is not used as evidence that Boltz math is preserved.

4. Real Fold-CP operation proof
   - port exactly one real operation first, preferably triangle multiplication before full attention;
   - preserve the original operation's dependency structure rather than replacing it with independent chunks;
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
- Follow-up PCIe checks show the 3090 result is not mainly a benchmark artifact: under load both 3090s negotiate PCIe Gen4 x4, not x8.
- Gen4 x4 raw bandwidth is ~7.88 GB/s per direction; 85-90% practical payload is ~6.7-7.1 GB/s, matching the measured ~6.7 GB/s.
- If the MCIO splitter is expected to provide x8/x8 to the 3090s, the hardware/BIOS/riser path is not currently delivering that to Linux/NVIDIA.
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

### Phase 1: make the probe a real single-GPU DRAM tile-store micro-runtime

Start with one GPU, preferably torch ordinal 0 / RTX 5090, because this isolates the fundamental mechanism from multi-GPU scheduling noise. The success criterion is not speedup; it is that the live logical state is DRAM-resident and only active windows enter VRAM.

Add:

- DRAM tile root under tmpfs, anonymous shared memory, or pinned host buffers.
- tile manifest: `tile_id`, `row_range`, `col_range`, `phase`, `version`, `dtype`, `shape`, `bytes`, `checksum`.
- one-GPU lease loop with explicit `load -> compute -> writeback -> release` lifecycle.
- one deterministic fake update, e.g. `tile = alpha * tile + beta * row_bias + gamma * col_bias`.
- reference full-state implementation for tiny N.
- tests proving one-worker tiled outputs equal reference and that peak device allocation stays below full-state size.

Avoid transfer-bound toy behavior by making lease granularity configurable:

- support larger tile windows / batches per lease;
- support multiple compute iterations while a tile/window is resident;
- report compute time vs H2D/D2H time separately;
- treat high transfer fraction as a scheduler failure signal, not as proof the architecture is invalid.

Acceptance:

```text
same logical plan + 1 GPU worker == reference
state lives in DRAM between leases
full logical state is never allocated in VRAM
logical shard/tile count is independent of GPU count
4-shard and 16-shard tiny plans both pass reference equivalence where memory geometry permits
compute/H2D/D2H timing is reported per lease
```

### Phase 1b: extend the same runtime to multiple workers only after single-GPU correctness

Acceptance:

```text
same logical plan + 2 workers == reference
same logical plan + 4 workers == reference
GPU count changes cadence only, not logical tile geometry
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

### Phase 3: port one Fold-CP operation without changing its dependency semantics

Start with triangle multiplication because it is accumulation-friendly, but do not turn it into an unrelated chunk loop. The DRAM tile-store version must preserve the existing Fold-CP operation's row/column dependency pattern and accumulation semantics.

Required:

- native tiny reference using the inspected Fold-CP math pattern;
- DRAM-tiled local implementation with explicit phase/dependency metadata;
- compare within tolerance;
- prove the same logical operation can run sequentially on one workhorse GPU while preserving live-context dependencies;
- then run with multiple GPU workers only after the one-workhorse version is correct.

Only after this works should we port triangular attention with its K/V/bias/mask chunk dependencies and online softmax state.

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

## PCIe root-cause check for 3090 bandwidth

Christian correctly flagged that ~6.6 GB/s is too slow for an expected PCIe Gen4 x8 path. Follow-up diagnostics show the 3090s are not currently running as x8 links.

Idle `nvidia-smi` query:

```text
nvidia-smi index 2, RTX 3090, bus E1:00.0: current Gen1 x4, max Gen4 x16, P8
nvidia-smi index 3, RTX 3090, bus E2:00.0: current Gen1 x4, max Gen4 x16, P8
```

During a sustained copy load on torch CUDA ordinals 1 and 2:

```text
nvidia-smi index 2, RTX 3090, bus E1:00.0: current Gen4 x4, max Gen4 x16, P2
nvidia-smi index 3, RTX 3090, bus E2:00.0: current Gen4 x4, max Gen4 x16, P2
```

Linux sysfs confirms the upstream root ports feeding those two 3090s are x4 ports, not x8 ports:

```text
/sys/bus/pci/devices/0000:e0:01.1/max_link_width = 4  -> downstream bus E1 RTX 3090
/sys/bus/pci/devices/0000:e0:01.4/max_link_width = 4  -> downstream bus E2 RTX 3090
/sys/bus/pci/devices/0000:e1:00.0/current_link_width = 4
/sys/bus/pci/devices/0000:e2:00.0/current_link_width = 4
```

Torch CUDA ordinal mapping from the Fold-CP uv environment:

```text
torch ordinal 0: RTX 5090, bus 01:00.0
torch ordinal 1: RTX 3090, bus E1:00.0
torch ordinal 2: RTX 3090, bus E2:00.0
torch ordinal 3: RTX 5060 Ti, bus C1:00.0
```

Note: `nvidia-smi` index 1 is the RTX 5060 Ti; torch ordinal 1 is a RTX 3090. Use torch ordinals for PyTorch worker assignment.

Longer per-direction pinned-memory copy probe with 512 MiB tiles, 16 iterations:

```text
Torch GPU0 RTX 5090:
  H2D 57.85 GB/s, D2H 58.02 GB/s, roundtrip 57.91 GB/s
Torch GPU1 RTX 3090:
  H2D 6.73 GB/s, D2H 6.77 GB/s, roundtrip 6.75 GB/s
Torch GPU2 RTX 3090:
  H2D 6.73 GB/s, D2H 6.77 GB/s, roundtrip 6.75 GB/s
Torch GPU3 RTX 5060 Ti:
  H2D 28.79 GB/s, D2H 29.01 GB/s, roundtrip 28.89 GB/s
```

Bandwidth math:

```text
PCIe Gen4 x4 raw per direction:  7.88 GB/s; 85-90% practical:  6.70-7.09 GB/s
PCIe Gen4 x8 raw per direction: 15.75 GB/s; 85-90% practical: 13.39-14.18 GB/s
PCIe Gen5 x8 raw per direction: 31.51 GB/s; 85-90% practical: 26.78-28.36 GB/s
PCIe Gen5 x16 raw per direction: 63.02 GB/s; 85-90% practical: 53.56-56.71 GB/s
```

Conclusion: the measured 3090 bandwidth is exactly what Gen4 x4 would predict. If the MCIO/riser path is expected to bifurcate as x8/x8, the platform is currently exposing the two 3090 downstream root ports as x4/x4 to Linux. Check BIOS PCIe bifurcation/slot settings, MCIO cable/riser wiring, and which motherboard lanes the splitter actually feeds. For runtime planning, treat the 3090s as Gen4 x4 devices until hardware/BIOS diagnostics prove otherwise.

Important nuance: Gen4 x4 is not automatically a problem for using a RTX 3090. It is often enough for VRAM-resident training/inference, batched work, and workloads that move data once then reuse it heavily. It becomes a runtime concern specifically for this DRAM-backed active-state design if tiles are ping-ponged between DRAM and VRAM at high cadence. Therefore the scheduler should not exclude the 3090s; it should give them longer-residency, higher-compute-per-byte leases and avoid equal barrier cadence with faster-copy GPUs.

- Do not hard-code 1x1/2x2/4x4 as hardware launch meanings. Treat them as logical grid presets only; internally they may correspond to 1, 4, 16, or more state tiles executed by one main workhorse GPU or by a later worker pool.

## First executable context-spill micro-runtime

After the 5090/main-workhorse pivot, the probe script now also contains a torch-free single-GPU context-spill simulation seam:

```text
fake_pair_state()
fake_reference_pair_update()
run_single_gpu_context_spill_simulation()
```

This is not real Boltz math and not a CUDA benchmark. It is the first executable contract test for the intended runtime shape:

```text
DRAM state -> load active tile/window -> compute -> writeback -> release -> next tile/window
```

Validated behavior:

- 4 logical shards and 16 logical shards both match the same full-state reference on one worker.
- manifest records `backend: single-gpu-dram-context-spill-sim`.
- manifest records `state_residency: dram_between_leases`.
- manifest records `full_state_allocated_in_vram: false`.
- manifest records peak active window bytes separately from full logical state bytes.
- lease lifecycle is explicit: `load`, `compute`, `writeback`, `release`.

Verification:

```bash
cd /home/dalab/biomodstack/biomodstack
python3 -m pytest scripts/test_dram_vram_tile_probe.py -q
python3 -m py_compile scripts/dram_vram_tile_probe.py scripts/test_dram_vram_tile_probe.py
```

Result:

```text
6 passed
```

A sample 16-shard simulation summary was written to:

```text
/tmp/single_gpu_context_spill_sim_16shards.json
```

Important caveat: this is still a fake local kernel. The next required step is to make the same contract use real pinned host buffers and one real CUDA-resident work window on torch ordinal 0 / RTX 5090, with timing split into load/compute/writeback.

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
