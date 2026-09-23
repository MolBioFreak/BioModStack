"""Native cache and Fold-CP admission boundaries without provider I/O."""
import json
from pathlib import Path

import pytest
import yaml

from services import model_msa_handoff as handoff
from services import msa_provider_setup as setup


def test_fold_cp_config_admission_before_disabled_or_enabled_msa(tmp_path, monkeypatch):
    source = tmp_path / 'configs'
    source.mkdir()
    (source / 'one.yaml').write_text(yaml.safe_dump({'sequences': [{'protein': {'id': 'A', 'sequence': 'ACDE'}}]}))
    (source / 'two.yaml').write_text(yaml.safe_dump({'sequences': [{'protein': {'id': 'B', 'sequence': 'FGHI'}}]}))
    monkeypatch.setattr('services.msa_preparation.prepare_model_msa', lambda **kw: pytest.fail('provider must not be called'))
    for enabled in (False, True):
        with pytest.raises(ValueError, match='exactly one top-level'):
            handoff.prepare_launch_msa('boltz_cp_experimental', {
                'boltz_use_msa': enabled, 'bcp_input_path': str(source),
            }, tmp_path / 'output')


def test_native_pairing_replay_not_flattened(tmp_path, monkeypatch):
    source = tmp_path / 'native.yaml'
    source.write_text(yaml.safe_dump({'sequences': [
        {'protein': {'id': 'A', 'sequence': 'ACDE'}},
        {'protein': {'id': 'B', 'sequence': 'FGHI'}},
    ]}))
    calls = []
    def replay(*, sequences, params):
        calls.append((sequences, params.get('msa_cache_only')))
        return {'cache_hit': True}
    monkeypatch.setattr('services.msa_preparation.replay_model_msa', replay)
    params = {'boltz_use_msa': True, 'bcp_input_path': str(source), 'msa_provider': 'colabfold_api'}
    assert setup.inspect_msa_cache('boltz_cp_experimental', params) == {'state': 'ready'}
    assert calls == [(['ACDE', 'FGHI'], None)]
    assert params.get('msa_cache_only') is None


def test_preprocessed_not_forced_through_config_validator(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, 'fold_cp_config_proteins', lambda _: pytest.fail('preprocessed is not YAML'))
    assert handoff.prepare_launch_msa('boltz_cp_experimental', {
        'boltz_use_msa': False, 'bcp_input_format': 'preprocessed',
        'bcp_input_path': str(tmp_path),
    }, tmp_path / 'output')['bcp_input_path'] == str(tmp_path)
    source = (Path(__file__).resolve().parents[3] / 'modules/boltz_cp_experimental.nf').read_text()
    assert 'DATA_ARG="\\$TASK_ROOT/staged_input/input_bundle"' in source
    assert 'if [ "\\$INPUT_FORMAT" = "config_files" ]; then' in source


def test_uncompiled_batch_cannot_claim_native_cache_ready(monkeypatch):
    monkeypatch.setattr('services.msa_preparation.replay_model_msa', lambda **kw: pytest.fail('uncompiled roster must not replay'))
    assert setup.inspect_msa_cache('boltz_cp_experimental', {
        'boltz_use_msa': True, 'msa_provider': 'colabfold_api', 'msa_cache_only': True, 'sequence': 'ACDE',
        'sequence_batch_entries': [{'name': 'variant', 'sequence': 'FGHI'}],
    }) == {'state': 'unresolved'}


@pytest.mark.parametrize('component_key', ['esmf_complex_components_json', 'complex_components_json'])
def test_esmfold_cache_inspects_native_complex_not_unrelated_primary(component_key, monkeypatch):
    from biomodstack_msa_api import MSACacheMiss

    calls = []
    cached = ['ACDE', 'FGHI']
    def replay(*, sequences, params):
        calls.append(sequences)
        if sequences != cached:
            raise MSACacheMiss()
        return {'cache_hit': True}

    monkeypatch.setattr('services.msa_preparation.replay_model_msa', replay)
    params = {
        'esmf_use_msa': True, 'msa_cache_only': True, 'msa_provider': 'colabfold_api',
        'core_protein_scientific_contract': 1, 'esmf_sequence': 'WRONG',
        component_key: json.dumps([
            {'id': 'A', 'type': 'protein', 'sequence': 'ACDE'},
            {'id': 'B', 'type': 'peptide', 'sequence': 'FGHI'},
            {'id': 'C', 'type': 'ligand', 'sequence': 'IGNORED'},
        ]),
    }
    assert setup.inspect_msa_cache('esmfold2', params) == {'state': 'ready'}
    assert calls == [['ACDE', 'FGHI']]

    calls.clear()
    cached = ['WRONG']  # A cached primary must not certify a different complex.
    assert setup.inspect_msa_cache('esmfold2', params) == {'state': 'miss'}
    assert calls == [['ACDE', 'FGHI']]
    with pytest.raises(ValueError, match='cache miss for native request roster'):
        setup.preflight_msa_provider('esmfold2', params)
