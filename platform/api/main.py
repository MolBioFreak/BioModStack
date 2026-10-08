"""
BioModStack Control Platform - FastAPI Backend

Main application entry point.
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import logging

from database import init_db, async_session
from molbio_database import init_molbio_db, molbio_health
from build_identity import current_build_identity
from readiness import collect_runtime_readiness
from routers import analyses, analytics, boltz_api_jobs, boltzgen, conformational_mapping, designs, external_imports, files, frameworks, frustrampnn, gpu, inputs, jobs, md_results, mobile_apk_updates, mobile_ui_updates, models, molecular_dynamics, molbio_ops, msa, ngs_alignment_sessions, nucleotide_sequences, ont_devices, ont_runs, queue, rcsb, ribocentre, rna_structure, sequence_qc, smiles_converter, system, templates, user_sequences, user_templates
from runtime_policy import workflow_launch_block_detail, workflow_launches_allowed
from biomodstack_runtime_profile import install_feature_enabled
from services.analysis_worker import AnalysisWorker
from services.boltz_api_jobs import BoltzApiJobWorker
from services.external_imports.worker import ExternalImportWorker
from services.gpu_orchestrator import GPUOrchestrator
from routers.gpu import get_gpu_stats

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global orchestrator instance
_orchestrator: GPUOrchestrator = None
_analysis_worker: AnalysisWorker = None
_external_import_worker: ExternalImportWorker | None = None
_boltz_api_job_worker: BoltzApiJobWorker | None = None


async def _orchestrator_launch_job(job_id, model_id, mode, params, output_dir):
    """Delegate a scheduler-owned job whose durable state is already running."""
    from services.nextflow import launch_nextflow_job_detached

    logger.info(f"[ORCHESTRATOR] Launching job {job_id} on GPU {params.get('gpu_id', 0)}")
    launch_nextflow_job_detached(
        job_id=job_id,
        model_id=model_id,
        mode=mode,
        params=params,
        output_dir=output_dir,
        allow_running_job=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and GPU orchestrator on startup."""
    global _orchestrator
    global _analysis_worker
    global _external_import_worker
    global _boltz_api_job_worker
    bioxp_runtime = None
    
    # Initialize independently owned core and MolBio persistence stores.
    await init_db()
    await init_molbio_db()
    
    # Initialize GPU orchestrator only when this runtime is allowed to own workflow launches.
    if workflow_launches_allowed():
        _orchestrator = GPUOrchestrator(
            db_session_factory=async_session,
            get_gpu_stats_fn=get_gpu_stats,
            launch_nextflow_job_fn=_orchestrator_launch_job,
            poll_interval=3.0
        )

        # Start orchestrator in background
        await _orchestrator.start()
        logger.info("[STARTUP] GPU Orchestrator started")
    else:
        _orchestrator = None
        logger.warning("[STARTUP] %s", workflow_launch_block_detail("start the GPU workflow scheduler"))

    _analysis_worker = AnalysisWorker(
        db_session_factory=async_session,
        poll_interval=2.0,
    )
    
    await _analysis_worker.start()
    logger.info("[STARTUP] Analysis worker started")

    _external_import_worker = ExternalImportWorker(async_session, poll_interval=2.0)
    await _external_import_worker.start()
    logger.info("[STARTUP] External result import worker started")

    _boltz_api_job_worker = BoltzApiJobWorker(async_session)
    await _boltz_api_job_worker.start()
    logger.info("[STARTUP] Boltz API submission worker started")

    if install_feature_enabled("bioxp"):
        from services.bioxp.runtime import create_bioxp_runtime

        bioxp_runtime = create_bioxp_runtime()
        app.state.bioxp_runtime = bioxp_runtime
        logger.info("[STARTUP] BioXP control plane initialized disconnected")
    
    yield
    
    # Cleanup on shutdown
    if bioxp_runtime is not None:
        await bioxp_runtime.close()
        logger.info("[SHUTDOWN] BioXP control plane closed")
    if _orchestrator:
        await _orchestrator.stop()
        logger.info("[SHUTDOWN] GPU Orchestrator stopped")
    if _analysis_worker:
        await _analysis_worker.stop()
        logger.info("[SHUTDOWN] Analysis worker stopped")
    if _boltz_api_job_worker:
        await _boltz_api_job_worker.stop()
        logger.info("[SHUTDOWN] Boltz API submission worker stopped")
    if _external_import_worker:
        await _external_import_worker.stop()
        logger.info("[SHUTDOWN] External result import worker stopped")


