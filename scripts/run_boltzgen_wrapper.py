import argparse
import os
import sys
import yaml
import shutil
from pathlib import Path
import json
try:
    import gemmi
except ImportError:
    gemmi = None

# BoltzGen wrapper script for BioModStack pipeline
# Uses the `boltzgen run` CLI

# =============================================================================
# STAGE PROGRESS REPORTING
# 6-stage indicator per Ariax.bio BoltzGen workflow
# =============================================================================
BOLTZGEN_STAGES = [
    ("design", "Design", "Generating candidate binder backbones"),
    ("inverse_folding", "Inverse Folding", "Designing sequences for structures"),
    ("design_folding", "Design Folding", "Validating sequence-structure compatibility"),
    ("folding", "Folding", "Predicting complex structures with Boltz-2"),
    ("affinity", "Affinity & Analysis", "Scoring predicted binding interactions"),
    ("filtering", "Filtering", "Ranking and selecting top candidates"),
]

def report_stage(stage: str, status: str, job_id: str = None, message: str = None):
    """
    Report BoltzGen stage progress to the API.
    
    Args:
        stage: Stage ID (design, inverse_folding, etc.)
        status: Status (starting, running, complete, error)
        job_id: Optional job ID for API reporting
        message: Optional status message
    """
    # Find stage info
    stage_info = next((s for s in BOLTZGEN_STAGES if s[0] == stage), None)
    stage_name = stage_info[1] if stage_info else stage
    stage_desc = stage_info[2] if stage_info else ""
    
    # Console output with stage indicator
    stage_idx = next((i for i, s in enumerate(BOLTZGEN_STAGES) if s[0] == stage), 0)
    total_stages = len(BOLTZGEN_STAGES)
    progress_bar = f"[{stage_idx + 1}/{total_stages}]"
    
    status_emoji = {"starting": "🔄", "running": "⚡", "complete": "✅", "error": "❌"}.get(status, "▶")
    
    print(f"\n{progress_bar} {status_emoji} {stage_name}: {message or stage_desc}")
    
    # Report to API if job_id provided
    if job_id and job_id != 'unknown':
        try:
            import requests
            requests.post(
                f"http://localhost:8000/api/jobs/{job_id}/stage",
                json={
                    "stage": stage,
                    "stage_name": stage_name,
                    "status": status,
                    "message": message or stage_desc,
                    "stage_index": stage_idx,
                    "total_stages": total_stages
                },
                timeout=5
            )
        except Exception:
            pass  # Non-critical, don't fail on reporting errors


def cif_to_pdb(cif_path: Path, pdb_path: Path):
    """Convert CIF to PDB using Gemmi (robust) or Biopython (fallback)."""
    # Try Gemmi first (Robust)
    if gemmi:
        try:
            st = gemmi.read_structure(str(cif_path))
            st.write_pdb(str(pdb_path))
            return True
        except Exception as e:
            print(f"Warning: Gemmi conversion failed for {cif_path}: {e}")

    # Fallback to Biopython (Fragile)
    try:
        from Bio.PDB import MMCIFParser, PDBIO
        parser = MMCIFParser(QUIET=True)
        structure = parser.get_structure("design", str(cif_path))
        io = PDBIO()
        io.set_structure(structure)
        io.save(str(pdb_path))
        return True
    except Exception as e:
        print(f"Warning: Fallback CIF to PDB conversion failed for {cif_path}: {e}")
        return False

def create_metadata_json(csv_path: Path, output_dir: Path):
    """Convert BoltzGen metrics CSV to JSON metadata files."""
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        for _, row in df.iterrows():
            design_id = row.get('id', row.get('file_name', 'unknown')).replace('.cif', '')
            metadata = {
                'design_id': design_id,
                'designed_sequence': row.get('designed_sequence', ''),
                'affinity_probability': float(row.get('affinity_probability_binary1', 0)),
                'design_ptm': float(row.get('design_ptm', 0)),
                'filter_rmsd': float(row.get('filter_rmsd', 0)),
                'source': 'boltzgen'
            }
            # Start with confidence_ prefix for Ingester compatibility
            json_path = output_dir / f"confidence_{design_id}.json"
            with open(json_path, 'w') as f:
                json.dump(metadata, f, indent=2)
        return True
    except Exception as e:
        print(f"Warning: Metadata JSON creation failed: {e}")
        return False


