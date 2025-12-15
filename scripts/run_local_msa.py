#!/usr/bin/env python3
"""
Run local MMseqs2 MSA search using ColabFold databases.

This bypasses the ColabFold API by running MMseqs2 locally against 
downloaded UniRef30 and ColabFoldDB databases.

Usage:
    python run_local_msa.py \
        --sequence "MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSH" \
        --name hemoglobin \
        --out_dir ./output \
        --db_path /media/dalab/Data\ and\ Models1/colabfold_db
"""
import argparse
import os
import subprocess
import tempfile
import hashlib
import gzip
from pathlib import Path
from datetime import datetime, timedelta


def compute_sequence_hash(sequence: str) -> str:
    """Compute SHA256 hash of sequence for cache key."""
    return hashlib.sha256(sequence.encode()).hexdigest()


def get_cache_path(cache_dir: str, seq_hash: str) -> Path:
    """Get path to cached file with subdirectory sharding."""
    subdir = seq_hash[:2]
    cache_path = Path(cache_dir) / subdir / f"{seq_hash}.a3m.gz"
    return cache_path


def check_cache(cache_dir: str, seq_hash: str, max_age_days: int) -> Path | None:
    """Check if valid cached MSA exists. Returns path if found, None otherwise."""
    cache_path = get_cache_path(cache_dir, seq_hash)
    
    if not cache_path.exists():
        return None
    
    # Check expiry
    if max_age_days > 0:
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
        age = datetime.now() - mtime
        if age > timedelta(days=max_age_days):
            print(f"Cache expired (age: {age.days} days), will refresh", flush=True)
            return None
    
    return cache_path


def save_to_cache(cache_dir: str, seq_hash: str, a3m_content: str) -> Path:
    """Save MSA to cache with gzip compression."""
    cache_path = get_cache_path(cache_dir, seq_hash)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    
    with gzip.open(cache_path, 'wt', encoding='utf-8') as f:
        f.write(a3m_content)
    
    file_size = cache_path.stat().st_size
    print(f"Saved to cache: {cache_path} ({file_size} bytes compressed)", flush=True)
    return cache_path


def load_from_cache(cache_path: Path, out_path: str) -> str:
    """Decompress cached MSA to output location."""
    with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
        content = f.read()
    
    with open(out_path, 'w') as f:
        f.write(content)
    
    return content


def run_local_mmseqs2(
    sequence: str,
    job_name: str,
    out_dir: str,
    db_path: str = "/media/dalab/Data and Models1/colabfold_db",
    cache_dir: str = None,
    max_age_days: int = 30,
    force_refresh: bool = False,
    num_threads: int = 32
):
    """
    Generate MSA using local MMseqs2 databases.
    
    Args:
        sequence: Amino acid sequence
        job_name: Name for output files
        out_dir: Output directory
        db_path: Path to ColabFold database directory
        cache_dir: Cache directory for storing results
        max_age_days: Cache expiry in days
        force_refresh: Bypass cache
        num_threads: CPU threads for MMseqs2
    """
    seq_hash = compute_sequence_hash(sequence)
    final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
    
    # Check cache first
    if cache_dir and not force_refresh:
        cached = check_cache(cache_dir, seq_hash, max_age_days)
        if cached:
            print(f"CACHE HIT: {seq_hash[:16]}... (loading from {cached})", flush=True)
            load_from_cache(cached, final_a3m)
            print(f"Loaded cached MSA to {final_a3m}", flush=True)
            return
    
    print(f"CACHE MISS: {seq_hash[:16]}... (running local MMseqs2)", flush=True)
    
    # Locate databases
    db_path = Path(db_path)
    mmseqs_bin = db_path / "mmseqs" / "bin" / "mmseqs"
    uniref_db = db_path / "uniref30_2302_db"
    
    if not mmseqs_bin.exists():
        # Try system mmseqs
        mmseqs_bin = "mmseqs"
    
    if not Path(str(uniref_db) + ".idx").exists() and not Path(str(uniref_db)).exists():
        raise FileNotFoundError(f"UniRef30 database not found at {uniref_db}")
    
    os.makedirs(out_dir, exist_ok=True)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Write query sequence
        query_fasta = os.path.join(tmp_dir, "query.fasta")
        with open(query_fasta, 'w') as f:
            f.write(f">query\n{sequence}\n")
        
        # Create query database
        query_db = os.path.join(tmp_dir, "query_db")
        subprocess.run([
            str(mmseqs_bin), "createdb", query_fasta, query_db
        ], check=True, capture_output=True)
        
        # Search against UniRef30
        result_db = os.path.join(tmp_dir, "result_db")
        subprocess.run([
            str(mmseqs_bin), "search",
            query_db, str(uniref_db), result_db,
            os.path.join(tmp_dir, "tmp"),
            "--threads", str(num_threads),
            "-s", "8.0",
            "--max-seqs", "10000",
            "-e", "0.001"
        ], check=True, capture_output=True)
        
        # Convert to A3M
        a3m_file = os.path.join(tmp_dir, "result.a3m")
        subprocess.run([
            str(mmseqs_bin), "result2msa",
            query_db, str(uniref_db), result_db, a3m_file,
            "--msa-format-mode", "6"
        ], check=True, capture_output=True)
        
        # Read result
        with open(a3m_file, 'r') as f:
            a3m_content = f.read()
        
        # Write to output
        with open(final_a3m, 'w') as f:
            f.write(a3m_content)
        
        print(f"MSA generated: {final_a3m}", flush=True)
        
        # Save to cache
        if cache_dir:
            save_to_cache(cache_dir, seq_hash, a3m_content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate MSA using local MMseqs2")
    parser.add_argument("--sequence", required=True, help="Amino acid sequence")
    parser.add_argument("--name", required=True, help="Job name for output files")
    parser.add_argument("--out_dir", required=True, help="Output directory")
    parser.add_argument("--db_path", 
                        default="/media/dalab/Data and Models1/colabfold_db",
                        help="Path to ColabFold database directory")
    parser.add_argument("--cache_dir", default=None, 
                        help="Cache directory (enables caching)")
    parser.add_argument("--max_age_days", type=int, default=30, 
                        help="Cache expiry in days")
    parser.add_argument("--force_refresh", action="store_true", 
                        help="Bypass cache")
    parser.add_argument("--threads", type=int, default=32, 
                        help="CPU threads for MMseqs2")
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    run_local_mmseqs2(
        args.sequence, 
        args.name, 
        args.out_dir,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
        max_age_days=args.max_age_days,
        force_refresh=args.force_refresh,
        num_threads=args.threads
    )
