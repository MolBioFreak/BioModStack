import argparse
import yaml
import json
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
    parser.add_argument("--protein_sequence", type=str, help="Protein sequence for co-folding")
    parser.add_argument("--dna_template_seq", type=str, help="DNA template sequence")
    parser.add_argument("--dna_primer_seq", type=str, help="DNA primer sequence")
    parser.add_argument("--dna_structure", type=str, help="Pre-generated DNA structure PDB")
    parser.add_argument("--secondary_structure", type=str, help="Secondary structure constraints (e.g., 'helix:1-20,sheet:25-35,loop:40-45')")
    parser.add_argument("--protocol", type=str, default="protein-anything", 
                        choices=["protein-anything", "peptide-anything", "protein-small_molecule", "nanobody-anything", "antibody-anything"],
                        help="BoltzGen protocol to use")
    parser.add_argument("--covalent_bonds", type=str, help="JSON array of covalent bond constraints")
    parser.add_argument("--output_yaml", type=str, required=True, help="Output YAML file")
    
    args = parser.parse_args()
    
    # Auto-extract DNA sequences from oligo filename if not provided
    # Supports naming patterns: ssDNA_CCCC.pdb, dsDNA_ATCG_CGAT.pdb
    if args.dna_structure and Path(args.dna_structure).exists():
        oligo_stem = Path(args.dna_structure).stem  # e.g., "ssDNA_CCCC"
        
        if not args.dna_template_seq:
            if oligo_stem.startswith("ssDNA_"):
                # Single-stranded: ssDNA_CCCC -> template = "CCCC"
                extracted_seq = oligo_stem.replace("ssDNA_", "")
                args.dna_template_seq = extracted_seq
                print(f"Auto-extracted ssDNA template sequence: {extracted_seq}")
                
            elif oligo_stem.startswith("dsDNA_"):
                # Double-stranded: dsDNA_ATCG_CGAT -> template = "ATCG", primer = "CGAT"
                parts = oligo_stem.replace("dsDNA_", "").split("_")
                if parts:
                    args.dna_template_seq = parts[0]
                    print(f"Auto-extracted dsDNA template sequence: {parts[0]}")
                    
                    if len(parts) > 1 and not args.dna_primer_seq:
                        args.dna_primer_seq = parts[1]
                        print(f"Auto-extracted dsDNA primer sequence: {parts[1]}")
    
    # Resolve SMILES from ligand_smiles or ntp_type
    smiles = args.ligand_smiles
    if not smiles and args.ntp_type:
        smiles = NTP_TEMPLATES.get(args.ntp_type)
    
    # Convert scaffold length format: "80-120" -> "80..120" (BoltzGen uses ..)
    scaffold_length = args.scaffold_length.replace('-', '..')
    
    entities = []
    constraints = []
    
    # Mode 4: DNA-Protein Complex Prediction
    if args.protein_sequence and args.dna_template_seq:
        print(f"Mode: DNA-Protein Complex Prediction")
        # Protein entity
        entities.append({
            'protein': {
                'id': 'A',
                'sequence': args.protein_sequence
            }
        })
        # DNA Template Entity
        dna_template = {'id': 'B'}
        if args.dna_structure and Path(args.dna_structure).exists():
             # If PDB provided, maybe use it? But Boltz usually takes seqs for co-folding
             # For now, just use sequence. If structure is meant to be a constraint/template,
             # BoltzGen schema might differ. We will stick to sequence-based co-folding.
             pass
        dna_template['sequence'] = args.dna_template_seq
        entities.append({'dna': dna_template})
        
        # DNA Primer Entity
        if args.dna_primer_seq:
            entities.append({
                'dna': {
                    'id': 'C',
                    'sequence': args.dna_primer_seq
                }
            })
            
    # Mode 1: Backbone docking - use existing protein structure
    elif args.input_pdb and Path(args.input_pdb).exists():
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
    
    # Parse covalent bond constraints (disulfides, WHL staples, custom)
    if args.covalent_bonds:
        try:
            bonds = json.loads(args.covalent_bonds)
            for bond in bonds:
                # BoltzGen format: bond: atom1: [chain, residue, atom_name], atom2: [chain, residue, atom_name]
                constraints.append({
                    'bond': {
                        'atom1': [bond['atom1_chain'], bond['atom1_residue'], bond['atom1_atom']],
                        'atom2': [bond['atom2_chain'], bond['atom2_residue'], bond['atom2_atom']]
                    }
                })
                print(f"Covalent bond: {bond['type']} - {bond['atom1_chain']}:{bond['atom1_residue']}:{bond['atom1_atom']} <-> {bond['atom2_chain']}:{bond['atom2_residue']}:{bond['atom2_atom']}")
                
                # If WHL staple, add WHL ligand entity
                if bond.get('type') == 'whl_staple':
                    # Check if WHL already added
                    has_whl = any(e.get('ligand', {}).get('ccd') == 'WHL' for e in entities)
                    if not has_whl:
                        entities.append({
                            'ligand': {
                                'id': 'W',
                                'ccd': 'WHL'
                            }
                        })
                        print("Added WHL ligand entity for staple")
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse covalent_bonds JSON: {e}")
    
    config = {'entities': entities}
    if constraints:
        config['constraints'] = constraints
    
    # Add secondary structure constraints
    if args.secondary_structure:
        ss_map = {'helix': [], 'sheet': [], 'loop': []}
        for part in args.secondary_structure.split(','):
            if ':' in part:
                ss_type, residues = part.split(':', 1)
                ss_type = ss_type.strip().lower()
                if ss_type in ss_map:
                    if '-' in residues:
                        start, end = residues.split('-')
                        ss_map[ss_type].append(f"{start}..{end}")
                    else:
                        ss_map[ss_type].append(residues.strip())
        
        # Add to first protein entity's secondary_structure block
        if any(ss_map.values()):
            secondary_structure = {}
            for ss_type, ranges in ss_map.items():
                if ranges:
                    secondary_structure[ss_type] = ','.join(ranges)
            
            # Find first protein entity and add secondary_structure
            for entity in entities:
                if 'protein' in entity:
                    entity['protein']['secondary_structure'] = secondary_structure
                    break
            print(f"Secondary structure constraints: {secondary_structure}")
    
    with open(args.output_yaml, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)
    
    print(f"Generated BoltzGen design spec: {args.output_yaml}")
    print(f"  Entities: {len(entities)}")
    print(f"  Protocol: {args.protocol}")
    if constraints:
        print(f"  Constraints: {len(constraints)}")

if __name__ == "__main__":
    main()