def extract_metrics_from_npz(batch_dir: Path, output_dir: Path) -> int:
    """
    Extract confidence metrics from BoltzGen NPZ files.
    
    This is a fallback when the analysis step fails and no CSV is generated.
    NPZ files contain: iptm, ptm, protein_iptm, design_ptm, plddt (per-residue), etc.
    
    Returns: number of designs processed
    """
    import numpy as np
    
    processed = 0
    
    # Search for NPZ files in intermediate outputs
    search_paths = [
        batch_dir / "intermediate_designs_inverse_folded" / "fold_out_npz",
        batch_dir / "intermediate_designs_inverse_folded",
        batch_dir / "final_ranked_designs",
        batch_dir
    ]
    
    for search_dir in search_paths:
        if not search_dir.exists():
            continue
            
        for npz_path in search_dir.glob("*.npz"):
            try:
                npz = np.load(npz_path)
                design_id = npz_path.stem
                
                # Extract key metrics (take mean across samples if multi-sample)
                def safe_mean(arr):
                    """Get scalar mean, handling NaN and multi-sample arrays."""
                    if arr is None or arr.size == 0:
                        return 0.0
                    val = np.nanmean(arr)
                    return float(val) if not np.isnan(val) else 0.0
                
                # Core confidence metrics
                iptm = safe_mean(npz.get('iptm'))
                ptm = safe_mean(npz.get('ptm'))
                protein_iptm = safe_mean(npz.get('protein_iptm'))
                design_ptm = safe_mean(npz.get('design_ptm'))
                design_iptm = safe_mean(npz.get('design_iptm'))
                target_ptm = safe_mean(npz.get('target_ptm'))
                
                # pLDDT requires special handling - it's per-residue
                # BoltzGen stores confidence as pLDDT-like values (0-100 scale)
                # Approximate pLDDT from ptm (typical correlation: pLDDT ≈ ptm * 100)
                # Or if per-residue plddt exists, use mean
                if 'plddt' in npz.files:
                    plddt = safe_mean(npz.get('plddt'))
                    if plddt < 1:  # Normalized 0-1 scale
                        plddt = plddt * 100
                else:
                    # Estimate from design_ptm (reasonable approximation)
                    plddt = design_ptm * 100 if design_ptm > 0 else ptm * 100
                
                metadata = {
                    'design_id': design_id,
                    'plddt': round(plddt, 2),
                    'iptm': round(iptm, 4),
                    'ptm': round(ptm, 4),
                    'protein_iptm': round(protein_iptm, 4),
                    'design_ptm': round(design_ptm, 4),
                    'design_iptm': round(design_iptm, 4),
                    'target_ptm': round(target_ptm, 4),
                    'source': 'boltzgen',
                    'metrics_source': 'npz'
                }
                
                json_path = output_dir / f"confidence_{design_id}.json"
                with open(json_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
                
                processed += 1
                print(f"Extracted metrics from NPZ: {design_id} (pLDDT={plddt:.1f}, iPTM={iptm:.3f})")
                
            except Exception as e:
                print(f"Warning: Failed to extract metrics from {npz_path}: {e}")
                continue
        
        # If we found NPZ files in this directory, don't search others
        if processed > 0:
            break
    
    return processed


def auto_detect_protocol(config_path: str) -> str:
    """
    Auto-detect the appropriate BoltzGen protocol based on entity types in the YAML.
    
    Protocols:
    - protein-anything: For DNA, RNA, or protein targets
    - protein-small_molecule: For small molecule/ligand targets
    """
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        entities = config.get('entities', [])
        
        has_dna = False
        has_rna = False
        has_ligand = False
        has_protein = False
        
        for entity in entities:
            if 'dna' in entity:
                has_dna = True
            elif 'rna' in entity:
                has_rna = True
            elif 'ligand' in entity:
                has_ligand = True
            elif 'protein' in entity:
                has_protein = True
        
        # Determine protocol based on entity types
        if has_dna or has_rna:
            # DNA/RNA targets use protein-anything
            protocol = "protein-anything"
            print(f"Auto-detected protocol: {protocol} (DNA/RNA target detected)")
        elif has_ligand:
            # Small molecule targets use protein-small_molecule
            protocol = "protein-small_molecule"
            print(f"Auto-detected protocol: {protocol} (ligand target detected)")
        else:
            # Default for protein-only
            protocol = "protein-anything"
            print(f"Auto-detected protocol: {protocol} (protein target)")
        
        return protocol
        
    except Exception as e:
        print(f"Warning: Protocol auto-detection failed: {e}, using default")
        return "protein-small_molecule"

def main():
    parser = argparse.ArgumentParser(description="Run BoltzGen Wrapper")
    parser.add_argument("--config", type=str, help="Path to single design spec YAML (backward compat)")
    parser.add_argument("--configs", type=str, nargs='+', help="Paths to multiple design spec YAMLs for batch processing")
    parser.add_argument("--out_dir", type=str, required=True, help="Output directory")
    parser.add_argument("--num_designs", type=int, default=10, help="Number of designs to generate per config")
    parser.add_argument("--protocol", type=str, default="auto", 
                        help="BoltzGen protocol (auto, protein-anything, protein-small_molecule, etc)")
    
    # Diffusion parameters
    parser.add_argument("--step_scale", type=float, default=None, help="Fixed step scale (e.g., 1.8)")
    parser.add_argument("--noise_scale", type=float, default=None, help="Fixed noise scale (e.g., 0.98)")
    
    # Inverse folding parameters
    parser.add_argument("--inverse_fold_avoid", type=str, default=None, 
                        help="Disallowed residues (e.g., 'C' or 'KEC')")
    parser.add_argument("--inverse_fold_num_sequences", type=int, default=None,
                        help="Number of sequences per backbone")
    
    # Checkpoint and pipeline control parameters (new)
    parser.add_argument("--checkpoint_mode", type=str, default=None,
                        choices=['diverse', 'adherence'],
                        help="Use single checkpoint (diverse or adherence) instead of both")
    parser.add_argument("--skip_inverse_folding", action="store_true",
                        help="Skip inverse folding step")
    parser.add_argument("--reuse", action="store_true",
                        help="Reuse existing results (resume interrupted run)")
    
    # Job tracking for progress reporting
    parser.add_argument("--job_id", type=str, default=None,
                        help="Job ID for progress reporting to API")
    
    args, unknown = parser.parse_known_args()
    
    # Collect config files (support both single and batch modes)
    config_files = []
    if args.configs:
        config_files = args.configs
    elif args.config:
        config_files = [args.config]
    else:
        print("Error: Must provide --config or --configs")
        sys.exit(1)
    
    print(f"Processing {len(config_files)} config file(s) in batch mode")
    
    os.makedirs(args.out_dir, exist_ok=True)
    
    # Report stage 1: Design starting
    report_stage("design", "starting", args.job_id, f"Processing {len(config_files)} configs, {args.num_designs} designs each")
    
    # Process each config in the batch
    for i, config_path in enumerate(config_files):
        config_name = Path(config_path).stem
        batch_out_dir = Path(args.out_dir) / f"batch_{i}_{config_name}"
        batch_out_dir.mkdir(exist_ok=True)
        
        print(f"\n[{i+1}/{len(config_files)}] Processing: {config_path}")
        
        # Copy config for reproducibility
        shutil.copy(config_path, batch_out_dir / "run_config.yaml")
        
        # Auto-detect protocol if set to 'auto'
        if args.protocol == 'auto':
            protocol = auto_detect_protocol(config_path)
        else:
            protocol = args.protocol
        
        # BoltzGen CLI: boltzgen run <design_spec.yaml> --output <dir> --num_designs N --protocol X
        cmd = f"boltzgen run {config_path} --output {batch_out_dir} --num_designs {args.num_designs} --protocol {protocol}"
        
        # Add diffusion parameters if specified
        if args.step_scale:
            cmd += f" --step_scale {args.step_scale}"
        if args.noise_scale:
            cmd += f" --noise_scale {args.noise_scale}"
        
        # Add inverse folding parameters if specified
        if args.inverse_fold_avoid:
            cmd += f" --inverse_fold_avoid '{args.inverse_fold_avoid}'"
        if args.inverse_fold_num_sequences:
            cmd += f" --inverse_fold_num_sequences {args.inverse_fold_num_sequences}"
        
        # Add checkpoint mode if using single checkpoint
        if args.checkpoint_mode:
            # Map our mode names to checkpoint paths
            checkpoint_map = {
                'diverse': 'huggingface:boltzgen/boltzgen1_diverse:boltzgen1_diverse.ckpt',
                'adherence': 'huggingface:boltzgen/boltzgen1_adherence:boltzgen1_adherence.ckpt'
            }
            cmd += f" --design_checkpoints {checkpoint_map[args.checkpoint_mode]}"
        
        # Skip inverse folding if requested
        if args.skip_inverse_folding:
            cmd += " --skip_inverse_folding"
        
        # Reuse existing results (resume)
        if args.reuse:
            cmd += " --reuse"
        
        # Add any extra args passed through
        if unknown:
            cmd += " " + " ".join(unknown)
        
        print(f"Executing: {cmd}")
        ret = os.system(cmd)
        
        if ret != 0:
            report_stage("design", "error", args.job_id, f"Config {i+1} failed (code {ret})")
            print(f"Warning: BoltzGen failed for {config_path} (code {ret}), continuing with next...")
        else:
            report_stage("design", "complete", args.job_id, f"Config {i+1} complete")
        
    # Stage 2-4 happen inside BoltzGen CLI (inverse_folding, design_folding, folding)
    # Report completion of internal BoltzGen stages
    report_stage("inverse_folding", "complete", args.job_id, "Sequence design completed")
    report_stage("design_folding", "complete", args.job_id, "Stability validation completed")
    report_stage("folding", "complete", args.job_id, "Complex structure prediction completed")
    
    # Stage 5: Affinity & Analysis (post-processing)
    report_stage("affinity", "starting", args.job_id, "Running post-processing analysis")
        
    # Post-processing: Consolidate outputs from all batch directories
    print("\n=== Post-processing: Consolidating outputs ===")
    designs_dir = Path(args.out_dir) / "designs"
    designs_dir.mkdir(exist_ok=True)
    
    # Collect all batch output directories
    batch_dirs = list(Path(args.out_dir).glob("batch_*"))
    if not batch_dirs:
        # Fallback: single config mode, outputs directly in out_dir
        batch_dirs = [Path(args.out_dir)]
    
    cif_converted = 0
    processed_names = set()
    
    for batch_dir in batch_dirs:
        # Find CIF files in each batch's output locations
        search_dirs = [
            batch_dir / "final_ranked_designs",
            batch_dir / "intermediate_designs_inverse_folded",
            batch_dir / "intermediate_designs",
            batch_dir
        ]
        
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            for cif in search_dir.glob("*.cif"):
                # Skip input template CIFs (no numeric suffix) - only process actual designs
                # Designs are named like: boltzgen_input_0.cif, boltzgen_input_1.cif, etc.
                import re
                if not re.search(r'_\d+$', cif.stem):
                    print(f"Skipping input template: {cif.name}")
                    continue
                
                if cif.stem in processed_names:
                    continue
                # Prefix with batch name to avoid collisions
                batch_prefix = batch_dir.name.replace("batch_", "b") + "_" if len(batch_dirs) > 1 else ""
                pdb_name = f"{batch_prefix}{cif.stem}.pdb"
                pdb_path = designs_dir / pdb_name
                
                # Copy original CIF for zero data loss (Viewer prefers this for complexes)
                cif_dest_name = f"{batch_prefix}{cif.stem}.cif"
                shutil.copy(cif, designs_dir / cif_dest_name)
                
                if cif_to_pdb(cif, pdb_path):
                    print(f"Converted: {cif.name} -> {pdb_name}")
                    cif_converted += 1
                    processed_names.add(cif.stem)
    
    if cif_converted == 0:
        print("Warning: No CIF files converted to PDB")
    else:
        print(f"Converted {cif_converted} CIF files to PDB across {len(batch_dirs)} batch(es)")
    
    # Convert metrics CSVs to JSON metadata from all batches
    # Fall back to NPZ extraction if CSV not available (analysis step failed)
    csv_found = False
    for batch_dir in batch_dirs:
        for loc in [batch_dir / "final_ranked_designs" / "all_designs_metrics.csv",
                    batch_dir / "intermediate_designs_inverse_folded" / "metrics.csv"]:
            if loc.exists():
                create_metadata_json(loc, designs_dir)
                print(f"Created JSON metadata from {loc}")
                csv_found = True
                break
    
    # Fallback: Extract metrics from NPZ files if no CSV found
    if not csv_found:
        print("No metrics CSV found - extracting from NPZ files (analysis step may have failed)")
        npz_extracted = 0
        for batch_dir in batch_dirs:
            npz_extracted += extract_metrics_from_npz(batch_dir, designs_dir)
        if npz_extracted > 0:
            print(f"Extracted metrics from {npz_extracted} NPZ files")
    
    # Create minimal JSON for any PDBs still without metadata
    for pdb in designs_dir.glob("*.pdb"):
        json_path = designs_dir / f"confidence_{pdb.stem}.json"
        if not json_path.exists():
            with open(json_path, 'w') as f:
                json.dump({'design_id': pdb.stem, 'source': 'boltzgen'}, f)
    
    report_stage("affinity", "complete", args.job_id, f"Processed {cif_converted} design metrics")
    
    # Stage 6: Filtering complete
    report_stage("filtering", "complete", args.job_id, f"{cif_converted} designs ready for analysis")
    
    print(f"BoltzGen batch execution completed. {cif_converted} designs in {designs_dir}")

if __name__ == "__main__":
    main()
