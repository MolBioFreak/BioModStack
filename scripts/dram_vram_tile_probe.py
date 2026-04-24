#!/usr/bin/env python3
"""DRAM->VRAM tile streaming probe for heterogeneous Fold-CP/Boltz2 runtime work.

This is deliberately a probe, not a Boltz kernel port. It answers the first
engineering question for the DRAM-intermediary design: can selected GPUs stream
coarse DRAM-resident tiles into VRAM concurrently, do some work, and write the
updated tile back without relying on CUDA peer access / NVLink?

The importable planning helpers stay torch-free so they can be unit-tested in
BioModStack's normal Python test environment. The actual CUDA benchmark imports
PyTorch only inside the probe command.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class GpuWorkerSpec:
    gpu_id: int
    name: str
    max_vram_gb: float
    weight: float = 1.0


@dataclass(frozen=True)
class TileSpec:
    tile_id: str
    row_range: tuple[int, int]
    col_range: tuple[int, int]


@dataclass(frozen=True)
class WorkerProbeResult:
    gpu_id: int
    name: str
    tile_bytes: int
    iterations: int
    elapsed_s: float
    bytes_h2d: int
    bytes_d2h: int
    effective_transfer_gbps: float
    ok: bool
    error: str | None = None


def estimate_pair_tile_bytes(*, tile_tokens: int, channels: int, dtype_bytes: int = 2, state_copies: int = 1) -> int:
    """Estimate bytes for square pair/context tiles.

    A Boltz-style pair tile has shape roughly [row_tokens, col_tokens, channels].
    `state_copies` is explicit because real kernels often need at least input and
    output state live somewhere at phase boundaries.
    """

    if tile_tokens <= 0:
        raise ValueError("tile_tokens must be positive")
    if channels <= 0:
        raise ValueError("channels must be positive")
    if dtype_bytes <= 0:
        raise ValueError("dtype_bytes must be positive")
    if state_copies <= 0:
        raise ValueError("state_copies must be positive")
    return tile_tokens * tile_tokens * channels * dtype_bytes * state_copies


def plan_square_tiles(*, sequence_length: int, tile_tokens: int) -> list[TileSpec]:
    """Create regular logical row/column tiles covering an N x N pair map."""

    if sequence_length <= 0:
        raise ValueError("sequence_length must be positive")
    if tile_tokens <= 0:
        raise ValueError("tile_tokens must be positive")
    ranges = []
    for start in range(0, sequence_length, tile_tokens):
        ranges.append((start, min(start + tile_tokens, sequence_length)))
    tiles: list[TileSpec] = []
    for row_idx, row_range in enumerate(ranges):
        for col_idx, col_range in enumerate(ranges):
            tiles.append(TileSpec(tile_id=f"r{row_idx:04d}_c{col_idx:04d}", row_range=row_range, col_range=col_range))
    return tiles


def assign_tiles_weighted(tiles: Iterable[TileSpec], workers: Iterable[GpuWorkerSpec]) -> dict[int, list[TileSpec]]:
    """Assign regular tiles by dispatch cadence, not ragged geometry.

    This is intentionally simple: make heterogeneity a scheduler property first.
    A 3:1 weight ratio means the larger worker appears ~3x as often in the
    dispatch cycle and therefore consumes more equal-sized logical tiles.
    """

    tile_list = list(tiles)
    worker_list = list(workers)
    if not worker_list:
        raise ValueError("at least one worker is required")
    for worker in worker_list:
        if worker.weight <= 0:
            raise ValueError(f"worker {worker.gpu_id} has non-positive weight")

    scale = 10
    cycle: list[GpuWorkerSpec] = []
    for worker in worker_list:
        slots = max(1, int(round(worker.weight * scale)))
        cycle.extend([worker] * slots)
    assignments: dict[int, list[TileSpec]] = {worker.gpu_id: [] for worker in worker_list}
    for idx, tile in enumerate(tile_list):
        worker = cycle[idx % len(cycle)]
        assignments[worker.gpu_id].append(tile)
    return assignments


def _parse_gpu_ids(text: str | None) -> list[int]:
    if not text:
        return []
    result: list[int] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        gpu_id = int(item)
        if gpu_id not in result:
            result.append(gpu_id)
    return result


def _detect_gpu_specs(gpu_ids: list[int] | None = None) -> list[GpuWorkerSpec]:
    """Detect GPU specs using CUDA ordinals when PyTorch is available.

    `nvidia-smi` indices and PyTorch CUDA ordinals can diverge when
    `CUDA_DEVICE_ORDER` is not pinned. The probe command addresses GPUs through
    PyTorch, so planning should prefer PyTorch's ordinal view whenever possible.
    """

    wanted = set(gpu_ids or [])
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            specs: list[GpuWorkerSpec] = []
            for gpu_id in range(torch.cuda.device_count()):
                if wanted and gpu_id not in wanted:
                    continue
                props = torch.cuda.get_device_properties(gpu_id)
                mem_gb = float(props.total_memory) / 1024.0**3
                specs.append(
                    GpuWorkerSpec(
                        gpu_id=gpu_id,
                        name=torch.cuda.get_device_name(gpu_id),
                        max_vram_gb=mem_gb,
                        weight=max(mem_gb, 1.0) / 16.0,
                    )
                )
            return specs
    except Exception:
        pass

    cmd = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total",
        "--format=csv,noheader,nounits",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    specs: list[GpuWorkerSpec] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        idx_text, name, mem_text = [part.strip() for part in line.split(",", 2)]
        gpu_id = int(idx_text)
        if wanted and gpu_id not in wanted:
            continue
        mem_gb = float(mem_text) / 1024.0
        # First-pass capacity weight; later runtime can override with explicit user plan.
        specs.append(GpuWorkerSpec(gpu_id=gpu_id, name=name, max_vram_gb=mem_gb, weight=max(mem_gb, 1.0) / 16.0))
    return specs


def _dtype_from_name(torch_mod: Any, name: str) -> Any:
    if name == "float16":
        return torch_mod.float16
    if name == "bfloat16":
        return torch_mod.bfloat16
    if name == "float32":
        return torch_mod.float32
    raise ValueError(f"Unsupported dtype {name}")


def _worker_copy_probe(
    gpu_id: int,
    tile_bytes: int,
    iterations: int,
    warmup: int,
    dtype_name: str,
    queue: mp.Queue,
) -> None:
    try:
        import torch

        torch.cuda.set_device(gpu_id)
        dtype = _dtype_from_name(torch, dtype_name)
        dtype_bytes = torch.tensor([], dtype=dtype).element_size()
        numel = max(1, tile_bytes // dtype_bytes)
        actual_tile_bytes = numel * dtype_bytes
        props = torch.cuda.get_device_properties(gpu_id)

        host_in = torch.empty(numel, dtype=dtype, pin_memory=True)
        host_out = torch.empty(numel, dtype=dtype, pin_memory=True)
        device_tile = torch.empty(numel, dtype=dtype, device=f"cuda:{gpu_id}")
        host_in.fill_(1.0 + gpu_id)

        torch.cuda.synchronize(gpu_id)
        start = time.perf_counter()
        total_iters = warmup + iterations
        for step in range(total_iters):
            device_tile.copy_(host_in, non_blocking=True)
            # Deliberately tiny fake update: enough to force a real kernel launch
            # while keeping the probe transfer-dominated.
            device_tile.mul_(1.0001)
            host_out.copy_(device_tile, non_blocking=True)
        torch.cuda.synchronize(gpu_id)
        elapsed_total = time.perf_counter() - start
        elapsed = elapsed_total * (iterations / total_iters) if total_iters else elapsed_total
        bytes_h2d = actual_tile_bytes * iterations
        bytes_d2h = actual_tile_bytes * iterations
        gbps = (bytes_h2d + bytes_d2h) / elapsed / 1e9 if elapsed > 0 else 0.0
        queue.put(
            asdict(
                WorkerProbeResult(
                    gpu_id=gpu_id,
                    name=props.name,
                    tile_bytes=actual_tile_bytes,
                    iterations=iterations,
                    elapsed_s=elapsed,
                    bytes_h2d=bytes_h2d,
                    bytes_d2h=bytes_d2h,
                    effective_transfer_gbps=gbps,
                    ok=True,
                )
            )
        )
    except BaseException as exc:  # pragma: no cover - exercised only on CUDA/runtime failures
        queue.put(
            asdict(
                WorkerProbeResult(
                    gpu_id=gpu_id,
                    name="unknown",
                    tile_bytes=tile_bytes,
                    iterations=iterations,
                    elapsed_s=0.0,
                    bytes_h2d=0,
                    bytes_d2h=0,
                    effective_transfer_gbps=0.0,
                    ok=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        )


def run_parallel_copy_probe(
    *,
    gpu_ids: list[int],
    tile_mb: int,
    iterations: int,
    warmup: int = 2,
    dtype_name: str = "float16",
) -> dict[str, Any]:
    """Run concurrent pinned-DRAM -> VRAM -> pinned-DRAM tile streaming probes."""

    if not gpu_ids:
        raise ValueError("gpu_ids must not be empty")
    if tile_mb <= 0:
        raise ValueError("tile_mb must be positive")
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    tile_bytes = tile_mb * 1024 * 1024

    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    procs = [
        ctx.Process(target=_worker_copy_probe, args=(gpu_id, tile_bytes, iterations, warmup, dtype_name, queue))
        for gpu_id in gpu_ids
    ]
    t0 = time.perf_counter()
    for proc in procs:
        proc.start()
    results = [queue.get() for _ in procs]
    for proc in procs:
        proc.join(timeout=30)
    wall_s = time.perf_counter() - t0
    for proc in procs:
        if proc.exitcode not in (0, None):
            results.append(
                asdict(
                    WorkerProbeResult(
                        gpu_id=-1,
                        name=f"pid={proc.pid}",
                        tile_bytes=tile_bytes,
                        iterations=iterations,
                        elapsed_s=0.0,
                        bytes_h2d=0,
                        bytes_d2h=0,
                        effective_transfer_gbps=0.0,
                        ok=False,
                        error=f"process exitcode {proc.exitcode}",
                    )
                )
            )
    ok_results = [item for item in results if item.get("ok")]
    total_bytes = sum(item["bytes_h2d"] + item["bytes_d2h"] for item in ok_results)
    max_worker_elapsed = max((item["elapsed_s"] for item in ok_results), default=0.0)
    aggregate_gbps_worker_window = total_bytes / max_worker_elapsed / 1e9 if max_worker_elapsed > 0 else 0.0
    aggregate_gbps_wall = total_bytes / wall_s / 1e9 if wall_s > 0 else 0.0
    return {
        "mode": "pinned_dram_vram_copy_update_copyback",
        "gpu_ids": gpu_ids,
        "tile_mb_requested": tile_mb,
        "iterations": iterations,
        "warmup": warmup,
        "dtype": dtype_name,
        "wall_s": wall_s,
        "aggregate_effective_gbps_worker_window": aggregate_gbps_worker_window,
        "aggregate_effective_gbps_wall_including_spawn": aggregate_gbps_wall,
        "results": sorted(results, key=lambda item: item["gpu_id"]),
    }


def _cmd_plan(args: argparse.Namespace) -> int:
    tiles = plan_square_tiles(sequence_length=args.sequence_length, tile_tokens=args.tile_tokens)
    specs = _detect_gpu_specs(_parse_gpu_ids(args.gpus))
    assignments = assign_tiles_weighted(tiles, specs) if specs else {}
    tile_bytes = estimate_pair_tile_bytes(
        tile_tokens=args.tile_tokens,
        channels=args.channels,
        dtype_bytes=args.dtype_bytes,
        state_copies=args.state_copies,
    )
    payload = {
        "sequence_length": args.sequence_length,
        "tile_tokens": args.tile_tokens,
        "channels": args.channels,
        "dtype_bytes": args.dtype_bytes,
        "state_copies": args.state_copies,
        "tile_bytes_estimate": tile_bytes,
        "tile_mib_estimate": tile_bytes / 1024 / 1024,
        "tile_count": len(tiles),
        "workers": [asdict(spec) for spec in specs],
        "assigned_tile_counts": {str(gpu_id): len(worker_tiles) for gpu_id, worker_tiles in assignments.items()},
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    gpu_ids = _parse_gpu_ids(args.gpus)
    if not gpu_ids:
        gpu_ids = [spec.gpu_id for spec in _detect_gpu_specs(None)]
    result = run_parallel_copy_probe(
        gpu_ids=gpu_ids,
        tile_mb=args.tile_mb,
        iterations=args.iterations,
        warmup=args.warmup,
        dtype_name=args.dtype,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if all(item.get("ok") for item in result["results"]) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Print a logical tile/worker assignment estimate")
    plan.add_argument("--sequence-length", type=int, required=True)
    plan.add_argument("--tile-tokens", type=int, required=True)
    plan.add_argument("--channels", type=int, default=128)
    plan.add_argument("--dtype-bytes", type=int, default=2)
    plan.add_argument("--state-copies", type=int, default=2)
    plan.add_argument("--gpus", default=None, help="Comma-separated GPU IDs; default all visible nvidia-smi GPUs")
    plan.set_defaults(func=_cmd_plan)

    probe = sub.add_parser("probe", help="Run a concurrent pinned-DRAM <-> VRAM streaming probe")
    probe.add_argument("--gpus", default=None, help="Comma-separated GPU IDs; default all visible nvidia-smi GPUs")
    probe.add_argument("--tile-mb", type=int, default=256)
    probe.add_argument("--iterations", type=int, default=8)
    probe.add_argument("--warmup", type=int, default=2)
    probe.add_argument("--dtype", choices=("float16", "bfloat16", "float32"), default="float16")
    probe.add_argument("--output", default=None, help="Optional JSON output path")
    probe.set_defaults(func=_cmd_probe)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
