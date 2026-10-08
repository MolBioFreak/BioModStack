from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

import database
from routers import gpu, workflow_adapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield


app = FastAPI(
    title="BioModStack Workflow Adapter",
    description="Host-native workflow adapter for detached Nextflow ownership.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(workflow_adapter.router, prefix="/api")
app.include_router(gpu.router, prefix="/api/gpu")
