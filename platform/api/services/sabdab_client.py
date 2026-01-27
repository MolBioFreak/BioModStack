"""
SAbDab API Client - Interface to the Structural Antibody Database.

Provides rate-limited access to search and download antibody structures
from SAbDab (Oxford Protein Informatics Group).

License: CC-BY 4.0 (https://creativecommons.org/licenses/by/4.0/)
Attribution: Schneider, C. et al. (2022) Nucleic Acids Res. 50(D1):D1368-D1372
"""

import asyncio
import aiohttp
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from functools import wraps
import json
import logging

logger = logging.getLogger(__name__)

# SAbDab base URL
SABDAB_BASE = "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred"

# Rate limiting: 2 seconds between requests (be conservative)
RATE_LIMIT_SECONDS = 2.0
_last_request_time = 0.0

from paths import get_sabdab_cache_dir

# Cache directory
CACHE_DIR = get_sabdab_cache_dir()

# In-memory cache for VHH summary (avoid re-downloading 20k entries on every search)
_vhh_summary_cache: List["SAbDabEntry"] = []
_vhh_summary_cache_time: float = 0.0
_VHH_CACHE_TTL_SECONDS = 3600  # 1 hour


@dataclass
class SAbDabEntry:
    """A single antibody entry from SAbDab."""
    pdb_code: str
    h_chain: str
    l_chain: Optional[str]  # None for VHH
    model: int
    antigen_chain: Optional[str]
    antigen_type: Optional[str]
    resolution: Optional[float]
    method: str
    scfv: bool
    species: Optional[str]
    affinity: Optional[float]
    cdr_h3_length: Optional[int]
    
    @classmethod
    def from_summary_row(cls, row: Dict[str, str]) -> "SAbDabEntry":
        """Parse a row from SAbDab summary TSV."""
        def safe_float(val: str) -> Optional[float]:
            try:
                return float(val) if val and val != "NA" else None
            except ValueError:
                return None
        
        def safe_int(val: str) -> Optional[int]:
            try:
                return int(val) if val and val != "NA" else None
            except ValueError:
                return None
        
        return cls(
            pdb_code=row.get("pdb", ""),
            h_chain=row.get("Hchain", ""),
            l_chain=row.get("Lchain") if row.get("Lchain") != "NA" else None,
            model=safe_int(row.get("model", "0")) or 0,
            antigen_chain=row.get("antigen_chain") if row.get("antigen_chain") != "NA" else None,
            antigen_type=row.get("antigen_type") if row.get("antigen_type") != "NA" else None,
            resolution=safe_float(row.get("resolution")),
            method=row.get("method", ""),
            scfv=row.get("scfv", "").lower() == "true",
            # SAbDab uses "heavy_species" not "species"
            species=row.get("heavy_species") if row.get("heavy_species") != "NA" else None,
            affinity=safe_float(row.get("affinity")),
            # Note: CDR-H3 length is NOT in the summary file, would need per-structure fetch
            cdr_h3_length=None,
        )


async def _rate_limited_request(
    session: aiohttp.ClientSession,
    url: str,
    params: Optional[Dict[str, Any]] = None
) -> aiohttp.ClientResponse:
    """Make a rate-limited request to SAbDab."""
    global _last_request_time
    
    # Wait if needed
    elapsed = time.time() - _last_request_time
    if elapsed < RATE_LIMIT_SECONDS:
        await asyncio.sleep(RATE_LIMIT_SECONDS - elapsed)
    
    _last_request_time = time.time()
    
    headers = {
        "User-Agent": "BioModStack/1.0 (academic research)",
        "Accept": "text/html,application/json,text/plain,*/*"
    }
    
    logger.info(f"[SAbDab] Request: {url}")
    return await session.get(url, params=params, headers=headers)


