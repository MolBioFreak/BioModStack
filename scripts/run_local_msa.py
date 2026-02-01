#!/usr/bin/env python3
"""
Run local MMseqs2 MSA search using ColabFold databases.

Supports GPU-accelerated MSA with hybrid CPU/GPU scheduling:
- If GPU is available: Use mmseqs-gpu for ~5-10 second MSA
- If GPU is busy: Fall back to CPU mmseqs for ~2-3 minute MSA

Usage:
    # Auto-detect GPU availability
    python run_local_msa.py \
        --sequence "MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSH" \
        --name hemoglobin \
        --out_dir ./output \
        --db_path "$BMS_COLABFOLD_DB" \
        --cache_dir "$BMS_MSA_CACHE"
        
    # Force GPU (specific device)
    python run_local_msa.py --use-gpu --gpu-id 2 ...
    
    # Force CPU
    python run_local_msa.py --cpu-only ...
"""
import argparse
import os
import subprocess
import tempfile
import hashlib
import gzip
import sys
import fcntl
import time
from pathlib import Path
from datetime import datetime, timedelta

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
    
    # Check expiry (0 = never expire)
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


def get_msa_lock_path(cache_dir: str, seq_hash: str) -> Path:
    """Get path to lock file for MSA generation serialization."""
    lock_dir = Path(cache_dir) / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{seq_hash}.lock"


def acquire_msa_lock(lock_path: Path, timeout: int = 600) -> int:
    """
    Acquire exclusive lock for MSA generation.
    
    Returns file descriptor if lock acquired, blocks if another process has it.
    Timeout in seconds (default 10 minutes for long MSA searches).
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    
    start_time = time.time()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (IOError, OSError):
            if time.time() - start_time > timeout:
                os.close(fd)
                raise TimeoutError(f"Timeout waiting for MSA lock: {lock_path}")
            print(f"Waiting for MSA lock (another job is generating MSA for this sequence)...", flush=True)
            time.sleep(5)  # Wait 5 seconds before retrying


def release_msa_lock(fd: int):
    """Release MSA lock."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except Exception:
        pass


def check_gpu_availability(threshold: int = 80) -> int | None:
    """
    Check for available GPU.
    
    Returns GPU ID if one is available, None otherwise.
    """
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,utilization.gpu,memory.used,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        
        gpus = []
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 4:
                gpu_id = int(parts[0])
                utilization = int(parts[1])
                mem_used = int(parts[2])
                mem_total = int(parts[3])
                mem_percent = (mem_used / mem_total * 100) if mem_total > 0 else 100
                gpus.append({
                    'id': gpu_id,
                    'utilization': utilization,
                    'memory_percent': mem_percent
                })
        
        # Sort by utilization (prefer least busy)
        gpus.sort(key=lambda g: g['utilization'])
        
        for gpu in gpus:
            if gpu['utilization'] < threshold and gpu['memory_percent'] < threshold:
                return gpu['id']
        
        return None
    except Exception:
        return None


