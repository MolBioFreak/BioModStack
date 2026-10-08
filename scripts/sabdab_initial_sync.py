#!/usr/bin/env python3
"""
SAbDab Initial Database Sync - Populate local SQLite mirror.

Downloads all VHH entries from NanoSAbDab and fetches CDR-H3 lengths
from IMGT annotation files.

Usage:
    python sabdab_initial_sync.py [--limit N] [--skip-cdr]
    
Options:
    --limit N     Only sync first N entries (for testing)
    --skip-cdr    Skip CDR length fetching (faster, but no CDR-H3 data)
    --resume      Resume from where we left off (skip existing entries)

License: CC-BY 4.0 (https://creativecommons.org/licenses/by/4.0/)
Attribution: Schneider, C. et al. (2022) Nucleic Acids Res. 50(D1):D1368-D1372
"""

import sys
import os
import argparse
import asyncio
import aiohttp
import time
import re
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "platform" / "api"))

from services.sabdab_db import SAbDabDatabase, VHHStructure, get_sabdab_db_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# SAbDab API configuration
SABDAB_BASE = "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred"
ABDB_BASE = "https://opig.stats.ox.ac.uk/webapps/abdb"
RATE_LIMIT_SECONDS = 2.0  # Be conservative with rate limiting


async def rate_limited_get(
    session: aiohttp.ClientSession,
    url: str,
    last_request_time: float,
    params: Optional[Dict[str, Any]] = None
) -> Tuple[Optional[str], float]:
    """Make a rate-limited GET request."""
    # Wait if needed
    elapsed = time.time() - last_request_time
    if elapsed < RATE_LIMIT_SECONDS:
        await asyncio.sleep(RATE_LIMIT_SECONDS - elapsed)
    
    headers = {
        "User-Agent": "BioModStack/1.0 (academic research, initial sync)",
        "Accept": "text/html,text/plain,*/*"
    }
    
    try:
        async with session.get(url, params=params, headers=headers, timeout=30) as response:
            new_time = time.time()
            if response.status == 200:
                return await response.text(), new_time
            else:
                logger.warning(f"Request failed: {url} -> {response.status}")
                return None, new_time
    except Exception as e:
        logger.error(f"Request error: {url} -> {e}")
        return None, time.time()


