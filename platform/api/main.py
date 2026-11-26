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
from molbio_ngs_database import (
    get_molbio_ngs_startup_error,
    init_molbio_ngs_db,
    molbio_ngs_health,
    molbio_ngs_session_factory,
    set_molbio_ngs_startup_error,
)
from build_identity import current_build_identity
from readiness import collect_runtime_readiness, http_readiness
from dev_issue_screenshot_upload_limit import DevIssueScreenshotUploadLimitMiddleware
from frustrampnn_upload_limit import FrustraMPNNUploadLimitMiddleware
from routers import atomworks_studio, molecular_construction, molecular_geometry
from routers import binder_blind_pose, binder_continuation
from routers import ligandmpnn_interface_context, sequence_native, protonpottsmpnn, protonpottsmpnn_prediction
from routers import analyses, analytics, boltz_api_jobs, boltzgen, conformational_mapping, designs, dev_issues, execution_targets, external_imports, files, frameworks, frustrampnn, gpu, inputs, jobs, md_results, mobile_apk_updates, mobile_ui_updates, models, molecular_dynamics, molbio_end_operations, molbio_ngs_experiments, molbio_ops, molbio_restriction, msa, ngs_alignment_sessions, ngs_molbio_n5, nucleotide_sequences, ont_devices, ont_runs, ont_signal_workbench, plr_results, projects, queue, rcsb, ribocentre, rna_structure, sequence_qc, smiles_converter, system, telemetry, templates, user_sequences, user_templates, viewer_resources
from runtime_policy import (
    WorkflowAdmissionBlocked,
    workflow_launch_block_detail,
    workflow_launches_allowed,
    workflow_mutation_admission,
)
from biomodstack_runtime_profile import install_feature_enabled
from services.ont_raw_signal_worker import OntRawSignalWorker
from services.ont_signal_worker import OntSignalWorker
from services.restriction_catalog import catalog_authority
from worker import wait_for_workflow_adapter_admission  # noqa: F401 - compatibility re-export

