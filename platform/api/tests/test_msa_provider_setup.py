"""Shared setup/service tests; fixtures are not live provider acceptance."""
import hashlib
import json
from pathlib import Path

import pytest

from services import msa_preparation as preparation
from services import msa_provider_setup as setup


@pytest.mark.parametrize('defect', ['oversize', 'empty', 'wrong_uid', 'state_file', 'state_symlink', 'state_public', 'state_unwritable'])
def test_readiness_rejects_unsafe_metadata_without_reads_or_creation(tmp_path, monkeypatch, defect):
    import os
    key = tmp_path / 'key'
    key.write_text('offline-fixture')
    key.chmod(0o600)
    state = tmp_path / 'state'
    cache = tmp_path / 'cache'
    monkeypatch.setenv('BMS_MSA_CACHE', str(cache))
    monkeypatch.setenv('BMS_MSA_API_STATE_ROOT', str(state))
    monkeypatch.setenv('BMS_NEUROSNAP_API_KEY_FILE', str(key))
    if defect in ('oversize', 'empty'):
        key.write_bytes(b'x' * (4097 if defect == 'oversize' else 0))
    elif defect == 'wrong_uid':
        original = Path.lstat
        def lstat(path, *args, _lstat=original, **kwargs):
            result = _lstat(path, *args, **kwargs)
            if path == key:
                fields = list(result)
                fields[4] += 1
                return os.stat_result(fields)
            return result
        monkeypatch.setattr(Path, 'lstat', lstat)
    elif defect == 'state_file':
        state.write_text('not a directory')
    elif defect == 'state_symlink':
        state.symlink_to(tmp_path, target_is_directory=True)
    elif defect == 'state_public':
        state.mkdir(mode=0o777)
        state.chmod(0o777)
    elif defect == 'state_unwritable':
        state.mkdir()
        original = os.access
        monkeypatch.setattr(os, 'access', lambda path, mode, _access=original: False if Path(path) == state else _access(path, mode))
    before = set(tmp_path.rglob('*'))
    for name in ('read_text', 'read_bytes', 'open'):
        original = getattr(Path, name)
        def deny(path, *args, _original=original, **kwargs):
            if path == key:
                pytest.fail('readiness must not open/read a credential')
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(Path, name, deny)
    result = setup.provider_readiness()
    provider = result['providers']['neurosnap_api']
    assert not provider['configured'] and provider['blockers']
    assert provider['live_acceptance'] == 'not_checked_by_setup'
    assert provider['authentication'] == 'not_checked'
    assert set(tmp_path.rglob('*')) == before
    assert not cache.exists()
    assert 'offline-fixture' not in json.dumps(result) and str(key) not in json.dumps(result)


