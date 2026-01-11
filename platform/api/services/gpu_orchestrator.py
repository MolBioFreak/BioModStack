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
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass

# Will be imported when integrated with FastAPI
# from database import Job, async_session
# from routers.gpu import get_gpu_stats
# from services.nextflow import launch_nextflow_job

logger = logging.getLogger(__name__)

# Project root for config files
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


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


# ═══════════════════════════════════════════════════════════════════════════════
# VRAM ESTIMATION PROFILES
# ═══════════════════════════════════════════════════════════════════════════════

# Base VRAM + scaling factor per model
# Formula: base + scale * (sequence_length / 100)^2
# NOTE: These are CONSERVATIVE estimates tuned to prevent OOMs.
# Better to underutilize GPU than crash jobs.
VRAM_PROFILES = {
    'boltz': {'base': 8000, 'scale': 45},       # Boltz-2 structure prediction
    'boltz_batch': {'base': 10000, 'scale': 50},# Boltz-2 batch mode uses more
    'boltzgen': {'base': 6000, 'scale': 30},    # BoltzGen design
    'rf3': {'base': 10000, 'scale': 55},        # RosettaFold3 (high VRAM)
    'af2': {'base': 12000, 'scale': 65},        # AlphaFold2 (highest VRAM)
    'rfdiffusion': {'base': 5000, 'scale': 25}, # RFdiffusion
    'rfantibody': {'base': 8000, 'scale': 40},  # RFantibody antibody generation
    'fampnn': {'base': 6000, 'scale': 20},      # Full-Atom MPNN (increased from 2000 due to OOMs)
    'mpnn': {'base': 2000, 'scale': 5},         # ProteinMPNN (light)
    'proteinmpnn': {'base': 2000, 'scale': 5},  # Alias
    'diffdock': {'base': 4000, 'scale': 12},    # DiffDock
    'unidock': {'base': 3000, 'scale': 8},      # Uni-Dock
    'msa_batch': {'base': 3000, 'scale': 2},    # MSA Generation (GPU streaming, LOW VRAM)
    'antibody_child': {'base': 10000, 'scale': 50},  # Antibody validation (Boltz + scoring)
    'antibody_denovo': {'base': 8000, 'scale': 40},  # Full antibody pipeline
    'default': {'base': 8000, 'scale': 35},     # Conservative fallback
}

# GPU capabilities
GPU_CAPABILITIES = {
    0: {'name': 'RTX 5090', 'vram_mb': 32607, 'supports_heavy': True},
    1: {'name': 'RTX 5060 Ti', 'vram_mb': 16311, 'supports_heavy': False},
    2: {'name': 'RTX 3090', 'vram_mb': 24576, 'supports_heavy': True},
    3: {'name': 'RTX 3090', 'vram_mb': 24576, 'supports_heavy': True},
}

# Models that need heavy GPUs (exclude 5060 Ti)
HEAVY_MODELS = {'af2', 'rfdiffusion', 'rf3'}


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


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULER CONFIG (from .gpu_config.json)
# ═══════════════════════════════════════════════════════════════════════════════

