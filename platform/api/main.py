"""
BioModStack Control Platform - FastAPI Backend

Main application entry point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os

from database import init_db
from routers import jobs, gpu, files, models, templates, inputs, designs, analytics, user_sequences, user_templates, msa_cache, smiles_converter


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    await init_db()
    yield


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
app.include_router(msa_cache.router, prefix="/api/msa-cache", tags=["msa-cache"])
app.include_router(smiles_converter.router, prefix="/api/smiles", tags=["smiles"])


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "biomodstack-api"}


@app.get("/")
async def root():
    """Root redirect to API docs."""
    return {"message": "BioModStack API", "docs": "/docs"}

