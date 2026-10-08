"""
MSA Cache management API endpoints.

Provides visibility into cached ColabFold MSA results and cache management operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from pydantic import BaseModel
from datetime import datetime, timedelta
from pathlib import Path
import os
import shutil

from database import get_session, MSACache

router = APIRouter()

# Cache directory (should match Nextflow config)
CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data" / "msa_cache"


class MSACacheEntry(BaseModel):
    """MSA cache entry response model."""
    id: str
    sequence_hash: str
    sequence_preview: str  # First 50 chars
    sequence_length: int
    msa_path: str
    file_size_bytes: int
    colabfold_job_id: str | None
    hit_count: int
    created_at: datetime
    last_accessed: datetime
    expires_at: datetime
    is_expired: bool


class MSACacheStats(BaseModel):
    """Cache statistics."""
    total_entries: int
    total_size_bytes: int
    total_size_mb: float
    expired_entries: int
    total_hits: int
    oldest_entry: datetime | None
    newest_entry: datetime | None


class PurgeResult(BaseModel):
    """Result of purge operation."""
    deleted_count: int
    freed_bytes: int
    freed_mb: float


@router.get("", response_model=list[MSACacheEntry])
async def list_cached_msas(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    include_expired: bool = Query(default=True),
    session: AsyncSession = Depends(get_session)
):
    """List all cached MSA entries with pagination."""
    query = select(MSACache).order_by(MSACache.created_at.desc()).offset(offset).limit(limit)
    
    if not include_expired:
        query = query.where(MSACache.expires_at > datetime.utcnow())
    
    result = await session.execute(query)
    entries = result.scalars().all()
    
    return [
        MSACacheEntry(
            id=e.id,
            sequence_hash=e.sequence_hash,
            sequence_preview=e.sequence[:50] + ("..." if len(e.sequence) > 50 else ""),
            sequence_length=e.sequence_length,
            msa_path=e.msa_path,
            file_size_bytes=e.file_size_bytes,
            colabfold_job_id=e.colabfold_job_id,
            hit_count=e.hit_count,
            created_at=e.created_at,
            last_accessed=e.last_accessed,
            expires_at=e.expires_at,
            is_expired=e.expires_at < datetime.utcnow()
        )
        for e in entries
    ]


@router.get("/stats", response_model=MSACacheStats)
async def get_cache_stats(session: AsyncSession = Depends(get_session)):
    """Get cache statistics."""
    now = datetime.utcnow()
    
    # Total entries and size
    total_result = await session.execute(
        select(func.count(MSACache.id), func.sum(MSACache.file_size_bytes), func.sum(MSACache.hit_count))
    )
    total_count, total_size, total_hits = total_result.one()
    total_size = total_size or 0
    total_hits = total_hits or 0
    
    # Expired entries
    expired_result = await session.execute(
        select(func.count(MSACache.id)).where(MSACache.expires_at < now)
    )
    expired_count = expired_result.scalar() or 0
    
    # Date range
    dates_result = await session.execute(
        select(func.min(MSACache.created_at), func.max(MSACache.created_at))
    )
    oldest, newest = dates_result.one()
    
    return MSACacheStats(
        total_entries=total_count or 0,
        total_size_bytes=total_size,
        total_size_mb=round(total_size / (1024 * 1024), 2),
        expired_entries=expired_count,
        total_hits=total_hits,
        oldest_entry=oldest,
        newest_entry=newest
    )


@router.get("/{sequence_hash}", response_model=MSACacheEntry)
async def get_cache_entry(sequence_hash: str, session: AsyncSession = Depends(get_session)):
    """Get details for a specific cached MSA."""
    result = await session.execute(
        select(MSACache).where(MSACache.sequence_hash == sequence_hash)
    )
    entry = result.scalar_one_or_none()
    
    if not entry:
        raise HTTPException(status_code=404, detail="Cache entry not found")
    
    return MSACacheEntry(
        id=entry.id,
        sequence_hash=entry.sequence_hash,
        sequence_preview=entry.sequence[:50] + ("..." if len(entry.sequence) > 50 else ""),
        sequence_length=entry.sequence_length,
        msa_path=entry.msa_path,
        file_size_bytes=entry.file_size_bytes,
        colabfold_job_id=entry.colabfold_job_id,
        hit_count=entry.hit_count,
        created_at=entry.created_at,
        last_accessed=entry.last_accessed,
        expires_at=entry.expires_at,
        is_expired=entry.expires_at < datetime.utcnow()
    )


@router.delete("/{sequence_hash}")
async def delete_cache_entry(sequence_hash: str, session: AsyncSession = Depends(get_session)):
    """Delete a specific cached MSA entry and its file."""
    result = await session.execute(
        select(MSACache).where(MSACache.sequence_hash == sequence_hash)
    )
    entry = result.scalar_one_or_none()
    
    if not entry:
        raise HTTPException(status_code=404, detail="Cache entry not found")
    
    # Delete file
    file_path = Path(entry.msa_path)
    freed_bytes = 0
    if file_path.exists():
        freed_bytes = file_path.stat().st_size
        file_path.unlink()
    
    # Delete DB record
    await session.delete(entry)
    await session.commit()
    
    return {"message": "Cache entry deleted", "freed_bytes": freed_bytes}


@router.post("/purge-expired", response_model=PurgeResult)
async def purge_expired_entries(session: AsyncSession = Depends(get_session)):
    """Delete all expired cache entries (older than 30 days)."""
    now = datetime.utcnow()
    
    # Find expired entries
    result = await session.execute(
        select(MSACache).where(MSACache.expires_at < now)
    )
    expired = result.scalars().all()
    
    deleted_count = 0
    freed_bytes = 0
    
    for entry in expired:
        # Delete file
        file_path = Path(entry.msa_path)
        if file_path.exists():
            freed_bytes += file_path.stat().st_size
            file_path.unlink()
        
        await session.delete(entry)
        deleted_count += 1
    
    await session.commit()
    
    return PurgeResult(
        deleted_count=deleted_count,
        freed_bytes=freed_bytes,
        freed_mb=round(freed_bytes / (1024 * 1024), 2)
    )


@router.post("/clear", response_model=PurgeResult)
async def clear_cache(
    confirm: bool = Query(default=False, description="Must be true to confirm deletion"),
    session: AsyncSession = Depends(get_session)
):
    """Clear the entire MSA cache. Requires confirm=true."""
    if not confirm:
        raise HTTPException(
            status_code=400, 
            detail="Must pass confirm=true to clear cache"
        )
    
    # Get all entries for file deletion
    result = await session.execute(select(MSACache))
    entries = result.scalars().all()
    
    deleted_count = 0
    freed_bytes = 0
    
    for entry in entries:
        file_path = Path(entry.msa_path)
        if file_path.exists():
            freed_bytes += file_path.stat().st_size
            file_path.unlink()
        deleted_count += 1
    
    # Clear all records
    await session.execute(delete(MSACache))
    await session.commit()
    
    # Also clean up any orphaned files in cache dir
    if CACHE_DIR.exists():
        for subdir in CACHE_DIR.iterdir():
            if subdir.is_dir():
                shutil.rmtree(subdir, ignore_errors=True)
    
    return PurgeResult(
        deleted_count=deleted_count,
        freed_bytes=freed_bytes,
        freed_mb=round(freed_bytes / (1024 * 1024), 2)
    )
