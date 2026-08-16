from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

import database
from routers import gpu, workflow_adapter
from mobile_apk_auth import require_tailnet_environment_tailscale_identity


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    # The adapter is lane-local.  The launcher script rejects an unqualified
    # process before uvicorn starts, while direct test/import use may omit the
    # identity; in that compatibility case there is no lane to reconcile.
    if os.getenv("BMS_WORKFLOW_ADAPTER_LANE", "").strip():
        report = await workflow_adapter.reconcile_workflow_adapter_startup()
        logger.info("Workflow adapter startup reconciliation: %s", report)
    yield


app = FastAPI(
    title="BioModStack Workflow Adapter",
    description="Host-native workflow adapter for detached Nextflow ownership.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://localhost"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def restrict_tailnet_forwarded_surface(request: Request, call_next):
    # Tailscale Serve injects this header on identity-aware proxy requests. The
    # adapter also serves private loopback-only API calls, so forwarded Tailnet
    # traffic must be restricted to the two deliberately published root routes.
    host = request.headers.get("host", "").split(":", 1)[0].rstrip(".").casefold()
    tailnet_forwarded = "tailscale-user-login" in request.headers or host.endswith(".ts.net")
    if tailnet_forwarded and request.url.path not in {"/", "/status", "/select"}:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    return await call_next(request)

app.include_router(workflow_adapter.router, prefix="/api")
app.include_router(gpu.router, prefix="/api/gpu")

# Tailscale Serve strips the configured set-path prefix before proxying. Keep
# control-only routes at the app root so Serve can proxy to an origin without a
# URL path; that preserves its authenticated identity headers. GET / is the
# canonical index for the configured Serve prefix; /status is a compatibility
# alias for older clients.
app.add_api_route(
    "/",
    workflow_adapter.workflow_adapter_tailnet_environment_status,
    methods=["GET"],
    dependencies=[Depends(require_tailnet_environment_tailscale_identity)],
)
app.add_api_route(
    "/status",
    workflow_adapter.workflow_adapter_tailnet_environment_status,
    methods=["GET"],
    dependencies=[Depends(require_tailnet_environment_tailscale_identity)],
)
app.add_api_route(
    "/select",
    workflow_adapter.workflow_adapter_select_tailnet_environment,
    methods=["POST"],
    dependencies=[Depends(require_tailnet_environment_tailscale_identity)],
)
