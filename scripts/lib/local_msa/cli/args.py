from __future__ import annotations

import argparse

from ..config import DEFAULT_CACHE_DIR, DEFAULT_COLABFOLD_API_HOST, DEFAULT_DB_PATH
from ..gpuserver import (
    DEFAULT_GPUSERVER_DB_LOAD_MODE,
    DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    DEFAULT_GPUSERVER_WAIT_TIMEOUT,
)

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate MSA using full ColabFold workflow (GPU/CPU hybrid)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quality Presets:
  maximum   Full ColabFold workflow with environmental DB (~15-30s)
  balanced  Environmental search without expansion (~8-15s) [DEFAULT]
  fast      UniRef30 only, minimal processing (~3-5s)
"""
    )
    parser.add_argument("--sequence", required=True, help="Amino acid sequence")
    parser.add_argument("--name", required=True, help="Job name for output files")
    parser.add_argument("--out_dir", required=True, help="Output directory")
    parser.add_argument("--db_path",
                        default=DEFAULT_DB_PATH,
                        help="Path to ColabFold database directory")
    parser.add_argument("--cache_dir",
                        default=DEFAULT_CACHE_DIR,
                        help="Cache directory")
    parser.add_argument("--max_age_days", type=int, default=0,
                        help="Cache expiry in days (0 = never expire)")
    parser.add_argument("--force_refresh", action="store_true",
                        help="Bypass cache")
    parser.add_argument("--cache-only", action="store_true",
                        help="Use only existing cache; fail if cache is missing")
    parser.add_argument("--threads", type=int, default=32,
                        help="CPU threads for MMseqs2")
    parser.add_argument("--use-gpu", action="store_true",
                        help="Force GPU mode")
    parser.add_argument("--gpu-id", type=int, default=None,
                        help="Specific GPU device ID")
    parser.add_argument("--cpu-only", action="store_true",
                        help="Force CPU mode (no GPU)")
    parser.add_argument("--gpu-mode", type=str, default="auto",
                        choices=["auto", "opportunistic", "required", "cpu"],
                        help="GPU policy: auto|opportunistic|required|cpu")
    parser.add_argument("--gpu-threshold", type=int, default=80,
                        help="Max util/memory %% for opportunistic GPU selection (default: 80)")
    parser.add_argument("--preferred-gpus", type=str, default=None,
                        help="Comma-separated preferred GPU IDs for MSA (e.g., 1,2)")
    parser.add_argument("--excluded-gpus", type=str, default=None,
                        help="Comma-separated GPU IDs to avoid for MSA (e.g., 0)")
    parser.add_argument("--gpu-server-mode", type=str, default="persistent",
                        choices=["auto", "required", "persistent", "off"],
                        help="MMseqs gpuserver policy: persistent|auto|required|off")
    parser.add_argument("--gpu-server-wait-timeout", type=int, default=DEFAULT_GPUSERVER_WAIT_TIMEOUT,
                        help="Seconds to wait for gpuserver handshake (0=no wait, -1=infinite)")
    parser.add_argument(
        "--gpu-server-db-load-mode",
        type=int,
        default=DEFAULT_GPUSERVER_DB_LOAD_MODE,
        choices=[0, 1, 2, 3],
        help=f"MMseqs db-load-mode for gpuserver-backed searches (default: {DEFAULT_GPUSERVER_DB_LOAD_MODE})",
    )
    parser.add_argument("--gpu-server-startup-wait", type=float, default=DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
                        help="Seconds to wait after starting gpuserver before first search")
    parser.add_argument("--disallow-cpu-fallback", action="store_true",
                        help="Fail instead of falling back to CPU MMseqs when GPU MMseqs is unavailable")
    parser.add_argument("--msa-provider", type=str, default="local",
                        choices=["local", "colabfold_api"],
                        help="MSA backend provider: local MMseqs2 or remote ColabFold API")
    parser.add_argument("--colabfold-api-host", type=str, default=DEFAULT_COLABFOLD_API_HOST,
                        help="ColabFold API host URL (default: https://api.colabfold.com)")
    parser.add_argument("--colabfold-api-min-interval", type=float, default=6.0,
                        help="Minimum seconds between remote ColabFold API submits")
    parser.add_argument("--colabfold-api-poll-interval", type=float, default=6.0,
                        help="Polling interval seconds for remote ColabFold API ticket status")
    parser.add_argument("--reference-sequence", type=str, default=None,
                        help="Reference sequence for cache key (mutagenesis mode)")

    # Quality Presets
    parser.add_argument("--preset", type=str, default="balanced",
                        choices=["maximum", "balanced", "fast"],
                        help="MSA quality preset (default: balanced)")

    # Override parameters
    parser.add_argument("--num-iterations", type=int, default=None,
                        help="Override: number of profile iterations")
    parser.add_argument("--use-env", type=int, default=None, choices=[0, 1],
                        help="Override: use environmental database")
    parser.add_argument("--use-expand", type=int, default=None, choices=[0, 1],
                        help="Override: use alignment expansion")
    parser.add_argument("--use-filter", type=int, default=None, choices=[0, 1],
                        help="Override: use quality filtering")

    # Legacy parameters (backward compat)
    parser.add_argument("--evalue", type=float, default=None,
                        help="Override: E-value threshold")
    parser.add_argument("--sensitivity", type=float, default=None,
                        help="Override: MMseqs2 sensitivity (1-8)")
    parser.add_argument("--max-seqs", type=int, default=None,
                        help="Override: maximum candidate sequences to retain")
    parser.add_argument("--min-seq-id", type=float, default=None,
                        help="Minimum sequence identity (0-1.0)")
    parser.add_argument("--min-coverage", type=float, default=None,
                        help="Minimum query coverage (0-1.0)")
    parser.add_argument("--taxon-list", type=str, default=None,
                        help="NCBI taxonomy IDs to filter (comma-separated)")
    parser.add_argument("--min-depth-warning", type=int, default=100,
                        help="Warn if MSA has fewer sequences (default: 100)")
    parser.add_argument("--min-depth-fail", type=int, default=0,
                        help="Fail if MSA has fewer sequences (0 = no fail)")
    parser.add_argument("--fast-env-fallback-min-depth", type=int, default=25,
                        help="For preset=fast with use_env disabled, auto-run EnvDB when UniRef depth is below this (0 disables fallback)")
    return parser