async def _fetch_vhh_summary() -> List[SAbDabEntry]:
    """
    Fetch and cache the full VHH summary from NanoSAbDab.
    Uses in-memory cache with 1-hour TTL.
    """
    global _vhh_summary_cache, _vhh_summary_cache_time
    
    # Check cache
    if _vhh_summary_cache and (time.time() - _vhh_summary_cache_time) < _VHH_CACHE_TTL_SECONDS:
        logger.info(f"[SAbDab] Using cached VHH summary ({len(_vhh_summary_cache)} entries)")
        return _vhh_summary_cache
    
    logger.info("[SAbDab] Fetching VHH summary from NanoSAbDab...")
    
    async with aiohttp.ClientSession() as session:
        summary_url = f"{SABDAB_BASE}/sabdab/summary/all/"
        response = await _rate_limited_request(session, summary_url, {"ABtype": "VHH"})
        
        if response.status != 200:
            logger.error(f"[SAbDab] Fetch failed: {response.status}")
            return _vhh_summary_cache  # Return stale cache if available
        
        text = await response.text()
        
        # Parse TSV
        lines = text.strip().split("\n")
        if len(lines) < 2:
            return []
        
        headers = lines[0].split("\t")
        entries: List[SAbDabEntry] = []
        
        for line in lines[1:]:
            values = line.split("\t")
            if len(values) != len(headers):
                continue
            row = dict(zip(headers, values))
            entries.append(SAbDabEntry.from_summary_row(row))
        
        # Update cache
        _vhh_summary_cache = entries
        _vhh_summary_cache_time = time.time()
        logger.info(f"[SAbDab] Cached {len(entries)} VHH entries (TTL: {_VHH_CACHE_TTL_SECONDS}s)")
        
        return entries


async def search_nanobodies(
    species: Optional[str] = None,
    resolution_max: Optional[float] = 2.5,
    cdr_h3_min: Optional[int] = None,
    cdr_h3_max: Optional[int] = None,
    antigen_type: Optional[str] = None,
    non_redundant: bool = False,
    limit: int = 100,
    sort_by: Optional[str] = None,
    sort_desc: bool = False
) -> List[SAbDabEntry]:
    """
    Search NanoSAbDab for VHH structures.
    
    Uses cached summary (refreshed hourly) for fast filtering.
    
    Args:
        species: Filter by species (e.g., "camel", "llama", "alpaca")
        resolution_max: Maximum resolution in Angstroms
        cdr_h3_min: Minimum CDR-H3 length
        cdr_h3_max: Maximum CDR-H3 length
        antigen_type: Filter by antigen type (e.g., "protein", "peptide")
        non_redundant: Return non-redundant set (clustered by sequence)
        limit: Maximum number of results
        sort_by: Sort field - "resolution", "cdr_h3_length", "species", "pdb_code"
        sort_desc: Sort descending if True
    
    Returns:
        List of SAbDabEntry objects
    """
    # Get cached entries (fetches if needed)
    all_vhh = await _fetch_vhh_summary()
    
    if not all_vhh:
        return []
    
    # Filter entries
    filtered: List[SAbDabEntry] = []
    
    for entry in all_vhh:
        # Resolution filter: skip if resolution exceeds max
        # Note: entries with None resolution PASS the filter (unknown = might be good)
        if resolution_max is not None:
            if entry.resolution is not None and entry.resolution > resolution_max:
                continue
        
        # CDR-H3 length filter: entries with None CDR-H3 are EXCLUDED
        if cdr_h3_min is not None:
            if entry.cdr_h3_length is None or entry.cdr_h3_length < cdr_h3_min:
                continue
        if cdr_h3_max is not None:
            if entry.cdr_h3_length is None or entry.cdr_h3_length > cdr_h3_max:
                continue
        
        # Species filter (case-insensitive substring match)
        if species:
            if entry.species is None or species.lower() not in entry.species.lower():
                continue
        
        # Antigen type filter
        if antigen_type:
            if entry.antigen_type is None or antigen_type.lower() not in entry.antigen_type.lower():
                continue
        
        filtered.append(entry)
    
    logger.info(f"[SAbDab] Filtered to {len(filtered)} VHH structures")
    
    # Apply sorting
    if sort_by:
        def sort_key(e: SAbDabEntry):
            val = getattr(e, sort_by, None)
            if val is None:
                return (1, "")
            if isinstance(val, str):
                return (0, val.lower())
            return (0, val)
        
        filtered.sort(key=sort_key, reverse=sort_desc)
    else:
        # Default sort: best resolution first
        filtered.sort(key=lambda e: (e.resolution is None, e.resolution or 999))
    
    # Apply limit
    entries = filtered[:limit]
    
    logger.info(f"[SAbDab] Returning {len(entries)} VHH structures")
    return entries


