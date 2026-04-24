# Fold-CP Tiled Runtime Logical Underpinning and CPU Warm-Start Shortcut Assessment

Date: 2026-04-23

## Question

Christian asked why the `tiled-context-worker-pool` plan is logically necessary, how each phase practically moves the context/CP map out of GPU-only residency, and whether a cheaper shortcut could work:

> run a fake/CPU Boltz2 job first to create the whole context map in DRAM, close it while preserving the map, then feed that map into the GPUs.

He also provided an annotated scheduler example for a ~70-80 GB job, above any single GPU's VRAM but around the aggregate capacity of the local 32 GB + 24 GB + 24 GB + 16 GB GPU set.

## Short answer

The intuition is directionally right: the global context/pair state must become a persistent object outside any one GPU, and GPUs should be workers that stage pieces of it into VRAM, update them, and write them back.

But the cheap shortcut is not to run a real CPU Boltz2 job and then resume on GPUs. That is not a good path because the pair/context map is not a static precomputed file. It is repeatedly mutated by MSA/pairformer/recycling operations, and those operations are the expensive part. A CPU run that computes the real final map would already have done most of the hard model work, would be extremely slow, would need large temporary memory, and current Boltz2/Fold-CP does not expose a clean resume API from an arbitrary persisted `z`/`s` trunk state.

The useful cheap version is narrower:

1. create a fake or CPU-initialized tile store in DRAM/NVMe,
2. treat it as the authoritative global `z`/`s` state,
3. have GPU workers claim tile/phase work, load tiles, update, and write back,
4. prove 1-worker vs N-worker equivalence,
5. then replace the fake update with one real Fold-CP operation.

That is why the plan starts with fake-kernel tile-store/runtime before real Boltz math.

## What the "context map" really is

In Boltz2/Fold-CP terms, the main global state is not a photo-like finished map. It includes:

- pair/context tensor `z`, roughly `N_token x N_token x C_z`
- single/token tensor `s`, roughly `N_token x C_s`
- masks and input features
- phase/layer/recycling intermediates
- output-side tensors only after trunk/state completion

In the distributed Fold-CP code, `z_init` is created from embedded features and relative/bond/contact conditioning, then `s` and `z` are repeatedly updated through recycling, MSA, and pairformer operations. The relevant distributed forward path shows this sequence around `src/boltz/distributed/model/models/boltz2.py:521-615`:

```text
input embedding -> s_init / z_init -> recycling buffers -> recycling loop -> MSA -> pairformer -> distogram/structure/confidence
```

The important point: `z` is not just created once and then consumed. It is the live mutable trunk state.

## Why original Fold-CP is not enough

Original Fold-CP already has real context-parallel math, but its state ownership is GPU/rank-native:

```text
selected ranks -> square CP mesh -> DTensor shards live on ranks -> ring collectives move chunks between ranks
```

That is honest true CP, but it is not an out-of-core runtime. It does not make DRAM/NVMe the authority for state. It still assumes simultaneous ranks and a square CP size.

## What the tiled worker-pool changes

The tiled worker-pool changes state ownership:

```text
old/native CP:
GPU rank owns shard state

new/target runtime:
context_store owns global state; GPU workers temporarily borrow tiles
```

That is the key logical shift.

The non-GPU layer is not just a cache. It becomes the source of truth for the current global model state.

## How each phase practically moves state out of GPU-only residency

### Phase 0 — Backend contract split

This does not move data yet.

It prevents conceptual corruption by separating:

- `shared-cache-serial-output-tiling`
- `native-fold-cp-square-mesh`
- `tiled-context-worker-pool`

Without this split, the system can keep producing tiled artifacts from a serial prediction while pretending it solved memory scaling.

### Phase 1 — User-defined plan schema and dry-run estimator

This creates the address space for non-GPU state.

Instead of GPU count defining the CP map, the plan defines:

- global tensor shapes
- tile size/grid
- dtype
- state kinds
- memory roots and quotas
- worker resources
- scheduler policy

This is the first practical step because the context map needs stable names and coordinates before it can live outside VRAM:

