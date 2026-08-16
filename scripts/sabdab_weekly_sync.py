#!/usr/bin/env python3
"""
SAbDab Weekly Sync - Incremental update of local database.

Fetches new VHH entries that were added since last sync.
Designed to run as a weekly cron job.

Usage:
    python sabdab_weekly_sync.py
    
Cron example (every Sunday at 3 AM):
    0 3 * * 0 cd /path/to/biomodstack && python scripts/sabdab_weekly_sync.py

License: CC-BY 4.0 (https://creativecommons.org/licenses/by/4.0/)
Attribution: Schneider, C. et al. (2022) Nucleic Acids Res. 50(D1):D1368-D1372
"""

import sys
import os
import asyncio
import aiohttp
import time
import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "platform" / "api"))

from services.sabdab_db import SAbDabDatabase, VHHStructure

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / "logs" / "sabdab_sync.log", mode="a")
    ]
)
logger = logging.getLogger(__name__)

# SAbDab API configuration
SABDAB_BASE = "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred"
ABDB_BASE = "https://opig.stats.ox.ac.uk/webapps/abdb"
RATE_LIMIT_SECONDS = 2.0


async def rate_limited_get(
    session: aiohttp.ClientSession,
    url: str,
    last_request_time: float,
    params: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], float]:
    """Make a rate-limited GET request."""
    elapsed = time.time() - last_request_time
    if elapsed < RATE_LIMIT_SECONDS:
        await asyncio.sleep(RATE_LIMIT_SECONDS - elapsed)
    
    headers = {
        "User-Agent": "BioModStack/1.0 (academic research, weekly sync)",
        "Accept": "text/html,text/plain,*/*"
    }
    
    try:
        async with session.get(url, params=params, headers=headers, timeout=30) as response:
            new_time = time.time()
            if response.status == 200:
                return await response.text(), new_time
            return None, new_time
    except Exception as e:
        logger.error(f"Request error: {url} -> {e}")
        return None, time.time()