def test_setup_is_non_submitting_and_does_not_read_secret(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_MSA_CACHE', str(tmp_path / 'cache'))
    key = tmp_path / 'key'
    key.write_text('fixture-not-a-real-credential')
    key.chmod(0o600)
    monkeypatch.setenv('BMS_NEUROSNAP_API_KEY_FILE', str(key))
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path == key:
            pytest.fail('readiness must not read credential contents')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    result = setup.provider_readiness()
    assert result['providers']['neurosnap_api']['configured'] is True
    assert result['providers']['neurosnap_api']['authentication'] == 'not_checked'
    assert 'fixture-not-a-real-credential' not in json.dumps(result)
    assert str(key) not in json.dumps(result)
    key.chmod(0o644)
    assert setup.provider_readiness()['providers']['neurosnap_api']['configured'] is False


def test_missing_key_does_not_disable_colabfold(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_MSA_CACHE', str(tmp_path))
    monkeypatch.delenv('BMS_NEUROSNAP_API_KEY_FILE', raising=False)
    import biomodstack_msa_controller as controller
    monkeypatch.setenv('BMS_MSA_CONTROLLER_CONFIG', str(tmp_path / 'config.json'))
    monkeypatch.setattr(controller, 'validate_controller_config', lambda path: {'fixture': True})
    result = setup.provider_readiness()
    assert result['providers']['colabfold_api']['configured'] is True
    assert result['providers']['neurosnap_api']['configured'] is False
    assert result['local_search_enabled'] is False


def test_deployment_owned_paths_never_come_from_job_params(tmp_path, monkeypatch):
    import biomodstack_msa_api as api
    monkeypatch.setenv('BMS_MSA_CACHE', str(tmp_path / 'owned-cache'))
    monkeypatch.delenv('BMS_NEUROSNAP_API_KEY_FILE', raising=False)
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        return {'fixture': True}
    monkeypatch.setattr(api, 'prepare_msa', prepare)
    result = preparation.prepare_model_msa(sequences=['AAAA'], params={
        'msa_provider': 'colabfold_api', 'msa_cache_dir': '/browser-chosen',
        'credential_file': '/browser-secret', 'protenix_n_sample': 3,
        'msa_cache_only': True,
    })
    assert result == {'fixture': True}
    assert calls[0]['cache_root'] == tmp_path / 'owned-cache' / 'provider_api'
    assert calls[0]['credential_file'] is None
    assert calls[0]['settings'] == {}
    assert calls[0]['cache_only'] is True


def test_setup_rejects_missing_key_before_provider_submission(tmp_path, monkeypatch):
    monkeypatch.setenv('BMS_MSA_CACHE', str(tmp_path))
    monkeypatch.delenv('BMS_NEUROSNAP_API_KEY_FILE', raising=False)
    with pytest.raises(ValueError, match='BMS_NEUROSNAP_API_KEY_FILE'):
        setup.preflight_msa_provider('protenix', {
            'msa_provider': 'neurosnap_api', 'protenix_msa_backend': 'neurosnap_api',
        })
    setup.preflight_msa_provider('protenix', {
        'msa_provider': 'neurosnap_api', 'protenix_use_msa': False,
    })
    setup.preflight_msa_provider('protenix', {
        'msa_provider': 'neurosnap_api', 'msa_cache_only': True,
    })


def test_shared_provider_result_reaches_native_model_and_relocates(tmp_path, monkeypatch):
    sequence = 'AAAA'
    raw = b'>fixture-query\nAAAA\n>fixture-hit\nAA-A\n'
    cached = tmp_path / 'cached.a3m'
    cached.write_bytes(raw)
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        return {'provider': 'neurosnap_api', 'request_digest': 'fixture-digest',
                'cache_hit': True, 'provenance': {'fixture': True},
                'artifacts': [{'chain_index': 0, 'role': 'unpaired', 'path': str(cached),
                               'sha256': hashlib.sha256(raw).hexdigest()}]}
    monkeypatch.setattr(preparation, 'prepare_model_msa', prepare)
    payload = [{'name': 'fixture', 'modelSeeds': [7, 19], 'sequences': [
        {'proteinChain': {'sequence': sequence, 'count': 2}}]}]
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(payload))
    settings = {'msa_provider': 'neurosnap_api', 'protenix_msa_backend': 'neurosnap_api'}
    destination = tmp_path / 'host' / 'prepared'
    receipt = preparation.prepare_protenix_inputs(None, source, destination, settings)
    assert calls[0]['sequences'] == [sequence]
    assert receipt['provenance']['cache_hit'] is True
    worker = tmp_path / 'worker'
    destination.rename(worker)
    cached.unlink()
    output = preparation.materialize_protenix_inputs(worker, worker / 'input.json', receipt['settings'])
    model = json.loads(output.read_text())
    assert model[0]['modelSeeds'] == [7, 19]
    chain = model[0]['sequences'][0]['proteinChain']
    assert chain['count'] == 2
    assert Path(chain['unpairedMsaPath']).read_bytes() == raw
    assert 'pairedMsaPath' not in chain


def test_protenix_paired_conversion_matches_pinned_native_group_convention():
    import re
    first = b'>101\nAAAA\n>UniRef100_A0A_TEST\t90\t0.9\nAA-A\n'
    second = b'>102\nCCCC\n>UniRef100_B0B_TEST\t80\t0.8\nCC-C\n'
    # Use normal UniRef accessions (no embedded suffix) as provided by MMseqs.
    first = first.replace(b'A0A_TEST', b'A0A')
    second = second.replace(b'B0B_TEST', b'B0B')
    converted = [preparation._protenix_paired_headers(data) for data in (first, second)]
    groups = []
    for data in converted:
        header = data.decode().splitlines()[2][1:]
        group = re.match(r'^UniRef100_[^_]+_([^_/]+)', header).group(1)
        groups.append(group)
        assert data.decode().splitlines()[0] == '>query'
    assert groups == ['1', '1']
    assert converted[0].splitlines()[3] == b'AA-A'
    assert converted[1].splitlines()[3] == b'CC-C'


def test_colabfold_miss_uses_qualified_resumable_controller(tmp_path, monkeypatch):
    import biomodstack_msa_api as api
    import biomodstack_msa_controller as controller
    monkeypatch.setenv('BMS_MSA_CACHE', str(tmp_path / 'cache'))
    config = tmp_path / 'controller.json'
    monkeypatch.setenv('BMS_MSA_CONTROLLER_CONFIG', str(config))
    calls = []
    active = []
    def client(**kwargs):
        calls.append(kwargs)
        if kwargs.get('cache_only'):
            raise api.MSACacheMiss()
        assert active == [True]
        return {'fixture': 'native-result'}
    def qualified(path, request, operation, *, resume_same_request=False):
        assert path == config and resume_same_request is True
        assert request['sequences'] == ['ACDE']
        assert request['settings'] == {'use_env': False}
        assert request['cache_root'] == str(tmp_path / 'cache/provider_api')
        active.append(True)
        return operation()
    monkeypatch.setattr(api, 'prepare_msa', client)
    monkeypatch.setattr(controller, 'prepare', qualified)
    assert preparation.prepare_model_msa(sequences=['ACDE'], params={
        'msa_provider': 'colabfold_api', 'colabfold_use_env': False}) == {'fixture': 'native-result'}
    assert len(calls) == 2 and calls[0]['cache_only'] is True


def test_verified_cache_replay_needs_no_configuration(tmp_path, monkeypatch):
    import biomodstack_msa_api as api
    import biomodstack_msa_controller as controller
    monkeypatch.setenv('BMS_MSA_CACHE', str(tmp_path / 'cache'))
    monkeypatch.delenv('BMS_MSA_CONTROLLER_CONFIG', raising=False)
    monkeypatch.setenv('BMS_NEUROSNAP_API_KEY_FILE', 'invalid-relative-secret-path')
    def replay(**kwargs):
        assert kwargs['cache_only'] is True and kwargs['credential_file'] is None
        return {'cache_hit': True}
    monkeypatch.setattr(api, 'prepare_msa', replay)
    monkeypatch.setattr(controller, 'prepare', lambda *a, **k: pytest.fail('replay must not qualify submission'))
    for provider in ('colabfold_api', 'neurosnap_api'):
        assert preparation.prepare_model_msa(sequences=['ACDE'], params={
            'msa_provider': provider}) == {'cache_hit': True}
        setup.preflight_msa_provider('protenix', {'sequence': 'ACDE', 'msa_provider': provider})


def test_controller_readiness_reuses_configuration_checker(tmp_path, monkeypatch):
    import biomodstack_msa_controller as controller
    monkeypatch.setenv('BMS_MSA_CACHE', str(tmp_path / 'cache'))
    config = tmp_path / 'controller.json'
    monkeypatch.setenv('BMS_MSA_CONTROLLER_CONFIG', str(config))
    calls = []
    def checker(path):
        calls.append(path)
        raise RuntimeError('fixture internal identity must not leak')
    monkeypatch.setattr(controller, 'validate_controller_config', checker)
    result = setup.provider_readiness()
    assert calls == [config]
    assert result['providers']['colabfold_api']['configured'] is False
    assert 'fixture internal' not in json.dumps(result)


def test_protenix_batch_searches_each_native_task_separately(tmp_path, monkeypatch):
    calls = []
    def provider(*, sequences, params):
        calls.append(sequences)
        artifacts = []
        for index, sequence in enumerate(sequences):
            for role in ('paired', 'unpaired'):
                data = f'>query\n{sequence}\n>UniRef100_fixture\n{sequence}\n'.encode()
                path = tmp_path / f'{len(calls)}-{index}-{role}.a3m'
                path.write_bytes(data)
                artifacts.append(dict(chain_index=index, role=role, path=str(path), sha256=hashlib.sha256(data).hexdigest()))
        return dict(provider='colabfold_api', request_digest=str(len(calls)), cache_hit=True,
                    provenance={'fixture': True}, artifacts=artifacts)
    monkeypatch.setattr(preparation, 'prepare_model_msa', provider)
    payload = [dict(name=name, modelSeeds=[7, 19], sequences=[
        {'proteinChain': {'id': [cid], 'sequence': sequence, 'count': 1}}
        for cid, sequence in zip(['B', 'A'], sequences)])
        for name, sequences in [('one', ['ACDE', 'FGHI']), ('two', ['KLMN', 'PQRS'])]]
    source = tmp_path / 'input.json'
    source.write_text(json.dumps(payload))
    manifest = preparation.prepare_protenix_inputs(None, source, tmp_path / 'portable', {
        'msa_provider': 'colabfold_api', 'colabfold_pairing_mode': 'unpaired_paired'})
    assert calls == [['ACDE', 'FGHI'], ['KLMN', 'PQRS']]
    assert [r['task_name'] for r in manifest['provenance']['task_receipts']] == ['one', 'two']
    assert len(manifest['artifacts']) == 8
    assert [t['modelSeeds'] for t in manifest['model_input']] == [[7, 19], [7, 19]]

def test_cache_miss_text_cannot_authorize_provider_submission(tmp_path, monkeypatch):
    import biomodstack_msa_api as api
    import biomodstack_msa_controller as controller
    calls = []
    def failed(**kwargs):
        calls.append(kwargs)
        raise api.MSAAPIError('no verified provider/settings/query-bound cached MSA')
    monkeypatch.setattr(api, 'prepare_msa', failed)
    monkeypatch.setattr(controller, 'prepare', lambda *a, **k: pytest.fail('must not submit'))
    with pytest.raises(api.MSAAPIError):
        preparation.prepare_model_msa(sequences=['ACDEFGHIKLMNPQRSTVWY'], params={})
    assert len(calls) == 1 and calls[0]['cache_only'] is True


def test_preflight_cold_cache_creates_no_storage(tmp_path, monkeypatch):
    root = tmp_path / 'uncreated-cache'
    monkeypatch.setattr(setup, 'cache_root', lambda: root)
    monkeypatch.setattr(setup, 'provider_readiness', lambda: {
        'providers': {'colabfold_api': {'configured': True, 'blockers': []}}})
    before = set(tmp_path.rglob('*'))
    setup.preflight_msa_provider('protenix', {
        'sequence_input': 'ACDEFGHIKLMNPQRSTVWY', 'protenix_use_msa': True})
    assert set(tmp_path.rglob('*')) == before
    assert not root.exists()
