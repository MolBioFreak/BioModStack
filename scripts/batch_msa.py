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

_data_root = os.getenv("BMS_DATA")
DEFAULT_DB_PATH = os.getenv("BMS_COLABFOLD_DB") or (
    f"{_data_root}/colabfold_db" if _data_root else "/mnt/BioModStack/colabfold_db"
)
DEFAULT_CACHE_DIR = os.getenv("BMS_MSA_CACHE") or (
    f"{_data_root}/msa_cache" if _data_root else "/mnt/BioModStack/msa_cache"
)


def compute_sequence_hash(sequence: str) -> str:
    """Compute SHA256 hash of sequence for cache key."""
    return hashlib.sha256(sequence.encode()).hexdigest()


def check_cache(cache_dir: Path, seq_hash: str, preset: str = "balanced") -> Optional[Path]:
    """Check if we have a cached MSA for this sequence hash + preset."""
    if not cache_dir:
        return None
    cache_subdir = cache_dir / seq_hash[:2]
    # Preset-aware cache key for consistency with run_local_msa.py
    cache_file = cache_subdir / f"{seq_hash}_{preset}.a3m.gz"
    if cache_file.exists():
        return cache_file
    # Fallback: check legacy format (no preset)
    legacy_cache = cache_subdir / f"{seq_hash}.a3m.gz"
    if legacy_cache.exists():
        return legacy_cache
    return None


def load_from_cache(cache_path: Path, out_path: Path) -> None:
    """Decompress cached MSA to output location."""
    with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
        content = f.read()
    with open(out_path, 'w') as f:
        f.write(content)


def save_to_cache(msa_path: Path, cache_dir: Path, seq_hash: str, preset: str = "balanced") -> Path:
    """Compress and save MSA to cache with preset-aware naming."""
    cache_subdir = cache_dir / seq_hash[:2]
    cache_subdir.mkdir(parents=True, exist_ok=True)
    # Preset-aware cache key for consistency with run_local_msa.py
    cache_file = cache_subdir / f"{seq_hash}_{preset}.a3m.gz"
    with open(msa_path, 'rb') as f_in:
        with gzip.open(cache_file, 'wb') as f_out:
            f_out.write(f_in.read())
    return cache_file


def run_batch_msa(
    sequences: List[Dict[str, str]],
    output_dir: Path,
    db_path: Path,
    cache_dir: Optional[Path],
    gpu_id: int = 0,
    reference_sequence: Optional[str] = None,
    force_refresh: bool = False
) -> Dict[str, Any]:
    """
    Generate MSAs for all sequences in a SINGLE mmseqs search.
    
    This is true batching: one database scan for all queries.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = output_dir / "tmp_batch"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n=== True Batch MSA Generation ===")
    print(f"Sequences: {len(sequences)}")
    print(f"Output: {output_dir}")
    print(f"GPU: {gpu_id}")
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
        
        # Check cache
        if cache_dir and not force_refresh:
            cached = check_cache(cache_dir, seq_hash)
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
    
    print(f"\nGenerating {len(sequences_to_process)} MSAs in single batch...")
    
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
    
    if mmseqs_blackwell.exists():
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
        search_cmd = [
            mmseqs_bin, "search",
            str(query_db), str(uniref_db), str(result_db), str(search_tmp),
            "-s", "8.0",
            "--max-seqs", "10000",
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
                    save_to_cache(msa_path, cache_dir, seq_hash)
                
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
    parser.add_argument("--reference_sequence", default=None,
                       help="Reference sequence for cache key (mutagenesis sharing)")
    
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
        force_refresh=args.force_refresh
    )
    
    # Exit with error if any failed
    if manifest["successful"] < manifest["total_sequences"]:
        sys.exit(1)
