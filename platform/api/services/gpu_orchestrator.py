"""
GPU Orchestrator Service

A background service that manages GPU job scheduling using bin-packing.
Runs alongside the FastAPI server and polls for pending jobs.

Key Features:
- VRAM-aware bin-packing (First Fit Decreasing)
- Multi-job per GPU (packs until target fill reached)
- User-configurable target VRAM fill via slider
- GPU enable/disable support
- OOM detection and retry handling
"""

import asyncio
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass

# Will be imported when integrated with FastAPI
# from database import Job, async_session
# from routers.gpu import get_gpu_stats
# from services.nextflow import launch_nextflow_job

logger = logging.getLogger(__name__)

from services.gpu_config import read_scheduler_config
from services.gpu_metadata import GPU_CAPABILITIES


@dataclass
class GPUState:
    """Snapshot of a single GPU's state."""
    index: int
    name: str
    memory_used_mb: int
    memory_total_mb: int
    memory_free_mb: int
    utilization: int
    temperature: int


@dataclass
class JobInfo:
    """Minimal job info for packing algorithm."""
    id: str
    name: str
    model_type: str
    vram_estimate_mb: int
    sequence_length: int
    priority: int
    pinned_gpu: Optional[int]
    created_at: datetime
    batch_id: Optional[str] = None  # For GPU locking - all jobs in a batch share exclusive GPU access
    pinned_gpus: Optional[List[int]] = None  # Multi-GPU allowlist for parallel distribution


# ═══════════════════════════════════════════════════════════════════════════════
# VRAM ESTIMATION PROFILES
# ═══════════════════════════════════════════════════════════════════════════════

# Base VRAM + scaling factor per model
# Formula: base + scale * (sequence_length / 100)^2
# NOTE: These estimates are tuned for actual observed VRAM usage.
# Boltz-2 typically uses 6-8GB even for larger proteins.
VRAM_PROFILES = {
    'boltz': {'base': 5000, 'scale': 20},       # Boltz-2 structure prediction (~6-8GB observed)
    'boltz_batch': {'base': 6000, 'scale': 25}, # Boltz-2 batch mode
    'boltzgen': {'base': 5000, 'scale': 20},    # BoltzGen design
    'rf3': {'base': 10000, 'scale': 55},        # RosettaFold3 (high VRAM)
    'af2': {'base': 12000, 'scale': 65},        # AlphaFold2 (highest VRAM)
    'rfdiffusion': {'base': 5000, 'scale': 25}, # RFdiffusion
    'rfantibody': {'base': 6000, 'scale': 25},  # RFantibody antibody generation
    'rfantibody_child': {'base': 6000, 'scale': 25},  # RFantibody child (same as parent)
    # ──────────────────────────────────────────────────────────────────────────
    # MPNN VARIANTS - All share similar lightweight architecture (~2-4GB)
    # ──────────────────────────────────────────────────────────────────────────
    'fampnn': {'base': 3000, 'scale': 10},      # FAMPNN sequence design (CDR-focused)
    'fampnn_child': {'base': 3000, 'scale': 10},# FAMPNN child jobs
    'proteinmpnn': {'base': 2000, 'scale': 5},  # ProteinMPNN (vanilla)
    'mpnn': {'base': 2000, 'scale': 5},         # Alias for ProteinMPNN
    'ligandmpnn': {'base': 2500, 'scale': 8},   # LigandMPNN (ligand-aware sequence design)
    'thermompnn': {'base': 2000, 'scale': 5},   # ThermoMPNN (stability-focused)
    'frustrampnn': {'base': 2500, 'scale': 8},  # FrustraMPNN (frustration analysis)
    # ──────────────────────────────────────────────────────────────────────────
    # PROTENIX VARIANTS - measured from docs: 6GB@500tok → 78GB@4000tok
    # ──────────────────────────────────────────────────────────────────────────
    'protenix': {'base': 4000, 'scale': 55},          # Protenix base (MSA mode)
    'protenix_esm': {'base': 6000, 'scale': 60},      # Protenix ESM2-3B (no-MSA, heavier)
    'protenix_mini_esm': {'base': 5000, 'scale': 50}, # Protenix mini ESM (lighter no-MSA)
    # ──────────────────────────────────────────────────────────────────────────
    'diffdock': {'base': 4000, 'scale': 12},    # DiffDock
    'unidock': {'base': 3000, 'scale': 8},      # Uni-Dock
    'msa_batch': {'base': 3000, 'scale': 2},    # MSA Generation (GPU streaming, LOW VRAM)
    'antibody_child': {'base': 6000, 'scale': 25},  # Antibody validation (Boltz + scoring) ~6-8GB
    'antibody_denovo': {'base': 6000, 'scale': 25},  # Full antibody pipeline
    'oligo_design': {'base': 7000, 'scale': 20},     # Oligo Designer (RFDpoly + NA-MPNN)
    'default': {'base': 6000, 'scale': 25},     # Conservative fallback
}

# Models that need heavy GPUs (exclude 5060 Ti)
HEAVY_MODELS = {'af2', 'rfdiffusion', 'rf3'}
PROTENIX_MODELS = {'protenix', 'protenix_esm', 'protenix_mini_esm'}


