#!/usr/bin/env python3
"""
prep_boltz_batch.py - Generate Boltz YAML configs for a batch of PDBs

This script takes a list of PDB files (which share the same backbone/CDR structure)
and a SHARED MSA file, then generates individual YAML config files for Boltz to process them.
"""

import os
import argparse
import json
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
    parser.add_argument('--pdb_files', required=True, nargs='+', help='List of PDB files (space or comma separated)')
    parser.add_argument('--msa_path', required=True, help='Path to shared MSA file')
    parser.add_argument('--out_dir', required=True, help='Output directory for YAMLs')
    parser.add_argument('--anchor_target', action='store_true', help='Attach structural templates to target chains')
    parser.add_argument('--target_chains', default='', help='Comma-separated target chain IDs to anchor')
    parser.add_argument('--binder_chains', default='', help='Comma-separated binder chain IDs that should receive the shared MSA')
    parser.add_argument('--template_manifest', default='', help='JSON manifest produced by extract_target_templates.py')
    parser.add_argument('--template_threshold', type=float, default=2.0, help='Template threshold in angstrom for force=true templates')
    parser.add_argument('--epitope_residues', default='', help='Comma-separated epitope residues like B:12,B:15')
    parser.add_argument('--pocket_max_distance', type=float, default=8.0, help='Pocket/contact guidance cutoff in angstrom')
    parser.add_argument('--pocket_force', action='store_true', help='Set force=true on emitted pocket constraints')
    
    args = parser.parse_args()
    
    os.makedirs(args.out_dir, exist_ok=True)
    
    # helper to flatten if needed
    raw_input = args.pdb_files
    if len(raw_input) == 1:
        # Check if it's a single string containing spaces or commas or brackets
        pdb_files = file_to_list(raw_input[0])
    else:
        # Already a list of files
        pdb_files = raw_input
    msa_abs_path = os.path.abspath(args.msa_path)
    target_chains = {token.strip() for token in (args.target_chains or '').split(',') if token.strip()}
    binder_chains = [token.strip() for token in (args.binder_chains or '').split(',') if token.strip()]
    epitope_contacts = []
    for token in (args.epitope_residues or '').split(','):
        value = token.strip()
        if not value or ':' not in value:
            continue
        chain_id, residue_id = value.split(':', 1)
        chain_id = chain_id.strip()
        residue_id = residue_id.strip()
        if chain_id and residue_id:
            epitope_contacts.append([chain_id, residue_id])
    template_manifest = {}
    if args.anchor_target:
        if not target_chains:
            raise ValueError("--anchor_target requires --target_chains")
        if not args.template_manifest:
            raise ValueError("--anchor_target requires --template_manifest")
        with open(args.template_manifest, 'r', encoding='utf-8') as handle:
            template_manifest = json.load(handle)
    
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

        template_info = template_manifest.get(name) if args.anchor_target else None
        if args.anchor_target and not isinstance(template_info, dict):
            raise ValueError(f"Missing target template manifest entry for {name}")
        
        shared_msa_bound = False
        shared_msa_chain_ids = set(binder_chains or ([next(iter(chains.keys()))] if chains else []))
        for chain_id, sequence in chains.items():
            entry = {
                "protein": {
                    "id": [chain_id],
                    "sequence": sequence
                }
            }
            if "NO_MSA" not in msa_abs_path and chain_id in shared_msa_chain_ids:
                entry["protein"]["msa"] = msa_abs_path
                shared_msa_bound = True
            elif chain_id in target_chains:
                entry["protein"]["msa"] = "empty"

            if args.anchor_target and chain_id in target_chains:
                template_path = os.path.abspath(str(template_info.get("cif", "")))
                if not template_path:
                    raise ValueError(f"Template path missing for anchored target chain {chain_id} in {name}")
                entry["protein"]["templates"] = [{
                    "cif": template_path,
                    "chain_id": chain_id,
                    "template_id": chain_id,
                    "force": True,
                    "threshold": args.template_threshold,
                }]
            
            yaml_data["sequences"].append(entry)

        if not shared_msa_bound and "NO_MSA" not in msa_abs_path and yaml_data["sequences"]:
            yaml_data["sequences"][0]["protein"]["msa"] = msa_abs_path

        if epitope_contacts and shared_msa_chain_ids:
            binder_chain = sorted(shared_msa_chain_ids)[0]
            yaml_data["constraints"] = [{
                "pocket": {
                    "binder": binder_chain,
                    "contacts": epitope_contacts,
                    "max_distance": args.pocket_max_distance,
                    "force": bool(args.pocket_force),
                }
            }]
            
        # Write YAML
        out_yaml = Path(args.out_dir) / f"{name}.yaml"
        with open(out_yaml, "w") as f:
            yaml.safe_dump(yaml_data, f, sort_keys=False)
            
    print("Done generating YAMLs")

if __name__ == "__main__":
    main()
