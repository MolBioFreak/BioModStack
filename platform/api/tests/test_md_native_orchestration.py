"""Native orchestration receiving with scratch stores and inert engine bytes."""
import copy
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from component_runtime import SourceIdentity, canonical_bytes
from services import nextflow
from services.md import launch_contract, results, completion
from services.md.starting_structures import MdNativeLaunchIntent, compile_native_job_intent
from scripts.bms_md.contract import build_run_manifest, prepare_verified_worker_inputs
from scripts.bms_md.native_config import lane_config
from scripts.bms_md.spawn_replicas import spawn_replicas
from scripts.bms_md.spawn_analysis import spawn_analysis, prepare_analysis_retry, QUALIFIED_RUNTIME_SHA256
from scripts.bms_md.aggregate_children import collect_children
from scripts.bms_md.collect_analysis import collect_analysis
from scripts.lib.component_adapter import runtime_from_environment, native_resource_config
from scripts.lib import portable_inputs
from services.remote_execution import bundle
from services.remote_execution.targets import selected_plan_target_resources
from test_remote_bundle_path_gaps import roots
from test_md_native_contract import _intent, _files, native_files, native_client
from test_md_results_trim import store, _tree, _seed


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))
    return path


def _native(roots, monkeypatch):
    import model_registry
    monkeypatch.setattr(model_registry, 'molecular_dynamics_feature_enabled', lambda: True)
    monkeypatch.setattr(launch_contract, '_bound_engine_runtime_identity',
                        lambda engine: {'image_name': 'gromacs.sif', 'sif_sha256': 'a'*64})
    source = _files(roots['inputs'] / 'system')
    second = source / 'second.gro'
    second.write_bytes(b'inert second window start\n')
    intent = _intent(source)
    intent.update(replicas=2, windows=[
        {'id': 'near', 'mdp': {'adsorption': {'pull-coord1-init': 0, 'gen-seed': 71}}},
        {'id': 'far', 'coordinates': str(second), 'mdp': {'adsorption': {'pull-coord1-init': 2.5, 'pull-coord2-k': 450, 'gen-seed': 73}}},
    ])
    spec = compile_native_job_intent(MdNativeLaunchIntent.model_validate(intent))
    identity = launch_contract.native_input_identity(spec)
    out = roots['results'] / 'parent'
    params = launch_contract.materialize_md_job_spec(params={'md_job_spec': spec,
        'md_source_provenance': {'native_input_identity': identity}}, job_id='parent',
        output_dir=out, resolve_runtime_path=lambda p: p)
    return out, params


def _context(out, params, monkeypatch):
    invocation = nextflow.compile_nextflow_invocation('molecular_dynamics', 'simulate', params,
        str(out), job_id='parent', source_identity=SourceIdentity('a'*40, 'b'*40),
        execution_context=nextflow.NativeCompilerExecutionContext(gpu_id=0, gpu_ids=(0,), anarcii_execution_mode='cpu'))
    invocation.materialize_inputs(out)
    assert invocation.execution_plan.complete
    assert 'scripts/bms_md/native_config.py' in {d.relative_path for d in invocation.execution_plan.dependencies}
    resources = selected_plan_target_resources(SimpleNamespace(id='local'), invocation.execution_plan,
                                              gpu_ids=[0], scratch_bytes=0)
    resources.update(gpu_id=0, admission_required=False)
    job = SimpleNamespace(id='parent', root_job_id=None, model_id='molecular_dynamics', mode='simulate',
        params=params, provenance={}, status='running', assigned_gpu=0, execution_target_id=None, child_output_dir=None)
    path = out / 'context.json'
    context = nextflow.component_launch_context(invocation, job, command=invocation.command,
        context_path=path, artifact_root=out, working_directory=out, attempt_id='fixture-attempt',
        target_id='local', lease_id='fixture-lease', resources=resources)
    _write(path, context)
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(path))
    return invocation, context, runtime_from_environment(path)


