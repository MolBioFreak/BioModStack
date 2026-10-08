# Fold-CP GPU communication facts

Date: 2026-04-24

Scope: original Fold-CP checkout at `/home/dalab/code/vendor/hermes-agent/tmp/boltz-cp`, plus simple local NCCL probes on the Pop!_OS workstation.

## Bottom line

Fold-CP manages multiple GPUs as a live `torch.distributed` / DTensor context-parallel program. It does not use a custom high-speed memory fabric in the code. The communication mechanisms visible in source are PyTorch Distributed, NCCL for CUDA process groups, DTensor sharding, and point-to-point `isend`/`irecv` operations organized into 2D ring/transpose patterns.

On this workstation, the live NCCL probes did not use CUDA direct P2P or NVLink. NCCL reported `isAllDirectP2p 0`, `isAllCudaP2p 0`, and channel paths `via SHM/direct/direct` for the tested same-node GPU communication. `nvidia-smi topo` also reports no NVLink and no P2P-capable GPU pairs.

## Fold-CP code facts

### Launch/process model

Source: `src/boltz/distributed/predict.py`

- `run_predict` initializes `DistributedManager` before loading/running the model.
- `size_dp * size_cp` must equal `world_size`.
- `size_cp` must be a perfect square.
- The CP mesh is built as `("cp", (sqrt(size_cp), sqrt(size_cp)))`.
- CPU Gloo groups are also created for object/data coordination, but CUDA model communication uses the distributed CUDA backend.

Relevant lines:

```text
predict.py:252-254  Distributed setup; initialize DistributedManager
predict.py:257-258  require world_size == size_dp * size_cp
predict.py:260-262  require size_cp perfect square
predict.py:291-294  create grid_group_sizes [("dp", size_dp), ("cp", (axis, axis))]
predict.py:296-310  create world_cpu/cp_cpu Gloo groups and CPU mesh mapping
```

Source: `src/boltz/distributed/manager.py`

- Each process/rank selects `cuda:{local_rank}`.
- For CUDA/NCCL, it calls `torch.distributed.init_process_group(..., device_id=manager.device)` when supported.
- It creates PyTorch `DeviceMesh` objects from rank layouts.
- A tuple group such as `cp=(2,2)` becomes mesh subgroup dimensions `cp_axis_0`, `cp_axis_1`.

Relevant lines:

```text
manager.py:348-358  choose local CUDA rank/device
manager.py:370-389  set CUDA device, empty cache, init NCCL process group
manager.py:473-490  compute layout and create torch.distributed.device_mesh.DeviceMesh
manager.py:611-653  tuple group sizes become subgroup axes and subgroup DeviceMesh
manager.py:580-594  2x2 layout example: coords (0,0)->rank0, (0,1)->rank1, (1,0)->rank2, (1,1)->rank3
```

### Communication primitive

Source: `src/boltz/distributed/comm.py`

- Fold-CP's custom communication wrapper queues `dist.P2POp(dist.isend, ...)` and `dist.P2POp(dist.irecv, ...)`.
- It dispatches with `dist.batch_isend_irecv`.
- It later blocks with `work.wait()` in `wait_until_finished()`.
- Therefore source-level semantics are asynchronous launch / overlap, followed by required synchronization before received data is used.

Relevant lines:

```text
comm.py:147-175  build P2POp isend/irecv operations
comm.py:178-204  dispatch queued ops with dist.batch_isend_irecv
comm.py:205-233  wait_until_finished blocks on work.wait()
comm.py:235-276  enqueue_to_dispatch requires wait before accessing inter-rank data
```

### Triangle multiplication communication

Source: `src/boltz/distributed/model/layers/triangular_mult.py`

- Inputs are DTensors sharded across token dimensions.
- The implementation uses a 2D ring communication object.
- Row/column chunks are rotated while each rank accumulates partial matrix products into its local output.

Relevant lines:

