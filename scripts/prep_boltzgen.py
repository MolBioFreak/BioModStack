import argparse
import yaml
import sys
from pathlib import Path

# Shared NTP templates (SMILES strings for nucleotides)
NTP_TEMPLATES = {
    'dATP': 'Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3',
    'dTTP': 'Cc1cn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)[nH]c1=O',
    'dGTP': 'Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3)c(=O)[nH]1',
    'dCTP': 'Nc1ccn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)n1',
    'ATP': 'Nc1ncnc2c1ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O',
    'UTP': 'O=c1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)[nH]1',
    'GTP': 'Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1',
    'CTP': 'Nc1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)n1'
}

# Mg2+ coordination template for catalytic sites
MG_COORD_SMILES = '[Mg+2]'


def parse_binding_site(binding_site_str):
    """Parse binding site residue specification.
    
    Format: 'A:45-52,A:78-85' -> list of (chain, start, end) tuples
    """
    if not binding_site_str:
        return []
    
    sites = []
    for part in binding_site_str.split(','):
        if ':' in part:
            chain, residues = part.split(':', 1)
            if '-' in residues:
                start, end = residues.split('-')
                sites.append((chain.strip(), int(start), int(end)))
            else:
                res = int(residues)
                sites.append((chain.strip(), res, res))
    return sites


def main():
    parser = argparse.ArgumentParser(description="Prepare BoltzGen YAML design spec")
    parser.add_argument("--ligand_smiles", type=str, help="Target ligand SMILES")
    parser.add_argument("--ntp_type", type=str, choices=list(NTP_TEMPLATES.keys()), help="NTP template type")
    parser.add_argument("--scaffold_length", type=str, default="80-120", help="Scaffold length range")
    parser.add_argument("--num_designs", type=int, default=10, help="Number of designs")
    parser.add_argument("--binding_site_residues", type=str, help="Binding pocket specification (e.g. 'A:45-52,A:78-85')")
    parser.add_argument("--catalytic_site", action="store_true", help="Include Mg2+ coordination for enzyme design")
    parser.add_argument("--input_pdb", type=str, help="Existing protein backbone for docking mode")
    parser.add_argument("--ligand_pdb", type=str, help="Ligand structure with 3D coordinates")
    parser.add_argument("--output_yaml", type=str, required=True, help="Output YAML file")
    
    args = parser.parse_args()
    
    # Resolve SMILES
    smiles = args.ligand_smiles
    if not smiles and args.ntp_type:
        smiles = NTP_TEMPLATES.get(args.ntp_type)
    
    # Convert scaffold length format: "80-120" -> "80..120" (BoltzGen uses ..)
    scaffold_length = args.scaffold_length.replace('-', '..')
    
    entities = []
    constraints = []
    
    # Mode 1: Backbone docking - use existing protein structure
    if args.input_pdb and Path(args.input_pdb).exists():
        print(f"Mode: Backbone docking with existing structure: {args.input_pdb}")
        # Read sequence from PDB (simplified - just use as template)
        entities.append({
            'protein': {
                'id': 'A',
                'pdb': args.input_pdb  # BoltzGen may accept PDB paths
            }
        })
    # Mode 2: Scaffold around ligand - fixed ligand pose
    elif args.ligand_pdb and Path(args.ligand_pdb).exists():
        print(f"Mode: Scaffold around fixed ligand: {args.ligand_pdb}")
        entities.append({
            'protein': {
                'id': 'A',
                'sequence': scaffold_length
            }
        })
        entities.append({
            'ligand': {
                'id': 'L',
                'pdb': args.ligand_pdb
            }
        })
    # Mode 3: Standard de-novo design with SMILES
    else:
        entities.append({
            'protein': {
                'id': 'A',
                'sequence': scaffold_length
            }
        })
        
        if smiles:
            entities.append({
                'ligand': {
                    'id': 'L',
                    'smiles': smiles
                }
            })
    
    # Add Mg2+ for catalytic site if requested
    if args.catalytic_site:
        print("Adding Mg2+ coordination for catalytic site")
        entities.append({
            'ligand': {
                'id': 'M',
                'smiles': MG_COORD_SMILES
            }
        })
    
    # Parse binding site constraints
    if args.binding_site_residues:
        sites = parse_binding_site(args.binding_site_residues)
        if sites:
            print(f"Binding site constraints: {sites}")
            # Add as pocket specification (format depends on BoltzGen API)
            constraints.append({
                'binding_pocket': {
                    'residues': args.binding_site_residues
                }
            })
    
    config = {'entities': entities}
    if constraints:
        config['constraints'] = constraints
    
    with open(args.output_yaml, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    
    print(f"Generated BoltzGen design spec: {args.output_yaml}")
    print(f"  Entities: {len(entities)}")
    if constraints:
        print(f"  Constraints: {len(constraints)}")

if __name__ == "__main__":
    main()
