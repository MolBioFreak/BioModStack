"""Shared setup/service tests; fixtures are not live provider acceptance."""
import hashlib
import json
from pathlib import Path

import pytest

from services import msa_preparation as preparation
from services import msa_provider_setup as setup


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
