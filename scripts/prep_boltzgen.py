import argparse
import yaml
import sys

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

def main():
    parser = argparse.ArgumentParser(description="Prepare BoltzGen YAML design spec")
    parser.add_argument("--ligand_smiles", type=str, help="Target ligand SMILES")
    parser.add_argument("--ntp_type", type=str, choices=NTP_TEMPLATES.keys(), help="NTP template type")
    parser.add_argument("--scaffold_length", type=str, default="80-120", help="Scaffold length range")
    parser.add_argument("--num_designs", type=int, default=10, help="Number of designs")
    parser.add_argument("--output_yaml", type=str, required=True, help="Output YAML file")
    
    args = parser.parse_args()
    
    # Resolve SMILES
    smiles = args.ligand_smiles
    if not smiles and args.ntp_type:
        smiles = NTP_TEMPLATES.get(args.ntp_type)
    
    # Convert scaffold length format: "80-120" -> "80..120" (BoltzGen uses ..)
    scaffold_length = args.scaffold_length.replace('-', '..')
    
    # BoltzGen valid schema format (from example):
    # entities:
    #   - protein:
    #       id: binder
    #       sequence: 80..120  # Range format with ..
    #   - ligand:
    #       id: target
    #       smiles: <SMILES>
    
    entities = []
    
    # Add designed protein binder
    entities.append({
        'protein': {
            'id': 'A',  # Single char chain ID
            'sequence': scaffold_length  # e.g., "80..120"
        }
    })
    
    # Add target ligand if provided
    if smiles:
        entities.append({
            'ligand': {
                'id': 'L',  # Single char chain ID
                'smiles': smiles
            }
        })
    
    config = {'entities': entities}

    with open(args.output_yaml, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    
    print(f"Generated BoltzGen design spec: {args.output_yaml}")

if __name__ == "__main__":
    main()
