"""
Result Ingester Service - Parse pipeline outputs into database.

Reads all_designs.csv and success_metrics.json from completed jobs
and populates the Design table in SQLite.
"""

import csv
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import Design, Job
from paths import get_data_root


async def ingest_job_results(
    job_id: str, 
    output_dir: str, 
    session: AsyncSession
) -> int:
    """
    Parse pipeline outputs and populate Design table.
    
    Args:
        job_id: The job ID to associate designs with
        output_dir: Path to the job's output directory (e.g., legacy pdj_results/job_xxx)
        session: Async database session
        
    Returns:
        Number of designs ingested
    """
    # Resolve relative paths to absolute using data root
    output_path = Path(output_dir)
    if not output_path.is_absolute():
        output_path = get_data_root() / output_dir
    
    csv_path = output_path / "results" / "all_designs.csv"
    
    if not csv_path.exists():
        print(f"[Ingester] No all_designs.csv found at {csv_path}")
        return 0
    
    # Check if designs already ingested for this job
    existing = await session.execute(
        select(Design).where(Design.job_id == job_id).limit(1)
    )
    if existing.scalar_one_or_none():
        print(f"[Ingester] Designs already ingested for job {job_id}")
        return 0
    
    # Extract PDB files from tar.gz archives
    pdb_dir = extract_pdb_files(output_path)
    
    designs_created = 0
    
    try:
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                # Map CSV columns to Design fields
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=row.get('description', f'design_{designs_created}'),
                    pdb_path=find_pdb_path(output_path, row.get('description', '')),
                    json_path=None,  # Could add if needed
                    
                    # Structural metrics from RFdiffusion
                    num_helices=safe_int(row.get('pr_helices')),
                    num_strands=safe_int(row.get('pr_strands')),
                    rog=safe_float(row.get('pr_RoG')),
                    
                    # Sequence design metrics
                    mpnn_score=safe_float(row.get('seq_mpnn_score')),
                    fampnn_psce=safe_float(row.get('seq_fampnn_psce')),
                    
                    # Structure prediction metrics (AF2/Boltz)
                    plddt_overall=safe_float(row.get('pr_plddt') or row.get('plddt')),
                    plddt_binder=safe_float(row.get('pr_plddt_binder')),
                    plddt_target=safe_float(row.get('pr_plddt_target')),
                    pae_interaction=safe_float(row.get('pr_pae_interaction')),
                    pae_overall=safe_float(row.get('pr_pae') or row.get('pae')),
                    rmsd_overall=safe_float(row.get('pr_rmsd')),
                    rmsd_binder=safe_float(row.get('pr_rmsd_binder')),
                    
                    # Boltz-2 specific
                    conf_score=safe_float(row.get('conf_score')),
                    ptm=safe_float(row.get('ptm')),
                    
                    # User annotations (defaults)
                    is_favorite=False,
                    notes=None,
                    
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
        
        await session.commit()
        print(f"[Ingester] Ingested {designs_created} designs for job {job_id}")
        
    except Exception as e:
        print(f"[Ingester] Error ingesting results: {e}")
        await session.rollback()
        return 0
    
    if designs_created == 0:
        print(f"[Ingester] No designs found in CSV or CSV missing. Trying loose files...")
        designs_created = await ingest_loose_files(job_id, output_path, session)

    return designs_created