def test_window_compiler_child_collector_and_result_receiving(roots, monkeypatch):
    out, params = _native(roots, monkeypatch)
    original = copy.deepcopy(params['md_job_spec'])
    invocation, context, runtime = _context(out, params, monkeypatch)
    config = Path(invocation.native_parameters['md_job_config'])
    metadata = _write(out/'metadata.json', {'replicas': 2, 'engine': 'gromacs'})
    preparation = out/'bundle'; preparation.mkdir()
    receipt = spawn_replicas(parent_job_id='parent', parent_name='Windows', normalized_config=config,
        metadata_path=metadata, preparation_bundle=preparation, api_url='http://unavailable.invalid')
    assert receipt['replica_count'] == 4
    assert [(r['window_id'], r['replicate_index']) for r in receipt['children']] == [('near', 0), ('near', 1), ('far', 0), ('far', 1)]
    assert params['md_job_spec'] == original
    assert json.loads(config.read_text())['windows'] == original['windows']
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    runtime.claim_root(owner_id='fixture', boot_id=boot)
    outputs = []
    for row in receipt['children']:
        child = row['id']; index = row['replica_index']
        request = runtime.request(child)
        cfg = json.loads(Path(request.payload['params']['md_job_config']).read_text())
        assert cfg == lane_config(json.loads(config.read_text()), index)
        assert cfg['stages'][0]['mdp']['gen-seed'] == (71 if index < 2 else 73)
        assert 'windows' not in cfg and cfg['replicas'] == 4
        child_out = out/'components'/child.replace(':', '-'); child_out.mkdir(parents=True)
        child_context = dict(context, child_id=child, child_output_dir=str(child_out),
                             child_working_directory=str(out/'work'/str(index)))
        compiled = nextflow.compile_component_nextflow_invocation(request, child_context)
        compiled.materialize_inputs(child_out)
        rendered = native_resource_config(compiled.execution_plan.to_dict(), context['resources'],
                                         str(out/'compute.lock'), nextflow.PROJECT_ROOT)
        assert 'cpus = 8' in rendered and 'flock -x 198' in rendered
        worker = prepare_verified_worker_inputs(Path(request.payload['params']['md_job_config']), child_out/'private')
        assert results._replica_protocol_matches(original, worker, replica_index=index)
        wrong = copy.deepcopy(worker); wrong['stages'][0]['mdp']['gen-seed'] = 999
        assert not results._replica_protocol_matches(original, wrong, replica_index=index)
        # External engine boundary only: no native science is invoked here.
        replica_dir = child_out/f'replica_{index}'; replica_dir.mkdir()
        final = replica_dir/'final.gro'; final.write_bytes(b'inert native final coordinates\n')
        manifest = build_run_manifest(output_dir=replica_dir, job_config=worker, replica_index=index,
            engine_version='inert-boundary', platform='CPU', artifacts={'final': final}, stages={'adsorption': {}})
        manifest.update(status='completed', orchestration_seed=original['random_seed'])
        manifest['artifacts']['final']['semantic_role'] = 'representative_structure'
        _write(replica_dir/'manifest.json', manifest)
        runtime.claim(child, owner_id='fixture', boot_id=boot)
        runtime.execution_finished(child, owner_id='fixture', boot_id=boot, output_dir=str(child_out), exit_code=0)
        outputs.append(str(child_out))
    receipt_path = _write(out/'spawn.json', receipt)
    status = _write(out/'status.json', dict(total=4, completed=0, execution_finished=4, failed=0, cancelled=0,
        child_ids=[r['id'] for r in receipt['children']], child_output_dirs=outputs))
    aggregate = collect_children(status, out, spawn_receipt=receipt_path)
    assert aggregate['status'] == 'completed'
    assert [r['window_id'] for r in aggregate['replicas']] == ['near', 'near', 'far', 'far']
    assert all(r['status'] == 'completed' for r in runtime.children('parent', 'md_replica'))
    monkeypatch.setenv('BMS_MD_RESULT_ROOT', str(roots['results']))
    job = SimpleNamespace(id='parent', model_id='molecular_dynamics', output_dir=str(out), child_output_dir=None,
                          params=params, provenance={})
    summary = results.summary(job)
    assert [(r['window_id'], r['replicate_index']) for r in summary['replicas']] == [('near', 0), ('near', 1), ('far', 0), ('far', 1)]
    assert summary['trajectory_playback']['supported'] is False
    # Failed optional analyses remain ordinary components, join without failing
    # successful dynamics, and remain present in explicit retry rosters.
    analysis = spawn_analysis(parent_job_id='parent', parent_name='Windows', aggregate_manifest=out/'manifest.json',
        api_url='http://unavailable.invalid', work_item_dir=out/'analysis_work', runtime_sha256=QUALIFIED_RUNTIME_SHA256)
    ids = [r['id'] for r in analysis['children']]
    for child in ids:
        assert runtime.request(child).required is False
        runtime.claim(child, owner_id='fixture', boot_id=boot)
        runtime.fail(child, owner_id='fixture', boot_id=boot, quiescent=True, reason='inert analysis exit',
                     failure_receipt={'code': 'execution_failed', 'source': 'worker'})
    astatus = _write(out/'analysis_status.json', dict(total=4, completed=0, failed=4, cancelled=0,
                        child_ids=ids, child_output_dirs=[]))
    collection = collect_analysis(astatus, out/'manifest.json', out, spawn_receipt=_write(out/'analysis_spawn.json', analysis))
    assert collection['optional'] is True and collection['status'] == 'partial_failure'
    runtime.join_children([r['job_id'] for r in runtime.children()])
    replacement, retry = prepare_analysis_retry(runtime, component_id=ids[0], operation_id='retry', failure_code='execution_failed')
    assert replacement.required is False and retry['analysis_count'] == 4
    from routers.md_results import _retained_analysis_roster
    roster = _retained_analysis_roster({'component_context_path': str(out/'context.json')}, 'parent', 'local', out)
    assert set(roster) == set(range(4))
    assert all(not row['required'] for row in roster.values())