app = FastAPI(
    title="BioModStack Control Platform",
    description="Extensible platform for protein modification and design",
    version="0.2.0",
    lifespan=lifespan
)

# Allow Private Network Access (PNA) preflights from secure origins
@app.middleware("http")
async def add_private_network_access_header(request: Request, call_next):
    response = await call_next(request)
    if request.headers.get("access-control-request-private-network") == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response

# CORS for localhost dev + local Cordova/web shells.
# Add remote wrapper origins explicitly via CORS_ORIGINS when needed.
default_origins = [
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1",
    "https://127.0.0.1",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://localhost:5173",
    "https://localhost:3000",
    "https://localhost:5173",
]
env_origins = os.getenv("CORS_ORIGINS")
allowed_origins = [o.strip() for o in env_origins.split(",")] if env_origins else default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(models.router, prefix="/api/models", tags=["models"])
app.include_router(molecular_dynamics.router)
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])
app.include_router(inputs.router, prefix="/api/inputs", tags=["inputs"])
app.include_router(boltz_api_jobs.router, prefix="/api/jobs/boltz-api", tags=["boltz-api-jobs"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(external_imports.router, prefix="/api/jobs/imports/external", tags=["external-result-imports"])
app.include_router(md_results.router, prefix="/api/jobs", tags=["molecular-dynamics-results"])
app.include_router(conformational_mapping.router)
app.include_router(designs.router, prefix="/api/designs", tags=["designs"])
app.include_router(analyses.router, prefix="/api", tags=["analyses"])
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
app.include_router(boltzgen.router)
app.include_router(molbio_ops.router)
app.include_router(rna_structure.router)
app.include_router(msa.router)
app.include_router(ribocentre.router, prefix="/api/ribocentre", tags=["ribocentre"])
app.include_router(frustrampnn.router)  # /api/frustrampnn/* - Energetic frustration analysis
if install_feature_enabled("bioxp"):
    from routers import bioxp

    app.include_router(bioxp.router, prefix="/api/bioxp", tags=["bioxp"])
app.include_router(sequence_qc.router, prefix="/api/sequence-qc", tags=["sequence-qc"])
app.include_router(ngs_alignment_sessions.router, prefix="/api", tags=["ngs-alignment"])
app.include_router(ont_devices.router, prefix="/api/ont", tags=["ont-devices"])
app.include_router(ont_runs.router, prefix="/api/ont", tags=["ont-runs"])
app.include_router(mobile_apk_updates.router, prefix="/api")
app.include_router(mobile_ui_updates.router, prefix="/api")

@app.get("/api/health")
async def health_check():
    """Separate process liveness from dependency and workflow readiness."""
    molbio = await molbio_health()
    readiness = await collect_runtime_readiness(molbio=molbio)
    return {
        "status": "healthy" if readiness["ready"] else "degraded",
        "service": "biomodstack-api",
        "liveness": {"alive": True, "status": "alive"},
        "readiness": readiness,
        "build": current_build_identity(),
        "molbio": molbio,
    }


@app.get("/api/version")
async def api_version():
    """Return immutable build identity for cross-surface provenance checks."""
    return {"service": "biomodstack-api", "build": current_build_identity()}


@app.get("/")
async def root():
    """Root redirect to API docs."""
    return {"message": "BioModStack API", "docs": "/docs"}