def run_local_mmseqs2(
    sequence: str,
    job_name: str,
    out_dir: str,
    db_path: str = DEFAULT_DB_PATH,
    cache_dir: str = None,
    max_age_days: int = 0,  # Default: never expire
    force_refresh: bool = False,
    num_threads: int = 32,
    use_gpu: bool = None,  # None = auto-detect
    gpu_id: int = None,
    cpu_only: bool = False,
    reference_sequence: str = None,  # For mutagenesis: use this seq for cache key
    # MSA Quality Parameters
    evalue: float = 0.001,         # E-value threshold (lower = stricter)
    sensitivity: float = 8.0,      # MMseqs sensitivity (1-8)
    min_seq_id: float = None,      # Minimum sequence identity (0-1.0)
    min_coverage: float = None,    # Minimum query coverage (0-1.0)
    taxon_list: str = None,        # Taxonomy IDs to include (comma-separated)
    min_depth_warning: int = 100,  # Warn if MSA has fewer sequences
    min_depth_fail: int = 0,       # Fail if MSA has fewer sequences (0 = warn only)
):
    """
    Generate MSA using local MMseqs2 databases.
    
    Args:
        sequence: Amino acid sequence
        job_name: Name for output files
        out_dir: Output directory
        db_path: Path to ColabFold database directory
        cache_dir: Cache directory for storing results
        max_age_days: Cache expiry in days (0 = never expire)
        force_refresh: Bypass cache
        num_threads: CPU threads for MMseqs2
        use_gpu: Force GPU mode (None = auto-detect)
        gpu_id: Specific GPU to use
        cpu_only: Force CPU mode
        reference_sequence: For mutagenesis - use this sequence for cache key
                           (allows variants to share WT MSA)
        evalue: E-value threshold for hits (default 0.001, lower = stricter)
        sensitivity: MMseqs2 sensitivity 1-8 (default 8.0, higher = slower but more sensitive)
        min_seq_id: Minimum sequence identity filter (0-1.0, None = no filter)
        min_coverage: Minimum query coverage filter (0-1.0, None = no filter)
        taxon_list: Comma-separated NCBI taxonomy IDs to restrict search
                   (e.g., '2' for Bacteria, '2759' for Eukaryota)
        min_depth_warning: Warn if MSA has fewer than this many sequences
        min_depth_fail: Fail if MSA has fewer than this many sequences (0 = no fail)
    """
    # For cache: use reference sequence if provided (mutagenesis mode)
    cache_key_seq = reference_sequence or sequence
    seq_hash = compute_sequence_hash(cache_key_seq)
    
    if reference_sequence:
        print(f"MUTAGENESIS MODE: Using reference sequence for cache (hash: {seq_hash[:16]}...)", flush=True)
    final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
    
    # Check cache first (before acquiring lock)
    if cache_dir and not force_refresh:
        cached = check_cache(cache_dir, seq_hash, max_age_days)
        if cached:
            print(f"CACHE HIT: {seq_hash[:16]}... (loading from {cached})", flush=True)
            load_from_cache(cached, final_a3m)
            print(f"Loaded cached MSA to {final_a3m}", flush=True)
            return
    
    # ═══════════════════════════════════════════════════════════════════════════════
    # ACQUIRE LOCK: Serialize MSA generation to prevent parallel DRAM OOM
    # Multiple jobs for same sequence will wait here while first one generates MSA
    # ═══════════════════════════════════════════════════════════════════════════════
    lock_fd = None
    if cache_dir:
        lock_path = get_msa_lock_path(cache_dir, seq_hash)
        print(f"Acquiring MSA lock for {seq_hash[:16]}...", flush=True)
        lock_fd = acquire_msa_lock(lock_path)
        print(f"MSA lock acquired", flush=True)
        
        # CRITICAL: Re-check cache after acquiring lock
        # Another job may have generated the MSA while we were waiting
        if not force_refresh:
            cached = check_cache(cache_dir, seq_hash, max_age_days)
            if cached:
                print(f"CACHE HIT (after lock): {seq_hash[:16]}... (loading from {cached})", flush=True)
                load_from_cache(cached, final_a3m)
                release_msa_lock(lock_fd)
                print(f"Loaded cached MSA to {final_a3m}", flush=True)
                return
    
    print(f"CACHE MISS: {seq_hash[:16]}... (running local MMseqs2)", flush=True)
    
    # Locate databases
    db_path = Path(db_path)
    mmseqs_cpu = db_path / "mmseqs" / "bin" / "mmseqs"
    mmseqs_gpu = db_path / "mmseqs-gpu" / "bin" / "mmseqs"  # GPU binary in separate dir
    uniref_db = db_path / "uniref30_2302_db"
    
    # Determine which binary to use
    selected_gpu_id = None
    use_gpu_flag = False
    if cpu_only:
        mmseqs_bin = mmseqs_cpu
        print("Using CPU mmseqs (forced)", flush=True)
    elif use_gpu or (use_gpu is None and mmseqs_gpu.exists()):
        # Auto-detect or forced GPU mode
        if gpu_id is not None:
            selected_gpu_id = gpu_id
        else:
            selected_gpu_id = check_gpu_availability()
        
        if selected_gpu_id is not None and mmseqs_gpu.exists():
            mmseqs_bin = mmseqs_gpu
            use_gpu_flag = True
            print(f"Using GPU mmseqs on device {selected_gpu_id}", flush=True)
        else:
            mmseqs_bin = mmseqs_cpu
            print("GPU unavailable, falling back to CPU mmseqs", flush=True)
    else:
        mmseqs_bin = mmseqs_cpu
        print("Using CPU mmseqs", flush=True)
    
    if not mmseqs_bin.exists():
        # Try system mmseqs
        mmseqs_bin = "mmseqs"
    
    if not Path(str(uniref_db) + ".idx").exists() and not Path(str(uniref_db)).exists():
        raise FileNotFoundError(f"UniRef30 database not found at {uniref_db}")
    
    os.makedirs(out_dir, exist_ok=True)
    
    # Set CUDA device if using GPU
    env = os.environ.copy()
    if selected_gpu_id is not None:
        env['CUDA_VISIBLE_DEVICES'] = str(selected_gpu_id)
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Write query sequence
        query_fasta = os.path.join(tmp_dir, "query.fasta")
        with open(query_fasta, 'w') as f:
            f.write(f">query\n{sequence}\n")
        
        # Create query database
        query_db = os.path.join(tmp_dir, "query_db")
        subprocess.run([
            str(mmseqs_bin), "createdb", query_fasta, query_db
        ], check=True, capture_output=True, env=env)
        
        # Search against UniRef30
        result_db = os.path.join(tmp_dir, "result_db")
        search_cmd = [
            str(mmseqs_bin), "search",
            query_db, str(uniref_db), result_db,
            os.path.join(tmp_dir, "tmp"),
            "-s", str(sensitivity),
            "--max-seqs", "10000",
            "-e", str(evalue),
            "--split-memory-limit", "16G",  # Cap RAM to prevent OOM (safe for 8 parallel jobs)
        ]
        # Add taxonomy filter if specified
        if taxon_list:
            search_cmd.extend(["--taxon-list", taxon_list])
            print(f"Filtering MSA by taxonomy: {taxon_list}", flush=True)
        # Add sequence identity filter if specified
        if min_seq_id is not None:
            search_cmd.extend(["--min-seq-id", str(min_seq_id)])
            print(f"Minimum sequence identity: {min_seq_id:.1%}", flush=True)
        # Add coverage filter if specified
        if min_coverage is not None:
            search_cmd.extend(["-c", str(min_coverage), "--cov-mode", "0"])
            print(f"Minimum coverage: {min_coverage:.1%}", flush=True)
        # Add GPU flag or CPU-specific options
        if use_gpu_flag and selected_gpu_id is not None:
            search_cmd.extend(["--gpu", "1"])  # Enable GPU acceleration
        else:
            search_cmd.extend(["--threads", str(num_threads)])
        
        subprocess.run(search_cmd, check=True, capture_output=True, env=env)
        
        # Convert to A3M
        a3m_file = os.path.join(tmp_dir, "result.a3m")
        subprocess.run([
            str(mmseqs_bin), "result2msa",
            query_db, str(uniref_db), result_db, a3m_file
        ], check=True, capture_output=True, env=env)
        
        # Read result and strip null bytes (mmseqs adds trailing 0x00)
        with open(a3m_file, 'rb') as f:
            a3m_bytes = f.read().replace(b'\x00', b'')
        a3m_content = a3m_bytes.decode('utf-8')
        
        # ═══════════════════════════════════════════════════════════════════════════
        # HEADER-BASED TAXONOMY FILTERING (MMseqs2 --taxon-list requires taxonomy-
        # aware database which UniRef30 lacks, so we filter by TaxID in headers)
        # ═══════════════════════════════════════════════════════════════════════════
        if taxon_list:
            import re
            target_taxids = set(taxon_list.split(','))
            # Map of domain TaxIDs to all descendant TaxIDs (simplified top-level only)
            domain_map = {
                '2': 'Bacteria',      # All bacteria
                '2157': 'Archaea',    # All archaea  
                '2759': 'Eukaryota',  # All eukaryotes
                '10239': 'Viruses',   # All viruses
            }
            filter_domains = {domain_map.get(tid) for tid in target_taxids if tid in domain_map}
            
            if filter_domains:
                print(f"Post-filtering MSA by taxonomy domains: {filter_domains}", flush=True)
                filtered_entries = []
                current_entry = []
                original_count = 0
                
                for line in a3m_content.split('\n'):
                    if line.startswith('>'):
                        # Save previous entry
                        if current_entry:
                            original_count += 1
                            header = current_entry[0]
                            # Check if it's the query (always keep)
                            if header == '>query' or 'query' in header.lower()[:20]:
                                filtered_entries.append('\n'.join(current_entry))
                            else:
                                # Extract Tax= field and check against domains
                                tax_match = re.search(r'Tax=([^T]+?)(?:TaxID=|$)', header)
                                if tax_match:
                                    tax_name = tax_match.group(1).strip()
                                    # Simple domain detection from taxon names
                                    is_bacteria = any(kw in tax_name.lower() for kw in 
                                        ['bacteri', 'escherichia', 'salmonella', 'streptococcus', 
                                         'staphylococcus', 'pseudomonas', 'clostridium', 'bacillus',
                                         'vibrio', 'neisseria', 'enterobacter', 'klebsiella'])
                                    if 'Bacteria' in filter_domains and is_bacteria:
                                        filtered_entries.append('\n'.join(current_entry))
                                    elif 'Bacteria' not in filter_domains:
                                        # For non-bacteria filters, keep if not clearly bacterial
                                        filtered_entries.append('\n'.join(current_entry))
                                else:
                                    # No tax info - keep if filter is permissive
                                    filtered_entries.append('\n'.join(current_entry))
                        current_entry = [line]
                    else:
                        current_entry.append(line)
                
                # Don't forget the last entry
                if current_entry:
                    original_count += 1
                    header = current_entry[0]
                    if header == '>query' or 'query' in header.lower()[:20]:
                        filtered_entries.append('\n'.join(current_entry))
                    else:
                        tax_match = re.search(r'Tax=([^T]+?)(?:TaxID=|$)', header)
                        if tax_match:
                            tax_name = tax_match.group(1).strip()
                            is_bacteria = any(kw in tax_name.lower() for kw in 
                                ['bacteri', 'escherichia', 'salmonella', 'streptococcus',
                                 'staphylococcus', 'pseudomonas', 'clostridium', 'bacillus',
                                 'vibrio', 'neisseria', 'enterobacter', 'klebsiella'])
                            if 'Bacteria' in filter_domains and is_bacteria:
                                filtered_entries.append('\n'.join(current_entry))
                            elif 'Bacteria' not in filter_domains:
                                filtered_entries.append('\n'.join(current_entry))
                
                a3m_content = '\n'.join(filtered_entries)
                filtered_count = len(filtered_entries)
                print(f"Taxonomy post-filter: {original_count} -> {filtered_count} sequences", flush=True)
        
        # ═══════════════════════════════════════════════════════════════════════════
        # MSA QUALITY VALIDATION: Count sequences and check depth thresholds
        # ═══════════════════════════════════════════════════════════════════════════
        msa_depth = a3m_content.count('\n>') + (1 if a3m_content.startswith('>') else 0)
        print(f"MSA depth: {msa_depth} sequences", flush=True)
        
        # Generate quality report
        quality_report = {
            "msa_depth": msa_depth,
            "query_length": len(sequence),
            "evalue": evalue,
            "sensitivity": sensitivity,
            "taxon_filter": taxon_list,
            "min_seq_id": min_seq_id,
            "min_coverage": min_coverage,
        }
        
        # Check depth thresholds
        if min_depth_fail > 0 and msa_depth < min_depth_fail:
            error_msg = (
                f"MSA FAILED: Only {msa_depth} sequences found (minimum: {min_depth_fail}). "
                f"This likely indicates poor homolog detection. "
                f"Consider: 1) Relaxing filters, 2) Using taxonomy filter to target correct kingdom, "
                f"3) Checking if sequence is a known protein family."
            )
            print(f"ERROR: {error_msg}", flush=True)
            raise RuntimeError(error_msg)
        
        if msa_depth < min_depth_warning:
            print(
                f"WARNING: MSA has only {msa_depth} sequences (recommended >{min_depth_warning}). "
                f"Structure prediction confidence may be low. "
                f"Consider adjusting search parameters or verifying input sequence.",
                flush=True
            )
        
        # Write to output
        with open(final_a3m, 'w') as f:
            f.write(a3m_content)
        
        # Write quality report JSON alongside MSA
        import json
        report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
        with open(report_path, 'w') as f:
            json.dump(quality_report, f, indent=2)
        print(f"MSA quality report: {report_path}", flush=True)
        
        print(f"MSA generated: {final_a3m}", flush=True)
        
        # Save to cache
        if cache_dir:
            save_to_cache(cache_dir, seq_hash, a3m_content)
            
            # Release MSA lock after cache is populated
            if lock_fd is not None:
                release_msa_lock(lock_fd)
                print("MSA lock released", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate MSA using local MMseqs2 (GPU/CPU hybrid)")
    parser.add_argument("--sequence", required=True, help="Amino acid sequence")
    parser.add_argument("--name", required=True, help="Job name for output files")
    parser.add_argument("--out_dir", required=True, help="Output directory")
    parser.add_argument("--db_path", 
                        default=DEFAULT_DB_PATH,
                        help="Path to ColabFold database directory")
    parser.add_argument("--cache_dir",
                        default=DEFAULT_CACHE_DIR,
                        help="Cache directory (default: BMS_MSA_CACHE or BMS_DATA/msa_cache)")
    parser.add_argument("--max_age_days", type=int, default=0, 
                        help="Cache expiry in days (0 = never expire)")
    parser.add_argument("--force_refresh", action="store_true", 
                        help="Bypass cache")
    parser.add_argument("--threads", type=int, default=32, 
                        help="CPU threads for MMseqs2")
    parser.add_argument("--use-gpu", action="store_true",
                        help="Force GPU mode")
    parser.add_argument("--gpu-id", type=int, default=None,
                        help="Specific GPU device ID")
    parser.add_argument("--cpu-only", action="store_true",
                        help="Force CPU mode (no GPU)")
    parser.add_argument("--reference-sequence", type=str, default=None,
                        help="Reference sequence for cache key (mutagenesis mode)")
    # MSA Quality Parameters
    parser.add_argument("--evalue", type=float, default=0.001,
                        help="E-value threshold (lower = stricter, default: 0.001)")
    parser.add_argument("--sensitivity", type=float, default=8.0,
                        help="MMseqs2 sensitivity 1-8 (default: 8.0)")
    parser.add_argument("--min-seq-id", type=float, default=None,
                        help="Minimum sequence identity (0-1.0)")
    parser.add_argument("--min-coverage", type=float, default=None,
                        help="Minimum query coverage (0-1.0)")
    parser.add_argument("--taxon-list", type=str, default=None,
                        help="NCBI taxonomy IDs to filter (comma-separated, e.g. '2' for Bacteria)")
    parser.add_argument("--min-depth-warning", type=int, default=100,
                        help="Warn if MSA has fewer sequences (default: 100)")
    parser.add_argument("--min-depth-fail", type=int, default=10,
                        help="Fail if MSA has fewer sequences (0 = no fail, default: 10)")
    
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
        num_threads=args.threads,
        use_gpu=args.use_gpu if args.use_gpu else None,
        gpu_id=args.gpu_id,
        cpu_only=args.cpu_only,
        reference_sequence=args.reference_sequence,
        evalue=args.evalue,
        sensitivity=args.sensitivity,
        min_seq_id=args.min_seq_id,
        min_coverage=args.min_coverage,
        taxon_list=args.taxon_list,
        min_depth_warning=args.min_depth_warning,
        min_depth_fail=args.min_depth_fail,
    )
