"""
Ribocentre Aptamer Database - Search and fetch aptamer sequences from Ribocentre-aptamer.

https://aptamer.ribocentre.org/
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

RIBOCENTRE_BASE_URL = "https://aptamer.ribocentre.org"

# Curated preset aptamers for common use cases
APTAMER_PRESETS = [
    {
        "id": "thrombin-HD1",
        "name": "Thrombin HD1",
        "sequence": "GGTTGGTGTGGTTGG",
        "target": "Thrombin",
        "kd_nm": 25.0,
        "type": "DNA",
        "description": "Anti-thrombin G-quadruplex aptamer (Bock et al., 1992)"
    },
    {
        "id": "theophylline",
        "name": "Theophylline (TCT8-4)",
        "sequence": "GGCGAUACCAGCCGAAAGGCCCUUGGCAGCGUC",
        "target": "Theophylline",
        "kd_um": 0.1,
        "type": "RNA",
        "description": "Small molecule binding aptamer (Jenison et al., 1994)"
    },
    {
        "id": "vegf-peg",
        "name": "Pegaptanib (Macugen)",
        "sequence": "CGGAAUCAGUGAAUGCUUAUACAUCCG",
        "target": "VEGF165",
        "kd_pm": 50.0,
        "type": "RNA",
        "description": "FDA-approved anti-VEGF aptamer for wet AMD"
    },
    {
        "id": "atp-aptamer",
        "name": "ATP Aptamer",
        "sequence": "ACCTGGGGGAGTATTGCGGAGGAAGGT",
        "target": "ATP",
        "kd_um": 6.0,
        "type": "DNA",
        "description": "Huizenga & Szostak ATP-binding aptamer"
    },
    {
        "id": "malachite-green",
        "name": "Malachite Green",
        "sequence": "GGAUCCCGACUGGCGAGAGCCAGGUAACGAAUGG",
        "target": "Malachite Green",
        "kd_nm": 800.0,
        "type": "RNA",
        "description": "Fluorogenic aptamer for imaging"
    },
    {
        "id": "spinach",
        "name": "Spinach2",
        "sequence": "GAUGUAGCUGCACCCUGUCAGUUUGUGCCGGCUGCUGACAUC",
        "target": "DFHBI",
        "kd_nm": 360.0,
        "type": "RNA",
        "description": "GFP-like fluorescent RNA aptamer"
    },
    {
        "id": "pdgf-bb",
        "name": "PDGF-BB Aptamer",
        "sequence": "CAGGCUACGGCACGUAGAGCAUCACCAUGAUCCUGUG",
        "target": "PDGF-BB",
        "kd_pm": 100.0,
        "type": "DNA",
        "description": "Platelet-derived growth factor aptamer"
    },
    {
        "id": "broccoli",
        "name": "Broccoli",
        "sequence": "GAGACGGUUGGUGAGUAGGCUCA",
        "target": "DFHBI-1T",
        "kd_nm": 360.0,
        "type": "RNA",
        "description": "Improved fluorescent RNA aptamer"
    }
]


class AptamerResult(BaseModel):
    id: str
    name: str
    sequence: str
    target: Optional[str] = None
    kd_value: Optional[float] = None
    kd_unit: Optional[str] = None
    aptamer_type: str  # DNA or RNA
    description: Optional[str] = None
    source: str = "ribocentre"  # or "preset"
    structure: Optional[str] = None  # Secondary structure if available
    length: Optional[int] = None


class AptamerSearchResponse(BaseModel):
    results: List[AptamerResult]
    total_count: int
    query: str
    source: str


@router.get("/presets")
async def list_presets():
    """List curated aptamer presets for common targets."""
    results = []
    for apt in APTAMER_PRESETS:
        kd_val = apt.get("kd_nm") or apt.get("kd_um") or apt.get("kd_pm")
        kd_unit = "nM" if "kd_nm" in apt else ("µM" if "kd_um" in apt else "pM")
        
        results.append(AptamerResult(
            id=apt["id"],
            name=apt["name"],
            sequence=apt["sequence"],
            target=apt.get("target"),
            kd_value=kd_val,
            kd_unit=kd_unit,
            aptamer_type=apt["type"],
            description=apt.get("description"),
            source="preset",
            length=len(apt["sequence"])
        ))
    
    return {
        "presets": results,
        "count": len(results)
    }


@router.get("/search")
async def search_aptamers(
    q: str = Query(..., description="Search query (target name, aptamer name, etc.)"),
    aptamer_type: Optional[str] = Query(None, description="Filter by type: DNA or RNA"),
    max_results: int = Query(30, ge=1, le=100)
):
    """
    Search for aptamers by target or name.
    
    First searches local presets, then Ribocentre if available.
    """
    q_lower = q.lower()
    results: List[AptamerResult] = []
    
    # Search presets first
    for apt in APTAMER_PRESETS:
        name_match = q_lower in apt["name"].lower()
        target_match = apt.get("target") and q_lower in apt["target"].lower()
        seq_match = q_lower.upper() in apt["sequence"]
        desc_match = apt.get("description") and q_lower in apt["description"].lower()
        
        type_filter = not aptamer_type or apt["type"].upper() == aptamer_type.upper()
        
        if type_filter and (name_match or target_match or seq_match or desc_match):
            kd_val = apt.get("kd_nm") or apt.get("kd_um") or apt.get("kd_pm")
            kd_unit = "nM" if "kd_nm" in apt else ("µM" if "kd_um" in apt else "pM")
            
            results.append(AptamerResult(
                id=apt["id"],
                name=apt["name"],
                sequence=apt["sequence"],
                target=apt.get("target"),
                kd_value=kd_val,
                kd_unit=kd_unit,
                aptamer_type=apt["type"],
                description=apt.get("description"),
                source="preset",
                length=len(apt["sequence"])
            ))
    
    # Try Ribocentre API (if accessible)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Ribocentre has a simple search endpoint
            response = await client.get(
                f"{RIBOCENTRE_BASE_URL}/api/aptamer/search",
                params={"q": q, "limit": max_results}
            )
            
            if response.status_code == 200:
                data = response.json()
                for item in data.get("results", []):
                    # Avoid duplicates
                    if not any(r.id == item.get("id") for r in results):
                        results.append(AptamerResult(
                            id=item.get("id", f"ribo_{len(results)}"),
                            name=item.get("name", "Unknown"),
                            sequence=item.get("sequence", ""),
                            target=item.get("target"),
                            kd_value=item.get("kd"),
                            kd_unit=item.get("kd_unit", "nM"),
                            aptamer_type=item.get("type", "RNA"),
                            description=item.get("description"),
                            source="ribocentre",
                            structure=item.get("structure"),
                            length=len(item.get("sequence", ""))
                        ))
    except Exception as e:
        logger.debug(f"[Ribocentre] API search failed (using presets only): {e}")
    
    # Apply type filter to all results
    if aptamer_type:
        results = [r for r in results if r.aptamer_type.upper() == aptamer_type.upper()]
    
    return AptamerSearchResponse(
        results=results[:max_results],
        total_count=len(results),
        query=q,
        source="preset+ribocentre"
    )


@router.get("/{aptamer_id}")
async def get_aptamer(aptamer_id: str):
    """Get details for a specific aptamer by ID."""
    # Check presets first
    for apt in APTAMER_PRESETS:
        if apt["id"] == aptamer_id:
            kd_val = apt.get("kd_nm") or apt.get("kd_um") or apt.get("kd_pm")
            kd_unit = "nM" if "kd_nm" in apt else ("µM" if "kd_um" in apt else "pM")
            
            return AptamerResult(
                id=apt["id"],
                name=apt["name"],
                sequence=apt["sequence"],
                target=apt.get("target"),
                kd_value=kd_val,
                kd_unit=kd_unit,
                aptamer_type=apt["type"],
                description=apt.get("description"),
                source="preset",
                length=len(apt["sequence"])
            )
    
    # Try Ribocentre
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{RIBOCENTRE_BASE_URL}/api/aptamer/{aptamer_id}")
            
            if response.status_code == 200:
                item = response.json()
                return AptamerResult(
                    id=item.get("id", aptamer_id),
                    name=item.get("name", "Unknown"),
                    sequence=item.get("sequence", ""),
                    target=item.get("target"),
                    kd_value=item.get("kd"),
                    kd_unit=item.get("kd_unit", "nM"),
                    aptamer_type=item.get("type", "RNA"),
                    description=item.get("description"),
                    source="ribocentre",
                    structure=item.get("structure"),
                    length=len(item.get("sequence", ""))
                )
    except Exception as e:
        logger.debug(f"[Ribocentre] Aptamer fetch failed: {e}")
    
    raise HTTPException(status_code=404, detail=f"Aptamer {aptamer_id} not found")
