# Fold-CP DRAM Context-Spill Workhorse Implementation Spec

> **For Hermes:** Use `test-driven-development` for each coding step. Use `subagent-driven-development` only after a phase is approved and tasks are converted into PR-sized slices.

**Date:** 2026-04-24

**Goal:** Turn the current BioModStack/Fold-CP large-protein scaffolding into an honest DRAM-backed live-context execution path, starting with one main workhorse GPU (torch ordinal 0 / RTX 5090) while preserving Fold-CP/Boltz context-parallel math semantics.

**Architecture:** The logical pair/context state remains one live global state. DRAM is the backing tier for state that does not fit in VRAM. The 5090 stages active windows into VRAM, runs math, writes back, and advances through explicit phase/barrier/version semantics. Logical shard count follows math/memory geometry, not GPU count.

**Tech stack:** Python, PyTorch/CUDA, Fold-CP/Boltz distributed code, BioModStack Nextflow/API integration, pytest regression tests, filesystem-backed DRAM tile store using `/dev/shm` or configured RAM-backed roots for early proofs.

**Current status summary:**

- BioModStack has a torch-free DRAM/context-spill simulation seam in `scripts/dram_vram_tile_probe.py`.
- That seam proves 4-shard and 16-shard logical plans can run on one worker and match a tiny full-state fake reference.
- The modified Fold-CP repo at `/home/dalab/tmp/boltz-cp` has large-protein plan/store/worker scaffolding under `src/boltz/distributed/large_protein/`.
- The current shared-cache path runs one serial `boltz.main predict` and then publishes sliced artifacts. That remains useful control-plane/output-publication scaffolding, but it is not live-context CP execution.
- Native Fold-CP math should remain the reference. DRAM spillover is a memory-tier substitution, not an alternative chunked inference algorithm.

**Implementation progress on 2026-04-24:**

- Phase 0 backend truth labels started in `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`.
- Added fail-closed selection for `bcp_backend=dram-context-spill-workhorse`; it raises `NotImplementedError` and records a bundle failure instead of falling back to shared-cache serial output tiling.
- Added explicit `metadata-only` backend routing so tests/debug paths can bypass shared prediction even when `input_path` is present.
- Added Fold-CP-local fake context-spill contract in `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/context_spill.py`.
- Added regression tests in `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_context_spill.py` proving 4-shard and 16-shard logical plans match a full fake reference on one workhorse and record DRAM residency/lifecycle metadata.
- Verification passed:
  - `uv run --extra test python -m pytest regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_runtime.py regression_tests/test_large_protein_worker.py regression_tests/test_large_protein_cli.py -q` → `35 passed`
  - `uv run --extra test python -m pytest regression_tests/test_boltz2_oom_failfast.py regression_tests/test_manager_subgroup_layout.py -q` → `4 passed, 14 warnings`
  - `uv run --extra test python -m py_compile src/boltz/distributed/large_protein/context_spill.py src/boltz/distributed/large_protein/worker.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_worker.py` → passed
- Phase 2 versioned DRAM tile-store primitives added in `context_spill.py` and tested by `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_context_store.py`.
- Phase 2 implemented:
  - state/tensor/tile manifest initialization;
  - `tile_versions.json`;
  - running/complete/failed lease records;
  - version increments only on writeback;
  - stale completed-lease writeback rejection;
  - barrier status blocking on pending/running/failed tiles.
- Phase 2 verification passed:
  - `uv run --extra test python -m pytest regression_tests/test_large_protein_context_store.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_runtime.py regression_tests/test_large_protein_worker.py regression_tests/test_large_protein_cli.py -q` → `39 passed`
  - `uv run --extra test python -m pytest regression_tests/test_boltz2_oom_failfast.py regression_tests/test_manager_subgroup_layout.py -q` → `4 passed, 14 warnings`
  - `uv run --extra test python -m py_compile src/boltz/distributed/large_protein/context_spill.py src/boltz/distributed/large_protein/worker.py regression_tests/test_large_protein_context_store.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_worker.py` → passed
- Phase 3 CUDA GPU0 fake live-state runner added in `context_spill.py` and tested by `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_context_spill_cuda.py`.
- Phase 3 implemented:
  - CUDA_VISIBLE_DEVICES=0 / `cuda:0` workhorse path;
  - pinned-host staging when available;
  - H2D copy, deterministic CUDA fake update, D2H writeback;
  - per-lease `h2d_s`, `compute_s`, `d2h_s`, `total_s`, device, window bytes, lifecycle, and version metadata;
  - 4-shard and 16-shard CUDA workhorse equivalence against the CPU fake reference.
