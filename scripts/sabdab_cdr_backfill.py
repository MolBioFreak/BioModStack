#!/usr/bin/env python3
"""
SAbDab CDR Backfill - Compute CDR-H3 lengths using local ANARCII.

Downloads PDBs in parallel and computes CDR lengths locally using ANARCII.
Much faster than per-PDB IMGT annotation downloads.

Usage:
    python sabdab_cdr_backfill.py [--workers N] [--limit N] [--dry-run]
"""

import sys
import os
import argparse
import asyncio
import aiohttp
import subprocess
import json
import tempfile
import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "platform" / "api"))

from services.sabdab_db import SAbDabDatabase

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# SAbDab API
SABDAB_BASE = "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred"

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent
CONTAINER_PATH = PROJECT_ROOT / "apptainer" / "antibody_tools.sif"


def extract_sequence_from_pdb(pdb_content: str, chain_id: str) -> Optional[str]:
    """Extract amino acid sequence from PDB content for a specific chain."""
    aa_map = {
        'ALA': 'A', 'CYS': 'C', 'ASP': 'D', 'GLU': 'E', 'PHE': 'F',
        'GLY': 'G', 'HIS': 'H', 'ILE': 'I', 'LYS': 'K', 'LEU': 'L',
        'MET': 'M', 'ASN': 'N', 'PRO': 'P', 'GLN': 'Q', 'ARG': 'R',
        'SER': 'S', 'THR': 'T', 'VAL': 'V', 'TRP': 'W', 'TYR': 'Y'
    }
    
    residues = []
    seen = set()
    
    for line in pdb_content.split('\n'):
        if not line.startswith('ATOM'):
            continue
        
        try:
            chain = line[21].strip()
            if chain != chain_id:
                continue
            
            res_name = line[17:20].strip()
            res_num = line[22:27].strip()
            
            key = (chain, res_num)
            if key in seen:
                continue
            seen.add(key)
            
            aa = aa_map.get(res_name, 'X')
            if aa != 'X':
                residues.append((res_num, aa))
        except (IndexError, ValueError):
            continue
    
    # Sort by residue number and join
    residues.sort(key=lambda x: (int(''.join(c for c in x[0] if c.isdigit() or c == '-') or '0'), x[0]))
    sequence = ''.join(aa for _, aa in residues)
    
    return sequence if len(sequence) >= 50 else None  # VHH minimum ~100 residues


def run_anarcii_batch(sequences: Dict[str, str]) -> Dict[str, Dict]:
    """
    Run ANARCII on a batch of sequences using the antibody_tools container.
    
    Args:
        sequences: Dict of {key: sequence}
    
    Returns:
        Dict of {key: {"cdr_h1_length": int, "cdr_h2_length": int, "cdr_h3_length": int, "cdr_h3_sequence": str}}
    """
    if not CONTAINER_PATH.exists():
        logger.error(f"Container not found: {CONTAINER_PATH}")
        return {}
    
    if not sequences:
        return {}
    
    # Create temp file with sequences in FASTA format
    with tempfile.NamedTemporaryFile(mode='w', suffix='.fasta', delete=False) as f:
        for key, seq in sequences.items():
            f.write(f">{key}\n{seq}\n")
        fasta_path = f.name
    
    try:
        # Run ANARCII inside container
        cmd = [
            "apptainer", "exec", str(CONTAINER_PATH),
            "python3", "-c", f'''
import json
from anarcii import Anarcii

# Read sequences from FASTA
seqs = {{}}
with open("{fasta_path}") as f:
    current_name = None
    for line in f:
        line = line.strip()
        if line.startswith(">"):
            current_name = line[1:]
        elif current_name and line:
            seqs[current_name] = line

numberer = Anarcii()
results = numberer.number(list(seqs.values()))

output = {{}}
for (seq_name, seq), (result_name, data) in zip(seqs.items(), results.items()):
    chain_type = data.get("chain_type", "")
    if chain_type not in ("H", "K", "L"):
        continue
    
    numbering = data.get("numbering", [])
    
    cdr1 = []
    cdr2 = []
    cdr3 = []
    
    for (pos, insertion), aa in numbering:
        if aa == "-":
            continue
        if 27 <= pos <= 38:
            cdr1.append(aa)
        elif 56 <= pos <= 65:
            cdr2.append(aa)
        elif 105 <= pos <= 117:
            cdr3.append(aa)
    
    output[seq_name] = {{
        "cdr_h1_length": len(cdr1),
        "cdr_h2_length": len(cdr2),
        "cdr_h3_length": len(cdr3),
        "cdr_h3_sequence": "".join(cdr3),
    }}

print(json.dumps(output))
'''
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 min timeout for batch
        )
        
        if result.returncode == 0 and result.stdout.strip():
            try:
                return json.loads(result.stdout.strip())
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON from ANARCII: {result.stdout[:200]}")
                return {}
        else:
            logger.error(f"ANARCII error: {result.stderr[:500]}")
            return {}
            
    except subprocess.TimeoutExpired:
        logger.error("ANARCII batch timeout")
        return {}
    except Exception as e:
        logger.error(f"ANARCII error: {e}")
        return {}
    finally:
        os.unlink(fasta_path)