```text
z tile (row=4, col=7, layer=12, recycle=1, dtype=bf16) -> storage URI + checksum + status
```

### Phase 2 — Context/tile store plus fake-kernel runtime

This is the first phase that actually proves the non-GPU residency model.

It creates a global tile store in DRAM/NVMe and updates it through a fake deterministic operation.

Worker flow:

```text
claim tile/phase lease
read required input tile(s) from context_store
copy working set to GPU VRAM
run fake update kernel
copy output back to DRAM/NVMe tile store
atomic write + checksum + status marker
release lease
barrier advances when all phase tiles complete
```

This phase proves the hard runtime mechanics without mixing in Boltz numerical complexity.

### Phase 3 — BioModStack scheduler integration

This makes BioModStack the dispatcher.

The parent job owns the global plan/store. Child workers stop being static output-bundle slicers and become tile/phase workers.

GPU count affects how many workers can run at once, not the logical plan shape.

### Phase 4 — Native Fold-CP proof track

This stays separate.

It proves true CP with the original square-mesh Fold-CP code but does not solve out-of-core execution.

### Phase 5 — Port one real Fold-CP operation

This is where real Boltz math starts using the persisted map.

The best first candidates are operations with known chunk/accumulation structure:

- triangle multiplication
- triangular attention

Original Fold-CP already contains useful patterns. For example, triangular attention converts DTensor shards to local tensors, streams/rings K/V/bias/mask chunks, and uses `tiled_softmax_attention_update` for online accumulation. The target runtime should replace "remote rank sends next chunk" with "context store provides next chunk" where possible.

Conceptually:

```text
for output tile z[i,j]:
    accumulator = empty
    for dependency chunk k in row/column/stripe:
        load z[i,k] or z[k,j] from store
        run partial operation in VRAM
        update accumulator
    write z_next[i,j] back to store
```

This is the real bridge from Fold-CP math to out-of-core execution.

### Phase 6 — Full out-of-core path

Only after one operation works:

- expand across pairformer blocks
- handle recycling
- add checkpoint/restart
- add tier promotion/demotion
- optimize storage format and tile size
- publish final structure only after all global state phases complete

## CPU Boltz2 warm-start shortcut assessment

### Version A: CPU creates `z_init` / initial feature tiles

This is plausible and useful.

`z_init`/`s_init` construction and masks/features can be computed or materialized into the tile store before GPU workers begin. This is a good bootstrapping step.

Limit: it only covers initialization, not the pairformer/recycling compute that dominates the problem.

### Version B: CPU runs the real trunk/pairformer to final `z`, then GPUs continue

This is not a good shortcut.

Reasons:

1. It does most of the hard work on CPU.
   If CPU has already run the trunk/pairformer to final `z`, the main memory-heavy computation has already happened, just very slowly.

2. The working set is larger than the final map.
   A 70 GB final pair/context state can require multiple live buffers/temporaries. Even 2x-5x overhead is 140-350 GB, above or near this host's RAM capacity.

3. Current Boltz2/Fold-CP does not expose a clean resume-from-persisted-`z` API.
   The model forward owns intermediate tensors internally. Closing the process frees execution state unless explicit layer-boundary serialization and resume hooks are added.

4. CPU kernels are not the target kernels.
   CPU execution may not match GPU kernel behavior/performance, especially for triangle attention/multiplication variants.

5. It does not solve repeated layer updates.
   Every pairformer/recycling phase needs to update the global state. The needed architecture is not "load a finished map once" but "persist and mutate the map across many phases."

### Version C: CPU/fake job creates a fake global context map for scheduler testing

This is exactly the recommended cheap proof.

It is valuable because it proves:

- tile metadata
- DRAM/NVMe storage
- worker leases
- barriers
- GPU staging
- writeback
- 1-worker vs N-worker equivalence

But it is a runtime proof, not a real Boltz shortcut.

## Mapping the annotated 70-80 GB scheduler example

Local raw VRAM pool:

```text
5090:    32 GB
3090:    24 GB
3090:    24 GB
5060 Ti: 16 GB
Total:   96 GB raw
```

