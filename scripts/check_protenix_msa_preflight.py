#!/usr/bin/env python3
"""Preflight the Protenix MSA path and decide whether GPU MMseqs is ready."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from local_msa_runtime import (
    DEFAULT_GPUSERVER_DB_LOAD_MODE,
    DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    DEFAULT_GPUSERVER_WAIT_TIMEOUT,
)
from prepare_protenix_msa import choose_backend, load_json, summarize_payload
from run_local_msa import inspect_mmseqs_runtime, parse_gpu_csv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check whether Protenix local MSA can run on GPU MMseqs")
    parser.add_argument("--input_json", required=True, help="Protenix-format input JSON")
    parser.add_argument("--output", required=True, help="Output JSON report")
    parser.add_argument("--backend", default="auto", choices=["auto", "colabfold_api", "local"], help="Requested MSA backend")
    parser.add_argument("--db-path", default="", help="Local ColabFold DB path")
    parser.add_argument("--cache-dir", default="", help="MSA cache directory")
    parser.add_argument("--cpu-only", action="store_true", help="Force CPU-only local MSA")
    parser.add_argument("--gpu-mode", default="auto", help="GPU mode passed to local MSA")
    parser.add_argument("--gpu-threshold", type=int, default=80, help="GPU threshold passed to local MSA")
    parser.add_argument("--preferred-gpus", default=None, help="Preferred GPU CSV")
    parser.add_argument("--excluded-gpus", default=None, help="Excluded GPU CSV")
    parser.add_argument("--gpu-server-mode", default="persistent", help="GPU server mode")
    parser.add_argument("--gpu-server-wait-timeout", type=int, default=DEFAULT_GPUSERVER_WAIT_TIMEOUT, help="GPU server wait timeout")
    parser.add_argument("--gpu-server-db-load-mode", type=int, default=DEFAULT_GPUSERVER_DB_LOAD_MODE, help="GPU server db load mode")
    parser.add_argument("--gpu-server-startup-wait", type=float, default=DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS, help="GPU server startup wait")
    parser.add_argument("--small-max-tasks", type=int, default=1, help="Auto-mode ColabFold API max task count")
    parser.add_argument("--small-max-protein-chains", type=int, default=4, help="Auto-mode ColabFold API max protein chain count")
    parser.add_argument("--small-max-total-residues", type=int, default=1500, help="Auto-mode ColabFold API residue cutoff")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_json = Path(args.input_json).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    payload = load_json(input_json)
    stats = summarize_payload(payload)
    backend = choose_backend(
        requested=args.backend,
        stats=stats,
        max_tasks=max(1, int(args.small_max_tasks)),
        max_chains=max(1, int(args.small_max_protein_chains)),
        max_residues=max(1, int(args.small_max_total_residues)),
    )

    report: dict[str, object] = {
        "allow_validation": True,
        "backend": backend,
        "stats": stats,
        "resume_direct": True,
        "resume_from_stage": "structure_validation",
        "resume_param_overrides": {
            "protenix_allow_cpu_msa_fallback": True,
        },
    }

    if backend == "local":
        if not str(args.db_path or "").strip() or not str(args.cache_dir or "").strip():
            runtime = {
                "status": "local_msa_config_missing",
                "use_gpu_mmseqs": False,
                "failure_message": "Local Protenix MSA is selected but the local MMseqs DB/cache paths are not configured.",
                "effective_preferred_gpus": [],
                "effective_excluded_gpus": [],
                "selected_gpu_id": None,
            }
        else:
            runtime = inspect_mmseqs_runtime(
                db_path=args.db_path,
                cache_dir=args.cache_dir,
                cpu_only=bool(args.cpu_only),
                gpu_mode=str(args.gpu_mode or "auto"),
                gpu_threshold=int(args.gpu_threshold),
                preferred_gpus=parse_gpu_csv(args.preferred_gpus),
                excluded_gpus=parse_gpu_csv(args.excluded_gpus),
                gpu_server_mode=str(args.gpu_server_mode or "persistent"),
                gpu_server_wait_timeout=int(args.gpu_server_wait_timeout),
                gpu_server_db_load_mode=int(args.gpu_server_db_load_mode),
                gpu_server_startup_wait=float(args.gpu_server_startup_wait),
                verbose=False,
            )
        report["local_msa_runtime"] = runtime
        report["gpu_mmseqs_ready"] = bool(runtime.get("use_gpu_mmseqs"))
        if not report["gpu_mmseqs_ready"]:
            reason = str(
                runtime.get("failure_message")
                or runtime.get("summary_message")
                or "GPU MMseqs is not enabled for this Protenix validation batch."
            )
            preferred = runtime.get("effective_preferred_gpus") or []
            excluded = runtime.get("effective_excluded_gpus") or []
            detail_lines = [
                f"Selected backend: {backend}",
                f"Reason: {reason}",
                f"Preferred GPUs: {preferred or 'auto'}",
                f"Excluded GPUs: {excluded or 'none'}",
            ]
            if runtime.get("selected_gpu_id") is not None:
                detail_lines.append(f"Selected GPU candidate: {runtime['selected_gpu_id']}")
            report.update(
                {
                    "allow_validation": False,
                    "prompt_kind": "protenix_msa_gpu_preflight",
                    "title": "GPU MMseqs unavailable for Protenix MSA",
                    "message": "This Protenix validation batch would fall back to CPU local MSA. The run is paused so you can either continue on CPU explicitly or leave it paused.",
                    "detail_lines": detail_lines,
                    "continue_label": "Continue On CPU",
                }
            )
    else:
        report["gpu_mmseqs_ready"] = False
        report["detail_lines"] = [f"Selected backend: {backend}"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
