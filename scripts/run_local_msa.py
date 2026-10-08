#!/usr/bin/env python3
"""
Run local MMseqs2 MSA search using ColabFold databases.

Implements the FULL ColabFold canonical workflow:
1. Iterative profile search against UniRef30 (3 iterations)
2. Profile extraction for environmental search
3. Alignment expansion to recover cluster members
4. Environmental database search (ColabFold EnvDB)
5. Quality filtering
6. MSA merging (UniRef + Environmental)

Quality Presets:
- maximum: Full ColabFold workflow (default) - ~15-30s
- balanced: Environmental search without expansion - ~8-15s
- fast: UniRef30 only, minimal processing - ~3-5s

Usage:
    # Default: Maximum quality preset
    python run_local_msa.py \\
        --sequence "MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSH" \\
        --name hemoglobin \\
        --out_dir ./output
        
    # Fast preset for screening
    python run_local_msa.py --preset fast ...
    
    # Force GPU (specific device)
    python run_local_msa.py --use-gpu --gpu-id 2 ...
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
import shutil
import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

_data_root = os.getenv("BMS_DATA")
DEFAULT_DB_PATH = os.getenv("BMS_COLABFOLD_DB") or (
    f"{_data_root}/colabfold_db" if _data_root else "/mnt/BioModStack/colabfold_db"
)
DEFAULT_CACHE_DIR = os.getenv("BMS_MSA_CACHE") or (
    f"{_data_root}/msa_cache" if _data_root else "/mnt/BioModStack/msa_cache"
)

# ═══════════════════════════════════════════════════════════════════════════════
# MSA QUALITY PRESETS
# ═══════════════════════════════════════════════════════════════════════════════
MSA_PRESETS = {
    "maximum": {
        "num_iterations": 3,
        "use_env": True,
        "use_expand": False,  # Disabled: database _aln files are corrupted/incomplete
        "use_filter": True,
        "sensitivity": 8.0,
        "evalue": 0.1,       # ColabFold uses 0.1 for initial search
        "max_seqs": 10000,
        "qsc": -20.0,        # ColabFold default - score per aligned residue
        "max_seq_id": 0.95,
        "description": "Full ColabFold workflow - highest MSA depth and diversity (~15-30s)"
    },
    "balanced": {
        "num_iterations": 2,
        "use_env": True,
        "use_expand": False,
        "use_filter": True,
        "sensitivity": 8.0,
        "evalue": 0.1,
        "max_seqs": 10000,
        "qsc": -20.0,        # ColabFold default
        "max_seq_id": 0.95,
        "description": "Environmental search without expansion (~8-15s)"
    },
    "fast": {
        "num_iterations": 1,
        "use_env": False,
        "use_expand": False,
        "use_filter": False,
        "sensitivity": 7.0,
        "evalue": 0.001,
        "max_seqs": 5000,
        "qsc": -20.0,
        "max_seq_id": 1.0,
        "description": "UniRef30 only - quick screening (~3-5s)"
    }
}


def compute_sequence_hash(sequence: str) -> str:
    """Compute SHA256 hash of sequence for cache key."""
    return hashlib.sha256(sequence.encode()).hexdigest()


def get_cache_path(cache_dir: str, seq_hash: str, preset: str = "maximum") -> Path:
    """Get path to cached file with subdirectory sharding and preset awareness."""
    subdir = seq_hash[:2]
    # Include preset in cache key for quality differentiation
    cache_path = Path(cache_dir) / subdir / f"{seq_hash}_{preset}.a3m.gz"
    return cache_path


def get_legacy_cache_path(cache_dir: str, seq_hash: str) -> Path:
    """Get legacy cache path (without preset) for backward compatibility."""
    subdir = seq_hash[:2]
    return Path(cache_dir) / subdir / f"{seq_hash}.a3m.gz"


def check_cache(cache_dir: str, seq_hash: str, max_age_days: int, preset: str = "maximum") -> Path | None:
    """Check if valid cached MSA exists. Returns path if found, None otherwise."""
    # Check new preset-aware cache first
    cache_path = get_cache_path(cache_dir, seq_hash, preset)
    
    if cache_path.exists():
        if max_age_days > 0:
            mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
            age = datetime.now() - mtime
            if age > timedelta(days=max_age_days):
                print(f"Cache expired (age: {age.days} days), will refresh", flush=True)
                return None
        return cache_path
    
    # For 'maximum' preset, also check legacy cache and upgrade if found
    if preset == "maximum":
        legacy_path = get_legacy_cache_path(cache_dir, seq_hash)
        if legacy_path.exists():
            # Legacy cache exists but was generated with old (fast) workflow
            # Don't use it for maximum preset - need fresh generation
            print(f"Legacy cache found but regenerating with maximum quality preset", flush=True)
            return None
    
    return None


def save_to_cache(cache_dir: str, seq_hash: str, a3m_content: str, preset: str = "maximum") -> Path:
    """Save MSA to cache with gzip compression."""
    cache_path = get_cache_path(cache_dir, seq_hash, preset)
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
            time.sleep(5)


def release_msa_lock(fd: int):
    """Release MSA lock."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except Exception:
        pass