async def ingest_loose_files(
    job_id: str, 
    output_path: Path, 
    session: AsyncSession
) -> int:
    """Ingest designs from individual JSON/PDB files (fallback)."""
    
    # Locations to search for confidence/metrics JSONs
    # Boltz outputs often in pdb_files/predictions/
    search_paths = [
        output_path / "pdb_files" / "predictions",
        output_path / "pdb_files",
        output_path
    ]
    
    designs_created = 0
    
    # Track ingested names to avoid duplicates
    ingested_names = set()

    for search_dir in search_paths:
        if not search_dir.exists():
            continue
            
        # Look for confidence_*.json patterns from Boltz
        for json_file in search_dir.glob("confidence_*.json"):
            try:
                # Filename format: confidence_DESIGNNAME.json
                design_name = json_file.stem.replace("confidence_", "")
                
                if design_name in ingested_names:
                    continue
                
                # Look for corresponding PDB
                # It might be in the same dir as DESIGNNAME.pdb
                pdb_path = search_dir / f"{design_name}.pdb"
                if not pdb_path.exists():
                    # Check parent pdb_files if we are in predictions subdir
                    pdb_path = output_path / "pdb_files" / f"{design_name}.pdb"
                
                if not pdb_path.exists():
                    # Try finding it recursively? No, keep simple first.
                    continue
                    
                # Read metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)
                
                # Check mapping
                plddt = metrics.get('complex_plddt') or metrics.get('plddt')
                # Scale pLDDT if seemingly 0-1 to 0-100
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0
                
                conf_score = metrics.get('confidence_score')
                ptm = metrics.get('ptm')
                pae = metrics.get('complex_pae') or metrics.get('pae') # Boltz puts pae in file? Usually separate.
                # Note: Boltz JSON (see step 294) has 'complex_pde' but not pae.
                # It does contain 'pair_chains_iptm'.
                
                # Create design
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(pdb_path),
                    json_path=str(json_file),
                    
                    # Metrics
                    plddt_overall=safe_float(plddt),
                    ptm=safe_float(ptm),
                    conf_score=safe_float(conf_score),
                    
                    # Defaults for others
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)
                
            except Exception as e:
                print(f"[Ingester] Error parsing loose file {json_file}: {e}")
                
    if designs_created > 0:
        try:
            await session.commit()
            print(f"[Ingester] Ingested {designs_created} designs from loose files for job {job_id}")
        except Exception as e:
            print(f"[Ingester] Error committing loose files: {e}")
            await session.rollback()
            return 0
            
    return designs_created


def extract_pdb_files(output_path: Path) -> Path:
    """
    Extract PDB files from tar.gz archives to a pdb_files directory.
    Returns the path to the directory containing extracted PDBs.
    """
    import tarfile
    
    pdb_dir = output_path / "pdb_files"
    
    # Skip if already extracted
    if pdb_dir.exists() and any(pdb_dir.glob("*.pdb")):
        print(f"[Ingester] PDB files already extracted to {pdb_dir}")
        return pdb_dir
    
    pdb_dir.mkdir(exist_ok=True)
    
    # Look for result tar.gz files to extract
    tar_locations = [
        output_path / "run" / "af2" / "af2_results.tar.gz",
        output_path / "run" / "boltz" / "boltz_results.tar.gz", 
        output_path / "run" / "rf3" / "rf3_results.tar.gz",
        output_path / "results" / "best_designs.tar.gz",
    ]
    
    for tar_path in tar_locations:
        if tar_path.exists():
            try:
                with tarfile.open(tar_path, 'r:gz') as tar:
                    for member in tar.getmembers():
                        if member.name.endswith('.pdb'):
                            # Extract just the filename
                            member.name = Path(member.name).name
                            tar.extract(member, pdb_dir)
                            print(f"[Ingester] Extracted {member.name}")
            except Exception as e:
                print(f"[Ingester] Error extracting {tar_path}: {e}")
    
    pdb_count = len(list(pdb_dir.glob("*.pdb")))
    print(f"[Ingester] Extracted {pdb_count} PDB files to {pdb_dir}")
    return pdb_dir


def find_pdb_path(output_path: Path, design_name: str) -> str:
    """Find the PDB file path for a design."""
    # Check in pdb_files directory first (extracted from tar.gz)
    pdb_files = output_path / "pdb_files"
    if pdb_files.exists():
        pdb_file = pdb_files / f"{design_name}.pdb"
        if pdb_file.exists():
            return str(pdb_file)
    
    # Check in best_designs directory
    best_designs = output_path / "best_designs"
    if best_designs.exists():
        pdb_file = best_designs / f"{design_name}.pdb"
        if pdb_file.exists():
            return str(pdb_file)
    
    # Fallback - return expected path in pdb_files
    return str(output_path / "pdb_files" / f"{design_name}.pdb")


def safe_float(value) -> Optional[float]:
    """Safely convert to float, returning None on failure."""
    if value is None or value == '' or value == 'NA' or value == 'nan':
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def safe_int(value) -> Optional[int]:
    """Safely convert to int, returning None on failure."""
    if value is None or value == '' or value == 'NA' or value == 'nan':
        return None
    try:
        return int(float(value))  # Handle "3.0" -> 3
    except (ValueError, TypeError):
        return None


async def get_job_summary_metrics(output_dir: str) -> dict:
    """
    Read success_metrics.json for job summary stats.
    """
    metrics_path = Path(output_dir) / "results" / "success_metrics.json"
    
    if not metrics_path.exists():
        return {}
    
    try:
        with open(metrics_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[Ingester] Error reading metrics: {e}")
        return {}