async def fetch_pdb(
    session: aiohttp.ClientSession,
    pdb_code: str,
    semaphore: asyncio.Semaphore
) -> Tuple[str, Optional[str]]:
    """Fetch IMGT-numbered PDB content."""
    async with semaphore:
        url = f"{SABDAB_BASE}/sabdab/pdb/{pdb_code}/?scheme=imgt"
        headers = {"User-Agent": "BioModStack/1.0 (CDR backfill)"}
        
        try:
            async with session.get(url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    content = await response.text()
                    if content.startswith(('HEADER', 'REMARK', 'ATOM')):
                        return pdb_code, content
                return pdb_code, None
        except Exception as e:
            logger.debug(f"Error fetching {pdb_code}: {e}")
            return pdb_code, None


async def download_pdbs_batch(
    pdb_codes: List[str],
    max_concurrent: int = 20
) -> Dict[str, str]:
    """Download multiple PDBs concurrently."""
    semaphore = asyncio.Semaphore(max_concurrent)
    results = {}
    
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_pdb(session, pdb, semaphore) for pdb in pdb_codes]
        
        for coro in asyncio.as_completed(tasks):
            pdb_code, content = await coro
            if content:
                results[pdb_code] = content
    
    return results


async def main():
    parser = argparse.ArgumentParser(description="Backfill CDR-H3 lengths using local ANARCII")
    parser.add_argument("--workers", type=int, default=20, help="Concurrent PDB downloads")
    parser.add_argument("--batch-size", type=int, default=200, help="ANARCII batch size")
    parser.add_argument("--limit", type=int, help="Limit entries to process")
    parser.add_argument("--dry-run", action="store_true", help="Don't update database")
    args = parser.parse_args()
    
    # Check container
    if not CONTAINER_PATH.exists():
        logger.error(f"ANARCII container not found: {CONTAINER_PATH}")
        logger.error("Run: apptainer pull antibody_tools.sif docker://...")
        return
    
    # Initialize database
    db = SAbDabDatabase()
    logger.info(f"Database: {db.db_path}")
    
    # Get entries missing CDR-H3 data
    with db._connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pdb_code, h_chain 
            FROM vhh_structures 
            WHERE cdr_h3_length IS NULL
            ORDER BY pdb_code
        """)
        entries = [(row["pdb_code"], row["h_chain"]) for row in cursor.fetchall()]
    
    logger.info(f"Entries missing CDR-H3: {len(entries)}")
    
    if args.limit:
        entries = entries[:args.limit]
        logger.info(f"Limited to {len(entries)} entries")
    
    if not entries:
        logger.info("All entries have CDR-H3 data!")
        return
    
    # Group by PDB code (one PDB can have multiple chains)
    pdb_chains = {}
    for pdb_code, h_chain in entries:
        if pdb_code not in pdb_chains:
            pdb_chains[pdb_code] = []
        pdb_chains[pdb_code].append(h_chain)
    
    logger.info(f"Unique PDBs to fetch: {len(pdb_chains)}")
    
    # Process in batches
    pdb_list = list(pdb_chains.keys())
    total_updated = 0
    
    for batch_start in range(0, len(pdb_list), args.batch_size):
        batch_pdbs = pdb_list[batch_start:batch_start + args.batch_size]
        logger.info(f"Batch {batch_start // args.batch_size + 1}: Fetching {len(batch_pdbs)} PDBs...")
        
        # Download PDBs concurrently
        pdb_contents = await download_pdbs_batch(batch_pdbs, args.workers)
        logger.info(f"  Downloaded: {len(pdb_contents)} PDBs")
        
        # Extract sequences for each chain
        sequences = {}
        for pdb_code, content in pdb_contents.items():
            for h_chain in pdb_chains[pdb_code]:
                seq = extract_sequence_from_pdb(content, h_chain)
                if seq:
                    key = f"{pdb_code}_{h_chain}"
                    sequences[key] = seq
        
        logger.info(f"  Extracted: {len(sequences)} sequences")
        
        if not sequences:
            continue
        
        # Run ANARCII batch
        logger.info(f"  Running ANARCII...")
        cdr_results = run_anarcii_batch(sequences)
        logger.info(f"  ANARCII results: {len(cdr_results)}")
        
        # Update database
        if not args.dry_run:
            with db._connection() as conn:
                cursor = conn.cursor()
                for key, cdr_data in cdr_results.items():
                    pdb_code, h_chain = key.rsplit("_", 1)
                    cursor.execute("""
                        UPDATE vhh_structures 
                        SET cdr_h1_length = ?,
                            cdr_h2_length = ?,
                            cdr_h3_length = ?,
                            cdr_h3_sequence = ?
                        WHERE pdb_code = ? AND h_chain = ?
                    """, (
                        cdr_data["cdr_h1_length"],
                        cdr_data["cdr_h2_length"],
                        cdr_data["cdr_h3_length"],
                        cdr_data["cdr_h3_sequence"],
                        pdb_code, h_chain
                    ))
                conn.commit()
                total_updated += len(cdr_results)
        
        logger.info(f"  Updated: {len(cdr_results)} entries (total: {total_updated})")
    
    logger.info("=" * 60)
    logger.info(f"CDR backfill complete! Updated {total_updated} entries")
    
    stats = db.get_stats()
    logger.info(f"Entries with CDR-H3: {stats['entries_with_cdr_h3']}")


if __name__ == "__main__":
    asyncio.run(main())
