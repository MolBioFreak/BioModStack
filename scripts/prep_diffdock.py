import argparse
import os
import shutil
import pandas as pd
from pathlib import Path

# NTP Templates - Shared with API/LigandMPNN
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
    parser = argparse.ArgumentParser(description="Prepare input CSV for DiffDock")
    parser.add_argument("--input_pdbs", nargs="+", help="List of input PDB files")
    parser.add_argument("--ligand_smiles", type=str, help="Ligand SMILES string")
    parser.add_argument("--ntp_type", type=str, choices=NTP_TEMPLATES.keys(), help="NTP template type")
    parser.add_argument("--output_csv", type=str, required=True, help="Output CSV path")
    parser.add_argument("--stm_pdbs_dir", type=str, required=True, help="Directory to stage PDBs")
    
    args = parser.parse_args()
    
    # Determine ligand SMILES
    smiles = args.ligand_smiles
    if not smiles and args.ntp_type:
        smiles = NTP_TEMPLATES.get(args.ntp_type)
        
    if not smiles:
        raise ValueError("Either --ligand_smiles or --ntp_type must be provided")
        
    # Create staging directory
    os.makedirs(args.stm_pdbs_dir, exist_ok=True)
    
    data = []
    
    # Process each PDB
    # If input_pdbs is a single string (nextflow sometimes does this), split it
    pdb_list = args.input_pdbs
    if len(pdb_list) == 1 and ' ' in pdb_list[0]:
        pdb_list = pdb_list[0].split()
        
    for pdb_path in pdb_list:
        pdb_path = Path(pdb_path)
        if not pdb_path.exists():
            continue
            
        # Copy to staging dir to ensure clean filenames
        staged_path = Path(args.stm_pdbs_dir) / pdb_path.name
        shutil.copy(pdb_path, staged_path)
        
        # DiffDock expects: complex_name, protein_path, ligand_description, protein_sequence
        # But CSV format: complex_name,protein_path,ligand_description,protein_sequence
        data.append({
            'complex_name': pdb_path.stem,
            'protein_path': str(staged_path.resolve()), # Absolute path needed
            'ligand_description': smiles,
            'protein_sequence': '' # Optional, DiffDock can parse from PDB
        })
        
    # Write CSV
    df = pd.DataFrame(data)
    df.to_csv(args.output_csv, index=False)
    print(f"Prepared {len(df)} docking tasks in {args.output_csv}")

if __name__ == "__main__":
    main()
