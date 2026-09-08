"""No scientific jobs or service submissions: real selection/admission gates."""
import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.msa_policy import POLICY, apply_msa_policy
from schemas import JobCreate
from services.nextflow import build_nextflow_command, _build_msa_batch_command

ROOT = Path(__file__).resolve().parents[3]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('key', ['msa_provider', 'protenix_msa_backend'])
def test_saved_local_rejected_by_global_schema_and_preview(key):
    params = {key: 'local', 'sequence': 'AAAA'}
    with pytest.raises(ValidationError, match='Local MSA search is disabled'):
        JobCreate(name='saved', model_id='protenix', mode='predict', params=params)
    with pytest.raises(ValueError, match='re-preview'):
        build_nextflow_command('protenix', 'predict', params, '/unused')
    assert params[key] == 'local'


def test_auto_effective_command_and_requested_intent():
    params = {'protenix_msa_backend': 'auto', 'msa_provider': 'auto', 'sequence': 'AAAA'}
    request = JobCreate(name='auto', model_id='protenix', mode='predict', params=params)
    assert request.params['msa_provider'] == 'auto'
    command = build_nextflow_command('protenix', 'predict', params, '/unused')
    for key in ('msa_provider', 'protenix_msa_backend'):
        assert command[command.index('--' + key) + 1] == 'colabfold_api'
    assert params['protenix_msa_backend'] == 'auto'


@pytest.mark.parametrize('params', [
    {'protenix_use_msa': False, 'protenix_msa_backend': 'none'},
    {'protenix_use_msa': False, 'protenix_msa_backend': 'esm'},
    {'boltz_use_msa': False},
    {'msa_a3m': '/verified/input.a3m', 'pairedMsaPath': '/verified/paired.a3m'},
])
def test_policy_does_not_drop_no_msa_or_alignment_inputs(params):
    effective = apply_msa_policy('protenix', params)
    for key, value in params.items():
        assert effective[key] == value


@pytest.mark.asyncio
async def test_internal_construct_cannot_bypass_admission():
    from routers import jobs
    request = JobCreate.model_construct(name='saved', model_id='protenix', mode='predict',
        params={'msa_provider': 'local'})
    with pytest.raises(HTTPException) as exc:
        await jobs.create_job(request, background_tasks=None, session=None)
    assert exc.value.status_code == 422
    assert 're-preview' in exc.value.detail


def test_local_batch_and_server_block_before_side_effects(monkeypatch):
    from services import msa_server
    monkeypatch.setattr(msa_server, 'get_colabfold_db', lambda: pytest.fail('DB accessed'))
    with pytest.raises(ValueError, match='Local MSA search is disabled'):
        msa_server.ensure_server_for_db('uniref', 0)
    with pytest.raises(ValueError, match='Local MSA search is disabled'):
        _build_msa_batch_command({}, '/unused')


@pytest.mark.asyncio
async def test_local_msa_routes_block_before_db_or_runtime():
    from routers import msa
    with pytest.raises(HTTPException) as exc:
        await msa.create_msa_job(None, session=None, molbio_session=None)
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await msa.start_msa_server(None)
    assert exc.value.status_code == 422


def test_protenix_auto_never_selects_local_for_large_jobs():
    prep = script('prepare_protenix_msa')
    assert prep.choose_backend('auto', {'tasks': 100000}, 1, 1, 1) == 'colabfold_api'
    with pytest.raises(ValueError, match='Local MSA search is disabled'):
        prep.choose_backend('local', {}, 1, 1, 1)


def test_local_runtime_block_before_output_or_db(tmp_path):
    runner = script('run_local_msa')
    with pytest.raises(ValueError, match='Local MSA search is disabled'):
        runner.run_colabfold_msa_workflow('AAAA', 'blocked', str(tmp_path / 'out'), db_path='/absent')
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('failure', [TimeoutError('timeout'), RuntimeError('429 throttled'), RuntimeError('cancelled')])
def test_api_failure_propagates_without_local_or_empty_fallback(tmp_path, monkeypatch, failure):
    import json
    from types import SimpleNamespace
    prep = script('prepare_protenix_msa')
    source = tmp_path / 'input.json'
    source.write_text(json.dumps([{'name': 'test', 'sequences': [{'proteinChain': {'sequence': 'AAAA', 'count': 1}}]}]))
    output = tmp_path / 'output.json'
    monkeypatch.setattr(prep, 'parse_args', lambda: SimpleNamespace(
        prepared_inputs=None, prepared_sha256=None,
        backend='auto', input_json=str(source), output_json=str(output), out_dir=str(tmp_path / 'work'),
        cache_dir=None, report_json=None, small_max_tasks=1, small_max_protein_chains=1,
        small_max_total_residues=1, colabfold_api_host='unchanged-provider'))
    from services import msa_preparation
    def fail(**kwargs):
        raise failure
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', fail)
    monkeypatch.setattr(prep, 'prepare_with_local_msa', lambda **kwargs: pytest.fail('local fallback'))
    with pytest.raises(type(failure), match=str(failure)):
        msa_preparation.prepare_protenix_inputs(None, source, tmp_path / 'prepared', {})
    assert not output.exists()


def test_existing_alignment_reused_without_search(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    prep = script('prepare_protenix_msa')
    alignment = tmp_path / 'verified.a3m'
    alignment.write_text('>query\nAAAA\n>homolog\nAAAA\n')
    source = tmp_path / 'input.json'
    payload = [{'name': 'test', 'sequences': [{'proteinChain': {
        'sequence': 'AAAA', 'count': 1, 'unpairedMsaPath': str(alignment),
    }}]}]
    source.write_text(json.dumps(payload))
    output = tmp_path / 'output.json'
    monkeypatch.setattr(prep, 'parse_args', lambda: SimpleNamespace(
        prepared_inputs=None, prepared_sha256=None,
        backend='auto', input_json=str(source), output_json=str(output),
        out_dir=str(tmp_path / 'work'), cache_dir=None, report_json=None))
    monkeypatch.setattr(prep, 'prepare_with_colabfold_api', lambda **kwargs: pytest.fail('search'))
    monkeypatch.setattr(prep, 'prepare_with_local_msa', lambda **kwargs: pytest.fail('local search'))
    prep.main()
    assert json.loads(output.read_text()) == payload
    assert alignment.read_text() == '>query\nAAAA\n>homolog\nAAAA\n'


def test_empty_fallback_rejected_without_changing_explicit_no_msa():
    with pytest.raises(ValueError, match='cannot silently disable MSA'):
        apply_msa_policy('boltz2', {'msa_allow_empty_fallback': True})
    from services.msa_policy import requires_msa_search
    assert not requires_msa_search('boltz2', {'msa_provider': 'colabfold_api', 'boltz_use_msa': False})


def test_policy_and_existing_global_model_enum_agree():
    from model_registry import get_registry
    model = get_registry().get_model('protenix')
    field = next(p for p in model.params if p.name == 'protenix_msa_backend')
    assert POLICY['enabled_search_backend'] in field.enum
    assert 'local' not in field.enum