def parse_summary_tsv(text: str) -> List[Dict[str, str]]:
    """Parse SAbDab summary TSV."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return []
    
    headers = lines[0].split("\t")
    entries = []
    
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) == len(headers):
            entries.append(dict(zip(headers, values)))
    
    return entries


def compute_cdr_h3_from_pdb(pdb_content: str, h_chain: str) -> Dict[str, Any]:
    """
    Compute CDR-H3 length from IMGT-numbered PDB file.
    
    In IMGT numbering, CDR-H3 spans positions 105-117 (with possible insertions).
    """
    result = {
        "cdr_h1_length": None,
        "cdr_h2_length": None,
        "cdr_h3_length": None,
        "cdr_h3_sequence": None
    }
    
    cdr_h1_range = range(27, 39)
    cdr_h2_range = range(56, 66)
    
    h1_residues = set()
    h2_residues = set()
    h3_residues = set()
    h3_sequence = []
    
    aa_map = {
        'ALA': 'A', 'CYS': 'C', 'ASP': 'D', 'GLU': 'E', 'PHE': 'F',
        'GLY': 'G', 'HIS': 'H', 'ILE': 'I', 'LYS': 'K', 'LEU': 'L',
        'MET': 'M', 'ASN': 'N', 'PRO': 'P', 'GLN': 'Q', 'ARG': 'R',
        'SER': 'S', 'THR': 'T', 'VAL': 'V', 'TRP': 'W', 'TYR': 'Y'
    }
    
    for line in pdb_content.split('\n'):
        if not line.startswith('ATOM'):
            continue
        
        try:
            atom_name = line[12:16].strip()
            if atom_name != 'CA':
                continue
            
            res_name = line[17:20].strip()
            chain = line[21].strip()
            res_num_str = line[22:27].strip()
            
            if chain != h_chain:
                continue
            
            res_num = int(''.join(c for c in res_num_str if c.isdigit() or c == '-'))
            
            if res_num in cdr_h1_range:
                h1_residues.add(res_num_str)
            elif res_num in cdr_h2_range:
                h2_residues.add(res_num_str)
            elif 105 <= res_num <= 129:
                h3_residues.add(res_num_str)
                h3_sequence.append((res_num, res_num_str, aa_map.get(res_name, 'X')))
        except (ValueError, IndexError):
            continue
    
    if h1_residues:
        result["cdr_h1_length"] = len(h1_residues)
    if h2_residues:
        result["cdr_h2_length"] = len(h2_residues)
    if h3_residues:
        result["cdr_h3_length"] = len(h3_residues)
        h3_sequence.sort(key=lambda x: (x[0], x[1]))
        result["cdr_h3_sequence"] = ''.join(x[2] for x in h3_sequence)
    
    return result


def safe_float(val: str) -> Optional[float]:
    try:
        return float(val) if val and val not in ("NA", "None", "") else None
    except ValueError:
        return None


def safe_int(val: str) -> Optional[int]:
    try:
        return int(val) if val and val not in ("NA", "None", "") else None
    except ValueError:
        return None


def row_to_vhh_structure(row: Dict[str, str], cdr_data: Optional[Dict[str, Any]] = None) -> VHHStructure:
    """Convert summary row to VHHStructure."""
    cdr = cdr_data or {}
    
    return VHHStructure(
        pdb_code=row.get("pdb", "").lower(),
        h_chain=row.get("Hchain", ""),
        model=safe_int(row.get("model", "0")) or 0,
        resolution=safe_float(row.get("resolution")),
        method=row.get("method") if row.get("method") != "NA" else None,
        r_free=safe_float(row.get("Rfree")),
        r_factor=safe_float(row.get("Rfactor")),
        date=row.get("date") if row.get("date") != "NA" else None,
        heavy_species=row.get("heavy_species") if row.get("heavy_species") != "NA" else None,
        heavy_subclass=row.get("heavy_subclass") if row.get("heavy_subclass") != "NA" else None,
        engineered=row.get("engineered", "").lower() == "true",
        scfv=row.get("scfv", "").lower() == "true",
        antigen_chain=row.get("antigen_chain") if row.get("antigen_chain") != "NA" else None,
        antigen_type=row.get("antigen_type") if row.get("antigen_type") != "NA" else None,
        antigen_name=row.get("antigen_name") if row.get("antigen_name") != "NA" else None,
        antigen_species=row.get("antigen_species") if row.get("antigen_species") != "NA" else None,
        cdr_h1_length=cdr.get("cdr_h1_length"),
        cdr_h2_length=cdr.get("cdr_h2_length"),
        cdr_h3_length=cdr.get("cdr_h3_length"),
        cdr_h3_sequence=cdr.get("cdr_h3_sequence"),
        affinity=safe_float(row.get("affinity")),
        delta_g=safe_float(row.get("delta_g")),
        affinity_method=row.get("affinity_method") if row.get("affinity_method") != "NA" else None,
        pmid=row.get("pmid") if row.get("pmid") != "NA" else None,
        authors=row.get("authors") if row.get("authors") != "NA" else None,
    )


async def fetch_cdr_data(
    session: aiohttp.ClientSession,
    pdb_code: str,
    h_chain: str,
    last_request_time: float
) -> Tuple[Optional[Dict[str, Any]], float]:
    """Fetch CDR data by downloading IMGT-numbered PDB and computing lengths."""
    url = f"{SABDAB_BASE}/sabdab/pdb/{pdb_code}/?scheme=imgt"
    text, new_time = await rate_limited_get(session, url, last_request_time)
    
    if text and text.startswith(('HEADER', 'REMARK', 'ATOM')):
        cdr_data = compute_cdr_h3_from_pdb(text, h_chain)
        if cdr_data.get("cdr_h3_length"):
            return cdr_data, new_time
    
    return None, new_time


async def main():
    logger.info("=" * 60)
    logger.info(f"SAbDab Weekly Sync - {datetime.now().isoformat()}")
    logger.info("=" * 60)
    
    # Initialize database
    db = SAbDabDatabase()
    
    # Get existing entries
    existing = db.get_existing_pdb_codes()
    logger.info(f"Current database: {len(existing)} unique PDB codes")
    
    # Start sync log
    log_id = db.start_sync_log("incremental")
    entries_added = 0
    
    try:
        async with aiohttp.ClientSession() as session:
            last_time = 0.0
            
            # Fetch current VHH summary
            logger.info("Fetching VHH summary...")
            summary_url = f"{SABDAB_BASE}/sabdab/summary/all/"
            text, last_time = await rate_limited_get(
                session, summary_url, last_time, {"ABtype": "VHH"}
            )
            
            if not text:
                raise RuntimeError("Failed to fetch VHH summary")
            
            entries = parse_summary_tsv(text)
            logger.info(f"Remote database: {len(entries)} entries")
            
            # Find new entries
            new_entries = [e for e in entries if e.get("pdb", "").lower() not in existing]
            logger.info(f"New entries to sync: {len(new_entries)}")
            
            if not new_entries:
                logger.info("Database is up to date, no new entries")
                db.complete_sync_log(log_id, 0, 0)
                return
            
            # Sync new entries
            for i, row in enumerate(new_entries):
                pdb_code = row.get("pdb", "").lower()
                h_chain = row.get("Hchain", "")
                
                # Fetch CDR data
                cdr_data, last_time = await fetch_cdr_data(
                    session, pdb_code, h_chain, last_time
                )
                
                # Upsert
                structure = row_to_vhh_structure(row, cdr_data)
                db.upsert(structure)
                entries_added += 1
                
                cdr_h3 = cdr_data.get("cdr_h3_length") if cdr_data else "N/A"
                logger.info(f"[{i+1}/{len(new_entries)}] Added {pdb_code} (CDR-H3: {cdr_h3})")
        
        # Complete sync
        db.complete_sync_log(log_id, entries_added, 0)
        
        logger.info("=" * 60)
        logger.info(f"Sync complete! Added {entries_added} new entries")
        
        stats = db.get_stats()
        logger.info(f"Total in database: {stats['total_entries']}")
        logger.info(f"Database size: {stats['db_size_mb']} MB")
        
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        db.complete_sync_log(log_id, entries_added, 0, str(e))
        raise


if __name__ == "__main__":
    # Ensure log directory exists
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    asyncio.run(main())