def test_windows_portable_after_original_inputs_removed(roots, monkeypatch):
    out, params = _native(roots, monkeypatch)
    invocation, _, _ = _context(out, params, monkeypatch)
    _, effective = bundle.compile_remote_dependencies('molecular_dynamics', 'simulate', list(invocation.command), native_invocation=invocation)
    assets = bundle._input_assets(effective, native_invocation=invocation,
        repo_root=nextflow.PROJECT_ROOT, runtime_paths=set(), output_dir=out)
    refs = portable_inputs.discover_native_input_references('molecular_dynamics', 'simulate',
        invocation.native_parameters, invocation.generated_inputs, output_dir=out, allowed_roots=list(roots.values()))
    assert any(r['selector'] == ['windows', 1, 'coordinates'] for r in refs)
    assert all(any(Path(r['source_path']) == path or Path(r['source_path']).is_relative_to(path) for path, _ in assets) for r in refs)
    worker = roots['data']/'worker'; worker.mkdir()
    transferred, bindings = {}, []
    for ref in refs:
        source = ref['source_path']
        if source not in transferred:
            target = worker/f'{len(transferred)}-{Path(source).name}'
            shutil.copyfile(source, target); transferred[source] = target
        bindings.append({'reference': ref, 'path': str(transferred[source])})
    binding = _write(worker/'bindings.json', {'schema': portable_inputs.SCHEMA, 'roots': [str(worker)], 'bindings': bindings})
    monkeypatch.setenv(portable_inputs.ENV, str(binding))
    archived = portable_inputs.resolve_input_path(invocation.native_parameters['md_job_config'])
    before = archived.read_bytes()
    shutil.rmtree(roots['inputs']); shutil.rmtree(out)
    received = prepare_verified_worker_inputs(archived, worker/'private')
    assert Path(received['windows'][1]['coordinates']).read_bytes() == b'inert second window start\n'
    assert archived.read_bytes() == before
    assert lane_config(received, 3)['input']['coordinates'] == received['windows'][1]['coordinates']


@pytest.mark.asyncio
async def test_optional_analysis_failure_real_mapped_finalizer(store, tmp_path, monkeypatch):
    from database import Job, MdRun
    from services.md.lifecycle import _publish_barrier
    _, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    # Existing supported legacy document with explicit optional analysis: no
    # engine schema overlay and no counterfeit v3 native acceptance.
    spec['analysis'] = {'selection': None}
    manifest_path = root/'replicas/replica_0/manifest.json'
    manifest = json.loads(manifest_path.read_text()); manifest['config'] = spec
    _write(manifest_path, manifest)
    shutil.rmtree(root/'analysis')
    (root/'md_completion_barrier.json').unlink()
    status = _write(root/'failure.json', dict(total=1, completed=0, failed=1, cancelled=0,
        child_ids=['analysis-child-0'], child_output_dirs=[]))
    collection = collect_analysis(status, root/'manifest.json', root)
    assert collection['optional'] and collection['failed_analysis_children'] == 1
    _publish_barrier(root, 'md-job-1')
    await _seed(maker, root, spec)
    async with maker() as session:
        child = await session.get(Job, 'analysis-child-0')
        child.status = child.queue_status = 'failed'; child.error_message = 'inert external boundary failure'
        await session.commit()
    async with maker() as session:
        job = await session.get(Job, 'md-job-1')
        snapshot = await completion.validate_and_finalize_md_job(job, session)
        assert snapshot['dynamics_state'] == 'completed'
        await session.commit()
    async with maker() as session:
        assert (await session.get(Job, 'md-job-1')).status == 'completed'
        assert (await session.get(MdRun, 'md-job-1')).phase == 'completed'
        assert (await session.get(Job, 'analysis-child-0')).status == 'failed'


