import os
import argparse
from collections import defaultdict
from Bio import SeqIO
import yaml

def extract_chain_groups(pdb_path):
    """Read polymer identity from residues, never from one-letter alphabets.

    Ordered entries avoid grouping separated chains across intervening entities.
    Modified amino acids retain CCD identity alongside their parent sequence.
    """
    from Bio.PDB import PDBParser
    from Bio.Data.PDBData import protein_letters_3to1, protein_letters_3to1_extended
    groups = {}
    model = next(PDBParser(QUIET=True).get_structure('input', pdb_path).get_models())
    for chain in model:
        residues = [r for r in chain if r.resname.strip() not in {'HOH', 'WAT'}]
        if not residues:
            continue
        names = [r.resname.strip() for r in residues]
        if all(n in {'DA', 'DC', 'DG', 'DT', 'DI', 'DN'} for n in names):
            kind, sequence = 'dna', ''.join(n[1:] for n in names)
            mods = ()
        elif all(n in {'A', 'C', 'G', 'U', 'I', 'N'} for n in names):
            kind, sequence = 'rna', ''.join(names)
            mods = ()
        else:
            # Only residues with protein backbone CA belong to this polymer.
            names = [r.resname.strip() for r in residues if 'CA' in r and ('N' in r or r.resname.strip() in protein_letters_3to1_extended)]
            if not names:
                continue
            kind = 'protein'
            sequence = ''.join(protein_letters_3to1_extended.get(n, 'X') for n in names)
            mods = tuple((i, n) for i, n in enumerate(names, 1) if n not in protein_letters_3to1)
        groups[(kind, sequence, mods, chain.id)] = [chain.id]
    return groups


def generate_yaml_config(chain_groups):
    """Legacy sequence-only callers are protein; typed PDB callers retain type."""
    sequences = []
    for key, chain_ids in chain_groups.items():
        kind, sequence, mods = key[:3] if isinstance(key, tuple) else ('protein', key, ())
        entity = {'id': list(chain_ids), 'sequence': sequence}
        if mods:
            entity['modifications'] = [{'position': p, 'ccd': c} for p, c in mods]
        if kind == 'protein':
            entity['msa'] = 'empty'
        sequences.append({kind: entity})
    return {'version': 1, 'sequences': sequences}

def process_pdb_files(input_dir, output_dir):
    """Process PDB files with flexible chain handling"""
    os.makedirs(output_dir, exist_ok=True)
    
    for filename in os.listdir(input_dir):
        # Handle both legacy 'fold_*' and new 'rfd3_*' naming conventions
        if filename.endswith('.pdb'):
            pdb_path = os.path.join(input_dir, filename)
            
            # Group chains by identical sequences
            chain_groups = extract_chain_groups(pdb_path)
            
            # Generate YAML config
            yaml_config = generate_yaml_config(chain_groups)
            
            # Create output path
            yaml_filename = filename.replace('.pdb', '.yaml')
            yaml_path = os.path.join(output_dir, yaml_filename)
            
            # Write YAML file
            with open(yaml_path, 'w') as yaml_file:
                yaml.dump(yaml_config, yaml_file, sort_keys=False)
            
            print(f'Generated: {yaml_path}')

def main():
    """Command-line interface setup"""
    parser = argparse.ArgumentParser(
        description='Generate Boltz-2 YAML configs with automatic chain grouping',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('-i', '--input', required=True,
                        help='Input directory containing PDB files')
    parser.add_argument('-o', '--output', default=None,
                        help='Output directory for YAML files')
    
    args = parser.parse_args()
    output_dir = args.output if args.output else args.input
    
    process_pdb_files(args.input, output_dir)

if __name__ == '__main__':
    main()
