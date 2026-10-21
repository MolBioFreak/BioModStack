"""Author mask producer -> installed native CPU preparation (inert model)."""
import json
from pathlib import Path
import subprocess
import sys

from test_proteinmpnn_role_transport import install_native_science_stubs, binding, atom


def backbone(path):
    text = ''
    for chain, number, insertion in [('H',10,''), ('H',10,'B'), ('H',11,''), ('L',5,''), ('T',10,'')]:
        for name in ['N','CA','C','O']:
            line = atom(len(text.splitlines())+1, chain, number, insertion=insertion)
            text += line[:12] + f'{name:^4}' + line[16:]
    path.write_text(text)


def test_legacy_mask_to_native_multichain_locks(tmp_path):
    from prep_mpnn_designs import map_inpaint_to_residues, modify_pdb_file
    from add_fixed_labels import add_fixed_labels_to_pdb
    source = tmp_path/'source.pdb'; backbone(source)
    selected = map_inpaint_to_residues(source, [False,True,False,False,False])
    assert selected == [('H',10,'B')]
    intermediate = tmp_path/'masked.pdb'
    modify_pdb_file(source, intermediate, selected)
    assert 'REMARK PDBinfo-LABEL: 2 FIXED' in intermediate.read_text()
    prepared = tmp_path/'input'; prepared.mkdir()
    add_fixed_labels_to_pdb(intermediate, prepared/'source.pdb', ['T'])
    roles = json.loads((prepared/'source.pdb').read_text().splitlines()[0][len(binding.ROLE_REMARK):])
    assert roles['fixed_positions'] == [['H',10,'B']]
    assert roles['designed_chains'] == ['H','L']
    assert roles['fixed_chains'] == ['T']
    env = install_native_science_stubs(tmp_path)
    out = tmp_path/'output'; out.mkdir()
    command = [str(tmp_path/'bin/python'), '/scripts/proteinmpnn_native_binding.py', '--native',
               '/dl_binder_design/mpnn_fr/dl_interface_design_multi.py', '--',
               '-pdbdir', str(prepared), '-outpdbdir', str(out), '-seqs_per_struct', '2', '-debug']
    result = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout+result.stderr
    samples = [json.loads(line) for line in (tmp_path/'samples.jsonl').read_text().splitlines()]
    assert samples[0]['fixed_mask'] == [[1,0,1,1,1]]
    assert samples[0]['chain_M'] == [[1,1,1,1,0]]
    for path in out.glob('*.json'):
        record = json.loads(path.read_text())
        assert record['chain_sequences']['H'][1] == 'A'
        assert record['chain_sequences']['T'] == 'A'
        assert record['score_contract']['conditioning_sequence'] == 'sampled_sequence'


def test_binder_aliases_share_author_identity_contract(tmp_path):
    source = tmp_path/'source.pdb'; backbone(source)
    alias = binding.role_contract(source, dict(binder_chains=['H','L'], target_chains=['T'], fixed_positions='H:10B'))
    direct = binding.role_contract(source, dict(design_chain='H,L', target_chain='T', fixed_positions='H:10B'))
    assert alias == direct


def test_binder_replay_preserves_native_pose_labels(tmp_path):
    source = tmp_path/'source.pdb'; backbone(source)
    source.write_text('REMARK PDBinfo-LABEL: 2 FIXED\n' + source.read_text())
    roles = binding.role_contract(source, dict(binder_chains=['H','L'], target_chains=['T']))
    assert roles['fixed_positions'] == [['H',10,'B']]


def test_local_redesign_does_not_expand_blank_insertion_selection(tmp_path):
    from add_fixed_labels_from_spec import add_labels
    source = tmp_path/'source.pdb'; backbone(source)
    out = tmp_path/'prepared.pdb'
    add_labels(source, out, {('H',10,'')})
    roles = json.loads(out.read_text().splitlines()[0][len(binding.ROLE_REMARK):])
    assert ['H',10,''] not in roles['fixed_positions']
    assert ['H',10,'B'] in roles['fixed_positions']
    assert [line for line in out.read_text().splitlines() if line.startswith('ATOM')] == source.read_text().splitlines()


def test_retained_protein_hunter_fallback_preserves_insertions(tmp_path):
    from run_protein_hunter_inference import _structure_sequence
    source = tmp_path/'source.pdb'; backbone(source)
    assert _structure_sequence(source, 'H') == 'AAA'
