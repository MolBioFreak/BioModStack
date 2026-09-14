"""Hosted ESMFold2 wiring; synthetic provider bytes, never scientific inference."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from schemas import JobCreate
from services.msa_policy import apply_msa_policy, requires_msa_search
from services.model_msa_handoff import prepare_launch_msa
from services.core_protein_execution_settings import prepare_receipt
from tests.test_msa_bundle_integration import offline_bundle
from tests.test_remote_rectify_inputs import placement, admit_current_job, pack

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('esm_hosted_runner', ROOT / 'scripts/run_esmfold2_inference.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


@pytest.fixture
def provider(monkeypatch, tmp_path):
    from biomodstack_msa_handoff import digest
    calls = []
    def prepare(*, sequences, params):
        calls.append((list(sequences), copy.deepcopy(params)))
        artifacts = []
        for i, sequence in enumerate(sequences):
            data = f'>query\n{sequence}\n>synthetic_transport_row\n{sequence}\n'.encode()
            path = tmp_path / f'provider-{i}.a3m'
            path.write_bytes(data)
            artifacts.append(dict(chain_index=i, role='unpaired', path=str(path), sha256=digest(data)))
        return dict(provider=params['msa_provider'], artifacts=artifacts,
                    request_digest='a' * 64, provenance={'synthetic': True}, cache_hit=False)
    monkeypatch.setattr('services.msa_preparation.prepare_model_msa', prepare)
    return calls


def request_params():
    return dict(esmf_use_msa=True, msa_provider='neurosnap_api', model_variant='full',
                quality_preset='custom', num_loops=3, num_sampling_steps=200,
                num_diffusion_samples=1, run_frustrampnn=False,
                complex_components=[{'type': 'protein', 'id': 'A', 'sequence': 'ACDE'},
                    {'type': 'dna', 'id': 'B', 'sequence': 'GATATGTAGCTGCT'},
                    {'type': 'ligand', 'id': 'C', 'ccd': ['MN']},
                    {'type': 'ligand', 'id': 'D', 'ccd': ['MN']},
                    {'type': 'ligand', 'id': 'E', 'ccd': ['GTP']}])


@pytest.mark.parametrize('model', ['esmfold2', 'esmfold2_experimental'])
def test_explicit_opt_in_schema_and_old_defaults(model):
    from model_registry import ModelRegistry
    fields = {p.name: p for p in ModelRegistry().get_model(model).params}
    assert fields['esmf_use_msa'].default is False
    assert 'neurosnap_api' in fields['msa_provider'].enum
    assert not requires_msa_search(model, {'msa_provider': 'neurosnap_api'})
    params = request_params()
    typed = JobCreate(name='transport', model_id=model, mode='predict', params=params)
    assert typed.params == params
    assert requires_msa_search(model, apply_msa_policy(model, params))
    with pytest.raises(ValueError, match='boolean'):
        JobCreate(name='bad', model_id=model, mode='predict', params={'esmf_use_msa': 'false'})
    with pytest.raises(ValueError, match='server-owned'):
        JobCreate(name='bad', model_id=model, mode='predict', params={'esmf_msa_preparation': {}})


def test_provider_to_native_staged_receipt_and_tamper(provider, tmp_path):
    original = request_params()
    before = copy.deepcopy(original)
    params = apply_msa_policy('esmfold2', original)
    params['core_protein_scientific_contract'] = 1
    prepared = prepare_launch_msa('esmfold2', params, tmp_path / 'prepared')
    authority = prepared.pop('esmf_msa_preparation')
    assert original == before
    assert len(provider) == 1 and provider[0][0] == ['ACDE']
    assert prepared['complex_components'][1:] == original['complex_components'][1:]
    msa = prepared['complex_components'][0]['msa_path']
    # Simulate placement with exactly copied bytes, keeping source path identity.
    relocated = tmp_path / 'remote-input.a3m'
    relocated.write_bytes(Path(msa).read_bytes())
    request = {**prepared, 'esmf_requested_settings_json': json.dumps(original),
                   'esmf_msa_preparation_json': json.dumps(authority)}
    argv, receipt = runner.compile_workflow_request(request, {msa: str(relocated)})
    original_bytes = relocated.read_bytes()
    relocated.write_text('>query\nQRST\n')
    with pytest.raises(ValueError, match='prepared MSA bytes differ'):
        runner.compile_workflow_request(request, {msa: str(relocated)})
    relocated.write_bytes(original_bytes)
    swapped = copy.deepcopy(request)
    swapped['complex_components'][0]['sequence'] = 'QRST'
    with pytest.raises(ValueError, match='prepared MSA sequence/scope differs'):
        runner.compile_workflow_request(swapped, {msa: str(relocated)})
    native = vars(runner.build_parser().parse_args(argv))
    assert (native['model_variant'], native['num_loops'], native['num_sampling_steps'], native['num_diffusion_samples']) == ('full', 3, 200, 1)
    assert json.loads(native['complex_components_json'])[0]['msa_path'] == str(relocated)
    path = tmp_path / 'effective_settings.json'
    path.write_text(json.dumps(receipt))
    job = SimpleNamespace(provenance={'core_protein_requested_params': original, 'esmf_msa_preparation': authority})
    verified = prepare_receipt(job, tmp_path, path)
    assert verified
    bad = copy.deepcopy(receipt)
    bad['sources'][0]['sha256'] = 'b' * 64
    path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='controller authority'):
        prepare_receipt(job, tmp_path, path)
    path.write_text(json.dumps(receipt))
    job.provenance.pop('esmf_msa_preparation')
    with pytest.raises(ValueError, match='inventories differ'):
        prepare_receipt(job, tmp_path, path)


def test_disabled_and_supplied_do_not_call_provider(provider, tmp_path):
    params = dict(sequence='ACDE', msa_provider='neurosnap_api')
    assert prepare_launch_msa('esmfold2', params, tmp_path / 'disabled') == params
    source = tmp_path / 'supplied.a3m'
    source.write_text('>q\nACDE\n')
    params.update(esmf_use_msa=True, model_variant='full', msa_path=str(source), core_protein_scientific_contract=1)
    prepared = prepare_launch_msa('esmfold2', params, tmp_path / 'supplied')
    assert prepared['msa_path'] == str(source)
    assert not provider


@pytest.mark.parametrize('variant', ['fast', None])
def test_fast_hosted_fails_before_provider(provider, tmp_path, variant):
    params = request_params()
    if variant is None:
        params.pop('model_variant')
    else:
        params['model_variant'] = variant
    with pytest.raises(ValueError, match='not MSA-conditioned'):
        JobCreate(name='fast', model_id='esmfold2', mode='predict', params=params)
    with pytest.raises(ValueError, match='not MSA-conditioned'):
        prepare_launch_msa('esmfold2', {**params, 'core_protein_scientific_contract': 1}, tmp_path)
    assert not provider


def test_actual_remote_bundle_preserves_generated_alignment(provider, placement, monkeypatch):
    import shutil
    from services.nextflow import compile_job_nextflow_invocation
    from scripts.lib.portable_inputs import resolve_input_path
    roots, job, target = placement
    job.model_id, job.mode = 'esmfold2', 'predict'
    job.params = request_params()
    admit_current_job(job, 'esmfold2', 'predict')
    original = copy.deepcopy(job.params)
    prepared = prepare_launch_msa('esmfold2', job.params, Path(job.output_dir) / 'prepared-msa')
    authority = prepared.pop('esmf_msa_preparation')
    job.provenance['esmf_msa_preparation'] = authority
    invocation = compile_job_nextflow_invocation(job, prepared, job.output_dir)
    invocation.materialize_inputs(Path(job.output_dir))
    for dependency in invocation.execution_plan.dependencies:
        if dependency.kind not in {'weights', 'image'}:
            continue
        path = roots['weights' if dependency.kind == 'weights' else 'containers'] / dependency.relative_path
        if dependency.kind == 'weights':
            path.mkdir(parents=True, exist_ok=True)
            path = path / 'transport-fixture.bin'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'explicit offline runtime transport double')
    result = pack(placement, invocation)
    assert job.params == original
    source = Path(prepared['complex_components'][0]['msa_path'])
    expected = source.read_bytes()
    assert any(transfer.source == source or source.is_relative_to(transfer.source)
               for transfer in result.input_transfers)
    for transfer in result.input_transfers:
        destination = Path(transfer.remote_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if transfer.source.is_dir():
            shutil.copytree(transfer.source, destination)
        else:
            shutil.copyfile(transfer.source, destination)
    # The existing bundle binding owner, not a hand-authored alias map.
    binding_env = result.envelope.environment
    monkeypatch.setenv('BMS_PORTABLE_INPUT_BINDINGS', binding_env['BMS_PORTABLE_INPUT_BINDINGS'])
    source.unlink()
    assert resolve_input_path(str(source)).read_bytes() == expected


def test_actual_typed_normalization_omitted_values_and_presets(monkeypatch, tmp_path):
    from routers.jobs import normalize_job_request
    from services.nextflow import build_nextflow_command
    native_bin = tmp_path / 'nextflow'
    native_bin.write_text('#!/bin/sh\nexit 99\n')
    native_bin.chmod(0o755)
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', str(native_bin))
    original = dict(sequence='ACDE', model_variant='full', run_frustrampnn=False)
    typed = normalize_job_request(JobCreate(name='old-ui', model_id='esmfold2', mode='predict', params=original))
    assert all(k not in typed.params for k in ['num_loops', 'num_sampling_steps', 'num_diffusion_samples', 'quality_preset'])
    params = {**typed.params, 'core_protein_scientific_contract': 1}
    cmd = build_nextflow_command('esmfold2', 'predict', params, str(tmp_path / 'out'), job_id='old-ui')
    keys = ('num_loops', 'num_sampling_steps', 'num_diffusion_samples')
    actual = {k: int(cmd[cmd.index('--esmf_' + k)+1]) for k in keys}
    assert actual == dict(num_loops=3, num_sampling_steps=50, num_diffusion_samples=1)
    _, receipt = runner.compile_workflow_request({**params, **actual}, {})
    assert [receipt['settings'][k]['effective'] for k in keys] == [3, 50, 1]
    cmd = build_nextflow_command('esmfold2', 'predict', {**params, 'quality_preset': 'thorough', 'num_diffusion_samples': 1}, str(tmp_path / 'preset'), job_id='preset')
    assert [cmd[cmd.index('--esmf_' + k)+1] for k in ('num_loops', 'num_sampling_steps', 'num_diffusion_samples')] == ['5', '100', '1']
