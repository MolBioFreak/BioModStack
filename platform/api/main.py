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
from routers import jobs, gpu, files, models


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
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(gpu.router, prefix="/api/gpu", tags=["gpu"])
app.include_router(files.router, prefix="/api/files", tags=["files"])


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "biomodstack-api"}


@app.get("/")
async def root():
    """Root redirect to API docs."""
    return {"message": "BioModStack API", "docs": "/docs"}

