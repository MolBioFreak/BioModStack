"""
Frameworks API Router - SAbDab integration and framework library management.

Provides endpoints for:
- Searching local SAbDab VHH database (SQLite-backed, offline-capable)
- Downloading and caching framework PDBs
- Managing local framework library
- Database statistics and filter options
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
from pathlib import Path
import logging

# Local database for search (offline-capable, fast)
from services.sabdab_db import get_sabdab_db, VHHStructure

# Remote client for PDB downloads (still needs network)
from services.sabdab_client import (
    download_pdb,
    get_structure_summary,
    convert_sabdab_to_hlt,
    CACHE_DIR
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/frameworks", tags=["frameworks"])


# Response models
class SAbDabSearchResult(BaseModel):
    """Search result with expanded fields from local database."""
    pdb_code: str
    h_chain: str
    model: int = 0
    resolution: Optional[float]
    method: Optional[str]
    species: Optional[str]  # heavy_species
    germline: Optional[str]  # heavy_subclass
    cdr_h3_length: Optional[int]
    cdr_h3_sequence: Optional[str]
    antigen_type: Optional[str]
    antigen_name: Optional[str]
    affinity: Optional[float]
    date: Optional[str]
    engineered: bool = False
    has_antigen: bool = False


class FrameworkDownloadResponse(BaseModel):
    pdb_code: str
    scheme: str
    cached: bool
    file_path: Optional[str]
    pdb_content: Optional[str]  # Only included if include_content=True


class CachedFramework(BaseModel):
    pdb_code: str
    scheme: str
    file_path: str
    size_bytes: int


class FrameworkLibraryResponse(BaseModel):
    frameworks: List[CachedFramework]
    total: int
    cache_dir: str


class DatabaseStatsResponse(BaseModel):
    total_entries: int
    entries_with_cdr_h3: int
    last_sync: Optional[str]
    species_distribution: dict
    db_path: str
    db_size_mb: float


class FilterOptionsResponse(BaseModel):
    species: List[str]
    methods: List[str]
    antigen_types: List[str]
    germlines: List[str]
    cdr_h3_length_range: List[int]


class SearchResponse(BaseModel):
    """Paginated search response."""
    results: List[SAbDabSearchResult]
    total: int
    limit: int
    offset: int


# ============================================================================
# Search Endpoints (Local Database)
# ============================================================================

@router.get("/sabdab/search", response_model=SearchResponse)
async def search_sabdab_frameworks(
    species: Optional[str] = Query(None, description="Filter by species (e.g., 'camel', 'llama')"),
    resolution_min: Optional[float] = Query(None, description="Minimum resolution in Angstroms"),
    resolution_max: Optional[float] = Query(2.5, description="Maximum resolution in Angstroms"),
    cdr_h3_min: Optional[int] = Query(None, description="Minimum CDR-H3 length"),
    cdr_h3_max: Optional[int] = Query(None, description="Maximum CDR-H3 length"),
    antigen_type: Optional[str] = Query(None, description="Antigen type filter"),
    has_antigen: Optional[bool] = Query(None, description="True=bound, False=unbound, None=all"),
    methods: Optional[str] = Query(None, description="Comma-separated methods (e.g., 'X-RAY DIFFRACTION,CRYO-EM')"),
    germlines: Optional[str] = Query(None, description="Comma-separated germlines (e.g., 'IGHV3,IGHV1')"),
    has_affinity: Optional[bool] = Query(None, description="True=with affinity data only"),
    include_scfv: bool = Query(False, description="Include scFv structures (default: VHH only)"),
    sort_by: str = Query("resolution", description="Sort by: 'resolution', 'cdr_h3_length', 'pdb_code', 'date'"),
    sort_desc: bool = Query(False, description="Sort descending"),
    limit: int = Query(50, ge=1, le=500, description="Maximum results"),
    offset: int = Query(0, ge=0, description="Pagination offset")
):
    """
    Search local SAbDab VHH database.
    
    Uses offline SQLite mirror for fast, reliable searches.
    No rate limiting - queries complete in milliseconds.
    """
    try:
        db = get_sabdab_db()
        
        # Parse comma-separated lists
        methods_list = [m.strip() for m in methods.split(",")] if methods else None
        germlines_list = [g.strip() for g in germlines.split(",")] if germlines else None
        
        # Execute search
        entries = db.search(
            species=species,
            resolution_min=resolution_min,
            resolution_max=resolution_max,
            cdr_h3_min=cdr_h3_min,
            cdr_h3_max=cdr_h3_max,
            antigen_type=antigen_type,
            has_antigen=has_antigen,
            methods=methods_list,
            germlines=germlines_list,
            has_affinity=has_affinity,
            include_scfv=include_scfv,
            sort_by=sort_by,
            sort_desc=sort_desc,
            limit=limit,
            offset=offset
        )
        
        # Get total count for pagination
        total = db.count(
            species=species,
            resolution_min=resolution_min,
            resolution_max=resolution_max,
            cdr_h3_min=cdr_h3_min,
            cdr_h3_max=cdr_h3_max,
            antigen_type=antigen_type,
            has_antigen=has_antigen,
            methods=methods_list,
            germlines=germlines_list,
            has_affinity=has_affinity,
            include_scfv=include_scfv
        )
        
        results = [
            SAbDabSearchResult(
                pdb_code=e.pdb_code.upper(),
                h_chain=e.h_chain,
                model=e.model,
                resolution=e.resolution,
                method=e.method,
                species=e.heavy_species,
                germline=e.heavy_subclass,
                cdr_h3_length=e.cdr_h3_length,
                cdr_h3_sequence=e.cdr_h3_sequence,
                antigen_type=e.antigen_type,
                antigen_name=e.antigen_name,
                affinity=e.affinity,
                date=e.date,
                engineered=e.engineered,
                has_antigen=e.antigen_chain is not None
            )
            for e in entries
        ]
        
        return SearchResponse(
            results=results,
            total=total,
            limit=limit,
            offset=offset
        )
    except Exception as e:
        logger.error(f"[SAbDab Search] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sabdab/stats", response_model=DatabaseStatsResponse)
async def get_database_stats():
    """Get local database statistics."""
    try:
        db = get_sabdab_db()
        stats = db.get_stats()
        return DatabaseStatsResponse(**stats)
    except Exception as e:
        logger.error(f"[SAbDab Stats] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sabdab/filters", response_model=FilterOptionsResponse)
async def get_filter_options():
    """Get available filter options for UI dropdowns."""
    try:
        db = get_sabdab_db()
        options = db.get_filter_options()
        return FilterOptionsResponse(**options)
    except Exception as e:
        logger.error(f"[SAbDab Filters] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Download Endpoints (Remote SAbDab)
# ============================================================================

@router.get("/sabdab/{pdb_code}/download", response_model=FrameworkDownloadResponse)
async def download_framework(
    pdb_code: str,
    scheme: str = Query("imgt", description="Numbering scheme: 'imgt', 'chothia', or 'original'"),
    include_content: bool = Query(False, description="Include PDB content in response"),
    convert_hlt: bool = Query(False, description="Convert to HLT format")
):
    """
    Download a framework PDB from SAbDab.
    
    Files are cached locally for future use. Use scheme='imgt' for
    IMGT-numbered files (recommended for RFantibody compatibility).
    """
    if scheme not in ("imgt", "chothia", "original"):
        raise HTTPException(status_code=400, detail="Invalid scheme. Use 'imgt', 'chothia', or 'original'")
    
    try:
        pdb_content = await download_pdb(pdb_code, scheme=scheme, cache=True)
        
        if not pdb_content:
            raise HTTPException(status_code=404, detail=f"PDB {pdb_code} not found in SAbDab")
        
        # Convert to HLT if requested
        if convert_hlt:
            pdb_content = convert_sabdab_to_hlt(pdb_content)
        
        cache_file = CACHE_DIR / f"{pdb_code.lower()}_{scheme}.pdb"
        
        return FrameworkDownloadResponse(
            pdb_code=pdb_code.upper(),
            scheme=scheme,
            cached=cache_file.exists(),
            file_path=str(cache_file) if cache_file.exists() else None,
            pdb_content=pdb_content if include_content else None
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SAbDab Download] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sabdab/{pdb_code}/summary")
async def get_framework_summary(pdb_code: str):
    """
    Get metadata summary for a specific structure.
    
    Tries local database first, falls back to remote API.
    """
    try:
        # Try local database first
        db = get_sabdab_db()
        entries = db.get_by_pdb(pdb_code)
        
        if entries:
            # Return first entry as dict
            return entries[0].to_dict()
        
        # Fall back to remote API
        summary = await get_structure_summary(pdb_code)
        if not summary:
            raise HTTPException(status_code=404, detail=f"Summary not found for {pdb_code}")
        return summary
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SAbDab Summary] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Local Library Management
# ============================================================================

@router.get("/library", response_model=FrameworkLibraryResponse)
async def list_cached_frameworks():
    """List all locally cached framework PDBs."""
    try:
        if not CACHE_DIR.exists():
            return FrameworkLibraryResponse(
                frameworks=[],
                total=0,
                cache_dir=str(CACHE_DIR)
            )
        
        frameworks = []
        for pdb_file in CACHE_DIR.glob("*.pdb"):
            # Parse filename: {pdb_code}_{scheme}.pdb
            parts = pdb_file.stem.split("_")
            if len(parts) >= 2:
                pdb_code = parts[0].upper()
                scheme = parts[1]
            else:
                pdb_code = parts[0].upper()
                scheme = "unknown"
            
            frameworks.append(CachedFramework(
                pdb_code=pdb_code,
                scheme=scheme,
                file_path=str(pdb_file),
                size_bytes=pdb_file.stat().st_size
            ))
        
        return FrameworkLibraryResponse(
            frameworks=frameworks,
            total=len(frameworks),
            cache_dir=str(CACHE_DIR)
        )
    except Exception as e:
        logger.error(f"[Framework Library] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/library/{pdb_code}")
async def remove_cached_framework(
    pdb_code: str,
    scheme: Optional[str] = Query(None, description="Specific scheme to remove, or all if None")
):
    """Remove a framework from the local cache."""
    try:
        if not CACHE_DIR.exists():
            raise HTTPException(status_code=404, detail="Cache directory does not exist")
        
        removed = []
        pattern = f"{pdb_code.lower()}_{scheme}.pdb" if scheme else f"{pdb_code.lower()}_*.pdb"
        
        for pdb_file in CACHE_DIR.glob(pattern):
            pdb_file.unlink()
            removed.append(str(pdb_file))
        
        if not removed:
            raise HTTPException(status_code=404, detail=f"No cached files found for {pdb_code}")
        
        return {"removed": removed, "count": len(removed)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Framework Library] Remove error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Attribution (CC-BY 4.0 compliance)
# ============================================================================

@router.get("/attribution")
async def get_sabdab_attribution():
    """Get SAbDab attribution information for CC-BY 4.0 compliance."""
    return {
        "source": "SAbDab: The Structural Antibody Database",
        "citation": "Schneider, C. et al. (2022) Nucleic Acids Res. 50(D1):D1368-D1372",
        "license": "CC-BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "website": "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabdab/",
        "local_mirror": "Offline-capable SQLite mirror with pre-computed CDR annotations"
    }
