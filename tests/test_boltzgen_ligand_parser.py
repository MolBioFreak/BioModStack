"""Installed-native ligand parsing and unchanged polymer controls, CPU only.

Set BMS_TEST_BOLTZGEN_IMAGE to the repaired candidate and
BMS_TEST_BOLTZGEN_BASE_IMAGE to its unmodified base. No model/checkpoint calls.
All coordinates and component dictionaries below are explicit synthetic fixtures.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def native_probe(work, baseline):
    import dataclasses
    import random
    import gemmi
    import numpy as np
    import torch
    from rdkit import Chem
    from rdkit.Chem import AllChem
    from boltzgen.data.parse.pdb_parser import parse_pdb
    from boltzgen.data.parse.mmcif import parse_mmcif
    from boltzgen.data.parse.schema import YamlDesignParser
    from boltzgen.data.tokenize.tokenizer import Tokenizer
    from boltzgen.data.data import Input
    from boltzgen.data.feature.featurizer import Featurizer
    from boltzgen.task.predict.data_from_yaml import collate
    from boltzgen.task.predict.writer import DesignWriter
    from boltzgen.data.write.mmcif import to_mmcif

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    random.seed(7)
    np.random.seed(7)
    torch.manual_seed(7)
    mols = {}
    gly = Chem.AddHs(Chem.MolFromSequence('G'), addResidueInfo=True)
    AllChem.EmbedMolecule(gly, randomSeed=7)
    for atom in gly.GetAtoms():
        atom.SetProp('name', atom.GetPDBResidueInfo().GetName().strip())
    gly.GetConformer().SetProp('name', 'Ideal')
    mols['GLY'] = gly
    ligand = Chem.AddHs(Chem.MolFromSmiles('CCO'))
    AllChem.EmbedMolecule(ligand, randomSeed=7)
    ligand.GetConformer().SetProp('name', 'Ideal')
    for i, atom in enumerate(ligand.GetAtoms(), 1):
        name = atom.GetSymbol() + str(i)
        atom.SetProp('name', name)
        info = Chem.AtomPDBResidueInfo()
        info.SetName(name.rjust(4))
        info.SetResidueName('EOH')
        info.SetChainId('L')
        info.SetResidueNumber(7)
        info.SetIsHeteroAtom(True)
        atom.SetMonomerInfo(info)
    mols['EOH'] = ligand
    ligand_path = work / 'ligand.pdb'
    Chem.MolToPDBFile(Chem.RemoveHs(ligand), str(ligand_path))
    polymer_path = work / 'polymer.pdb'
    Chem.MolToPDBFile(Chem.RemoveHs(gly), str(polymer_path))
    mixed_path = work / 'mixed.pdb'
    mixed_path.write_text('\n'.join(line for line in
        (polymer_path.read_text() + ligand_path.read_text()).splitlines()
        if line.startswith(('ATOM', 'HETATM'))) + '\nEND\n')
    ligand_st = gemmi.read_structure(str(ligand_path))
    ligand_st.setup_entities()
    ligand_cif = work / 'ligand.cif'
    # Serializing an empty loop omits it. This covers both native PDB's empty
    # polymer loop and ordinary ligand-only mmCIF's absent category.
    ligand_st.make_mmcif_document().write_file(str(ligand_cif))
    expected_coords = {a.name: np.array([a.pos.x, a.pos.y, a.pos.z])
                       for chain in ligand_st[0] for res in chain for a in res}
    report = {'baseline': baseline, 'failures': {}, 'controls': {}, 'ligands': {}}

    def fingerprint(structure):
        return {field.name: hashlib.sha256(getattr(structure, field.name).tobytes()).hexdigest()
                for field in dataclasses.fields(structure)}

    for name, parse, path in [('pdb', parse_pdb, ligand_path), ('cif', parse_mmcif, ligand_cif)]:
        try:
            structure = parse(path, mols=mols, moldir=work, use_assembly=False).data
        except (IndexError, RuntimeError) as exc:
            if not baseline:
                raise
            report['failures'][name] = {'class': type(exc).__name__, 'message': str(exc)}
            print('PRESERVED_BASELINE_FAILURE', name, type(exc).__name__, repr(str(exc)))
            continue
        assert not baseline, 'baseline unexpectedly accepts ligand-only input'
        assert len(structure.chains) == 1 and len(structure.residues) == 1
        assert structure.residues['name'].tolist() == ['EOH']
        present = structure.atoms[structure.atoms['is_present']]
        assert set(present['name']) == set(expected_coords)
        for atom in present:
            np.testing.assert_allclose(atom['coords'], expected_coords[atom['name']], atol=1e-6)
        assert len(structure.bonds) == 2
        report['ligands'][name] = {'atoms': present['name'].tolist(), 'bonds': len(structure.bonds),
                                  'coordinates': present['coords'].tolist()}

    parser = YamlDesignParser(work)
    for label, path, selection in [('polymer', polymer_path, 'A'), ('mixed', mixed_path, 'L')]:
        parsed = parse_pdb(path, mols=mols, moldir=work, use_assembly=False).data
        spec = {'entities': [{'protein': {'id': 'B', 'sequence': '4'}},
                            {'file': {'path': str(path), 'include': [{'chain': {'id': selection}}]}}]}
        target = parser.parse_boltzgen_schema(label, spec, mols, work, base_file_path=work)
        report['controls'][label] = {'parsed': fingerprint(parsed),
                                    'schema': fingerprint(target.structure),
                                    'design_mask': target.design_info.res_design_mask.tolist(),
                                    'cif': hashlib.sha256(to_mmcif(target.structure).encode()).hexdigest()}

    if not baseline:
        # Ordinary preparation -> installed schema -> features -> writer. The
        # prediction is deliberately the input coordinates, never model output.
        sys.path.insert(0, str(ROOT))
        from scripts.prep_boltzgen import preparation_parser, build_design_config
        args = preparation_parser().parse_args(['--generation_mode', 'scaffold_around_ligand',
            '--scaffold_length', '4', '--ligand_pdb', str(ligand_path), '--ligand_chains', 'L',
            '--output_yaml', str(work / 'unused.yaml')])
        spec = build_design_config(args)
        target = parser.parse_boltzgen_schema('ligand-only', spec, mols, work, base_file_path=work)
        structure = target.structure
        assert len(structure.residues) == 5
        assert int(target.design_info.res_design_mask.sum()) == 4
        assert structure.residues['name'][-1] == 'EOH'
        assert not target.design_info.res_design_mask[-1]
        assert target.design_info.res_structure_groups[-1] != 0
        tokenized = Tokenizer().tokenize(structure)
        tokenized.tokens['design_mask'] = target.design_info.res_design_mask[tokenized.token_to_res]
        data = Input(tokens=tokenized.tokens, bonds=tokenized.bonds,
                     token_to_res=tokenized.token_to_res, structure=structure, msa={}, templates=None)
        feat = Featurizer().process(data, random=np.random.default_rng(17), molecules=mols,
            training=False, max_seqs=1, design=True, atom14=False, compute_frames=False, disulfide_on=False)
        feat.update(id='ligand-only', extra_mols={}, chain_design_mask=feat['design_mask'].bool())
        batch = collate([feat])
        prediction = dict(batch, coords=batch['coords'][0], exception=False)
        writer = DesignWriter(work / 'writer', res_atoms_only=False, atom14=False, design=True, write_native=True)
        writer.write_on_batch_end(prediction=prediction, batch=batch, sample_id='fixture')
        assert writer.failed == 0
        output = writer.outdir / 'fixture_0.cif'
        assert output.exists()
        reloaded = parse_mmcif(output, mols=mols, moldir=work, use_assembly=False).data
        assert 'EOH' in reloaded.residues['name']
        report['pipeline'] = {'residues': len(structure.residues), 'designed': 4,
                              'feature_tokens': len(tokenized.tokens), 'writer': output.name}
    else:
        assert set(report['failures']) == {'pdb', 'cif'}
    (work / 'result.json').write_text(json.dumps(report, indent=2))
    print('NATIVE_LIGAND_PROBE_PASS', json.dumps(report))


def test_native_ligand_only_and_polymer_parity(tmp_path):
    import pytest
    candidate = os.environ.get('BMS_TEST_BOLTZGEN_IMAGE')
    base = os.environ.get('BMS_TEST_BOLTZGEN_BASE_IMAGE')
    if not candidate or not base:
        pytest.skip('set candidate and original BoltzGen images for native CPU qualification')
    for label, image in [('base', base), ('candidate', candidate)]:
        command = ['apptainer', 'exec', '--cleanenv', '--no-home', '--bind', f'{ROOT}:/work:ro',
            '--bind', f'{tmp_path}:/probe', '--env', 'TMPDIR=/probe',
            '--env', 'MPLCONFIGDIR=/probe/matplotlib', '--env', 'NUMBA_CACHE_DIR=/probe/numba',
            '--env', 'OMP_NUM_THREADS=1', image, '/opt/venv/bin/python',
            '/work/tests/test_boltzgen_ligand_parser.py', f'/probe/{label}', label]
        result = subprocess.run(command, text=True, capture_output=True, timeout=180)
        (tmp_path / (label + '.log')).write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
    original = json.loads((tmp_path / 'base/result.json').read_text())
    corrected = json.loads((tmp_path / 'candidate/result.json').read_text())
    assert original['controls'] == corrected['controls']
    assert corrected['ligands']['pdb'] == corrected['ligands']['cif']


def test_parser_repair_preserves_both_native_source_identities(tmp_path):
    import zipfile
    sys.path.insert(0, str(ROOT / 'scripts'))
    from lib import boltzgen_native as native
    with zipfile.ZipFile(ROOT / 'tests/fixtures/boltzgen_617e549/installed_source.zip') as archive:
        archive.extractall(tmp_path)
    cli = tmp_path / 'opt/venv/bin/boltzgen'
    original = native.observe_source(cli, root=tmp_path)
    assert native.supported(original)
    path = tmp_path / native.LIGAND_PARSER_SOURCE
    subprocess.run(['patch', '--batch', '--forward', str(path),
                    str(ROOT / 'apptainer/patches/boltzgen/ligand-only-mmcif.patch')], check=True)
    corrected = native.observe_source(cli, root=tmp_path)
    assert native.supported(corrected) and native.supported(original)
    assert corrected['files'][native.LIGAND_PARSER_SOURCE] == native.LIGAND_PARSER_SHA256
    assert original['source_sha256'] != corrected['source_sha256']
    path.write_bytes(path.read_bytes() + b'\n# unknown edit\n')
    assert native.observe_source(cli, root=tmp_path)['state'] == 'unavailable'


if __name__ == '__main__':
    native_probe(sys.argv[1], sys.argv[2] == 'base')
