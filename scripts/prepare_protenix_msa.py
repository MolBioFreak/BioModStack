#!/usr/bin/env python3
"""Prepare Protenix-compatible MSA paths before inference.

This script resolves MSA inputs up front so Protenix does not need to enter its
internal web-service polling path during `protenix pred`.

Backends:
- auto: use ColabFold API-compatible Protenix conversion for small jobs,
  otherwise use local BMS MSA generation per protein chain.
- colabfold_api: use Protenix's built-in ColabFold-compatible request parser to
  generate pairing/non_pairing A3Ms.
- local: use BioModStack local MSA generation and attach per-chain unpaired
  MSAs plus query-only pairing placeholders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

DEFAULT_COLABFOLD_API_HOST = os.getenv("BMS_COLABFOLD_API_HOST") or "https://api.colabfold.com"
DEFAULT_SMALL_MAX_TASKS = 1
DEFAULT_SMALL_MAX_PROTEIN_CHAINS = 4
DEFAULT_SMALL_MAX_TOTAL_RESIDUES = 1500


def load_json(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected top-level list in {path}")
    return data


def dump_json(path: Path, payload: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def iter_protein_chains(payload: List[Dict[str, Any]]) -> Iterable[Tuple[int, int, Dict[str, Any]]]:
    for task_idx, task in enumerate(payload):
        sequences = task.get("sequences", [])
        if not isinstance(sequences, list):
            continue
        for seq_idx, wrapper in enumerate(sequences):
            if not isinstance(wrapper, dict):
                continue
            chain = wrapper.get("proteinChain")
            if isinstance(chain, dict):
                yield task_idx, seq_idx, chain


def summarize_payload(payload: List[Dict[str, Any]]) -> Dict[str, int]:
    protein_chains = 0
    total_residues = 0
    unique_sequences = set()
    for _task_idx, _seq_idx, chain in iter_protein_chains(payload):
        sequence = str(chain.get("sequence", "") or "").strip()
        if not sequence:
            continue
        protein_chains += 1
        total_residues += len(sequence)
        unique_sequences.add(sequence)
    return {
        "tasks": len(payload),
        "protein_chains": protein_chains,
        "unique_sequences": len(unique_sequences),
        "total_residues": total_residues,
    }


def _existing_msa_paths(chain: Dict[str, Any]) -> Tuple[Path | None, Path | None]:
    paired = chain.get("pairedMsaPath")
    unpaired = chain.get("unpairedMsaPath")
    paired_path = Path(paired) if isinstance(paired, str) and paired.strip() else None
    unpaired_path = Path(unpaired) if isinstance(unpaired, str) and unpaired.strip() else None
    return paired_path, unpaired_path


def _hydrate_old_precomputed_dir(chain: Dict[str, Any]) -> None:
    msa_info = chain.get("msa")
    if not isinstance(msa_info, dict):
        return
    precomputed_dir = msa_info.get("precomputed_msa_dir")
    if not isinstance(precomputed_dir, str) or not precomputed_dir.strip():
        return
    precomputed_path = Path(precomputed_dir)
    pairing = precomputed_path / "pairing.a3m"
    non_pairing = precomputed_path / "non_pairing.a3m"
    if pairing.exists():
        chain["pairedMsaPath"] = str(pairing.resolve())
    if non_pairing.exists():
        chain["unpairedMsaPath"] = str(non_pairing.resolve())


def all_protein_chains_have_msa(payload: List[Dict[str, Any]]) -> bool:
    saw_protein = False
    for _task_idx, _seq_idx, chain in iter_protein_chains(payload):
        saw_protein = True
        _hydrate_old_precomputed_dir(chain)
        paired_path, unpaired_path = _existing_msa_paths(chain)
        if paired_path and paired_path.exists():
            continue
        if unpaired_path and unpaired_path.exists():
            continue
        return False
    return saw_protein


def choose_backend(requested: str, stats: Dict[str, int], max_tasks: int, max_chains: int, max_residues: int) -> str:
    normalized = (requested or "auto").strip().lower()
    if normalized in {"local", "colabfold_api"}:
        return normalized
    if normalized != "auto":
        raise ValueError(f"Unsupported backend '{requested}'")
    if (
        stats["tasks"] <= max_tasks
        and stats["protein_chains"] <= max_chains
        and stats["total_residues"] <= max_residues
    ):
        return "colabfold_api"
    return "local"


def _copy_or_link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def prepare_with_colabfold_api(input_json: Path, output_json: Path, work_dir: Path, host: str) -> Path:
    os.environ["MMSEQS_SERVICE_HOST_URL"] = (host or DEFAULT_COLABFOLD_API_HOST).strip()
    try:
        from runner.msa_search import update_infer_json
    except Exception as exc:
        raise RuntimeError(f"Could not import Protenix runner.msa_search inside container: {exc}") from exc

    updated_json, _actual_updated = update_infer_json(
        str(input_json),
        str(work_dir),
        use_msa=True,
        mode="colabfold",
    )
    updated_path = Path(updated_json).resolve()
    _copy_or_link(updated_path, output_json)
    return output_json.resolve()


def _run_local_msa(
    sequence: str,
    output_dir: Path,
    db_path: str,
    cache_dir: str,
    threads: int,
    preset: str,
    cpu_only: bool,
    gpu_mode: str,
    gpu_threshold: int,
    preferred_gpus: str | None,
    excluded_gpus: str | None,
    gpu_server_mode: str,
    gpu_server_wait_timeout: int,
    gpu_server_db_load_mode: int,
    gpu_server_startup_wait: float,
) -> Path:
    script_path = Path(__file__).resolve().with_name("run_local_msa.py")
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script_path),
        "--sequence",
        sequence,
        "--name",
        "query",
        "--out_dir",
        str(output_dir),
        "--db_path",
        db_path,
        "--cache_dir",
        cache_dir,
        "--threads",
        str(max(1, int(threads))),
        "--preset",
        preset,
        "--msa-provider",
        "local",
        "--gpu-mode",
        gpu_mode,
        "--gpu-threshold",
        str(int(gpu_threshold)),
        "--gpu-server-mode",
        gpu_server_mode,
        "--gpu-server-wait-timeout",
        str(int(gpu_server_wait_timeout)),
        "--gpu-server-db-load-mode",
        str(int(gpu_server_db_load_mode)),
        "--gpu-server-startup-wait",
        str(float(gpu_server_startup_wait)),
    ]
    if cpu_only:
        cmd.append("--cpu-only")
    if preferred_gpus:
        cmd.extend(["--preferred-gpus", preferred_gpus])
    if excluded_gpus:
        cmd.extend(["--excluded-gpus", excluded_gpus])

    subprocess.run(cmd, check=True)

    msa_path = output_dir / "query.a3m"
    if not msa_path.exists():
        raise FileNotFoundError(f"Local MSA generation did not create {msa_path}")
    return msa_path.resolve()


def prepare_with_local_msa(
    payload: List[Dict[str, Any]],
    output_json: Path,
    work_dir: Path,
    db_path: str,
    cache_dir: str,
    threads: int,
    preset: str,
    cpu_only: bool,
    gpu_mode: str,
    gpu_threshold: int,
    preferred_gpus: str | None,
    excluded_gpus: str | None,
    gpu_server_mode: str,
    gpu_server_wait_timeout: int,
    gpu_server_db_load_mode: int,
    gpu_server_startup_wait: float,
) -> Path:
    msa_root = work_dir / "local_msa"
    msa_root.mkdir(parents=True, exist_ok=True)
    seq_to_msa_dir: Dict[str, Path] = {}

    for _task_idx, _seq_idx, chain in iter_protein_chains(payload):
        _hydrate_old_precomputed_dir(chain)
        paired_path, unpaired_path = _existing_msa_paths(chain)
        if (paired_path and paired_path.exists()) or (unpaired_path and unpaired_path.exists()):
            continue

        sequence = str(chain.get("sequence", "") or "").strip()
        if not sequence:
            continue
        if sequence in seq_to_msa_dir:
            msa_dir = seq_to_msa_dir[sequence]
        else:
            seq_hash = hashlib.sha256(sequence.encode("utf-8")).hexdigest()[:16]
            msa_dir = msa_root / seq_hash
            msa_dir.mkdir(parents=True, exist_ok=True)
            query_a3m = _run_local_msa(
                sequence=sequence,
                output_dir=msa_dir,
                db_path=db_path,
                cache_dir=cache_dir,
                threads=threads,
                preset=preset,
                cpu_only=cpu_only,
                gpu_mode=gpu_mode,
                gpu_threshold=gpu_threshold,
                preferred_gpus=preferred_gpus,
                excluded_gpus=excluded_gpus,
                gpu_server_mode=gpu_server_mode,
                gpu_server_wait_timeout=gpu_server_wait_timeout,
                gpu_server_db_load_mode=gpu_server_db_load_mode,
                gpu_server_startup_wait=gpu_server_startup_wait,
            )
            non_pairing = msa_dir / "non_pairing.a3m"
            pairing = msa_dir / "pairing.a3m"
            _copy_or_link(query_a3m, non_pairing)
            pairing.write_text(f">query\n{sequence}\n", encoding="utf-8")
            seq_to_msa_dir[sequence] = msa_dir.resolve()

        chain["pairedMsaPath"] = str((msa_dir / "pairing.a3m").resolve())
        chain["unpairedMsaPath"] = str((msa_dir / "non_pairing.a3m").resolve())

    dump_json(output_json, payload)
    return output_json.resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare Protenix-compatible MSA inputs")
    parser.add_argument("--input_json", required=True, help="Input Protenix JSON")
    parser.add_argument("--output_json", required=True, help="Output JSON with MSA paths")
    parser.add_argument("--out_dir", required=True, help="Working directory for MSA artifacts")
    parser.add_argument("--backend", default="auto", choices=["auto", "colabfold_api", "local"], help="MSA backend")
    parser.add_argument("--colabfold-api-host", default=DEFAULT_COLABFOLD_API_HOST, help="ColabFold API host")
    parser.add_argument("--db-path", default=os.getenv("BMS_COLABFOLD_DB") or "", help="Local ColabFold DB path")
    parser.add_argument("--cache-dir", default=os.getenv("BMS_MSA_CACHE") or "", help="MSA cache directory")
    parser.add_argument("--threads", type=int, default=32, help="Threads for local MSA")
    parser.add_argument("--preset", default="fast", choices=["maximum", "balanced", "fast"], help="Local MSA preset")
    parser.add_argument("--cpu-only", action="store_true", help="Force CPU-only local MSA")
    parser.add_argument("--gpu-mode", default="auto", help="Local MSA GPU mode")
    parser.add_argument("--gpu-threshold", type=int, default=80, help="Local MSA GPU threshold")
    parser.add_argument("--preferred-gpus", default=None, help="Preferred GPU CSV for local MSA")
    parser.add_argument("--excluded-gpus", default=None, help="Excluded GPU CSV for local MSA")
    parser.add_argument("--gpu-server-mode", default="persistent", help="Local MSA gpuserver mode")
    parser.add_argument("--gpu-server-wait-timeout", type=int, default=120, help="Local MSA gpuserver wait timeout")
    parser.add_argument("--gpu-server-db-load-mode", type=int, default=0, help="Local MSA gpuserver db load mode")
    parser.add_argument("--gpu-server-startup-wait", type=float, default=1.0, help="Local MSA gpuserver startup wait")
    parser.add_argument("--small-max-tasks", type=int, default=DEFAULT_SMALL_MAX_TASKS, help="Auto mode ColabFold API max task count")
    parser.add_argument("--small-max-protein-chains", type=int, default=DEFAULT_SMALL_MAX_PROTEIN_CHAINS, help="Auto mode ColabFold API max protein chains")
    parser.add_argument("--small-max-total-residues", type=int, default=DEFAULT_SMALL_MAX_TOTAL_RESIDUES, help="Auto mode ColabFold API max total residues")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_json = Path(args.input_json).expanduser().resolve()
    output_json = Path(args.output_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = load_json(input_json)
    stats = summarize_payload(payload)
    print(
        "[prepare_protenix_msa] input stats: "
        f"tasks={stats['tasks']} protein_chains={stats['protein_chains']} "
        f"unique_sequences={stats['unique_sequences']} total_residues={stats['total_residues']}",
        flush=True,
    )

    if stats["protein_chains"] == 0:
        dump_json(output_json, payload)
        print(f"[prepare_protenix_msa] No protein chains found; copied input to {output_json}", flush=True)
        print(str(output_json), flush=True)
        return

    if all_protein_chains_have_msa(payload):
        dump_json(output_json, payload)
        print(f"[prepare_protenix_msa] Existing MSA paths detected; reusing input via {output_json}", flush=True)
        print(str(output_json), flush=True)
        return

    backend = choose_backend(
        requested=args.backend,
        stats=stats,
        max_tasks=max(1, args.small_max_tasks),
        max_chains=max(1, args.small_max_protein_chains),
        max_residues=max(1, args.small_max_total_residues),
    )
    print(f"[prepare_protenix_msa] Selected backend: {backend}", flush=True)

    if backend == "colabfold_api":
        prepared = prepare_with_colabfold_api(
            input_json=input_json,
            output_json=output_json,
            work_dir=out_dir,
            host=args.colabfold_api_host,
        )
    else:
        if not args.db_path:
            raise ValueError("Local Protenix MSA preparation requires --db-path")
        if not args.cache_dir:
            raise ValueError("Local Protenix MSA preparation requires --cache-dir")
        prepared = prepare_with_local_msa(
            payload=payload,
            output_json=output_json,
            work_dir=out_dir,
            db_path=args.db_path,
            cache_dir=args.cache_dir,
            threads=args.threads,
            preset=args.preset,
            cpu_only=bool(args.cpu_only),
            gpu_mode=str(args.gpu_mode or "auto"),
            gpu_threshold=int(args.gpu_threshold),
            preferred_gpus=args.preferred_gpus,
            excluded_gpus=args.excluded_gpus,
            gpu_server_mode=str(args.gpu_server_mode or "persistent"),
            gpu_server_wait_timeout=int(args.gpu_server_wait_timeout),
            gpu_server_db_load_mode=int(args.gpu_server_db_load_mode),
            gpu_server_startup_wait=float(args.gpu_server_startup_wait),
        )

    print(f"[prepare_protenix_msa] Prepared input JSON: {prepared}", flush=True)
    print(str(prepared), flush=True)


if __name__ == "__main__":
    main()
