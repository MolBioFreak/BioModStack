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
