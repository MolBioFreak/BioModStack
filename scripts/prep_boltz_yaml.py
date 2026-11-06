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

def candidate_yaml_config(pdb_path):
    """Serialize the existing chemistry-aware candidate recipe to native Boltz.

    CCD owns intra-residue chemistry; the recipe owns explicit cross-residue
    connectivity and source ordering. No distance-based bonds or protonation.
    Protein MSA remains explicitly empty for this no-MSA prep process.
    """
    from pathlib import Path
    from prepare_protein_local_validator_input import build_contract

    try:
        contract = build_contract(Path(pdb_path), [])
    except ValueError as exc:
        # This shared prep also has historical nucleic-only callers; the
        # Resequence recipe requires protein. Keep their existing behavior.
        if 'PDB contains no supported protein residues:' not in str(exc):
            raise
        return generate_yaml_config(extract_chain_groups(pdb_path))
    sequences = []
    labels = {}
    for index, component in enumerate(contract['components'], 1):
        kind = component['molecule_type']
        labels[str(index)] = component['chain_id']
        entity = {'id': [component['chain_id']]}
        if kind in {'protein', 'dna', 'rna'}:
            entity['sequence'] = component['sequence']
            if kind == 'protein':
                entity['msa'] = 'empty'
            modifications = component.get('modifications', [])
            if modifications:
                entity['modifications'] = [
                    {'position': mod['ptmPosition'] if kind == 'protein' else mod['basePosition'],
                     'ccd': (mod['ptmType'] if kind == 'protein' else mod['modificationType']).removeprefix('CCD_')}
                    for mod in modifications]
        else:
            kind = 'ligand'  # Boltz represents ions by their source CCD too.
            entity['ccd'] = component['ccd']
        sequences.append({kind: entity})
    result = {'version': 1, 'sequences': sequences}
    bonds = contract['protenix_input'][0].get('covalent_bonds', [])
    if bonds:
        result['constraints'] = [{'bond': {
            f'atom{side}': [labels[bond[f'entity{side}']], int(bond[f'position{side}']), bond[f'atom{side}']]
            for side in (1, 2)}} for bond in bonds]
    return result


def process_pdb_files(input_dir, output_dir):
    """Process PDB files with flexible chain handling"""
    os.makedirs(output_dir, exist_ok=True)
    
    for filename in os.listdir(input_dir):
        # Handle both legacy 'fold_*' and new 'rfd3_*' naming conventions
        if filename.endswith('.pdb'):
            pdb_path = os.path.join(input_dir, filename)
            
            yaml_config = candidate_yaml_config(pdb_path)
            
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