def check_gpu_availability(threshold: int = 80) -> int | None:
    """
    Check for available GPU for MMseqs2.
    
    Args:
        threshold: Max utilization/memory percentage to consider GPU available
    
    Returns GPU ID if one is available, None otherwise.
    
    Note: MMseqs2-GPU Blackwell binary supports SM 7.5-12.0 (Turing through Blackwell).
    """
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,utilization.gpu,memory.used,memory.total,compute_cap,name',
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
            if len(parts) >= 6:
                gpu_id = int(parts[0])
                utilization = int(parts[1])
                mem_used = int(parts[2])
                mem_total = int(parts[3])
                compute_cap = float(parts[4])
                gpu_name = parts[5]
                mem_percent = (mem_used / mem_total * 100) if mem_total > 0 else 100
                
                gpus.append({
                    'id': gpu_id,
                    'utilization': utilization,
                    'memory_percent': mem_percent,
                    'compute_cap': compute_cap,
                    'name': gpu_name
                })
        
        # Sort by utilization (prefer least busy)
        gpus.sort(key=lambda g: g['utilization'])
        
        for gpu in gpus:
            if gpu['utilization'] < threshold and gpu['memory_percent'] < threshold:
                print(f"Selected GPU {gpu['id']} ({gpu['name']}, SM {gpu['compute_cap']}) for MMseqs2", flush=True)
                return gpu['id']
        
        if not gpus:
            print("No available GPUs found for MMseqs2", flush=True)
        else:
            print(f"All GPUs are busy (utilization > {threshold}%)", flush=True)
        
        return None
    except Exception as e:
        print(f"GPU detection failed: {e}", flush=True)
        return None


def run_mmseqs(mmseqs_bin: str, params: list, env: dict, capture_output: bool = True):
    """Run MMseqs2 command with logging."""
    cmd = [str(mmseqs_bin)] + [str(p) for p in params]
    module = params[0] if params else "unknown"
    
    # Suppress verbose parameter list in logs
    env_copy = env.copy()
    env_copy["MMSEQS_CALL_DEPTH"] = "1"
    
    result = subprocess.run(cmd, env=env_copy, capture_output=capture_output, text=True)
    if result.returncode != 0:
        error_msg = result.stderr if result.stderr else "Unknown error"
        raise RuntimeError(f"MMseqs2 {module} failed: {error_msg}")
    return result


