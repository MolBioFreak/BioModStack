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


def _read_protein_residues(pdb_path: str):
    residues = {}
    with open(pdb_path, 'r') as f:
        for line in f:
            if line.startswith('ATOM') or line.startswith('HETATM'):
                resname = line[17:20].strip()
                if resname not in AA_3TO1:
                    continue
                chain = line[21]
                try:
                    resnum = int(line[22:26].strip())
                except ValueError:
                    continue
                residues[(chain, resnum)] = resname
    return [
        (chain, resnum, residues[(chain, resnum)])
        for chain, resnum in sorted(residues.keys(), key=lambda item: (item[0], item[1]))
    ]


def extract_sequence_from_pdb(pdb_path: str, chain_id: str = None) -> str:
    """Extract protein sequence from a PDB file.
    
    Args:
        pdb_path: Path to PDB file
        chain_id: Optional chain ID to extract (if None, extracts first protein chain)
    
    Returns:
        One-letter amino acid sequence string, or empty string on failure
    """
    try:
        residues = _read_protein_residues(pdb_path)
        if not residues:
            return ''

        if chain_id:
            seq = ''.join(AA_3TO1.get(resname, 'X') for chain, _, resname in residues if chain == chain_id)
        else:
            first_chain = residues[0][0]
            seq = ''.join(AA_3TO1.get(resname, 'X') for chain, _, resname in residues if chain == first_chain)

        return seq
    except Exception as e:
        print(f"Warning: Failed to extract sequence from {pdb_path}: {e}")
        return ''


def extract_sequence_and_position_map_from_pdb(pdb_path: str, chain_id: str = None):
    """Return sequence plus a PDB-residue-number -> 1-indexed-sequence-position map."""
    try:
        residues = _read_protein_residues(pdb_path)
        if not residues:
            return '', None, {}

        available_chains = [chain for chain, _, _ in residues]
        chosen_chain = chain_id if chain_id and chain_id in available_chains else available_chains[0]
        chain_residues = [(resnum, resname) for chain, resnum, resname in residues if chain == chosen_chain]
        sequence = ''.join(AA_3TO1.get(resname, 'X') for resnum, resname in chain_residues)
        position_map = {resnum: idx + 1 for idx, (resnum, _) in enumerate(chain_residues)}
        return sequence, chosen_chain, position_map
    except Exception as e:
        print(f"Warning: Failed to map residues from {pdb_path}: {e}")
        return '', None, {}


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


def _format_position_ranges(positions):
    if not positions:
        return ''

    merged = []
    sorted_positions = sorted(set(int(pos) for pos in positions))
    start = prev = sorted_positions[0]

    for pos in sorted_positions[1:]:
        if pos == prev + 1:
            prev = pos
            continue
        merged.append(str(start) if start == prev else f"{start}..{prev}")
        start = prev = pos

    merged.append(str(start) if start == prev else f"{start}..{prev}")
    return ",".join(merged)


def _map_binding_sites_to_sequence_positions(binding_sites, position_map, chain_id):
    positions = []
    for site_chain, start, end in binding_sites:
        if chain_id and site_chain != chain_id:
            continue
        for residue_number in range(start, end + 1):
            sequence_position = position_map.get(residue_number)
            if sequence_position is not None:
                positions.append(sequence_position)
    return _format_position_ranges(positions)


def _load_nanobody_scaffold_specs(raw_value):
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid nanobody scaffold spec payload: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError("Nanobody scaffold specs must be a JSON list")
    return parsed


