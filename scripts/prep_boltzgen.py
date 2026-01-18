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

# 3-letter to 1-letter amino acid mapping
AA_3TO1 = {
    'ALA': 'A', 'ARG': 'R', 'ASN': 'N', 'ASP': 'D', 'CYS': 'C',
    'GLN': 'Q', 'GLU': 'E', 'GLY': 'G', 'HIS': 'H', 'ILE': 'I',
    'LEU': 'L', 'LYS': 'K', 'MET': 'M', 'PHE': 'F', 'PRO': 'P',
    'SER': 'S', 'THR': 'T', 'TRP': 'W', 'TYR': 'Y', 'VAL': 'V',
    # Non-standard
    'MSE': 'M', 'SEC': 'C', 'PYL': 'K'
}


def extract_sequence_from_pdb(pdb_path: str, chain_id: str = None) -> str:
    """Extract protein sequence from a PDB file.
    
    Args:
        pdb_path: Path to PDB file
        chain_id: Optional chain ID to extract (if None, extracts first protein chain)
    
    Returns:
        One-letter amino acid sequence string, or empty string on failure
    """
    try:
        residues = {}  # (chain, resnum) -> resname
        with open(pdb_path, 'r') as f:
            for line in f:
                if line.startswith('ATOM') or line.startswith('HETATM'):
                    resname = line[17:20].strip()
                    if resname not in AA_3TO1:
                        continue  # Skip non-protein residues
                    chain = line[21]
                    if chain_id and chain != chain_id:
                        continue
                    try:
                        resnum = int(line[22:26].strip())
                    except ValueError:
                        continue
                    residues[(chain, resnum)] = resname
        
        if not residues:
            return ''
        
        # Sort by chain, then residue number
        sorted_res = sorted(residues.items(), key=lambda x: (x[0][0], x[0][1]))
        
        # If no chain specified, use all residues (typical for single-chain files)
        if chain_id:
            seq = ''.join(AA_3TO1.get(r[1], 'X') for r in sorted_res)
        else:
            # Just get the first chain
            first_chain = sorted_res[0][0][0]
            seq = ''.join(AA_3TO1.get(r[1], 'X') for r in sorted_res if r[0][0] == first_chain)
        
        return seq
    except Exception as e:
        print(f"Warning: Failed to extract sequence from {pdb_path}: {e}")
        return ''


