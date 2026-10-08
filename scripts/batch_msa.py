#!/usr/bin/env python3
"""
True Batch MSA Generation Script

Generates MSAs for multiple sequences in a SINGLE mmseqs search operation.
This keeps the GPU loaded and scans the database once for all queries.

!!! DEPRECATION WARNING !!!
This script will be consolidated into run_local_msa.py --batch-json in a future release.
For now, it continues to work but users should plan to migrate.

Workflow:
1. Write all sequences to ONE query FASTA
2. mmseqs createdb (one query database)
3. mmseqs search with --gpu 1 (single GPU-accelerated search)
4. mmseqs result2msa (generate MSAs)
5. mmseqs unpackdb (split into individual A3M files)

Usage:
    python3 batch_msa.py --sequences '[{"name": "seq1", "sequence": "MKTAY..."}]' \
                         --output_dir ./msa_outputs \
                         --db_path "$BMS_COLABFOLD_DB" \
                         --preset balanced
"""

import warnings
warnings.warn(
    "batch_msa.py is deprecated. Future: use run_local_msa.py --batch-json",
    DeprecationWarning,
    stacklevel=2
)

import argparse
import hashlib
import json
import os
import subprocess
import sys
import gzip
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional

_default_data_root = Path(os.path.expanduser(os.getenv("BMS_DATA") or "~/.biomodstack"))
DEFAULT_DB_PATH = os.getenv("BMS_COLABFOLD_DB") or str(_default_data_root / "colabfold_db")
DEFAULT_CACHE_DIR = os.getenv("BMS_MSA_CACHE") or str(_default_data_root / "msa_cache")


def compute_sequence_hash(sequence: str) -> str:
    """Compute SHA256 hash of sequence for cache key."""
    return hashlib.sha256(sequence.encode()).hexdigest()


def _cached_depth(cache_path: Path) -> Optional[int]:
    """Count sequences in cached A3M.gz."""
    try:
        depth = 0
        with gzip.open(cache_path, 'rt', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if line.startswith(">"):
                    depth += 1
        return depth
    except Exception:
        return None


def _cleanup_profile_caches(cache_dir: Path, seq_hash: str) -> int:
    """Remove profile-scoped cache files for a sequence hash."""
    cache_subdir = cache_dir / seq_hash[:2]
    if not cache_subdir.exists():
        return 0
    removed = 0
    for profile_file in cache_subdir.glob(f"{seq_hash}_*.a3m.gz"):
        try:
            profile_file.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def check_cache(cache_dir: Path, seq_hash: str, preset: str = "fast") -> Optional[Path]:
    """Check if we have a canonical cached MSA for this sequence hash."""
    if not cache_dir:
        return None
    cache_subdir = cache_dir / seq_hash[:2]
    canonical_cache = cache_subdir / f"{seq_hash}.a3m.gz"
    if canonical_cache.exists():
        _cleanup_profile_caches(cache_dir, seq_hash)
        return canonical_cache

    _ = preset
    if not cache_subdir.exists():
        return None

    # Legacy migration: choose deepest profile cache and promote to canonical.
    candidates = []
    for profile_file in cache_subdir.glob(f"{seq_hash}_*.a3m.gz"):
        depth = _cached_depth(profile_file)
        if depth is None:
            continue
        candidates.append((depth, profile_file.stat().st_mtime, profile_file))
    if not candidates:
        return None

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_path = candidates[0][2]
    cache_subdir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best_path, canonical_cache)
    _cleanup_profile_caches(cache_dir, seq_hash)
    return canonical_cache
    return None


def load_from_cache(cache_path: Path, out_path: Path) -> None:
    """Decompress cached MSA to output location."""
    with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
        content = f.read()
    with open(out_path, 'w') as f:
        f.write(content)


def save_to_cache(msa_path: Path, cache_dir: Path, seq_hash: str, preset: str = "fast") -> Path:
    """Compress and save MSA to canonical single-cache path."""
    _ = preset
    cache_subdir = cache_dir / seq_hash[:2]
    cache_subdir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_subdir / f"{seq_hash}.a3m.gz"
    with open(msa_path, 'rb') as f_in:
        with gzip.open(cache_file, 'wb') as f_out:
            f_out.write(f_in.read())
    _cleanup_profile_caches(cache_dir, seq_hash)
    return cache_file


