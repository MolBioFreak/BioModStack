#!/usr/bin/env python3
"""
prep_boltz_batch.py - Generate Boltz YAML configs for a batch of PDBs

This script takes a list of PDB files (which share the same backbone/CDR structure)
and a SHARED MSA file, then generates individual YAML config files for Boltz to process them.
"""

import os
import sys
import argparse
import yaml
from pathlib import Path

# Try BioPython import, fallback to simple parser
try:
    from Bio import SeqIO
except ImportError:
    SeqIO = None

def parse_pdb_simple(pdb_path):
    """Fallback PDB parser to extract sequence if BioPython is missing"""
    # Simple extraction of amino acid sequence from CA atoms
    aa_codes = {
        'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
        'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
        'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
        'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V'
    }
    
    chain_seqs = {}
    with open(pdb_path) as f:
        seen = set()
        for line in f:
            if line.startswith('ATOM') and line[13:15] == 'CA':
                chain = line[21]
                resnum = line[22:26].strip()
                resname = line[17:20].strip()
                code = aa_codes.get(resname, 'X')
                
                key = f"{chain}_{resnum}"
                if key not in seen:
                    if chain not in chain_seqs: chain_seqs[chain] = []
                    chain_seqs[chain].append(code)
                    seen.add(key)
    
    return {c: "".join(s) for c, s in chain_seqs.items()}

def file_to_list(file_string):
    """Parses Nextflow string input (space separated or list) into file list"""
    # Remove brackets if present (Nextflow list string representation)
    clean = file_string.strip('[]')
    # Split by comma or space
    if ',' in clean:
        parts = clean.split(',')
    else:
        parts = clean.split()
    return [p.strip() for p in parts if p.strip()]

def main():
    parser = argparse.ArgumentParser(description='Prepare Boltz batch YAMLs')
    parser.add_argument('--pdb_files', required=True, help='List of PDB files (space or comma separated)')
    parser.add_argument('--msa_path', required=True, help='Path to shared MSA file')
    parser.add_argument('--out_dir', required=True, help='Output directory for YAMLs')
    
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    
    pdb_files = file_to_list(args.pdb_files)
    msa_abs_path = os.path.abspath(args.msa_path)
    
    print(f"Generating YAMLs for {len(pdb_files)} PDBs using shared MSA: {args.msa_path}")
    
    for pdb_file_str in pdb_files:
        pdb_path = Path(pdb_file_str)
        if not pdb_path.exists():
            print(f"Warning: PDB file not found: {pdb_path}")
            continue
            
        name = pdb_path.stem
        
        # Extract sequence
        if SeqIO:
             # robust parsing
             chains = {}
             for record in SeqIO.parse(pdb_path, "pdb-atom"):
                 chain_id = record.annotations.get("chain", "A")
                 chains[chain_id] = str(record.seq)
        else:
             chains = parse_pdb_simple(pdb_path)
        
        # Build YAML structure
        yaml_data = {
            "version": 1,
            "sequences": []
        }
        
        for chain_id, sequence in chains.items():
            entry = {
                "protein": {
                    "id": [chain_id],
                    "sequence": sequence
                }
            }
            # Attach the shared MSA to every chain
            # Note: Ideally we attach it only to the heavy/light chains derived from the antigen
            # But for batch backbones sharing an antigen, the MSA usually covers the complex.
            if "NO_MSA" not in msa_abs_path:
                entry["protein"]["msa"] = msa_abs_path
            
            yaml_data["sequences"].append(entry)
            
        # Write YAML
        out_yaml = Path(args.out_dir) / f"{name}.yaml"
        with open(out_yaml, "w") as f:
            yaml.dump(yaml_data, f, sort_keys=False)
            
    print("Done generating YAMLs")

if __name__ == "__main__":
    main()
