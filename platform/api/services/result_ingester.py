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
from typing import Optional, Dict, List, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import Design, Job
from paths import get_data_root
from .structure_utils import calculate_epitope_contacts


def _summarize_frustration_rows(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None

    pos_values: Dict[tuple[int, str], List[float]] = {}
    for row in rows:
        try:
            position = int(row["position"])
            chain = str(row["chain"])
            value = float(row["frustration_pred"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (position, chain)
        pos_values.setdefault(key, []).append(value)

    if not pos_values:
        return None

    residues = []
    high_count = 0
    min_count = 0
    for (pos, chain), values in sorted(pos_values.items(), key=lambda item: (item[0][1], item[0][0])):
        frust = sum(values) / len(values)
        if frust <= -1.0:
            frust_class = "high"
            high_count += 1
        elif frust >= 0.58:
            frust_class = "min"
            min_count += 1
        else:
            frust_class = "neutral"
        residues.append({
            "pos": pos,
            "chain": chain,
            "frust": round(float(frust), 3),
            "frustClass": frust_class,
        })

    total = len(pos_values)
    pct_high = round(high_count / total * 100, 1) if total > 0 else 0.0
    return {
        "high_count": high_count,
        "min_count": min_count,
        "pct_high": pct_high,
        "residues": residues,
    }


def _normalize_frustration_target_name(value: str) -> str:
    raw = str(value).strip()
    return Path(raw).stem if raw else raw


def extract_frustration_targets(csv_path: Path) -> List[str]:
    """
    Return distinct design/PDB identifiers embedded in a frustration CSV.

    FrustraMPNN can emit one CSV per structure or a batch CSV with a `pdb` column.
    """
    try:
        import pandas as pd

        df = pd.read_csv(csv_path, usecols=lambda c: c in {"pdb"})
        if "pdb" not in df.columns:
            return []
        seen: List[str] = []
        for value in df["pdb"].dropna().astype(str).tolist():
            normalized = _normalize_frustration_target_name(value)
            if normalized and normalized not in seen:
                seen.append(normalized)
        return seen
    except ImportError:
        pass
    except Exception as e:
        print(f"[Ingester] Error extracting frustration targets from {csv_path}: {e}")
        return []

    try:
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            if "pdb" not in (reader.fieldnames or []):
                return []
            seen: List[str] = []
            for row in reader:
                value = row.get("pdb")
                if not value:
                    continue
                normalized = _normalize_frustration_target_name(value)
                if normalized and normalized not in seen:
                    seen.append(normalized)
            return seen
    except Exception as e:
        print(f"[Ingester] Error extracting frustration targets without pandas from {csv_path}: {e}")
        return []


def parse_frustration_csv(csv_path: Path, pdb_name_filter: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Parse FrustraMPNN output CSV into structured frustration data.
    
    CSV format: position,chain,frustration_pred (multiple rows per position due to ensemble)
    
    Returns:
        dict with keys:
            - high_count: int (residues with frust <= -1.0)
            - min_count: int (residues with frust >= 0.58)
            - pct_high: float (percent highly frustrated)
            - residues: list of dicts with {pos, chain, frust, frustClass}
    """
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)

        if pdb_name_filter and "pdb" in df.columns:
            target = _normalize_frustration_target_name(pdb_name_filter)
            df = df[df["pdb"].astype(str).map(_normalize_frustration_target_name) == target]
            if df.empty:
                return None

        rows = df[["position", "chain", "frustration_pred"]].to_dict("records")
        return _summarize_frustration_rows(rows)
    except ImportError:
        # Fallback without pandas
        try:
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                rows = []
                target = _normalize_frustration_target_name(pdb_name_filter) if pdb_name_filter else None
                for row in reader:
                    if target and row.get("pdb"):
                        if _normalize_frustration_target_name(row["pdb"]) != target:
                            continue
                    rows.append(row)

                return _summarize_frustration_rows(rows)
        except Exception as e:
            print(f"[Ingester] Error parsing frustration CSV without pandas: {e}")
            return None
    except Exception as e:
        print(f"[Ingester] Error parsing frustration CSV: {e}")
        return None


def parse_backbone_id(design_name: str) -> Optional[int]:
    """
    Extract backbone ID from design name.
    
    Formats:
    - antibody_job_2_seq_15_model_0 -> 2
    - boltzgen_input_5 -> 5
    - rfd_design_3 -> 3
    """
    import re
    
    # Pattern: job_X, input_X, design_X
    match = re.search(r'(?:job|input|design)[_-](\d+)', design_name)
    if match:
        return int(match.group(1))
    return None


def _parse_job_params(raw_params: Any) -> Dict[str, Any]:
    if isinstance(raw_params, dict):
        return raw_params
    if isinstance(raw_params, str) and raw_params:
        try:
            parsed = json.loads(raw_params)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _parse_epitope_residues(raw_value: Any) -> Optional[List[str]]:
    if not raw_value:
        return None
    if isinstance(raw_value, list):
        cleaned = [str(item).strip() for item in raw_value if str(item).strip()]
        return cleaned or None
    if isinstance(raw_value, str):
        cleaned = [item.strip() for item in raw_value.split(",") if item.strip()]
        return cleaned or None
    return None


def _parse_custom_cdr_lengths(job_params: Dict[str, Any]) -> Dict[str, int]:
    loops_raw = job_params.get("antibody_design_loops") or ""
    custom_raw = job_params.get("rfantibody_design_loops_custom")
    if not loops_raw or not custom_raw:
        return {}

    if isinstance(loops_raw, str):
        loop_names = [item.strip() for item in loops_raw.split(",") if item.strip()]
    elif isinstance(loops_raw, list):
        loop_names = [str(item).strip() for item in loops_raw if str(item).strip()]
    else:
        loop_names = []

    if isinstance(custom_raw, str):
        custom_text = custom_raw.strip().strip("[]")
        custom_ranges = [item.strip() for item in custom_text.split(",") if item.strip()]
    elif isinstance(custom_raw, list):
        custom_ranges = [str(item).strip() for item in custom_raw if str(item).strip()]
    else:
        custom_ranges = []

    if not loop_names or len(loop_names) != len(custom_ranges):
        return {}

    import re

    lengths: Dict[str, int] = {}
    for loop_name, raw_range in zip(loop_names, custom_ranges):
        match = re.match(r"^[A-Za-z](\d+)-(\d+)$", raw_range)
        if not match:
            continue
        start = int(match.group(1))
        end = int(match.group(2))
        if end >= start:
            lengths[loop_name] = end - start + 1
    return lengths


async def ingest_job_results(
    job_id: str, 
    output_dir: str, 
    session: AsyncSession,
    epitope_residues: Optional[list] = None
) -> int:
    """
    Parse pipeline outputs and populate Design table.
    
    Args:
        job_id: The job ID to associate designs with
        output_dir: Path to the job's output directory (e.g., legacy bms_results/job_xxx)
        session: Async database session
        epitope_residues: Optional list of epitope residues (e.g., ["A111", "A112"])
            for calculating contact metrics
        
    Returns:
        Number of designs ingested
    """
    # Resolve relative paths to absolute using data root
    output_path = Path(output_dir)
    if not output_path.is_absolute():
        output_path = get_data_root() / output_dir

    if not output_path.exists():
        print(f"[Ingester] Output dir not found: {output_path}")
        return 0
    
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
                    design_name = row.get('description', f'design_{designs_created}')
                    design = Design(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        name=design_name,
                        pdb_path=find_pdb_path(output_path, design_name),
                        json_path=None,  # Could add if needed
                        
                        # Backbone grouping
                        backbone_id=parse_backbone_id(design_name),
                        
                        # Structural metrics (predicted structures)
                        num_helices=safe_int(row.get('pr_helices')),
                        num_strands=safe_int(row.get('pr_strands')),
                        rog=safe_float(row.get('pr_RoG')),
                        # RFdiffusion backbone metrics
                        rfd_rog=safe_float(row.get('rfd_RoG')),
                        
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

    # Post-ingestion: Check for FrustraMPNN results and attach to designs
    if designs_created > 0:
        await ingest_frustration_data(job_id, output_path, session)
        await ingest_maturation_data(job_id, output_path, session)

    return designs_created


async def ingest_maturation_data(
    job_id: str,
    output_path: Path,
    session: AsyncSession
) -> int:
    """
    Parse PPIFlow maturation score JSONs and update matching designs.
    
    Maturation score JSONs are in {output_dir}/run/ppiflow/results/{name}_maturation_score.json
    Each contains: delta_interface_score, interface_score_matured, rmsd_backbone, sequence_identity, clash_count_ca
    """
    # Check multiple possible locations (maturation_child publishes to run/ppiflow/results/)
    maturation_dirs = [
        output_path / "run" / "ppiflow" / "results",
        output_path / "ppiflow" / "results",
        output_path,
    ]
    
    score_files = []
    for d in maturation_dirs:
        if d.exists():
            score_files.extend(d.glob("*_maturation_score.json"))
    
    if not score_files:
        return 0
    
    # Deduplicate by filename (same file may appear in multiple search paths)
    seen = set()
    unique_files = []
    for f in score_files:
        if f.name not in seen:
            seen.add(f.name)
            unique_files.append(f)
    score_files = unique_files
    
    print(f"[Ingester] Found {len(score_files)} maturation score JSONs to process")
    
    updated_count = 0
    
    for json_path in score_files:
        # Extract design name: "designname_maturation_score.json" -> "designname"
        design_name = json_path.stem.replace("_maturation_score", "")
        
        try:
            import json as json_mod
            data = json_mod.loads(json_path.read_text())
        except Exception as e:
            print(f"[Ingester] Error parsing maturation JSON {json_path}: {e}")
            continue
        
        # Find matching design in DB (try both with and without job_id for child jobs)
        result = await session.execute(
            select(Design).where(
                Design.job_id == job_id,
                Design.name == design_name
            )
        )
        design = result.scalar_one_or_none()
        
        if not design:
            # Try matching by name only across child jobs
            from database import Job
            child_result = await session.execute(
                select(Job.id).where(Job.parent_job_id == job_id)
            )
            child_ids = [row[0] for row in child_result.all()]
            if child_ids:
                all_ids = [job_id] + child_ids
                result = await session.execute(
                    select(Design).where(
                        Design.job_id.in_(all_ids),
                        Design.name == design_name
                    )
                )
                design = result.scalar_one_or_none()
        
        if not design:
            continue
        
        # Update design with maturation metrics
        delta = data.get("delta_interface_score")
        matured = data.get("interface_score_matured")
        rmsd_bb = data.get("rmsd_backbone")
        
        if delta is not None:
            design.maturation_delta_interface = float(delta)
        if matured is not None:
            design.maturation_interface_score = float(matured)
        if rmsd_bb is not None:
            design.maturation_rmsd = float(rmsd_bb)
        
        updated_count += 1
    
    if updated_count > 0:
        await session.commit()
        print(f"[Ingester] Updated {updated_count} designs with maturation metrics")
    
    return updated_count


async def ingest_frustration_data(
    job_id: str,
    output_path: Path,
    session: AsyncSession
) -> int:
    """
    Parse FrustraMPNN output CSVs and update matching designs with frustration data.
    
    FrustraMPNN outputs are in {output_dir}/frustration/{design_name}_frustration.csv
    """
    frustration_dir = output_path / "frustration"
    if not frustration_dir.exists():
        print(f"[Ingester] No frustration directory found at {frustration_dir}")
        return 0
    
    # Find all frustration CSVs
    frustration_csvs = list(frustration_dir.glob("*_frustration.csv"))
    if not frustration_csvs:
        print(f"[Ingester] No frustration CSV files found in {frustration_dir}")
        return 0
    
    print(f"[Ingester] Found {len(frustration_csvs)} frustration CSVs to process")
    
    child_result = await session.execute(
        select(Job.id).where(Job.parent_job_id == job_id)
    )
    child_ids = [row[0] for row in child_result.all()]
    design_job_ids = [job_id] + child_ids

    async def find_matching_design(design_token: str) -> Optional[Design]:
        normalized = _normalize_frustration_target_name(design_token)
        if not normalized:
            return None

        candidate_names = [normalized]
        exact_result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.name.in_(candidate_names)
            )
        )
        design = exact_result.scalar_one_or_none()
        if design:
            return design

        prefix_result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.name.like(f"{normalized}%")
            )
        )
        design = prefix_result.scalars().first()
        if design:
            return design

        pdb_result = await session.execute(
            select(Design).where(
                Design.job_id.in_(design_job_ids),
                Design.pdb_path.like(f"%/{normalized}.pdb")
            )
        )
        return pdb_result.scalars().first()

    updated_count = 0
    
    for csv_path in frustration_csvs:
        csv_targets = extract_frustration_targets(csv_path)
        target_names = csv_targets or [csv_path.stem.replace("_frustration", "")]

        for target_name in target_names:
            design = await find_matching_design(target_name)
            if not design:
                print(f"[Ingester] No matching design for frustration target: {target_name}")
                continue

            frust_data = parse_frustration_csv(csv_path, pdb_name_filter=target_name if csv_targets else None)
            if not frust_data:
                print(f"[Ingester] Failed to parse frustration CSV: {csv_path} (target={target_name})")
                continue

            design.frustration_high_count = frust_data['high_count']
            design.frustration_min_count = frust_data['min_count']
            design.frustration_pct_high = frust_data['pct_high']
            design.frustration_residues = frust_data['residues']
            design.frustration_csv_path = str(csv_path)

            updated_count += 1
            print(
                f"[Ingester] Updated {design.name} with frustration data: "
                f"{frust_data['high_count']} high, {frust_data['min_count']} min"
            )
    
    if updated_count > 0:
        await session.commit()
        print(f"[Ingester] Updated {updated_count} designs with frustration data")
    
    return updated_count


async def ingest_loose_files(
    job_id: str, 
    output_path: Path, 
    session: AsyncSession
) -> int:
    """Ingest designs from individual JSON/PDB files (fallback)."""
    
    job_params: Dict[str, Any] = {}
    job_result = await session.execute(select(Job.params).where(Job.id == job_id))
    raw_job_params = job_result.scalar_one_or_none()
    job_params = _parse_job_params(raw_job_params)

    epitope_residues = _parse_epitope_residues(
        job_params.get("epitope_residues") or job_params.get("selected_residues")
    )
    custom_cdr_lengths = _parse_custom_cdr_lengths(job_params)
    
    # Locations to search for confidence/metrics JSONs
    # Boltz outputs often in pdb_files/predictions/
    # RF3 outputs in pdb_files/rf3/output/*/
    search_paths = [
        output_path / "pdb_files" / "predictions",
        output_path / "pdb_files" / "validated_designs",
        output_path / "pdb_files",
        output_path / "collected",
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

    # Protenix outputs: predictions/{design_name}/ containing .cif + confidence.json
    protenix_base = output_path / "pdb_files" / "predictions"
    if not protenix_base.exists():
        protenix_base = output_path / "run" / "protenix" / "predictions"
    protenix_run_base = output_path / "run" / "protenix_complex" / "predictions"
    for pbase in [protenix_base, protenix_run_base]:
        if pbase.exists():
            for subdir in pbase.iterdir():
                if subdir.is_dir():
                    search_paths.append(subdir)
    
    designs_created = 0
    
    # Track ingested names to avoid duplicates
    ingested_names = set()
    
    print(f"[Ingester DEBUG] Search paths: {[str(p) for p in search_paths]}")

    for search_dir in search_paths:
        if not search_dir.exists():
            continue
        
        recursive_scan = search_dir.name in {"pdb_files", "validated_designs", "collected"} or "collected" in search_dir.parts
        json_files = set(list(search_dir.rglob("confidence_*.json")) if recursive_scan else list(search_dir.glob("confidence_*.json")))
        boltz_aligned_jsons = list(search_dir.rglob("*_boltzpred.json")) if recursive_scan else list(search_dir.glob("*_boltzpred.json"))
        json_files.update(boltz_aligned_jsons)
        json_files = sorted(json_files)
        print(f"[Ingester DEBUG] {search_dir}: {len(json_files)} confidence JSONs found")
        
        # BOLTZ2: Look for confidence_*.json patterns
        for json_file in json_files:
            try:
                raw_name = json_file.stem
                if raw_name.endswith("_boltzpred"):
                    design_name = raw_name.replace("_boltzpred", "")
                else:
                    design_name = raw_name.replace("confidence_", "")
                
                # Skip input templates (no numeric suffix) - these are not actual designs
                # Actual designs are named like: boltzgen_input_0, boltzgen_input_1, etc.
                import re
                if not re.search(r'_\d+$', design_name):
                    print(f"[Ingester] Skipping input template: {design_name}")
                    continue
                
                if design_name in ingested_names:
                    continue
                
                # Look for corresponding Structure (CIF preferred for complexes, PDB fallback)
                structure_candidates = [
                    search_dir / f"{design_name}.cif",
                    output_path / "pdb_files" / f"{design_name}.cif",
                    search_dir / f"{raw_name}.pdb",
                    search_dir / f"{design_name}_boltzpred.pdb",
                    search_dir / f"{design_name}.pdb",
                    output_path / "pdb_files" / f"{raw_name}.pdb",
                    output_path / "pdb_files" / f"{design_name}_boltzpred.pdb",
                    output_path / "pdb_files" / f"{design_name}.pdb",
                    output_path / "pdb_files" / "predictions" / f"{raw_name}.pdb",
                    output_path / "pdb_files" / "predictions" / f"{design_name}_boltzpred.pdb",
                    output_path / "pdb_files" / "predictions" / f"{design_name}.pdb",
                ]
                structure_path = next((candidate for candidate in structure_candidates if candidate.exists()), None)

                if structure_path is None:
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
                
                # NEW: Extract interface metrics
                iptm = metrics.get('iptm')
                protein_iptm = metrics.get('protein_iptm')
                complex_iplddt = metrics.get('complex_iplddt')
                complex_ipde = metrics.get('complex_ipde')
                chains_ptm = metrics.get('chains_ptm')  # dict: {"0": 0.76, "1": 0.51}
                pair_chains_iptm = metrics.get('pair_chains_iptm')  # NxN matrix
                has_clash_raw = metrics.get('full_has_clash')
                if has_clash_raw is None:
                    has_clash_raw = metrics.get('has_clash')
                disorder = metrics.get('disorder') or metrics.get('full_disorder_prob_mean')
                num_recycles = metrics.get('num_recycles')
                rmsd_overall = metrics.get('rmsd_overall') or metrics.get('boltz_overall_rmsd')
                rmsd_binder = metrics.get('rmsd_binder') or metrics.get('boltz_binder_rmsd')

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
                
                # Calculate epitope contacts if epitope_residues provided
                epitope_contact_count = None
                epitope_min_distance = None
                if epitope_residues and structure_path:
                    epitope_contact_count, epitope_min_distance = calculate_epitope_contacts(
                        structure_path, 
                        epitope_residues,
                        antibody_chain="A",  # RFantibody outputs antibody as chain A
                        target_chain="B"     # Target as chain B
                    )
                
                # Create design
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(json_file),
                    
                    # Backbone grouping
                    backbone_id=parse_backbone_id(design_name),
                    
                    # Epitope contact metrics
                    epitope_contact_count=epitope_contact_count,
                    epitope_min_distance=epitope_min_distance,
                    
                    # Metrics
                    plddt_overall=safe_float(plddt),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    conf_score=safe_float(conf_score),
                    ligand_iptm=safe_float(ligand_iptm),
                    rmsd_overall=safe_float(rmsd_overall),
                    rmsd_binder=safe_float(rmsd_binder),
                    affinity_score=safe_float(affinity_score),
                    binder_probability=safe_float(binder_probability),
                    residue_plddt=residue_plddt,
                    
                    # NEW: Interface metrics
                    iptm=safe_float(iptm),
                    protein_iptm=safe_float(protein_iptm),
                    complex_iplddt=safe_float(complex_iplddt),
                    complex_ipde=safe_float(complex_ipde),
                    chains_ptm=chains_ptm,
                    pair_chains_iptm=pair_chains_iptm,
                    disorder=safe_float(disorder),
                    num_recycles=safe_int(num_recycles),
                    has_clash=(bool(has_clash_raw) if has_clash_raw is not None else None),
                    confidence_metrics=metrics,
                    
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
        rf3_jsons = list(search_dir.rglob("*_summary_confidences.json")) if recursive_scan else list(search_dir.glob("*_summary_confidences.json"))
        for json_file in rf3_jsons:
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
                    
                    # Backbone grouping
                    backbone_id=parse_backbone_id(design_name),
                    
                    # Metrics
                    plddt_overall=safe_float(plddt),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    iptm=safe_float(iptm),  # NEW: Store RF3 iptm
                    conf_score=safe_float(conf_score),
                    residue_plddt=residue_plddt,
                    confidence_metrics=metrics,
                    
                    # Defaults for others
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)
                
            except Exception as e:
                print(f"[Ingester] Error parsing RF3 file {json_file}: {e}")

        # PROTENIX: Current CLI emits *_summary_confidence_sample_*.json alongside
        # *_sample_*.cif in predictions/. Keep legacy confidence.json parsing too.
        protenix_jsons = set()
        if search_dir.name in {"pdb_files", "predictions", "validated_designs", "collected"}:
            for json_path in search_dir.rglob("*_summary_confidence_sample_*.json"):
                protenix_jsons.add(json_path)
            for sub in search_dir.iterdir():
                if sub.is_dir():
                    conf_json = sub / "confidence.json"
                    if conf_json.exists():
                        protenix_jsons.add(conf_json)
        else:
            for json_path in search_dir.glob("*_summary_confidence_sample_*.json"):
                protenix_jsons.add(json_path)
            conf_json = search_dir / "confidence.json"
            if conf_json.exists():
                protenix_jsons.add(conf_json)

        for json_file in sorted(protenix_jsons):
            try:
                structure_path = None
                design_name = json_file.parent.name
                stem = json_file.stem

                # New format:
                #   <name>_summary_confidence_sample_<rank>.json
                #   <name>_sample_<rank>.cif
                if "_summary_confidence_sample_" in stem:
                    base_name, sample_rank = stem.rsplit("_summary_confidence_sample_", 1)
                    design_name = f"{base_name}_sample_{sample_rank}"
                    candidate = json_file.with_name(f"{design_name}.cif")
                    if not candidate.exists():
                        candidate = json_file.with_name(f"{design_name}.pdb")
                    if candidate.exists():
                        structure_path = candidate

                if design_name in ingested_names:
                    continue

                # Legacy format: confidence.json in per-sample subdir
                if structure_path is None:
                    cif_files = list(json_file.parent.glob("*.cif"))
                    if cif_files:
                        structure_path = cif_files[0]
                    else:
                        pdb_files = list(json_file.parent.glob("*.pdb"))
                        if not pdb_files:
                            print(f"[Ingester] No CIF/PDB found for Protenix design {design_name}")
                            continue
                        structure_path = pdb_files[0]

                # Read Protenix confidence metrics
                with open(json_file, 'r') as f:
                    metrics = json.load(f)

                # Protenix confidence keys vary across releases:
                #   current: plddt/ptm/iptm/gpde/chain_*/*_iptm/ranking_score/has_clash
                #   legacy: complex_plddt/complex_pde/...
                plddt = (
                    metrics.get('full_plddt')
                    or metrics.get('complex_plddt')
                    or metrics.get('plddt')
                )
                if plddt is not None and plddt <= 1.0:
                    plddt = plddt * 100.0

                conf_score = metrics.get('ranking_score') or metrics.get('confidence_score')
                ptm = metrics.get('full_ptm') or metrics.get('ptm')
                iptm = metrics.get('full_iptm') or metrics.get('iptm')
                protein_iptm = metrics.get('protein_iptm')
                ligand_iptm = metrics.get('ligand_iptm')
                complex_iplddt = metrics.get('complex_iplddt')
                complex_ipde = (
                    metrics.get('complex_ipde')
                    or metrics.get('gpde')
                    or metrics.get('complex_pde')
                )
                chains_ptm = metrics.get('chain_ptm') or metrics.get('chains_ptm')
                pair_chains_iptm = metrics.get('chain_pair_iptm') or metrics.get('pair_chains_iptm')
                chain_plddt = metrics.get('chain_plddt')
                has_clash = metrics.get('full_has_clash')
                if has_clash is None:
                    has_clash = metrics.get('has_clash')
                disorder = metrics.get('disorder')
                if disorder is None:
                    disorder = metrics.get('full_disorder_prob_mean')
                num_recycles = metrics.get('num_recycles')
                rmsd_overall = metrics.get('rmsd_overall') or metrics.get('protenix_overall_rmsd')
                rmsd_binder = metrics.get('rmsd_binder') or metrics.get('protenix_binder_rmsd')

                pae = metrics.get('complex_pae') or metrics.get('pae') or metrics.get('gpde')
                if pae is None:
                    pde = metrics.get('complex_pde')
                    if pde is not None:
                        pae = pde

                # Extract per-residue pLDDT from CIF B-factors
                _, residue_plddt = extract_plddt_from_pdb(structure_path)

                plddt_binder = None
                plddt_target = None
                if isinstance(chain_plddt, list) and len(chain_plddt) >= 2:
                    plddt_binder = chain_plddt[0]
                    plddt_target = chain_plddt[1]
                    if plddt_binder is not None and plddt_binder <= 1.0:
                        plddt_binder *= 100.0
                    if plddt_target is not None and plddt_target <= 1.0:
                        plddt_target *= 100.0

                epitope_contact_count = None
                epitope_min_distance = None
                if epitope_residues and structure_path:
                    epitope_contact_count, epitope_min_distance = calculate_epitope_contacts(
                        structure_path,
                        epitope_residues,
                        antibody_chain="A",
                        target_chain="B",
                    )

                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=str(json_file),

                    backbone_id=parse_backbone_id(design_name),
                    epitope_contact_count=epitope_contact_count,
                    epitope_min_distance=epitope_min_distance,

                    plddt_overall=safe_float(plddt),
                    plddt_binder=safe_float(plddt_binder),
                    plddt_target=safe_float(plddt_target),
                    pae_overall=safe_float(pae),
                    ptm=safe_float(ptm),
                    iptm=safe_float(iptm),
                    protein_iptm=safe_float(protein_iptm),
                    rmsd_overall=safe_float(rmsd_overall),
                    rmsd_binder=safe_float(rmsd_binder),
                    conf_score=safe_float(conf_score),
                    ligand_iptm=safe_float(ligand_iptm),
                    complex_iplddt=safe_float(complex_iplddt),
                    complex_ipde=safe_float(complex_ipde),
                    chains_ptm=chains_ptm,
                    pair_chains_iptm=pair_chains_iptm,
                    residue_plddt=residue_plddt,
                    cdr_h1_length=custom_cdr_lengths.get("H1"),
                    cdr_h2_length=custom_cdr_lengths.get("H2"),
                    cdr_h3_length=custom_cdr_lengths.get("H3"),
                    cdr_l1_length=custom_cdr_lengths.get("L1"),
                    cdr_l2_length=custom_cdr_lengths.get("L2"),
                    cdr_l3_length=custom_cdr_lengths.get("L3"),
                    disorder=safe_float(disorder),
                    num_recycles=safe_int(num_recycles),
                    has_clash=(bool(has_clash) if has_clash is not None else None),
                    confidence_metrics=metrics,

                    is_favorite=False,
                    created_at=datetime.utcnow()
                )

                # Store clash info in notes if present
                if has_clash:
                    design.notes = 'steric_clash_detected'

                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

            except Exception as e:
                print(f"[Ingester] Error parsing Protenix file {json_file}: {e}")
                
    # If still no designs, try just finding raw structures (e.g. valid job but missing metadata)
    if designs_created == 0:
        print("[Ingester] No JSON metrics found. Scanning for raw structure files...")

        # Determine job type for model-specific ingestion logic
        job_model_id = None
        try:
            job_result = await session.execute(select(Job.model_id).where(Job.id == job_id))
            job_model_id = job_result.scalar_one_or_none()
        except Exception:
            pass
        is_oligo = (job_model_id or "").lower() in ("oligo_design", "oligo_designer")

        # --- Oligo-specific ingestion ---
        # For oligo_design jobs: ONLY ingest from run/rebuilt/ (full-atom PDBs).
        # Do NOT fallback to rglob which grabs backbone PDBs from run/rfdpoly/ and run/nampnn/.
        if is_oligo:
            print("[Ingester] Oligo design job detected — using oligo-specific ingestion")
            structure_paths = []
            rebuilt_dir = output_path / "run" / "rebuilt"
            if rebuilt_dir.exists():
                structure_paths.extend(list(rebuilt_dir.glob("out_*.pdb")))
                # Also check nested rebuilt/rebuilt/ (older publishDir layout)
                nested = rebuilt_dir / "rebuilt"
                if nested.exists():
                    structure_paths.extend(list(nested.glob("out_*.pdb")))
                print(f"[Ingester] Found {len(structure_paths)} rebuilt PDBs in {rebuilt_dir}")
            
            if not structure_paths:
                print(f"[Ingester] No rebuilt PDBs found for oligo job under {rebuilt_dir}")

            # Parse NA-MPNN quality metrics from nampnn_metrics.json
            nampnn_design_metrics = {}
            for metrics_path in [
                output_path / "run" / "nampnn" / "nampnn_metrics.json",
                output_path / "run" / "rebuilt" / "rebuild_metrics.json",
            ]:
                if metrics_path.exists():
                    try:
                        with open(metrics_path) as f:
                            parsed = json.load(f)
                        # nampnn_metrics.json has a 'designs' list with per-design metrics
                        if "designs" in parsed:
                            for d in parsed["designs"]:
                                conf = d.get("overall_confidence")
                                rec = d.get("seq_rec")
                                header = d.get("header", "")
                                if conf is not None or rec is not None:
                                    nampnn_design_metrics[header] = {
                                        "overall_confidence": conf,
                                        "seq_rec": rec,
                                    }
                        # rebuild_metrics.json has 'nampnn_metrics' dict
                        if "nampnn_metrics" in parsed:
                            for key, metrics in parsed["nampnn_metrics"].items():
                                nampnn_design_metrics[key] = metrics
                        print(f"[Ingester] Parsed {len(nampnn_design_metrics)} design metrics from {metrics_path.name}")
                    except Exception as e:
                        print(f"[Ingester] Warning: could not parse {metrics_path}: {e}")

            for structure_path in structure_paths:
                design_name = structure_path.stem
                if design_name in ingested_names:
                    continue
                    
                # For oligo jobs: B-factors contain NA-MPNN design confidence (not pLDDT)
                # Extract them but label correctly
                bfactor_avg, residue_bfactors = extract_plddt_from_pdb(structure_path)
                
                # Look up NA-MPNN metrics for this design
                overall_confidence = None
                seq_rec = None
                for key, metrics in nampnn_design_metrics.items():
                    if design_name.replace("out_", "") in key or key in design_name:
                        overall_confidence = metrics.get("overall_confidence")
                        seq_rec = metrics.get("seq_rec")
                        break
                
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=None,
                    
                    backbone_id=parse_backbone_id(design_name),
                    
                    # For oligo: B-factors are NA-MPNN design confidence, NOT pLDDT
                    # Store in plddt_overall for viewer compatibility but note it's design confidence
                    plddt_overall=bfactor_avg if bfactor_avg and bfactor_avg > 0 else None,
                    residue_plddt=residue_bfactors,
                    
                    # NA-MPNN quality metrics
                    conf_score=overall_confidence,  # overall_confidence from FASTA header
                    mpnn_score=seq_rec,  # sequence recovery from FASTA header
                    
                    is_favorite=False,
                    created_at=datetime.utcnow()
                )
                session.add(design)
                designs_created += 1
                ingested_names.add(design_name)

        # --- Standard (non-oligo) ingestion ---
        else:
            structure_paths = []
            
            # For non-oligo jobs: prefer run/rebuilt/ over raw structures
            rebuilt_dir = output_path / "run" / "rebuilt"
            if rebuilt_dir.exists():
                structure_paths.extend(list(rebuilt_dir.glob("*.pdb")))
                nested = rebuilt_dir / "rebuilt"
                if nested.exists():
                    structure_paths.extend(list(nested.glob("*.pdb")))
                print(f"[Ingester] Found {len(structure_paths)} rebuilt PDBs in {rebuilt_dir}")
            
            if not structure_paths:
                structure_paths.extend(list(output_path.rglob("*.pdb")))
                structure_paths.extend(list(output_path.rglob("*.cif")))
                structure_paths.extend(list(output_path.rglob("*.mmcif")))

            if not structure_paths:
                print(f"[Ingester] No raw structures found under {output_path}")

            for structure_path in structure_paths:
                design_name = structure_path.stem
                if design_name in ingested_names:
                    continue
                    
                # Calculate pLDDT from structure (supports PDB/CIF)
                plddt, residue_plddt = extract_plddt_from_pdb(structure_path)
                epitope_contact_count = None
                epitope_min_distance = None
                if epitope_residues and structure_path:
                    epitope_contact_count, epitope_min_distance = calculate_epitope_contacts(
                        structure_path,
                        epitope_residues,
                        antibody_chain="A",
                        target_chain="B",
                    )
                    
                design = Design(
                    id=str(uuid.uuid4()),
                    job_id=job_id,
                    name=design_name,
                    pdb_path=str(structure_path),
                    json_path=None,
                    
                    backbone_id=parse_backbone_id(design_name),
                    epitope_contact_count=epitope_contact_count,
                    epitope_min_distance=epitope_min_distance,
                    
                    plddt_overall=plddt,
                    residue_plddt=residue_plddt,
                    cdr_h1_length=custom_cdr_lengths.get("H1"),
                    cdr_h2_length=custom_cdr_lengths.get("H2"),
                    cdr_h3_length=custom_cdr_lengths.get("H3"),
                    cdr_l1_length=custom_cdr_lengths.get("L1"),
                    cdr_l2_length=custom_cdr_lengths.get("L2"),
                    cdr_l3_length=custom_cdr_lengths.get("L3"),
                    
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

    if not output_path.exists():
        print(f"[Ingester] Output path missing, skipping extraction: {output_path}")
        return pdb_dir
    
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
        # Check validated designs subdir
        validated_dir = pdb_files / "validated_designs"
        if validated_dir.exists():
            pdb_file = validated_dir / f"{design_name}.pdb"
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
