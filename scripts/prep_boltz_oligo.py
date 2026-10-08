#!/usr/bin/env python3
"""
Convert RFDpoly multi-polymer PDBs to Boltz-2 YAML format.
Detects DNA/RNA/protein chains from residue names.

Addresses addendum items:
- #7: Chain type serialization (exact format)
- #8: Downstream compatibility (RFDpoly → Boltz-2)
- #10: RNA vs DNA residue detection ambiguity
"""

import argparse
import yaml
from pathlib import Path
from typing import List, Tuple

# Residue name sets for polymer detection (addendum fix #10)
DNA_RESIDUES = {'DA', 'DT', 'DG', 'DC', 'DU', 'DI'}
RNA_RESIDUES = {'A', 'U', 'G', 'C', 'RA', 'RU', 'RG', 'RC', 'I'}
# Note: Single-letter A/G/C are ambiguous but typically RNA in RFDpoly output

PROTEIN_RESIDUES = {
    'ALA', 'ARG', 'ASN', 'ASP', 'CYS', 'GLN', 'GLU', 'GLY', 'HIS', 'ILE',
    'LEU', 'LYS', 'MET', 'PHE', 'PRO', 'SER', 'THR', 'TRP', 'TYR', 'VAL',
    'MSE', 'SEC', 'PYL'  # Modified amino acids
}


def detect_polymer_type(residues: List[str]) -> str:
    """
    Detect polymer type from residue names.
    Returns: 'dna', 'rna', or 'protein'
    """
    if not residues:
        return 'protein'  # Default fallback
    
    # Count matches for each type
    dna_count = sum(1 for r in residues if r.upper() in DNA_RESIDUES)
    rna_count = sum(1 for r in residues if r.upper() in RNA_RESIDUES)
    protein_count = sum(1 for r in residues if r.upper() in PROTEIN_RESIDUES)
    
    total = len(residues)
    
    # Prefer DNA detection (more reliable residue names)
    if dna_count > total * 0.8:
        return 'dna'
    elif rna_count > total * 0.8:
        return 'rna'
    elif protein_count > total * 0.5:
        return 'protein'
    elif dna_count > rna_count:
        return 'dna'
    elif rna_count > 0:
        return 'rna'
    else:
        return 'protein'


def extract_sequence(residues: List[str], polymer_type: str) -> str:
    """Extract sequence string from residue names."""
    if polymer_type == 'protein':
        # 3-letter to 1-letter conversion
        aa_map = {
            'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
            'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
            'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
            'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V',
            'MSE': 'M', 'SEC': 'C', 'PYL': 'K'
        }
        return ''.join(aa_map.get(r.upper(), 'X') for r in residues)
    
    elif polymer_type == 'dna':
        # DNA residue to base
        dna_map = {'DA': 'A', 'DT': 'T', 'DG': 'G', 'DC': 'C', 'DU': 'U'}
        return ''.join(dna_map.get(r.upper(), r[1] if len(r) > 1 else r) for r in residues)
    
    else:  # RNA
        # RNA residue to base
        rna_map = {'RA': 'A', 'RU': 'U', 'RG': 'G', 'RC': 'C', 'A': 'A', 'U': 'U', 'G': 'G', 'C': 'C'}
        return ''.join(rna_map.get(r.upper(), r[0]) for r in residues)


def parse_pdb_chains(pdb_path: Path) -> List[Tuple[str, str, List[str]]]:
    """
    Parse PDB file and extract chain information.
    Returns: list of (chain_id, polymer_type, sequence)
    """
    chains = {}
    
    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('ATOM') or line.startswith('HETATM'):
                chain_id = line[21]
                res_name = line[17:20].strip()
                res_num = line[22:26].strip()
                
                if chain_id not in chains:
                    chains[chain_id] = {}
                
                # Store unique residues by number
                if res_num not in chains[chain_id]:
                    chains[chain_id][res_num] = res_name
    
    result = []
    for chain_id in sorted(chains.keys()):
        residues = [chains[chain_id][rn] for rn in sorted(chains[chain_id].keys(), key=int)]
        polymer_type = detect_polymer_type(residues)
        sequence = extract_sequence(residues, polymer_type)
        result.append((chain_id, polymer_type, sequence))
    
    return result


def pdb_to_boltz_yaml(pdb_path: Path, output_path: Path) -> dict:
    """
    Convert PDB to Boltz-2 YAML format.
    Returns the generated YAML as dict.
    """
    chains = parse_pdb_chains(pdb_path)
    
    sequences = []
    for chain_id, polymer_type, sequence in chains:
        # Boltz-2 format (addendum fix #7 - exact format)
        sequences.append({
            polymer_type: {
                'id': chain_id,
                'sequence': sequence
            }
        })
    
    yaml_data = {'sequences': sequences}
    
    with open(output_path, 'w') as f:
        yaml.dump(yaml_data, f, default_flow_style=False)
    
    return yaml_data


def main():
    parser = argparse.ArgumentParser(
        description='Convert RFDpoly PDBs to Boltz-2 YAML format'
    )
    parser.add_argument('--input_pdbs', nargs='+', required=True,
                        help='Input PDB files')
    parser.add_argument('--output_dir', required=True,
                        help='Output directory for YAML files')
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for pdb_file in args.input_pdbs:
        pdb_path = Path(pdb_file)
        if not pdb_path.exists():
            print(f"Warning: {pdb_path} not found, skipping")
            continue
        
        output_path = output_dir / f"{pdb_path.stem}.yaml"
        yaml_data = pdb_to_boltz_yaml(pdb_path, output_path)
        
        # Print summary
        chain_types = [list(s.keys())[0] for s in yaml_data['sequences']]
        print(f"{pdb_path.name} → {output_path.name}: {', '.join(chain_types)}")


if __name__ == '__main__':
    main()
