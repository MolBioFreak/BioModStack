"""Focused candidate writer tests; optional installed-native CPU qualification.

Run directly with Python. Set BMS_PLR_NATIVE_CPU=1 in the installed Protenix
image to exercise its actual atom/token/feature parser without inference.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prepare_protein_local_validator_input.py'
spec = importlib.util.spec_from_file_location('plr_writer', SCRIPT)
assert spec and spec.loader
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


def atom(serial, residue, chain, number, name='CA', element='C', record='ATOM', insertion=' '):
    return (f'{record:<6}{serial:5d} {name:^4} {residue:>3} {chain}{number:4d}{insertion}   '
            f'{float(serial):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00          {element:>2}\n')


def record(name, fields):
    row = list(' ' * 80)
    row[:len(name)] = name
    for start, value in fields:
        row[start:start + len(value)] = value
    return ''.join(row) + '\n'


def mixed():
    return (atom(1, 'HYP', 'Q', 41, insertion='A') +
            atom(2, 'CYS', 'Q', 42, 'SG', 'S') +
            atom(3, 'NAG', 'Q', 70, 'C1', record='HETATM') +
            atom(4, 'DI', 'D', 90, 'P', 'P') +
            atom(5, 'ZN', 'Q', 90, 'ZN', 'ZN', 'HETATM') +
            'CONECT    2    3\nCONECT    3    2\nEND\n')


def cyclic():
    return (atom(1, 'ALA', 'A', 7, 'N', 'N') + atom(2, 'ALA', 'A', 7, 'C') +
            atom(3, 'GLY', 'A', 7, 'N', 'N', insertion='A') +
            atom(4, 'GLY', 'A', 7, 'C', insertion='A') +
            'CONECT    2    3\nCONECT    4    1\nEND\n')


def modified_context():
    return (record('MODRES', [(12, 'PSU'), (16, 'R'), (18, '  80'), (24, '  U')]) +
            atom(1, 'MSE', 'Q', 10, record='HETATM') +
            atom(2, 'CYS', 'Q', 11, 'SG', 'S') + atom(3, 'CYS', 'T', 12, 'SG', 'S') +
            atom(4, 'PSU', 'R', 80, 'P', 'P', 'HETATM') +
            record('SSBOND', [(15, 'Q'), (17, '  11'), (29, 'T'), (31, '  12')]))


class CandidateChemistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='plr-candidate-', dir=os.environ['TMPDIR'])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def contract(self, text):
        source = self.root / 'selected.pdb'
        source.write_text(text)
        return writer.build_contract(source, [29, 17], 'selected-candidate')

    def test_candidate_context_and_cli_authority(self):
        contract = self.contract(mixed())
        (self.root / 'ancestor.pdb').write_text(atom(1, 'ALA', 'Q', 41))
        out, native = self.root / 'contract.json', self.root / 'native.json'
        subprocess.run([sys.executable, str(SCRIPT), '--input-pdb', str(self.root / 'selected.pdb'),
                        '--candidate-id', 'selected-candidate', '--model-seeds', '29,17',
                        '--contract-out', str(out), '--protenix-out', str(native)], check=True)
        self.assertEqual(json.loads(out.read_text()), contract)
        self.assertEqual(json.loads(native.read_text()), contract['protenix_input'])
        task = contract['protenix_input'][0]
        self.assertEqual(task['modelSeeds'], [29, 17])
        self.assertEqual(task['sequences'][0]['proteinChain'], {
            'sequence': 'PC', 'id': ['Q'], 'count': 1,
            'modifications': [{'ptmType': 'CCD_HYP', 'ptmPosition': 1}]})
        self.assertEqual([next(iter(s.values()))['id'] for s in task['sequences']],
                         [['Q'], ['Q_2'], ['D'], ['Q_4']])
        self.assertEqual(task['sequences'][2]['dnaSequence']['sequence'], 'I')
        self.assertEqual(contract['source_residue_map'][0]['source_insertion_code'], 'A')
        self.assertEqual(len(task['covalent_bonds']), 1)
        self.assertEqual(task['covalent_bonds'][0]['entity2'], '2')

    def test_model_connectivity_cannot_alias_selected_serials(self):
        coordinates = atom(1, 'CYS', 'Q', 10, 'SG', 'S') + atom(2, 'NAG', 'L', 20, 'C1', record='HETATM')
        text = ('MODEL        1\n' + coordinates + 'ENDMDL\nMODEL        2\n' + coordinates +
                'CONECT    1    2\nENDMDL\n')
        self.assertNotIn('covalent_bonds', self.contract(text)['protenix_input'][0])
        self.assertEqual(len(self.contract(text + 'CONECT    1    2\n')['protenix_input'][0]['covalent_bonds']), 1)

    def test_reverse_backbone_closure_survives(self):
        task = self.contract(cyclic())['protenix_input'][0]
        self.assertEqual(task['sequences'][0]['proteinChain']['sequence'], 'AG')
        self.assertEqual(task['covalent_bonds'], [dict(entity1='1', copy1=1, position1='2', atom1='C',
                                                      entity2='1', copy2=1, position2='1', atom2='N')])

    def test_modres_link_and_ssbond_insertion_mapping(self):
        text = (record('MODRES', [(12, 'SEP'), (16, 'Q'), (18, '  41'), (22, 'A'), (24, 'SER')]) +
                atom(1, 'SEP', 'Q', 41, insertion='A', record='HETATM') +
                atom(2, 'CYS', 'Q', 42, 'SG', 'S') + atom(3, 'CYS', 'R', 42, 'SG', 'S') +
                atom(4, 'NAG', 'L', 70, 'C1', record='HETATM') +
                record('LINK', [(12, ' OG '), (17, 'SEP'), (21, 'Q'), (22, '  41'), (26, 'A'),
                                (42, ' C1 '), (47, 'NAG'), (51, 'L'), (52, '  70')]) +
                record('SSBOND', [(15, 'Q'), (17, '  42'), (29, 'R'), (31, '  42')]))
        task = self.contract(text)['protenix_input'][0]
        self.assertEqual(task['sequences'][0]['proteinChain']['sequence'], 'SC')
        self.assertEqual(task['sequences'][0]['proteinChain']['modifications'],
                         [{'ptmType': 'CCD_SEP', 'ptmPosition': 1}])
        self.assertEqual([(b['entity1'], b['position1'], b['atom1'], b['entity2'], b['atom2'])
                          for b in task['covalent_bonds']], [('1', '1', 'OG', '3', 'C1'), ('1', '2', 'SG', '2', 'SG')])

    @unittest.skipUnless(os.environ.get('BMS_PLR_NATIVE_CPU') == '1', 'installed native CPU lane only')
    def test_installed_native_atoms_bonds_and_masks(self):
        import numpy as np
        from protenix.data.inference.json_to_feature import SampleDictToFeatures
        results = []
        for label, text in [('mixed', mixed()), ('cyclic', cyclic()), ('modified_context', modified_context())]:
            task = self.contract(text)['protenix_input'][0]
            features, atoms, tokens = SampleDictToFeatures(copy.deepcopy(task)).get_feature_dict()
            axis = [{'chain': str(a.chain_id), 'residue': int(a.res_id), 'name': str(a.res_name),
                     'atom': str(a.atom_name)} for a in atoms]
            edges = {tuple(sorted((int(i), int(j)))) for i, j, _ in atoms.bonds.as_array()}
            for bond in task['covalent_bonds']:
                endpoints = []
                for n in (1, 2):
                    idx = np.flatnonzero((atoms.label_entity_id == bond[f'entity{n}']) &
                        (atoms.copy_id == bond[f'copy{n}']) & (atoms.res_id == int(bond[f'position{n}'])) &
                        (atoms.atom_name == bond[f'atom{n}']))
                    self.assertEqual(len(idx), 1)
                    endpoints.append(int(idx[0]))
                self.assertIn(tuple(sorted(endpoints)), edges)
            self.assertEqual(features['ref_mask'].shape[0], len(atoms))
            self.assertGreater(float(features['ref_mask'].sum()), 0)
            if label == 'mixed':
                self.assertEqual(set(atoms.res_name), {'HYP', 'CYS', 'NAG', 'DI', 'ZN'})
                self.assertTrue(np.all(atoms.is_protein[atoms.chain_id == 'Q']))
                self.assertTrue(np.all(atoms.is_dna[atoms.chain_id == 'D']))
                self.assertTrue(np.all(atoms.is_ligand[atoms.chain_id == 'Q_2']))
                self.assertNotIn('O1', atoms.atom_name[atoms.res_name == 'NAG'])
            elif label == 'cyclic':
                self.assertNotIn('OXT', atoms.atom_name[atoms.res_id == 2])
            else:
                self.assertEqual(task['sequences'][2]['rnaSequence']['modifications'],
                                 [{'modificationType': 'CCD_PSU', 'basePosition': 1}])
                self.assertTrue(np.all(atoms.is_rna[atoms.chain_id == 'R']))
                self.assertIn('PSU', atoms.res_name)
                # Installed native intentionally normalizes MSE after reading
                # the retained CCD_MSE override; do not call this lossless MSE.
                self.assertEqual(task['sequences'][0]['proteinChain']['modifications'][0]['ptmType'], 'CCD_MSE')
                self.assertIn('MET', atoms.res_name)
                self.assertNotIn('MSE', atoms.res_name)
            self.assertEqual(float(features['bond_mask'].sum()), 2.0 if label == 'mixed' else 0.0)
            if label == 'cyclic':
                # Native token bonds omit adjacent standard polymer pairs,
                # even though the corrected closure exists in the atom graph.
                self.assertEqual(float(features['token_bonds'].sum()), 0.0)
            else:
                self.assertGreater(float(features['token_bonds'].sum()), 0.0)
            results.append({'case': label, 'status': 'pass', 'atoms': axis,
                            'token_bonds_sum': float(features['token_bonds'].sum()),
                            'bonds': atoms.bonds.as_array().tolist(), 'tokens': len(tokens),
                            'masks': {k: {'shape': list(v.shape), 'sum': float(v.sum())}
                                      for k, v in features.items() if 'mask' in k and hasattr(v, 'shape')}})
        if os.environ.get('BMS_PLR_EVIDENCE'):
            Path(os.environ['BMS_PLR_EVIDENCE']).write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
