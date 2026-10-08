#!/usr/bin/env python3
"""
Extract a single chain from a multi-chain PDB file.

This utility is used to filter target PDB files before sending to design pipelines.
When a user selects a specific chain (e.g., Chain I from 7TLY), only that chain
should be passed to RFantibody, BindCraft, and BoltzGen.

Usage:
    python extract_chain.py input.pdb output.pdb --chain I
    python extract_chain.py input.pdb output.pdb --chains A,B  # Multiple chains
"""

import argparse
import sys
from pathlib import Path


def extract_chains(input_pdb: str, output_pdb: str, chains: list[str],
                   renumber: bool = False, new_chain_id: str = None,
                   model_number: int | None = None) -> dict:
    """
    Extract specified chain(s) from a PDB file.
    
    Args:
        input_pdb: Path to input PDB file
        output_pdb: Path to output PDB file  
        chains: List of chain IDs to extract (e.g., ['I'] or ['A', 'B'])
        renumber: If True, renumber residues starting from 1
        new_chain_id: If set, rename all extracted chains to this ID
        
    Returns:
        dict with extraction statistics
    """
    chains_set = set(c.upper() for c in chains)
    
    extracted_lines = []
    atom_count = 0
    residue_numbers = {}  # For renumbering: (chain, orig_resnum) -> new_resnum
    current_new_resnum = 0
    last_resnum = None
    last_chain = None
    inside_model = False
    saw_model_records = False
    keep_current_model = model_number is None
    selected_model_found = model_number is None
    
    with open(input_pdb, 'r') as f:
        for line in f:
            record_type = line[:6].strip()

            if record_type == 'MODEL':
                saw_model_records = True
                inside_model = True
                try:
                    current_model_number = int(line[10:].strip())
                except ValueError:
                    current_model_number = None
                keep_current_model = model_number is None or current_model_number == model_number
                if keep_current_model:
                    selected_model_found = True
                continue

            if record_type == 'ENDMDL':
                inside_model = False
                if model_number is not None and keep_current_model:
                    break
                keep_current_model = model_number is None
                continue

            if inside_model and not keep_current_model:
                continue
            
            # Keep REMARK, CRYST1, etc. header lines
            if record_type in ['REMARK', 'HEADER', 'TITLE', 'COMPND', 'SOURCE', 
                               'KEYWDS', 'EXPDTA', 'AUTHOR', 'CRYST1', 'ORIGX1',
                               'ORIGX2', 'ORIGX3', 'SCALE1', 'SCALE2', 'SCALE3']:
                extracted_lines.append(line)
                continue
            
            # Process ATOM/HETATM lines
            if record_type in ['ATOM', 'HETATM']:
                chain_id = line[21].upper()
                
                if chain_id in chains_set:
                    atom_count += 1
                    
                    if renumber or new_chain_id:
                        # Parse residue number
                        try:
                            orig_resnum = int(line[22:26])
                        except ValueError:
                            orig_resnum = 0
                        
                        # Track residue changes for renumbering
                        if renumber:
                            key = (chain_id, orig_resnum)
                            if key not in residue_numbers:
                                if orig_resnum != last_resnum or chain_id != last_chain:
                                    current_new_resnum += 1
                                residue_numbers[key] = current_new_resnum
                                last_resnum = orig_resnum
                                last_chain = chain_id
                            
                            new_resnum = residue_numbers[key]
                            line = line[:22] + f"{new_resnum:4d}" + line[26:]
                        
                        # Rename chain if requested
                        if new_chain_id:
                            line = line[:21] + new_chain_id[0] + line[22:]
                    
                    extracted_lines.append(line)
            
            # Keep TER records for extracted chains
            elif record_type == 'TER':
                if len(line) > 21:
                    chain_id = line[21].upper()
                    if chain_id in chains_set:
                        if new_chain_id:
                            line = line[:21] + new_chain_id[0] + line[22:]
                        extracted_lines.append(line)
                elif extracted_lines and extracted_lines[-1][:4] == 'ATOM':
                    # TER without chain info - keep if last line was from our chain
                    extracted_lines.append(line)
    
    if model_number is not None and saw_model_records and not selected_model_found:
        raise ValueError(f"Requested model {model_number} not found in {input_pdb}")

    # Add END record
    if extracted_lines and not extracted_lines[-1].startswith('END'):
        extracted_lines.append('END\n')
    
    # Write output
    with open(output_pdb, 'w') as f:
        f.writelines(extracted_lines)
    
    # Calculate statistics
    unique_residues = len(set(residue_numbers.values())) if residue_numbers else 0
    
    return {
        'input_file': str(input_pdb),
        'output_file': str(output_pdb),
        'chains_extracted': list(chains_set),
        'atom_count': atom_count,
        'residue_count': unique_residues or 'unknown',
        'renumbered': renumber,
        'chain_renamed_to': new_chain_id,
        'model_number': model_number,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract chain(s) from a PDB file"
    )
    parser.add_argument("input_pdb", help="Input PDB file")
    parser.add_argument("output_pdb", help="Output PDB file")
    parser.add_argument("--chain", "-c", 
                        help="Single chain ID to extract (e.g., I)")
    parser.add_argument("--chains", 
                        help="Comma-separated chain IDs (e.g., A,B)")
    parser.add_argument("--renumber", "-r", action="store_true",
                        help="Renumber residues starting from 1")
    parser.add_argument("--rename-chain", dest="new_chain_id",
                        help="Rename extracted chain(s) to this ID (e.g., T for target)")
    parser.add_argument("--model-number", type=int,
                        help="Specific MODEL number to extract before chain filtering")
    
    args = parser.parse_args()
    
    # Determine chains to extract
    if args.chain:
        chains = [args.chain]
    elif args.chains:
        chains = [c.strip() for c in args.chains.split(',')]
    else:
        print("Error: Must specify --chain or --chains", file=sys.stderr)
        sys.exit(1)
    
    if not Path(args.input_pdb).exists():
        print(f"Error: Input file not found: {args.input_pdb}", file=sys.stderr)
        sys.exit(1)
    
    result = extract_chains(
        args.input_pdb, 
        args.output_pdb, 
        chains,
        renumber=args.renumber,
        new_chain_id=args.new_chain_id,
        model_number=args.model_number
    )
    
    print(f"Extracted {result['atom_count']} atoms from chain(s) {result['chains_extracted']}")
    print(f"Output: {result['output_file']}")
    
    if result['renumbered']:
        print(f"  Residues renumbered (1-{result['residue_count']})")
    if result['chain_renamed_to']:
        print(f"  Chain renamed to: {result['chain_renamed_to']}")


if __name__ == "__main__":
    main()
