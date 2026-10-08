"""
MSA job submission API for nucleotide/protein sequences.
Creates a GPU-backed msa_batch job using existing orchestrator pipeline.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
from pathlib import Path
import uuid
import os
import json as json_lib
import hashlib
import gzip

from database import Job, NucleotideSequence, get_session
from services.gpu_orchestrator import estimate_vram
from services.msa_server import (
    ensure_server_for_db,
    read_server_settings,
    resolve_msa_gpu_id,
    server_status,
    stop_servers,
    touch_query_activity,
    write_server_settings,
)
from paths import get_results_dir, get_msa_cache_dir


router = APIRouter(prefix="/api/msa", tags=["msa"])


class MSASequence(BaseModel):
    name: str
    sequence: str


class MSARequest(BaseModel):
    name: str
    sequence: Optional[str] = None
    sequence_id: Optional[str] = None
    reference_sequence: Optional[str] = None
    sequences: Optional[List[MSASequence]] = None
    msa_use_gpu: Optional[bool] = True
    msa_force_refresh: Optional[bool] = False
    msa_max_seqs: Optional[int] = None


class MSAResponse(BaseModel):
    job_id: str
    status: str
    output_dir: str
    created_at: datetime


def _normalize_sequence_for_cache(sequence: str) -> str:
    return "".join((sequence or "").split())


def _cached_msa_depth(cache_path: Path) -> int | None:
    try:
        depth = 0
        with gzip.open(cache_path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith(">"):
                    depth += 1
        return depth
    except Exception:
        return None


@router.get("/cache-info")
async def get_msa_cache_info(sequence: str, max_entries: int = 25):
    """
    Inspect local MSA cache entries for a sequence.

    Returns canonical single-cache status and any legacy profile-scoped entries.
    """
    normalized = _normalize_sequence_for_cache(sequence)
    if not normalized:
        raise HTTPException(status_code=400, detail="sequence is required")

    if max_entries < 1:
        max_entries = 1
    if max_entries > 100:
        max_entries = 100

    seq_hash = hashlib.sha256(normalized.encode()).hexdigest()
    cache_root = get_msa_cache_dir()
    subdir = cache_root / seq_hash[:2]
    canonical = subdir / f"{seq_hash}.a3m.gz"

    candidates: list[Path] = []
    if canonical.exists():
        candidates.append(canonical)
    if subdir.exists():
        candidates.extend(
            sorted(
                [p for p in subdir.glob(f"{seq_hash}_*.a3m.gz") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        )

    entries = []
    for cache_path in candidates[:max_entries]:
        name = cache_path.name
        if name == f"{seq_hash}.a3m.gz":
            profile = "single"
        else:
            profile = name.replace(f"{seq_hash}_", "").replace(".a3m.gz", "")
        stat = cache_path.stat()
        entries.append(
            {
                "name": name,
                "profile": profile,
                "path": str(cache_path),
                "size_bytes": int(stat.st_size),
                "modified_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
                "depth": _cached_msa_depth(cache_path),
                "canonical": cache_path == canonical,
            }
        )

    best_depth = None
    for entry in entries:
        depth_val = entry.get("depth")
        if isinstance(depth_val, int):
            best_depth = depth_val if best_depth is None else max(best_depth, depth_val)

    return {
        "sequence_hash": seq_hash,
        "cache_dir": str(cache_root),
        "canonical_exists": canonical.exists(),
        "canonical_path": str(canonical) if canonical.exists() else None,
        "cache_entries": len(candidates),
        "best_depth": best_depth,
        "entries": entries,
    }


@router.post("", response_model=MSAResponse)
async def create_msa_job(request: MSARequest, session: AsyncSession = Depends(get_session)):
    sequences = []

    if request.sequences:
        sequences = [{"name": s.name, "sequence": s.sequence} for s in request.sequences]
    elif request.sequence_id:
        result = await session.execute(
            select(NucleotideSequence).where(NucleotideSequence.id == request.sequence_id)
        )
        seq = result.scalar_one_or_none()
        if not seq:
            raise HTTPException(status_code=404, detail="Sequence not found")
        sequences = [{"name": seq.name, "sequence": seq.sequence}]
    elif request.sequence:
        sequences = [{"name": request.name, "sequence": request.sequence}]
    else:
        raise HTTPException(status_code=400, detail="Provide sequence, sequence_id, or sequences list")

    job_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    base_output_dir = str(get_results_dir() / f"msa_{request.name}_{timestamp}")
    os.makedirs(base_output_dir, exist_ok=True)

    seq_len = len(sequences[0]["sequence"]) if sequences else 300
    vram = estimate_vram("msa_batch", seq_len)

    job = Job(
        id=job_id,
        name=f"{request.name}_msa",
        status="queued",
        model_id="msa_batch",
        mode="msa_generation",
        params={
            "sequences": sequences,
            "sequences_json": json_lib.dumps(sequences),
            "reference_sequence": request.reference_sequence,
            "msa_use_gpu": bool(request.msa_use_gpu) if request.msa_use_gpu is not None else True,
            "msa_force_refresh": bool(request.msa_force_refresh),
            "msa_max_seqs": request.msa_max_seqs,
        },
        output_dir=base_output_dir,
        created_at=datetime.utcnow(),
        queue_status="queued",
        vram_estimate_mb=vram,
        sequence_length=seq_len,
        job_phase="msa_generation",
        msa_sequences=sequences,
    )

    session.add(job)
    await session.commit()
    await session.refresh(job)

    return MSAResponse(
        job_id=job.id,
        status=job.status,
        output_dir=job.output_dir or "",
        created_at=job.created_at
    )


class MSAServerStartRequest(BaseModel):
    gpu_id: Optional[int] = None
    include_envdb: Optional[bool] = None
    max_seqs: int = 300
    prefilter_mode: int = 1
    db_load_mode: int = 0
    startup_wait_seconds: float = 1.0


class MSAServerStopRequest(BaseModel):
    gpu_id: Optional[int] = None


class MSAServerSettingsUpdate(BaseModel):
    include_envdb_on_start: Optional[bool] = None
    auto_stop_idle_enabled: Optional[bool] = None
    auto_stop_idle_minutes: Optional[int] = None
    pinned_gpu_id: Optional[int] = None


@router.get("/server/status")
async def get_msa_server_status(
    gpu_id: Optional[int] = None,
    include_envdb: Optional[bool] = None,
    max_seqs: int = 300,
    prefilter_mode: int = 1,
    db_load_mode: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """Get current persistent MSA server state for the selected/default GPU."""
    try:
        settings = read_server_settings()
        effective_gpu_id = gpu_id if gpu_id is not None else settings.get("pinned_gpu_id")
        active_batch_result = await session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.model_id == "msa_batch",
                Job.queue_status == "running",
            )
        )
        has_active_batch_job = int(active_batch_result.scalar() or 0) > 0

        return server_status(
            gpu_id=effective_gpu_id,
            include_envdb=include_envdb,
            max_seqs=max_seqs,
            prefilter_mode=prefilter_mode,
            db_load_mode=db_load_mode,
            has_active_batch_job=has_active_batch_job,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read MSA server status: {exc}")


@router.get("/server/settings")
async def get_msa_server_settings():
    """Get persisted MSA server settings."""
    try:
        return {"success": True, "settings": read_server_settings()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read MSA server settings: {exc}")


@router.put("/server/settings")
async def update_msa_server_settings(request: MSAServerSettingsUpdate):
    """Update persisted MSA server settings."""
    try:
        current = read_server_settings()
        patch = {field: getattr(request, field) for field in request.model_fields_set}
        merged = {**current, **patch}
        settings = write_server_settings(merged)
        return {"success": True, "settings": settings}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update MSA server settings: {exc}")


@router.post("/server/start")
async def start_msa_server(request: MSAServerStartRequest):
    """
    Start persistent MMseqs gpuserver(s) for local MSA.

    By default this starts UniRef only on the scheduler-preferred MSA GPU.
    EnvDB startup follows persisted server settings unless explicitly requested.
    """
    try:
        settings = read_server_settings()
        include_envdb = (
            bool(request.include_envdb)
            if request.include_envdb is not None
            else bool(settings.get("include_envdb_on_start", False))
        )
        requested_gpu_id = request.gpu_id if request.gpu_id is not None else settings.get("pinned_gpu_id")
        gpu_id = resolve_msa_gpu_id(requested_gpu_id)
        started = [
            ensure_server_for_db(
                db_alias="uniref",
                gpu_id=gpu_id,
                max_seqs=request.max_seqs,
                prefilter_mode=request.prefilter_mode,
                db_load_mode=request.db_load_mode,
                startup_wait_seconds=request.startup_wait_seconds,
            )
        ]
        if include_envdb:
            started.append(
                ensure_server_for_db(
                    db_alias="envdb",
                    gpu_id=gpu_id,
                    max_seqs=request.max_seqs,
                    prefilter_mode=request.prefilter_mode,
                    db_load_mode=request.db_load_mode,
                    startup_wait_seconds=request.startup_wait_seconds,
                )
            )
        touch_query_activity(
            {
                "event": "manual_server_start",
                "gpu_id": gpu_id,
                "include_envdb": include_envdb,
            }
        )
        return {
            "success": True,
            "gpu_id": gpu_id,
            "include_envdb": include_envdb,
            "servers": started,
            "message": f"MSA server ready on GPU {gpu_id}",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start MSA server: {exc}")


@router.post("/server/stop")
async def stop_msa_server(request: MSAServerStopRequest):
    """Stop persistent MMseqs gpuserver(s)."""
    try:
        settings = read_server_settings()
        effective_gpu_id = request.gpu_id if request.gpu_id is not None else settings.get("pinned_gpu_id")
        result = stop_servers(gpu_id=effective_gpu_id)
        return {
            "success": True,
            **result,
            "message": "Stopped persistent MSA server(s)",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to stop MSA server: {exc}")
