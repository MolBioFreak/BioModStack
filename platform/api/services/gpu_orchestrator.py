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


# ═══════════════════════════════════════════════════════════════════════════════
# VRAM ESTIMATION PROFILES
# ═══════════════════════════════════════════════════════════════════════════════

# Base VRAM + scaling factor per model
# Formula: base + scale * (sequence_length / 100)^2
VRAM_PROFILES = {
    'boltz': {'base': 7000, 'scale': 40},      # Boltz-2 structure prediction
    'boltzgen': {'base': 5000, 'scale': 25},   # BoltzGen design
    'rf3': {'base': 8000, 'scale': 50},        # RosettaFold3
    'af2': {'base': 9000, 'scale': 60},        # AlphaFold2
    'rfdiffusion': {'base': 4000, 'scale': 20},# RFdiffusion
    'fampnn': {'base': 2000, 'scale': 8},      # Full-Atom MPNN
    'mpnn': {'base': 1000, 'scale': 3},        # ProteinMPNN (very light)
    'diffdock': {'base': 3000, 'scale': 10},   # DiffDock
    'default': {'base': 6000, 'scale': 30},    # Fallback
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
            "target_vram_fill": 0.75,
            "enabled": True,
        },
        "overrides": {}
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
        
        for gpu in active_gpus:
            gpu_caps = GPU_CAPABILITIES.get(gpu.index, {'supports_heavy': True})
            
            # Check 1: Respect user pinning
            if job.pinned_gpu is not None:
                if job.pinned_gpu != gpu.index:
                    continue
            
            # Check 2: Model compatibility (heavy models skip 5060 Ti)
            if job.model_type in HEAVY_MODELS:
                if not gpu_caps.get('supports_heavy', True):
                    continue
            
            # Check 3: VRAM availability
            available = (capacity[gpu.index] * target_fill) - projected[gpu.index]
            
            if job.vram_estimate_mb > available:
                continue  # Doesn't fit
            
            # ═══════════════════════════════════════════════════════════════
            # SCORING: Prefer larger/faster GPUs first, then consider packing
            # ═══════════════════════════════════════════════════════════════
            # Primary: Prefer GPUs with more total VRAM (faster/better)
            # Secondary: Prefer emptier GPUs (lower current utilization)
            # Tertiary: Prefer lower-index GPUs (determinism)
            
            current_utilization = projected[gpu.index] / capacity[gpu.index]
            
            score = (
                capacity[gpu.index] / 10000  # Prefer larger GPUs (5090 > 3090 > 5060 Ti)
                + (1.0 - current_utilization) * 5  # Prefer emptier GPUs
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
            from sqlalchemy import select
            from database import Job
            
            result = await session.execute(
                select(Job).where(
                    Job.queue_status == "queued",
                    Job.paused == False,
                    # CRITICAL: Only pick up jobs that went through the new orchestrator
                    # (have vram_estimate_mb set during job creation)
                    Job.vram_estimate_mb.isnot(None)
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
                    created_at=job.created_at
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
                
                # Stagger launches to prevent GPU memory racing (2 second delay between)
                if i > 0:
                    await asyncio.sleep(2.0)
                
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
