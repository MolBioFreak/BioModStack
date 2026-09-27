"""Public BoltzGen controller transport; no native inference is run."""
import json
from copy import deepcopy
from pathlib import Path

from fastapi import BackgroundTasks
import pytest
import yaml

from database import Job
from routers import jobs
from schemas import JobCreate
from services import nextflow
from scripts.lib.boltzgen_inputs import input_identity, identity_digest
from test_core_protein_scientific_admission import admission
from test_boltzgen_runtime_regressions import _write_pdb


@pytest.fixture
def target(tmp_path, monkeypatch):
    root = tmp_path / 'sources'
    root.mkdir()
    path = root / 'target.pdb'
    _write_pdb(path, 'A', [1, 2, 3])
    monkeypatch.setattr(jobs, 'get_allowed_roots', lambda: {'inputs': root, 'bms_results': tmp_path / 'results'})
    import paths
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs': root, 'bms_results': tmp_path / 'results'})
    monkeypatch.setattr(paths, 'get_results_dir', lambda: tmp_path / 'results')
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: root)
    monkeypatch.setattr(paths, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(nextflow, 'get_data_root', lambda: tmp_path)
    monkeypatch.setenv('BMS_HOME', str(Path(__file__).resolve().parents[3]))
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['protein_binder', 'peptide_binder'])
async def test_public_boltzgen_queue_prepares_target_and_native_settings(admission, target, mode):
    requested = {'target_pdb': str(target), 'num_designs': 2, 'alpha': 0., 'min_plddt': None,
                 'boltzgen_skip_inverse_folding': False}
    response = await jobs._create_job(JobCreate(name='initial-' + mode, model_id='boltzgen', mode=mode,
        params=requested), BackgroundTasks(), admission)
    job = await admission.get(Job, response.id)
    root = Path(job.params['boltzgen_yaml_config'])
    config = yaml.safe_load((root / 'boltzgen_input.yaml').read_text())
    structural = [row['file'] for row in config['entities'] if 'file' in row]
    assert len(structural) == 1
    assert (root / structural[0]['path']).read_bytes() == target.read_bytes()
    assert job.params['boltzgen_prepared_sha256'] == identity_digest(input_identity(root))
    assert job.params['boltzgen_alpha'] == 0.
    assert job.params['boltzgen_skip_inverse_folding'] is False
    target.unlink()
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    assert invocation.entrypoint == 'workflows/boltzgen_generation.nf'
    assert invocation.command[invocation.command.index('-profile') + 1] == 'boltzgen,workstation_ryzen7960x'
    assert invocation.native_parameters['boltzgen_yaml_config'] == str(root)
    assert invocation.native_parameters['boltzgen_alpha'] == 0.
    assert invocation.native_parameters['boltzgen_skip_inverse_folding'] is False
    assert invocation.native_parameters['boltzgen_min_plddt'] is None
    assert '-params-file' in invocation.command
    native_json = next(item for item in invocation.generated_inputs if item.relative_path == '.boltzgen-generation-settings.json')
    native_settings = json.loads(native_json.payload)
    assert native_settings['boltzgen_alpha'] == 0.
    assert native_settings['boltzgen_skip_inverse_folding'] is False
    assert native_settings['boltzgen_min_plddt'] is None
    assert native_settings['core_protein_scientific_contract'] == job.provenance['core_protein_scientific_contract'] == 1
    assert native_settings['boltzgen_prepared_sha256'] == job.params['boltzgen_prepared_sha256']
    assert 'boltzgen_target_pdb_path' not in native_settings
    assert '--boltzgen_target_pdb_path' not in invocation.command
    assert '--boltzgen_scaffold_path' not in invocation.command
    assert json.loads(invocation.effective_json)['boltzgen_generation_mode'] == mode
    assert not {'rfd_models', 'af2_models', 'boltz_models', 'msa_local_db', 'msa_cache_dir'} & invocation.native_parameters.keys()