def read_scheduler_config() -> Dict[str, Any]:
    """Read scheduler config from .gpu_config.json."""
    config_path = PROJECT_ROOT / ".gpu_config.json"
    
    default_config = {
        "global": {
            "target_vram_fill": 0.85,  # More aggressive packing (was 0.75)
            "enabled": True,
        },
        "overrides": {},
        "workflow_pins": {},  # Map of model_type -> gpu_id
        "gpu_locks": {}  # Map of batch_id -> gpu_id for exclusive locks
    }
    
    if not config_path.exists():
        return default_config
    
    try:
        with open(config_path) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read scheduler config: {e}")
        return default_config


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
    
    if not active_gpus:
        logger.warning("[PACK] No active GPUs available (all disabled)")
        return []
    
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
        
        # ═══════════════════════════════════════════════════════════════════
        # PRE-CHECK: Determine forced GPU assignment from pins/locks
        # Priority: batch_lock > job.pinned_gpu > workflow_pin
        # ═══════════════════════════════════════════════════════════════════
        forced_gpu = None
        
        # Check 0a: Batch lock (highest priority - exclusive GPU for batch)
        batch_lock_gpu = get_batch_lock_gpu(getattr(job, 'batch_id', None), config)
        if batch_lock_gpu is not None:
            forced_gpu = batch_lock_gpu
            logger.debug(f"[PACK] {job.name}: Batch lock forces GPU {forced_gpu}")
        
        # Check 0b: Job-level pinning (user explicitly chose GPU for this job)
        elif job.pinned_gpu is not None:
            forced_gpu = job.pinned_gpu
            logger.debug(f"[PACK] {job.name}: Job pin forces GPU {forced_gpu}")
        
        # Check 0c: Workflow-level pin (all jobs of this model_type go to specific GPU)
        else:
            workflow_pin = get_workflow_pin(job.model_type, config)
            if workflow_pin is not None:
                forced_gpu = workflow_pin
                logger.debug(f"[PACK] {job.name}: Workflow pin ({job.model_type}) forces GPU {forced_gpu}")
        
        for gpu in active_gpus:
            gpu_caps = GPU_CAPABILITIES.get(gpu.index, {'supports_heavy': True})
            
            # Check 1: Respect forced GPU assignment (pins/locks)
            if forced_gpu is not None:
                if forced_gpu != gpu.index:
                    continue
            
            # Check 2: GPU lock exclusion (skip GPUs locked by other batches)
            job_batch_id = getattr(job, 'batch_id', None)
            if is_gpu_locked(gpu.index, job_batch_id, config):
                continue  # GPU is locked by a different batch
            
            # Check 3: Model compatibility (heavy models skip 5060 Ti)
            if job.model_type in HEAVY_MODELS:
                if not gpu_caps.get('supports_heavy', True):
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
            global_config = config.get("global", {})
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
            # Multiple MSA jobs in parallel cause DRAM OOM (~16GB each)
            # ═══════════════════════════════════════════════════════════════════
            running_msa_count_result = await session.execute(
                select(func.count()).where(
                    Job.model_id == 'msa_batch',
                    Job.queue_status == 'running'
                )
            )
            running_msa_count = running_msa_count_result.scalar() or 0
            
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
            
            # If MSA job is already running, exclude other MSA jobs from scheduling
            if running_msa_count > 0:
                base_conditions.append(Job.model_id != 'msa_batch')
                logger.debug("[ORCHESTRATOR] MSA job running, blocking additional MSA jobs")
            
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
            
            # Convert to JobInfo for packing
            job_infos = []
            for job in pending_jobs:
                # Estimate VRAM if not set
                vram = job.vram_estimate_mb
                if vram is None:
                    seq_len = job.sequence_length or 300
                    model = job.model_id or 'default'
                    vram = estimate_vram(model, seq_len)
                
                job_infos.append(JobInfo(
                    id=job.id,
                    name=job.name,
                    model_type=job.model_id,
                    vram_estimate_mb=vram,
                    sequence_length=job.sequence_length or 300,
                    priority=job.priority or 0,
                    pinned_gpu=job.pinned_gpu,
                    created_at=job.created_at,
                    batch_id=getattr(job, 'batch_id', None)  # For GPU locking
                ))
            
            # 3. Get GPU state
            gpu_stats = self.get_gpu_stats()
            gpu_states = [
                GPUState(
                    index=g.index,
                    name=g.name,
                    memory_used_mb=g.memory_used_mb,
                    memory_total_mb=g.memory_total_mb,
                    memory_free_mb=g.memory_total_mb - g.memory_used_mb,
                    utilization=g.utilization,
                    temperature=g.temperature
                )
                for g in gpu_stats
            ]
            
            # 4. Run bin-packing
            assignments = pack_jobs_to_gpus(job_infos, gpu_states, target_fill, config)
            
            if not assignments:
                return  # No jobs could be packed
            
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
        """Check running jobs for completion or failure."""
        # TODO: Implement by checking Nextflow process status
        pass
    
    async def handle_oom_failures(self):
        """Handle OOM failures based on job's oom_tolerance setting."""
        # TODO: Implement by parsing Nextflow logs for OOM errors
        pass
    
    async def handle_msa_job_completion(self, msa_job_id: str, manifest_path: str):
        """
        Handle MSA batch job completion.
        
        Updates child inference jobs from 'pending_msa' to 'queued' status,
        allowing them to enter the normal job queue.
        """
        async with self.db_session_factory() as session:
            from sqlalchemy import select
            from database import Job
            import json
            
            # Get child jobs waiting for this MSA job
            result = await session.execute(
                select(Job).where(
                    Job.parent_job_id == msa_job_id,
                    Job.queue_status == "pending_msa"
                )
            )
            child_jobs = result.scalars().all()
            
            if not child_jobs:
                logger.info(f"[MSA COMPLETE] No child jobs for MSA job {msa_job_id}")
                return
            
            # Parse manifest for MSA paths
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
                
                # Build sequence hash -> MSA path mapping
                msa_paths = {}
                for seq_info in manifest.get("sequences", []):
                    if seq_info.get("success"):
                        msa_paths[seq_info["sequence_hash"]] = seq_info["msa_path"]
            except Exception as e:
                logger.error(f"[MSA COMPLETE] Failed to parse manifest: {e}")
                # Still unlock jobs even without MSA paths
                msa_paths = {}
            
            # Update each child job with its MSA path and queue status
            import hashlib
            for job in child_jobs:
                # Find MSA path for this job's sequence
                sequence = job.params.get("sequence", "")
                seq_hash = hashlib.sha256(sequence.encode()).hexdigest()
                msa_path = msa_paths.get(seq_hash)
                
                # Update job params with MSA path
                if msa_path:
                    job.params = {**job.params, "msa_path": msa_path}
                
                # Move to queued - now ready for inference!
                job.queue_status = "queued"
                
                logger.info(f"[MSA COMPLETE] Unlocked {job.name} for inference (MSA: {msa_path or 'not found'})")
            
            await session.commit()
            logger.info(f"[MSA COMPLETE] Unlocked {len(child_jobs)} inference jobs")


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