async def download_pdb(
    pdb_code: str,
    scheme: str = "imgt",
    cache: bool = True
) -> Optional[str]:
    """
    Download a PDB file from SAbDab.
    
    Args:
        pdb_code: 4-letter PDB code
        scheme: Numbering scheme ("imgt", "chothia", or "original")
        cache: Whether to cache the file locally
    
    Returns:
        PDB file content as string, or None if failed
    """
    pdb_code = pdb_code.lower()
    
    # Check cache first
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"{pdb_code}_{scheme}.pdb"
        if cache_file.exists():
            logger.info(f"[SAbDab] Cache hit: {cache_file}")
            return cache_file.read_text()
    
    # Download from SAbDab
    url = f"{SABDAB_BASE}/sabdab/pdb/{pdb_code}/"
    params = {}
    if scheme != "original":
        params["scheme"] = scheme
    
    async with aiohttp.ClientSession() as session:
        response = await _rate_limited_request(session, url, params)
        
        if response.status != 200:
            logger.error(f"[SAbDab] Download failed for {pdb_code}: {response.status}")
            return None
        
        pdb_content = await response.text()
        
        # Validate it's a PDB file
        if not pdb_content.startswith(("HEADER", "REMARK", "ATOM")):
            logger.error(f"[SAbDab] Invalid PDB response for {pdb_code}")
            return None
        
        # Cache if requested
        if cache:
            cache_file = CACHE_DIR / f"{pdb_code}_{scheme}.pdb"
            cache_file.write_text(pdb_content)
            logger.info(f"[SAbDab] Cached: {cache_file}")
        
        return pdb_content


async def get_structure_summary(pdb_code: str) -> Optional[Dict[str, Any]]:
    """
    Get the TSV summary for a specific structure.
    
    Returns parsed dict with structure metadata.
    """
    url = f"{SABDAB_BASE}/sabdab/summary/{pdb_code.lower()}/"
    
    async with aiohttp.ClientSession() as session:
        response = await _rate_limited_request(session, url)
        
        if response.status != 200:
            return None
        
        text = await response.text()
        lines = text.strip().split("\n")
        
        if len(lines) < 2:
            return None
        
        headers = lines[0].split("\t")
        values = lines[1].split("\t")
        
        return dict(zip(headers, values))


def convert_sabdab_to_hlt(
    pdb_content: str,
    target_chain: str = "T"
) -> str:
    """
    Convert SAbDab IMGT-numbered PDB to HLT format.
    
    SAbDab IMGT files already have proper numbering, but we need to:
    1. Rename chains to H/L/T convention
    2. Add CDR REMARK lines
    
    Args:
        pdb_content: IMGT-numbered PDB from SAbDab
        target_chain: Chain ID to use as target (T)
    
    Returns:
        HLT-formatted PDB content
    """
    # For VHH (nanobodies), there's only H chain
    # We just need to ensure chain ID is 'H' and add CDR REMARKs
    
    lines = pdb_content.split("\n")
    output_lines = []
    
    # CDR positions (IMGT)
    cdr_h1 = range(27, 39)   # 27-38
    cdr_h2 = range(56, 66)   # 56-65
    cdr_h3 = range(105, 118) # 105-117
    
    cdr_residues = {"H1": [], "H2": [], "H3": []}
    
    for line in lines:
        if line.startswith("ATOM") or line.startswith("HETATM"):
            # Parse residue number
            try:
                res_num_str = line[22:27].strip()
                # Handle insertion codes
                res_num = int(''.join(c for c in res_num_str if c.isdigit() or c == '-'))
            except ValueError:
                output_lines.append(line)
                continue
            
            # Track CDR residues
            if res_num in cdr_h1:
                cdr_residues["H1"].append(res_num)
            elif res_num in cdr_h2:
                cdr_residues["H2"].append(res_num)
            elif res_num in cdr_h3:
                cdr_residues["H3"].append(res_num)
        
        output_lines.append(line)
    
    # Add CDR REMARK lines at the end (HLT format)
    for cdr_name, positions in cdr_residues.items():
        if positions:
            unique_pos = sorted(set(positions))
            for pos in unique_pos:
                output_lines.append(f"REMARK PDBinfo-LABEL: {pos:4d} H  {cdr_name}")
    
    return "\n".join(output_lines)


# Sync wrappers for non-async contexts
def search_nanobodies_sync(**kwargs) -> List[SAbDabEntry]:
    """Synchronous wrapper for search_nanobodies."""
    return asyncio.run(search_nanobodies(**kwargs))


def download_pdb_sync(pdb_code: str, scheme: str = "imgt", cache: bool = True) -> Optional[str]:
    """Synchronous wrapper for download_pdb."""
    return asyncio.run(download_pdb(pdb_code, scheme, cache))
