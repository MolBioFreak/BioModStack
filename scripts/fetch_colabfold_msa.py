#!/usr/bin/env python3
"""
Fetch MSA from ColabFold API with local caching.
Based on the official ColabFold client implementation.

Features:
- Caches MSA results by sequence hash (SHA256)
- Gzip compression (~5x size reduction)
- 30-day expiry with automatic purge
- SQLite metadata tracking
"""
import time
import requests
import argparse
import sys
import os
import tarfile
import hashlib
import gzip
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


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


def save_to_cache(cache_dir: str, seq_hash: str, a3m_content: str, 
                  sequence: str, colabfold_job_id: str, db_path: str = None) -> Path:
    """Save MSA to cache with gzip compression."""
    cache_path = get_cache_path(cache_dir, seq_hash)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Compress and save
    with gzip.open(cache_path, 'wt', encoding='utf-8') as f:
        f.write(a3m_content)
    
    file_size = cache_path.stat().st_size
    print(f"Saved to cache: {cache_path} ({file_size} bytes compressed)", flush=True)
    
    # Update SQLite metadata if db_path provided
    if db_path and os.path.exists(db_path):
        try:
            update_db_metadata(db_path, seq_hash, sequence, str(cache_path), 
                              file_size, colabfold_job_id)
        except Exception as e:
            print(f"Warning: Failed to update cache metadata in DB: {e}", flush=True)
    
    return cache_path


