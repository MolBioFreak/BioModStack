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

# Project root (parent of platform directory)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


async def ingest_job_results(
    job_id: str, 
    output_dir: str, 
    session: AsyncSession
) -> int:
    """
    Parse pipeline outputs and populate Design table.
    
    Args:
        job_id: The job ID to associate designs with
        output_dir: Path to the job's output directory (e.g., pdj_results/job_xxx)
        session: Async database session
        
    Returns:
        Number of designs ingested
    """
    # Resolve relative paths to absolute using PROJECT_ROOT
    output_path = Path(output_dir)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_dir
    
    csv_path = output_path / "results" / "all_designs.csv"
    
    if not csv_path.exists():
        print(f"[Ingester] No all_designs.csv found at {csv_path}")
        # Proceed to try other methods if CSV missing
    else:
        # Check if designs already ingested for this job (only if we have a CSV to potentially dupe against? 
        # Actually logic for dupe check is before extraction.
        pass
    
    # Extract PDB files from tar.gz archives
    pdb_dir = extract_pdb_files(output_path)
    
    designs_created = 0

    designs_created = 0
    
    # Only try to process CSV if it exists
    if csv_path.exists():
        # Check if designs already ingested for this job
        existing = await session.execute(
            select(Design).where(Design.job_id == job_id).limit(1)
        )
        if existing.scalar_one_or_none():
            print(f"[Ingester] Designs already ingested for job {job_id}")
            return 0
        
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
            # Don't return 0 here, let it fall through to valid loose file check if CSV failed partial?
            # Or return 0? Standard flow usually returns if error.
            # But let's allow fallback if designs_created is still 0
            pass
    
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
    # RF3 outputs in pdb_files/rf3/output/*/
    search_paths = [
        output_path / "pdb_files" / "predictions",
        output_path / "pdb_files",
        output_path,
    ]
    
    # Also search RF3 nested output directories
    rf3_base = output_path / "pdb_files" / "rf3" / "output"
    if rf3_base.exists():
        for subdir in rf3_base.iterdir():
            if subdir.is_dir():
                search_paths.append(subdir)
                # Also search seed-*/sample-* subdirs
                for sample_dir in subdir.glob("seed-*_sample-*"):
                    if sample_dir.is_dir():
                        search_paths.append(sample_dir)
    
    designs_created = 0
    
    # Track ingested names to avoid duplicates
    ingested_names = set()

    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        
        # BOLTZ2: Look for confidence_*.json patterns
        for json_file in search_dir.glob("confidence_*.json"):
            try:
                # Filename format: confidence_DESIGNNAME.json
                design_name = json_file.stem.replace("confidence_", "")
                
                # Skip input templates (no numeric suffix) - these are not actual designs
                # Actual designs are named like: boltzgen_input_0, boltzgen_input_1, etc.
                import re
                if not re.search(r'_\d+$', design_name):
                    print(f"[Ingester] Skipping input template: {design_name}")
                    continue
                
                if design_name in ingested_names:
                    continue
                
                # Look for corresponding Structure (CIF preferred for complexes, PDB fallback)
                structure_path = search_dir / f"{design_name}.cif"
                if not structure_path.exists():
                    structure_path = output_path / "pdb_files" / f"{design_name}.cif"
                
                if not structure_path.exists():
                    # Fallback to PDB
                    structure_path = search_dir / f"{design_name}.pdb"
                    if not structure_path.exists():
                        structure_path = output_path / "pdb_files" / f"{design_name}.pdb"
                    if not structure_path.exists():
                        structure_path = output_path / "pdb_files" / "predictions" / f"{design_name}.pdb"
                
                if not structure_path.exists():
                    continue
                    
                # Read Boltz2 metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)
                
                # Boltz2 format: complex_plddt, ptm, iptm, confidence_score, complex_pde
                plddt = metrics.get('complex_plddt') or metrics.get('plddt')
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0
                
                conf_score = metrics.get('confidence_score')
                ptm = metrics.get('ptm')
                ligand_iptm = metrics.get('ligand_iptm')
                
                # Boltz2 uses 'complex_pde' not PAE - convert PDE to estimated PAE
                pae = metrics.get('complex_pae') or metrics.get('pae')
                if pae is None:
                    pde = metrics.get('complex_pde')
                    if pde is not None:
                        # PDE (Predicted Distance Error) is similar to PAE but different scale
                        pae = pde  # Store as-is for now
                
                # Look for Affinity JSON
                affinity_score = None
                binder_probability = None
                
                # Try multiple locations for affinity file
                affinity_file = search_dir / f"affinity_{design_name}.json"
                if not affinity_file.exists():
                    affinity_file = output_path / "pdb_files" / "predictions" / f"affinity_{design_name}.json"
                    
                if affinity_file.exists():
                    try:
                        with open(affinity_file, 'r') as af:
                            aff_metrics = json.load(af)
                            affinity_score = aff_metrics.get('affinity_pred_value')
                            binder_probability = aff_metrics.get('affinity_probability_binary')
                    except Exception as e:
                        print(f"[Ingester] Error parsing affinity file {affinity_file}: {e}")

                # Extract per-residue pLDDT from PDB B-factors
                _, residue_plddt = extract_plddt_from_pdb(structure_path)
                
                # Create design
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(json_file),
                    
                    # Metrics
                    plddt_overall=safe_float(plddt),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    conf_score=safe_float(conf_score),
                    ligand_iptm=safe_float(ligand_iptm),
                    affinity_score=safe_float(affinity_score),
                    binder_probability=safe_float(binder_probability),
                    residue_plddt=residue_plddt,
                    
                    # Defaults for others
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)
                
            except Exception as e:
                print(f"[Ingester] Error parsing Boltz2 file {json_file}: {e}")
        
        # RF3: Look for *_summary_confidences.json patterns
        for json_file in search_dir.glob("*_summary_confidences.json"):
            try:
                # Filename format: DESIGNNAME_summary_confidences.json
                design_name = json_file.stem.replace("_summary_confidences", "")
                
                if design_name in ingested_names:
                    continue
                
                # Look for corresponding structure file (RF3 outputs .cif not .pdb)
                # Try CIF first (RF3 default), then PDB as fallback
                structure_path = None
                
                # Check for CIF with _model suffix
                cif_path = search_dir / f"{design_name}_model.cif"
                if not cif_path.exists():
                    cif_path = search_dir / f"{design_name}.cif"
                if not cif_path.exists():
                    cif_path = search_dir.parent / f"{design_name}_model.cif"
                if not cif_path.exists():
                    cif_path = search_dir.parent / f"{design_name}.cif"
                
                if cif_path.exists():
                    structure_path = cif_path
                else:
                    # Fallback to PDB
                    pdb_path = search_dir / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        pdb_path = search_dir.parent / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        pdb_path = search_dir.parent.parent / f"{design_name}.pdb"
                    if not pdb_path.exists():
                        # Try without seed/sample suffix
                        base_name = design_name.rsplit("_seed-", 1)[0]
                        pdb_path = output_path / "pdb_files" / f"{base_name}.pdb"
                    if pdb_path.exists():
                        structure_path = pdb_path
                
                if not structure_path:
                    print(f"[Ingester] No structure file found for RF3 design {design_name}")
                    continue
                    
                # Read RF3 metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)
                
                # RF3 format: overall_plddt, ptm, iptm, overall_pae, ranking_score
                plddt = metrics.get('overall_plddt')
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0
                
                pae = metrics.get('overall_pae')
                ptm = metrics.get('ptm')
                iptm = metrics.get('iptm')
                ranking_score = metrics.get('ranking_score')
                
                # Use ranking_score as confidence if available
                conf_score = ranking_score if ranking_score is not None else None
                
                # Extract per-residue pLDDT from structure B-factors (works for both PDB and CIF via Biotite)
                from .structure_utils import get_residue_plddt
                _, residue_plddt = get_residue_plddt(structure_path)
                
                # Create design
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),  # Can be .cif or .pdb
                    json_path=str(json_file),
                    
                    # Metrics
                    plddt_overall=safe_float(plddt),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    conf_score=safe_float(conf_score),
                    residue_plddt=residue_plddt,
                    
                    # Defaults for others
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)
                
            except Exception as e:
                print(f"[Ingester] Error parsing RF3 file {json_file}: {e}")
                
    # If still no designs, try just finding PDBs (e.g. valid job but missing metadata)
    if designs_created == 0:
        print("[Ingester] No JSON metrics found. Scanning for raw PDB files...")

        for search_dir in search_paths:
            if not search_dir.exists():
                continue
                
            for pdb_file in search_dir.glob("*.pdb"):
                design_name = pdb_file.stem
                if design_name in ingested_names:
                    continue
                    
                # Calculate pLDDT from structure
                plddt, residue_plddt = extract_plddt_from_pdb(pdb_file)
                    
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(pdb_file),
                    json_path=None,
                    
                    # Store extracted pLDDT (both average and per-residue)
                    plddt_overall=plddt,
                    residue_plddt=residue_plddt,
                    
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

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


