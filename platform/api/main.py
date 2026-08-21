"""
BioModStack Control Platform - FastAPI Backend

Main application entry point.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import asyncio
import os
import logging
import re

from database import async_session, current_launch_context_id, init_db
from experiment_database import experiment_session_factory, get_experiment_db_path, init_experiment_db
from molbio_database import init_molbio_db, molbio_health
from molbio_ngs_database import init_molbio_ngs_db, molbio_ngs_health
from molbio_ngs_database import molbio_ngs_session_factory
from build_identity import current_build_identity
from readiness import collect_runtime_readiness, http_readiness
from frustrampnn_upload_limit import FrustraMPNNUploadLimitMiddleware
from routers import analyses, analytics, boltz_api_jobs, boltzgen, conformational_mapping, designs, dev_issues, external_imports, experiment_workspaces, files, frameworks, frustrampnn, gpu, inputs, jobs, md_results, mobile_apk_updates, mobile_ui_updates, models, molecular_dynamics, molbio_ngs_experiments, molbio_ops, msa, ngs_alignment_sessions, ngs_molbio_n5, nucleotide_sequences, ont_devices, ont_runs, ont_signal_workbench, payload_ownership_audit, project_manager, projects, queue, rcsb, ribocentre, rna_structure, sequence_qc, shape_blueprint, smiles_converter, system, telemetry, templates, user_sequences, user_templates, viewer_resources
from runtime_policy import workflow_launch_block_detail, workflow_launches_allowed
from biomodstack_runtime_profile import install_feature_enabled
from services.analysis_worker import AnalysisWorker
from services.boltz_api_jobs import BoltzApiJobWorker
from services.external_imports.worker import ExternalImportWorker
from services.gpu_orchestrator import GPUOrchestrator
from services.md.reconcile import MdReconcilerWorker
from services.ont_raw_signal_worker import OntRawSignalWorker
from services.ont_signal_worker import OntSignalWorker
from services.global_experiments.worker import (
    GlobalExperimentWorker,
    install_global_experiment_worker,
)
from services.ngs_molbio_n5 import reconcile_startup_admissions
from routers.gpu import get_gpu_stats
from services.workflow_adapter import workflow_adapter_base_url

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global orchestrator instance
_orchestrator: GPUOrchestrator = None
_analysis_worker: AnalysisWorker = None
_external_import_worker: ExternalImportWorker | None = None
_boltz_api_job_worker: BoltzApiJobWorker | None = None
_md_reconciler: MdReconcilerWorker | None = None
_global_experiment_worker: GlobalExperimentWorker | None = None
_ont_raw_signal_worker: OntRawSignalWorker | None = None
_ont_signal_worker: OntSignalWorker | None = None


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


async def wait_for_workflow_adapter_admission(
    *,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.25,
) -> None:
    """Block scheduler admission until the configured adapter answers health probes."""
    base_url = workflow_adapter_base_url()
    if not base_url:
        return
    health_url = f"{base_url}/api/workflow-adapter/health"
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout_seconds))
    last_status = "unavailable"
    while True:
        ready, last_status = await http_readiness(health_url)
        if ready:
            return
        if loop.time() >= deadline:
            raise RuntimeError(
                f"Configured workflow adapter is not ready for scheduler admission: {last_status}"
            )
        await asyncio.sleep(max(0.0, float(poll_interval_seconds)))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and GPU orchestrator on startup."""
    global _orchestrator
    global _analysis_worker
    global _external_import_worker
    global _boltz_api_job_worker
    global _md_reconciler
    global _global_experiment_worker
    global _ont_raw_signal_worker
    global _ont_signal_worker
    bioxp_runtime = None
    
    # Initialize independently owned core, global experiment, MolBio, and MolBio/NGS state stores.
    await init_db()
    await init_experiment_db()
    await init_molbio_db()
    await init_molbio_ngs_db()
    async with experiment_session_factory() as admission_session:
        async with async_session() as admission_core_session:
            pending_resource_evidence = await reconcile_startup_admissions(
                admission_session,
                admission_core_session,
            )
            await admission_session.commit()
        if pending_resource_evidence:
            logger.warning(
                "[STARTUP] %s managed-workflow resource admissions await producer evidence",
                pending_resource_evidence,
            )
    
    # Initialize GPU orchestrator only when this runtime is allowed to own workflow launches.
    if workflow_launches_allowed():
        await wait_for_workflow_adapter_admission()
        _orchestrator = GPUOrchestrator(
            db_session_factory=async_session,
            get_gpu_stats_fn=get_gpu_stats,
            launch_nextflow_job_fn=_orchestrator_launch_job,
            poll_interval=3.0
        )

        # Start orchestrator in background
        await _orchestrator.start()
        logger.info("[STARTUP] GPU Orchestrator started")
        _md_reconciler = MdReconcilerWorker(async_session, poll_interval=5.0)
        await _md_reconciler.start()
        logger.info("[STARTUP] MD lifecycle reconciler started")
        _global_experiment_worker = GlobalExperimentWorker(
            experiment_session_factory,
            async_session,
            molbio_ngs_session_factory,
            database_path=get_experiment_db_path(),
        )
        install_global_experiment_worker(_global_experiment_worker)
        await _global_experiment_worker.start()
        logger.info(
            "[STARTUP] Global experiment dispatcher/reconciler lease=%s",
            _global_experiment_worker.lease_state,
        )
    else:
        _orchestrator = None
        _md_reconciler = None
        _global_experiment_worker = None
        _ont_raw_signal_worker = None
        install_global_experiment_worker(None)
        logger.warning("[STARTUP] %s", workflow_launch_block_detail("start the GPU workflow scheduler"))

    ont_runtime_image = os.getenv("BMS_ONT_SLOW5TOOLS_IMAGE", "").strip()
    ont_runtime_digest = os.getenv("BMS_ONT_SLOW5TOOLS_IMAGE_DIGEST", "").strip()
    if ont_runtime_image and len(ont_runtime_digest) == 64:
        _ont_raw_signal_worker = OntRawSignalWorker(async_session, poll_interval=5.0)
        await _ont_raw_signal_worker.start()
        logger.info("[STARTUP] ONT raw-signal derivation worker started")
    else:
        _ont_raw_signal_worker = None
        logger.info("[STARTUP] ONT raw-signal worker disabled: pinned runtime identity absent")

    try:
        OntSignalWorker._runtime_identity()
    except (OSError, RuntimeError):
        _ont_signal_worker = None
        logger.info("[STARTUP] ONT signal-workbench worker disabled: approved Squigualiser runtime policy absent or mismatched")
    else:
        _ont_signal_worker = OntSignalWorker(async_session, molbio_ngs_session_factory, poll_interval=5.0)
        await _ont_signal_worker.start()
        logger.info("[STARTUP] governed ONT signal-workbench worker started")

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
        await bioxp_runtime.start()
        snapshot = bioxp_runtime.connection.snapshot()
        logger.info(
            "[STARTUP] BioXP control plane initialized active=%s reachable=%s runtime_ready=%s hardware_ready=%s",
            snapshot.active,
            snapshot.reachable,
            snapshot.runtime_ready,
            snapshot.hardware_ready,
        )
    
    yield
    
    # Cleanup on shutdown
    if bioxp_runtime is not None:
        await bioxp_runtime.close()
        logger.info("[SHUTDOWN] BioXP control plane closed")
    if _orchestrator:
        await _orchestrator.stop()
        logger.info("[SHUTDOWN] GPU Orchestrator stopped")
    if _md_reconciler:
        await _md_reconciler.stop()
        logger.info("[SHUTDOWN] MD lifecycle reconciler stopped")
    if _global_experiment_worker:
        await _global_experiment_worker.stop()
        install_global_experiment_worker(None)
        logger.info("[SHUTDOWN] Global experiment dispatcher/reconciler stopped")
    if _analysis_worker:
        await _analysis_worker.stop()
        logger.info("[SHUTDOWN] Analysis worker stopped")
    if _boltz_api_job_worker:
        await _boltz_api_job_worker.stop()
        logger.info("[SHUTDOWN] Boltz API submission worker stopped")
    if _external_import_worker:
        await _external_import_worker.stop()
        logger.info("[SHUTDOWN] External result import worker stopped")
    if _ont_raw_signal_worker:
        await _ont_raw_signal_worker.stop()
        logger.info("[SHUTDOWN] ONT raw-signal lease-recovery worker stopped")
    if _ont_signal_worker:
        await _ont_signal_worker.stop()
        logger.info("[SHUTDOWN] ONT signal-workbench worker stopped")