def parse_summary_tsv(text: str) -> List[Dict[str, str]]:
    """Parse SAbDab summary TSV into list of dicts."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return []
    
    headers = lines[0].split("\t")
    entries = []
    
    for line in lines[1:]:
        values = line.split("\t")
        if len(values) != len(headers):
            continue
        entries.append(dict(zip(headers, values)))
    
    return entries


def parse_imgt_annotation(text: str) -> Dict[str, Any]:
    """
    Parse IMGT annotation file to extract CDR lengths.
    
    IMGT annotation files contain lines like:
        gene_type: VH
        CDR1-IMGT: 27-38 (length 12)
        CDR2-IMGT: 56-65 (length 10)
        CDR3-IMGT: 105-117 (length 13)
    """
    result = {
        "cdr_h1_length": None,
        "cdr_h2_length": None,
        "cdr_h3_length": None,
        "cdr_h3_sequence": None
    }
    
    # Try to extract CDR3 length from annotation
    # Pattern: CDR3-IMGT: START-END (length N) SEQUENCE
    cdr3_pattern = r"CDR3-IMGT:\s*(\d+)-(\d+)\s*\(length\s*(\d+)\)\s*(\w*)"
    cdr2_pattern = r"CDR2-IMGT:\s*(\d+)-(\d+)\s*\(length\s*(\d+)\)"
    cdr1_pattern = r"CDR1-IMGT:\s*(\d+)-(\d+)\s*\(length\s*(\d+)\)"
    
    cdr3_match = re.search(cdr3_pattern, text)
    if cdr3_match:
        result["cdr_h3_length"] = int(cdr3_match.group(3))
        if cdr3_match.group(4):
            result["cdr_h3_sequence"] = cdr3_match.group(4)
    
    cdr2_match = re.search(cdr2_pattern, text)
    if cdr2_match:
        result["cdr_h2_length"] = int(cdr2_match.group(3))
    
    cdr1_match = re.search(cdr1_pattern, text)
    if cdr1_match:
        result["cdr_h1_length"] = int(cdr1_match.group(3))
    
    return result


def safe_float(val: str) -> Optional[float]:
    """Safely parse float from string."""
    try:
        return float(val) if val and val not in ("NA", "None", "") else None
    except ValueError:
        return None


def safe_int(val: str) -> Optional[int]:
    """Safely parse int from string."""
    try:
        return int(val) if val and val not in ("NA", "None", "") else None
    except ValueError:
        return None


def row_to_vhh_structure(row: Dict[str, str], cdr_data: Optional[Dict[str, Any]] = None) -> VHHStructure:
    """Convert a summary row to VHHStructure."""
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


def compute_cdr_h3_from_pdb(pdb_content: str, h_chain: str) -> Dict[str, Any]:
    """
    Compute CDR-H3 length from IMGT-numbered PDB file.
    
    In IMGT numbering, CDR-H3 spans positions 105-117 (with possible insertions).
    We count unique residue numbers in this range for the heavy chain.
    """
    result = {
        "cdr_h1_length": None,
        "cdr_h2_length": None,
        "cdr_h3_length": None,
        "cdr_h3_sequence": None
    }
    
    # IMGT CDR definitions (position ranges)
    cdr_h1_range = range(27, 39)   # 27-38
    cdr_h2_range = range(56, 66)   # 56-65
    cdr_h3_range = range(105, 118) # 105-117 (can have insertions up to ~129)
    
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
        
        # Parse PDB ATOM record
        try:
            atom_name = line[12:16].strip()
            # Only count CA atoms to avoid duplicates
            if atom_name != 'CA':
                continue
            
            res_name = line[17:20].strip()
            chain = line[21].strip()
            res_num_str = line[22:27].strip()
            
            # Skip if not the target chain
            if chain != h_chain:
                continue
            
            # Parse residue number (may have insertion code)
            res_num = int(''.join(c for c in res_num_str if c.isdigit() or c == '-'))
            
            # Categorize by CDR region
            if res_num in cdr_h1_range:
                h1_residues.add(res_num_str)
            elif res_num in cdr_h2_range:
                h2_residues.add(res_num_str)
            elif 105 <= res_num <= 129:  # Extended range for H3 with insertions
                h3_residues.add(res_num_str)
                h3_sequence.append((res_num, res_num_str, aa_map.get(res_name, 'X')))
        except (ValueError, IndexError):
            continue
    
    # Set lengths
    if h1_residues:
        result["cdr_h1_length"] = len(h1_residues)
    if h2_residues:
        result["cdr_h2_length"] = len(h2_residues)
    if h3_residues:
        result["cdr_h3_length"] = len(h3_residues)
        # Build sequence ordered by residue number
        h3_sequence.sort(key=lambda x: (x[0], x[1]))
        result["cdr_h3_sequence"] = ''.join(x[2] for x in h3_sequence)
    
    return result


async def fetch_cdr_data(
    session: aiohttp.ClientSession,
    pdb_code: str,
    h_chain: str,
    last_request_time: float
) -> Tuple[Optional[Dict[str, Any]], float]:
    """Fetch CDR data by downloading IMGT-numbered PDB and computing lengths."""
    # Fetch IMGT-numbered PDB file
    url = f"{SABDAB_BASE}/sabdab/pdb/{pdb_code}/?scheme=imgt"
    
    text, new_time = await rate_limited_get(session, url, last_request_time)
    
    if text and text.startswith(('HEADER', 'REMARK', 'ATOM')):
        cdr_data = compute_cdr_h3_from_pdb(text, h_chain)
        if cdr_data.get("cdr_h3_length"):
            return cdr_data, new_time
    
    return None, new_time


async def main():
    parser = argparse.ArgumentParser(description="Sync SAbDab VHH data to local SQLite")
    parser.add_argument("--limit", type=int, help="Limit number of entries to sync")
    parser.add_argument("--skip-cdr", action="store_true", help="Skip CDR length fetching")
    parser.add_argument("--resume", action="store_true", help="Skip existing entries")
    args = parser.parse_args()
    
    # Initialize database
    db = SAbDabDatabase()
    logger.info(f"Database path: {db.db_path}")
    
    # Get existing entries if resuming
    existing_keys = set()
    if args.resume:
        existing_keys = db.get_existing_pdb_codes()
        logger.info(f"Resume mode: {len(existing_keys)} existing PDB codes will be skipped")
    
    # Start sync log
    sync_type = "initial" if not args.resume else "resume"
    log_id = db.start_sync_log(sync_type)
    
    entries_added = 0
    entries_skipped = 0
    
    try:
        async with aiohttp.ClientSession() as session:
            last_time = 0.0
            
            # Step 1: Fetch VHH summary
            logger.info("Fetching VHH summary from NanoSAbDab...")
            summary_url = f"{SABDAB_BASE}/sabdab/summary/all/"
            text, last_time = await rate_limited_get(
                session, summary_url, last_time, {"ABtype": "VHH"}
            )
            
            if not text:
                raise RuntimeError("Failed to fetch VHH summary")
            
            entries = parse_summary_tsv(text)
            logger.info(f"Parsed {len(entries)} VHH entries from summary")
            
            # Apply limit if specified
            if args.limit:
                entries = entries[:args.limit]
                logger.info(f"Limited to {len(entries)} entries")
            
            # Step 2: Process each entry
            total = len(entries)
            for i, row in enumerate(entries):
                pdb_code = row.get("pdb", "").lower()
                h_chain = row.get("Hchain", "")
                
                # Skip if resuming and entry exists
                if args.resume and pdb_code in existing_keys:
                    entries_skipped += 1
                    continue
                
                # Fetch CDR data unless skipped
                cdr_data = None
                if not args.skip_cdr:
                    cdr_data, last_time = await fetch_cdr_data(
                        session, pdb_code, h_chain, last_time
                    )
                    if cdr_data:
                        logger.debug(f"[{i+1}/{total}] {pdb_code} CDR-H3: {cdr_data.get('cdr_h3_length')}")
                
                # Convert and upsert
                structure = row_to_vhh_structure(row, cdr_data)
                db.upsert(structure)
                entries_added += 1
                
                # Progress update every 50 entries
                if (i + 1) % 50 == 0:
                    logger.info(f"Progress: {i+1}/{total} ({entries_added} added, {entries_skipped} skipped)")
        
        # Complete sync
        db.complete_sync_log(log_id, entries_added, 0)
        
        logger.info("=" * 60)
        logger.info(f"Sync complete!")
        logger.info(f"  Entries added: {entries_added}")
        logger.info(f"  Entries skipped: {entries_skipped}")
        
        # Print stats
        stats = db.get_stats()
        logger.info(f"  Total in database: {stats['total_entries']}")
        logger.info(f"  With CDR-H3 length: {stats['entries_with_cdr_h3']}")
        logger.info(f"  Database size: {stats['db_size_mb']} MB")
        
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        db.complete_sync_log(log_id, entries_added, 0, str(e))
        raise


if __name__ == "__main__":
    asyncio.run(main())