def extract_plddt_from_pdb(pdb_path):
    """
    Extract pLDDT from structure B-factors.
    Supports both PDB and CIF files via Biotite, with fallback to manual parsing.
    Returns (avg_plddt, per_residue_array).
    """
    path = Path(pdb_path) if not isinstance(pdb_path, Path) else pdb_path
    
    # Try Biotite first (handles PDB and CIF)
    try:
        from .structure_utils import get_residue_plddt
        avg_plddt, per_residue = get_residue_plddt(path)
        if avg_plddt is not None:
            return avg_plddt, per_residue
    except ImportError:
        pass  # Biotite not available, fall through to manual
    except Exception as e:
        print(f"[Ingester] Biotite extraction failed for {path}, trying manual: {e}")
    
    # Fallback: Manual PDB parsing (only works for .pdb files)
    if not str(path).lower().endswith('.pdb'):
        print(f"[Ingester] Cannot manually parse non-PDB file: {path}")
        return None, None
        
    try:
        residue_scores = []  # One score per residue (CA atom)
        all_scores = []  # All atom scores for average
        
        with open(path, 'r') as f:
            for line in f:
                if line.startswith("ATOM  ") or line.startswith("HETATM"):
                    # B-factor is columns 61-66 (1-indexed) -> 60-66 (0-indexed)
                    try:
                        bfactor = float(line[60:66].strip())
                        all_scores.append(bfactor)
                        
                        # Extract CA atoms only for per-residue (one per residue)
                        atom_name = line[12:16].strip()
                        if atom_name == "CA":
                            residue_scores.append(round(bfactor, 2))
                    except ValueError:
                        pass
        
        avg_plddt = sum(all_scores) / len(all_scores) if all_scores else None
        per_residue = residue_scores if residue_scores else None
        
        return avg_plddt, per_residue
    except Exception:
        return None, None


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