def _run_colabfold_per_sequence(
    sequences: List[Dict[str, str]],
    output_dir: Path,
    db_path: Path,
    cache_dir: Optional[Path],
    gpu_id: int,
    reference_sequence: Optional[str],
    force_refresh: bool,
    cpu_only: bool,
    preset: str,
    max_seqs: Optional[int],
    use_expand: Optional[int],
    use_env: Optional[int],
    num_iterations: Optional[int],
    evalue: Optional[float],
    min_seq_id: Optional[float],
    min_coverage: Optional[float],
    taxon_list: Optional[str],
    min_depth_warning: Optional[int],
    min_depth_fail: Optional[int],
    gpu_mode: Optional[str],
    gpu_threshold: Optional[int],
    preferred_gpus: Optional[str],
    excluded_gpus: Optional[str],
    gpu_server_mode: Optional[str],
    gpu_server_wait_timeout: Optional[int],
    gpu_server_db_load_mode: Optional[int],
    gpu_server_startup_wait: Optional[float],
) -> List[Dict[str, Any]]:
    """
    Fallback path that preserves full run_local_msa.py behavior for quality-critical presets.
    """
    run_local_msa_script = Path(__file__).with_name("run_local_msa.py")
    if not run_local_msa_script.exists():
        raise RuntimeError(f"Missing run_local_msa.py at {run_local_msa_script}")

    results: List[Dict[str, Any]] = []
    for seq_info in sequences:
        name = seq_info["name"]
        sequence = seq_info["sequence"]
        cache_key_seq = reference_sequence if reference_sequence else sequence
        seq_hash = compute_sequence_hash(cache_key_seq)
        msa_path = output_dir / f"{name}.a3m"
        quality_path = output_dir / f"{name}_msa_quality.json"

        cmd = [
            "python3", str(run_local_msa_script),
            "--sequence", sequence,
            "--name", name,
            "--out_dir", str(output_dir),
            "--db_path", str(db_path),
            "--preset", preset,
            "--gpu-id", str(gpu_id),
        ]
        if max_seqs is not None:
            cmd.extend(["--max-seqs", str(max(1, int(max_seqs)))])

        if cache_dir:
            cmd.extend(["--cache_dir", str(cache_dir)])
        if reference_sequence:
            cmd.extend(["--reference-sequence", reference_sequence])
        if force_refresh:
            cmd.append("--force_refresh")
        if cpu_only:
            cmd.append("--cpu-only")
        if use_expand is not None:
            cmd.extend(["--use-expand", str(int(use_expand))])
        if use_env is not None:
            cmd.extend(["--use-env", str(int(use_env))])
        if num_iterations is not None:
            cmd.extend(["--num-iterations", str(num_iterations)])
        if evalue is not None:
            cmd.extend(["--evalue", str(evalue)])
        if min_seq_id is not None:
            cmd.extend(["--min-seq-id", str(min_seq_id)])
        if min_coverage is not None:
            cmd.extend(["--min-coverage", str(min_coverage)])
        if taxon_list:
            cmd.extend(["--taxon-list", taxon_list])
        if min_depth_warning is not None:
            cmd.extend(["--min-depth-warning", str(min_depth_warning)])
        if min_depth_fail is not None:
            cmd.extend(["--min-depth-fail", str(min_depth_fail)])
        if gpu_mode:
            cmd.extend(["--gpu-mode", gpu_mode])
        if gpu_threshold is not None:
            cmd.extend(["--gpu-threshold", str(gpu_threshold)])
        if preferred_gpus:
            cmd.extend(["--preferred-gpus", preferred_gpus])
        if excluded_gpus:
            cmd.extend(["--excluded-gpus", excluded_gpus])
        if gpu_server_mode:
            cmd.extend(["--gpu-server-mode", gpu_server_mode])
        if gpu_server_wait_timeout is not None:
            cmd.extend(["--gpu-server-wait-timeout", str(gpu_server_wait_timeout)])
        if gpu_server_db_load_mode is not None:
            cmd.extend(["--gpu-server-db-load-mode", str(gpu_server_db_load_mode)])
        if gpu_server_startup_wait is not None:
            cmd.extend(["--gpu-server-startup-wait", str(gpu_server_startup_wait)])

        print(f"  COLABFOLD MODE: {name} ({preset})")
        proc = subprocess.run(cmd, text=True, capture_output=True)
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="")

        if proc.returncode == 0 and msa_path.exists():
            cache_hit = False
            if quality_path.exists():
                try:
                    payload = json.loads(quality_path.read_text(encoding="utf-8"))
                    cache_hit = bool(payload.get("from_cache"))
                except Exception:
                    cache_hit = False
            results.append(
                {
                    "name": name,
                    "sequence_hash": seq_hash,
                    "msa_path": str(msa_path.absolute()),
                    "cache_hit": cache_hit,
                    "success": True,
                }
            )
        else:
            results.append(
                {
                    "name": name,
                    "sequence_hash": seq_hash,
                    "msa_path": None,
                    "cache_hit": False,
                    "success": False,
                    "error": f"run_local_msa.py failed with exit code {proc.returncode}",
                }
            )

    return results