A practical safe pool is lower because of CUDA contexts, weights, fragmentation, and temporary buffers. Rough 80-85% usable pool is about 76.8-81.6 GB.

For a 70 GB state, a 4:3:3:2 weighted placement gives roughly:

```text
5090:    23.3 GB
3090:    17.5 GB
3090:    17.5 GB
5060 Ti: 11.7 GB
```

For an 80 GB state:

```text
5090:    26.7 GB
3090:    20.0 GB
3090:    20.0 GB
5060 Ti: 13.3 GB
```

This matches the screenshot intuition: 5090 gets more shards, 3090s get medium shards, 5060 Ti is optional or capped.

But current native Fold-CP cannot do this weighted 4:3:3:2 ownership. Native Fold-CP uses equal ranks in a square CP mesh. With 4 equal ranks, an 80 GB state is 20 GB per rank before overhead, which is too high for the 16 GB card. A weighted worker-pool or out-of-core runtime is needed for the annotated behavior.

## Practical policy for a 70 GB above-one-card job

Preferred policy:

1. Try in-core weighted worker placement first if safe:
   - e.g. caps around 28 GB on 5090, 20 GB on each 3090, 10-12 GB on 5060 Ti.
2. Use equal logical tiles but weighted dispatch:
   - the 5090 claims more tiles over time
   - 3090s claim fewer
   - 5060 Ti claims only small/safe work or is skipped
3. If aggregate safe VRAM is insufficient, keep the full state in DRAM/NVMe and stream active tiles.
4. Queue only if the user policy forbids slow/offloaded mode or there is not enough SSD/DRAM quota.

Do not start with ragged mathematical tile boundaries. Keep logical tiles regular and express heterogeneity through scheduler assignment/caps first.

## Evidence level and feasibility caveat

There is not yet evidence that full Boltz2/Fold-CP can run today as an asynchronous host-persistent inference engine. That specific runtime does not exist in the inspected code.

The evidence is weaker and more specific:

1. Original Fold-CP proves the pair/context math can be decomposed across chunks/ranks.
   - triangular attention streams K/V/bias/mask chunks in a ring and incrementally combines block outputs with `tiled_softmax_attention_update`
   - triangle multiplication rotates chunks and accumulates partial matmul contributions
   - this proves the operations are not inherently "one monolithic full tensor or impossible"

2. The current communication is asynchronous only locally and temporarily.
   - `comm.py` dispatches `dist.batch_isend_irecv` asynchronously, but every step later calls `wait_until_finished()` before using received data
   - the algorithm is still globally synchronous at step/barrier boundaries
   - this is not a fault-tolerant asynchronous scheduler and not a host-side persistent store

3. `tiled_softmax_attention_update` is direct evidence that at least attention-style blocks can be mathematically accumulated over chunks without storing the full attention matrix at once.
   - this supports tiled/out-of-core reformulation for some operations
   - it does not automatically prove the whole model can be ported without substantial work

4. The missing proof is the important one:
   - can a persisted DRAM/NVMe context store replace GPU-rank ownership across a real operation and still match reference output?

Therefore the honest feasibility statement is:

- technically plausible
- not proven for full Boltz2/Fold-CP yet
- likely feasible operation-by-operation
- high engineering risk
- should be validated by fake-kernel equivalence first, then one real operation, not by claiming full asynchronous inference exists

The minimum proof that would upgrade this from plausible to real is:

```text
tiny reference input -> serial/native result
same input -> persisted tile store -> 1 worker result
same input -> persisted tile store -> N worker result
all outputs match within tolerance
metadata proves state was stored outside VRAM between phases
```

## Final answer

Christian's intuition is correct at the systems level: make the global context map a persisted host-side object and feed pieces into GPUs.

The correction is that this is a proposed runtime architecture, not something original Fold-CP already proves end-to-end. The persisted object must be mutated phase-by-phase throughout the model. It is not a one-time CPU precompute followed by a GPU finish. The cheap proof should be a fake CPU/DRAM-initialized tile store plus GPU worker update loop, not a real CPU Boltz2 trunk run.
