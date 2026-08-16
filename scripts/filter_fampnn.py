#!/usr/bin/env python3
"""
Filter FAMPNN designs based on PSCE scores.

Supports filtering by:
- fampnn_avg_psce: Average PSCE across all residues (lower = better)
- fampnn_max_residue_psce: Maximum per-residue PSCE (catches individual bad residues)
"""
import os
import argparse
import json
import shutil

def load_json_data(json_dir):
    """Load FAMPNN PSCE scores from JSON metadata files"""
    data_map = {}
    
    for json_file in os.listdir(json_dir):
        if json_file.endswith('.json'):
            with open(os.path.join(json_dir, json_file)) as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        design_name = data['design']
                        data_map[design_name] = {
                            'avg_psce': float(data.get('fampnn_avg_psce', 999)),
                            'max_psce': float(data.get('fampnn_max_residue_psce', 999)),
                            'min_psce': float(data.get('fampnn_min_residue_psce', 0)),
                        }
                    except (json.JSONDecodeError, KeyError) as e:
                        print(f"Warning: Error parsing line in {json_file}: {e}")
    return data_map

def filter_designs(data_map, max_avg_psce, max_residue_psce):
    """Filter FAMPNN designs based on PSCE thresholds"""
    passed = {}
    rejected_avg = 0
    rejected_max = 0
    
    for design, scores in data_map.items():
        # Check average PSCE
        if max_avg_psce is not None and scores['avg_psce'] > max_avg_psce:
            rejected_avg += 1
            print(f"Rejected {design}: avg_psce {scores['avg_psce']:.2f} > {max_avg_psce}")
            continue
        
        # Check max residue PSCE
        if max_residue_psce is not None and scores['max_psce'] > max_residue_psce:
            rejected_max += 1
            print(f"Rejected {design}: max_residue_psce {scores['max_psce']:.2f} > {max_residue_psce}")
            continue
        
        passed[design] = scores
    
    print(f"\nFilter summary:")
    print(f"  Rejected by avg PSCE: {rejected_avg}")
    print(f"  Rejected by max residue PSCE: {rejected_max}")
    print(f"  Passed: {len(passed)}")
    
    return passed

def copy_filtered_designs(filtered_designs, pdb_dir, json_dir, output_dir):
    """Copy matching PDBs and JSONs to output directory.
    
    Now handles mismatched naming between JSON metadata and PDB files:
    - First tries exact match: {design}.pdb
    - Falls back to partial match: any PDB containing the design name
    """
    os.makedirs(output_dir, exist_ok=True)
    copied_count = 0
    
    # Build index of available PDBs for fuzzy matching
    available_pdbs = [f for f in os.listdir(pdb_dir) if f.endswith('.pdb')]
    
    for design in filtered_designs:
        pdb_copied = False
        
        # Try exact match first
        pdb_file = os.path.join(pdb_dir, f"{design}.pdb")
        if os.path.exists(pdb_file):
            shutil.copy2(pdb_file, output_dir)
            copied_count += 1
            pdb_copied = True
        else:
            # Fallback: find PDBs containing the design name (handles prefix/suffix mismatches)
            matching_pdbs = [p for p in available_pdbs if design in p or design.replace('_', '') in p.replace('_', '')]
            if matching_pdbs:
                for match_pdb in matching_pdbs:
                    src = os.path.join(pdb_dir, match_pdb)
                    shutil.copy2(src, output_dir)
                    copied_count += 1
                    pdb_copied = True
                    print(f"Matched {design} -> {match_pdb}")
            
        if not pdb_copied:
            print(f"Warning: PDB file for {design} not found in {pdb_dir}")
            
        # Copy JSON metadata if available
        json_file = os.path.join(json_dir, f"{design}.json")
        if os.path.exists(json_file):
            shutil.copy2(json_file, output_dir)
        else:
            print(f"Warning: JSON file for {design} not found")
    
    return copied_count

def main():
    parser = argparse.ArgumentParser(description="Filter FAMPNN designs using JSON metadata")
    parser.add_argument("--jsons", required=True, help="Directory containing JSON metadata files")
    parser.add_argument("--pdbs", required=True, help="Directory containing PDB files")
    parser.add_argument("--fampnn-max-psce", type=float,
                        help="Maximum FAMPNN average PSCE score")
    parser.add_argument("--fampnn-max-residue-psce", type=float,
                        help="Maximum per-residue PSCE score (catches individual bad residues)")
    parser.add_argument("--output-dir", default="filtered_output_fampnn",
                        help="Output directory for filtered designs (default: filtered_output_fampnn)")
    
    args = parser.parse_args()
    
    # Print filter settings
    if args.fampnn_max_psce is not None:
        print(f"Filter: avg PSCE ≤ {args.fampnn_max_psce}")
    if args.fampnn_max_residue_psce is not None:
        print(f"Filter: max residue PSCE ≤ {args.fampnn_max_residue_psce}")
    if args.fampnn_max_psce is None and args.fampnn_max_residue_psce is None:
        print("No FAMPNN score filtering applied; copying all designs.")
    
    # Load and filter scores
    data = load_json_data(args.jsons)
    print(f'Pre-filter designs: {len(data)}')
    
    filtered = filter_designs(data, args.fampnn_max_psce, args.fampnn_max_residue_psce)
    print(f'Post-filter designs: {len(filtered)}')
  
    # Copy matching files
    copied_count = copy_filtered_designs(filtered.keys(), args.pdbs, args.jsons, args.output_dir)
    
    print(f"\nResults: {len(filtered)} designs found, {copied_count} PDB files copied to {args.output_dir}")

if __name__ == "__main__":
    main()
