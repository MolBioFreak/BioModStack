#!/usr/bin/env python3
"""
Generate ssDNA and dsDNA oligonucleotide PDB library using RDKit.

Categories:
- ssDNA 2nt (16 dinucleotides)
- ssDNA 3nt (common trinucleotides)
- ssDNA 4nt (common tetranucleotides)
- dsDNA 2bp, 3bp, 4bp variants
"""

import os
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import AllChem

# Nucleotide SMILES building blocks (nucleoside monophosphates)
# These are phosphorylated nucleosides, 5'->3' direction.
NUCLEOSIDE_SMILES = {
    'A': 'Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](CO)O3',  # dA
    'T': 'Cc1cn([C@H]2C[C@H](O)[C@@H](CO)O2)c(=O)[nH]c1=O',  # dT
    'G': 'Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](CO)O3)c(=O)[nH]1',  # dG
    'C': 'Nc1ccn([C@H]2C[C@H](O)[C@@H](CO)O2)c(=O)n1',  # dC
}

# Full NTP SMILES for reference
NTP_SMILES = {
    'dATP': 'Nc1ncnc2c1ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3',
    'dTTP': 'Cc1cn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)[nH]c1=O',
    'dGTP': 'Nc1nc2c(ncn2[C@H]3C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O3)c(=O)[nH]1',
    'dCTP': 'Nc1ccn([C@H]2C[C@H](O)[C@@H](COP(=O)(O)OP(=O)(O)OP(=O)(O)O)O2)c(=O)n1',
}

def sequence_to_smiles(sequence: str) -> str:
    """Convert DNA sequence to concatenated nucleoside SMILES (dot notation)."""
    sequence = sequence.upper()
    nucleosides = [NUCLEOSIDE_SMILES[base] for base in sequence if base in NUCLEOSIDE_SMILES]
    return '.'.join(nucleosides)

def complement(base: str) -> str:
    """Get Watson-Crick complement."""
    complements = {'A': 'T', 'T': 'A', 'G': 'C', 'C': 'G'}
    return complements.get(base.upper(), 'N')

def reverse_complement(sequence: str) -> str:
    """Get reverse complement of DNA sequence."""
    return ''.join(complement(b) for b in reversed(sequence.upper()))

def generate_3d_pdb(smiles: str, name: str, output_dir: Path) -> bool:
    """Generate 3D PDB from SMILES using RDKit."""
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            print(f"  Failed to parse: {name}")
            return False
        
        mol = Chem.AddHs(mol)
        
        # Try ETKDGv3 first
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        result = AllChem.EmbedMolecule(mol, params)
        
        if result == -1:
            # Fallback to simpler embedding
            result = AllChem.EmbedMolecule(mol, randomSeed=42)
            if result == -1:
                print(f"  Embedding failed: {name}")
                return False
        
        # Energy minimize with MMFF
        try:
            mmff_props = AllChem.MMFFGetMoleculeProperties(mol)
            if mmff_props:
                ff = AllChem.MMFFGetMoleculeForceField(mol, mmff_props)
                if ff:
                    ff.Minimize(maxIts=500)
        except:
            pass
        
        # Save PDB
        pdb_block = Chem.MolToPDBBlock(mol)
        pdb_path = output_dir / f"{name}.pdb"
        with open(pdb_path, 'w') as f:
            f.write(pdb_block)
        
        print(f"  Generated: {name}.pdb ({mol.GetNumAtoms()} atoms)")
        return True
        
    except Exception as e:
        print(f"  Error generating {name}: {e}")
        return False

def main():
    # Output directory
    output_dir = Path(__file__).parent.parent / "platform" / "api" / "inputs" / "ligands"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    generated = 0
    failed = 0
    
    # === ssDNA Dinucleotides (16 combinations) ===
    print("\n=== Generating ssDNA Dinucleotides (16) ===")
    bases = ['A', 'T', 'G', 'C']
    for b1 in bases:
        for b2 in bases:
            seq = f"{b1}{b2}"
            smiles = sequence_to_smiles(seq)
            if generate_3d_pdb(smiles, f"ssDNA_{seq}", output_dir):
                generated += 1
            else:
                failed += 1
    
    # === ssDNA Trinucleotides (common codons) ===
    print("\n=== Generating ssDNA Trinucleotides (common) ===")
    common_trinucs = [
        'ATG',  # Start codon
        'TAA', 'TAG', 'TGA',  # Stop codons
        'AAA', 'AAG',  # Lys
        'GAA', 'GAG',  # Glu
        'GGG', 'GGC',  # Gly
        'TTT', 'TTC',  # Phe
        'CCC', 'CCG',  # Pro
        'ATC', 'ATT',  # Ile
        'GCA', 'GCG',  # Ala
        'CAG', 'CAA',  # Gln
    ]
    for seq in common_trinucs:
        smiles = sequence_to_smiles(seq)
        if generate_3d_pdb(smiles, f"ssDNA_{seq}", output_dir):
            generated += 1
        else:
            failed += 1
    
    # === ssDNA Tetranucleotides (common patterns) ===
    print("\n=== Generating ssDNA Tetranucleotides (common) ===")
    common_tetras = [
        'ATAT', 'TATA',  # AT-rich
        'GCGC', 'CGCG',  # GC-rich
        'AAAA', 'TTTT',  # Poly-A/T
        'GGGG', 'CCCC',  # Poly-G/C
        'ATCG', 'CGAT',  # Mixed
    ]
    for seq in common_tetras:
        smiles = sequence_to_smiles(seq)
        if generate_3d_pdb(smiles, f"ssDNA_{seq}", output_dir):
            generated += 1
        else:
            failed += 1
    
    # === dsDNA (template + primer as separate molecules) ===
    print("\n=== Generating dsDNA Pairs (common) ===")
    dsdna_seqs = [
        'AT', 'TA', 'GC', 'CG',  # 2bp
        'ATG', 'TAC', 'GCA',  # 3bp (codons)
        'ATAT', 'GCGC', 'ATCG',  # 4bp
    ]
    for seq in dsdna_seqs:
        rc = reverse_complement(seq)
        # Combine template + primer strands
        smiles = sequence_to_smiles(seq) + '.' + sequence_to_smiles(rc)
        if generate_3d_pdb(smiles, f"dsDNA_{seq}_{rc}", output_dir):
            generated += 1
        else:
            failed += 1
    
    print(f"\n=== Summary ===")
    print(f"Generated: {generated}")
    print(f"Failed: {failed}")
    print(f"Output: {output_dir}")

if __name__ == '__main__':
    main()
