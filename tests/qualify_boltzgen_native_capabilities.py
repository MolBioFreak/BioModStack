"""Run inside the pinned SIF on CPU, with only this repo and scratch mounted.
No checkpoints, inference, GPU, downloader, or native pipeline execution.
Uses explicitly synthetic RDKit alanine/glycine chemical fixtures for parsing.
"""
import json
import sys
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import AllChem
from boltzgen.data.parse.schema import YamlDesignParser
from boltzgen.cli.boltzgen import build_parser, protocol_configs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.prep_boltzgen import preparation_parser, build_design_config

root = Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
mols = {}
for residue, sequence in [('ALA', 'A'), ('GLY', 'G')]:
    mol = Chem.MolFromSequence(sequence)
    for atom in mol.GetAtoms():
        atom.SetProp('name', atom.GetPDBResidueInfo().GetName().strip())
    mol = Chem.AddHs(mol, addResidueInfo=True)
    AllChem.EmbedMolecule(mol, randomSeed=7)
    for atom in mol.GetAtoms():
        if not atom.HasProp('name'): atom.SetProp('name', atom.GetPDBResidueInfo().GetName().strip())
    mols[residue] = mol
Chem.MolToPDBFile(Chem.RemoveHs(mols['ALA']), str(root / 'backbone.pdb'))
# Synthetic ethanol pose with an explicit CCD-like component dictionary. This
# exercises native small-molecule file import, not affinity or activity quality.
ligand = Chem.AddHs(Chem.MolFromSmiles('CCO'))
AllChem.EmbedMolecule(ligand, randomSeed=7)
for i, atom in enumerate(ligand.GetAtoms(), 1):
    name = atom.GetSymbol() + str(i)
    atom.SetProp('name', name)
    info = Chem.AtomPDBResidueInfo()
    info.SetName(name.rjust(4)); info.SetResidueName('EOH')
    info.SetChainId('L'); info.SetResidueNumber(1); info.SetIsHeteroAtom(True)
    atom.SetMonomerInfo(info)
mols['EOH'] = ligand
ligand_text = Chem.MolToPDBBlock(Chem.RemoveHs(ligand))
backbone_text = (root / 'backbone.pdb').read_text()
(root / 'ligand.pdb').write_text('\n'.join(line for line in (backbone_text + ligand_text).splitlines()
    if line.startswith(('ATOM', 'HETATM'))) + '\nEND\n')
parser = YamlDesignParser(root)
protocols = {'protein_binder': 'protein-anything', 'peptide_binder': 'peptide-anything',
             'nanobody_binder': 'nanobody-anything', 'antibody_binder': 'antibody-anything',
             **{m: 'protein-small_molecule' for m in ['ligand_binder','ntp_binder','scaffold_around_ligand','backbone_docking']}}
for mode, protocol in protocols.items():
    argv = ['--generation_mode', mode, '--protocol', protocol, '--scaffold_length', '4', '--output_yaml', str(root / (mode+'.yaml'))]
    if mode == 'ligand_binder': argv += ['--ligand_smiles', 'CCO']
    if mode == 'ntp_binder': argv += ['--ntp_type', 'ATP', '--catalytic_site']
    if mode == 'scaffold_around_ligand': argv += ['--ligand_pdb', str(root/'ligand.pdb'), '--ligand_chains', 'L']
    if mode == 'backbone_docking': argv += ['--input_pdb', str(root/'backbone.pdb'), '--ntp_type', 'ATP']
    if mode == 'antibody_binder': argv += ['--scaffold_path', str(root/'backbone.pdb'), '--scaffold_chain', 'A', '--scaffold_design_ranges', '1']
    if mode == 'peptide_binder': argv += ['--cyclic']
    spec = build_design_config(preparation_parser().parse_args(argv))
    target = parser.parse_boltzgen_schema(mode, spec, mols, root, base_file_path=root)
    native_argv = ['run', str(root/(mode+'.yaml')), '--output', str(root/'not-run'), '--protocol', protocol, '--num_designs', '1']
    native = build_parser().parse_args(native_argv)
    assert native.protocol == protocol and protocol in protocol_configs
    print(json.dumps({'mode': mode, 'protocol': protocol, 'native_argv': native_argv,
                      'entities': spec['entities'], 'residues': len(target.structure.residues),
                      'designed': int(target.design_info.res_design_mask.sum()),
                      'structure_groups': target.design_info.res_structure_groups.tolist()}), flush=True)
print('PASS: eight native schemas and native CLI argument parses; no inference')