def run_batch_msa(
    sequences: List[Dict[str, str]],
    output_dir: Path,
    db_path: Path,
    cache_dir: Optional[Path],
    gpu_id: int = 0,
    reference_sequence: Optional[str] = None,
    force_refresh: bool = False,
    cpu_only: bool = False,
    max_seqs: Optional[int] = None,
    preset: str = "fast",
    use_expand: Optional[int] = None,
    use_env: Optional[int] = None,
    num_iterations: Optional[int] = None,
    evalue: Optional[float] = None,
    min_seq_id: Optional[float] = None,
    min_coverage: Optional[float] = None,
    taxon_list: Optional[str] = None,
    min_depth_warning: Optional[int] = None,
    min_depth_fail: Optional[int] = None,
    gpu_mode: Optional[str] = None,
    gpu_threshold: Optional[int] = None,
    preferred_gpus: Optional[str] = None,
    excluded_gpus: Optional[str] = None,
    gpu_server_mode: Optional[str] = None,
    gpu_server_wait_timeout: Optional[int] = None,
    gpu_server_db_load_mode: Optional[int] = None,
    gpu_server_startup_wait: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Generate MSAs for multiple sequences.

    Fast preset uses true batched mmseqs search. Higher-quality presets or advanced
    overrides defer to run_local_msa.py per sequence for full ColabFold behavior.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    preset = str(preset or "fast").strip().lower()
    if preset not in {"maximum", "balanced", "fast"}:
        print(f"WARNING: Unknown preset '{preset}', falling back to 'fast'")
        preset = "fast"

    use_colabfold_mode = (
        preset != "fast"
        or use_expand is not None
        or use_env is not None
        or num_iterations is not None
        or evalue is not None
        or min_seq_id is not None
        or min_coverage is not None
        or bool(taxon_list)
        or min_depth_warning is not None
        or min_depth_fail is not None
    )

    print(f"\n=== True Batch MSA Generation ===")
    print(f"Sequences: {len(sequences)}")
    print(f"Output: {output_dir}")
    print(f"GPU: {gpu_id}")
    print(f"CPU only: {cpu_only}")
    print(f"Preset: {preset}")
    print(f"Max seqs: {max_seqs if max_seqs is not None else 'preset default'}")
    print(f"Mode: {'colabfold-compatible' if use_colabfold_mode else 'true-batch-fast'}")
    print()
    
    # Determine which sequences need MSA generation (not in cache)
    results = []
    sequences_to_process = []
    name_to_hash = {}
    
    for seq_info in sequences:
        name = seq_info["name"]
        sequence = seq_info["sequence"]
        cache_key_seq = reference_sequence if reference_sequence else sequence
        seq_hash = compute_sequence_hash(cache_key_seq)
        name_to_hash[name] = seq_hash
        
        msa_path = output_dir / f"{name}.a3m"
        
        # Fast-mode cache check uses preset-aware key.
        # ColabFold-compatible mode relies on run_local_msa.py cache handling.
        if cache_dir and not force_refresh and not use_colabfold_mode:
            cached = check_cache(cache_dir, seq_hash, preset=preset)
            if cached:
                load_from_cache(cached, msa_path)
                print(f"  CACHE HIT: {name}")
                results.append({
                    "name": name,
                    "sequence_hash": seq_hash,
                    "msa_path": str(msa_path.absolute()),
                    "cache_hit": True,
                    "success": True
                })
                continue
        
        # Need to generate
        sequences_to_process.append(seq_info)
        print(f"  CACHE MISS: {name}")
    
    if not sequences_to_process:
        print("\nAll sequences found in cache!")
        manifest = {
            "total_sequences": len(sequences),
            "successful": len(results),
            "cache_hits": len(results),
            "sequences": results
        }
        manifest_path = output_dir / "msa_manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)
        return manifest

    if use_colabfold_mode:
        print(f"\nGenerating {len(sequences_to_process)} MSAs with full ColabFold-compatible workflow...")
        try:
            results.extend(
                _run_colabfold_per_sequence(
                    sequences=sequences_to_process,
                    output_dir=output_dir,
                    db_path=db_path,
                    cache_dir=cache_dir,
                    gpu_id=gpu_id,
                    reference_sequence=reference_sequence,
                    force_refresh=force_refresh,
                    cpu_only=cpu_only,
                    preset=preset,
                    max_seqs=max_seqs,
                    use_expand=use_expand,
                    use_env=use_env,
                    num_iterations=num_iterations,
                    evalue=evalue,
                    min_seq_id=min_seq_id,
                    min_coverage=min_coverage,
                    taxon_list=taxon_list,
                    min_depth_warning=min_depth_warning,
                    min_depth_fail=min_depth_fail,
                    gpu_mode=gpu_mode,
                    gpu_threshold=gpu_threshold,
                    preferred_gpus=preferred_gpus,
                    excluded_gpus=excluded_gpus,
                    gpu_server_mode=gpu_server_mode,
                    gpu_server_wait_timeout=gpu_server_wait_timeout,
                    gpu_server_db_load_mode=gpu_server_db_load_mode,
                    gpu_server_startup_wait=gpu_server_startup_wait,
                )
            )
        except Exception as e:
            print(f"  BATCH ERROR: {e}")
            for seq_info in sequences_to_process:
                name = seq_info["name"]
                if not any(r["name"] == name for r in results):
                    results.append({
                        "name": name,
                        "sequence_hash": name_to_hash.get(name, ""),
                        "msa_path": None,
                        "cache_hit": False,
                        "success": False,
                        "error": str(e),
                    })

        manifest = {
            "total_sequences": len(sequences),
            "successful": sum(1 for r in results if r["success"]),
            "cache_hits": sum(1 for r in results if r.get("cache_hit", False)),
            "sequences": results
        }
        manifest_path = output_dir / "msa_manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        print(f"\n=== Complete ===")
        print(f"Successful: {manifest['successful']}/{manifest['total_sequences']}")
        print(f"Cache hits: {manifest['cache_hits']}")
        print(f"Manifest: {manifest_path}")
        return manifest

    print(f"\nGenerating {len(sequences_to_process)} MSAs in single batch...")
    tmp_dir = output_dir / "tmp_batch"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1: Write all sequences to ONE query FASTA
    # ═══════════════════════════════════════════════════════════════════════════
    query_fasta = tmp_dir / "query_batch.fasta"
    with open(query_fasta, 'w') as f:
        for seq_info in sequences_to_process:
            f.write(f">{seq_info['name']}\n{seq_info['sequence']}\n")
    
    # Find mmseqs binary (prefer Blackwell GPU, then Ampere GPU, then CPU)
    mmseqs_blackwell = db_path / "mmseqs-gpu-blackwell" / "bin" / "mmseqs"
    mmseqs_gpu = db_path / "mmseqs-gpu" / "bin" / "mmseqs"
    mmseqs_cpu = db_path / "mmseqs" / "bin" / "mmseqs"
    
    if cpu_only:
        mmseqs_bin = str(mmseqs_cpu)
        use_gpu = False
        print(f"  Using CPU MMseqs2 (forced): {mmseqs_bin}")
    elif mmseqs_blackwell.exists():
        mmseqs_bin = str(mmseqs_blackwell)
        use_gpu = True
        print(f"  Using Blackwell GPU MMseqs2: {mmseqs_bin}")
    elif mmseqs_gpu.exists():
        mmseqs_bin = str(mmseqs_gpu)
        use_gpu = True
        print(f"  Using Ampere GPU MMseqs2: {mmseqs_bin}")
    else:
        mmseqs_bin = str(mmseqs_cpu)
        use_gpu = False
        print(f"  Using CPU MMseqs2: {mmseqs_bin}")
    
    uniref_db = db_path / "uniref30_2302_db"
    
    # Set GPU environment
    env = os.environ.copy()
    if use_gpu:
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    
    try:
        # ═══════════════════════════════════════════════════════════════════════
        # STEP 2: Create query database (ONE database for all sequences)
        # ═══════════════════════════════════════════════════════════════════════
        query_db = tmp_dir / "query_db"
        print("  Creating query database...")
        subprocess.run([
            mmseqs_bin, "createdb", str(query_fasta), str(query_db)
        ], check=True, capture_output=True, env=env)
        
        # ═══════════════════════════════════════════════════════════════════════
        # STEP 3: SINGLE mmseqs search (GPU stays loaded for entire batch!)
        # ═══════════════════════════════════════════════════════════════════════
        result_db = tmp_dir / "result_db"
        search_tmp = tmp_dir / "search_tmp"
        search_tmp.mkdir(exist_ok=True)
        
        print(f"  Running batch search ({'GPU ' + str(gpu_id) if use_gpu else 'CPU'})...")
        effective_max_seqs = max(1, int(max_seqs)) if max_seqs is not None else 300
        search_cmd = [
            mmseqs_bin, "search",
            str(query_db), str(uniref_db), str(result_db), str(search_tmp),
            "-s", "8.0",
            "--max-seqs", str(effective_max_seqs),
            "-e", "0.001",
            "--split-memory-limit", "16G",
        ]
        if use_gpu:
            search_cmd.extend(["--gpu", "1"])
        
        subprocess.run(search_cmd, check=True, capture_output=True, env=env, timeout=1800)
        
        # ═══════════════════════════════════════════════════════════════════════
        # STEP 4: Generate MSAs from search results
        # ═══════════════════════════════════════════════════════════════════════
        msa_db = tmp_dir / "msa_db"
        print("  Converting results to MSA...")
        subprocess.run([
            mmseqs_bin, "result2msa",
            str(query_db), str(uniref_db), str(result_db), str(msa_db)
        ], check=True, capture_output=True, env=env, timeout=600)
        
        # ═══════════════════════════════════════════════════════════════════════
        # STEP 5: Unpack to individual A3M files
        # ═══════════════════════════════════════════════════════════════════════
        unpacked_dir = tmp_dir / "unpacked"
        unpacked_dir.mkdir(exist_ok=True)
        print("  Unpacking individual MSAs...")
        subprocess.run([
            mmseqs_bin, "unpackdb", str(msa_db), str(unpacked_dir), "--unpack-suffix", ".a3m"
        ], check=True, capture_output=True, env=env, timeout=300)
        
        # ═══════════════════════════════════════════════════════════════════════
        # STEP 6: Map unpacked files to sequence names and save to cache
        # ═══════════════════════════════════════════════════════════════════════
        # Read the header lookup from query database
        header_lookup = tmp_dir / "query_db.lookup"
        name_to_idx = {}
        if header_lookup.exists():
            with open(header_lookup) as f:
                for line in f:
                    parts = line.strip().split('\t')
                    if len(parts) >= 2:
                        idx, name = parts[0], parts[1]
                        name_to_idx[name] = idx
        
        for seq_info in sequences_to_process:
            name = seq_info["name"]
            seq_hash = name_to_hash[name]
            msa_path = output_dir / f"{name}.a3m"
            
            # Find the unpacked file (by index or by name)
            idx = name_to_idx.get(name, None)
            unpacked_file = None
            
            # Try by index
            if idx:
                candidate = unpacked_dir / f"{idx}.a3m"
                if candidate.exists():
                    unpacked_file = candidate
            
            # Try by direct name match
            if not unpacked_file:
                for f in unpacked_dir.glob("*.a3m"):
                    # Read first line to check header
                    with open(f) as fh:
                        header = fh.readline().strip()
                        if header == f">{name}":
                            unpacked_file = f
                            break
            
            # Fallback: just copy any file with matching index
            if not unpacked_file:
                idx_in_batch = sequences_to_process.index(seq_info)
                candidate = unpacked_dir / f"{idx_in_batch}.a3m"
                if candidate.exists():
                    unpacked_file = candidate
            
            if unpacked_file and unpacked_file.exists():
                # Read and clean null bytes
                with open(unpacked_file, 'rb') as f:
                    content = f.read().replace(b'\x00', b'')
                with open(msa_path, 'wb') as f:
                    f.write(content)
                
                # Save to cache
                if cache_dir:
                    save_to_cache(msa_path, cache_dir, seq_hash, preset=preset)
                
                print(f"  DONE: {name}")
                results.append({
                    "name": name,
                    "sequence_hash": seq_hash,
                    "msa_path": str(msa_path.absolute()),
                    "cache_hit": False,
                    "success": True
                })
            else:
                print(f"  ERROR: {name} - unpacked file not found")
                results.append({
                    "name": name,
                    "sequence_hash": seq_hash,
                    "msa_path": None,
                    "cache_hit": False,
                    "success": False,
                    "error": "Unpacked file not found"
                })
        
    except Exception as e:
        print(f"  BATCH ERROR: {e}")
        # Mark all remaining as failed
        for seq_info in sequences_to_process:
            name = seq_info["name"]
            if not any(r["name"] == name for r in results):
                results.append({
                    "name": name,
                    "sequence_hash": name_to_hash.get(name, ""),
                    "msa_path": None,
                    "cache_hit": False,
                    "success": False,
                    "error": str(e)
                })
    finally:
        # Cleanup temp directory
        shutil.rmtree(tmp_dir, ignore_errors=True)
    
    # Create manifest
    manifest = {
        "total_sequences": len(sequences),
        "successful": sum(1 for r in results if r["success"]),
        "cache_hits": sum(1 for r in results if r.get("cache_hit", False)),
        "sequences": results
    }
    
    manifest_path = output_dir / "msa_manifest.json"
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    print(f"\n=== Complete ===")
    print(f"Successful: {manifest['successful']}/{manifest['total_sequences']}")
    print(f"Cache hits: {manifest['cache_hits']}")
    print(f"Manifest: {manifest_path}")
    
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="True Batch MSA Generation")
    parser.add_argument("--sequences", required=True, 
                       help="JSON array of {name, sequence} objects")
    parser.add_argument("--output_dir", required=True,
                       help="Output directory for MSA files")
    parser.add_argument("--db_path", default=DEFAULT_DB_PATH,
                       help="Path to ColabFold database")
    parser.add_argument("--cache_dir", default=DEFAULT_CACHE_DIR,
                       help="Path to MSA cache directory")
    parser.add_argument("--force_refresh", action="store_true",
                       help="Bypass cache and regenerate MSAs")
    parser.add_argument("--gpu_id", type=int, default=0,
                       help="GPU ID to use for search")
    parser.add_argument("--cpu-only", action="store_true",
                       help="Force CPU mode (disable GPU MMseqs2)")
    parser.add_argument("--max-seqs", type=int, default=None,
                       help="Maximum candidate sequences retained in search")
    parser.add_argument("--preset", type=str, default="fast",
                       choices=["maximum", "balanced", "fast"],
                       help="MSA quality preset")
    parser.add_argument("--reference_sequence", default=None,
                       help="Reference sequence for cache key (mutagenesis sharing)")
    parser.add_argument("--use-expand", type=int, default=None, choices=[0, 1],
                       help="Override expansion mode (0/1)")
    parser.add_argument("--use-env", type=int, default=None, choices=[0, 1],
                       help="Override environmental DB usage (0/1)")
    parser.add_argument("--num-iterations", type=int, default=None,
                       help="Override number of profile iterations")
    parser.add_argument("--evalue", type=float, default=None,
                       help="Override e-value threshold")
    parser.add_argument("--min-seq-id", type=float, default=None,
                       help="Minimum sequence identity (0-1)")
    parser.add_argument("--min-coverage", type=float, default=None,
                       help="Minimum query coverage (0-1)")
    parser.add_argument("--taxon-list", type=str, default=None,
                       help="NCBI taxonomy IDs to filter (comma-separated)")
    parser.add_argument("--min-depth-warning", type=int, default=None,
                       help="Warn if MSA has fewer sequences")
    parser.add_argument("--min-depth-fail", type=int, default=None,
                       help="Fail if MSA has fewer sequences")
    parser.add_argument("--gpu-mode", type=str, default=None,
                       choices=["auto", "opportunistic", "required", "cpu"],
                       help="GPU policy override")
    parser.add_argument("--gpu-threshold", type=int, default=None,
                       help="Max util/memory threshold for opportunistic GPU selection")
    parser.add_argument("--preferred-gpus", type=str, default=None,
                       help="Comma-separated preferred GPU IDs")
    parser.add_argument("--excluded-gpus", type=str, default=None,
                       help="Comma-separated excluded GPU IDs")
    parser.add_argument("--gpu-server-mode", type=str, default=None,
                       choices=["auto", "required", "persistent", "off"],
                       help="MMseqs gpuserver policy override")
    parser.add_argument("--gpu-server-wait-timeout", type=int, default=None,
                       help="Seconds to wait for gpuserver handshake")
    parser.add_argument("--gpu-server-db-load-mode", type=int, default=None, choices=[0, 1, 2, 3],
                       help="MMseqs db-load-mode for gpuserver-backed searches")
    parser.add_argument("--gpu-server-startup-wait", type=float, default=None,
                       help="Seconds to wait after starting gpuserver")
    
    args = parser.parse_args()
    
    # Parse sequences JSON
    try:
        sequences = json.loads(args.sequences)
    except json.JSONDecodeError as e:
        print(f"Error parsing sequences JSON: {e}")
        sys.exit(1)
    
    # Run batch MSA
    manifest = run_batch_msa(
        sequences=sequences,
        output_dir=Path(args.output_dir),
        db_path=Path(args.db_path),
        cache_dir=Path(args.cache_dir) if args.cache_dir else None,
        gpu_id=args.gpu_id,
        reference_sequence=args.reference_sequence,
        force_refresh=args.force_refresh,
        cpu_only=args.cpu_only,
        max_seqs=args.max_seqs,
        preset=args.preset,
        use_expand=args.use_expand,
        use_env=args.use_env,
        num_iterations=args.num_iterations,
        evalue=args.evalue,
        min_seq_id=args.min_seq_id,
        min_coverage=args.min_coverage,
        taxon_list=args.taxon_list,
        min_depth_warning=args.min_depth_warning,
        min_depth_fail=args.min_depth_fail,
        gpu_mode=args.gpu_mode,
        gpu_threshold=args.gpu_threshold,
        preferred_gpus=args.preferred_gpus,
        excluded_gpus=args.excluded_gpus,
        gpu_server_mode=args.gpu_server_mode,
        gpu_server_wait_timeout=args.gpu_server_wait_timeout,
        gpu_server_db_load_mode=args.gpu_server_db_load_mode,
        gpu_server_startup_wait=args.gpu_server_startup_wait,
    )
    
    # Exit with error if any failed
    if manifest["successful"] < manifest["total_sequences"]:
        sys.exit(1)