@pytest.mark.parametrize('mode', ['protein_binder', 'peptide_binder', 'nanobody_binder'])
def test_native_request_defaults_and_file_aliases_stay_model_owned(target, mode):
    from services.boltzgen_request_compatibility import parameter_contract
    request = JobCreate(name='native-fields', model_id='boltzgen', mode=mode,
        params={'target_pdb': 'inputs/target.pdb', 'alpha': 0, 'min_plddt': None,
                'filter_biased': False, 'job_name': 'native-fields', 'num_parallel_jobs': 1})
    original = request.model_dump()
    normalized = jobs.normalize_job_request(request)
    fields = {field['name'] for field in parameter_contract(mode)}
    assert set(normalized.params) == fields | {'boltzgen_mode', 'boltzgen_generation_mode', 'job_name', 'num_parallel_jobs'}
    assert normalized.params['boltzgen_target_pdb_path'] == str(target)
    assert normalized.params['boltzgen_min_plddt'] is None
    assert normalized.params['boltzgen_alpha'] == 0
    assert normalized.params['boltzgen_filter_biased'] is False
    assert 'min_plddt' not in normalized.params
    assert jobs.normalize_job_request(normalized).model_dump() == normalized.model_dump()
    assert request.model_dump() == original


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['protein_binder', 'peptide_binder', 'nanobody_binder'])
async def test_native_template_save_reopen_update_preserves_exact_science(admission, target, mode):
    from routers import models, user_templates
    from services.boltzgen_request_compatibility import parameter_contract
    requested = {'target_pdb': 'inputs/target.pdb', 'alpha': 0, 'min_plddt': None,
                 'filter_biased': False, 'step_scale': None, 'scaffold_length': '80-120'}
    original = deepcopy(requested)
    template = await user_templates.create_user_template(user_templates.UserTemplateCreate(
        name='native-' + mode, base_template_id='antibody_denovo', model_id='boltzgen',
        mode=mode, params=requested), admission)
    template_id = template.id
    admission.expire_all()
    reopened = await user_templates.get_user_template(template_id, admission)
    assert (reopened.model_id, reopened.mode, reopened.params) == ('boltzgen', mode, original)
    changed = {**original, 'skip_inverse_folding': False, 'noise_scale': 0}
    await user_templates.update_user_template(template_id,
        user_templates.UserTemplateUpdate(params=changed), admission)
    admission.expire_all()
    reopened = await user_templates.get_user_template(template_id, admission)
    assert reopened.params == changed
    normalized = jobs.normalize_job_request(JobCreate(name=reopened.name,
        model_id=reopened.model_id, mode=reopened.mode, params=reopened.params))
    assert normalized.params['boltzgen_min_plddt'] is None
    assert normalized.params['boltzgen_skip_inverse_folding'] is False
    assert normalized.params['boltzgen_noise_scale'] == 0
    discovery = await models.get_generation_settings('boltzgen', mode)
    assert discovery['parameters'] == parameter_contract(mode)
    assert requested == original


@pytest.mark.asyncio
async def test_initial_boltzgen_saved_replay_uses_snapshot_after_source_removed(admission, target):
    first = await jobs._create_job(JobCreate(name='native-original', model_id='boltzgen', mode='protein_binder',
        params={'target_pdb': 'inputs/target.pdb', 'alpha': 0, 'skip_inverse_folding': False}),
        BackgroundTasks(), admission)
    parent = await admission.get(Job, first.id)
    original = deepcopy(parent.params)
    first_root = Path(original['boltzgen_yaml_config'])
    expected = {path.relative_to(first_root).as_posix(): path.read_bytes()
                for path in first_root.rglob('*') if path.is_file()}
    target.unlink()
    replay = jobs._public_job_params(parent)
    replay.pop('remote_result_policy', None)
    replay.pop('core_protein_scientific_contract', None)  # Server reassigns its own producer contract.
    second = await jobs._create_job(JobCreate(name='native-replay', model_id='boltzgen',
        mode='protein_binder', params=replay), BackgroundTasks(), admission)
    child = await admission.get(Job, second.id)
    child_root = Path(child.params['boltzgen_yaml_config'])
    assert child_root != first_root
    assert {path.relative_to(child_root).as_posix(): path.read_bytes()
            for path in child_root.rglob('*') if path.is_file()} == expected
    assert parent.params == original
    assert child.params['boltzgen_alpha'] == 0
    assert child.params['boltzgen_skip_inverse_folding'] is False
    assert child.params['boltzgen_min_plddt'] is None
