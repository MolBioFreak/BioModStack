import argparse
import shutil
import os
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Filter BoltzGen results")
    parser.add_argument("--pdbs", nargs="+", help="Input PDB files")
    parser.add_argument("--jsons", nargs="+", help="Input JSON files")
    parser.add_argument("--boltzgen-min-plddt", type=float, default=None, help="Minimum pLDDT")
    parser.add_argument("--boltzgen-min-conf-score", type=float, default=None, help="Minimum confidence score")
    parser.add_argument("--out_dir", type=str, required=True, help="Output directory")
    
    args = parser.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    
    if not args.pdbs:
        print("No PDBs to filter")
        return
        
    pdb_list = args.pdbs
    if len(pdb_list) == 1 and ' ' in pdb_list[0]:
        pdb_list = pdb_list[0].split()
    
    # Build JSON lookup for metrics
    json_metrics = {}
    json_list = args.jsons or []
    if len(json_list) == 1 and ' ' in json_list[0]:
        json_list = json_list[0].split()
    
    for json_path in json_list:
        try:
            with open(json_path) as f:
                data = json.load(f)
                design_id = data.get('design_id', Path(json_path).stem)
                json_metrics[design_id] = data
        except Exception as e:
            print(f"Warning: Could not parse {json_path}: {e}")
    
    passed = 0
    filtered = 0
    for pdb in pdb_list:
        path = Path(pdb)
        design_id = path.stem
        
        # Get metrics from JSON if available
        metrics = json_metrics.get(design_id, {})
        plddt = metrics.get('design_ptm', 0) * 100  # Convert pTM to approximate pLDDT scale
        conf_score = metrics.get('affinity_probability', 0)
        
        # Apply filters
        if args.boltzgen_min_plddt and plddt < args.boltzgen_min_plddt:
            print(f"Filtered {design_id}: pLDDT {plddt:.1f} < {args.boltzgen_min_plddt}")
            filtered += 1
            continue
        
        if args.boltzgen_min_conf_score and conf_score < args.boltzgen_min_conf_score:
            print(f"Filtered {design_id}: confidence {conf_score:.3f} < {args.boltzgen_min_conf_score}")
            filtered += 1
            continue
        
        # Copy passing designs
        shutil.copy(path, Path(args.out_dir) / path.name)
        passed += 1
        
    print(f"Filtered {filtered} designs, kept {passed} designs.")

if __name__ == "__main__":
    main()
