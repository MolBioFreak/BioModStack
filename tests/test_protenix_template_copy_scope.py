"""Focused transport checks; installed CPU feature proof lives in audit evidence."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/prepare_protenix_exact_templates.py'
spec = importlib.util.spec_from_file_location('exact_copy_scope', SCRIPT)
assert spec is not None and spec.loader is not None
exact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exact)


def test_copy_split_rebases_alias_bonds_and_restraints(tmp_path):
    task = {'name': 'sample', 'sequences': [
        {'ion': {'ion': 'ZN', 'count': 1, 'id': ['Z']}},
        {'proteinChain': {'sequence': 'APC', 'count': 2, 'id': ['B', 'T'],
                          'modifications': [{'ptmPosition': 2, 'ptmType': 'CCD_HYP'}],
                          'templatesPath': 'caller.a3m', 'unpairedMsa': ''}},
        {'ligand': {'ligand': 'CCD_NAG', 'count': 1, 'id': ['L']}}],
        'covalent_bonds': [{'left_entity': 2, 'left_copy': 2, 'left_position': 3, 'left_atom': 'SG',
                           'right_entity': 3, 'right_copy': 1, 'right_position': 1, 'right_atom': 'C1'}],
        'constraint': {'pocket': {'binder_chain': {'entity': 2, 'copy': 1},
                                  'contact_residues': [{'entity': 2, 'copy': 2, 'position': 2}],
                                  'max_distance': 8},
                       'contact': [{'entity1': 2, 'copy1': 2, 'position1': 1,
                                    'entity2': 3, 'copy2': 1, 'position2': 1, 'max_distance': 8}]}}
    manifest = {'sample': {'template_id': 'abcd', 'sequences': {'A': 'APC'}}}
    assert exact.attach_manifest_templates([task], manifest, tmp_path, ['T']) == 1
    binder = task['sequences'][1]['proteinChain']
    target = task['sequences'][2]['proteinChain']
    assert binder['templatesPath'] == 'caller.a3m'
    assert binder['modifications'] == target['modifications']
    assert target['unpairedMsa'] == ''
    assert task['covalent_bonds'][0]['left_entity'] == 3
    assert task['covalent_bonds'][0]['left_copy'] == 1
    assert task['covalent_bonds'][0]['right_entity'] == 4
    assert task['constraint']['pocket']['contact_residues'][0] == {'entity': 3, 'copy': 1, 'position': 2}
    assert task['constraint']['contact'][0]['entity2'] == 4


def test_omitted_bond_copies_are_zipped(tmp_path):
    task = {'name': 'sample', 'sequences': [
        {'proteinChain': {'sequence': 'AC', 'count': 2, 'id': ['B', 'T']}},
        {'ligand': {'ligand': 'CCD_NAG', 'count': 2, 'id': ['L', 'M']}}],
        'covalent_bonds': [{'entity1': '1', 'position1': 2, 'atom1': 'SG',
                           'entity2': '2', 'position2': 1, 'atom2': 'C1'}]}
    manifest = {'sample': {'template_id': 'abcd', 'sequences': {'A': 'AC'}}}
    assert exact.attach_manifest_templates([task], manifest, tmp_path, ['T'], generic_templates=True) == 1
    assert 'templatesPath' not in task['sequences'][0]['proteinChain']
    assert [(b['entity1'], b['copy1'], b['entity2'], b['copy2']) for b in task['covalent_bonds']] == [('1', 1, '3', 1), ('2', 1, '3', 2)]


def test_equal_sequences_do_not_override_source_chain_identity(tmp_path):
    task = {'name': 'sample', 'sequences': [{'proteinChain': {'sequence': 'AC', 'count': 1, 'id': ['T']}}]}
    manifest = {'sample': {'template_id': 'abcd', 'sequences': {'T': 'AC', 'U': 'AC'}}}
    assert exact.attach_manifest_templates([task], manifest, tmp_path, ['T']) == 1
    text = Path(task['sequences'][0]['proteinChain']['templatesPath']).read_text()
    assert '>abcd_T/' in text and '>abcd_U/' not in text
