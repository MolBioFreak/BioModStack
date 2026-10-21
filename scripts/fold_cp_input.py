"""Fold-CP input chemistry adapter; native dedfdba accepts SMILES/CCD only.

Convert an explicitly supplied single-molecule MOL/SDF/MOL2 file to isomeric
SMILES, not a template or coordinate constraint. No conformers are generated
here. Native parsing/CCD/preparation remains the scientific authority.
"""
from pathlib import Path


def normalize_ligand_files(document, owner):
    from rdkit import Chem

    for item in document.get('sequences', []):
        ligand = item.get('ligand')
        if not isinstance(ligand, dict) or not ligand.get('path'):
            continue
        if ligand.get('smiles') or ligand.get('ccd'):
            continue  # explicit native chemistry takes precedence, as before
        path = Path(ligand['path'])
        if not path.is_absolute():
            path = Path(owner) / path
        suffix = path.suffix.lower()
        if suffix == '.sdf':
            molecules = list(Chem.SDMolSupplier(str(path), removeHs=False))
            if len(molecules) != 1:
                raise ValueError('A Fold-CP ligand component represents one molecule; use one SDF record per component')
            molecule = molecules[0]
        elif suffix == '.mol2':
            molecule = Chem.MolFromMol2File(str(path), removeHs=False)
        else:
            molecule = Chem.MolFromMolFile(str(path), removeHs=False)
        if molecule is None:
            raise ValueError(f'Could not parse Fold-CP ligand chemistry: {path}')
        ligand['smiles'] = Chem.MolToSmiles(molecule, isomericSmiles=True)
        del ligand['path']
    return document