@pytest.mark.parametrize('windows', [None, []])
def test_nonwindow_document_and_native_seed_are_unchanged(windows):
    config = {'schema': 'bms.md.job.v3', 'replicas': 2, 'stages': [{'name': 'production', 'mdp': {'gen-seed': -1}}]}
    if windows is not None:
        config['windows'] = windows
    assert lane_config(config, 1) == config


def test_optional_analysis_executes_real_nextflow_barrier_body(tmp_path, monkeypatch):
    """Execute the real embedded Python; this is not a Nextflow runtime claim."""
    workflow = (Path(__file__).resolve().parents[3] / 'workflows/experimental/molecular_dynamics/orchestrator.nf').read_text()
    aggregate = _write(tmp_path/'manifest.json', {'job_id': 'parent', 'status': 'completed', 'replicas': [{'replica_index': 0}]})
    analysis = _write(tmp_path/'analysis.json', {'job_id': 'parent', 'status': 'partial_failure', 'optional': True,
        'aggregate_manifest_sha256': hashlib.sha256(aggregate.read_bytes()).hexdigest(),
        'completed_analysis_children': 0, 'required_analysis_children': 1})
    monkeypatch.chdir(tmp_path)
    for process in ('MD_ASSERT_ANALYSIS_OUTCOME', 'MD_COMPLETION_BARRIER'):
        body = workflow.split('process ' + process + ' {', 1)[1].split("python3 - <<'PY'", 1)[1].split('\nPY', 1)[0]
        body = body.replace('${aggregate_manifest}', str(aggregate)).replace('${analysis_manifest}', str(analysis))
        exec(compile(body, process, 'exec'), {})
    barrier = json.loads((tmp_path/'md_completion_barrier.json').read_text())
    assert barrier['status'] == 'completed'
    assert barrier['aggregate_manifest_sha256'] == hashlib.sha256(aggregate.read_bytes()).hexdigest()
    _write(aggregate, {'job_id': 'parent', 'status': 'partial_failure'})
    with pytest.raises(SystemExit, match='before durable collection'):
        exec(compile(body, 'MD_COMPLETION_BARRIER', 'exec'), {})


def test_native_checkpoint_receipt_and_reader_use_authored_final_stage(tmp_path, monkeypatch):
    from scripts.bms_md.checkpoint_receipt import write_checkpoint_receipt
    from scripts.bms_md.native_config import compatibility_key
    from services.md.pause_actuator import _checkpoint_receipt
    config = {'schema': 'bms.md.job.v3', 'engine': 'gromacs', 'random_seed': 99,
        'stages': [{'name': 'warmup', 'mdp': {'gen-seed': 71}}, {'name': 'sample', 'mdp': {'dt': 0.002}}],
        'input': {}, 'execution': {}}
    source = _write(tmp_path/'config.json', config)
    checkpoint = tmp_path/'sample/sample.cpt'; checkpoint.parent.mkdir()
    checkpoint.write_bytes(b'inert checkpoint for receiving only')
    # The external native dump is inert, while receipt identity, byte hashing
    # and API path/endpoint receiving are actual production code.
    import scripts.bms_md.checkpoint_receipt as receipts
    monkeypatch.setattr(receipts.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout='step = 10\nt = 7.01\n'))
    path = write_checkpoint_receipt(config_path=source, output_dir=tmp_path)
    value = json.loads(path.read_text())
    assert value['checkpoint_path'] == 'sample/sample.cpt'
    assert value['compatibility_key'] == compatibility_key(config)
    assert value['execution_plan_sha256'] == hashlib.sha256(canonical_bytes(config)).hexdigest()
    received, actual, relative = _checkpoint_receipt([tmp_path])
    assert received == value and actual == checkpoint and relative == 'sample/sample.cpt'
    assert (received['step'], received['time_ps']) == (10, 7.01)