def _write_scaffold_yaml_files(output_yaml_path: Path, scaffold_specs):
    scaffold_yaml_names = []
    for index, scaffold in enumerate(scaffold_specs, start=1):
        spec_payload = scaffold.get("spec") if isinstance(scaffold, dict) else None
        if not isinstance(spec_payload, dict):
            continue
        display_name = str(scaffold.get("name") or f"scaffold_{index}").strip() or f"scaffold_{index}"
        safe_name = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in display_name).strip("_") or f"scaffold_{index}"
        scaffold_yaml = output_yaml_path.with_name(f"{safe_name}_{index}.yaml")
        with scaffold_yaml.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(spec_payload, handle, sort_keys=False)
        scaffold_yaml_names.append(scaffold_yaml.name)
    return scaffold_yaml_names


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
    parser.add_argument("--nanobody_scaffold_specs", type=str, help="JSON scaffold spec list for file-backed nanobody mode")
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

    binding_sites = parse_binding_site(args.binding_site_residues)
    unique_binding_site_chains = sorted({chain for chain, _, _ in binding_sites})
    if len(unique_binding_site_chains) > 1:
        print(
            "Warning: Multi-chain binding-site conditioning is not yet scaffold-aware in this prep path; "
            f"using only the mapped target chain context ({', '.join(unique_binding_site_chains)} requested)"
        )
    
    # Convert scaffold length format: "80-120" -> "80..120" (BoltzGen uses ..)
    scaffold_length = args.scaffold_length.replace('-', '..')
    nanobody_scaffold_specs = _load_nanobody_scaffold_specs(args.nanobody_scaffold_specs)

    entities = []
    constraints = []
    target_entity = None
    target_position_map = {}
    target_position_chain = None
    
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
        
        # Target antigen entity (if provided)
        if args.target_pdb and Path(args.target_pdb).exists():
            print(f"  Target antigen: {args.target_pdb}")
            target_chain_hint = unique_binding_site_chains[0] if len(unique_binding_site_chains) == 1 else None
            target_seq, target_position_chain, target_position_map = extract_sequence_and_position_map_from_pdb(
                args.target_pdb,
                target_chain_hint,
            )

            if nanobody_scaffold_specs:
                target_file = {
                    'path': args.target_pdb,
                    'include': [{'chain': {'id': target_position_chain or target_chain_hint or 'all'}}],
                }
                mapped_binding_positions = _map_binding_sites_to_sequence_positions(
                    binding_sites,
                    target_position_map,
                    target_position_chain,
                ) if binding_sites and target_position_map else ''
                if mapped_binding_positions and (target_position_chain or target_chain_hint):
                    target_file['binding_types'] = [
                        {'chain': {'id': target_position_chain or target_chain_hint, 'binding': mapped_binding_positions}}
                    ]
                    print(f"  Applied target binding conditioning via file spec: {mapped_binding_positions}")
                target_entity = {'file': target_file}
                entities.append(target_entity)
            elif target_seq:
                target_entity = {
                    'protein': {
                        'id': 'T',  # Target
                        'path': args.target_pdb,
                        'sequence': target_seq  # Required by BoltzGen schema
                    }
                }
                entities.append(target_entity)
                print(f"  Target sequence: {len(target_seq)} AA")
                if target_position_chain:
                    print(f"  Target conditioning chain: {target_position_chain}")
            else:
                # Fallback: just use path and hope BoltzGen handles it
                print("  Warning: Could not extract sequence from target PDB")
                target_entity = {
                    'protein': {
                        'id': 'T',
                        'path': args.target_pdb
                    }
                }
                entities.append(target_entity)
        elif smiles:
            # Small molecule target
            print(f"  Small molecule target: {smiles[:50]}...")
            entities.append({
                'ligand': {
                    'id': 'L',
                    'smiles': smiles
                }
            })

        if nanobody_scaffold_specs:
            scaffold_yaml_names = _write_scaffold_yaml_files(Path(args.output_yaml), nanobody_scaffold_specs)
            if scaffold_yaml_names:
                scaffold_paths = scaffold_yaml_names if len(scaffold_yaml_names) > 1 else scaffold_yaml_names[0]
                entities.append({'file': {'path': scaffold_paths}})
                print(f"  Scaffold-backed nanobody mode with {len(scaffold_yaml_names)} scaffold spec(s)")
        elif args.nanobody_framework:
            # Protein entity - use framework if provided, else scaffold length range
            framework_seq = args.nanobody_framework

            cdr_h1_range = (args.cdr_h1_length or "6..10").replace('-', '..')
            cdr_h2_range = (args.cdr_h2_length or "8..12").replace('-', '..')
            cdr_h3_range = (args.cdr_h3_length or "10..20").replace('-', '..')

            if 'X' in framework_seq:
                import re
                x_runs = list(re.finditer(r'X+', framework_seq))

                if len(x_runs) >= 3:
                    new_seq = framework_seq
                    for i, cdr_range in enumerate(reversed([cdr_h1_range, cdr_h2_range, cdr_h3_range])):
                        if i < len(x_runs):
                            match = x_runs[-(i + 1)]
                            new_seq = new_seq[:match.start()] + cdr_range + new_seq[match.end():]
                    framework_seq = new_seq
                    print(f"  Masked CDRs with length ranges: CDR1={cdr_h1_range}, CDR2={cdr_h2_range}, CDR3={cdr_h3_range}")
                else:
                    framework_seq = re.sub(r'X+', cdr_h3_range, framework_seq)
                    print(f"  Replaced X markers with length range: {cdr_h3_range}")
            else:
                seq_len = len(framework_seq)
                if seq_len >= 110:
                    fr1 = framework_seq[:26]
                    fr2 = framework_seq[38:55]
                    fr3 = framework_seq[65:104]
                    fr4 = framework_seq[117:]
                    framework_seq = f"{fr1}{cdr_h1_range}{fr2}{cdr_h2_range}{fr3}{cdr_h3_range}{fr4}"
                    print("  Scaffold redesign: Inserted CDR length ranges at IMGT positions")
                    print(f"    CDR1={cdr_h1_range}, CDR2={cdr_h2_range}, CDR3={cdr_h3_range}")
                else:
                    print(f"  Warning: Framework sequence ({seq_len} AA) shorter than typical VHH - using de novo mode")
                    framework_seq = '110..130'

            entities.append({
                'protein': {
                    'id': 'H',
                    'sequence': framework_seq
                }
            })
            print(
                f"  Final VHH sequence spec: {framework_seq[:50]}..."
                if len(framework_seq) > 50
                else f"  Final VHH sequence spec: {framework_seq}"
            )
            print("  Using sequence-template nanobody mode - skipping auto secondary structure constraints")
        else:
            entities.append({
                'protein': {
                    'id': 'H',
                    'sequence': '120..130'
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
    if binding_sites:
        print(f"Binding site constraints: {binding_sites}")
        if target_entity and 'protein' in target_entity and target_position_map:
            mapped_binding_positions = _map_binding_sites_to_sequence_positions(
                binding_sites,
                target_position_map,
                target_position_chain,
            )
            if mapped_binding_positions:
                target_entity['protein']['binding_types'] = {
                    'binding': mapped_binding_positions
                }
                print(
                    "  Applied target binding conditioning "
                    f"({target_position_chain or 'entity'} -> {mapped_binding_positions})"
                )
            else:
                print("  Warning: Binding-site residues did not map onto the target sequence; skipping conditioning")
        else:
            print("  Warning: Binding-site residues were provided without a protein target context; skipping conditioning")
    
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
