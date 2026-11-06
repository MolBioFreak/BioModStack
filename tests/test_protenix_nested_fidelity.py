"""Regression of actual nested Protenix writers, without model execution."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prep_protenix_batch import main as prepare_batch
from prepare_protein_local_validator_input import build_contract
from run_protenix_inference import prepare_inline_msa_input


def atom(serial, residue, chain, number, *, name='CA', element='C', insertion='', record='ATOM'):
    return (f'{record:<6}{serial:5d} {name:^4} {residue:>3} {chain}{number:4d}{insertion:1s}   '
            f'{float(serial):8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00          {element:>2}\n')


def batch(tmp_path, monkeypatch, epitope, model=None):
    source = tmp_path / 'candidate.pdb'
    source.write_text(atom(1, 'ALA', 'H', 1) + atom(2, 'VAL', 'T', 1) + 'END\n')
    target = tmp_path / 'target.pdb'
    target.write_text('MODEL        1\n' + atom(1, 'GLY', 'X', 42) +
        atom(2, 'CYS', 'X', 42, insertion='A') + atom(3, 'ZN', 'X', 90, name='ZN', element='ZN', record='HETATM') +
        'ENDMDL\nMODEL        2\n' + atom(4, 'ALA', 'X', 42) +
        atom(5, 'ZN', 'X', 91, name='ZN', element='ZN', record='HETATM') + 'ENDMDL\nEND\n')
    argv = ['prep_protenix_batch.py', '--pdb_files', str(source), '--target_pdb', str(target),
            '--target_chains', 'X', '--binder_source_chains', 'H', '--external-target-as-target',
            '--epitope_residues', epitope, '--seeds', '29,17', '--out_json', str(tmp_path / 'native.json')]
    if model is not None:
        argv += ['--target_model_number', str(model)]
    monkeypatch.setattr(sys, 'argv', argv)
    prepare_batch()
    return json.loads((tmp_path / 'native.json').read_text())[0]


@pytest.mark.parametrize('epitope,position', [('X:1', 1), ('X42', 1), ('X42A', 2)])
def test_external_source_epitope_and_default_model(tmp_path, monkeypatch, epitope, position):
    value = batch(tmp_path, monkeypatch, epitope)
    assert value['modelSeeds'] == [29, 17]
    assert value['sequences'][1]['proteinChain'] == {'id': ['T'], 'sequence': 'GC', 'count': 1}
    assert value['sequences'][2]['ion']['count'] == 1
    assert value['constraint']['pocket']['contact_residues'] == [{'entity': 2, 'copy': 1, 'position': position}]


def test_ion_sites_with_distinct_insertion_codes_are_not_collapsed(tmp_path):
    from prep_protenix_batch import extract_target_ion_counts
    source = tmp_path / 'ions.pdb'
    source.write_text(atom(1, 'ZN', 'T', 90, name='ZN', element='ZN', record='HETATM') +
                      atom(2, 'ZN', 'T', 90, name='ZN', element='ZN', insertion='A', record='HETATM'))
    assert extract_target_ion_counts(source) == {'ZN': 2}


def test_explicit_second_model_selects_both_sequence_and_ions(tmp_path, monkeypatch):
    value = batch(tmp_path, monkeypatch, 'X42', 2)
    assert value['sequences'][1]['proteinChain']['sequence'] == 'A'
    assert value['sequences'][2]['ion']['count'] == 1


def test_plr_keeps_candidate_chemistry_ids_and_explicit_bond(tmp_path):
    source = tmp_path / 'candidate.pdb'
    source.write_text(atom(1, 'HYP', 'Q', 41, insertion='A') + atom(2, 'CYS', 'Q', 42, name='SG', element='S') +
        atom(3, 'NAG', 'L', 70, name='C1', record='HETATM') +
        atom(4, 'DI', 'D', 90, name='P', element='P') +
        'CONECT    2    3\nCONECT    3    2\nEND\n')
    contract = build_contract(source, [29, 17])
    native = contract['protenix_input'][0]
    protein = native['sequences'][0]['proteinChain']
    assert protein == {'id': ['Q'], 'count': 1, 'sequence': 'PC',
                       'modifications': [{'ptmType': 'CCD_HYP', 'ptmPosition': 1}]}
    assert native['sequences'][1] == {'ligand': {'id': ['L'], 'count': 1, 'ligand': 'CCD_NAG'}}
    assert native['sequences'][2] == {'dnaSequence': {'id': ['D'], 'count': 1, 'sequence': 'I'}}
    assert native['covalent_bonds'] == [{'entity1': '1', 'copy1': 1, 'position1': '2', 'atom1': 'SG',
                                         'entity2': '2', 'copy2': 1, 'position2': '1', 'atom2': 'C1'}]
    assert contract['source_residue_map'][0]['source_insertion_code'] == 'A'


def test_template_search_stages_exact_alignment_bytes_and_keeps_hits(tmp_path):
    supplied = tmp_path / 'custom-name.a3m'
    supplied.write_text('>query\nAC\n>other\nAC\n')
    hits = tmp_path / 'exact.a3m'
    hits.write_text('>1abc_A/1-2\nAC\n')
    task = [{'name': 'x', 'sequences': [
        {'proteinChain': {'sequence': 'AC', 'count': 1, 'unpairedMsaPath': str(supplied)}},
        {'proteinChain': {'sequence': 'AC', 'count': 1, 'templatesPath': str(hits)}}]}]
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(task))
    result = json.loads(Path(prepare_inline_msa_input(str(source), str(tmp_path / 'out'), use_template=True)).read_text())
    path = Path(result[0]['sequences'][0]['proteinChain']['unpairedMsaPath'])
    assert path.name == 'non_pairing.a3m' and path.read_bytes() == supplied.read_bytes()
    assert result[0]['sequences'][1] == task[0]['sequences'][1]
    assert json.loads(source.read_text()) == task


def test_no_msa_templates_query_does_not_suppress_requested_msa_search(tmp_path):
    task = [{'name': 'x', 'sequences': [{'proteinChain': {'sequence': 'AC', 'count': 1}}]}]
    source = tmp_path / 'input.json'; source.write_text(json.dumps(task))
    assert prepare_inline_msa_input(str(source), str(tmp_path / 'out'), use_template=True, use_msa=True) == str(source)
    derived = prepare_inline_msa_input(str(source), str(tmp_path / 'out'), use_template=True, use_msa=False)
    chain = json.loads(Path(derived).read_text())[0]['sequences'][0]['proteinChain']
    assert Path(chain['unpairedMsaPath']).read_text() == '>query\nAC\n'
    assert 'templatesPath' not in chain  # A query is not a fabricated hit.


def test_antibody_child_projection_uses_complete_protenix_settings(tmp_path):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('Existing offline Nextflow/Groovy jar not selected')
    text = (ROOT / 'workflows/antibody_denovo.nf').read_text()
    start = text.index('                def child_params = groovy.json.JsonOutput.toJson(', text.index('msa_for_spawn ='))
    end = text.index('\n                ])', start) + len('\n                ])')
    params = {'protenix_seeds': '0,17', 'protenix_n_sample': 3, 'protenix_anchor_target': True,
              'protenix_anchor_strict': False, 'protenix_target_geometry_mode': 'conditioned',
              'protenix_pocket_max_distance': 6.2, 'protenix_auto_pocket_residue_count': 9,
              'protenix_use_msa': False, 'protenix_use_template': False, 'epitope_residues': 'X42A'}
    script = tmp_path / 'child.groovy'
    script.write_text('def params = new groovy.json.JsonSlurper().parseText(' + json.dumps(json.dumps(params)) +
                      ')\ndef structure_validator="protenix"\n' + text[start:end] + '\nprintln(child_params)\n')
    result = subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    child = json.loads(result.stdout)
    assert all(child[k] == value for k, value in params.items())


def test_pocket_entity_ordinals_and_explicit_copies_include_nonproteins():
    from protenix_constraint_utils import add_pocket_constraint
    task = {'sequences': [
        {'ion': {'ion': 'ZN', 'count': 1}},
        {'proteinChain': {'sequence': 'AC', 'count': 2, 'id': ['P', 'Q']}},
        {'ligand': {'ligand': 'CCD_NAG', 'count': 1}},
        {'proteinChain': {'sequence': 'GS', 'count': 2, 'id': ['T', 'U']}}]}
    assert add_pocket_constraint(task, binder_chain_ids=['Q'], target_residue_specs=[('U', 2)])
    pocket = task['constraint']['pocket']
    assert pocket['binder_chain'] == {'entity': 2, 'copy': 2}
    assert pocket['contact_residues'] == [{'entity': 4, 'copy': 2, 'position': 2}]


def test_exact_template_uses_selected_bytes_model_and_target_role(tmp_path):
    from Bio.PDB.MMCIF2Dict import MMCIF2Dict
    from extract_target_templates import extract_templates
    from prepare_protenix_exact_templates import attach_manifest_templates
    source = tmp_path / '1abc.pdb'
    source.write_text('MODEL        1\n' + atom(1, 'ALA', 'T', 5) + 'ENDMDL\nMODEL        2\n' +
                      atom(2, 'GLY', 'T', 42, insertion='A') + 'ENDMDL\nEND\n')
    source.with_suffix('.cif').write_text('not the selected source; must never replace its coordinates')
    ordinary = extract_templates([source], ['T'], tmp_path / 'ordinary', model_number=2)
    assert Path(ordinary['1abc']['cif']).name == '1abc.cif'  # Existing non-Protenix consumers.
    manifest = extract_templates([source], ['T'], tmp_path / 'native', model_number=2, native_template_ids=True)
    row = manifest['1abc']
    cif = MMCIF2Dict(row['cif'])
    assert cif['_atom_site.label_comp_id'] == ['GLY']
    assert cif['_atom_site.auth_seq_id'] == ['42']
    assert cif['_atom_site.pdbx_PDB_ins_code'] == ['A']
    assert cif['_atom_site.pdbx_PDB_model_num'] == ['1']
    assert cif['_chem_comp.type'] == ['L-peptide linking']
    native = [{'name': '1abc', 'sequences': [
        {'proteinChain': {'id': ['B'], 'sequence': 'G', 'count': 1}},
        {'proteinChain': {'id': ['Q'], 'sequence': 'G', 'count': 1}}]}]
    assert attach_manifest_templates(native, manifest, tmp_path / 'hits', ['Q']) == 1
    assert Path(native[0]['sequences'][0]['proteinChain']['templatesPath']).read_text() == ''
    assert Path(native[0]['sequences'][1]['proteinChain']['templatesPath']).read_text().startswith('>' + row['template_id'] + '_T/')


@pytest.mark.parametrize('workflow', ['antibody_child', 'antibody_denovo'])
@pytest.mark.parametrize('selector', ['protenix', 'protenix_v2'])
def test_actual_workflow_validator_alias_selects_protenix(tmp_path, workflow, selector):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('Existing offline Nextflow/Groovy jar not selected')
    text = (ROOT / 'workflows' / f'{workflow}.nf').read_text()
    start = text.index('def structure_validator = ')
    end = text.index("structure_validator = 'boltz2'", start)
    end = text.index('}', end) + 1
    script = tmp_path / 'selector.groovy'
    script.write_text(f'def params = [structure_validator: "{selector}"]\n' +
                      'def log = [warn: { throw new AssertionError(it) }]\n' +
                      text[start:end] + '\nassert structure_validator == "protenix"\n')
    result = subprocess.run(['java', '-cp', jar, 'groovy.ui.GroovyMain', str(script)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_numeric_author_epitope_and_legacy_spacing():
    from prep_protenix_batch import resolve_epitope_residues
    mapping = {('1', 42, 'A'): 2, ('x', -3, ''): 1}
    assert resolve_epitope_residues('142A,x-3,1 : 2', mapping, {'1': 'T'}) == [('T', 2), ('x', 1), ('T', 2)]


def test_auto_pocket_uses_writer_residue_roster_and_author_model(tmp_path, monkeypatch):
    source = tmp_path / 'candidate.pdb'
    source.write_text(atom(1, 'ALA', 'H', 1) + 'END\n')
    target = tmp_path / 'target.pdb'
    target.write_text('MODEL        2\n' + atom(1, 'VAL', 'X', 5) + 'ENDMDL\nMODEL        1\n' +
                      atom(2, 'GLY', 'X', 42) + atom(3, 'UNK', 'X', 43) +
                      atom(4, 'CYS', 'X', 42, insertion='A') + 'ENDMDL\nEND\n')
    output = tmp_path / 'native.json'
    monkeypatch.setattr(sys, 'argv', ['prep_protenix_batch.py', '--pdb_files', str(source),
        '--target_pdb', str(target), '--target_chains', 'X', '--binder_source_chains', 'H',
        '--external-target-as-target', '--auto_pocket_if_missing', '--out_json', str(output)])
    prepare_batch()
    value = json.loads(output.read_text())[0]
    assert value['sequences'][1]['proteinChain']['sequence'] == 'GC'
    assert sorted(r['position'] for r in value['constraint']['pocket']['contact_residues']) == [1, 2]