- Phase 3 verification passed:
  - `CUDA_VISIBLE_DEVICES=0 uv run --extra test python -m pytest regression_tests/test_large_protein_context_spill_cuda.py regression_tests/test_large_protein_context_store.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_runtime.py regression_tests/test_large_protein_worker.py regression_tests/test_large_protein_cli.py -q` → `42 passed`
  - `uv run --extra test python -m pytest regression_tests/test_boltz2_oom_failfast.py regression_tests/test_manager_subgroup_layout.py -q` → `4 passed, 14 warnings`
  - `uv run --extra test python -m py_compile src/boltz/distributed/large_protein/context_spill.py src/boltz/distributed/large_protein/worker.py regression_tests/test_large_protein_context_spill_cuda.py regression_tests/test_large_protein_context_store.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_worker.py` → passed
- Phase 4 operation DAG metadata added in `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/phase_dag.py` and tested by `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_phase_dag.py`.
- Phase 4 implemented:
  - `triangle_mult_outgoing` phase records;
  - dependency axes `row`, `col`, `k_accumulation`;
  - per-output-tile dependencies over all required lhs `(i,k)` and rhs `(k,j)` tiles;
  - `can_execute_output_tile()` dependency-version gate;
  - `phase_barrier_status()` output-version barrier gate;
  - explicit `allow_serial_workhorse_execution: true`, meaning one 5090 may serialize the same dependency graph without erasing it.
- Phase 4 verification passed:
  - `CUDA_VISIBLE_DEVICES=0 uv run --extra test python -m pytest regression_tests/test_large_protein_phase_dag.py regression_tests/test_large_protein_context_spill_cuda.py regression_tests/test_large_protein_context_store.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_runtime.py regression_tests/test_large_protein_worker.py regression_tests/test_large_protein_cli.py -q` → `46 passed`
  - `uv run --extra test python -m pytest regression_tests/test_boltz2_oom_failfast.py regression_tests/test_manager_subgroup_layout.py -q` → `4 passed, 14 warnings`
  - `uv run --extra test python -m py_compile src/boltz/distributed/large_protein/phase_dag.py src/boltz/distributed/large_protein/context_spill.py src/boltz/distributed/large_protein/worker.py regression_tests/test_large_protein_phase_dag.py regression_tests/test_large_protein_context_spill_cuda.py regression_tests/test_large_protein_context_store.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_worker.py` → passed
- Phase 5 triangle-multiplication spill proof added in `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/ops/triangle_mult_spill.py` and tested by `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_triangle_mult_spill.py`.
- Phase 5 implemented:
  - deterministic `z[N,N,C]` tiny state;
  - full reference `einsum("ikc,kjc->ijc")`;
  - CPU tiled spill execution preserving per-output k accumulation;
  - CUDA workhorse execution on `cuda:0` preserving the same k-dependency loop;
  - 2x2 and 4x4 logical grids compared against full reference;
  - per-output-tile manifest records dependencies, k indices, accumulation steps, lifecycle, and CUDA telemetry for the workhorse path.
- Important caveat: this is a triangle-multiplication-shaped operation proof, not yet the full Boltz/Fold-CP layer with all projections/gates/norms. It proves the live-state DAG and k-accumulation execution pattern can be preserved through CPU/CUDA spill machinery.
- Phase 5 verification passed:
  - `CUDA_VISIBLE_DEVICES=0 uv run --extra test python -m pytest regression_tests/test_large_protein_triangle_mult_spill.py regression_tests/test_large_protein_phase_dag.py regression_tests/test_large_protein_context_spill_cuda.py regression_tests/test_large_protein_context_store.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_runtime.py regression_tests/test_large_protein_worker.py regression_tests/test_large_protein_cli.py -q` → `50 passed`
  - `uv run --extra test python -m pytest regression_tests/test_boltz2_oom_failfast.py regression_tests/test_manager_subgroup_layout.py -q` → `4 passed, 14 warnings`
  - `uv run --extra test python -m py_compile src/boltz/distributed/large_protein/ops/triangle_mult_spill.py src/boltz/distributed/large_protein/phase_dag.py src/boltz/distributed/large_protein/context_spill.py src/boltz/distributed/large_protein/worker.py regression_tests/test_large_protein_triangle_mult_spill.py regression_tests/test_large_protein_phase_dag.py regression_tests/test_large_protein_context_spill_cuda.py regression_tests/test_large_protein_context_store.py regression_tests/test_large_protein_context_spill.py regression_tests/test_large_protein_worker.py` → passed