# Configure logging
logging.basicConfig(level=logging.INFO)
# httpx/httpcore log every request line at INFO. Background pollers (BioXP
# status every 10 s, adapter health, provider probes) made that most of
# api.log; failures still surface through WARNING and the callers' own logs.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# API-owned background controllers (remaining loops live in worker.py / biomodstack-worker).
_ont_raw_signal_worker: OntRawSignalWorker | None = None
_ont_signal_worker: OntSignalWorker | None = None
_ont_run_monitor = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize databases and API-owned controllers on startup."""
    global _ont_raw_signal_worker
    global _ont_signal_worker
    bioxp_runtime = None
    global _ont_run_monitor
    ngs_ont_enabled = install_feature_enabled("ngs_ont")
    bioxp_enabled = install_feature_enabled("bioxp")
    
    # Required checked-in scientific authority must be complete before workers start.
    catalog_authority.require()

    # Initialize independently owned core, global experiment, and MolBio state stores (fatal on error).
    await init_db()
    from services.remote_execution.targets import AttachmentController
    from services.remote_execution.preloading import PreloadController
    app.state.preload_controller = PreloadController(async_session)
    await app.state.preload_controller.recover()
    app.state.attachment_controller = AttachmentController(async_session)
    await app.state.attachment_controller.recover()
    await init_experiment_db()
    await init_molbio_db()

    # Optional MolBio/NGS store: never opened when disabled; startup error degrades only NGS routes.
    if ngs_ont_enabled:
        try:
            await init_molbio_ngs_db()
        except Exception as exc:
            set_molbio_ngs_startup_error(str(exc))
            logger.warning("[STARTUP] MolBio/NGS store unavailable (NGS routes will return 503): %s", exc)
    else:
        set_molbio_ngs_startup_error(None)

    # Background scheduling, MD reconciliation, remote telemetry/inventory, analysis,
    # external imports, Boltz API, FrustraMPNN statistics, and launch-context
    # recovery/publication run in biomodstack-worker (R31/R35).
    if not workflow_launches_allowed():
        _ont_raw_signal_worker = None

    if ngs_ont_enabled:
        from services.ont_local_images import (
            configured_container_runtime as _ont_container_runtime,
            local_image_identity as _ont_local_image_identity,
        )
        try:
            _ont_local_image_identity(
                "BMS_ONT_SLOW5TOOLS_IMAGE", _ont_container_runtime()
            )
        except RuntimeError:
            ont_raw_image_present = False
        else:
            ont_raw_image_present = True
        if ont_raw_image_present:
            _ont_raw_signal_worker = OntRawSignalWorker(async_session, poll_interval=5.0)
            await _ont_raw_signal_worker.start()
            logger.info("[STARTUP] ONT raw-signal derivation worker started")
        else:
            _ont_raw_signal_worker = None
            logger.info("[STARTUP] ONT raw-signal worker disabled: raw-signal image is not built on this machine")

        # The raw-signal worker already polls active runs; otherwise run a plain
        # status monitor so MinKNOW runs started from BMS still reach completion.
        if _ont_raw_signal_worker is None and os.getenv("BMS_ONT_MINKNOW_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            from services.ont_run_control import OntRunMonitor
            _ont_run_monitor = OntRunMonitor(poll_interval=10.0)
            await _ont_run_monitor.start()
            logger.info("[STARTUP] ONT run status monitor started")

        if get_molbio_ngs_startup_error() is None:
            try:
                OntSignalWorker._runtime_identity()
            except (OSError, RuntimeError):
                _ont_signal_worker = None
                logger.info("[STARTUP] ONT signal-workbench worker disabled: Squigualiser image is not built on this machine")
            else:
                _ont_signal_worker = OntSignalWorker(async_session, molbio_ngs_session_factory, poll_interval=5.0)
                if await _ont_signal_worker.start():
                    logger.info("[STARTUP] governed ONT signal-workbench worker started")
                else:
                    logger.warning("[STARTUP] ONT signal-workbench backend unavailable: worker blocked; scientific work unchanged; restart after provisioning to retry recovery")
        else:
            _ont_signal_worker = None
    else:
        _ont_raw_signal_worker = None
        _ont_run_monitor = None
        _ont_signal_worker = None

    app.state.bioxp_runtime = None
    app.state.bioxp_startup_error = None
    if bioxp_enabled:
        try:
            from services.bioxp.runtime import create_bioxp_runtime

            bioxp_runtime = create_bioxp_runtime()
            await bioxp_runtime.start()
            app.state.bioxp_runtime = bioxp_runtime
            snapshot = bioxp_runtime.connection.snapshot()
            logger.info(
                "[STARTUP] BioXP control plane initialized active=%s reachable=%s runtime_ready=%s hardware_ready=%s",
                snapshot.active,
                snapshot.reachable,
                snapshot.runtime_ready,
                snapshot.hardware_ready,
            )
        except Exception as exc:
            bioxp_runtime = None
            app.state.bioxp_runtime = None
            app.state.bioxp_startup_error = str(exc)
            logger.warning("[STARTUP] BioXP control plane unavailable (BioXP routes will return 503): %s", exc)
    
    yield
    
    # Cleanup API-owned controllers on shutdown.
    await app.state.preload_controller.close()
    await app.state.attachment_controller.close()
    if bioxp_runtime is not None:
        await bioxp_runtime.close()
        logger.info("[SHUTDOWN] BioXP control plane closed")
    if _ont_raw_signal_worker:
        await _ont_raw_signal_worker.stop()
        logger.info("[SHUTDOWN] ONT raw-signal lease-recovery worker stopped")
    if _ont_run_monitor:
        await _ont_run_monitor.stop()
        logger.info("[SHUTDOWN] ONT run status monitor stopped")
    if _ont_signal_worker:
        await _ont_signal_worker.stop()
        logger.info("[SHUTDOWN] ONT signal-workbench worker stopped")


_BIOXP_ENABLED = install_feature_enabled("bioxp")
_NGS_ONT_ENABLED = install_feature_enabled("ngs_ont")
_STATS_ENABLED = install_feature_enabled("stats")
_PROTEIN_COMPUTE_ENABLED = install_feature_enabled("protein_compute")

app = FastAPI(
    title="BioModStack Control Platform",
    description="Extensible platform for protein modification and design",
    version="0.2.0",
    lifespan=lifespan
)
app.state.mounted_features = {
    "bioxp": _BIOXP_ENABLED,
    "ngs_ont": _NGS_ONT_ENABLED,
    "stats": _STATS_ENABLED,
    "protein_compute": _PROTEIN_COMPUTE_ENABLED,
}
if _NGS_ONT_ENABLED:
    app.add_exception_handler(
        ngs_alignment_sessions.OntNgsRouteError,
        ngs_alignment_sessions.ont_ngs_route_error_handler,
    )

app.add_middleware(FrustraMPNNUploadLimitMiddleware)
app.add_middleware(DevIssueScreenshotUploadLimitMiddleware)


_BIOXP_RUN_START_EXACT_PATHS = frozenset({
    "/api/bioxp/methods/quick-runs",
    "/api/bioxp/protocols/submit",
})
_BIOXP_METHOD_LIBRARY_RUN_RE = re.compile(r"^/api/bioxp/methods/library/[^/]+/runs$")


def _is_deployment_fence_exempt(method: str, path: str) -> bool:
    verb = method.upper()
    if verb in {"GET", "HEAD", "OPTIONS"}:
        return True
    normalized = path.rstrip("/") or "/"
    if normalized == "/api/bioxp" or normalized.startswith("/api/bioxp/"):
        if verb == "POST" and (
            normalized in _BIOXP_RUN_START_EXACT_PATHS
            or _BIOXP_METHOD_LIBRARY_RUN_RE.fullmatch(normalized) is not None
        ):
            return False
        return True
    return False


@app.middleware("http")
async def deployment_admission_fence(request: Request, call_next):
    if _is_deployment_fence_exempt(request.method, request.url.path):
        return await call_next(request)
    try:
        with workflow_mutation_admission():
            return await call_next(request)
    except WorkflowAdmissionBlocked as exc:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

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
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(sequence_native.router, prefix="/api", tags=["native-sequence-results"])
if _PROTEIN_COMPUTE_ENABLED:
    app.include_router(boltz_api_jobs.router, prefix="/api/jobs/boltz-api", tags=["boltz-api-jobs"])
    app.include_router(protonpottsmpnn.router, prefix="/api/jobs", tags=["protonpottsmpnn"])
    app.include_router(protonpottsmpnn_prediction.router, prefix="/api/jobs", tags=["protonpottsmpnn"])
    app.include_router(binder_blind_pose.router, prefix="/api/blind-pose", tags=["blind-pose"])
    app.include_router(binder_continuation.router, prefix="/api/binder-continuation", tags=["binder-continuation"])
    app.include_router(ligandmpnn_interface_context.router)
    app.include_router(conformational_mapping.router)
    app.include_router(frameworks.router)  # /api/frameworks/* - SAbDab integration
    app.include_router(boltzgen.router)
    app.include_router(msa.router)
    app.include_router(frustrampnn.router)  # /api/frustrampnn/* - Energetic frustration analysis
app.include_router(
    execution_targets.router,
    prefix="/api/execution-targets",
    tags=["execution-targets"],
)
app.include_router(external_imports.router, prefix="/api/jobs/imports/external", tags=["external-result-imports"])
app.include_router(md_results.router, prefix="/api/jobs", tags=["molecular-dynamics-results"])
app.include_router(viewer_resources.router, prefix="/api/jobs", tags=["viewer-resources"])
app.include_router(plr_results.router, prefix="/api/jobs", tags=["plr-workflow-results"])
app.include_router(designs.router, prefix="/api/designs", tags=["designs"])
app.include_router(analyses.router, prefix="/api", tags=["analyses"])
app.include_router(gpu.router, prefix="/api/gpu", tags=["gpu"])
app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(atomworks_studio.router)
app.include_router(molecular_construction.router)
app.include_router(molecular_geometry.router)
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(user_sequences.router, prefix="/api/user-sequences", tags=["user-sequences"])
app.include_router(user_templates.router, prefix="/api/user-templates", tags=["user-templates"])
app.include_router(projects.router)
from routers import family_msa_results
app.include_router(family_msa_results.router)
if dev_issues.dev_issue_ledger_enabled():
    dev_issues.init_dev_issues_store()
    app.include_router(dev_issues.router)
# msa_cache router removed - now using file-based caching
app.include_router(smiles_converter.router, prefix="/api/smiles", tags=["smiles"])
app.include_router(queue.router, prefix="/api", tags=["queue"])  # /api/queue/*
app.include_router(rcsb.router, prefix="/api/rcsb", tags=["rcsb"])
app.include_router(nucleotide_sequences.router)  # /api/sequences/*
app.include_router(system.router, prefix="/api", tags=["system"])  # /api/system/*
app.include_router(telemetry.router, prefix="/api", tags=["telemetry"])
from routers import tools_installer
app.include_router(tools_installer.router)  # /api/tools/* - tool installer
app.include_router(molbio_ops.router)
from routers import molbio_golden_gate_design
app.include_router(molbio_golden_gate_design.router)
from routers import molbio_gibson_suggest
app.include_router(molbio_gibson_suggest.router)
app.include_router(molbio_restriction.router)
from routers import molbio_relatives
app.include_router(molbio_relatives.router)
from routers import molbio_branches
app.include_router(molbio_branches.router)
app.include_router(molbio_end_operations.router)
app.include_router(rna_structure.router)
app.include_router(ribocentre.router, prefix="/api/ribocentre", tags=["ribocentre"])
if _BIOXP_ENABLED:
    from routers import bioxp

    app.include_router(bioxp.router, prefix="/api/bioxp", tags=["bioxp"])
if _NGS_ONT_ENABLED:
    app.include_router(molbio_ngs_experiments.router)
    app.include_router(ngs_molbio_n5.router)
    app.include_router(sequence_qc.router, prefix="/api/sequence-qc", tags=["sequence-qc"])
    app.include_router(ngs_alignment_sessions.router, prefix="/api", tags=["ngs-alignment"])
    ngs_alignment_sessions.install_governed_ngs_openapi(app)
    app.include_router(ont_devices.router, prefix="/api/ont", tags=["ont-devices"])
    app.include_router(ont_runs.router, prefix="/api/ont", tags=["ont-runs"])
    app.include_router(ont_signal_workbench.router, prefix="/api/ont/signal-workbench", tags=["ont-signal-workbench"])
    app.include_router(ont_runs.barcode_router, prefix="/api/jobs", tags=["ont-barcode-units"])
app.include_router(mobile_apk_updates.router, prefix="/api")
app.include_router(mobile_ui_updates.router, prefix="/api")

async def _safe_molbio_ngs_health(*, deep: bool = False) -> dict[str, object]:
    startup_error = get_molbio_ngs_startup_error()
    if startup_error is not None:
        return {
            "status": "unavailable",
            "error": startup_error,
            "attestation": {"ok": False},
        }
    try:
        return await molbio_ngs_health(deep=deep)
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": str(exc),
            "attestation": {"ok": False},
        }


@app.get("/api/health")
async def health_check(deep: bool = False):
    """Report readiness without rescanning retained science on ordinary polls.

    Explicit ``?deep=true`` requests keep the full diagnostic integrity audit.
    Startup, release and exact scientific reads retain their own checks.
    """
    ngs_enabled = bool(app.state.mounted_features.get("ngs_ont", True))
    if ngs_enabled:
        molbio, molbio_ngs = await asyncio.gather(
            molbio_health(deep=deep), _safe_molbio_ngs_health(deep=deep),
        )
    else:
        molbio = await molbio_health(deep=deep)
        molbio_ngs = {"status": "disabled", "enabled": False}
    readiness = await collect_runtime_readiness(
        molbio=molbio, molbio_ngs=molbio_ngs,
    )
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
