#!/usr/bin/env python3
"""
merge_complex.py - Merge target protein and antibody into a single complex PDB

Prepares input for ColabDesign's binder protocol by combining:
- Target protein (chain T)
- Antibody (chains H and/or L)

Usage:
    python merge_complex.py --target target.pdb --antibody antibody.pdb --output complex.pdb
"""

import argparse
from pathlib import Path


def read_pdb_atoms(pdb_path: Path) -> list[str]:
    """Read ATOM/HETATM records from PDB file."""
    atoms = []
    with open(pdb_path) as f:
        for line in f:
            if line.startswith(('ATOM', 'HETATM')):
                atoms.append(line)
    return atoms


def renumber_residues(atoms: list[str], chain_id: str, start_resnum: int = 1) -> list[str]:
    """Renumber residues sequentially and change chain ID."""
    result = []
    last_resnum = None
    current_resnum = start_resnum
    
    for line in atoms:
        old_resnum = line[22:26].strip()
        
        if old_resnum != last_resnum:
            if last_resnum is not None:
                current_resnum += 1
            last_resnum = old_resnum
        
        # Rebuild line with new chain and residue number
        new_line = (
            line[:21] + 
            chain_id + 
            f"{current_resnum:4d}" + 
            line[26:]
        )
        result.append(new_line)
    
    return result, current_resnum


def merge_complex(target_pdb: Path, antibody_pdb: Path, output_pdb: Path, 
                  target_chain: str = 'T', antibody_chains: str = 'HL'):
    """
    Merge target and antibody PDBs into a single complex.
    
    Args:
        target_pdb: Path to target protein PDB
        antibody_pdb: Path to antibody PDB (may have H/L chains or single chain)
        output_pdb: Path for output complex PDB
        target_chain: Chain ID for target in output (default: T)
        antibody_chains: Chain IDs for antibody in output (default: HL for Fab, H for VHH)
    """
    # Read atoms
    target_atoms = read_pdb_atoms(target_pdb)
    antibody_atoms = read_pdb_atoms(antibody_pdb)
    
    if not target_atoms:
        raise ValueError(f"No ATOM records found in target PDB: {target_pdb}")
    if not antibody_atoms:
        raise ValueError(f"No ATOM records found in antibody PDB: {antibody_pdb}")
    
    # Renumber target as chain T
    target_renumbered, _ = renumber_residues(target_atoms, target_chain)
    
    # Check if antibody has multiple chains
    antibody_chain_ids = set(line[21] for line in antibody_atoms)
    
    if len(antibody_chain_ids) > 1:
        # Multi-chain antibody (Fab) - keep original H/L chain IDs
        antibody_final = []
        for line in antibody_atoms:
            chain = line[21]
            if chain in ('H', 'L'):
                antibody_final.append(line)
            else:
                # Remap other chains to H
                new_line = line[:21] + 'H' + line[22:]
                antibody_final.append(new_line)
    else:
        # Single chain (VHH/nanobody) - use first char of antibody_chains
        binder_chain = antibody_chains[0] if antibody_chains else 'H'
        antibody_final, _ = renumber_residues(antibody_atoms, binder_chain)
    
    # Write merged complex
    with open(output_pdb, 'w') as f:
        f.write(f"REMARK   Merged complex for ColabDesign AF2 backprop\n")
        f.write(f"REMARK   Target chain: {target_chain}\n")
        f.write(f"REMARK   Antibody chains: {antibody_chains}\n")
        
        # Write target first
        for line in target_renumbered:
            f.write(line)
        f.write("TER\n")
        
        # Write antibody
        for line in antibody_final:
            f.write(line)
        f.write("TER\n")
        f.write("END\n")
    
    print(f"Created complex: {output_pdb}")
    print(f"  Target ({target_chain}): {len(target_renumbered)} atoms")
    print(f"  Antibody: {len(antibody_final)} atoms")


def main():
    parser = argparse.ArgumentParser(
        description='Merge target and antibody PDBs into complex for AF2 backprop'
    )
    parser.add_argument('--target', required=True, type=Path,
                        help='Target protein PDB file')
    parser.add_argument('--antibody', required=True, type=Path,
                        help='Antibody PDB file')
    parser.add_argument('--output', required=True, type=Path,
                        help='Output complex PDB file')
    parser.add_argument('--target_chain', default='T',
                        help='Chain ID for target in output (default: T)')
    parser.add_argument('--antibody_chains', default='HL',
                        help='Chain IDs for antibody (default: HL for Fab, H for VHH)')
    
    args = parser.parse_args()
    
    if not args.target.exists():
        raise FileNotFoundError(f"Target PDB not found: {args.target}")
    if not args.antibody.exists():
        raise FileNotFoundError(f"Antibody PDB not found: {args.antibody}")
    
    merge_complex(
        args.target,
        args.antibody,
        args.output,
        args.target_chain,
        args.antibody_chains
    )


if __name__ == '__main__':
    main()
