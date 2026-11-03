"""Inert failing Nextflow task -> terminal observer -> cold Results/Project.

Only the container/scientific executable is replaced. The module, publisher,
filesystem collection, SQL registration and HTTP reader are production code.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from database import Job, Design, get_session
from test_remote_lifecycle_gaps import store  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]


def failing_task(tmp_path, *, population, selected=False, success=False):
    jar = Path(os.environ['BMS_TEST_NEXTFLOW_JAR'])
    run = tmp_path / 'task'
    run.mkdir()
    closure = run / 'closure'
    closure.mkdir()
    (closure / 'request.json').write_text('{}')
    (closure / 'bindings.json').write_text('{}')
    bins = run / "bin's directory"
    bins.mkdir()
    script = '#!' + sys.executable + '\n' + f'''
import json, pathlib, sys
sys.path.insert(0, {str(ROOT / 'scripts')!r})
from bms_rfd4.publication import NativeObserver, publish
root = pathlib.Path('rfd4_proteina').resolve()
root.mkdir()
observer = NativeObserver()
try:
    if {population!r} == 'partial':
        path = root / 'native/exact.cif'
        path.parent.mkdir()
        path.write_text('data_fixture_only\\n# Inert transport document, not generated science\\n')
        observer.events.append(dict(event='native_writer', example_id='original',
            path=str(path), population='completed_rollout'))
    if not {success!r}:
        raise RuntimeError('deliberate inert native failure')
finally:
    receipt = dict(execution_status={'completed' if success else 'failed'!r}, fixture_only=True)
    (root / 'receipt.json').write_text(json.dumps(receipt))
    publish(root, dict(mode='generate'), receipt, observer)
'''
    # Both executables exist. The default sentinel detects ignored worker selection.
    default = bins / 'apptainer'
    default.write_text('#!/bin/sh\nexit 84\n' if selected else script)
    default.chmod(0o755)
    executable = bins / 'selected launcher'
    executable.write_text(script)
    executable.chmod(0o755)
    driver = run / 'driver.nf'
    driver.write_text("include { RunRFD4Proteina } from '" + str(ROOT / 'modules/rfd4_proteina.nf') + "'\n"
                      "workflow { RunRFD4Proteina(file(params.closure)) }\n")
    config = run / 'offline.config'
    config.write_text("process.executor='local'\nprocess.cpus=1\nprocess.memory='256 MB'\n"
                      "process.shell=['/bin/bash','-euo','pipefail']\nexecutor.queueSize=1\n")
    output = run / "output's retained root"
    params = dict(closure=str(closure), out_dir=str(output), cache_root='', code_root=str(ROOT),
                  weights_root=str(run / 'weights'), container_dir=str(run / 'images'),
                  gpu_id=0, rfd4_proteina_bundle=str(run / 'bundle'))
    parameter_file = run / 'params.json'
    parameter_file.write_text(json.dumps(params))
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('NXF_', 'BMS_SELECTED_IMAGE_', 'BMS_RFD4_', 'BMS_CONTAINER_'))
           and key not in ('JAVA_TOOL_OPTIONS', 'JDK_JAVA_OPTIONS', '_JAVA_OPTIONS')}
    env.update(PATH=str(bins) + os.pathsep + os.environ['PATH'], NXF_OFFLINE='true',
               NXF_DISABLE_CHECK_LATEST='true')
    for key in ('HOME', 'NXF_HOME', 'NXF_TEMP', 'TMPDIR', 'BMS_DATA', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME'):
        env[key] = str(run / key.lower())
        Path(env[key]).mkdir()
    if selected:
        env['BMS_CONTAINER_EXECUTABLE'] = str(executable)
    command = [shutil.which('java'), '--add-opens=java.base/java.lang=ALL-UNNAMED',
               '--add-opens=java.base/java.util=ALL-UNNAMED', '-jar', str(jar),
               '-C', str(config), 'run', str(driver), '-params-file', str(parameter_file),
               '-work-dir', str(run / 'work'), '-ansi-log', 'false']
    (run / 'command.json').write_text(json.dumps(command))
    completed = subprocess.run(command, cwd=run, env=env, capture_output=True, text=True, timeout=75)
    (run / 'nextflow.stdout.log').write_text(completed.stdout + completed.stderr)
    evidence = os.environ.get('BMS_PUBLICATION_EVIDENCE')
    if evidence:
        shutil.copytree(run, Path(evidence) / tmp_path.name, dirs_exist_ok=True)
    assert (completed.returncode == 0) == success, completed.stdout + completed.stderr
    tasks = list((run / 'work').rglob('.exitcode'))
    assert len(tasks) == 1
    assert tasks[0].read_text().strip() == ('0' if success else '1')
    publication = output / 'collected/rfd4_proteina/manifest.json'
    assert publication.is_file(), 'failed task did not collect its native publication'
    assert json.loads(publication.read_text())['execution_status'] == ('completed' if success else 'failed')
    shutil.rmtree(run / 'work')  # cold reader cannot recover from Nextflow scratch
    return output


@pytest.mark.asyncio
@pytest.mark.parametrize('population,status,selected', [
    ('partial', 'failed', False), ('zero', 'failed', False),
    ('partial', 'cancelled', True), ('zero', 'cancelled', True),
])
async def test_failing_task_to_cold_reader(store, tmp_path, monkeypatch, population, status, selected):
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = failing_task(tmp_path, population=population, selected=selected)
    from services.nextflow import _observe_local_terminal_binder_results
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id = 'rfd4_proteina'
        job.mode = 'generate'
        job.status = job.queue_status = status
        job.execution_target_id = job.remote_attempt_id = job.remote_state = None
        job.output_dir = str(output)
        job.error_message = 'original terminal failure'
        await session.commit()
        await _observe_local_terminal_binder_results(session, job.id, str(output))
        await session.commit()
    from routers import jobs
    app = FastAPI()
    app.include_router(jobs.router, prefix='/jobs')
    async def cold_session():
        async with store() as session:
            yield session
    app.dependency_overrides[get_session] = cold_session
    async with AsyncClient(transport=ASGITransport(app), base_url='http://fixture') as client:
        response = await client.get('/jobs/job/generation-results')
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['execution_status'] == status
        assert page['native_execution_status'] == 'failed'
        assert page['total'] == (1 if population == 'partial' else 0)
        assert page['artifacts']
    from services.global_experiments.adapters import NativeBinderJobResultAdapter
    async with store() as session:
        job = await session.get(Job, 'job')
        assert job.status == job.queue_status == status
        assert job.error_message == 'original terminal failure'
        assert not list(await session.scalars(select(Design)))
        adapter = NativeBinderJobResultAdapter(job.model_id)
        result = await adapter.verify(session, job.id)
        assert result['metadata']['job_status'] == status
        assert [row.entity_id for row in await adapter.search(session, query='job', limit=10)] == ['job']
        # Replay is registration-only and must not duplicate custody or change state.
        await _observe_local_terminal_binder_results(session, job.id, str(output))
        await session.commit()


def test_selected_launcher_success(tmp_path):
    failing_task(tmp_path, population='zero', selected=True, success=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['rfd4_proteina', 'complexa', 'laproteina', 'disco'])
@pytest.mark.parametrize('parent', [False, True])
async def test_shared_observer_uses_native_owner(store, tmp_path, monkeypatch, model, parent):
    # Separate owner-dispatch regression; process collection is covered above.
    from test_denovo_canonical_return import native_tree
    from services.binder_result_observation import observe_retained_binder_results
    from services.rfd4_proteina_results import read_published_generation_page
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = tmp_path / 'native-output'
    native_tree(output, model, 'zero', compressed=False)
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id = 'protein_modification_experimental' if parent else model
        job.mode = 'de_novo_design' if parent else 'generate'
        job.params = {'generator': model} if parent else {}
        job.status = job.queue_status = 'failed'
        job.execution_target_id = job.remote_attempt_id = job.remote_state = None
        job.output_dir = str(output)
        result = await observe_retained_binder_results(session, job, output, commit=True)
        assert result['available']
    async with store() as session:
        job = await session.get(Job, 'job')
        assert (await read_published_generation_page(job, session))['model_id'] == model
        assert job.status == 'failed'
        assert not list(await session.scalars(select(Design)))


def test_duplicate_condition_examples_preserve_every_input(tmp_path, monkeypatch):
    # Replace scientific parsing only, leaving real preparation, IDs and file writes.
    # The fake array carries unique per-input payloads and original identity.
    import numpy as np
    calls = []
    class Array:
        def __init__(self, spec, example_id):
            self.payload = {'source': Path(spec['input'][0]).read_text(), 'example_id': example_id}
        def get_annotation_categories(self):
            return ['chain_id', 'res_id']
        def get_annotation(self, name):
            return np.array(['A'] if name == 'chain_id' else [42])
    def build(spec, *, spec_dir, example_id, validate):
        calls.append(example_id)
        return {'atom_array': Array(spec, example_id)}
    modules = {
        'rfproteina': {}, 'rfproteina.conditions': {},
        'rfproteina.utils.parse_presets': {'ANNOTATED_CIF': {}},
        'rfproteina.utils.common': {'validate_design_id': lambda value: None},
        'rfproteina.inference_utils.condition_spec': {'build_annotated_atom_array_from_condition_spec': build},
        'atomworks.io': {'parse': lambda *a, **k: None},
        'atomworks.io.utils.io_utils': {'to_cif_file': lambda a, p, **kw: p.write_text(json.dumps(a.payload))},
        'atomworks.io.utils.standard_annotations': {'S_SEGMIN': None, 'S_SEGMAX': None},
        'atomworks.ml.utils.expandable_segment': {'insert_expandable_segment': None},
    }
    for name, members in modules.items():
        module = types.ModuleType(name)
        module.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, module)
    from bms_rfd4.inputs import prepare_inputs
    bindings = {}
    names = ['same', 'same', 'same_input_0000', 'unique']
    for index in range(len(names)):
        path = tmp_path / f'source{index}.cif'
        path.write_text(f'original input {index}')
        bindings[str(index)] = str(path)
    request = {'input': {'kind': 'condition_spec', 'examples': [
        {'name': name, 'sources': [{'slot': str(index)}]}
        for index, name in enumerate(names)]}}
    native, mapping = prepare_inputs(request, bindings, tmp_path / 'prepared')
    assert len(list(native.glob('*.cif'))) == len(names), 'an accepted example was overwritten'
    assert calls == names
    assert [row['example_id'] for row in mapping] == names
    assert len({row['input_id'] for row in mapping}) == len(names)
    assert [row['example_index'] for row in mapping] == list(range(len(names)))
    assert [row['input_id'] for row in mapping][2:] == names[2:]
    for index, row in enumerate(mapping):
        assert Path(row['native']).stem == row['input_id']
        assert json.loads(Path(row['native']).read_text()) == {
            'source': f'original input {index}', 'example_id': names[index]}
        assert row['native_atom_identity'] == {'chain_id': ['A'], 'res_id': [42]}
    assert json.loads((native.parent / 'input_identity.json').read_text()) == mapping


def test_prepared_input_identity_survives_failed_runner(tmp_path, monkeypatch):
    from bms_rfd4 import run as runner
    monkeypatch.setitem(sys.modules, 'rfproteina', types.ModuleType('rfproteina'))
    omega = types.ModuleType('omegaconf')
    omega.OmegaConf = object()
    monkeypatch.setitem(sys.modules, 'omegaconf', omega)
    monkeypatch.setattr(runner.importlib.metadata, 'version', lambda name: 'fixture-only')
    def fail(*args, **kwargs):
        raise RuntimeError('inert config failure')
    monkeypatch.setattr(runner, 'compose_native', fail)
    inputs = tmp_path / 'prepared/native'
    inputs.mkdir(parents=True)
    identity = [{'example_id': 'same', 'input_id': f'same_input_{i:04d}', 'example_index': i}
                for i in range(2)]
    (inputs.parent / 'input_identity.json').write_text(json.dumps(identity))
    request = {'input': {'kind': 'unconditional', 'name': 'fixture',
                        'chains': [{'chain_id': 'A', 'min_length': 1, 'max_length': 1}]}}
    with pytest.raises(RuntimeError, match='inert config failure'):
        runner._run(request, {}, tmp_path / 'output', prepared_inputs=inputs, config_dir=tmp_path)
    manifest = json.loads((tmp_path / 'output/manifest.json').read_text())
    assert manifest['receipt']['input_identity'] == identity
    assert manifest['execution_status'] == 'failed'
    assert manifest['records'] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unpaired,status', [(False, 'failed'), (True, 'cancelled')])
async def test_rfd3_existing_publication_owner_cold_read(store, tmp_path, monkeypatch, unpaired, status):
    from scripts.rfd3_generation import contract
    monkeypatch.setitem(sys.modules, 'contract', contract)
    from scripts.rfd3_generation.build_result_manifest import build_result_manifest
    from services.binder_result_observation import observe_retained_binder_results
    from services.rfd3_generation_results import read_generation_results
    monkeypatch.setenv('BMS_DATA', str(tmp_path))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path))
    output = tmp_path / 'rfd3-output'
    native = output / 'run/rfd3'
    native.mkdir(parents=True)
    request = {'schema': 'bms.rfd3.generation.request.v1', 'job_id': 'job', 'request_id': 'request',
               'generation': {'min_length': 1, 'max_length': 20, 'num_designs': 2},
               'execution': {'seed': 0, 'dump_trajectories': False}}
    request_path = tmp_path / 'request.json'
    request_path.write_text(json.dumps(request))
    metadata = native / 'unpaired.json'
    metadata.write_text('{}')
    manifest = build_result_manifest(request_path=request_path, structure_paths=[],
        metadata_paths=[metadata] if unpaired else [], accepted_candidate_ids=None,
        storage_prefix='run/rfd3', execution_status=status)
    path = output / 'collected/rfd3_generation/rfd3_generation_result_manifest.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))
    async with store() as session:
        job = await session.get(Job, 'job')
        job.model_id = 'protein_modification_experimental'
        job.mode = 'de_novo_design'
        job.params = {'generator': 'rfd3', 'rfd3_generation_request': request}
        job.status = job.queue_status = status
        job.execution_target_id = job.remote_attempt_id = job.remote_state = None
        job.output_dir = str(output)
        result = await observe_retained_binder_results(session, job, output, commit=True)
        assert result['available'] and result['native_record_count'] == 0
    async with store() as session:
        job = await session.get(Job, 'job')
        page = await read_generation_results(job, session, offset=0, limit=100)
        assert page['counts']['generated'] == 0
        assert page['execution_status'] == status
        assert len(page['population']['unpaired_artifacts']) == int(unpaired)
        assert job.status == job.queue_status == status
        assert not list(await session.scalars(select(Design)))
