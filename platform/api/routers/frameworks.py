"""
Frameworks API Router - SAbDab integration and framework library management.

Provides endpoints for:
- Searching SAbDab/NanoSAbDab for VHH frameworks
- Downloading and caching framework PDBs
- Managing local framework library
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
from pathlib import Path
import logging

from services.sabdab_client import (
    search_nanobodies,
    download_pdb,
    get_structure_summary,
    convert_sabdab_to_hlt,
    SAbDabEntry,
    CACHE_DIR
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/frameworks", tags=["frameworks"])


# Response models
class SAbDabSearchResult(BaseModel):
    pdb_code: str
    h_chain: str
    l_chain: Optional[str]
    resolution: Optional[float]
    method: str
    species: Optional[str]
    cdr_h3_length: Optional[int]
    antigen_type: Optional[str]


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


# Endpoints
@router.get("/sabdab/search", response_model=List[SAbDabSearchResult])
async def search_sabdab_frameworks(
    species: Optional[str] = Query(None, description="Filter by species (e.g., 'camel', 'llama')"),
    resolution_max: float = Query(2.5, description="Maximum resolution in Angstroms"),
    cdr_h3_min: Optional[int] = Query(None, description="Minimum CDR-H3 length"),
    cdr_h3_max: Optional[int] = Query(None, description="Maximum CDR-H3 length"),
    antigen_type: Optional[str] = Query(None, description="Antigen type filter"),
    limit: int = Query(50, ge=1, le=500, description="Maximum results")
):
    """
    Search NanoSAbDab for VHH nanobody structures.
    
    Rate-limited to 1 request every 2 seconds to respect SAbDab servers.
    Results are filtered by resolution and CDR-H3 length.
    """
    try:
        entries = await search_nanobodies(
            species=species,
            resolution_max=resolution_max,
            cdr_h3_min=cdr_h3_min,
            cdr_h3_max=cdr_h3_max,
            antigen_type=antigen_type,
            limit=limit
        )
        
        return [
            SAbDabSearchResult(
                pdb_code=e.pdb_code,
                h_chain=e.h_chain,
                l_chain=e.l_chain,
                resolution=e.resolution,
                method=e.method,
                species=e.species,
                cdr_h3_length=e.cdr_h3_length,
                antigen_type=e.antigen_type
            )
            for e in entries
        ]
    except Exception as e:
        logger.error(f"[SAbDab Search] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    """Get metadata summary for a specific structure."""
    try:
        summary = await get_structure_summary(pdb_code)
        if not summary:
            raise HTTPException(status_code=404, detail=f"Summary not found for {pdb_code}")
        return summary
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[SAbDab Summary] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


# Attribution endpoint (CC-BY 4.0 compliance)
@router.get("/attribution")
async def get_sabdab_attribution():
    """Get SAbDab attribution information for CC-BY 4.0 compliance."""
    return {
        "source": "SAbDab: The Structural Antibody Database",
        "citation": "Schneider, C. et al. (2022) Nucleic Acids Res. 50(D1):D1368-D1372",
        "license": "CC-BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "website": "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/sabdab/"
    }