def parse_binding_site(binding_site_str):
    """Parse binding site residue specification.
    
    Supports multiple formats:
    - Frontend format: 'A45,A46,B100' (chain letter directly before residue number)
    - Backend format: 'A:45-52,A:78-85' (chain:residue or chain:start-end)
    
    Returns: list of (chain, start, end) tuples
    """
    if not binding_site_str:
        return []
    
    sites = []
    for part in binding_site_str.split(','):
        part = part.strip()
        if not part:
            continue
            
        if ':' in part:
            # Backend format: A:45 or A:45-52
            chain, residues = part.split(':', 1)
            if '-' in residues:
                start, end = residues.split('-')
                sites.append((chain.strip(), int(start), int(end)))
            else:
                res = int(residues)
                sites.append((chain.strip(), res, res))
        else:
            # Frontend format: A45, B100 (letter followed by number)
            import re
            match = re.match(r'^([A-Z])(\d+)(?:-(\d+))?$', part)
            if match:
                chain = match.group(1)
                start = int(match.group(2))
                end = int(match.group(3)) if match.group(3) else start
                sites.append((chain, start, end))
    
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
    
    # Nanobody-specific arguments
    parser.add_argument("--nanobody_framework", type=str, help="VHH framework sequence template (X marks CDR positions)")
    parser.add_argument("--cdr_h1_length", type=str, default="5-8", help="CDR-H1 length range (e.g., '5-8')")
    parser.add_argument("--cdr_h2_length", type=str, default="6-10", help="CDR-H2 length range (e.g., '6-10')")
    parser.add_argument("--cdr_h3_length", type=str, default="12-18", help="CDR-H3 length range (e.g., '12-18')")
    parser.add_argument("--target_pdb", type=str, help="Target antigen PDB file for nanobody/antibody design")
    
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
        # BoltzGen requires BOTH path AND sequence for PDB-loaded entities
        input_seq = extract_sequence_from_pdb(args.input_pdb)
        if input_seq:
            entities.append({
                'protein': {
                    'id': 'A',
                    'path': args.input_pdb,
                    'sequence': input_seq
                }
            })
            print(f"  Backbone sequence: {len(input_seq)} AA")
        else:
            print("  Warning: Could not extract sequence from input PDB")
            entities.append({
                'protein': {
                    'id': 'A',
                    'path': args.input_pdb
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
                'path': args.ligand_pdb  # Use 'path' for PDB files
            }
        })
    # Mode 5: Nanobody design (VHH)
    elif args.nanobody_framework or args.protocol == 'nanobody-anything':
        print(f"Mode: Nanobody (VHH) design")
        
        # Protein entity - use framework if provided, else scaffold length range
        if args.nanobody_framework:
            # BoltzGen proper scaffold-constrained design:
            # Replace CDR regions with length ranges (e.g., "6..10") in the sequence
            # This tells BoltzGen to design those positions while keeping framework fixed
            # 
            # IMGT VHH CDR positions (approximate, 0-indexed):
            # CDR-H1: positions 26-35 (10 residues typical)
            # CDR-H2: positions 50-65 (16 residues typical)  
            # CDR-H3: positions 95-115 (variable, 8-25 residues)
            
            framework_seq = args.nanobody_framework
            
            # Parse CDR length ranges
            cdr_h1_range = args.cdr_h1_length if args.cdr_h1_length else "6..10"
            cdr_h2_range = args.cdr_h2_length if args.cdr_h2_length else "8..12"
            cdr_h3_range = args.cdr_h3_length if args.cdr_h3_length else "10..20"
            
            # Convert "6-10" to "6..10" (BoltzGen format)
            cdr_h1_range = cdr_h1_range.replace('-', '..')
            cdr_h2_range = cdr_h2_range.replace('-', '..')
            cdr_h3_range = cdr_h3_range.replace('-', '..')
            
            # If framework contains X markers, use those positions
            if 'X' in framework_seq:
                # Count X runs and replace with length ranges
                import re
                x_runs = list(re.finditer(r'X+', framework_seq))
                
                if len(x_runs) >= 3:
                    # 3 CDR regions marked
                    new_seq = framework_seq
                    # Replace from end to preserve indices
                    for i, cdr_range in enumerate(reversed([cdr_h1_range, cdr_h2_range, cdr_h3_range])):
                        if i < len(x_runs):
                            match = x_runs[-(i+1)]
                            new_seq = new_seq[:match.start()] + cdr_range + new_seq[match.end():]
                    framework_seq = new_seq
                    print(f"  Masked CDRs with length ranges: CDR1={cdr_h1_range}, CDR2={cdr_h2_range}, CDR3={cdr_h3_range}")
                else:
                    # Replace all X with single length range
                    framework_seq = re.sub(r'X+', cdr_h3_range, framework_seq)
                    print(f"  Replaced X markers with length range: {cdr_h3_range}")
            else:
                # Full sequence provided - need to insert CDR length ranges at IMGT positions
                # This is scaffold redesign mode - replace CDR positions with length ranges
                # 
                # Typical VHH positions (IMGT):
                # FR1: 1-26, CDR1: 27-38, FR2: 39-55, CDR2: 56-65, FR3: 66-104, CDR3: 105-117, FR4: 118-128
                # Converting to 0-indexed: FR1: 0-25, CDR1: 26-37, FR2: 38-54, CDR2: 55-64, FR3: 65-103, CDR3: 104-116, FR4: 117+
                
                seq_len = len(framework_seq)
                if seq_len >= 110:  # Typical VHH length
                    # Extract framework regions and insert CDR length ranges
                    fr1 = framework_seq[:26]      # FR1 (fixed)
                    fr2 = framework_seq[38:55]    # FR2 (fixed) 
                    fr3 = framework_seq[65:104]   # FR3 (fixed)
                    fr4 = framework_seq[117:]     # FR4 (fixed)
                    
                    # Construct hybrid sequence: FR1 + CDR1_range + FR2 + CDR2_range + FR3 + CDR3_range + FR4
                    framework_seq = f"{fr1}{cdr_h1_range}{fr2}{cdr_h2_range}{fr3}{cdr_h3_range}{fr4}"
                    print(f"  Scaffold redesign: Inserted CDR length ranges at IMGT positions")
                    print(f"    CDR1={cdr_h1_range}, CDR2={cdr_h2_range}, CDR3={cdr_h3_range}")
                else:
                    # Sequence too short - use as-is but warn
                    print(f"  Warning: Framework sequence ({seq_len} AA) shorter than typical VHH - using de novo mode")
                    framework_seq = '110..130'  # Fall back to de novo
            
            entities.append({
                'protein': {
                    'id': 'H',  # Heavy chain / VHH
                    'sequence': framework_seq
                }
            })
            print(f"  Final VHH sequence spec: {framework_seq[:50]}..." if len(framework_seq) > 50 else f"  Final VHH sequence spec: {framework_seq}")
        else:
            # De novo - use VHH-typical length range
            entities.append({
                'protein': {
                    'id': 'H',
                    'sequence': '110..130'  # Typical VHH length
                }
            })
        
        # Target antigen entity (if provided)
        if args.target_pdb and Path(args.target_pdb).exists():
            print(f"  Target antigen: {args.target_pdb}")
            # BoltzGen requires BOTH path AND sequence for PDB-loaded entities
            target_seq = extract_sequence_from_pdb(args.target_pdb)
            if target_seq:
                entities.append({
                    'protein': {
                        'id': 'T',  # Target
                        'path': args.target_pdb,
                        'sequence': target_seq  # Required by BoltzGen schema
                    }
                })
                print(f"  Target sequence: {len(target_seq)} AA")
            else:
                # Fallback: just use path and hope BoltzGen handles it
                print("  Warning: Could not extract sequence from target PDB")
                entities.append({
                    'protein': {
                        'id': 'T',
                        'path': args.target_pdb
                    }
                })
        elif smiles:
            # Small molecule target
            print(f"  Small molecule target: {smiles[:50]}...")
            entities.append({
                'ligand': {
                    'id': 'L',
                    'smiles': smiles
                }
            })
        
        # Generate CDR secondary structure constraints
        # NOTE: Only apply when using a length range (de novo), NOT when using a full framework sequence
        # BoltzGen only allows secondary structure constraints on positions that will be designed
        if not args.nanobody_framework:  # De novo mode with length range
            cdr_constraints = []
            # Note: These are approximate IMGT positions for VHH
            # CDR-H1: ~26-35, CDR-H2: ~50-65, CDR-H3: ~95-102+
            if args.cdr_h1_length:
                cdr_constraints.append(f"loop:26-33")  # CDR-H1 region
            if args.cdr_h2_length:
                cdr_constraints.append(f"loop:50-58")  # CDR-H2 region
            if args.cdr_h3_length:
                cdr_constraints.append(f"loop:95-115")  # CDR-H3 region (most variable)
            
            if cdr_constraints and not args.secondary_structure:
                # Only add if not already specified
                args.secondary_structure = ','.join(cdr_constraints)
                print(f"  CDR constraints: {args.secondary_structure}")
        else:
            # When using a framework sequence, we don't add secondary structure constraints
            # The framework already defines the structure
            print(f"  Using full framework - skipping secondary structure constraints")
            
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
            # Set include_proximity on the binder entity to bias towards these residues
            # Format: include_proximity on the target entity with binder as reference
            # Note: BoltzGen uses entity-level include_proximity or design-level constraints
            # For now, we'll use include_proximity format on the binder entity
            for entity in entities:
                if 'protein' in entity and entity['protein'].get('id') in ['A', 'H']:  # Binder
                    # Add include_proximity to binder to encourage contacts with target residues
                    entity['protein']['include_proximity'] = {
                        'chain': sites[0][0],  # Reference chain (target)
                        'res_index': sites[0][1],  # Reference residue
                        'radius': 10  # 10 angstroms
                    }
                    break
    
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
