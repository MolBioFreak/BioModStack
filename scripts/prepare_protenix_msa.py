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

Cache policy: every protein chain is checked against the shared MSA cache
(sequence SHA-256 key) before backend selection. A full cache hit skips MSA
generation entirely and makes the requested backend irrelevant (reported as
backend "cache"). API-fetched MSAs are persisted into the shared cache so
later identical runs short-circuit; existing cache entries are never
overwritten.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_msa_runtime import (
    DEFAULT_GPUSERVER_DB_LOAD_MODE,
    DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS,
    DEFAULT_GPUSERVER_WAIT_TIMEOUT,
    resolve_protenix_local_gpu_server_mode,
)
from local_msa.batching import run_batch_msa
from local_msa.runtime import inspect_mmseqs_runtime, parse_gpu_csv

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


def write_msa_report(
    report_path: Path,
    payload: List[Dict[str, Any]],
    backend: str,
    stats: Dict[str, int],
    local_msa_runtime_contract: Dict[str, Any] | None = None,
) -> None:
    report: Dict[str, Any] = {
        "backend": backend,
        "tasks": stats["tasks"],
        "protein_chains": stats["protein_chains"],
        "unique_sequences": stats["unique_sequences"],
        "total_residues": stats["total_residues"],
        "chains": [],
    }
    if local_msa_runtime_contract:
        report["local_msa_runtime_contract"] = dict(local_msa_runtime_contract)

    for task_idx, seq_idx, chain in iter_protein_chains(payload):
        _hydrate_old_precomputed_dir(chain)
        paired_path, unpaired_path = _existing_msa_paths(chain)
        sequence = str(chain.get("sequence", "") or "").strip()
        report["chains"].append(
            {
                "task_index": task_idx,
                "sequence_index": seq_idx,
                "sequence_length": len(sequence),
                "sequence_sha256_16": hashlib.sha256(sequence.encode("utf-8")).hexdigest()[:16] if sequence else None,
                "paired_msa_path": str(paired_path.resolve()) if paired_path and paired_path.exists() else None,
                "unpaired_msa_path": str(unpaired_path.resolve()) if unpaired_path and unpaired_path.exists() else None,
                "has_paired_msa": bool(paired_path and paired_path.exists()),
                "has_unpaired_msa": bool(unpaired_path and unpaired_path.exists()),
            }
        )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _copy_or_link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _iter_a3m_records(path: Path) -> List[Tuple[str, str]]:
    records: List[Tuple[str, str]] = []
    header: str | None = None
    seq_lines: List[str] = []
    open_func = gzip.open if str(path).endswith(".gz") else open
    with open_func(path, "rt", encoding="utf-8") as handle:
        raw_lines = handle.read().splitlines()
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if header is not None:
                records.append((header, "".join(seq_lines)))
            header = line[1:] or "query"
            seq_lines = []
        else:
            seq_lines.append(line)
    if header is not None:
        records.append((header, "".join(seq_lines)))
    return records


def _cached_a3m_path(cache_root: str | Path | None, sequence: str) -> Path | None:
    if not cache_root or not sequence:
        return None
    full_hash = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    candidate = Path(cache_root) / full_hash[:2] / f"{full_hash}.a3m.gz"
    if candidate.is_file():
        return candidate
    return None


