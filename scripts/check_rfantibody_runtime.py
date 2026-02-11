#!/usr/bin/env python3
"""RFantibody runtime preflight checks.

Fails fast when the runtime stack is incompatible before RFantibody inference starts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class RuntimeInfo:
    rfantibody_commit: str
    torch_version: str
    torch_cuda_version: str
    cuda_available: bool
    device_name: str
    device_capability: str
    dgl_version: str
    dgl_backend: str
    dgl_copy_e_sum_ok: bool
    dgl_copy_e_mean_ok: bool


def _resolve_rfantibody_commit() -> str:
    env_commit = os.getenv("RFANTIBODY_COMMIT")
    if env_commit:
        return env_commit
    try:
        return (
            subprocess.check_output(
                ["git", "-C", "/opt/RFantibody", "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            .strip()
        )
    except Exception:
        return "unknown"


def _write_json(path: Optional[str], payload: dict) -> None:
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))


def _fail(message: str, diagnostics: dict, output_path: Optional[str]) -> int:
    payload = {"ok": False, "error": message, "diagnostics": diagnostics}
    _write_json(output_path, payload)
    print(f"[RFA-PREFLIGHT] ERROR: {message}", file=sys.stderr)
    if diagnostics:
        print("[RFA-PREFLIGHT] Diagnostics:", file=sys.stderr)
        print(json.dumps(diagnostics, indent=2), file=sys.stderr)
    return 1


def run_preflight(output_path: Optional[str]) -> int:
    diagnostics: dict = {}

    try:
        import torch
    except Exception as exc:
        return _fail(f"Failed to import torch: {exc}", diagnostics, output_path)

    diagnostics["torch_version"] = torch.__version__
    diagnostics["torch_cuda_version"] = torch.version.cuda
    diagnostics["cuda_available"] = bool(torch.cuda.is_available())
    if not torch.cuda.is_available():
        return _fail("CUDA is not available to torch", diagnostics, output_path)

    try:
        device_index = torch.cuda.current_device()
        device = torch.device(f"cuda:{device_index}")
        device_name = torch.cuda.get_device_name(device_index)
        capability = torch.cuda.get_device_capability(device_index)
    except Exception as exc:
        return _fail(f"Failed to query CUDA device details: {exc}", diagnostics, output_path)

    diagnostics["device_name"] = device_name
    diagnostics["device_capability"] = f"{capability[0]}.{capability[1]}"

    try:
        import dgl
    except Exception as exc:
        return _fail(f"Failed to import dgl: {exc}", diagnostics, output_path)

    diagnostics["dgl_version"] = dgl.__version__
    diagnostics["dgl_backend"] = os.getenv("DGLBACKEND", "unset")

    # Hard-fail on explicit CUDA tag mismatches (e.g., dgl+cu121 with torch CUDA 12.8).
    torch_cuda = str(torch.version.cuda or "")
    torch_cuda_tag = ""
    if torch_cuda and "." in torch_cuda:
        major, minor = torch_cuda.split(".", 1)
        torch_cuda_tag = f"cu{major}{minor.split('.', 1)[0]}"
    dgl_cuda_tag = ""
    if "+cu" in str(dgl.__version__):
        dgl_cuda_tag = str(dgl.__version__).split("+", 1)[1]
    diagnostics["torch_cuda_tag"] = torch_cuda_tag or "unknown"
    diagnostics["dgl_cuda_tag"] = dgl_cuda_tag or "unknown"
    if torch_cuda_tag and dgl_cuda_tag and dgl_cuda_tag != torch_cuda_tag:
        return _fail(
            f"DGL CUDA build tag mismatch: dgl={dgl_cuda_tag}, torch={torch_cuda_tag}",
            diagnostics,
            output_path,
        )

    try:
        import dgl.ops as dgl_ops
    except Exception as exc:
        return _fail(f"Failed to import dgl.ops: {exc}", diagnostics, output_path)

    # Minimal deterministic GPU sanity check for the message-passing ops used by SE3Transformer.
    try:
        src = torch.tensor([0, 1, 2, 2], device=device, dtype=torch.int64)
        dst = torch.tensor([1, 2, 0, 1], device=device, dtype=torch.int64)
        graph = dgl.graph((src, dst), num_nodes=3, device=device)

        edge_values = torch.arange(1, 17, device=device, dtype=torch.float32).reshape(4, 4)

        gpu_sum = dgl_ops.copy_e_sum(graph, edge_values)
        gpu_mean = dgl_ops.copy_e_mean(graph, edge_values)

        graph_cpu = dgl.graph((src.cpu(), dst.cpu()), num_nodes=3)
        cpu_values = edge_values.cpu()
        cpu_sum = dgl_ops.copy_e_sum(graph_cpu, cpu_values)
        cpu_mean = dgl_ops.copy_e_mean(graph_cpu, cpu_values)

        if gpu_sum.shape != (3, 4) or gpu_mean.shape != (3, 4):
            return _fail(
                f"Unexpected DGL op output shapes: sum={tuple(gpu_sum.shape)}, mean={tuple(gpu_mean.shape)}",
                diagnostics,
                output_path,
            )

        if not torch.isfinite(gpu_sum).all() or not torch.isfinite(gpu_mean).all():
            return _fail("DGL op outputs contain non-finite values", diagnostics, output_path)

        if not torch.allclose(gpu_sum.cpu(), cpu_sum, atol=1e-5, rtol=1e-5):
            return _fail("DGL copy_e_sum GPU output does not match CPU reference", diagnostics, output_path)

        if not torch.allclose(gpu_mean.cpu(), cpu_mean, atol=1e-5, rtol=1e-5):
            return _fail("DGL copy_e_mean GPU output does not match CPU reference", diagnostics, output_path)

    except Exception as exc:
        return _fail(f"DGL CUDA op sanity test failed: {exc}", diagnostics, output_path)

    runtime = RuntimeInfo(
        rfantibody_commit=_resolve_rfantibody_commit(),
        torch_version=torch.__version__,
        torch_cuda_version=str(torch.version.cuda),
        cuda_available=True,
        device_name=device_name,
        device_capability=f"{capability[0]}.{capability[1]}",
        dgl_version=dgl.__version__,
        dgl_backend=os.getenv("DGLBACKEND", "unset"),
        dgl_copy_e_sum_ok=True,
        dgl_copy_e_mean_ok=True,
    )

    payload = {"ok": True, "runtime": asdict(runtime)}
    _write_json(output_path, payload)

    print("[RFA-PREFLIGHT] OK")
    print(f"[RFA-PREFLIGHT] RFantibody commit: {runtime.rfantibody_commit}")
    print(f"[RFA-PREFLIGHT] torch: {runtime.torch_version} (CUDA {runtime.torch_cuda_version})")
    print(f"[RFA-PREFLIGHT] dgl: {runtime.dgl_version} (backend={runtime.dgl_backend})")
    print(
        f"[RFA-PREFLIGHT] GPU: {runtime.device_name} (capability {runtime.device_capability})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RFantibody runtime stack before inference")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output file for runtime diagnostics",
    )
    args = parser.parse_args()
    return run_preflight(args.output)


if __name__ == "__main__":
    sys.exit(main())
