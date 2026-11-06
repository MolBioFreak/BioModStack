"""Shape peer handoff: real controller packager and native command builders.

Only provider I/O and inference are inert. Shared compiler declaration/caller
integration is tested separately by the parent lane, not silently mocked here.
"""
from pathlib import Path
import json
import sys
from types import SimpleNamespace
import shutil
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/shape_blueprint'))
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import run_shape_validator_suite as peer
import component_adapter
from services import msa_preparation
from biomodstack_msa_handoff import digest


@pytest.mark.parametrize('validator', ['boltz2', 'protenix_v2'])
@pytest.mark.parametrize('provider', ['colabfold_api', 'neurosnap_api'])
def test_selected_provider_controller_package_to_native_reader(tmp_path, monkeypatch, validator, provider):
    settings = dict(msa_provider=provider, colabfold_use_env=False, colabfold_use_filter=False,
        colabfold_pairing_mode='unpaired', msa_neurosnap_coverage_percent=37,
        msa_neurosnap_identity_percent=81, msa_neurosnap_max_sequences=27,
        msa_neurosnap_force_uppercase=False, msa_neurosnap_pad_sequences=False,
        protenix_msa_backend=provider, protenix_use_msa=True, boltz_use_msa=True)
    source = tmp_path / 'provider-source.a3m'
    source.write_text('>query\nAAAAA\n>INERT_provider_hit\nAAA-A\n')
    calls = []
    def provider_io(*, sequences, params):
        assert sequences == ['AAAAA']
        assert params['msa_provider'] == provider
        assert params['colabfold_use_filter'] is False
        assert params['msa_neurosnap_coverage_percent'] == 37
        assert params['msa_neurosnap_identity_percent'] == 81
        calls.append(dict(params))
        return dict(provider=provider, request_digest='a' * 64, cache_hit=False,
            provenance={'fixture': 'INERT provider response'}, artifacts=[dict(chain_index=0, role='unpaired',
                path=str(source), sha256=digest(source.read_bytes()))])
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', provider_io)
    def transport(service_id, roster):
        assert service_id == 'shape:' + validator + ':generated_msa'
        native = tmp_path / 'native.json'
        native.write_text(json.dumps(roster))
        package = tmp_path / 'controller-package'
        msa_preparation.prepare_protenix_inputs(None, native, package, settings)
        relocated = tmp_path / 'worker-package'
        shutil.copytree(package, relocated)
        shutil.rmtree(package)
        source.unlink()
        return relocated, digest((relocated / 'msa-inputs.json').read_bytes())
    monkeypatch.setattr(component_adapter, 'await_external_service', transport)
    observed = []
    def native_inference(command, **kwargs):
        if validator == 'boltz2':
            payload = json.loads((tmp_path / 'output/boltz_inputs/shape.yaml').read_bytes())
            chain = payload['sequences'][0]['protein']
            msa = Path(chain['msa'])
            assert '--use_msa_server' not in command
        else:
            payload = json.loads((tmp_path / 'output/protenix_input.json').read_bytes())
            chain = payload[0]['sequences'][0]['proteinChain']
            msa = Path(chain['unpairedMsaPath'])
        assert msa.read_text() == '>query\nAAAAA\n>INERT_provider_hit\nAAA-A\n'
        observed.append(command)
        return SimpleNamespace(returncode=0, stdout='INERT native invocation', stderr='')
    monkeypatch.setattr(peer.subprocess, 'run', native_inference)
    monkeypatch.setattr(peer, '_sample_record', lambda *args: {'status': 'completed', 'fixture': 'INERT'})
    if validator == 'boltz2':
        peer._run_boltz('AAAAA', tmp_path / 'output', recycling_steps=3, sampling_steps=50, diagnostics=[], settings=settings)
    else:
        peer._run_protenix('AAAAA', tmp_path / 'output', seed=0, code_root=ROOT, diagnostics=[], settings=settings)
    assert len(calls) == len(observed) == 1


def test_shape_declaration_keeps_peer_specific_settings_and_no_msa_paths():
    from services.shape_native_settings import generated_msa_services
    params = dict(shape_sequence_policy='auto', shape_sequences_per_backbone=1,
        shape_validator_suite='boltz2,protenix_v2', shape_validator_settings={
            'boltz2': {'boltz_use_msa': True, 'msa_provider': 'neurosnap_api', 'msa_neurosnap_coverage_percent': 37},
            'protenix_v2': {'protenix_use_msa': True, 'msa_provider': 'colabfold_api', 'colabfold_use_filter': False}})
    rows = generated_msa_services(params)
    assert [(row.logical_id, row.provider) for row in rows] == [
        ('shape:boltz2:generated_msa', 'neurosnap_api'), ('shape:protenix_v2:generated_msa', 'colabfold_api')]
    for row, validator in zip(rows, ('boltz2', 'protenix_v2')):
        assert json.loads(row.settings_json) == params['shape_validator_settings'][validator]
    assert generated_msa_services({**params, 'shape_sequences_per_backbone': 0}) == []
    assert generated_msa_services({**params, 'shape_sequence_policy': 'skip'}) == []


def test_selected_capacity_is_not_host_sum_or_free_memory():
    from services.shape_requests import selected_gpu_memory_gib
    observation = {'gpus': [{'index': 0, 'memory_total_mb': 49152, 'memory_used_mb': 48000},
                            {'index': 1, 'memory_total_mb': 16384, 'memory_used_mb': 0}]}
    assert selected_gpu_memory_gib(observation, [0]) == 48
    assert selected_gpu_memory_gib(observation, [1]) == 16
    assert selected_gpu_memory_gib(observation, [0, 1]) == 16
    assert selected_gpu_memory_gib(None, [0]) is None
    assert selected_gpu_memory_gib(observation, [2]) is None