def hydrate_chains_from_shared_cache(
    payload: List[Dict[str, Any]],
    out_dir: Path,
    cache_dir: str,
    binder_chain_ids: Sequence[str] | None = None,
    binder_max_unpaired_rows: int | None = None,
    binder_min_residue_coverage_fraction: float = 0.0,
) -> int:
    """Attach cached A3Ms to protein chains whose exact sequence is already in
    the shared MSA cache. Runs before backend selection so a full cache hit
    makes the requested backend irrelevant. Returns the number of chains
    satisfied from the cache."""
    if not cache_dir:
        return 0
    cache_root = Path(cache_dir)
    if not cache_root.is_dir():
        return 0
    binder_chain_id_set = {token.strip() for token in (binder_chain_ids or []) if str(token).strip()}
    hydrated = 0
    seq_role_to_msa_dir: Dict[Tuple[str, str], Path] = {}
    for _task_idx, _seq_idx, chain in iter_protein_chains(payload):
        _hydrate_old_precomputed_dir(chain)
        paired_path, unpaired_path = _existing_msa_paths(chain)
        if (paired_path and paired_path.exists()) or (unpaired_path and unpaired_path.exists()):
            continue
        sequence = str(chain.get("sequence", "") or "").strip()
        if not sequence:
            continue
        cached = _cached_a3m_path(cache_root, sequence)
        if cached is None:
            continue
        is_binder = bool(set(_chain_ids(chain)) & binder_chain_id_set)
        role_key = "binder" if is_binder else "default"
        profile_key = (sequence, role_key)
        msa_dir = seq_role_to_msa_dir.get(profile_key)
        if msa_dir is None:
            full_hash = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
            role_suffix = "_binder" if is_binder else ""
            msa_dir = out_dir / "local_msa_cache" / f"{full_hash}{role_suffix}"
            msa_dir.mkdir(parents=True, exist_ok=True)
            non_pairing = msa_dir / "non_pairing.a3m"
            kept_records = _write_sanitized_a3m(
                cached,
                non_pairing,
                query_sequence=sequence,
                max_rows=binder_max_unpaired_rows if is_binder else None,
                min_residue_coverage_fraction=(
                    binder_min_residue_coverage_fraction if is_binder else 0.0
                ),
            )
            if is_binder and kept_records <= 8 and binder_min_residue_coverage_fraction > 0:
                kept_records = _write_sanitized_a3m(
                    cached,
                    non_pairing,
                    query_sequence=sequence,
                    max_rows=binder_max_unpaired_rows,
                    min_residue_coverage_fraction=0.0,
                )
                print(
                    f"[prepare_protenix_msa] Cached binder MSA for sequence {full_hash[:16]} was too sparse "
                    f"after coverage pruning; reused capped hits without coverage filtering ({kept_records} rows).",
                    flush=True,
                )
            (msa_dir / "pairing.a3m").write_text(f">query\n{sequence}\n", encoding="utf-8")
            seq_role_to_msa_dir[profile_key] = msa_dir
        chain["pairedMsaPath"] = str((msa_dir / "pairing.a3m").resolve())
        chain["unpairedMsaPath"] = str((msa_dir / "non_pairing.a3m").resolve())
        hydrated += 1
    return hydrated


