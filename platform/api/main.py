"""
BioModStack Control Platform - FastAPI Backend

Main application entry point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
import asyncio
import logging

from database import init_db, async_session
from routers import jobs, gpu, files, models, templates, inputs, designs, analytics, user_sequences, user_templates, smiles_converter, queue, rcsb, nucleotide_sequences, system, frameworks, molbio_ops, msa
from services.gpu_orchestrator import GPUOrchestrator
from routers.gpu import get_gpu_stats

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global orchestrator instance
_orchestrator: GPUOrchestrator = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and GPU orchestrator on startup."""
    global _orchestrator
    
    # Initialize database
    await init_db()
    
    # Initialize GPU orchestrator
    from services.nextflow import launch_nextflow_job
    
    # Wrapper to call the real Nextflow launcher with GPU assignment
    async def orchestrator_launch_job(job_id, model_id, mode, params, output_dir):
        """Launch a job via Nextflow with GPU assignment from orchestrator.
        
        Uses asyncio.create_task to fire-and-forget, so multiple jobs can launch
        in parallel without waiting for each to complete.
        """
        logger.info(f"[ORCHESTRATOR] Launching job {job_id} on GPU {params.get('gpu_id', 0)}")
        # Fire-and-forget: create a background task for the Nextflow job
        # This allows the orchestrator to launch multiple jobs in parallel
        asyncio.create_task(launch_nextflow_job(
            job_id=job_id,
            model_id=model_id,
            mode=mode,
            params=params,
            output_dir=output_dir
        ))
    
    _orchestrator = GPUOrchestrator(
        db_session_factory=async_session,
        get_gpu_stats_fn=get_gpu_stats,
        launch_nextflow_job_fn=orchestrator_launch_job,
        poll_interval=3.0
    )
    
    # Start orchestrator in background
    await _orchestrator.start()
    logger.info("[STARTUP] GPU Orchestrator started")
    
    yield
    
    # Cleanup on shutdown
    if _orchestrator:
        await _orchestrator.stop()
        logger.info("[SHUTDOWN] GPU Orchestrator stopped")


app = FastAPI(
    title="BioModStack Control Platform",
    description="Extensible platform for protein modification and design",
    version="0.2.0",
    lifespan=lifespan
)

# CORS for frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(models.router, prefix="/api/models", tags=["models"])
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])
app.include_router(inputs.router, prefix="/api/inputs", tags=["inputs"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(designs.router, prefix="/api/designs", tags=["designs"])
app.include_router(gpu.router, prefix="/api/gpu", tags=["gpu"])
app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(user_sequences.router, prefix="/api/user-sequences", tags=["user-sequences"])
app.include_router(user_templates.router, prefix="/api/user-templates", tags=["user-templates"])
# msa_cache router removed - now using file-based caching
app.include_router(smiles_converter.router, prefix="/api/smiles", tags=["smiles"])
app.include_router(queue.router, prefix="/api", tags=["queue"])  # /api/queue/*
app.include_router(rcsb.router, prefix="/api/rcsb", tags=["rcsb"])
app.include_router(nucleotide_sequences.router)  # /api/sequences/*
app.include_router(system.router, prefix="/api", tags=["system"])  # /api/system/*
app.include_router(frameworks.router)  # /api/frameworks/* - SAbDab integration
app.include_router(molbio_ops.router)
app.include_router(msa.router)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "biomodstack-api"}


@app.get("/")
async def root():
    """Root redirect to API docs."""
    return {"message": "BioModStack API", "docs": "/docs"}