```text
triangular_mult.py:268-277  initial row/column sends and waits
triangular_mult.py:279-291  double-buffered loop: enqueue next row/col chunks, matmul current chunks, wait
triangular_mult.py:303-322  docstring: distributed 2D grid, ring communication, avoids materializing full tensors
triangular_mult.py:326-330  inputs must be DTensors sharded on dimensions 1 and 2
triangular_mult.py:332-335  tensor b rotated by row, tensor a by column; each process accumulates partial products
```

### Triangular attention communication

Source: `src/boltz/distributed/model/layers/triangular_attention.py`

- Inputs/weights are DTensors on the same device mesh.
- K/V/mask/bias chunks are shuffled/rotated using `One2OneComm` handles.
- Each step launches communication for the next block, computes the current block, merges it into online softmax accumulators, then waits for the next block.

Relevant lines:

```text
triangular_attention.py:630-656  initial K/V/mask/bias shuffles
triangular_attention.py:700-704  wait for initial mask/bias readiness
triangular_attention.py:720-731  launch send/recv for next K/V/mask/bias chunks
triangular_attention.py:738-807  compute current attention block and update online softmax accumulator
triangular_attention.py:809-813  wait for next K/V/bias/mask communication
```

Source: `src/boltz/distributed/utils.py`

- `tiled_softmax_attention_update` explicitly implements chunked online softmax accumulation.
- Its docstring states this is useful when sequence length is too large to fit in memory and maintains mathematical equivalence to full softmax over chunks.

Relevant lines:

```text
utils.py:742-760  online softmax accumulation over chunks; avoids storing all intermediate values
```

## Local workstation hardware/topology facts

Command:

```bash
nvidia-smi topo -m
nvidia-smi topo -p2p r/w/p/a/n
```

Observed GPU topology:

```text
GPU0  GPU1  GPU2  GPU3
GPU0   X    NODE  NODE  NODE
GPU1  NODE   X    NODE  NODE
GPU2  NODE  NODE   X    PHB
GPU3  NODE  NODE  PHB    X
```

Meaning from NVIDIA legend:

- `NODE`: traverses PCIe plus interconnect between PCIe host bridges within a NUMA node.
- `PHB`: traverses PCIe plus a PCIe host bridge, typically the CPU.
- `NV#`: would indicate NVLink; none is present here.

Observed P2P capability:

- P2P read: no `OK` pairs; mostly `NS`, GPU2/GPU3 `CNS`.
- P2P write: no `OK` pairs; mostly `NS`, GPU2/GPU3 `CNS`.
- P2P over PCIe: no `OK` pairs.
- P2P atomics: no `OK` pairs.
- P2P NVLink: no `OK` pairs.

So this workstation should be treated as no CUDA GPU peer memory access and no NVLink fabric.

## Local PyTorch/NCCL environment

Command:

```bash
uv run python - <<'PY'
import torch
print(torch.__version__)
print(torch.version.cuda)
print(torch.distributed.is_nccl_available())
print(torch.cuda.nccl.version())
print(torch.cuda.device_count())
for i in range(torch.cuda.device_count()):
    print(i, torch.cuda.get_device_name(i), torch.cuda.get_device_properties(i).total_memory//(1024**2), 'MiB')
PY
```

Observed:

```text
torch 2.11.0+cu130
CUDA 13.0
NCCL available True
NCCL version (2, 28, 9)
GPU0 RTX 5090 32109 MiB
GPU1 RTX 3090 24124 MiB
GPU2 RTX 3090 24124 MiB
GPU3 RTX 5060 Ti 15825 MiB
```

## Local NCCL probe facts

Probe scripts were written to `/tmp/nccl_foldcp_style_probe.py` and `/tmp/nccl_pair_probe.py` and launched via `torchrun --standalone --nproc_per_node=4` with:

```bash
NCCL_DEBUG=INFO
NCCL_DEBUG_SUBSYS=INIT,GRAPH,COLL,P2P
TORCH_DISTRIBUTED_DEBUG=DETAIL
```

### Probe 1: 4-rank NCCL allreduce + ring point-to-point

Purpose: confirm NCCL communicator works and inspect transport selected for a Fold-CP-like CUDA tensor ring.

