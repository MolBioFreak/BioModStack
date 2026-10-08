#!/usr/bin/env python3
"""
batch_antiberty.py - Score immunogenicity for a batch of PDBs

Extracts sequences from a list of PDB files and computes AntiBERTy 
pseudo-log-likelihood (PLL) scores in a single batch inference pass.
"""

import os
import sys
import argparse
import csv
import torch
import antiberty
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
    """Parses Nextflow string input into file list"""
    clean = file_string.strip('[]')
    if ',' in clean:
        parts = clean.split(',')
    else:
        parts = clean.split()
    return [p.strip() for p in parts if p.strip()]

def load_sequences(pdb_files):
    """Load heavy/light chain sequences from PDBs"""
    data = []
    
    for pdb_str in pdb_files:
        pdb_path = Path(pdb_str)
        if not pdb_path.exists():
            continue
            
        name = pdb_path.stem
        chains = {}
        
        if SeqIO:
             for record in SeqIO.parse(pdb_path, "pdb-atom"):
                 chain_id = record.annotations.get("chain", "A")
                 chains[chain_id] = str(record.seq)
        else:
             chains = parse_pdb_simple(pdb_path)
        
        # Heuristic: AntiBERTy is fine with full sequence, but usually applied to variable regions
        # For batching, we just score all chains found
        for chain_id, seq in chains.items():
            if len(seq) > 20: # Skip fragments
                data.append({
                    "file": pdb_path.name,
                    "chain": chain_id,
                    "sequence": seq
                })
    return data

def main():
    parser = argparse.ArgumentParser(description='Batch AntiBERTy Scoring')
    parser.add_argument('--pdb_files', required=True, help='List of PDB files')
    parser.add_argument('--out_csv', required=True, help='Output CSV file')
    
    args = parser.parse_args()
    
    # 1. Load Data
    pdb_files = file_to_list(args.pdb_files)
    print(f"Loading sequences from {len(pdb_files)} PDBs...")
    
    records = load_sequences(pdb_files)
    if not records:
        print("No valid sequences found")
        with open(args.out_csv, 'w') as f:
            f.write("file,chain,score\n")
        return

    sequences = [r["sequence"] for r in records]
    
    # 2. Run Inference
    print(f"Running AntiBERTy on {len(sequences)} sequences...")
    try:
        # Load model manually to control device? or rely on library
        # antiberty.pseudo_log_likelihood handles batching
        scores = antiberty.pseudo_log_likelihood(sequences, batch_size=32)
        
        # 3. Write Results
        with open(args.out_csv, 'w') as f:
            writer = csv.writer(f)
            writer.writerow(["file", "chain", "score"])
            for i, score in enumerate(scores):
                rec = records[i]
                writer.writerow([rec["file"], rec["chain"], f"{score:.4f}"])
        
        print(f"Saved scores to {args.out_csv}")
        
    except Exception as e:
        print(f"Error running AntiBERTy: {e}")
        # Write empty CSV on failure to prevent pipeline crash
        with open(args.out_csv, 'w') as f:
            f.write("file,chain,score\n")
        sys.exit(1)

if __name__ == "__main__":
    main()