def estimate_vram(model_type: str, sequence_length: int) -> int:
    """
    Estimate VRAM required for a job.
    
    Uses quadratic scaling: base + scale * (length/100)^2
    """
    profile = VRAM_PROFILES.get(model_type, VRAM_PROFILES['default'])
    base = profile['base']
    scale = profile['scale']
    
    # Quadratic scaling for attention-based models
    length_factor = (sequence_length / 100) ** 2
    estimated = int(base + scale * length_factor)
    
    return estimated


def _normalize_job_params(raw_params: Any) -> Dict[str, Any]:
    """Ensure job params are a dict, handling JSON-encoded strings safely."""
    if raw_params is None:
        return {}
    if isinstance(raw_params, dict):
        return raw_params
    if isinstance(raw_params, str):
        try:
            import json
            loaded = json.loads(raw_params)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}
    return {}


def _normalize_pinned_gpus(raw_value: Any) -> Optional[List[int]]:
    """Normalize pinned_gpus to a list of ints, or None if invalid."""
    if raw_value is None:
        return None
    values = []
    if isinstance(raw_value, list):
        values = raw_value
    elif isinstance(raw_value, str):
        values = [v.strip() for v in raw_value.split(",") if v.strip()]
    else:
        return None

    normalized = []
    for value in values:
        try:
            normalized.append(int(value))
        except (TypeError, ValueError):
            continue
    return normalized or None


def _normalize_gpu_id_list(raw_value: Any) -> Optional[List[int]]:
    """Normalize scheduler-config GPU list (list or comma string) to sorted ints."""
    if raw_value is None:
        return None
    values = []
    if isinstance(raw_value, list):
        values = raw_value
    elif isinstance(raw_value, str):
        values = [v.strip() for v in raw_value.split(",") if v.strip()]
    else:
        return None

    normalized = []
    for value in values:
        try:
            normalized.append(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(normalized)) or None


def _median(values: List[int]) -> Optional[int]:
    if not values:
        return None
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2 == 1:
        return sorted_vals[mid]
    return int((sorted_vals[mid - 1] + sorted_vals[mid]) / 2)


def _compute_auto_limit(
    model_id: str,
    job_infos: List["JobInfo"],
    gpu_stats: List[Any],
    config: Dict[str, Any]
) -> int:
    """Compute a VRAM-based concurrency cap for a model."""
    model_vrams = [j.vram_estimate_mb for j in job_infos if j.model_type == model_id]
    vram_estimate = _median(model_vrams)
    if vram_estimate is None:
        vram_estimate = estimate_vram(model_id, 300)
    vram_estimate = max(1, int(vram_estimate))

    target_fill = config.get("global", {}).get("target_vram_fill", 0.85)
    limit = 0
    for gpu in gpu_stats:
        if is_gpu_disabled(gpu.index, config):
            continue
        if model_id in HEAVY_MODELS:
            gpu_caps = GPU_CAPABILITIES.get(gpu.index, {'supports_heavy': True})
            if not gpu_caps.get('supports_heavy', True):
                continue

        gpu_override = config.get("overrides", {}).get(str(gpu.index), {})
        safety_margin = gpu_override.get("vram_safety_margin_mb", 500)
        available = (gpu.memory_total_mb * target_fill) - gpu.memory_used_mb - safety_margin
        if available <= 0:
            continue
        limit += max(0, math.floor(available / vram_estimate))

    return max(0, limit)


def is_gpu_disabled(gpu_index: int, config: Dict[str, Any]) -> bool:
    """Check if a GPU is disabled in config."""
    overrides = config.get("overrides", {})
    gpu_override = overrides.get(str(gpu_index), {})
    return gpu_override.get("disabled", False)


def get_workflow_pin(job_model_type: str, config: Dict[str, Any]) -> Optional[int]:
    """
    Check if a workflow type has a workflow-level GPU pin.
    
    Workflow pins are set via the API to route all jobs of a specific model_type
    (e.g., 'boltz', 'fampnn', 'rfantibody') to a specific GPU.
    
    Returns the pinned GPU index, or None if no workflow pin exists.
    """
    workflow_pins = config.get("workflow_pins", {})
    if job_model_type in workflow_pins:
        return workflow_pins[job_model_type]
    return None


def is_gpu_locked(gpu_index: int, batch_id: Optional[str], config: Dict[str, Any]) -> bool:
    """
    Check if a GPU is locked for exclusive use by a different batch.
    
    GPU locks are used when a user pins all child jobs in a batch to a single GPU
    AND locks that GPU from other workflows.
    
    Returns True if the GPU is locked by a DIFFERENT batch, False otherwise.
    """
    gpu_locks = config.get("gpu_locks", {})
    for locked_batch, locked_gpu in gpu_locks.items():
        if locked_gpu == gpu_index:
            # GPU is locked - only allow if this job is from the same batch
            if batch_id and batch_id == locked_batch:
                return False  # Same batch, allowed
            return True  # Different batch, blocked
    return False