app = FastAPI(
    title="BioModStack Control Platform",
    description="Extensible platform for protein modification and design",
    version="0.2.0",
    lifespan=lifespan
)

app.add_middleware(FrustraMPNNUploadLimitMiddleware)

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
    "http://127.0.0.1:18082",
    "http://localhost:3000",
    "http://localhost:18082",
    "https://localhost:3000",
    "https://localhost:18082",
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


@app.middleware("http")
async def launch_context_provenance(request: Request, call_next):
    raw = request.headers.get("x-bms-launch-context-id")
    if raw is not None and (len(raw) > 128 or not raw.strip() or any(char.isspace() for char in raw)):
        return JSONResponse(status_code=400, content={"detail": "invalid launch-context header"})
    token = current_launch_context_id.set(raw)
    try:
        return await call_next(request)
    finally:
        current_launch_context_id.reset(token)

# Include routers
app.include_router(models.router, prefix="/api/models", tags=["models"])
app.include_router(molecular_dynamics.router)
app.include_router(templates.router, prefix="/api/templates", tags=["templates"])
app.include_router(inputs.router, prefix="/api/inputs", tags=["inputs"])
app.include_router(boltz_api_jobs.router, prefix="/api/jobs/boltz-api", tags=["boltz-api-jobs"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(external_imports.router, prefix="/api/jobs/imports/external", tags=["external-result-imports"])
app.include_router(md_results.router, prefix="/api/jobs", tags=["molecular-dynamics-results"])
app.include_router(viewer_resources.router, prefix="/api/jobs", tags=["viewer-resources"])
app.include_router(conformational_mapping.router)
app.include_router(shape_blueprint.router, prefix="/api/shape-blueprint", tags=["shape-blueprint"])
app.include_router(designs.router, prefix="/api/designs", tags=["designs"])
app.include_router(analyses.router, prefix="/api", tags=["analyses"])
app.include_router(gpu.router, prefix="/api/gpu", tags=["gpu"])
app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(user_sequences.router, prefix="/api/user-sequences", tags=["user-sequences"])
app.include_router(user_templates.router, prefix="/api/user-templates", tags=["user-templates"])
app.include_router(experiment_workspaces.router)
app.include_router(molbio_ngs_experiments.router)
app.include_router(projects.router)
app.include_router(project_manager.router)
app.include_router(ngs_molbio_n5.router)
app.include_router(payload_ownership_audit.router)
if dev_issues.dev_issue_ledger_enabled():
    app.include_router(dev_issues.router)
# msa_cache router removed - now using file-based caching
app.include_router(smiles_converter.router, prefix="/api/smiles", tags=["smiles"])
app.include_router(queue.router, prefix="/api", tags=["queue"])  # /api/queue/*
app.include_router(rcsb.router, prefix="/api/rcsb", tags=["rcsb"])
app.include_router(nucleotide_sequences.router)  # /api/sequences/*
app.include_router(system.router, prefix="/api", tags=["system"])  # /api/system/*
app.include_router(telemetry.router, prefix="/api", tags=["telemetry"])
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
app.include_router(ont_signal_workbench.router, prefix="/api/ont/signal-workbench", tags=["ont-signal-workbench"])
app.include_router(ont_runs.barcode_router, prefix="/api/jobs", tags=["ont-barcode-units"])
app.include_router(mobile_apk_updates.router, prefix="/api")
app.include_router(mobile_ui_updates.router, prefix="/api")

@app.get("/api/health")
async def health_check():
    """Separate process liveness from dependency and workflow readiness."""
    molbio = await molbio_health()
    molbio_ngs = await molbio_ngs_health()
    readiness = await collect_runtime_readiness(molbio=molbio, molbio_ngs=molbio_ngs)
    return {
        "status": "healthy" if readiness["ready"] else "degraded",
        "service": "biomodstack-api",
        "liveness": {"alive": True, "status": "alive"},
        "readiness": readiness,
        "build": current_build_identity(),
        "molbio": molbio,
        "molbio_ngs": molbio_ngs,
    }


@app.get("/api/version")
async def api_version():
    """Return immutable build identity for cross-surface provenance checks."""
    return {"service": "biomodstack-api", "build": current_build_identity()}


@app.get("/")
async def root():
    """Root redirect to API docs."""
    return {"message": "BioModStack API", "docs": "/docs"}
