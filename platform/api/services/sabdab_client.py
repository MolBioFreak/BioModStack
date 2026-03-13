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
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from functools import wraps
import json
import logging
from datetime import datetime, timezone

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


def _isoformat_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _cache_metadata_path(cache_file: Path) -> Path:
    return cache_file.with_suffix(f"{cache_file.suffix}.meta.json")


def get_cache_timestamps(cache_file: Path) -> tuple[str, Optional[str]]:
    stat = cache_file.stat()
    cached_at = _isoformat_timestamp(stat.st_mtime)
    last_used_at: Optional[str] = None
    metadata_path = _cache_metadata_path(cache_file)
    if metadata_path.exists():
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            cached_at = payload.get("cached_at") or cached_at
            last_used_at = payload.get("last_used_at") or None
        except Exception as exc:
            logger.warning(f"[SAbDab] Failed to read cache metadata for {cache_file}: {exc}")
    return cached_at, last_used_at


def set_cache_timestamps(cache_file: Path, *, cached_at: str, last_used_at: Optional[str]) -> None:
    metadata_path = _cache_metadata_path(cache_file)
    metadata_path.write_text(
        json.dumps(
            {
                "cached_at": cached_at,
                "last_used_at": last_used_at,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _touch_cache_file(cache_file: Path) -> None:
    try:
        cached_at, _ = get_cache_timestamps(cache_file)
        set_cache_timestamps(
            cache_file,
            cached_at=cached_at,
            last_used_at=_isoformat_timestamp(time.time()),
        )
    except Exception as exc:
        logger.warning(f"[SAbDab] Failed to update last-used timestamp for {cache_file}: {exc}")


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
            _touch_cache_file(cache_file)
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
            now_iso = _isoformat_timestamp(time.time())
            set_cache_timestamps(cache_file, cached_at=now_iso, last_used_at=now_iso)
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
    heavy_chain: Optional[str] = None,
    light_chain: Optional[str] = None,
    antigen_chain: Optional[str] = None,
    target_chain: str = "T"
) -> str:
    """
    Convert SAbDab IMGT-numbered PDB to HLT format.
    
    SAbDab IMGT files already have proper numbering, but RFantibody expects:
    1. Antibody chains relabeled to H/L
    2. Optional antigen chain relabeled to T
    3. Residues renumbered sequentially across the retained chains
    4. HLT REMARK loop labels using the renumbered residue indices
    
    Args:
        pdb_content: IMGT-numbered PDB from SAbDab
        heavy_chain: Original heavy/VHH chain ID in the SAbDab structure
        light_chain: Original light chain ID in the SAbDab structure
        antigen_chain: Original antigen chain ID in the SAbDab structure
        target_chain: Chain ID to use as target (T)
    
    Returns:
        HLT-formatted PDB content
    """
    def _norm_chain(chain_id: Optional[str]) -> Optional[str]:
        if not chain_id:
            return None
        chain_id = chain_id.strip()
        return chain_id[:1] if chain_id else None

    def _residue_number(line: str) -> Optional[int]:
        token = line[22:27].strip()
        digits = ''.join(ch for ch in token if ch.isdigit() or ch == '-')
        if not digits:
            return None
        return int(digits)

    heavy_chain = _norm_chain(heavy_chain)
    light_chain = _norm_chain(light_chain)
    antigen_chain = _norm_chain(antigen_chain)
    target_chain = _norm_chain(target_chain) or "T"

    chain_map: Dict[str, str] = {}
    chain_order: List[str] = []

    for original, renamed in (
        (heavy_chain, "H"),
        (light_chain, "L"),
        (antigen_chain, target_chain),
    ):
        if original and original not in chain_map:
            chain_map[original] = renamed
            chain_order.append(original)

    if not chain_map:
        # Conservative fallback: treat the first chain in the file as the heavy chain.
        for line in pdb_content.splitlines():
            if line.startswith(("ATOM", "HETATM")):
                inferred_chain = _norm_chain(line[21:22])
                if inferred_chain:
                    chain_map[inferred_chain] = "H"
                    chain_order.append(inferred_chain)
                    break

    cdr_ranges = {
        "H": {"H1": range(27, 39), "H2": range(56, 66), "H3": range(105, 118)},
        "L": {"L1": range(27, 39), "L2": range(56, 66), "L3": range(105, 118)},
    }

    chain_lines: Dict[str, List[str]] = {chain_id: [] for chain_id in chain_order}
    for line in pdb_content.splitlines():
        record = line[:6].strip()
        if record in {"ATOM", "HETATM"}:
            source_chain = _norm_chain(line[21:22])
            if source_chain not in chain_map:
                continue
            chain_lines.setdefault(source_chain, []).append(line)

    next_residue_index = 1
    residue_map: Dict[tuple[str, str, str], tuple[str, int]] = {}
    cdr_positions: Dict[str, List[int]] = {name: [] for group in cdr_ranges.values() for name in group.keys()}
    output_lines: List[str] = []

    for source_chain in chain_order:
        for line in chain_lines.get(source_chain, []):
            resseq = line[22:26]
            icode = line[26:27]
            residue_key = (source_chain or "", resseq, icode)

            if residue_key not in residue_map:
                residue_map[residue_key] = (chain_map[source_chain], next_residue_index)
                renamed_chain = chain_map[source_chain]
                original_resnum = _residue_number(line)
                if original_resnum is not None and renamed_chain in cdr_ranges:
                    for cdr_name, residue_range in cdr_ranges[renamed_chain].items():
                        if original_resnum in residue_range:
                            cdr_positions[cdr_name].append(next_residue_index)
                            break
                next_residue_index += 1

            renamed_chain, new_resnum = residue_map[residue_key]
            rebuilt = f"{line[:21]}{renamed_chain}{new_resnum:4d} {line[27:]}"
            output_lines.append(rebuilt)

        if chain_lines.get(source_chain):
            output_lines.append("TER")

    for cdr_name in ("H1", "H2", "H3", "L1", "L2", "L3"):
        for pos in sorted(set(cdr_positions.get(cdr_name, []))):
            output_lines.append(f"REMARK PDBinfo-LABEL: {pos:4d} {cdr_name}")

    if output_lines and output_lines[-1].strip() != "END":
        output_lines.append("END")

    return "\n".join(output_lines) + "\n"


# Sync wrappers for non-async contexts
def search_nanobodies_sync(**kwargs) -> List[SAbDabEntry]:
    """Synchronous wrapper for search_nanobodies."""
    return asyncio.run(search_nanobodies(**kwargs))


def download_pdb_sync(pdb_code: str, scheme: str = "imgt", cache: bool = True) -> Optional[str]:
    """Synchronous wrapper for download_pdb."""
    return asyncio.run(download_pdb(pdb_code, scheme, cache))