---

## Non-negotiable guardrails

The implementation must reject these failure modes:

- independent mini-runs on protein fragments;
- serial Boltz once followed by output slicing, when the selected backend claims true context execution;
- arbitrary chunk loops that do not preserve Fold-CP row/column/ring/attention dependencies;
- tile updates without explicit phase/version/barrier state;
- coupling logical shard count to GPU count;
- claiming success before one real Fold-CP operation matches a native/full tiny reference.

Acceptable simplification:

- The single-5090 path may serialize work that native Fold-CP would run across simultaneous ranks, as long as it serializes the same algebra and dependency graph.

Terminology:

- `shared-cache-serial-output-tiling`: current shared prediction + post-hoc publication path.
- `native-fold-cp-square-mesh`: existing synchronous Fold-CP CP mesh using torch distributed/DTensor/NCCL.
- `dram-context-spill-workhorse`: target first real out-of-core live-context backend using one 5090 and DRAM-backed global state.
- `tiled-context-worker-pool`: later heterogeneous multi-GPU extension of the same state-store/scheduler model.

---

## Phase 0 — Freeze truth labels and backend selection

**Objective:** Make it impossible for metadata/UI/API to confuse serial shared-cache tiling with DRAM-backed live-context execution.

**Files:**

- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`
- Modify: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_worker.py`
- Modify later: `/home/dalab/biomodstack/biomodstack/platform/api/services/nextflow.py`
- Modify later: `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`

### Task 0.1: Add explicit backend request/readback fields

**Test first:** add a test that initializes a plan with input metadata:

```json
{
  "job_id": "backend-truth",
  "sequence_length": 8,
  "bcp_backend": "dram-context-spill-workhorse"
}
```

Assert the plan manifest preserves `bcp_backend` and that worker result metadata echoes the selected backend.

**Implementation:** keep `bcp_backend` in `PlanManifest.input_metadata` and route executor selection through a small backend selector.

**Acceptance:**

```text
shared-cache requests report shared-cache-serial-output-tiling
metadata-only requests report metadata-only
DRAM workhorse requests do not silently fall back to shared-cache
```

### Task 0.2: Fail closed for unavailable true-context backend

Until the DRAM workhorse executor exists, `bcp_backend=dram-context-spill-workhorse` should raise an explicit `NotImplementedError` or feature-gated failure, not run shared-cache.

**Acceptance:**

```text
requesting DRAM workhorse cannot accidentally run serial shared-cache predict
failure artifact says backend unavailable / not implemented
```

---

## Phase 1 — Promote the fake context-spill contract into Fold-CP repo

**Objective:** Move the currently BioModStack-local fake contract into the Fold-CP large-protein test surface, because Fold-CP is where real operation ports must live.

**Files:**

- Create: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/context_spill.py`
- Create: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_context_spill.py`
- Keep reference/helper source: `/home/dalab/biomodstack/biomodstack/scripts/dram_vram_tile_probe.py`

### Task 1.1: Add torch-free state/tile primitives

Implement dataclasses/functions in `context_spill.py`:

```python
@dataclass(frozen=True)
class StateTensorSpec:
    name: str
    shape: tuple[int, ...]
    dtype: str
    residency: str  # "dram"

@dataclass(frozen=True)
class TileWindowSpec:
    tile_id: str
    row_range: tuple[int, int]
    col_range: tuple[int, int]
    version: int
    phase: str

@dataclass(frozen=True)
class LeaseRecord:
    lease_id: str
    tile_id: str
    phase: str
    lifecycle: tuple[str, ...]
    worker_id: str
    gpu_id: int | None
    h2d_s: float | None = None
    compute_s: float | None = None
    d2h_s: float | None = None
```

### Task 1.2: Add fake full-reference and tiled workhorse runner

Port the existing torch-free fake state/update equivalence into Fold-CP tests.

**Required tests:**

- `test_single_workhorse_four_shards_matches_reference`
- `test_single_workhorse_sixteen_shards_matches_reference`
- `test_logical_shard_count_is_independent_from_worker_count`
- `test_manifest_records_dram_residency_and_lifecycle`

**Acceptance command:**

```bash
cd /home/dalab/tmp/boltz-cp
uv run --extra test python -m pytest regression_tests/test_large_protein_context_spill.py -q
```

Expected:

