from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from build_identity import current_build_identity


def _load_router_module(module_name: str):
    module_path = Path(__file__).resolve().parent / "routers" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(f"mobile_update_publisher_{module_name}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load mobile update router: {module_name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mobile_apk_updates = _load_router_module("mobile_apk_updates")
mobile_ui_updates = _load_router_module("mobile_ui_updates")


app = FastAPI(
    title="BioModStack Mobile Update Publisher",
    version="1",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://localhost"],
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["Accept", "Range"],
    allow_private_network=True,
)


@app.middleware("http")
async def add_private_network_access_header(request: Request, call_next):
    response = await call_next(request)
    if request.headers.get("access-control-request-private-network") == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "healthy",
        "service": "biomodstack-mobile-update-publisher",
        "build": current_build_identity(),
    }


app.include_router(mobile_ui_updates.router, prefix="/api")
app.include_router(mobile_apk_updates.router, prefix="/api")
