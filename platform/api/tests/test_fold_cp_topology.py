"""Fold-CP topology transport; scratch SQL and inert native CLI, no inference."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, Request, Response
from sqlalchemy import update

from database import Job, ExecutionTarget
from model_registry import ModelRegistry
from schemas import JobCreate
from routers.jobs import ResumeJobRequest
from routers import jobs
from services import gpu_orchestrator as scheduler
from services.nextflow import build_nextflow_command, _derive_boltz_cp_gpu_launch_settings
from test_core_protein_scientific_admission import admission
from test_multiworker_scheduling import workers

ROOT = Path(__file__).resolve().parents[3]


def params(size=3, topology='1d'):
    return dict(sequence='ACDEFGHIK', size_cp=size, cp_topology=topology,
                pinned_gpus=list(range(size)), use_msa=False, boltz_use_msa=False, run_frustrampnn=False)


@pytest.mark.parametrize('size', [3, 8])
@pytest.mark.parametrize('alias', [False, True])
def test_admission_and_compiler_non_square_boundary(size, alias, tmp_path):
    values = params(size)
    if alias:
        values['bcp_cp_topology'] = values.pop('cp_topology')
        values['bcp_size_cp'] = values.pop('size_cp')
    normalized = jobs.normalize_job_request(JobCreate(name='topology', model_id='boltz_cp_experimental',
                                                     mode='design', params=values))
    assert normalized.params['cp_topology'] == '1d'
    validated = jobs._normalize_boltz_cp_params_for_validation('boltz_cp_experimental', normalized.params)
    assert validated['size_cp'] == size
    assert ModelRegistry().validate_job_params('boltz_cp_experimental', 'design', validated) == []
    command = build_nextflow_command('boltz_cp_experimental', 'design', normalized.params, str(tmp_path))
    assert command[command.index('--bcp_cp_topology') + 1] == '1d'
    assert command[command.index('--bcp_size_cp') + 1] == str(size)
    assert '--cp_topology' not in command


@pytest.mark.parametrize('size,count', [(3, 4), (8, 6), (0, 3), (-1, 3), (17, 17), (3.5, 3), (True, 3)])
def test_explicit_1d_values_never_round(size, count, tmp_path):
    with pytest.raises(ValueError, match='cannot be reduced automatically'):
        _derive_boltz_cp_gpu_launch_settings(pinned_gpus=list(range(count)), requested_size_cp=size, cp_topology='1d')
    from fastapi import HTTPException
    values = params()
    values.update(size_cp=size, pinned_gpus=list(range(count)))
    with pytest.raises(HTTPException) as error:
        jobs._normalize_boltz_cp_params_for_validation('boltz_cp_experimental', values)
    assert error.value.status_code == 422
    with pytest.raises(ValueError, match='cannot be reduced automatically'):
        build_nextflow_command('boltz_cp_experimental', 'design', values, str(tmp_path))


@pytest.mark.parametrize('count,expected', [(0, 1), (2, 1), (3, 1), (4, 4), (8, 4), (9, 9), (16, 16)])
def test_omitted_and_explicit_2d_are_identical(count, expected):
    kwargs = dict(pinned_gpus=list(range(count)), requested_size_cp=None)
    omitted = _derive_boltz_cp_gpu_launch_settings(**kwargs)
    assert omitted == _derive_boltz_cp_gpu_launch_settings(**kwargs, cp_topology='2d')
    assert omitted[1] == expected
    if count in (3, 8):
        with pytest.raises(ValueError, match='square CP size'):
            _derive_boltz_cp_gpu_launch_settings(pinned_gpus=list(range(count)), requested_size_cp=count)
        assert _derive_boltz_cp_gpu_launch_settings(**kwargs, cp_topology='1d')[1] == count


@pytest.mark.parametrize('topology', ['3d', '1D', '', None])
def test_topology_is_a_closed_enum(topology, tmp_path):
    from fastapi import HTTPException
    values = params(3, topology)
    with pytest.raises(HTTPException) as error:
        jobs._normalize_boltz_cp_params_for_validation('boltz_cp_experimental', values)
    assert error.value.status_code == 422
    with pytest.raises(ValueError, match='cp_topology must be one of'):
        build_nextflow_command('boltz_cp_experimental', 'design', values, str(tmp_path))


@pytest.mark.asyncio
async def test_discovery_contract():
    from template_registry import TemplateRegistry
    model = ModelRegistry().get_model('boltz_cp_experimental')
    field = next(p for p in model.params if p.name == 'cp_topology')
    assert field.default == '2d' and field.enum == ['2d', '1d']
    assert 'cp_topology' in next(m for m in model.modes if m.id == 'design').params
    from routers import models
    document = await models.get_model('boltz_cp_experimental')
    field = next(p for p in document['params'] if p['name'] == 'cp_topology')
    assert field['default'] == '2d' and field['enum'] == ['2d', '1d']
    template = TemplateRegistry(ROOT / 'platform/api/config/templates').get_template('structure_prediction')
    field = next(p for p in template.user_params if p.name == 'bcp_cp_topology')
    assert field.default == '2d' and field.enum == ['2d', '1d']


@pytest.mark.asyncio
@pytest.mark.parametrize('topology,size', [(None, 4), ('1d', 3), ('1d', 8)])
async def test_saved_clone_and_actual_retry_keep_effective_topology(admission, monkeypatch, topology, size):
    values = params(size, topology)
    values['msa_cache_only'] = True  # Offline persistence fixture, no provider work.
    if topology is None:
        values.pop('cp_topology')
    request = JobCreate(name='saved-topology', model_id='boltz_cp_experimental', mode='design', params=values)
    created = await jobs._create_job(request, BackgroundTasks(), admission)
    admission.expire_all()
    saved = await admission.get(Job, created.id)
    assert saved.params['cp_topology'] == (topology or '2d')
    clone_params = dict(saved.params)
    clone_params.pop('remote_result_policy', None)
    clone_params.pop('core_protein_scientific_contract', None)
    clone = JobCreate.model_validate_json(JobCreate(name='clone', model_id=saved.model_id,
                                      mode=saved.mode, params=clone_params).model_dump_json())
    cloned = await jobs._create_job(clone, BackgroundTasks(), admission)
    assert (await admission.get(Job, cloned.id)).params['cp_topology'] == (topology or '2d')
    saved.status = 'failed'
    await admission.commit()
    monkeypatch.setattr(jobs, '_raise_if_workflow_launches_disabled', lambda *args: None)
    resumed = await jobs.resume_job(saved.id, Request({'type': 'http', 'headers': []}), Response(),
                                   request=ResumeJobRequest(), background_tasks=BackgroundTasks(), session=admission)
    admission.expire_all()
    retry = await admission.get(Job, resumed['new_job_id'])
    assert retry.params['cp_topology'] == (topology or '2d')
    assert retry.params['size_cp'] == size


@pytest.mark.parametrize('size', [3, 8])
def test_native_component_projection_retains_topology_and_count(size, tmp_path):
    from services.nextflow import compile_nextflow_invocation
    inv = compile_nextflow_invocation('boltz_cp_experimental', 'design', params(size), str(tmp_path / 'out'))
    native = inv.native_parameters
    assert native['bcp_cp_topology'] == '1d' and native['bcp_size_cp'] == size
    component = next(c for c in inv.execution_plan.metadata.static_components
                     if c.component_key == 'RunBoltzCPExperimental')
    resources = json.loads(component.resources_json)
    assert resources['gpu']['count'] == size
    assert resources['gpu']['cp_topology'] == '1d'


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [3, 8])
async def test_real_remote_scheduler_claims_all_1d_devices(workers, monkeypatch, size):
    from services.remote_execution import targets
    async with workers() as session:
        await session.execute(update(Job).values(paused=True))
        job = await session.get(Job, 'job-2')
        job.paused = False
        job.model_id, job.mode, job.params, job.vram_estimate_mb = 'boltz_cp_experimental', 'design', params(size), 6000
        target = await session.get(ExecutionTarget, 'vast:2')
        target.capabilities = dict(gpu_count=size)
        await session.commit()
    monkeypatch.setattr(scheduler, 'read_scheduler_config', lambda: {'global': {'enabled': True}})
    async def telemetry(target):
        return dict(available=True, observed_at='fixture', gpus=[dict(index=i, uuid=f'GPU-{i}',
                    memory_total_mb=16384, memory_used_mb=0) for i in range(size)])
    monkeypatch.setattr(targets, 'remote_target_telemetry', telemetry)
    launched = []
    async def launch(**kwargs):
        launched.append(kwargs)
    await scheduler.GPUOrchestrator(workers, lambda: [], launch)._process_cycle()
    assert len(launched) == 1
    assert launched[0]['params']['cp_topology'] == '1d'
    async with workers() as session:
        job = await session.get(Job, 'job-2')
        assert job.provenance['remote_execution_assignment']['gpu_indices'] == list(range(size))


@pytest.mark.parametrize('size', [3, 8])
@pytest.mark.asyncio
async def test_real_local_scheduler_retains_1d_selection(workers, monkeypatch, size):
    from types import SimpleNamespace
    async with workers() as session:
        await session.execute(update(Job).values(paused=True))
        job = await session.get(Job, 'job-2')
        job.paused, job.execution_target_id = False, None
        job.model_id, job.mode, job.params, job.vram_estimate_mb = 'boltz_cp_experimental', 'design', params(size), 6000
        await session.commit()
    monkeypatch.setattr(scheduler, 'read_scheduler_config', lambda: {'global': {'enabled': True}})
    stats = [SimpleNamespace(index=i, name='inert GPU', memory_used_mb=0, memory_total_mb=16384,
                             utilization=0, temperature=30, processes=[]) for i in range(size)]
    launched = []
    async def launch(**kwargs):
        launched.append(kwargs)
    await scheduler.GPUOrchestrator(workers, lambda: stats, launch)._process_cycle()
    assert len(launched) == 1
    assert launched[0]['params']['cp_topology'] == '1d'
    assert launched[0]['params']['pinned_gpus'] == list(range(size))


@pytest.mark.runtime_integration
@pytest.mark.parametrize('size,topology,count,error', [
    (3, '1d', 3, None), (8, '1d', 8, None), (4, '2d', 4, None), (4, None, 4, None),
    (3, '2d', 3, 'bcp_size_cp must be a perfect square'),
    (3, '1d', 4, 'bcp_size_cp must divide'), (8, '1d', 6, 'bcp_size_cp must divide'),
    (0, '1d', 4, 'bcp_size_cp must be a positive integer up to 16'),
    (3.5, '1d', 4, 'bcp_size_cp must be a positive integer up to 16'),
    (17, '1d', 17, 'bcp_size_cp must be a positive integer up to 16'),
])
def test_real_nextflow_module_emits_native_cli(tmp_path, size, topology, count, error):
    """Actual Groovy interpolation and shell; only torch launcher is inert."""
    native = tmp_path / 'native'
    native.mkdir()
    cli = native / 'torch' / 'distributed'
    cli.mkdir(parents=True)
    (native / 'torch' / '__init__.py').write_text('')
    (cli / '__init__.py').write_text('')
    (cli / 'run.py').write_text("import json,os,sys\nfrom pathlib import Path\nroot=Path(sys.argv[sys.argv.index('--out_dir')+1])\n(root/'native-argv.json').write_text(json.dumps(sys.argv[1:]))\n(root/'boltz_results_fixture').mkdir()\n")
    input_yaml = tmp_path / 'input.yaml'
    input_yaml.write_text('version: 1\nsequences: []\n')
    config = tmp_path / 'fixture.config'
    config.write_text('process.executor="local"\nprocess.container=null\napptainer.enabled=false\nsingularity.enabled=false\n')
    workflow = ROOT / 'workflows/boltz_cp_experimental.nf'
    env = dict(os.environ, NXF_VER='25.10.1', NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true',
               NXF_HOME=str(tmp_path / 'nxf-home'),
               NXF_DIST=os.environ.get('BMS_TEST_NEXTFLOW_DIST', str(Path.home() / '.nextflow/framework')),
               PYTHONPATH=str(native))
    topology_args = ['--bcp_cp_topology', topology] if topology is not None else []
    result = subprocess.run(['/usr/local/bin/nextflow', '-C', str(config), 'run', str(workflow), '-offline',
                            '-w', str(tmp_path / 'work'), '--out_dir', str(tmp_path / 'out'),
                            '--bcp_repo_path', str(native), '--bcp_input_path', str(input_yaml),
                            *topology_args, '--bcp_size_cp', str(size),
                            '--run_frustrampnn', 'false',
                            '--bcp_gpu_ids', ','.join(map(str, range(count)))], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=120)
    if error is None:
        assert result.returncode == 0, result.stdout + result.stderr
        argv_files = list((tmp_path / 'work').rglob('native-argv.json'))
        assert len(argv_files) == 1
        argv = json.loads(argv_files[0].read_text())
        assert argv[argv.index('--cp_topology') + 1] == (topology or '2d')
        assert argv[argv.index('--size_cp') + 1] == str(size)
        assert argv[argv.index('--size_dp') + 1] == '1'
    else:
        assert result.returncode != 0
        assert error in result.stdout + result.stderr