```text
4 passed
```

---

## Phase 2 — Add DRAM tile-store v0 with versions, phases, and leases

**Objective:** Replace plain bundle markers with a real state-store contract for live pair/context state.

**Files:**

- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/tile_store.py`
- Create or modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/context_spill.py`
- Create: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_context_store.py`

### Required store layout additions

Add paths under `StoreLayout`:

```text
state/
  tensors/
    z/
    s/
  manifests/
    state_tensors.json
    tile_versions.json
  leases/
    pending/
    running/
    complete/
    failed/
  phases/
    phase_manifest.json
    barriers.json
```

### Required data model

Each tile/window record must include:

```json
{
  "tile_id": "r0000_c0001",
  "state_name": "z",
  "row_range": [0, 512],
  "col_range": [512, 1024],
  "shape": [512, 512, 128],
  "dtype": "float16",
  "version": 0,
  "phase": "init",
  "residency": "dram",
  "path": "state/tensors/z/r0000_c0001.npy"
}
```

### Tests

- create a store for N=8, tile_tokens=4, dtype=float32, channels=2;
- initialize all `z` tiles in DRAM;
- lease one tile to worker `workhorse-0`;
- mark lifecycle `load -> compute -> writeback -> release`;
- assert version increments only on writeback;
- assert stale writeback from older version fails;
- assert barrier cannot advance if any required tile is pending/running/failed.

**Acceptance command:**

```bash
cd /home/dalab/tmp/boltz-cp
uv run --extra test python -m pytest regression_tests/test_large_protein_context_store.py -q
```

---

## Phase 3 — Real pinned DRAM ↔ CUDA window runner on GPU0

**Objective:** Prove the same state-store contract can move a tile/window through pinned host memory into VRAM on the RTX 5090, compute a deterministic CUDA update, and write back.

**Files:**

- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/context_spill.py`
- Create: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_context_spill_cuda.py`
- Optional benchmark artifact writer: `/home/dalab/tmp/boltz-cp/scripts/context_spill_cuda_probe.py`

### Required runner API

```python
def run_cuda_workhorse_lease(
    *,
    store_root: str | Path,
    tile_id: str,
    state_name: str = "z",
    gpu_id: int = 0,
    compute_steps: int = 1,
    dtype: str = "float16",
) -> LeaseRecord:
    ...
```

### Required behavior

- Allocate/load tile from host-side backing store.
- Use pinned host tensor or pinned staging buffer when supported.
- Copy H2D to `cuda:0`.
- Run deterministic CUDA update with global row/col indices included in the math.
- Copy D2H.
- Write back to DRAM store.
- Record `h2d_s`, `compute_s`, `d2h_s`, `total_s`, bytes, GPU name, CUDA ordinal.
- Avoid allocating full logical state on GPU.

### Tests

CUDA tests must skip cleanly if CUDA is unavailable:

```python
pytest.importorskip("torch")
if not torch.cuda.is_available():
    pytest.skip("CUDA unavailable")
```

Required tests:

- 4-shard plan matches CPU reference after CUDA workhorse pass.
- 16-shard plan matches CPU reference after CUDA workhorse pass.
- manifest reports `peak_device_window_bytes < full_state_bytes`.
- telemetry fields are present and positive.

**Acceptance command:**

```bash
cd /home/dalab/tmp/boltz-cp
CUDA_VISIBLE_DEVICES=0 uv run --extra test python -m pytest regression_tests/test_large_protein_context_spill_cuda.py -q
```

---

## Phase 4 — Define operation DAG metadata from Fold-CP math

**Objective:** Before porting real math, encode the dependency structure that makes this live-context execution and not arbitrary chunking.

**Source references:**

- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/layers/triangular_mult.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/model/layers/triangular_attention.py`
- `/home/dalab/tmp/boltz-cp/src/boltz/distributed/utils.py`

### Required facts to preserve

Triangle multiplication:

- Fold-CP `_distributed_bmm` does row/column ring-style rotation.
- Each ring step computes a partial matmul and accumulates into `out`.
- Serialized DRAM workhorse execution must represent the same logical `k`-axis accumulation across row/column tile dependencies.

Triangular attention:

- K/V/bias/mask chunks are streamed/rotated.
- `tiled_softmax_attention_update` performs numerically stable online softmax accumulation.
- A future DRAM version must persist online-softmax intermediate state, not recompute/stitch independent attention fragments.

### Files

- Create: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/phase_dag.py`
- Create: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_phase_dag.py`

