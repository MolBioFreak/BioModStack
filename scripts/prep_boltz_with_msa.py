#!/usr/bin/env python3
"""
prep_boltz_with_msa.py - Generate Boltz YAML configs with MSA paths

This script:
1. Extracts sequences from PDB files
2. Reuses sequence-verified cached alignments; cache misses fail closed.
3. Creates Boltz-2 YAML config files with MSA paths

Used by PrepBoltzWithMSA Nextflow process for antibody structure validation.
"""

import os
import sys
import argparse
import yaml
import hashlib
from collections import defaultdict
from pathlib import Path

from prep_boltz_yaml import extract_chain_groups, generate_yaml_config


def get_msa_for_sequence(sequence, name, args):
    """Reuse identity-verified input only; cache miss requires controller preparation."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from biomodstack_msa_handoff import validate_a3m
    from biomodstack_msa_controller import BLOCKED
    seq_hash = hashlib.md5(sequence.encode()).hexdigest()[:12]
    cache_file = Path(args.cache_dir) / f"{seq_hash}.a3m"
    local_file = Path(args.msa_output) / f"{name}.a3m"
    if not cache_file.is_file():
        raise RuntimeError(BLOCKED)
    data = validate_a3m(cache_file, sequence)
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(data)
    return str(local_file.resolve())

def generate_yaml_with_msa(pdb_filename, chain_groups, msa_paths):
    result = generate_yaml_config(chain_groups)
    for entry in result['sequences']:
        if 'protein' not in entry:
            continue
        protein = entry['protein']
        key = f"{pdb_filename}_{protein['id'][0]}"
        if key not in msa_paths or not msa_paths[key]:
            raise ValueError('Missing prepared protein alignment; no implicit no-MSA fallback')
        protein['msa'] = msa_paths[key]
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Generate Boltz-2 YAML configs with MSA generation'
    )
    parser.add_argument('-i', '--input', required=True, help='Input PDB directory')
    parser.add_argument('-o', '--output', required=True, help='Output YAML directory')
    parser.add_argument('--msa_output', required=True, help='Output MSA directory')
    parser.add_argument('--db_path', help='Legacy ignored argument; local search disabled')
    parser.add_argument('--cache_dir', required=True, help='MSA cache directory')
    parser.add_argument('--threads', type=int, default=32, help='MSA threads')
    parser.add_argument('--msa_script', help='Legacy ignored argument; only verified alignments are consumed')
    
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
        
        for key, chain_ids in chain_groups.items():
            kind, sequence = key[:2]
            chain_id = chain_ids[0]
            msa_name = f"{base_name}_{chain_id}"
            if kind != 'protein':
                continue
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