def cache_colabfold_results(payload: List[Dict[str, Any]], cache_dir: str) -> int:
    """Persist API-fetched chain A3Ms into the shared cache so later runs skip
    the remote service. Existing cache entries are never overwritten."""
    if not cache_dir:
        return 0
    cache_root = Path(cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    persisted = 0
    for _task_idx, _seq_idx, chain in iter_protein_chains(payload):
        sequence = str(chain.get("sequence", "") or "").strip()
        if not sequence:
            continue
        _hydrate_old_precomputed_dir(chain)
        paired_path, unpaired_path = _existing_msa_paths(chain)
        source = unpaired_path or paired_path
        if source is None or not source.exists() or not source.is_file():
            continue
        full_hash = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
        dest = cache_root / full_hash[:2] / f"{full_hash}.a3m.gz"
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(dest, "wt", encoding="utf-8") as handle:
            handle.write(source.read_text(encoding="utf-8"))
        persisted += 1
    return persisted


def _aligned_a3m_length(sequence: str) -> int:
    return len(re.sub(r"[a-z]", "", sequence))


def _residue_coverage(sequence: str) -> int:
    return len(re.findall(r"[A-Z]", sequence))


def _chain_ids(chain: Dict[str, Any]) -> list[str]:
    raw = chain.get("id")
    if isinstance(raw, list):
        return [str(token).strip() for token in raw if str(token).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _write_sanitized_a3m(
    src: Path,
    dst: Path,
    *,
    query_sequence: str,
    max_rows: int | None = None,
    min_residue_coverage_fraction: float = 0.0,
) -> int:
    expected_len = len(query_sequence)
    kept: List[Tuple[str, str]] = [("query", query_sequence)]
    seen_sequences = {query_sequence}
    min_coverage = max(0.0, float(min_residue_coverage_fraction))

    for idx, (header, sequence) in enumerate(_iter_a3m_records(src)):
        if idx == 0 and header.lower() == "query":
            continue
        if _aligned_a3m_length(sequence) != expected_len:
            continue
        if expected_len > 0 and (_residue_coverage(sequence) / expected_len) < min_coverage:
            continue
        if sequence in seen_sequences:
            continue
        kept.append((header, sequence))
        seen_sequences.add(sequence)
        if max_rows is not None and len(kept) >= max(1, int(max_rows)):
            break

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as handle:
        for header, sequence in kept:
            handle.write(f">{header}\n{sequence}\n")
    return len(kept)


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
    allow_cpu_fallback: bool,
    local_msa_timeout_seconds: int,
    cache_only: bool,
) -> Path:
    script_path = Path(__file__).resolve().with_name("run_local_msa.py")
    output_dir.mkdir(parents=True, exist_ok=True)

    def build_cmd(force_cpu_only: bool) -> list[str]:
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
        if force_cpu_only:
            cmd.append("--cpu-only")
        if preferred_gpus:
            cmd.extend(["--preferred-gpus", preferred_gpus])
        if excluded_gpus:
            cmd.extend(["--excluded-gpus", excluded_gpus])
        if not allow_cpu_fallback and not force_cpu_only:
            cmd.append("--disallow-cpu-fallback")
        if cache_only:
            cmd.append("--cache-only")
        return cmd

    def run_cmd(cmd: list[str], timeout_seconds: int) -> None:
        proc = subprocess.Popen(cmd, start_new_session=True)
        try:
            if timeout_seconds and int(timeout_seconds) > 0:
                proc.wait(timeout=int(timeout_seconds))
            else:
                proc.wait()
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout_seconds) from exc
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)

    timeout_seconds = max(0, int(local_msa_timeout_seconds or 0))
    try:
        run_cmd(build_cmd(force_cpu_only=cpu_only), timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        if allow_cpu_fallback and not cpu_only:
            print(
                f"[prepare_protenix_msa] Local GPU MSA timed out after {timeout_seconds}s; retrying on CPU for this sequence.",
                flush=True,
            )
            run_cmd(build_cmd(force_cpu_only=True), timeout_seconds)
        else:
            raise RuntimeError(
                f"Local Protenix MSA timed out after {timeout_seconds}s for a sequence of length {len(sequence)}."
            ) from exc

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
    allow_cpu_fallback: bool,
    local_msa_timeout_seconds: int,
    selected_gpu_id: int | None = None,
    cache_only: bool = False,
    binder_chain_ids: Sequence[str] | None = None,
    binder_max_unpaired_rows: int | None = None,
    binder_min_residue_coverage_fraction: float = 0.0,
) -> Path:
    msa_root = work_dir / "local_msa"
    msa_root.mkdir(parents=True, exist_ok=True)
    binder_chain_id_set = {token.strip() for token in (binder_chain_ids or []) if str(token).strip()}
    raw_msa_by_sequence: Dict[str, Path] = {}
    seq_role_to_msa_dir: Dict[Tuple[str, str], Path] = {}
    sequence_to_chain_ids: Dict[str, set[str]] = {}

    missing_sequences: Dict[str, str] = {}
    for _task_idx, _seq_idx, chain in iter_protein_chains(payload):
        _hydrate_old_precomputed_dir(chain)
        paired_path, unpaired_path = _existing_msa_paths(chain)
        if (paired_path and paired_path.exists()) or (unpaired_path and unpaired_path.exists()):
            continue
        sequence = str(chain.get("sequence", "") or "").strip()
        if not sequence:
            continue
        sequence_to_chain_ids.setdefault(sequence, set()).update(_chain_ids(chain))
        missing_sequences.setdefault(sequence, hashlib.sha256(sequence.encode("utf-8")).hexdigest()[:16])

    use_true_batch_fast = preset.strip().lower() == "fast" and len(missing_sequences) > 1
    if use_true_batch_fast:
        batch_output_dir = work_dir / "local_msa_batch"
        batch_output_dir.mkdir(parents=True, exist_ok=True)
        print(
            f"[prepare_protenix_msa] Using true batched local MSA for {len(missing_sequences)} unique sequences.",
            flush=True,
        )
        sequences = [
            {"name": seq_hash, "sequence": sequence}
            for sequence, seq_hash in missing_sequences.items()
        ]
        manifest = run_batch_msa(
            sequences=sequences,
            output_dir=batch_output_dir,
            db_path=Path(db_path),
            cache_dir=Path(cache_dir) if cache_dir else None,
            gpu_id=selected_gpu_id,
            force_refresh=False,
            cache_only=cache_only,
            cpu_only=cpu_only,
            threads=threads,
            preset=preset,
            gpu_mode=gpu_mode,
            gpu_threshold=gpu_threshold,
            preferred_gpus=preferred_gpus,
            excluded_gpus=excluded_gpus,
            gpu_server_mode=gpu_server_mode,
            gpu_server_wait_timeout=gpu_server_wait_timeout,
            gpu_server_db_load_mode=gpu_server_db_load_mode,
            gpu_server_startup_wait=gpu_server_startup_wait,
        )
        manifest_map = {
            str(item.get("name")): item
            for item in manifest.get("sequences", [])
            if isinstance(item, dict) and item.get("name")
        }
        for sequence, seq_hash in missing_sequences.items():
            result = manifest_map.get(seq_hash)
            if not result or not result.get("success") or not result.get("msa_path"):
                failure_reason = None
                if isinstance(result, dict):
                    failure_reason = result.get("error")
                if cache_only:
                    raise RuntimeError(
                        f"Batched local Protenix MSA cache miss for sequence {seq_hash}: {failure_reason or 'no cached MSA found'}"
                    )
                raise RuntimeError(
                    f"Batched local Protenix MSA failed for sequence {seq_hash}: {failure_reason or 'unknown error'}"
                )
            raw_msa_by_sequence[sequence] = Path(str(result["msa_path"])).resolve()

    for _task_idx, _seq_idx, chain in iter_protein_chains(payload):
        _hydrate_old_precomputed_dir(chain)
        paired_path, unpaired_path = _existing_msa_paths(chain)
        if (paired_path and paired_path.exists()) or (unpaired_path and unpaired_path.exists()):
            continue

        sequence = str(chain.get("sequence", "") or "").strip()
        if not sequence:
            continue
        chain_id_set = set(_chain_ids(chain))
        role_key = "binder" if chain_id_set & binder_chain_id_set else "default"
        profile_key = (sequence, role_key)

        if profile_key not in seq_role_to_msa_dir:
            seq_hash = hashlib.sha256(sequence.encode("utf-8")).hexdigest()[:16]
            role_suffix = "_binder" if role_key == "binder" else ""
            msa_dir = msa_root / f"{seq_hash}{role_suffix}"
            msa_dir.mkdir(parents=True, exist_ok=True)
            query_a3m = raw_msa_by_sequence.get(sequence)
            if query_a3m is None:
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
                    allow_cpu_fallback=allow_cpu_fallback,
                    local_msa_timeout_seconds=local_msa_timeout_seconds,
                    cache_only=cache_only,
                )
                raw_msa_by_sequence[sequence] = query_a3m
            non_pairing = msa_dir / "non_pairing.a3m"
            pairing = msa_dir / "pairing.a3m"
            is_binder = role_key == "binder"
            kept_records = _write_sanitized_a3m(
                query_a3m,
                non_pairing,
                query_sequence=sequence,
                max_rows=binder_max_unpaired_rows if is_binder else None,
                min_residue_coverage_fraction=binder_min_residue_coverage_fraction if is_binder else 0.0,
            )
            if is_binder and kept_records <= 8 and binder_min_residue_coverage_fraction > 0:
                kept_records = _write_sanitized_a3m(
                    query_a3m,
                    non_pairing,
                    query_sequence=sequence,
                    max_rows=binder_max_unpaired_rows,
                    min_residue_coverage_fraction=0.0,
                )
                print(
                    f"[prepare_protenix_msa] Binder MSA for sequence {seq_hash} was too sparse after coverage pruning; "
                    f"reused capped hits without coverage filtering ({kept_records} rows).",
                    flush=True,
                )
            pairing.write_text(f">query\n{sequence}\n", encoding="utf-8")
            seq_role_to_msa_dir[profile_key] = msa_dir.resolve()

        msa_dir = seq_role_to_msa_dir[profile_key]
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
    parser.add_argument("--gpu-server-wait-timeout", type=int, default=DEFAULT_GPUSERVER_WAIT_TIMEOUT, help="Local MSA gpuserver wait timeout")
    parser.add_argument("--gpu-server-db-load-mode", type=int, default=DEFAULT_GPUSERVER_DB_LOAD_MODE, help="Local MSA gpuserver db load mode")
    parser.add_argument("--gpu-server-startup-wait", type=float, default=DEFAULT_GPUSERVER_STARTUP_WAIT_SECONDS, help="Local MSA gpuserver startup wait")
    parser.add_argument("--allow-cpu-fallback", action="store_true", help="Allow CPU MMseqs when GPU MMseqs is unavailable")
    parser.add_argument("--cache-only", action="store_true", help="Use only cached MSAs; fail on any cache miss")
    parser.add_argument("--local-msa-timeout-seconds", type=int, default=900, help="Kill and fail a local per-sequence MSA attempt after this many seconds (0 disables)")
    parser.add_argument("--report_json", default="", help="Optional JSON report path summarizing prepared MSA inputs")
    parser.add_argument("--small-max-tasks", type=int, default=DEFAULT_SMALL_MAX_TASKS, help="Auto mode ColabFold API max task count")
    parser.add_argument("--small-max-protein-chains", type=int, default=DEFAULT_SMALL_MAX_PROTEIN_CHAINS, help="Auto mode ColabFold API max protein chains")
    parser.add_argument("--small-max-total-residues", type=int, default=DEFAULT_SMALL_MAX_TOTAL_RESIDUES, help="Auto mode ColabFold API max total residues")
    parser.add_argument("--binder-chain-ids", default="", help="Comma-separated binder chain IDs that should receive aggressive MSA pruning")
    parser.add_argument("--binder-max-unpaired-msa-rows", type=int, default=256, help="Maximum unpaired MSA rows to keep for binder chains (including query)")
    parser.add_argument("--binder-min-residue-coverage", type=float, default=0.5, help="Minimum residue coverage fraction for binder-chain A3M rows")
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
        if args.report_json:
            write_msa_report(Path(args.report_json).expanduser().resolve(), payload, "none", stats)
        print(f"[prepare_protenix_msa] No protein chains found; copied input to {output_json}", flush=True)
        print(str(output_json), flush=True)
        return

    hydrated_from_cache = 0
    if args.cache_dir:
        hydrated_from_cache = hydrate_chains_from_shared_cache(
            payload,
            out_dir,
            args.cache_dir,
            binder_chain_ids=[token.strip() for token in str(args.binder_chain_ids or "").split(",") if token.strip()],
            binder_max_unpaired_rows=(int(args.binder_max_unpaired_msa_rows) if int(args.binder_max_unpaired_msa_rows) > 0 else None),
            binder_min_residue_coverage_fraction=float(args.binder_min_residue_coverage),
        )
        if hydrated_from_cache:
            print(
                f"[prepare_protenix_msa] Shared-cache hydration: {hydrated_from_cache}/{stats['protein_chains']} protein chains",
                flush=True,
            )

    if all_protein_chains_have_msa(payload):
        dump_json(output_json, payload)
        backend_label = "cache" if hydrated_from_cache else "precomputed"
        if args.report_json:
            write_msa_report(Path(args.report_json).expanduser().resolve(), payload, backend_label, stats)
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

    local_msa_runtime_contract: Dict[str, Any] | None = None
    if backend == "colabfold_api":
        prepared = prepare_with_colabfold_api(
            input_json=input_json,
            output_json=output_json,
            work_dir=out_dir,
            host=args.colabfold_api_host,
        )
        if args.cache_dir:
            persisted = cache_colabfold_results(load_json(Path(prepared)), args.cache_dir)
            if persisted:
                print(
                    f"[prepare_protenix_msa] Persisted {persisted} API-fetched MSAs into shared cache",
                    flush=True,
                )
    else:
        if not args.db_path:
            raise ValueError("Local Protenix MSA preparation requires --db-path")
        if not args.cache_dir:
            raise ValueError("Local Protenix MSA preparation requires --cache-dir")

        local_msa_runtime_contract = resolve_protenix_local_gpu_server_mode(args.gpu_server_mode)
        requested_gpu_server_mode = local_msa_runtime_contract["requested_gpu_server_mode"]
        resolved_gpu_server_mode = local_msa_runtime_contract["effective_gpu_server_mode"]
        if requested_gpu_server_mode != resolved_gpu_server_mode:
            print(
                "[prepare_protenix_msa] Forcing gpu-server-mode=off for local Protenix MSA to avoid persistent gpuserver handshake stalls.",
                flush=True,
            )

        runtime = inspect_mmseqs_runtime(
            db_path=args.db_path,
            cache_dir=args.cache_dir,
            cpu_only=bool(args.cpu_only),
            gpu_mode=str(args.gpu_mode or "auto"),
            gpu_threshold=int(args.gpu_threshold),
            preferred_gpus=parse_gpu_csv(args.preferred_gpus),
            excluded_gpus=parse_gpu_csv(args.excluded_gpus),
            gpu_server_mode=resolved_gpu_server_mode,
            gpu_server_wait_timeout=int(args.gpu_server_wait_timeout),
            gpu_server_db_load_mode=int(args.gpu_server_db_load_mode),
            gpu_server_startup_wait=float(args.gpu_server_startup_wait),
            verbose=True,
        )
        if runtime.get("selected_gpu_id") is not None:
            local_msa_runtime_contract["selected_gpu_id"] = runtime.get("selected_gpu_id")
        if not bool(runtime.get("use_gpu_mmseqs")) and not bool(args.allow_cpu_fallback):
            failure_message = str(
                runtime.get("failure_message")
                or runtime.get("summary_message")
                or "GPU MMseqs unavailable"
            )
            raise RuntimeError(
                f"{failure_message}. Local Protenix MSA will not continue on CPU without explicit approval."
            )
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
            gpu_server_mode=resolved_gpu_server_mode,
            gpu_server_wait_timeout=int(args.gpu_server_wait_timeout),
            gpu_server_db_load_mode=int(args.gpu_server_db_load_mode),
            gpu_server_startup_wait=float(args.gpu_server_startup_wait),
            allow_cpu_fallback=bool(args.allow_cpu_fallback),
            local_msa_timeout_seconds=int(args.local_msa_timeout_seconds),
            selected_gpu_id=runtime.get("selected_gpu_id"),
            cache_only=bool(args.cache_only),
            binder_chain_ids=[token.strip() for token in str(args.binder_chain_ids or "").split(",") if token.strip()],
            binder_max_unpaired_rows=(int(args.binder_max_unpaired_msa_rows) if int(args.binder_max_unpaired_msa_rows) > 0 else None),
            binder_min_residue_coverage_fraction=float(args.binder_min_residue_coverage),
        )

    if args.report_json:
        prepared_payload = load_json(Path(prepared))
        write_msa_report(
            Path(args.report_json).expanduser().resolve(),
            prepared_payload,
            backend,
            stats,
            local_msa_runtime_contract=local_msa_runtime_contract if backend == "local" else None,
        )

    print(f"[prepare_protenix_msa] Prepared input JSON: {prepared}", flush=True)
    print(str(prepared), flush=True)


if __name__ == "__main__":
    main()
