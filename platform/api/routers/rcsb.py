"""
RCSB PDB Fetcher - Download, cache, and search PDB structures from RCSB.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, List
import httpx
import logging
import json

logger = logging.getLogger(__name__)

from paths import get_code_root, to_allowed_relative

router = APIRouter()

# Cache directory for RCSB downloads
RCSB_CACHE_DIR = get_code_root() / "rcsb"
RCSB_CACHE_DIR.mkdir(exist_ok=True)

RCSB_BASE_URL = "https://files.rcsb.org/download"
RCSB_SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"


# ============================================================================
# SEARCH ROUTE - MUST BE BEFORE /{pdb_id} TO AVOID ROUTE CONFLICT
# ============================================================================

# Response models for search
class SearchResult(BaseModel):
    pdb_id: str
    title: str
    resolution: Optional[float] = None
    organism: Optional[str] = None
    method: Optional[str] = None
    release_date: Optional[str] = None


class SearchResponse(BaseModel):
    results: List[SearchResult]
    total_count: int
    query: str


@router.get("/search")
async def search_rcsb(
    q: str = Query(..., description="Search query (keywords, organism, etc.)"),
    max_results: int = Query(20, ge=1, le=100, description="Maximum results to return")
):
    """
    Search RCSB for structures by keywords.
    
    Examples:
    - "terminal deoxynucleotidyl transferase"
    - "myosin motor domain"
    - "DNA polymerase human"
    
    Returns matching PDB entries with metadata.
    """
    if len(q) < 2:
        raise HTTPException(status_code=400, detail="Query must be at least 2 characters")
    
    # Build RCSB search query
    # Using full-text search across multiple fields
    search_query = {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {
                "value": q
            }
        },
        "request_options": {
            "paginate": {
                "start": 0,
                "rows": max_results
            },
            "results_content_type": ["experimental"],
            "sort": [
                {
                    "sort_by": "score",
                    "direction": "desc"
                }
            ]
        },
        "return_type": "entry"
    }
    
    logger.info(f"[RCSB] Searching for: {q}")
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                RCSB_SEARCH_URL,
                json=search_query,
                headers={"Content-Type": "application/json"}
            )
            
            if response.status_code != 200:
                logger.warning(f"[RCSB] Search failed: {response.status_code} - {response.text[:200]}")
                return SearchResponse(results=[], total_count=0, query=q)
            
            data = response.json()
            total_count = data.get("total_count", 0)
            result_set = data.get("result_set", [])
            
            # Extract PDB IDs
            pdb_ids = [r.get("identifier", "").upper() for r in result_set]
            
            if not pdb_ids:
                return SearchResponse(results=[], total_count=0, query=q)
            
            # Fetch metadata for each PDB
            results = []
            for pdb_id in pdb_ids[:max_results]:
                try:
                    metadata = await _fetch_pdb_metadata(client, pdb_id)
                    if metadata:
                        results.append(metadata)
                except Exception as e:
                    logger.warning(f"[RCSB] Failed to fetch metadata for {pdb_id}: {e}")
                    # Add basic result anyway
                    results.append(SearchResult(pdb_id=pdb_id, title=pdb_id))
            
            return SearchResponse(
                results=results,
                total_count=total_count,
                query=q
            )
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="RCSB search timed out")
    except Exception as e:
        logger.error(f"[RCSB] Search error: {e}")
        raise HTTPException(status_code=502, detail=f"RCSB search failed: {str(e)}")


# ============================================================================
# LIST CACHED - ALSO BEFORE /{pdb_id}
# ============================================================================

@router.get("")
async def list_cached():
    """List all cached RCSB PDB files."""
    cached = []
    
    for pdb_file in RCSB_CACHE_DIR.glob("*.pdb"):
        pdb_id = pdb_file.stem.upper()
        cached.append({
            "pdb_id": pdb_id,
            "path": to_allowed_relative(pdb_file),
            "url": f"/api/rcsb/{pdb_id}/file",
            "size_bytes": pdb_file.stat().st_size
        })
    
    return {
        "cached": cached,
        "count": len(cached),
        "cache_dir": str(RCSB_CACHE_DIR)
    }


# ============================================================================
# PARAMETERIZED ROUTES - AFTER STATIC ROUTES
# ============================================================================

@router.get("/{pdb_id}/file")
async def serve_cached_pdb(pdb_id: str):
    """Serve a cached PDB file for Mol* viewer."""
    pdb_id = pdb_id.upper().strip()
    cache_path = RCSB_CACHE_DIR / f"{pdb_id.lower()}.pdb"
    
    if not cache_path.exists():
        raise HTTPException(status_code=404, detail=f"PDB {pdb_id} not cached. Fetch it first.")
    
    return FileResponse(
        path=cache_path,
        media_type="chemical/x-pdb",
        filename=f"{pdb_id}.pdb"
    )


@router.get("/{pdb_id}")
async def fetch_pdb(pdb_id: str, force: bool = False):
    """
    Fetch a PDB structure from RCSB and cache it locally.
    
    Args:
        pdb_id: 4-character PDB ID (e.g., '1KEJ', '4I27')
        force: If True, re-download even if cached
        
    Returns:
        Structure metadata and local file path
    """
    pdb_id = pdb_id.upper().strip()
    
    if len(pdb_id) != 4:
        raise HTTPException(status_code=400, detail="PDB ID must be 4 characters")
    
    cache_path = RCSB_CACHE_DIR / f"{pdb_id.lower()}.pdb"
    
    # Check cache
    if cache_path.exists() and not force:
        logger.info(f"[RCSB] Using cached PDB: {pdb_id}")
        return {
            "pdb_id": pdb_id,
            "cached": True,
            "path": to_allowed_relative(cache_path),
            "url": f"/api/rcsb/{pdb_id}/file",
            "size_bytes": cache_path.stat().st_size
        }
    
    # Download from RCSB
    url = f"{RCSB_BASE_URL}/{pdb_id.upper()}.pdb"
    logger.info(f"[RCSB] Downloading PDB from: {url}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail=f"PDB {pdb_id} not found on RCSB")
            
            response.raise_for_status()
            
            # Save to cache
            cache_path.write_bytes(response.content)
            logger.info(f"[RCSB] Cached PDB {pdb_id} to {cache_path}")
            
            return {
                "pdb_id": pdb_id,
                "cached": False,
                "path": to_allowed_relative(cache_path),
                "url": f"/api/rcsb/{pdb_id}/file",
                "size_bytes": len(response.content)
            }
            
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"RCSB fetch failed: {e}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="RCSB fetch timed out")


@router.delete("/{pdb_id}")
async def delete_cached(pdb_id: str):
    """Remove a cached PDB file."""
    pdb_id = pdb_id.upper().strip()
    cache_path = RCSB_CACHE_DIR / f"{pdb_id.lower()}.pdb"
    
    if not cache_path.exists():
        raise HTTPException(status_code=404, detail=f"PDB {pdb_id} not in cache")
    
    cache_path.unlink()
    logger.info(f"[RCSB] Deleted cached PDB: {pdb_id}")
    
    return {"deleted": pdb_id, "success": True}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

async def _fetch_pdb_metadata(client: httpx.AsyncClient, pdb_id: str) -> Optional[SearchResult]:
    """Fetch metadata for a single PDB entry from RCSB GraphQL API."""
    graphql_url = "https://data.rcsb.org/graphql"
    
    query = """
    query ($id: String!) {
        entry(entry_id: $id) {
            struct { title }
            exptl { method }
            rcsb_entry_info {
                resolution_combined
            }
            rcsb_accession_info {
                initial_release_date
            }
            polymer_entities {
                rcsb_entity_source_organism {
                    scientific_name
                }
            }
        }
    }
    """
    
    try:
        response = await client.post(
            graphql_url,
            json={"query": query, "variables": {"id": pdb_id}},
            headers={"Content-Type": "application/json"},
            timeout=5.0
        )
        
        if response.status_code != 200:
            return None
            
        data = response.json()
        entry = data.get("data", {}).get("entry", {})
        
        if not entry:
            return None
        
        # Extract organism from first polymer entity
        organism = None
        polymer_entities = entry.get("polymer_entities", [])
        if polymer_entities:
            sources = polymer_entities[0].get("rcsb_entity_source_organism", [])
            if sources:
                organism = sources[0].get("scientific_name")
        
        # Extract resolution
        resolution = None
        res_info = entry.get("rcsb_entry_info", {})
        if res_info:
            res_combined = res_info.get("resolution_combined")
            if res_combined and len(res_combined) > 0:
                resolution = res_combined[0]
        
        # Extract method
        method = None
        exptl = entry.get("exptl", [])
        if exptl:
            method = exptl[0].get("method")
        
        return SearchResult(
            pdb_id=pdb_id,
            title=entry.get("struct", {}).get("title", pdb_id),
            resolution=resolution,
            organism=organism,
            method=method,
            release_date=entry.get("rcsb_accession_info", {}).get("initial_release_date")
        )
        
    except Exception as e:
        logger.debug(f"[RCSB] Metadata fetch failed for {pdb_id}: {e}")
        return None