@pytest.mark.asyncio
@pytest.mark.parametrize('compiled', [False, True])
async def test_v3_endpoint_ingestion_uses_native_observation_not_requested_duration(store, tmp_path, monkeypatch, compiled):
    from database import Job, MdAttemptSegment
    _, maker = store
    root, spec = _tree(tmp_path, monkeypatch)
    spec.update(schema='bms.md.job.v3', stages=[] if compiled else [{'name': 'sample', 'mdp': {'dt': 9, 'nsteps': 999999}}])
    path = root/'replicas/replica_0/manifest.json'
    manifest = json.loads(path.read_text())
    stage = 'production' if compiled else 'sample'
    manifest.update(config=spec, job_schema='bms.md.job.v3', final_stage=stage,
        native_endpoints={stage: {'stage': stage, 'step': 10, 'time_ps': 7.01,
                                 'native_tpr': {'dt': '0.001', 'tinit': '7', 'init-step': '4', 'nsteps': '6'}}})
    _write(path, manifest)
    _, segment_id = await _seed(maker, root, spec)
    async with maker() as session:
        job = await session.get(Job, 'md-job-1')
        # Separate receiving test: full v3 completion also requires the
        # engine-owned run schema to be integrated; do not stub that validator.
        inventory = results._load_inventory(job, include_analysis=False)
        await completion._ingest_durable_artifacts(job, session, _inventory=inventory)
        await session.commit()
    async with maker() as session:
        segment = await session.get(MdAttemptSegment, segment_id)
        assert (segment.end_step, segment.end_time_ps) == (10, 7.01)


def test_wham_uses_selected_native_container_and_preserves_native_failure(tmp_path, monkeypatch):
    from scripts.bms_md.collect_analysis import run_native_wham
    import subprocess
    import lib.container_runtime as containers
    root = tmp_path/'replica'; root.mkdir()
    artifacts = {}
    for name, role in [('production.tpr', 'production_tpr'), ('pullx.xvg', 'pull_coordinates')]:
        path = root/name; path.write_bytes(b'inert native input')
        artifacts[role] = {'path': name, 'bytes': path.stat().st_size,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'semantic_role': role}
    manifest = _write(root/'manifest.json', {'artifacts': artifacts})
    commands = []
    monkeypatch.setattr(containers, 'container_executable', lambda: '/selected/container-runtime')
    def fail(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=17, stdout='inert native WHAM failure')
    monkeypatch.setattr(subprocess, 'run', fail)
    request = {'unit': 'nm', 'temperature_k': 300, 'windows': [{'replica': 0, 'window': 'w0', 'coordinate': 1, 'coordinate_count': 1}]}
    result = run_native_wham({0: manifest}, request, tmp_path/'wham', gromacs_container='/selected/gromacs.sif')
    assert commands[0][:3] == ['/selected/container-runtime', 'exec', '--bind']
    assert commands[0][4:7] == ['/selected/gromacs.sif', 'gmx', 'wham']
    assert result['status'] == 'failed' and result['returncode'] == 17
    assert 'inert native WHAM failure' in result['error']['message']
    assert (tmp_path/'wham/wham.log').read_text() == 'inert native WHAM failure'


def test_typed_preview_binds_window_bytes_and_managed_path_authority(native_client, native_files):
    intent = _intent(native_files)
    intent['windows'] = [{'id': 'w0', 'coordinates': str(native_files/'reference.gro'),
                          'mdp': {'adsorption': {'pull-coord1-init': 0, 'pull-coord2-k': 450}}}]
    def preview():
        return native_client.post('/api/molecular-dynamics/launch-preview', json={
            'schema_version': 'bms.md.launch-preview-request.v1', 'intent': intent})
    first = preview()
    assert first.status_code == 200, first.text
    assert first.json()['effective_request']['windows'] == intent['windows']
    (native_files/'reference.gro').write_bytes(b'changed window coordinates')
    second = preview()
    assert second.status_code == 200, second.text
    assert second.json()['preview_digest'] != first.json()['preview_digest']
    intent['windows'][0]['coordinates'] = '/etc/hosts'
    assert preview().status_code == 403


def test_wham_retry_selects_existing_gromacs_runtime(roots, monkeypatch):
    out, params = _native(roots, monkeypatch)
    params['md_job_spec']['analysis'] = {'wham': {'unit': 'nm', 'temperature_k': 300,
        'windows': [{'replica': 0, 'window': 'near', 'coordinate': 1, 'coordinate_count': 2}]}}
    params['md_analysis_retry_spawn_receipt'] = str(out/'retained-analysis-retry.json')
    invocation, _, _ = _context(out, params, monkeypatch)
    assert any(d.relative_path == 'gromacs-md-2025.3.sif' and d.selector == 'md_gromacs_container'
               for d in invocation.execution_plan.dependencies)
    processes = {c.component_key for c in invocation.execution_plan.metadata.static_components}
    assert 'MD_COLLECT_ANALYSIS' in processes and 'MD_GROMACS_REPLICA' not in processes
