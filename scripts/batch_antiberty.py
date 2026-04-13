#!/usr/bin/env python3
"""
batch_antiberty.py - Score immunogenicity for a batch of PDBs

Extracts sequences from a list of PDB files and computes AntiBERTy 
pseudo-log-likelihood (PLL) scores in a single batch inference pass.
"""


import sys
import argparse
import csv
from pathlib import Path
import torch
from antiberty import AntiBERTyRunner

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

def parse_chain_csv(raw):
    return {token.strip() for token in str(raw or "").split(",") if token.strip()}

def load_sequences(pdb_files, allowed_chain_ids=None):
    """Load antibody-chain sequences from PDBs."""
    data = []
    allowed_chain_id_set = {str(chain_id).strip() for chain_id in (allowed_chain_ids or set()) if str(chain_id).strip()}
    
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
        # For batching, score only the configured binder/antibody chains when known.
        for chain_id, seq in chains.items():
            if allowed_chain_id_set and chain_id not in allowed_chain_id_set:
                continue
            if len(seq) > 20: # Skip fragments
                data.append({
                    "file": pdb_path.name,
                    "chain": chain_id,
                    "sequence": seq
                })
    return data

def main():
    parser = argparse.ArgumentParser(description='Batch AntiBERTy Scoring')
    parser.add_argument('--pdb_files', nargs='+', required=True, help='List of PDB files')
    parser.add_argument('--out_csv', required=True, help='Output CSV file')
    parser.add_argument('--chain_ids', default='', help='Optional comma-separated antibody/binder chain IDs to score')
    
    args = parser.parse_args()
    
    # 1. Load Data - pdb_files is now a list directly from argparse
    pdb_files = args.pdb_files
    print(f"Loading sequences from {len(pdb_files)} PDBs...")
    
    allowed_chain_ids = parse_chain_csv(args.chain_ids)
    records = load_sequences(pdb_files, allowed_chain_ids=allowed_chain_ids)
    if not records:
        print("No valid sequences found")
        with open(args.out_csv, 'w') as f:
            f.write("file,chain,score\n")
        return

    sequences = [r["sequence"] for r in records]
    
    # 2. Run Inference
    print(f"Running AntiBERTy on {len(sequences)} sequences...")
    try:
        # Use AntiBERTyRunner class
        runner = AntiBERTyRunner()
        
        # Check if model is on CUDA
        model_device = next(runner.model.parameters()).device
        print(f"AntiBERTy model device: {model_device}")
        
        if model_device.type == 'cuda':
            # Attempt to ensure new tensors (like inputs created internally) are on CUDA
            # This handles the case where the library creates CPU tensors but expects GPU
            print("Enabling CUDA default tensor type for inference...")
            torch.set_default_tensor_type('torch.cuda.FloatTensor')
            try:
                scores = runner.pseudo_log_likelihood(sequences, batch_size=32)
            finally:
                # Revert to default
                torch.set_default_tensor_type('torch.FloatTensor')
        else:
             scores = runner.pseudo_log_likelihood(sequences, batch_size=32)
        
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