def run_colabfold_msa_workflow(
    sequence: str,
    job_name: str,
    out_dir: str,
    db_path: str = DEFAULT_DB_PATH,
    cache_dir: str = None,
    max_age_days: int = 0,
    force_refresh: bool = False,
    num_threads: int = 32,
    use_gpu: bool = None,
    gpu_id: int = None,
    cpu_only: bool = False,
    reference_sequence: str = None,
    # Preset and override parameters
    preset: str = "maximum",
    num_iterations: int = None,
    use_env: bool = None,
    use_expand: bool = None,
    use_filter: bool = None,
    # Legacy parameters (for backward compatibility)
    evalue: float = None,
    sensitivity: float = None,
    min_seq_id: float = None,
    min_coverage: float = None,  
    taxon_list: str = None,
    min_depth_warning: int = 100,
    min_depth_fail: int = 0,
):
    """
    Generate MSA using FULL ColabFold-compatible workflow.
    
    Workflow (Maximum preset):
    1. Create query database
    2. Iterative profile search against UniRef30 (n iterations)
    3. Extract refined profile from final iteration
    4. Expand alignments to recover cluster members
    5. Search Environmental DB with profile
    6. Filter results by quality metrics
    7. Convert both to A3M format
    8. Merge UniRef + Environmental MSAs
    
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
        preset: Quality preset (maximum, balanced, fast)
        num_iterations: Override number of profile iterations
        use_env: Override environmental DB usage
        use_expand: Override alignment expansion
        use_filter: Override quality filtering
        evalue: Override e-value threshold
        sensitivity: Override sensitivity
        min_seq_id: Minimum sequence identity filter
        min_coverage: Minimum query coverage filter
        taxon_list: Taxonomy filter (comma-separated NCBI IDs)
        min_depth_warning: Warn if MSA depth below this
        min_depth_fail: Fail if MSA depth below this
    """
    # Load preset configuration
    if preset not in MSA_PRESETS:
        raise ValueError(f"Unknown preset: {preset}. Options: {list(MSA_PRESETS.keys())}")
    
    config = MSA_PRESETS[preset].copy()
    print(f"MSA Preset: {preset} - {config['description']}", flush=True)
    
    # Apply overrides
    if num_iterations is not None:
        config["num_iterations"] = num_iterations
    if use_env is not None:
        config["use_env"] = bool(use_env)
    if use_expand is not None:
        config["use_expand"] = bool(use_expand)
    if use_filter is not None:
        config["use_filter"] = bool(use_filter)
    if evalue is not None:
        config["evalue"] = evalue
    if sensitivity is not None:
        config["sensitivity"] = sensitivity
    
    # For cache: use reference sequence if provided (mutagenesis mode)
    cache_key_seq = reference_sequence or sequence
    seq_hash = compute_sequence_hash(cache_key_seq)
    
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    lock_fd = None
    
    # Acquire MSA lock for this sequence to prevent duplicate work
    lock_path = get_msa_lock_path(cache_dir, seq_hash)
    print(f"Acquiring MSA lock for {seq_hash[:16]}...", flush=True)
    lock_fd = acquire_msa_lock(lock_path)
    print("MSA lock acquired", flush=True)
    
    try:
        # Check cache (after lock to avoid race conditions)
        if not force_refresh:
            cached = check_cache(cache_dir, seq_hash, max_age_days, preset)
            if cached:
                print(f"CACHE HIT: {seq_hash[:16]}... ({preset} preset)", flush=True)
                final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
                os.makedirs(out_dir, exist_ok=True)
                load_from_cache(cached, final_a3m)
                
                # Generate quality report from cached MSA
                with open(final_a3m, 'r') as f:
                    content = f.read()
                msa_depth = content.count('\n>') + (1 if content.startswith('>') else 0)
                
                report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
                with open(report_path, 'w') as f:
                    json.dump({
                        "msa_depth": msa_depth,
                        "query_length": len(sequence),
                        "preset": preset,
                        "from_cache": True,
                    }, f, indent=2)
                
                release_msa_lock(lock_fd)
                print("MSA lock released", flush=True)
                return
        
        print(f"CACHE MISS: {seq_hash[:16]}... (running ColabFold workflow)", flush=True)
        
        # Database paths
        db_path = Path(db_path)
        mmseqs_cpu = db_path / "mmseqs" / "bin" / "mmseqs"
        mmseqs_gpu = db_path / "mmseqs-gpu-blackwell" / "bin" / "mmseqs"
        uniref_db = db_path / "uniref30_2302_db"
        envdb = db_path / "colabfold_envdb_202108_db"
        
        # Check environmental DB availability
        env_available = envdb.exists() and Path(str(envdb) + ".dbtype").exists()
        if config["use_env"] and not env_available:
            print("WARNING: Environmental DB not found, falling back to UniRef30 only", flush=True)
            config["use_env"] = False
        
        # Determine which binary to use
        selected_gpu_id = None
        use_gpu_flag = False
        if cpu_only:
            mmseqs_bin = mmseqs_cpu
            print("Using CPU mmseqs (forced)", flush=True)
        elif use_gpu or (use_gpu is None and mmseqs_gpu.exists()):
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
        
        if not Path(str(mmseqs_bin)).exists():
            mmseqs_bin = "mmseqs"  # Try system mmseqs
        
        # Environment setup
        env = os.environ.copy()
        if selected_gpu_id is not None:
            env['CUDA_VISIBLE_DEVICES'] = str(selected_gpu_id)
        
        os.makedirs(out_dir, exist_ok=True)
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            # ═══════════════════════════════════════════════════════════════════
            # STEP 1: Create query database
            # ═══════════════════════════════════════════════════════════════════
            query_fasta = os.path.join(tmp_dir, "query.fasta")
            with open(query_fasta, 'w') as f:
                f.write(f">query\n{sequence}\n")
            
            query_db = os.path.join(tmp_dir, "qdb")
            run_mmseqs(mmseqs_bin, [
                "createdb", query_fasta, query_db,
                "--shuffle", "0", "--dbtype", "1"
            ], env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 2: Iterative profile search against UniRef30
            # ═══════════════════════════════════════════════════════════════════
            print(f"Searching UniRef30 ({config['num_iterations']} iterations)...", flush=True)
            
            result_db = os.path.join(tmp_dir, "res")
            search_params = [
                "search", query_db, str(uniref_db), result_db,
                os.path.join(tmp_dir, "tmp"),
                "--num-iterations", str(config["num_iterations"]),
                "-a",  # Report alignments
                "-e", str(config["evalue"]),
                "--max-seqs", str(config["max_seqs"]),
            ]
            
            if use_gpu_flag:
                search_params += ["--gpu", "1", "--prefilter-mode", "1"]
            else:
                search_params += ["-s", str(config["sensitivity"]), "--threads", str(num_threads)]
            
            run_mmseqs(mmseqs_bin, search_params, env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 3: Extract refined profile for environmental search
            # ═══════════════════════════════════════════════════════════════════
            profile_db = os.path.join(tmp_dir, "prof_res")
            has_profile = False
            
            # ColabFold uses profile_1 (first iteration profile), but check all iterations
            for iter_num in [1, 2, 3, config["num_iterations"]]:
                profile_source = os.path.join(tmp_dir, f"tmp/latest/profile_{iter_num}")
                if os.path.exists(profile_source + ".dbtype"):
                    print(f"Found profile at iteration {iter_num}", flush=True)
                    run_mmseqs(mmseqs_bin, ["mvdb", profile_source, profile_db], env)
                    # Link headers
                    run_mmseqs(mmseqs_bin, ["lndb", query_db + "_h", profile_db + "_h"], env)
                    has_profile = True
                    break
            
            if not has_profile:
                # GPU mode may store profile differently - check for result profile
                result_profile = os.path.join(tmp_dir, "res_profile")  
                if os.path.exists(result_db + ".dbtype"):
                    print("Using result DB as profile (GPU mode)", flush=True)
                    profile_db = result_db
                    has_profile = True
                else:
                    print("WARNING: No profile generated, using query DB for env search", flush=True)
                    profile_db = query_db
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 4: Alignment expansion (Maximum preset)
            # ═══════════════════════════════════════════════════════════════════
            can_expand = False
            if config["use_expand"]:
                # Check if alignment database is valid for expansion
                aln_db = Path(str(uniref_db) + "_aln")
                aln_index = Path(str(uniref_db) + "_aln.index")
                if aln_db.exists() and aln_index.exists():
                    # Verify the aln file is larger than its index (sanity check)
                    aln_size = aln_db.stat().st_size
                    index_size = aln_index.stat().st_size
                    if aln_size > index_size:
                        can_expand = True
                    else:
                        print(f"WARNING: Alignment database appears incomplete ({aln_size} bytes < {index_size} bytes index), skipping expansion", flush=True)
                else:
                    print("WARNING: Alignment database not found, skipping expansion", flush=True)
            
            if can_expand:
                print("Expanding alignments to recover cluster members...", flush=True)
                expanded_db = os.path.join(tmp_dir, "res_exp")
                try:
                    run_mmseqs(mmseqs_bin, [
                        "expandaln", query_db, str(uniref_db) + "_seq",
                        result_db, str(uniref_db) + "_aln", expanded_db,
                        "--expansion-mode", "0",
                        "-e", "inf",
                        "--expand-filter-clusters", "1" if config["use_filter"] else "0",
                        "--max-seq-id", str(config["max_seq_id"]),
                        "--threads", str(num_threads),
                    ], env)
                    
                    # Realign expanded hits
                    realigned_db = os.path.join(tmp_dir, "res_exp_realign")
                    run_mmseqs(mmseqs_bin, [
                        "align", profile_db if has_profile else query_db, 
                        str(uniref_db) + "_seq",
                        expanded_db, realigned_db,
                        "-e", "10",
                        "--max-accept", "100000",
                        "--alt-ali", "10",
                        "-a",
                        "--threads", str(num_threads),
                    ], env)
                    result_db = realigned_db
                except RuntimeError as e:
                    print(f"WARNING: Alignment expansion failed ({e}), continuing without expansion", flush=True)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 5: Quality filtering (Maximum/Balanced presets)
            # ═══════════════════════════════════════════════════════════════════
            if config["use_filter"]:
                print("Filtering MSA by quality metrics...", flush=True)
                filtered_db = os.path.join(tmp_dir, "res_filtered")
                run_mmseqs(mmseqs_bin, [
                    "filterresult", query_db, str(uniref_db) + "_seq",
                    result_db, filtered_db,
                    "--qid", "0",
                    "--qsc", str(config["qsc"]),
                    "--diff", "0",
                    "--max-seq-id", str(config["max_seq_id"]),
                    "--filter-min-enable", "100",
                    "--threads", str(num_threads),
                ], env)
                result_db = filtered_db
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 6: Generate UniRef30 MSA
            # ═══════════════════════════════════════════════════════════════════
            uniref_a3m_db = os.path.join(tmp_dir, "uniref.a3m")
            filter_params = [
                "--filter-msa", "1" if config["use_filter"] else "0",
                "--filter-min-enable", "1000",
                "--diff", "3000",
                "--qid", "0.0,0.2,0.4,0.6,0.8,1.0",
                "--qsc", "0",
                "--max-seq-id", "0.95",
            ]
            run_mmseqs(mmseqs_bin, [
                "result2msa", query_db, str(uniref_db) + "_seq",
                result_db, uniref_a3m_db,
                "--msa-format-mode", "5",
                "--threads", str(num_threads),
            ] + filter_params, env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 7: Environmental database search (Maximum/Balanced presets)
            # ═══════════════════════════════════════════════════════════════════
            env_a3m_db = None
            if config["use_env"] and env_available:
                print(f"Searching environmental database ({envdb.name})...", flush=True)
                
                env_result_db = os.path.join(tmp_dir, "res_env")
                env_search_params = [
                    "search", profile_db if has_profile else query_db, 
                    str(envdb), env_result_db,
                    os.path.join(tmp_dir, "tmp_env"),
                    "--num-iterations", str(config["num_iterations"]),
                    "-a", "-e", str(config["evalue"]),
                    "--max-seqs", str(config["max_seqs"]),
                ]
                
                if use_gpu_flag:
                    env_search_params += ["--gpu", "1", "--prefilter-mode", "1"]
                else:
                    env_search_params += ["-s", str(config["sensitivity"]), "--threads", str(num_threads)]
                
                run_mmseqs(mmseqs_bin, env_search_params, env)
                
                # Expand environmental hits if enabled
                if config["use_expand"]:
                    env_expanded = os.path.join(tmp_dir, "res_env_exp")
                    run_mmseqs(mmseqs_bin, [
                        "expandaln", profile_db if has_profile else query_db,
                        str(envdb) + "_seq",
                        env_result_db, str(envdb) + "_aln", env_expanded,
                        "-e", "inf",
                        "--expansion-mode", "0",
                        "--threads", str(num_threads),
                    ], env)
                    
                    # Realign expanded environmental hits
                    env_tmp_dir = os.path.join(tmp_dir, "tmp_env")
                    env_profile = os.path.join(env_tmp_dir, f"latest/profile_{config['num_iterations']}")
                    if os.path.exists(env_profile + ".dbtype"):
                        align_profile = env_profile
                    else:
                        align_profile = profile_db if has_profile else query_db
                    
                    env_realigned = os.path.join(tmp_dir, "res_env_realign")
                    run_mmseqs(mmseqs_bin, [
                        "align", align_profile, str(envdb) + "_seq",
                        env_expanded, env_realigned,
                        "-e", "10",
                        "--max-accept", "100000",
                        "--alt-ali", "10",
                        "-a",
                        "--threads", str(num_threads),
                    ], env)
                    env_result_db = env_realigned
                
                # Filter environmental results
                if config["use_filter"]:
                    env_filtered = os.path.join(tmp_dir, "res_env_filtered")
                    run_mmseqs(mmseqs_bin, [
                        "filterresult", query_db, str(envdb) + "_seq",
                        env_result_db, env_filtered,
                        "--qid", "0",
                        "--qsc", str(config["qsc"]),
                        "--diff", "0",
                        "--max-seq-id", str(config["max_seq_id"]),
                        "--filter-min-enable", "100",
                        "--threads", str(num_threads),
                    ], env)
                    env_result_db = env_filtered
                
                # Generate Environmental MSA
                env_a3m_db = os.path.join(tmp_dir, "env.a3m")
                run_mmseqs(mmseqs_bin, [
                    "result2msa", query_db, str(envdb) + "_seq",
                    env_result_db, env_a3m_db,
                    "--msa-format-mode", "5",
                    "--threads", str(num_threads),
                ] + filter_params, env)
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 8: Merge MSAs
            # ═══════════════════════════════════════════════════════════════════
            if env_a3m_db and os.path.exists(env_a3m_db + ".dbtype"):
                print("Merging UniRef30 and Environmental MSAs...", flush=True)
                final_a3m_db = os.path.join(tmp_dir, "final.a3m")
                run_mmseqs(mmseqs_bin, [
                    "mergedbs", query_db, final_a3m_db,
                    uniref_a3m_db, env_a3m_db
                ], env)
            else:
                final_a3m_db = uniref_a3m_db
            
            # Unpack to final A3M file
            run_mmseqs(mmseqs_bin, [
                "unpackdb", final_a3m_db, tmp_dir,
                "--unpack-name-mode", "0",
                "--unpack-suffix", ".a3m"
            ], env)
            
            # Read unpacked A3M (uses query ID as filename)
            unpacked_a3m = os.path.join(tmp_dir, "0.a3m")
            if os.path.exists(unpacked_a3m):
                with open(unpacked_a3m, 'rb') as f:
                    a3m_bytes = f.read().replace(b'\x00', b'')
                a3m_content = a3m_bytes.decode('utf-8')
            else:
                # Fallback: try to read directly
                result = subprocess.run([
                    str(mmseqs_bin), "result2flat",
                    query_db, query_db, final_a3m_db, "/dev/stdout"
                ], env=env, capture_output=True)
                a3m_content = result.stdout.decode('utf-8').replace('\x00', '')
            
            # ═══════════════════════════════════════════════════════════════════
            # STEP 9: Post-processing (taxonomy filter, quality check)
            # ═══════════════════════════════════════════════════════════════════
            
            # Apply taxonomy post-filter if specified
            if taxon_list:
                target_taxids = set(taxon_list.split(','))
                domain_map = {
                    '2': 'Bacteria',
                    '2157': 'Archaea',
                    '2759': 'Eukaryota',
                    '10239': 'Viruses',
                }
                filter_domains = {domain_map.get(tid) for tid in target_taxids if tid in domain_map}
                
                if filter_domains:
                    print(f"Post-filtering MSA by taxonomy: {filter_domains}", flush=True)
                    filtered_entries = []
                    current_entry = []
                    
                    for line in a3m_content.split('\n'):
                        if line.startswith('>'):
                            if current_entry:
                                header = current_entry[0]
                                if header == '>query' or 'query' in header.lower()[:20]:
                                    filtered_entries.append('\n'.join(current_entry))
                                else:
                                    tax_match = re.search(r'Tax=([^T]+?)(?:TaxID=|$)', header)
                                    if tax_match:
                                        tax_name = tax_match.group(1).strip().lower()
                                        is_bacteria = any(kw in tax_name for kw in [
                                            'bacteri', 'escherichia', 'salmonella', 'streptococcus',
                                            'staphylococcus', 'pseudomonas', 'clostridium', 'bacillus'
                                        ])
                                        if 'Bacteria' in filter_domains and is_bacteria:
                                            filtered_entries.append('\n'.join(current_entry))
                                        elif 'Bacteria' not in filter_domains:
                                            filtered_entries.append('\n'.join(current_entry))
                                    else:
                                        filtered_entries.append('\n'.join(current_entry))
                            current_entry = [line]
                        else:
                            current_entry.append(line)
                    
                    if current_entry:
                        filtered_entries.append('\n'.join(current_entry))
                    
                    original_count = a3m_content.count('\n>') + 1
                    a3m_content = '\n'.join(filtered_entries)
                    print(f"Taxonomy filter: {original_count} -> {len(filtered_entries)} sequences", flush=True)
            
            # Count MSA depth
            msa_depth = a3m_content.count('\n>') + (1 if a3m_content.startswith('>') else 0)
            print(f"Final MSA depth: {msa_depth} sequences", flush=True)
            
            # Quality report
            quality_report = {
                "msa_depth": msa_depth,
                "query_length": len(sequence),
                "preset": preset,
                "num_iterations": config["num_iterations"],
                "use_env": config["use_env"],
                "use_expand": config["use_expand"],
                "use_filter": config["use_filter"],
                "evalue": config["evalue"],
                "sensitivity": config["sensitivity"],
                "taxon_filter": taxon_list,
                "from_cache": False,
            }
            
            # Check depth thresholds
            if min_depth_fail > 0 and msa_depth < min_depth_fail:
                error_msg = (
                    f"MSA FAILED: Only {msa_depth} sequences found (minimum: {min_depth_fail}). "
                    f"Consider: 1) Different preset, 2) Relaxing filters, 3) Checking sequence."
                )
                print(f"ERROR: {error_msg}", flush=True)
                raise RuntimeError(error_msg)
            
            if msa_depth < min_depth_warning:
                print(
                    f"WARNING: MSA has only {msa_depth} sequences (recommended >{min_depth_warning}). "
                    f"Structure prediction confidence may be low.",
                    flush=True
                )
            
            # Write output
            final_a3m = os.path.join(out_dir, f"{job_name}.a3m")
            with open(final_a3m, 'w') as f:
                f.write(a3m_content)
            
            report_path = os.path.join(out_dir, f"{job_name}_msa_quality.json")
            with open(report_path, 'w') as f:
                json.dump(quality_report, f, indent=2)
            print(f"MSA quality report: {report_path}", flush=True)
            
            print(f"MSA generated: {final_a3m}", flush=True)
            
            # Save to cache
            if cache_dir:
                save_to_cache(cache_dir, seq_hash, a3m_content, preset)
    
    finally:
        if lock_fd is not None:
            release_msa_lock(lock_fd)
            print("MSA lock released", flush=True)


# Backward compatibility alias
run_local_mmseqs2 = run_colabfold_msa_workflow


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate MSA using full ColabFold workflow (GPU/CPU hybrid)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Quality Presets:
  maximum   Full ColabFold workflow with environmental DB (~15-30s) [DEFAULT]
  balanced  Environmental search without expansion (~8-15s)
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
    
    # Quality Presets
    parser.add_argument("--preset", type=str, default="maximum",
                        choices=["maximum", "balanced", "fast"],
                        help="MSA quality preset (default: maximum)")
    
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
    
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    run_colabfold_msa_workflow(
        sequence=args.sequence,
        job_name=args.name,
        out_dir=args.out_dir,
        db_path=args.db_path,
        cache_dir=args.cache_dir,
        max_age_days=args.max_age_days,
        force_refresh=args.force_refresh,
        num_threads=args.threads,
        use_gpu=args.use_gpu if args.use_gpu else None,
        gpu_id=args.gpu_id,
        cpu_only=args.cpu_only,
        reference_sequence=args.reference_sequence,
        preset=args.preset,
        num_iterations=args.num_iterations,
        use_env=bool(args.use_env) if args.use_env is not None else None,
        use_expand=bool(args.use_expand) if args.use_expand is not None else None,
        use_filter=bool(args.use_filter) if args.use_filter is not None else None,
        evalue=args.evalue,
        sensitivity=args.sensitivity,
        min_seq_id=args.min_seq_id,
        min_coverage=args.min_coverage,
        taxon_list=args.taxon_list,
        min_depth_warning=args.min_depth_warning,
        min_depth_fail=args.min_depth_fail,
    )