### Required phase records

```json
{
  "phase_id": "triangle_mult_outgoing",
  "op_kind": "triangle_multiplication",
  "inputs": ["z"],
  "outputs": ["z_update"],
  "dependency_axes": ["row", "col", "k_accumulation"],
  "barrier_after": true,
  "allow_serial_workhorse_execution": true
}
```

### Tests

- A triangle multiplication phase for a 4x4 logical grid creates per-output-tile dependencies over all required k tiles.
- A tile cannot execute until all declared input dependencies are present at the required version.
- Phase barrier cannot advance until every output tile has the expected version.

---

## Phase 5 — Port one real operation: triangle multiplication first

**Objective:** Implement the first architecture-preserving real math proof.

**Important:** This is not full Boltz prediction. This is one operation-level equivalence proof.

**Files:**

- Create: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/ops/triangle_mult_spill.py`
- Create: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/ops/__init__.py`
- Create: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_triangle_mult_spill.py`

### First operation shape

Start with a small operation-isolated tensor shape that captures block dependency semantics:

```text
z-like input: [N, N, C]
logical grid: 2x2 and 4x4
operation: output[i,j] = sum_k f(lhs[i,k], rhs[k,j])
```

Use torch CPU/full reference first, then CUDA workhorse tiled execution.

### Acceptance criteria

- 2x2 logical grid result matches full reference within tolerance.
- 4x4 logical grid result matches full reference within tolerance.
- the same logical grid can execute on one GPU in serialized k-accumulation order.
- metadata records k-dependencies, partial accumulation versions, and final output versions.
- no independent mini-run or output-slicing semantics appear in result metadata.

**Acceptance command:**

```bash
cd /home/dalab/tmp/boltz-cp
CUDA_VISIBLE_DEVICES=0 uv run --extra test python -m pytest regression_tests/test_large_protein_triangle_mult_spill.py -q
```

---

## Phase 6 — Only after triangle-mult proof, port triangular attention statefully

**Objective:** Preserve online-softmax semantics for streamed attention chunks.

**Files:**

- Create: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/ops/triangular_attention_spill.py`
- Create: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_triangular_attention_spill.py`

### Required persistent state

For each output tile/window, persist:

```text
running max/logits scale
running denominator/sumexp
running weighted value accumulator
output version
chunk index / completed dependency set
```

### Acceptance criteria

- tiny attention reference equals tiled online-softmax result within tolerance;
- K/V/bias/mask dependencies are explicit in DAG metadata;
- stale or missing chunk state fails closed;
- output is not assembled from independent attention mini-runs.

---

## Phase 7 — Add `dram-context-spill-workhorse` executor

**Objective:** Wire the proven DRAM workhorse operation path into `large_protein/worker.py` without disturbing legacy/shared-cache modes.

**Files:**

- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/worker.py`
- Modify: `/home/dalab/tmp/boltz-cp/src/boltz/distributed/large_protein/runtime.py`
- Modify: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_worker.py`
- Modify: `/home/dalab/tmp/boltz-cp/regression_tests/test_large_protein_cli.py`

### Required behavior

- `bcp_backend=shared-cache-serial-output-tiling` keeps old shared-cache behavior.
- `bcp_backend=metadata-only` keeps metadata-only behavior for tests.
- `bcp_backend=dram-context-spill-workhorse` calls the new context-spill executor.
- The executor writes results with:

```json
{
  "backend": "dram-context-spill-workhorse",
  "executor": "context-spill-workhorse",
  "state_residency": "dram_between_leases",
  "gpu_id": 0,
  "logical_tile_count": 16,
  "phase_count": 1,
  "real_ops_completed": ["triangle_multiplication"],
  "full_state_allocated_in_vram": false
}
```

### Acceptance criteria

- requesting DRAM backend no longer fails once feature is implemented;
- worker does not call `_ensure_shared_prediction()` for DRAM backend;
- failure in a lease records bundle failure;
- finalization distinguishes DRAM workhorse outputs from shared-cache publication outputs.

---

## Phase 8 — BioModStack integration behind an experimental backend flag

**Objective:** Expose the new backend without changing existing structure-prediction defaults.

**Files:**

- Modify: `/home/dalab/biomodstack/biomodstack/platform/api/services/nextflow.py`
- Modify: `/home/dalab/biomodstack/biomodstack/platform/api/tests/test_boltz_cp_experimental.py`
- Modify: `/home/dalab/biomodstack/biomodstack/modules/boltz_cp_experimental.nf`
- Modify: `/home/dalab/biomodstack/biomodstack/workflows/boltz_cp_experimental.nf`
- Modify: `/home/dalab/biomodstack/biomodstack/scripts/spawn_boltz_cp_children.py`

### Public/API contract

Add or preserve explicit fields:

```json
{
  "bcp_backend": "dram-context-spill-workhorse",
  "logical_grid_shape": [4, 4],
  "main_workhorse_gpu": 0,
  "dram_cache_root": "/dev/shm or configured path",
  "tile_tokens": 512,
  "state_dtype": "float16",
  "state_channels": 128,
  "compute_steps_per_residency": 1
}
```

Rules:

- `logical_grid_shape` chooses math/memory decomposition.
- `main_workhorse_gpu` chooses the initial executor.
- GPU count does not rewrite `logical_grid_shape`.
- `size_cp` remains legacy compatibility only, not public authority.

### Acceptance criteria

- API preserves user-defined logical grid/tile settings.
- Nextflow passes `bcp_backend` and workhorse settings into the Fold-CP large-protein CLI.
- UI/status metadata clearly says this is experimental operation-level/context-spill proof unless full prediction integration is later completed.

---

## Phase 9 — Verification matrix

Run these after each relevant phase.

### BioModStack local probe tests

```bash
cd /home/dalab/biomodstack/biomodstack
python3 -m pytest scripts/test_dram_vram_tile_probe.py -q
python3 -m py_compile scripts/dram_vram_tile_probe.py scripts/test_dram_vram_tile_probe.py
```

Expected current baseline:

```text
6 passed
```

### Fold-CP control-plane/runtime tests

```bash
cd /home/dalab/tmp/boltz-cp
uv run --extra test python -m pytest \
  regression_tests/test_large_protein_runtime.py \
  regression_tests/test_large_protein_worker.py \
  regression_tests/test_large_protein_cli.py \
  regression_tests/test_boltz2_oom_failfast.py \
  regression_tests/test_manager_subgroup_layout.py \
  -q
