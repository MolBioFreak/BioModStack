#!/usr/bin/env python3
"""
prep_unidock.py - Prepare inputs for Uni-Dock GPU docking

Features:
- Converts receptor PDB to PDBQT using Meeko
- Handles flexible receptor residues (sidechains)
- Generates 3D ligand conformers from SMILES
- Computes docking box (auto-center or specified)
- Supports NTP template library

Usage:
    python prep_unidock.py --input_pdb receptor.pdb \
        --ligand_smiles "CCO" --box_size 25 --out_dir ./prep
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple
import numpy as np

# NTP SMILES templates (matching DiffDock templates)
NTP_TEMPLATES = {
    'dATP': 'Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3',
    'dTTP': 'Cc1cn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)[nH]c1=O',
    'dGTP': 'Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3)c(=O)[nH]1',
    'dCTP': 'Nc1ccn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)n1',
    'ATP': 'Nc1ncnc2c1ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O',
    'UTP': 'O=c1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)[nH]1',
    'GTP': 'Nc1nc2c(ncn2[C@@H]3O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]3O)c(=O)[nH]1',
    'CTP': 'Nc1ccn([C@@H]2O[C@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)[C@@H](O)[C@H]2O)c(=O)n1',
}


def prepare_receptor_pdbqt(pdb_path: Path, out_dir: Path, 
                           flexible_residues: Optional[str] = None) -> Tuple[Path, Optional[Path]]:
    """
    Convert receptor PDB to PDBQT using Meeko.
    
    Args:
        pdb_path: Input PDB file
        out_dir: Output directory
        flexible_residues: Comma-separated residues like "A:42,A:89"
    
    Returns:
        Tuple of (rigid_pdbqt_path, flexible_pdbqt_path or None)
    """
    from meeko import PDBQTWriterLegacy
    from meeko import RDKitMolCreate
    try:
        from meeko import PDBQTReceptor
        USE_MEEKO_RECEPTOR = True
    except ImportError:
        USE_MEEKO_RECEPTOR = False
    
    receptor_pdbqt = out_dir / "receptor.pdbqt"
    flex_pdbqt = None
    
    if USE_MEEKO_RECEPTOR and flexible_residues:
        # Parse flexible residues
        flex_list = []
        for res_spec in flexible_residues.split(','):
            res_spec = res_spec.strip()
            if ':' in res_spec:
                chain, resid = res_spec.split(':')
                flex_list.append((chain.strip(), int(resid.strip())))
        
        print(f"Preparing receptor with {len(flex_list)} flexible residues: {flex_list}")
        
        # Use Meeko's flexible receptor preparation
        rec = PDBQTReceptor(str(pdb_path))
        for chain, resid in flex_list:
            rec.set_flexible(chain, resid)
        
        # Write rigid and flexible parts
        rigid_pdbqt, flex_pdbqt_content = rec.write_pdbqt_string()
        receptor_pdbqt.write_text(rigid_pdbqt)
        
        if flex_pdbqt_content:
            flex_pdbqt = out_dir / "receptor_flex.pdbqt"
            flex_pdbqt.write_text(flex_pdbqt_content)
            print(f"Wrote flexible receptor: {flex_pdbqt}")
    else:
        # Standard rigid receptor - use command-line tool or OpenBabel fallback
        print("Preparing rigid receptor...")
        try:
            # Try mk_prepare_receptor.py from Meeko
            import subprocess
            result = subprocess.run([
                'mk_prepare_receptor.py',
                '-i', str(pdb_path),
                '-o', str(receptor_pdbqt),
            ], capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"mk_prepare_receptor failed: {result.stderr}")
        except (FileNotFoundError, RuntimeError) as e:
            print(f"Meeko CLI failed ({e}), using OpenBabel fallback...")
            # OpenBabel fallback - use 'r' option for rigid receptor format
            from openbabel import openbabel as ob
            conv = ob.OBConversion()
            conv.SetInAndOutFormats("pdb", "pdbqt")
            # 'r' option tells OpenBabel to treat as receptor (no ROOT/BRANCH/ENDROOT)
            conv.AddOption("r", ob.OBConversion.OUTOPTIONS)
            mol = ob.OBMol()
            conv.ReadFile(mol, str(pdb_path))
            conv.WriteFile(mol, str(receptor_pdbqt))
            
            # Verify output doesn't have ROOT tag (sanity check)
            with open(receptor_pdbqt, 'r') as f:
                content = f.read()
            if 'ROOT' in content:
                print("Warning: Receptor PDBQT has ROOT tag, stripping...")
                # Strip all ligand-specific lines
                lines = content.split('\n')
                clean_lines = [l for l in lines if not l.startswith(('ROOT', 'ENDROOT', 'BRANCH', 'ENDBRANCH', 'TORSDOF', 'REMARK  Name', 'REMARK  1', 'REMARK  2', 'REMARK  3', 'REMARK  4', 'REMARK  5', 'REMARK  6', 'REMARK  7', 'REMARK  8', 'REMARK  9', 'REMARK  status', 'REMARK    '))]
                with open(receptor_pdbqt, 'w') as f:
                    f.write('\n'.join(clean_lines))
    
    print(f"Wrote receptor PDBQT: {receptor_pdbqt}")
    return receptor_pdbqt, flex_pdbqt


def prepare_ligand_pdbqt(smiles: str, name: str, out_dir: Path) -> Path:
    """
    Generate 3D conformer from SMILES and convert to PDBQT.
    
    Args:
        smiles: SMILES string
        name: Ligand name for output file
        out_dir: Output directory
        
    Returns:
        Path to PDBQT file
    """
    from rdkit import Chem
    from rdkit.Chem import AllChem
    
    # Generate 3D structure
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Failed to parse SMILES: {smiles}")
    
    mol = Chem.AddHs(mol)
    
    # Generate 3D conformer using ETKDGv3
    params = AllChem.ETKDGv3()
    params.randomSeed = 42
    result = AllChem.EmbedMolecule(mol, params)
    if result == -1:
        # Try with random coordinates as fallback
        result = AllChem.EmbedMolecule(mol, randomSeed=42)
        if result == -1:
            raise ValueError(f"Failed to generate 3D conformer for: {smiles[:50]}...")
    
    # Optimize geometry with MMFF
    try:
        AllChem.MMFFOptimizeMolecule(mol, maxIters=500)
    except:
        try:
            AllChem.UFFOptimizeMolecule(mol, maxIters=500)
        except:
            pass  # Continue without optimization
    
    pdbqt_path = out_dir / f"{name}.pdbqt"
    
    # Try Meeko first (preferred for docking)
    try:
        from meeko import MoleculePreparation, PDBQTWriterLegacy
        preparator = MoleculePreparation()
        mol_setups = preparator.prepare(mol)
        
        # Meeko 0.6+ returns a list of MoleculeSetup objects
        if isinstance(mol_setups, list):
            if len(mol_setups) == 0:
                raise ValueError("Meeko returned empty list")
            mol_setup = mol_setups[0]
        else:
            mol_setup = mol_setups
        
        pdbqt_string = PDBQTWriterLegacy.write_string(mol_setup)[0]
        pdbqt_path.write_text(pdbqt_string)
        print(f"  Ligand '{name}' (Meeko): {smiles[:40]}... -> {pdbqt_path}")
        return pdbqt_path
        
    except Exception as meeko_err:
        print(f"  Meeko failed for '{name}': {meeko_err}, trying OpenBabel...")
    
    # Fallback: Use OpenBabel for PDBQT conversion
    try:
        from openbabel import openbabel as ob
        
        # First write PDB
        pdb_path = out_dir / f"{name}_temp.pdb"
        pdb_block = Chem.MolToPDBBlock(mol)
        pdb_path.write_text(pdb_block)
        
        # Convert PDB to PDBQT using OpenBabel
        obConversion = ob.OBConversion()
        obConversion.SetInAndOutFormats("pdb", "pdbqt")
        
        obMol = ob.OBMol()
        obConversion.ReadFile(obMol, str(pdb_path))
        
        # Add hydrogens and compute partial charges (Gasteiger)
        obMol.AddHydrogens()
        
        # Write PDBQT
        obConversion.WriteFile(obMol, str(pdbqt_path))
        
        # Clean up temp file
        pdb_path.unlink()
        
        print(f"  Ligand '{name}' (OpenBabel): {smiles[:40]}... -> {pdbqt_path}")
        return pdbqt_path
        
    except Exception as ob_err:
        raise ValueError(f"Both Meeko and OpenBabel failed for '{name}': {ob_err}")




def compute_box_center(pdb_path: Path, ligand_coords: Optional[np.ndarray] = None) -> Tuple[float, float, float]:
    """
    Compute docking box center.
    
    Priority:
    1. Ligand centroid (if provided)
    2. Geometric center of receptor
    """
    from Bio.PDB import PDBParser
    
    if ligand_coords is not None and len(ligand_coords) > 0:
        centroid = ligand_coords.mean(axis=0)
        return tuple(centroid)
    
    # Fall back to receptor center
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure('receptor', str(pdb_path))
    
    coords = []
    for atom in structure.get_atoms():
        coords.append(atom.coord)
    
    coords = np.array(coords)
    centroid = coords.mean(axis=0)
    
    return tuple(centroid)


def parse_box_center(box_center_str: str) -> Tuple[float, float, float]:
    """Parse 'x,y,z' string to tuple."""
    parts = box_center_str.split(',')
    return (float(parts[0]), float(parts[1]), float(parts[2]))


def main():
    parser = argparse.ArgumentParser(description='Prepare inputs for Uni-Dock')
    parser.add_argument('--input_pdb', required=True, help='Input receptor PDB file')
    parser.add_argument('--ligand_smiles', help='Ligand SMILES string')
    parser.add_argument('--ntp_type', help='NTP template type (dATP, dTTP, etc.)')
    parser.add_argument('--box_size', type=int, default=25, help='Box edge length in Angstroms')
    parser.add_argument('--box_center', help='Box center as "x,y,z" (auto if not specified)')
    parser.add_argument('--flexible_residues', help='Flexible residues as "A:42,A:89"')
    parser.add_argument('--out_dir', default='.', help='Output directory')
    
    args = parser.parse_args()
    
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    input_pdb = Path(args.input_pdb)
    if not input_pdb.exists():
        print(f"ERROR: Input PDB not found: {input_pdb}", file=sys.stderr)
        sys.exit(1)
    
    # Prepare receptor
    print(f"\n=== Preparing receptor: {input_pdb} ===")
    receptor_pdbqt, flex_pdbqt = prepare_receptor_pdbqt(
        input_pdb, out_dir, args.flexible_residues
    )
    
    # Prepare ligands
    ligand_dir = out_dir / "ligands"
    ligand_dir.mkdir(exist_ok=True)
    
    ligand_smiles_list = []
    
    # Get ligand from NTP template
    if args.ntp_type:
        if args.ntp_type not in NTP_TEMPLATES:
            print(f"ERROR: Unknown NTP type: {args.ntp_type}", file=sys.stderr)
            print(f"Available: {list(NTP_TEMPLATES.keys())}", file=sys.stderr)
            sys.exit(1)
        smiles = NTP_TEMPLATES[args.ntp_type]
        ligand_smiles_list.append((args.ntp_type, smiles))
    
    # Get ligand from SMILES
    if args.ligand_smiles:
        ligand_smiles_list.append(('ligand', args.ligand_smiles))
    
    if not ligand_smiles_list:
        print("ERROR: No ligand specified (use --ligand_smiles or --ntp_type)", file=sys.stderr)
        sys.exit(1)
    
    print(f"\n=== Preparing {len(ligand_smiles_list)} ligand(s) ===")
    ligand_coords = None
    for name, smiles in ligand_smiles_list:
        try:
            pdbqt_path = prepare_ligand_pdbqt(smiles, name, ligand_dir)
            # Get coords for box centering (use first ligand)
            if ligand_coords is None:
                from rdkit import Chem
                mol = Chem.MolFromSmiles(smiles)
                mol = Chem.AddHs(mol)
                from rdkit.Chem import AllChem
                AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
                conf = mol.GetConformer()
                ligand_coords = np.array([conf.GetAtomPosition(i) for i in range(mol.GetNumAtoms())])
        except Exception as e:
            print(f"WARNING: Failed to prepare ligand '{name}': {e}", file=sys.stderr)
    
    # Compute box parameters
    print(f"\n=== Computing docking box ===")
    if args.box_center:
        cx, cy, cz = parse_box_center(args.box_center)
        print(f"Using specified center: ({cx:.2f}, {cy:.2f}, {cz:.2f})")
    else:
        cx, cy, cz = compute_box_center(input_pdb, ligand_coords)
        print(f"Auto-computed center: ({cx:.2f}, {cy:.2f}, {cz:.2f})")
    
    # Convert to Python floats for JSON serialization (numpy float32 not serializable)
    box_params = {
        'cx': float(round(cx, 3)),
        'cy': float(round(cy, 3)),
        'cz': float(round(cz, 3)),
        'sx': int(args.box_size),
        'sy': int(args.box_size),
        'sz': int(args.box_size),
    }
    
    box_file = out_dir / "box_params.json"
    box_file.write_text(json.dumps(box_params, indent=2))
    print(f"Box params: {box_params}")
    print(f"Written to: {box_file}")
    
    print(f"\n=== Preparation complete ===")
    print(f"Receptor: {receptor_pdbqt}")
    if flex_pdbqt:
        print(f"Flexible residues: {flex_pdbqt}")
    print(f"Ligands: {ligand_dir}")
    print(f"Box params: {box_file}")


if __name__ == '__main__':
    main()