def get_batch_lock_gpu(batch_id: Optional[str], config: Dict[str, Any]) -> Optional[int]:
    """
    Get the GPU that a batch has locked for exclusive use.
    
    Returns the locked GPU index, or None if no lock exists.
    """
    if not batch_id:
        return None
    gpu_locks = config.get("gpu_locks", {})
    return gpu_locks.get(batch_id)


# ═══════════════════════════════════════════════════════════════════════════════
# BIN-PACKING ALGORITHM
# ═══════════════════════════════════════════════════════════════════════════════

def pack_jobs_to_gpus(
    jobs: List[JobInfo],
    gpus: List[GPUState],
    target_fill: float,
    config: Dict[str, Any]
) -> List[Tuple[JobInfo, int]]:
    """
    First Fit Decreasing bin-packing for GPU job assignment.
    
    Returns list of (job, gpu_index) tuples for jobs that can launch now.
    """
    if not jobs or not gpus:
        return []
    
    # ═══════════════════════════════════════════════════════════════════════
    # 1. FILTER GPUS - Exclude disabled GPUs
    # ═══════════════════════════════════════════════════════════════════════
    active_gpus = [
        g for g in gpus
        if not is_gpu_disabled(g.index, config)
    ]
    active_gpu_ids = {g.index for g in active_gpus}
    global_config = config.get("global", {})
    msa_preferred = _normalize_gpu_id_list(global_config.get("msa_preferred_gpu_ids")) or []
    msa_preferred_active = [gpu_id for gpu_id in msa_preferred if gpu_id in active_gpu_ids]
    msa_avoid_heavy = bool(global_config.get("msa_avoid_heavy_gpus", False))
    non_heavy_active_ids = {
        g.index for g in active_gpus
        if not GPU_CAPABILITIES.get(g.index, {'supports_heavy': True}).get('supports_heavy', True)
    }
    
    if not active_gpus:
        logger.warning("[PACK] No active GPUs available (all disabled)")
        return []
    if msa_preferred and not msa_preferred_active:
        logger.warning(f"[PACK] MSA preferred GPUs {msa_preferred} are unavailable; using general GPU selection")
    
    # ═══════════════════════════════════════════════════════════════════════
    # 2. SORT JOBS - Priority first, then VRAM descending, then age
    # ═══════════════════════════════════════════════════════════════════════
    sorted_jobs = sorted(
        jobs,
        key=lambda j: (-j.priority, -j.vram_estimate_mb, j.created_at)
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # 3. BUILD GPU STATE - Track projected VRAM usage
    # ═══════════════════════════════════════════════════════════════════════
    projected = {g.index: g.memory_used_mb for g in active_gpus}
    capacity = {g.index: g.memory_total_mb for g in active_gpus}
    
    # ═══════════════════════════════════════════════════════════════════════
    # 4. THE PACKING LOOP
    # ═══════════════════════════════════════════════════════════════════════
    assignments = []
    
    for job in sorted_jobs:
        best_gpu = None
        best_score = -float('inf')

        if job.model_type in PROTENIX_MODELS:
            has_protenix_gpu = any(
                GPU_CAPABILITIES.get(g.index, {'supports_protenix': True}).get('supports_protenix', True)
                for g in active_gpus
            )
            if not has_protenix_gpu:
                logger.warning(f"[PACK] {job.name}: No Protenix-compatible GPU available")
                continue
        
        # Log if this job has a multi-GPU allowlist
        if job.pinned_gpus is not None and len(job.pinned_gpus) > 0:
            filtered_allowlist = [g for g in job.pinned_gpus if g in active_gpu_ids]
            if not filtered_allowlist:
                logger.warning(f"[PACK] {job.name}: GPU allowlist has no active GPUs; skipping")
                continue
            if filtered_allowlist != job.pinned_gpus:
                logger.info(f"[PACK] {job.name}: GPU allowlist filtered to active GPUs {filtered_allowlist}")
            job = JobInfo(
                id=job.id,
                name=job.name,
                model_type=job.model_type,
                vram_estimate_mb=job.vram_estimate_mb,
                sequence_length=job.sequence_length,
                priority=job.priority,
                pinned_gpu=job.pinned_gpu,
                created_at=job.created_at,
                batch_id=job.batch_id,
                pinned_gpus=filtered_allowlist,
            )
        
        # ═══════════════════════════════════════════════════════════════════
        # PRE-CHECK: Determine forced GPU assignment from pins/locks
        # Priority: batch_lock > job.pinned_gpu > workflow_pin
        # ═══════════════════════════════════════════════════════════════════
        forced_gpu = None
        
        # Check 0a: Batch lock (highest priority - exclusive GPU for batch)
        batch_lock_gpu = get_batch_lock_gpu(getattr(job, 'batch_id', None), config)
        if batch_lock_gpu is not None:
            if batch_lock_gpu in active_gpu_ids:
                forced_gpu = batch_lock_gpu
                logger.debug(f"[PACK] {job.name}: Batch lock forces GPU {forced_gpu}")
            else:
                logger.warning(f"[PACK] {job.name}: Batch lock targets inactive GPU {batch_lock_gpu}")
                continue
        
        # Check 0b: Job-level pinning (user explicitly chose GPU for this job)
        elif job.pinned_gpu is not None:
            if job.pinned_gpu in active_gpu_ids:
                forced_gpu = job.pinned_gpu
                logger.debug(f"[PACK] {job.name}: Job pin forces GPU {forced_gpu}")
            else:
                logger.warning(f"[PACK] {job.name}: Job pin targets inactive GPU {job.pinned_gpu}")
                continue
        
        # Check 0c: Workflow-level pin (all jobs of this model_type go to specific GPU)
        else:
            workflow_pin = get_workflow_pin(job.model_type, config)
            if workflow_pin is not None:
                if workflow_pin in active_gpu_ids:
                    forced_gpu = workflow_pin
                    logger.debug(f"[PACK] {job.name}: Workflow pin ({job.model_type}) forces GPU {forced_gpu}")
                else:
                    logger.warning(f"[PACK] {job.name}: Workflow pin targets inactive GPU {workflow_pin}")
                    continue
        
        for gpu in active_gpus:
            gpu_caps = GPU_CAPABILITIES.get(gpu.index, {'supports_heavy': True})
            
            # Check 1: Respect forced GPU assignment (pins/locks)
            if forced_gpu is not None:
                if forced_gpu != gpu.index:
                    continue
            
            # Check 1b: Multi-GPU allowlist (if specified, only use these GPUs)
            if job.pinned_gpus is not None and len(job.pinned_gpus) > 0:
                if gpu.index not in job.pinned_gpus:
                    continue  # This GPU is not in the allowlist

            # Check 1c: MSA preferred GPU allowlist from scheduler config (if active)
            if job.model_type == 'msa_batch' and msa_preferred_active:
                if gpu.index not in msa_preferred_active:
                    continue
            
            # Check 2: GPU lock exclusion (skip GPUs locked by other batches)
            job_batch_id = getattr(job, 'batch_id', None)
            if is_gpu_locked(gpu.index, job_batch_id, config):
                continue  # GPU is locked by a different batch
            
            # Check 3: Model compatibility (heavy models skip 5060 Ti)
            if job.model_type in HEAVY_MODELS:
                if not gpu_caps.get('supports_heavy', True):
                    continue

            # Check 3b: Protenix compatibility (skip unsupported GPUs, e.g. Blackwell until stack update)
            if job.model_type in PROTENIX_MODELS:
                if not gpu_caps.get('supports_protenix', True):
                    continue
            
            # Check 4: VRAM availability (with per-GPU safety margin)
            gpu_override = config.get("overrides", {}).get(str(gpu.index), {})
            safety_margin = gpu_override.get("vram_safety_margin_mb", 500)
            target_fill = config.get("global", {}).get("target_vram_fill", 0.85)
            available = (capacity[gpu.index] * target_fill) - projected[gpu.index] - safety_margin
            
            if job.vram_estimate_mb > available:
                continue  # Doesn't fit
            
            # ═══════════════════════════════════════════════════════════════
            # SCORING: Configurable weights for GPU preference
            # ═══════════════════════════════════════════════════════════════
            # Read weights from config
            capacity_weight = global_config.get("capacity_weight", 3.0)
            emptiness_weight = global_config.get("emptiness_weight", 5.0)
            
            # Check for per-GPU priority tier override
            priority_tier = gpu_override.get("priority_tier")
            
            current_utilization = projected[gpu.index] / capacity[gpu.index]
            
            # Calculate score
            if priority_tier is not None:
                # User-defined priority tier (higher = preferred)
                base_tier = priority_tier * 10
            else:
                # Auto-calculate from capacity
                # 5090 (32GB) → 9.6, 3090 (24GB) → 7.2, 5060 Ti (16GB) → 4.8
                base_tier = (capacity[gpu.index] / 10000) * capacity_weight
            
            emptiness_bonus = (1.0 - current_utilization) * emptiness_weight
            
            score = (
                base_tier
                + emptiness_bonus
                - gpu.index * 0.001  # Tie-breaker: prefer GPU 0
            )

            # Keep MSA lightweight by favoring non-heavy GPUs when requested.
            if job.model_type == 'msa_batch' and msa_avoid_heavy and non_heavy_active_ids:
                if gpu.index in non_heavy_active_ids:
                    score += 1000.0
                else:
                    score -= 1000.0
            
            if score > best_score:
                best_score = score
                best_gpu = gpu.index
        
        # ═══════════════════════════════════════════════════════════════════
        # ASSIGN IF FOUND
        # ═══════════════════════════════════════════════════════════════════
        if best_gpu is not None:
            assignments.append((job, best_gpu))
            projected[best_gpu] += job.vram_estimate_mb
            
            logger.info(
                f"[PACK] {job.name} → GPU {best_gpu} | "
                f"VRAM: {job.vram_estimate_mb}MB | "
                f"Projected: {projected[best_gpu]}/{capacity[best_gpu]}MB "
                f"({projected[best_gpu]/capacity[best_gpu]*100:.1f}%)"
            )
    
    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY LOGGING
    # ═══════════════════════════════════════════════════════════════════════
    if assignments:
        logger.info(f"[PACK COMPLETE] Assigned {len(assignments)} jobs this cycle")
        for gpu in active_gpus:
            util = projected[gpu.index] / capacity[gpu.index] * 100
            logger.info(f"  GPU {gpu.index}: {util:.1f}% projected")
    
    return assignments


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR LOOP
# ═══════════════════════════════════════════════════════════════════════════════

class GPUOrchestrator:
    """
    Background service that manages GPU job scheduling.
    
    Usage:
        orchestrator = GPUOrchestrator(db_session_factory, get_gpu_stats_fn, launch_job_fn)
        await orchestrator.start()
    """
    
    def __init__(
        self,
        db_session_factory,
        get_gpu_stats_fn,
        launch_nextflow_job_fn,
        poll_interval: float = 3.0
    ):
        self.db_session_factory = db_session_factory
        self.get_gpu_stats = get_gpu_stats_fn
        self.launch_nextflow_job = launch_nextflow_job_fn
        self.poll_interval = poll_interval
        self._running = False
        self._task = None
    
    async def start(self):
        """Start the orchestrator loop."""
        if self._running:
            logger.warning("Orchestrator already running")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[ORCHESTRATOR] Started")
    
    async def stop(self):
        """Stop the orchestrator loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[ORCHESTRATOR] Stopped")
    
    async def _run_loop(self):
        """Main orchestrator loop."""
        while self._running:
            try:
                await self._process_cycle()
                # Poll progress for running jobs (updates stage_progress in DB)
                await self.update_running_job_progress()
                # Check for completed/failed jobs and update their status
                await self.check_job_completions()
            except Exception as e:
                logger.error(f"[ORCHESTRATOR] Error in cycle: {e}", exc_info=True)
            
            await asyncio.sleep(self.poll_interval)
    
    async def _process_cycle(self):
        """Single orchestrator cycle: check queue, pack jobs, launch."""
        # 1. Read config
        config = read_scheduler_config()
        
        if not config.get("global", {}).get("enabled", True):
            return  # Orchestrator disabled
        
        target_fill = config.get("global", {}).get("target_vram_fill", 0.75)
        
        # 2. Get pending jobs from database
        async with self.db_session_factory() as session:
            from sqlalchemy import select, and_, or_, func
            from database import Job
            
            # ═══════════════════════════════════════════════════════════════════
            # CRITICAL: MSA job limiting - only ONE MSA batch at a time
            # Multiple MSA jobs in parallel can cause DRAM pressure.
            # Concurrency is configurable via scheduler global.msa_concurrency_limit.
            # ═══════════════════════════════════════════════════════════════════
            running_msa_count_result = await session.execute(
                select(func.count()).where(
                    Job.model_id == 'msa_batch',
                    Job.queue_status == 'running'
                )
            )
            running_msa_count = running_msa_count_result.scalar() or 0
            msa_concurrency_limit = config.get("global", {}).get("msa_concurrency_limit", 1)
            try:
                msa_concurrency_limit = max(1, int(msa_concurrency_limit))
            except (TypeError, ValueError):
                msa_concurrency_limit = 1
            
            # Build the base query for queued jobs
            base_conditions = [
                Job.queue_status == "queued",
                Job.paused == False,
                Job.vram_estimate_mb.isnot(None),
                # CRITICAL: Exclude jobs waiting for parent MSA job to complete
                # Jobs with parent_job_id are linked to an MSA batch job
                # They get their queue_status changed from pending_msa -> queued only after MSA completes
                or_(Job.parent_job_id.is_(None), Job.queue_status != "pending_msa")
            ]
            
            # If MSA concurrency limit is reached, exclude additional MSA jobs.
            if running_msa_count >= msa_concurrency_limit:
                base_conditions.append(Job.model_id != 'msa_batch')
                logger.debug(
                    "[ORCHESTRATOR] MSA concurrency reached "
                    f"({running_msa_count}/{msa_concurrency_limit}); blocking additional MSA jobs"
                )
            
            result = await session.execute(
                select(Job).where(
                    and_(*base_conditions)
                ).order_by(
                    Job.priority.desc(),
                    Job.created_at
                )
            )
            pending_jobs = result.scalars().all()
            
            if not pending_jobs:
                return  # Nothing to do
            
            # Concurrency limits applied later after GPU stats are available
            
            # Convert to JobInfo for packing
            job_infos = []
            for job in pending_jobs:
                # Estimate VRAM if not set
                vram = job.vram_estimate_mb
                if vram is None:
                    seq_len = job.sequence_length or 300
                    model = job.model_id or 'default'
                    vram = estimate_vram(model, seq_len)
                
                # Extract pinned_gpus from job params if present
                job_params = _normalize_job_params(job.params)
                pinned_gpus = _normalize_pinned_gpus(job_params.get('pinned_gpus'))
                
                job_infos.append(JobInfo(
                    id=job.id,
                    name=job.name,
                    model_type=job.model_id,
                    vram_estimate_mb=vram,
                    sequence_length=job.sequence_length or 300,
                    priority=job.priority or 0,
                    pinned_gpu=job.pinned_gpu if isinstance(job.pinned_gpu, int) else None,
                    created_at=job.created_at,
                    batch_id=getattr(job, 'batch_id', None),  # For GPU locking
                    pinned_gpus=pinned_gpus  # Multi-GPU allowlist
                ))
            
            # 3. Get GPU state
            gpu_stats = self.get_gpu_stats()
            if not gpu_stats:
                logger.warning("[ORCHESTRATOR] No GPU stats available; skipping scheduling cycle")
                return

            # ═══════════════════════════════════════════════════════════════════════
            # CONCURRENCY LIMITS: Filter jobs by per-model concurrent limits
            # ═══════════════════════════════════════════════════════════════════════
            concurrency_limits = config.get("concurrency_limits", {})
            if concurrency_limits:
                # Count running jobs per model type
                running_by_model = {}
                running_result = await session.execute(
                    select(Job.model_id, func.count(Job.id)).where(
                        Job.queue_status == "running"
                    ).group_by(Job.model_id)
                )
                for model_id, count in running_result:
                    running_by_model[model_id] = count

                # Precompute auto limits
                auto_limits = {}
                for model_id, limit in concurrency_limits.items():
                    if isinstance(limit, str) and limit.lower() == "auto":
                        auto_limit = _compute_auto_limit(model_id, job_infos, gpu_stats, config)
                        auto_limits[model_id] = auto_limit

                # Filter pending jobs based on limits
                pending_by_id = {job.id: job for job in pending_jobs}
                filtered_infos = []
                skipped_by_limit = {}
                for job_info in job_infos:
                    limit = concurrency_limits.get(job_info.model_type)
                    if isinstance(limit, str) and limit.lower() == "auto":
                        limit = auto_limits.get(job_info.model_type)
                    if isinstance(limit, str):
                        limit = None
                    if limit is not None:
                        running_count = running_by_model.get(job_info.model_type, 0)
                        if running_count >= limit:
                            skipped_by_limit[job_info.model_type] = skipped_by_limit.get(job_info.model_type, 0) + 1
                            continue
                        filtered_for_model = len([j for j in filtered_infos if j.model_type == job_info.model_type])
                        if running_count + filtered_for_model >= limit:
                            skipped_by_limit[job_info.model_type] = skipped_by_limit.get(job_info.model_type, 0) + 1
                            continue
                    filtered_infos.append(job_info)

                if skipped_by_limit:
                    logger.info(f"[CONCURRENCY] Skipped jobs due to limits: {skipped_by_limit}")

                job_infos = filtered_infos
                pending_jobs = [pending_by_id[j.id] for j in job_infos]

                if not job_infos:
                    return  # All jobs blocked by concurrency limits
            
            # ═══════════════════════════════════════════════════════════════════════
            # CRITICAL: Include recently-launched jobs in VRAM projection
            # Jobs take 10-30s to actually allocate GPU memory after launching.
            # Without this, the orchestrator keeps launching more every 3s cycle.
            # Query running jobs and add their estimated VRAM to GPU usage.
            # ═══════════════════════════════════════════════════════════════════════
            running_jobs_result = await session.execute(
                select(Job).where(
                    Job.queue_status == 'running',
                    Job.assigned_gpu.isnot(None),
                    Job.vram_estimate_mb.isnot(None)
                )
            )
            running_jobs = running_jobs_result.scalars().all()
            
            # Sum estimated VRAM per GPU from running jobs
            pending_vram = {}  # gpu_index -> estimated VRAM from running jobs
            for rj in running_jobs:
                gpu_idx = rj.assigned_gpu
                if gpu_idx is not None:
                    pending_vram[gpu_idx] = pending_vram.get(gpu_idx, 0) + (rj.vram_estimate_mb or 0)
            
            if pending_vram:
                logger.debug(f"[ORCHESTRATOR] Pending VRAM from running jobs: {pending_vram}")
            
            gpu_states = []
            for g in gpu_stats:
                # Add pending VRAM to memory_used for scheduling purposes
                effective_used = g.memory_used_mb + pending_vram.get(g.index, 0)
                gpu_states.append(GPUState(
                    index=g.index,
                    name=g.name,
                    memory_used_mb=effective_used,  # Include pending VRAM
                    memory_total_mb=g.memory_total_mb,
                    memory_free_mb=g.memory_total_mb - effective_used,
                    utilization=g.utilization,
                    temperature=g.temperature
                ))
            
            # 4. Run bin-packing
            assignments = pack_jobs_to_gpus(job_infos, gpu_states, target_fill, config)
            
            if not assignments:
                return  # No jobs could be packed
            
            # ═══════════════════════════════════════════════════════════════════════
            # CRITICAL: Limit launches per cycle to prevent RAM/VRAM overload
            # When many jobs queue at once (e.g., 40 FAMPNN children), launching them
            # all simultaneously causes system RAM exhaustion before GPU allocation.
            # Limit to 3 per cycle (~9 seconds), allowing VRAM to allocate before more.
            # ═══════════════════════════════════════════════════════════════════════
            max_launches_per_cycle = config.get("global", {}).get("max_launches_per_cycle", 3)
            assignments = assignments[:max_launches_per_cycle]
            
            if len(assignments) < len(job_infos):
                logger.info(f"[ORCHESTRATOR] Throttled to {len(assignments)}/{len(job_infos)} launches this cycle")
            
            # 5. Launch assigned jobs with stagger to prevent GPU initialization OOM
            for i, (job_info, gpu_id) in enumerate(assignments):
                # Find the actual Job object
                job = next((j for j in pending_jobs if j.id == job_info.id), None)
                if not job:
                    continue
                
                # Stagger launches to prevent GPU memory racing (0.5 second delay between)
                # Reduced from 2.0s for faster throughput
                if i > 0:
                    await asyncio.sleep(0.5)
                
                try:
                    # Launch Nextflow with GPU assignment
                    await self.launch_nextflow_job(
                        job_id=job.id,
                        model_id=job.model_id,
                        mode=job.mode,
                        params={**job.params, 'gpu_id': gpu_id},
                        output_dir=job.output_dir
                    )
                    
                    # Update job status
                    job.queue_status = "running"
                    job.assigned_gpu = gpu_id
                    job.started_at = datetime.utcnow()
                    job.vram_estimate_mb = job_info.vram_estimate_mb
                    
                    logger.info(f"[LAUNCH] {job.name} on GPU {gpu_id}")
                    
                except Exception as e:
                    logger.error(f"[LAUNCH FAILED] {job.name}: {e}")
                    job.queue_status = "failed"
                    job.error_message = str(e)
            
            await session.commit()
    
    async def check_job_completions(self):
        """
        Check running jobs for completion or failure.
        
        Uses multiple detection strategies:
        1. PID check (if tracked)
        2. Job name in running processes
        3. Model-type processes on assigned GPU
        """
        import subprocess
        
        try:
            async with self.db_session_factory() as session:
                from sqlalchemy import select, func
                from database import Job, Design
                
                # Get jobs that are currently running
                result = await session.execute(
                    select(Job).where(
                        Job.queue_status == "running"
                    )
                )
                running_jobs = result.scalars().all()
                
                if not running_jobs:
                    return

                # Cross-check against launcher-tracked Nextflow processes.
                # This prevents stale "completed" reconciliation when ps parsing
                # misses a still-active process.
                active_launch_jobs = set()
                try:
                    from services.nextflow import get_running_jobs
                    active_launch_jobs = set(get_running_jobs().keys())
                except Exception as proc_err:
                    logger.debug(f"[COMPLETION] Could not query launcher running jobs: {proc_err}")
                
                # Get all running processes once (expensive operation)
                try:
                    ps_result = subprocess.run(
                        ['ps', 'aux'],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    all_processes = ps_result.stdout if ps_result.returncode == 0 else ""
                except Exception as e:
                    logger.debug(f"[COMPLETION] Failed to get process list: {e}")
                    all_processes = ""
                
                # Count FAMPNN/Nextflow processes per GPU
                gpu_has_activity = {}
                for line in all_processes.split('\n'):
                    if 'seq_design.py' in line or 'nextflow' in line:
                        # Try to extract gpu_id from the process args
                        import re
                        gpu_match = re.search(r'gpu_id[=\s]+(\d+)', line)
                        if gpu_match:
                            gpu_id = int(gpu_match.group(1))
                            gpu_has_activity[gpu_id] = gpu_has_activity.get(gpu_id, 0) + 1
                
                reconciled = 0
                for job in running_jobs:
                    job_is_running = False

                    # Method 0: Trust launcher's active process registry.
                    if job.id in active_launch_jobs:
                        job_is_running = True
                    
                    # Method 1: Check if job ID appears in any process (Nextflow uses --job_id UUID)
                    # Also check job name as fallback for non-Nextflow processes
                    if job.id in all_processes:
                        job_is_running = True
                    elif job.name in all_processes or job.name.replace(' ', '.') in all_processes:
                        job_is_running = True
                    
                    # Method 2: Check if there's any activity on the job's assigned GPU
                    if not job_is_running and job.assigned_gpu is not None:
                        if job.assigned_gpu in gpu_has_activity:
                            # GPU has activity - job might be running
                            # But we need to be more specific for FAMPNN
                            if 'fampnn' in job.name.lower() and 'seq_design' not in all_processes:
                                # No FAMPNN processes at all - job is done
                                job_is_running = False
                            else:
                                job_is_running = True
                    
                    # Method 3: If job has been running > 1 minute and no process found, reconcile state
                    if not job_is_running and job.started_at:
                        age_seconds = (datetime.utcnow() - job.started_at).total_seconds()
                        if age_seconds > 60:  # Only mark complete if running > 1 min
                            failure_reason = None
                            if job.error_message:
                                failure_reason = str(job.error_message)
                            elif job.output_dir:
                                try:
                                    out_dir = Path(job.output_dir)
                                    nf_log = out_dir / "nextflow.log"
                                    if not nf_log.exists():
                                        nf_log = out_dir / ".nextflow.log"
                                    if nf_log.exists():
                                        tail = nf_log.read_text(errors="ignore")[-8000:]
                                        error_markers = (
                                            "ERROR ~ Error executing process",
                                            "terminated with an error exit status",
                                            "Nextflow exited with code",
                                        )
                                        for marker in error_markers:
                                            if marker in tail:
                                                failure_reason = f"Reconciled from nextflow.log: {marker}"
                                                break
                                except Exception as log_err:
                                    logger.debug(f"[COMPLETION] Could not inspect nextflow log for {job.name}: {log_err}")

                            if failure_reason:
                                if job.status == "running":
                                    job.status = "failed"
                                job.queue_status = "failed"
                                job.completed_at = datetime.utcnow()
                                logger.warning(
                                    f"[COMPLETION] {job.name} reconciled as failed "
                                    f"(no process found, age: {age_seconds:.0f}s): {failure_reason}"
                                )
                            else:
                                job.queue_status = "completed"
                                if job.status == "running":
                                    job.status = "completed"
                                job.current_stage = "Complete"
                                job.stage_progress = None
                                job.completed_at = datetime.utcnow()

                                # Best-effort safety net: if a top-level workflow completed but
                                # launch task finalization was missed, ingest outputs so Data Viewer
                                # is populated instead of showing an empty completed job.
                                if job.parent_job_id is None and job.output_dir:
                                    try:
                                        existing_designs = (
                                            await session.execute(
                                                select(func.count(Design.id)).where(Design.job_id == job.id)
                                            )
                                        ).scalar() or 0
                                        if existing_designs == 0:
                                            from services.result_ingester import ingest_job_results

                                            created = await ingest_job_results(str(job.id), job.output_dir, session)
                                            logger.info(
                                                f"[COMPLETION] Ingested {created} designs "
                                                f"for reconciled top-level job {job.name}"
                                            )
                                    except Exception as ingest_err:
                                        logger.warning(
                                            f"[COMPLETION] Reconcile ingestion failed for {job.name}: {ingest_err}"
                                        )
                                logger.info(f"[COMPLETION] {job.name} completed (no process found, age: {age_seconds:.0f}s)")

                            reconciled += 1
                
                if reconciled > 0:
                    await session.commit()
                    logger.info(f"[COMPLETION] Reconciled {reconciled} stale running jobs")
                
        except Exception as e:
            logger.error(f"[COMPLETION] Error checking completions: {e}", exc_info=True)
    
    async def handle_oom_failures(self):
        """Handle OOM failures based on job's oom_tolerance setting."""
        # TODO: Implement by parsing Nextflow logs for OOM errors
        pass
    
    async def update_running_job_progress(self):
        """
        Poll stage progress for all running jobs.
        
        Reads work directory logs to extract granular progress (e.g., "5/30 designs").
        This runs every orchestrator cycle (~3 seconds) to provide live progress updates.
        """
        from services.nextflow import parse_stage_progress
        
        try:
            async with self.db_session_factory() as session:
                from sqlalchemy import select
                from database import Job
                
                # Get jobs that are currently running and have a work directory
                result = await session.execute(
                    select(Job).where(
                        Job.queue_status == "running",
                        Job.stage_work_dir.isnot(None)
                    )
                )
                running_jobs = result.scalars().all()
                
                for job in running_jobs:
                    if not job.current_stage or not job.stage_work_dir:
                        continue
                    
                    # Parse progress from work directory log
                    total_designs = None
                    job_params = _normalize_job_params(job.params)
                    if job_params:
                        total_designs = job_params.get('rfantibody_num_designs') or job_params.get('num_designs')
                    
                    progress = parse_stage_progress(
                        job.stage_work_dir,
                        job.current_stage,
                        total_designs
                    )
                    
                    if progress and progress != job.stage_progress:
                        job.stage_progress = progress
                        logger.debug(f"[ORCHESTRATOR] Job {job.name} progress: {progress}")
                
                await session.commit()
                
        except Exception as e:
            logger.debug(f"[ORCHESTRATOR] Error updating progress: {e}")
    
# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE TESTING
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Quick test of packing algorithm
    logging.basicConfig(level=logging.INFO)
    
    # Mock data
    test_gpus = [
        GPUState(0, "RTX 5090", 5000, 32607, 27607, 10, 45),
        GPUState(1, "RTX 5060 Ti", 2000, 16311, 14311, 5, 40),
        GPUState(2, "RTX 3090", 8000, 24576, 16576, 15, 50),
        GPUState(3, "RTX 3090", 3000, 24576, 21576, 8, 48),
    ]
    
    test_jobs = [
        JobInfo("1", "boltz_job_1", "boltz", 12000, 500, 0, None, datetime.now()),
        JobInfo("2", "rf3_job_1", "rf3", 14000, 600, 0, None, datetime.now()),
        JobInfo("3", "mpnn_job_1", "mpnn", 2000, 200, 0, None, datetime.now()),
        JobInfo("4", "mpnn_job_2", "mpnn", 2000, 200, 0, None, datetime.now()),
        JobInfo("5", "mpnn_job_3", "mpnn", 2000, 200, 0, None, datetime.now()),
        JobInfo("6", "fampnn_job_1", "fampnn", 4000, 300, 0, None, datetime.now()),
    ]
    
    config = {"global": {"target_vram_fill": 0.80, "enabled": True}, "overrides": {}}
    
    print("\n=== BIN PACKING TEST ===\n")
    assignments = pack_jobs_to_gpus(test_jobs, test_gpus, 0.80, config)
    
    print(f"\nTotal assigned: {len(assignments)} / {len(test_jobs)} jobs")
