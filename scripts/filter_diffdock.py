import argparse
import os
import shutil
import json
import glob
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Filter DiffDock results")
    parser.add_argument("--pdbs", nargs="+", help="Input PDB files")
    parser.add_argument("--jsons", nargs="+", help="Input JSON files (optional)")
    parser.add_argument("--confidence_threshold", type=float, default=0.0, help="Minimum confidence score")
    parser.add_argument("--out_dir", type=str, required=True, help="Output directory")
    
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    
    # DiffDock filenames usually look like: rank1_confidence-0.5.pdb
    # But from our module, we might have renamed them or kept structure
    
    pdb_list = args.pdbs
    if not pdb_list:
        print("No PDBs to filter")
        return

    if len(pdb_list) == 1 and ' ' in pdb_list[0]:
        pdb_list = pdb_list[0].split()
        
    count = 0
    passed = 0
    
    for pdb_file in pdb_list:
        count += 1
        path = Path(pdb_file)
        filename = path.name
        
        # Try to extract confidence from filename if possible
        # Example: complex1_rank1_confidence-1.23.pdb
        confidence = -999.0
        
        parts = filename.split('_')
        for part in parts:
            if part.startswith('confidence'):
                try:
                    # Remove .pdb and 'confidence' prefix
                    val_str = part.replace('confidence', '').replace('.pdb', '')
                    confidence = float(val_str)
                except:
                    pass
        
        # If we couldn't parse from filename, check if there's a JSON sidecar
        # This part depends on how RunDiffDock outputs things. 
        # For now, filename parsing is the standard DiffDock output way.
        
        if confidence >= args.confidence_threshold:
            shutil.copy(path, Path(args.out_dir) / filename)
            passed += 1
        else:
            print(f"Skipping {filename}: confidence {confidence} < {args.confidence_threshold}")
            
    print(f"Filtered {count} designs. {passed} passed threshold {args.confidence_threshold}")

if __name__ == "__main__":
    main()