def update_db_metadata(db_path: str, seq_hash: str, sequence: str, msa_path: str,
                       file_size: int, colabfold_job_id: str):
    """Update MSA cache metadata in SQLite."""
    import uuid
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if table exists (may not if API hasn't run init_db yet)
    cursor.execute("""
        SELECT name FROM sqlite_master 
        WHERE type='table' AND name='msa_cache'
    """)
    if not cursor.fetchone():
        # Create table if needed
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS msa_cache (
                id TEXT PRIMARY KEY,
                sequence_hash TEXT UNIQUE NOT NULL,
                sequence TEXT NOT NULL,
                sequence_length INTEGER NOT NULL,
                msa_path TEXT NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                colabfold_job_id TEXT,
                hit_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_msa_cache_hash ON msa_cache(sequence_hash)")
    
    now = datetime.utcnow()
    expires_at = now + timedelta(days=30)
    
    # Upsert
    cursor.execute("""
        INSERT INTO msa_cache (id, sequence_hash, sequence, sequence_length, msa_path, 
                               file_size_bytes, colabfold_job_id, created_at, last_accessed, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(sequence_hash) DO UPDATE SET
            msa_path = excluded.msa_path,
            file_size_bytes = excluded.file_size_bytes,
            colabfold_job_id = excluded.colabfold_job_id,
            created_at = excluded.created_at,
            expires_at = excluded.expires_at
    """, (str(uuid.uuid4()), seq_hash, sequence, len(sequence), msa_path,
          file_size, colabfold_job_id, now.isoformat(), now.isoformat(), expires_at.isoformat()))
    
    conn.commit()
    conn.close()


def update_cache_hit(db_path: str, seq_hash: str):
    """Increment hit count and update last_accessed for cache hit."""
    if not db_path or not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE msa_cache 
            SET hit_count = hit_count + 1, last_accessed = ?
            WHERE sequence_hash = ?
        """, (datetime.utcnow().isoformat(), seq_hash))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Warning: Failed to update cache hit count: {e}", flush=True)


def load_from_cache(cache_path: Path, out_path: str) -> str:
    """Decompress cached MSA to output location."""
    with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
        content = f.read()
    
    with open(out_path, 'w') as f:
        f.write(content)
    
    return content


def run_mmseqs2(sequence: str, job_name: str, out_dir: str, 
                cache_dir: str = None, max_age_days: int = 30,
                force_refresh: bool = False, db_path: str = None):
    """Fetch MSA from ColabFold API with caching support."""
    
    seq_hash = compute_sequence_hash(sequence)
    final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
    
    # Check cache first (unless force refresh)
    if cache_dir and not force_refresh:
        cached = check_cache(cache_dir, seq_hash, max_age_days)
        if cached:
            print(f"CACHE HIT: {seq_hash[:16]}... (loading from {cached})", flush=True)
            load_from_cache(cached, final_a3m)
            print(f"Loaded cached MSA to {final_a3m}", flush=True)
            update_cache_hit(db_path, seq_hash)
            return
    
    print(f"CACHE MISS: {seq_hash[:16]}... (fetching from ColabFold)", flush=True)
    
    host_url = "https://api.colabfold.com"
    
    # 1. Submit job
    print(f"Submitting sequence to ColabFold API...", flush=True)
    query = f">1\n{sequence}"
    
    error_count = 0
    while True:
        try:
            res = requests.post(f'{host_url}/ticket/msa', data={'q': query, 'mode': 'env'}, timeout=6.02)
            res.raise_for_status()
            out = res.json()
            break
        except requests.exceptions.Timeout:
            print("Timeout while submitting. Retrying...", flush=True)
            continue
        except Exception as e:
            error_count += 1
            print(f"Error submitting ({error_count}/5): {e}", flush=True)
            time.sleep(5)
            if error_count >= 5:
                raise
            continue
    
    job_id = out.get('id')
    status = out.get('status', 'PENDING')
    print(f"Job ID: {job_id}, Status: {status}", flush=True)
    
    # 2. Poll status (only if not already complete)
    while status not in ['COMPLETE', 'ERROR']:
        time.sleep(5)
        error_count = 0
        while True:
            try:
                res = requests.get(f'{host_url}/ticket/{job_id}', timeout=6.02)
                res.raise_for_status()
                out = res.json()
                break
            except requests.exceptions.Timeout:
                print("Timeout while polling. Retrying...", flush=True)
                continue
            except Exception as e:
                error_count += 1
                print(f"Polling error ({error_count}/5): {e}", flush=True)
                time.sleep(5)
                if error_count > 5:
                    raise
                continue
        
        status = out.get('status', 'ERROR')
        print(f"Status: {status}", flush=True)
    
    if status == 'ERROR':
        print("Error from ColabFold server.", flush=True)
        sys.exit(1)
    
    print("MSA generation complete.", flush=True)
    
    # 3. Download results
    download_url = f'{host_url}/result/download/{job_id}'
    print(f"Downloading from {download_url}...", flush=True)
    
    error_count = 0
    while True:
        try:
            res = requests.get(download_url, timeout=60)
            res.raise_for_status()
            break
        except requests.exceptions.Timeout:
            print("Timeout while downloading. Retrying...", flush=True)
            continue
        except Exception as e:
            error_count += 1
            print(f"Download error ({error_count}/5): {e}", flush=True)
            time.sleep(5)
            if error_count > 5:
                raise
            continue
    
    # Save tar.gz
    tar_path = os.path.join(out_dir, f"{job_name}.tar.gz")
    with open(tar_path, "wb") as f:
        f.write(res.content)
    print(f"Saved to {tar_path}", flush=True)
    
    # Extract a3m
    with tarfile.open(tar_path, "r:gz") as tar:
        a3m_members = [m for m in tar.getmembers() if m.name.endswith(".a3m")]
        if not a3m_members:
            print("No A3M file found in archive.", flush=True)
            sys.exit(1)
        
        f = tar.extractfile(a3m_members[0])
        # Strip null bytes that may be present from tar padding
        a3m_content = f.read().decode("utf-8").rstrip('\x00')
        
        with open(final_a3m, "w") as out:
            out.write(a3m_content)
        
        print(f"Extracted A3M to {final_a3m}", flush=True)
    
    # Save to cache
    if cache_dir:
        save_to_cache(cache_dir, seq_hash, a3m_content, sequence, job_id, db_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch MSA from ColabFold with caching")
    parser.add_argument("--sequence", required=True, help="Amino acid sequence")
    parser.add_argument("--name", required=True, help="Job name for output files")
    parser.add_argument("--out_dir", required=True, help="Output directory")
    parser.add_argument("--cache_dir", default=None, help="Cache directory (enables caching)")
    parser.add_argument("--max_age_days", type=int, default=30, help="Cache expiry in days (0=never)")
    parser.add_argument("--force_refresh", action="store_true", help="Bypass cache, re-fetch from API")
    parser.add_argument("--db_path", default=None, help="SQLite database path for metadata tracking")
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    run_mmseqs2(args.sequence, args.name, args.out_dir,
                cache_dir=args.cache_dir, max_age_days=args.max_age_days,
                force_refresh=args.force_refresh, db_path=args.db_path)