Facts:

```text
RANK_FACT rank=0 cuda:0 RTX 5090
RANK_FACT rank=1 cuda:1 RTX 3090
RANK_FACT rank=2 cuda:2 RTX 3090
RANK_FACT rank=3 cuda:3 RTX 5060 Ti

ALLREDUCE_FACT first=10.0 on all ranks
P2P_RING_FACT completed; each rank received expected neighbor value
```

Transport-related NCCL facts:

```text
NET/IB : No device found.
Using network Socket
Check P2P Type isAllDirectP2p 0 directMode 0 isAllCudaP2p 0
Channel 00/02 : 0 1 2 3
Channel 01/02 : 0 1 2 3
Channel ... via SHM/direct/direct
NVLS multicast support is not available
```

Interpretation:

- NCCL did not find InfiniBand/RDMA devices.
- CUDA direct P2P was not available.
- Same-node GPU channel paths were reported as `SHM/direct/direct`, i.e. NCCL shared-memory intra-node transport, not NVLink/CUDA peer memory.

### Probe 2: Fold-CP 2x2 row/column-style exchanges

Purpose: explicitly exercise pairs that resemble a 2x2 CP mesh:

- Column-like swaps: 0<->2 and 1<->3.
- Row-like swaps: 0<->1 and 2<->3.

Facts:

```text
PAIR_FACT col rank=0 partner=2 recv_first=2.0
PAIR_FACT col rank=2 partner=0 recv_first=0.0
PAIR_FACT col rank=1 partner=3 recv_first=3.0
PAIR_FACT col rank=3 partner=1 recv_first=1.0
PAIR_FACT row rank=0 partner=1 recv_first=11.0
PAIR_FACT row rank=1 partner=0 recv_first=10.0
PAIR_FACT row rank=2 partner=3 recv_first=13.0
PAIR_FACT row rank=3 partner=2 recv_first=12.0
```

Transport-related NCCL facts:

```text
Check P2P Type isAllDirectP2p 0 directMode 0 isAllCudaP2p 0
Channel 0 -> 2 via SHM/direct/direct
Channel 1 -> 3 via SHM/direct/direct
Channel 0 -> 1 via SHM/direct/direct
Channel 2 -> 3 via SHM/direct/direct
NET/IB : No device found.
Using network Socket
NVLS multicast support is not available
```

Interpretation:

- Fold-CP-like row and column pair exchanges succeed on this machine.
- They do not use CUDA direct P2P or NVLink.
- The observed intra-node route is NCCL SHM transport.

## Interpretation for Christian's question

Christian's expectation is basically right: Fold-CP's source does not reveal a special memory-bandwidth enhancement mechanism beyond standard PyTorch/NCCL/DTensor distributed execution. If the authors ran on H100 clusters, the high bandwidth would come from the normal cluster/platform fabric: NVLink/NVSwitch inside nodes where available, InfiniBand/RDMA between nodes where available, and NCCL choosing appropriate transports.

On this local mixed-consumer-GPU workstation, those high-bandwidth mechanisms are absent:

- no NVLink reported by `nvidia-smi topo -m`
- no CUDA P2P pairs reported by `nvidia-smi topo -p2p`
- no InfiniBand/RDMA device found by NCCL
- NCCL probes show `SHM/direct/direct`, not NVLink/P2P

So for this box, Fold-CP-style multi-GPU communication is real, but it is not a fast GPU fabric. It is standard NCCL over the available host/PCIe/shared-memory path.

## Consequence for future BioModStack runtime design

Native Fold-CP is a true live distributed CP program, but it assumes synchronized ranks and equal-ish square CP mesh participation. On hardware without CUDA P2P/NVLink and with heterogeneous VRAM, a deliberate host-side DRAM/NVMe tile store may be architecturally cleaner than pretending the machine is a uniform H100/NVSwitch node.

That does not mean DRAM is faster than NVLink; it means DRAM can become an explicit scheduling/cache layer when no fast GPU-GPU fabric exists.