```

### New context-spill tests as they are added

```bash
cd /home/dalab/tmp/boltz-cp
uv run --extra test python -m pytest \
  regression_tests/test_large_protein_context_spill.py \
  regression_tests/test_large_protein_context_store.py \
  regression_tests/test_large_protein_phase_dag.py \
  -q
```

### CUDA workhorse tests

```bash
cd /home/dalab/tmp/boltz-cp
CUDA_VISIBLE_DEVICES=0 uv run --extra test python -m pytest \
  regression_tests/test_large_protein_context_spill_cuda.py \
  regression_tests/test_large_protein_triangle_mult_spill.py \
  -q
```

### BioModStack integration tests once backend flag is wired

```bash
cd /home/dalab/biomodstack/biomodstack/platform/api
uv run --group dev python -m pytest tests/test_boltz_cp_experimental.py -q
```

---

## Phase ordering recommendation

Do not jump straight to full Boltz prediction.

Recommended order:

1. Phase 0: backend truth/fail-closed semantics.
2. Phase 1: Fold-CP-local fake context-spill contract.
3. Phase 2: versioned DRAM state store + leases + barriers.
4. Phase 3: CUDA GPU0 pinned-window runner with telemetry.
5. Phase 4: operation DAG metadata from native Fold-CP math.
6. Phase 5: triangle multiplication spill proof.
7. Phase 7: worker executor wiring.
8. Phase 8: BioModStack backend flag integration.
9. Phase 6 / later: triangular attention and larger pairformer slice.

The first milestone worth calling a real data-plane proof is Phase 5 passing on GPU0: one real Fold-CP-shaped operation, live global state in DRAM, one workhorse GPU, exact/tolerant match to full tiny reference.

---

## Definition of done for the next sprint

The next sprint should stop at this concrete deliverable:

```text
DRAM context-spill workhorse v0:
- Fold-CP repo contains context_spill.py and phase_dag.py.
- 4-shard and 16-shard fake live-state plans pass in Fold-CP regression tests.
- versioned tile store rejects stale writes and enforces barriers.
- CUDA GPU0 workhorse runner copies one tile/window through pinned DRAM↔VRAM and reports H2D/compute/D2H telemetry.
- first real triangle-multiplication-style operation matches full tiny reference on 2x2 and 4x4 logical grids.
- worker metadata cannot confuse this backend with shared-cache serial output tiling.
```

Only after that should we claim the DRAM context-spill path is more than scaffolding.
