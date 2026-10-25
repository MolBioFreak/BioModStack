"""Sparse discovery and portable evaluation handoff (no scientific execution)."""
import copy
import json
from pathlib import Path

import pytest
from services import rfd4_proteina, complexa


@pytest.mark.parametrize('mode', ['generate', 'evaluate'])
def test_rfd4_omitted_settings_are_not_materialized(mode):
    input_ = ({'kind': 'unconditional', 'name': 'minimal', 'chains': [
        {'chain_id': 'A', 'min_length': 40, 'max_length': 40}]} if mode == 'generate'
        else {'kind': 'annotated', 'slots': ['exact']})
    params = {'rfd4_request': {'mode': mode, 'input': input_}}
    original = copy.deepcopy(params)
    assert rfd4_proteina.selected_oracle_assets(mode, params) == ()
    compiled = rfd4_proteina.compile_request(mode, params)
    assert 'settings' not in compiled['request']
    assert params == original


def test_rfd4_profile_readback_is_detached_and_not_request_defaults():
    from scripts.lib.rfd4_proteina_contract import _PROFILE
    result = rfd4_proteina.generation_settings()['native_profile_defaults']
    assert result['default_generation'] == _PROFILE['default_effective_generation']
    for group, profiles in result['generation_by_group'].items():
        for name, values in profiles.items():
            assert values == _PROFILE['groups'][group][name]['effective_generation']
    result['default_generation']['n_recycle'] = -999
    assert _PROFILE['default_effective_generation']['n_recycle'] != -999
    assert rfd4_proteina.generation_settings('evaluate')['native_profile_defaults'] is None


@pytest.mark.parametrize('operation', ['evaluate', 'analysis'])
def test_complexa_exact_evaluation_basket_is_separate_from_target(tmp_path, operation):
    target = tmp_path / 'target.pdb'
    candidate = tmp_path / 'candidate.pdb'
    target.write_bytes(b'REMARK exact target\nEND\n')
    candidate.write_bytes(b'REMARK exact candidate\nEND\n')
    request = {'task': 'protein_binder', 'task_name': 'selected', 'operation': operation,
               'source': {'document_id': 'target'}, 'evaluation_slots': ['candidate', 'target']}
    params = {'complexa_request': request}
    assert complexa.source_slots_for_params('evaluate', params) == ['target', 'candidate']
    prepared = complexa.prepare_for_job('evaluate', params, tmp_path / 'job',
        resolved_sources={'target': target, 'candidate': candidate}, allowed_roots=[tmp_path],
        source_identities={'target': {'document_id': 'target'}, 'candidate': {'document_id': 'candidate'}})
    root = Path(prepared['complexa_request_dir'])
    assert (root / 'source.pdb').read_bytes() == target.read_bytes()
    assert (root / 'evaluation/0000.pdb').read_bytes() == candidate.read_bytes()
    assert (root / 'evaluation/0001.pdb').read_bytes() == target.read_bytes()
    assert json.loads((root / 'request.json').read_text()) == request
    bindings = json.loads((root / 'source-bindings.json').read_text())
    assert bindings['target']['paths'] == ['source.pdb', 'evaluation/0001.pdb']
    assert bindings['candidate']['identity']['document_id'] == 'candidate'
    # Cold preparation of a different basket must not retain old candidates.
    changed = {**request, 'source': None, 'evaluation_slots': ['candidate']}
    complexa.prepare_for_job('evaluate', {'complexa_request': changed}, tmp_path / 'job',
        resolved_sources={'candidate': candidate}, allowed_roots=[tmp_path], source_identities={})
    assert not (root / 'source.pdb').exists()
    assert not (root / 'source-path.txt').exists()
    assert sorted(p.name for p in (root / 'evaluation').iterdir()) == ['0000.pdb']


def test_complexa_does_not_rename_cif_bytes_to_pdb(tmp_path):
    source = tmp_path / 'selected.cif.zst'
    source.write_bytes(b'inert compressed-format custody fixture')
    request = {'task': 'ligand_binder', 'task_name': 'selected', 'source': {'document_id': 'exact'}}
    prepared = complexa.prepare_for_job('generate', {'complexa_request': request}, tmp_path / 'job',
        resolved_sources={'exact': source}, allowed_roots=[tmp_path], source_identities={})
    root = Path(prepared['complexa_request_dir'])
    assert not (root / 'source.pdb').exists()
    assert (root / 'source-path.txt').read_text().strip() == 'source.cif.zst'
    assert (root / 'source.cif.zst').read_bytes() == source.read_bytes()


def test_complexa_inactive_evaluation_sources_do_not_expand_closure():
    params = {'complexa_request': {'task': 'AME', 'task_name': 'named',
              'evaluation_slots': ['inactive']}}
    assert complexa.source_slots_for_params('generate', params) == []
