#!/usr/bin/env python3
"""
prep_boltz_with_msa.py - Generate Boltz YAML configs with MSA paths

This script:
1. Extracts sequences from PDB files
2. Generates MSAs using GPU MMseqs2 (via run_local_msa.py)  
3. Creates Boltz-2 YAML config files with MSA paths

Used by PrepBoltzWithMSA Nextflow process for antibody structure validation.
"""

import os
import sys
import argparse
import yaml
import subprocess
import hashlib
from collections import defaultdict
from pathlib import Path

# Try BioPython import
try:
    from Bio import SeqIO
except ImportError:
    print("Warning: BioPython not available. Using simplified PDB parser.")
    SeqIO = None


def parse_pdb_simple(pdb_path):
    """Fallback PDB parser if BioPython not available"""
    sequences = defaultdict(list)
    aa_codes = {
        'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
        'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
        'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
        'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V'
    }
    
    last_residue = {}
    with open(pdb_path) as f:
        for line in f:
            if line.startswith('ATOM') and line[12:16].strip() == 'CA':
                chain = line[21]
                resname = line[17:20].strip()
                resnum = int(line[22:26])
                
                if chain not in last_residue or resnum != last_residue[chain]:
                    aa = aa_codes.get(resname, 'X')
                    sequences[chain].append(aa)
                    last_residue[chain] = resnum
    
    return {chain: ''.join(seq) for chain, seq in sequences.items()}


def extract_chain_groups(pdb_path):
    """Group chains by identical sequences, returns {sequence: [chain_ids]}"""
    if SeqIO:
        sequence_map = defaultdict(list)
        for record in SeqIO.parse(pdb_path, 'pdb-atom'):
            chain_id = record.annotations.get('chain', 'A')
            sequence = str(record.seq).replace('X', '')
            if len(sequence) > 10:
                sequence_map[sequence].append(chain_id)
        return sequence_map
    else:
        chains = parse_pdb_simple(pdb_path)
        sequence_map = defaultdict(list)
        for chain_id, sequence in chains.items():
            sequence = sequence.replace('X', '')
            if len(sequence) > 10:
                sequence_map[sequence].append(chain_id)
        return sequence_map


def get_msa_for_sequence(sequence, name, args):
    """Generate or retrieve cached MSA for a sequence"""
    seq_hash = hashlib.md5(sequence.encode()).hexdigest()[:12]
    cache_file = Path(args.cache_dir) / f"{seq_hash}.a3m"
    local_file = Path(args.msa_output) / f"{name}.a3m"
    
    # Check cache first
    if cache_file.exists():
        print(f"  Using cached MSA: {cache_file}")
        import shutil
        shutil.copy(cache_file, local_file)
        return str(local_file.resolve())
    
    # Generate MSA using run_local_msa.py
    msa_script = Path(args.msa_script)
    if msa_script.exists():
        cmd = [
            "python3", str(msa_script),
            "--sequence", sequence,
            "--name", name,
            "--out_dir", args.msa_output,
            "--db_path", args.db_path,
            "--cache_dir", args.cache_dir,
            "--threads", str(args.threads)
        ]
        print(f"  Generating MSA for {name} ({len(sequence)} aa)...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode == 0 and local_file.exists():
                print(f"  Generated MSA: {local_file}")
                return str(local_file.resolve())
            else:
                stderr_snippet = result.stderr[:300] if result.stderr else "No stderr"
                print(f"  MSA generation failed: {stderr_snippet}")
        except subprocess.TimeoutExpired:
            print(f"  MSA generation timed out for {name}")
        except Exception as e:
            print(f"  MSA generation error: {e}")
    else:
        print(f"  Warning: run_local_msa.py not found at {msa_script}")
    
    return None


def generate_yaml_with_msa(pdb_filename, chain_groups, msa_paths):
    """Create YAML config with MSA paths for each chain group"""
    sequences = []
    for sequence, chain_ids in chain_groups.items():
        entry = {
            'protein': {
                'id': sorted(chain_ids),
                'sequence': sequence
            }
        }
        chain_key = f"{pdb_filename}_{chain_ids[0]}"
        if chain_key in msa_paths and msa_paths[chain_key]:
            entry['protein']['msa'] = msa_paths[chain_key]
        else:
            entry['protein']['msa'] = 'empty'
        sequences.append(entry)
    return {'sequences': sequences}


def main():
    parser = argparse.ArgumentParser(
        description='Generate Boltz-2 YAML configs with MSA generation'
    )
    parser.add_argument('-i', '--input', required=True, help='Input PDB directory')
    parser.add_argument('-o', '--output', required=True, help='Output YAML directory')
    parser.add_argument('--msa_output', required=True, help='Output MSA directory')
    parser.add_argument('--db_path', required=True, help='MMseqs2 database path')
    parser.add_argument('--cache_dir', required=True, help='MSA cache directory')
    parser.add_argument('--threads', type=int, default=32, help='MSA threads')
    parser.add_argument('--msa_script', required=True, help='Path to run_local_msa.py')
    
    args = parser.parse_args()
    
    os.makedirs(args.output, exist_ok=True)
    os.makedirs(args.msa_output, exist_ok=True)
    
    pdb_files = [f for f in os.listdir(args.input) if f.endswith('.pdb')]
    print(f"Processing {len(pdb_files)} PDB files...")
    
    all_msa_paths = {}
    
    for pdb_file in pdb_files:
        print(f"\nProcessing: {pdb_file}")
        pdb_path = os.path.join(args.input, pdb_file)
        base_name = pdb_file.replace('.pdb', '')
        chain_groups = extract_chain_groups(pdb_path)
        
        for sequence, chain_ids in chain_groups.items():
            chain_id = chain_ids[0]
            msa_name = f"{base_name}_{chain_id}"
            msa_path = get_msa_for_sequence(sequence, msa_name, args)
            all_msa_paths[f"{pdb_file}_{chain_id}"] = msa_path
        
        yaml_config = generate_yaml_with_msa(pdb_file, chain_groups, all_msa_paths)
        yaml_path = os.path.join(args.output, f"{base_name}.yaml")
        with open(yaml_path, 'w') as f:
            yaml.dump(yaml_config, f, sort_keys=False)
        print(f"  Created: {yaml_path}")
    
    print(f"\nGenerated {len(pdb_files)} YAML files with MSA paths")


if __name__ == '__main__':
    main()
