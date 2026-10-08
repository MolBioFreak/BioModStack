"""Offline remote-parent tests. Model mocks are wiring evidence, not live science."""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from services.frustrampnn.contracts import canonical_json_bytes
from services.frustrampnn.identity import deterministic_candidate_id
from services.frustrampnn.settings import FrustraMPNNRequestedSettings, requested_settings_sha256
from services.frustrampnn.persistence import load_and_validate_result_bundle
from test_structure_prediction_frustrampnn_v2_transport import (
    _prepare_module, _selected_settings, _settings_transport_bytes, _two_model_pdb,
)
from test_frustrampnn_component_phase3 import _mock_v2_runtime

ROOT = Path(__file__).resolve().parents[3]
remote = importlib.import_module('scripts.remote_frustrampnn_batch')
publisher = importlib.import_module('scripts.publish_remote_frustrampnn_bundle')
component = importlib.import_module('scripts.run_frustrampnn_component')


def prepared(root, index, *, enabled=True, size=2, workflow='structure_prediction'):
    root.mkdir(parents=True)
    source = root / 'model.pdb'
    source.write_bytes(_two_model_pdb())
    preparer = _prepare_module()
    identity = dict(producer_method='protenix', producer_sample=f'sample-{index}',
                    producer_rank=index, producer_output_key=f'protenix/sample-{index}/model.pdb')
    metadata = dict(parent_job_id='remote-parent', parent_workflow_id=workflow,
                    producer_stage=f'{workflow}:protenix',
                    producer_candidate_key=f'frustrampnn/sources/protenix/sample-{index}.normalized.pdb',
                    requiredness='required', **identity,
                    producer_identity_sha256=preparer.producer_identity_sha256(identity),
                    producer_artifact_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), source_format='pdb')
    settings = _selected_settings().model_dump(mode='json', exclude_none=False)
    settings.update(batching_enabled=enabled, structures_per_job=size)
    settings = FrustraMPNNRequestedSettings.model_validate(settings)
    decoded = preparer._decode_metadata(base64.b64encode(canonical_json_bytes(metadata)).decode(), source=source, request_version=3)
    preparer.prepare_candidate(source=source, output_pdb=root / remote.FILES[1],
        request_path=root / remote.FILES[0], metadata=decoded, request_version=3,
        structure_map_path=root / remote.FILES[2], settings_payload=_settings_transport_bytes(settings),
        settings_sha256=requested_settings_sha256(settings), settings_value_origin='operator_request')
    request, _ = remote.read_candidate(root)
    assert request['candidate_id'] == deterministic_candidate_id(**{k: metadata[k] for k in ('parent_job_id', 'parent_workflow_id', 'producer_stage', 'producer_candidate_key')})
    assert request['requested_settings'] == settings.model_dump(mode='json', exclude_none=False)
    return request


@pytest.mark.parametrize('workflow', ['structure_prediction', 'complex_prediction'])
def test_remote_batch_preserves_v3_provenance_settings_and_cardinality(tmp_path, workflow):
    dirs = [tmp_path / str(i) for i in range(2)]
    requests = [prepared(path, i, workflow=workflow) for i, path in enumerate(dirs)]
    manifest, batch = remote.materialize_batch(list(reversed(dirs)), tmp_path / 'authority')
    assert batch['expected_cardinality'] == 2
    assert batch['settings_sha256'] == requests[0]['requested_settings_sha256']
    assert [r['candidate_id'] for r in batch['records']] == [r['candidate_id'] for r in sorted(requests, key=lambda r: (r['source_artifact']['relative_path'], r['candidate_id']))]
    assert remote.grouped._read_batch(manifest) == batch
    work = tmp_path / 'work'
    work.mkdir()
    for record in batch['records']:
        candidate = remote.prepare_record(record, authority_root=manifest.parent.parent, work_root=work)
        result = json.loads(candidate.request_path.read_bytes())
        assert result['requested_settings'] == requests[0]['requested_settings']
        assert result['source_artifact']['relative_path'].endswith('.normalized.pdb')
        assert result['source_artifact']['artifact_id'] == result['candidate_id']
    source = manifest.parent.parent / batch['records'][0]['source_relative_path']
    source.write_bytes(source.read_bytes() + b'REMARK tamper\n')
    with pytest.raises(ValueError, match='byte binding'):
        remote.prepare_record(batch['records'][0], authority_root=manifest.parent.parent, work_root=work)


@pytest.mark.parametrize('enabled,size,count', [(False, 2, 2), (True, 1, 2), (True, 2, 1), (True, 2, 3)])
def test_remote_grouped_path_rejects_settings_cardinality_mismatch(tmp_path, enabled, size, count):
    dirs = [tmp_path / str(i) for i in range(count)]
    for i, path in enumerate(dirs):
        prepared(path, i, enabled=enabled, size=size)
    if enabled and count <= size:
        # A final singleton is a valid native group, not a short-batch error.
        _, batch = remote.materialize_batch(dirs, tmp_path / 'authority')
        assert batch['expected_cardinality'] == count
    else:
        with pytest.raises(ValueError, match='cardinality'):
            remote.materialize_batch(dirs, tmp_path / 'authority')


def test_runtime_child_preparation_retains_canonical_native_envelope(tmp_path, monkeypatch):
    from scripts.run_frustrampnn_parent_fanout import prepare_runtime_child
    source_dir = tmp_path / 'source'
    request = prepared(source_dir, 0)
    context = tmp_path / 'context.json'
    context.write_text(json.dumps(dict(ledger_path=str(tmp_path / 'ledger.sqlite'),
        artifact_root=str(tmp_path), attempt_id='attempt', root_job_id='parent',
        target_id='target', lease_id='lease')))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(context))
    source = source_dir / 'model.pdb'
    settings = dict(request['requested_settings'])
    origin = settings.pop('settings_value_origin')
    payload = dict(parent_job_id='parent', params=dict(frustrampnn_component_group=dict(
        settings=settings, settings_value_origin=origin, candidates=[dict(
            source_relative_path=source.relative_to(tmp_path).as_posix(),
            source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            source_size_bytes=source.stat().st_size,
            metadata=dict(candidate_id=request['candidate_id'], parent_job_id='parent',
                parent_workflow_id='protein_design', producer_stage='terminal',
                producer_candidate_key='terminal/model.pdb', requiredness='required'))])))
    output = tmp_path / 'child'
    params = prepare_runtime_child(payload, child_id='child', output_root=output)
    assert prepare_runtime_child(payload, child_id='child', output_root=output) == params
    manifest = Path(params['frustrampnn_batch_manifest_path'])
    assert manifest == output / 'inputs/frustrampnn_scheduler_batch_v3.json'
    envelope = params['_frustrampnn_child_v1']
    assert envelope['normalized_requested_settings'] == request['requested_settings']
    assert envelope['settings_sha256'] == request['requested_settings_sha256']
    assert envelope['batch_manifest_sha256'] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    from services.nextflow import build_nextflow_command
    command = build_nextflow_command('frustrampnn', 'analyze', {**params, 'gpu_id': 0}, str(output), job_id='child')
    assert command[command.index('--frustrampnn_batch_manifest_path') + 1] == str(manifest)
    source.write_bytes(source.read_bytes() + b'REMARK changed after submission\n')
    with pytest.raises(ValueError, match='source binding changed'):
        prepare_runtime_child(payload, child_id='child', output_root=output)
    for use_runtime in (False, True):
        root = tmp_path / str(use_runtime)
        root.mkdir()
        with monkeypatch.context() as scoped:
            _assert_parent_fanout_submission_reuses_grouping_source_snapshot(root, scoped, use_runtime)


def _assert_parent_fanout_submission_reuses_grouping_source_snapshot(tmp_path, monkeypatch, use_runtime):
    from types import SimpleNamespace
    from scripts import run_frustrampnn_parent_fanout as client
    from lib import component_adapter

    directory = tmp_path / 'candidate'
    directory.mkdir()
    source = directory / 'source.pdb'
    raw = _two_model_pdb()
    source.write_bytes(raw)
    metadata = dict(candidate_id='candidate-1', parent_job_id='parent',
        parent_workflow_id='protein_design', producer_stage='terminal',
        producer_candidate_key='terminal/model.pdb', requiredness='required')
    (directory / 'metadata.json').write_bytes(client._canonical_bytes(metadata))
    settings = dict(batching_enabled=False, structures_per_job=1)
    original_plan = client.plan_frustrampnn
    planned = []

    def plan(records, requested):
        planned.extend(records)
        result = original_plan(records, requested)
        source.write_bytes(raw + b'REMARK changed after grouping snapshot\n')
        return result

    class SubmissionObserved(Exception):
        pass

    def submit(payload, **kwargs):
        member = payload['params']['frustrampnn_component_group']['candidates'][0]
        assert member['metadata'] == metadata
        assert member['source_relative_path'] == 'candidate/source.pdb'
        assert member['source_sha256'] == planned[0]['input_sha256'] == hashlib.sha256(raw).hexdigest()
        assert member['source_size_bytes'] == len(raw)
        raise SubmissionObserved

    def post(*args, **kwargs):
        assert kwargs['files'] == [('structure_files', ('source.pdb', raw, 'chemical/x-pdb'))]
        raise SubmissionObserved

    monkeypatch.setattr(client, 'component_runtime_enabled', lambda: use_runtime)
    monkeypatch.setattr(component_adapter, 'runtime_from_environment', lambda: SimpleNamespace(artifact_root=tmp_path))
    monkeypatch.setattr(client, 'plan_frustrampnn', plan)
    monkeypatch.setattr(client, 'submit_child_job', submit)
    monkeypatch.setattr(client.requests, 'post', post)
    with pytest.raises(SubmissionObserved):
        client.execute_parent_fanout(parent_job_id='parent', parent_workflow_id='protein_design',
            settings_json=client._canonical_bytes(settings).decode(), candidate_dirs=[directory],
            output_receipt=tmp_path / 'terminal.json', output_bundles=tmp_path / 'bundles',
            capability='test-capability')


def test_remote_batch_rejects_duplicate_identity(tmp_path):
    directory = tmp_path / 'candidate'
    prepared(directory, 0)
    with pytest.raises(ValueError, match='duplicate'):
        remote.materialize_batch([directory, directory], tmp_path / 'authority')


def test_remote_canonical_mock_bundle_reopens_after_explicit_copy(tmp_path, monkeypatch):
    """A mocked model exercises real canonical preparation, finalization and publication."""
    directory = tmp_path / 'candidate'
    request = prepared(directory, 0, enabled=False, size=1)
    _mock_v2_runtime(component, monkeypatch, tmp_path)
    bundle = tmp_path / 'candidate_bundle'
    component.run_component(request=request, request_payload=canonical_json_bytes(request),
        source_structure=directory / remote.FILES[1], structure_map=directory / remote.FILES[2],
        output_dir=bundle, container=tmp_path / 'mock.sif', physical_gpu_id=3)
    worker = tmp_path / 'worker' / 'remote-parent'
    worker.mkdir(parents=True)
    monkeypatch.setenv('BMS_REMOTE_EXECUTION', '1')
    marker = publisher.publish_remote(source_bundle=bundle, allowed_root=worker,
        destination=worker / 'frustrampnn/results' / request['candidate_id'], marker=tmp_path / 'published.json')
    assert marker['source'] == request['source_artifact']['relative_path']
    assert (worker / marker['source']).read_bytes() == (directory / remote.FILES[1]).read_bytes()
    # This explicit fixture copy represents an authorized pull, never automatic download.
    pulled = tmp_path / 'pulled' / 'remote-parent'
    shutil.copytree(worker, pulled)
    terminal = json.loads((pulled / marker['result']).read_bytes())
    accepted = load_and_validate_result_bundle((pulled / marker['manifest']).parent,
        expected_parent_job_id='remote-parent', terminal_envelope=terminal)
    assert accepted.contract_version == 3
    assert terminal['candidate_id'] == request['candidate_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('workflow', ['structure_prediction', 'complex_prediction'])
async def test_pulled_receipts_ingest_native_frustra_rows_and_designs(tmp_path, monkeypatch, workflow):
    """Mock inference only; real publication, receipt application and native DB ingestion."""
    from types import SimpleNamespace
    import uuid
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from database import Base, Job, Design, FrustraMPNNResult
    from services.remote_stage_receipts import write_remote_stage_receipt, apply_remote_stage_receipts
    from services.result_ingester import _ingest_explicit_frustrampnn_results

    candidate = tmp_path / 'candidate'
    request = prepared(candidate, 0, enabled=False, size=25, workflow=workflow)
    _mock_v2_runtime(component, monkeypatch, tmp_path)
    bundle = tmp_path / 'bundle'
    component.run_component(request=request, request_payload=canonical_json_bytes(request),
        source_structure=candidate / remote.FILES[1], structure_map=candidate / remote.FILES[2],
        output_dir=bundle, container=tmp_path / 'mock.sif', physical_gpu_id=3)
    worker = tmp_path / 'worker'
    worker.mkdir()
    attempt = str(uuid.uuid4())
    for key, value in {'BMS_REMOTE_EXECUTION': '1', 'BMS_REMOTE_ATTEMPT_ID': attempt,
                       'BMS_REMOTE_JOB_ID': 'remote-parent', 'BMS_REMOTE_OUTPUT_ROOT': str(worker)}.items():
        monkeypatch.setenv(key, value)
    marker = publisher.publish_remote(source_bundle=bundle, allowed_root=worker,
        destination=worker / 'frustrampnn/results' / request['candidate_id'], marker=tmp_path / 'marker.json')
    write_remote_stage_receipt(job_id='remote-parent', stage='frustrampnn', status='complete',
        outputs=[marker['result'], marker['manifest']], job_root_relative=True)
    pulled = tmp_path / 'pulled'
    shutil.copytree(worker, pulled)  # Authorized transfer fixture, not auto-download.
    manifest = SimpleNamespace(job_id='remote-parent', attempt_id=attempt, exit_code=0, artifacts=[
        SimpleNamespace(relative_path=p.relative_to(pulled).as_posix(), size_bytes=p.stat().st_size,
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(), link_target=None)
        for p in pulled.rglob('*') if p.is_file()])
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "native.sqlite"}')
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            job = Job(id='remote-parent', name='parent', model_id=workflow, mode='predict',
                params={}, status='running', queue_status='running', remote_state='returning',
                execution_target_id='target', remote_attempt_id=attempt,
                nextflow_run_id=f'remote:{attempt}', output_dir=str(pulled))
            session.add(job)
            await session.flush()
            await apply_remote_stage_receipts(session=session, job=job, attempt_id=attempt,
                output_root=pulled, manifest=manifest)
            assert await _ingest_explicit_frustrampnn_results(job, pulled, session, commit=False) == 1
            await session.commit()
            terminal = json.loads((pulled / marker['result']).read_bytes())
            native = await session.get(FrustraMPNNResult, ('remote-parent', terminal['invocation_id']))
            assert native is not None
            design = await session.get(Design, request['candidate_id'])
            assert design.source_stage_family == workflow
            assert Path(design.pdb_path).read_bytes() == (candidate / remote.FILES[1]).read_bytes()
            assert await _ingest_explicit_frustrampnn_results(job, pulled, session, commit=False) == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize('workflow,prepare_name', [
    ('structure_prediction', 'PrepareStructurePredictionFrustraMPNNCandidate'),
    ('complex_prediction', 'MaterializeComplexPredictionFrustraMPNNCandidate'),
])
def test_selected_parent_remote_branch_has_no_child_scheduler(workflow, prepare_name):
    text = (ROOT / 'workflows' / f'{workflow}.nf').read_text()
    remote_branch = text.split("if (System.getenv('BMS_REMOTE_EXECUTION') == '1') {", 1)[1].split('} else {', 1)[0]
    assert prepare_name in remote_branch
    assert 'RemoteCanonicalFrustraMPNN' in remote_branch
    assert 'SchedulerFrustraMPNNParentFanout' not in remote_branch
    assert 'SchedulerFrustraMPNNParentFanout(' in text.split(remote_branch, 1)[1]
    for name in ('remote_frustrampnn_batch.py', 'publish_remote_frustrampnn_bundle.py'):
        source = (ROOT / 'scripts' / name).read_text()
        assert 'requests.' not in source and 'urllib' not in source
        assert '/api/' not in source


@pytest.mark.parametrize('enabled,size,count,expected_groups', [
    (False, 25, 5, [1, 1, 1, 1, 1]), (True, 2, 3, [1, 2]),
    (True, 1, 3, [1, 1, 1]), (True, 3, 3, [3]),
])
@pytest.mark.runtime_integration
def test_offline_nextflow_remote_dag_routes_exact_groups(tmp_path, enabled, size, count, expected_groups):
    """Real Nextflow DAG, fake scientific helper commands; never a model acceptance test."""
    image = 'nextflow/nextflow:25.10.1'
    if not shutil.which('docker') or subprocess.run(['docker', 'image', 'inspect', image], capture_output=True).returncode:
        pytest.skip('pinned offline Nextflow image unavailable')
    (tmp_path / 'out').mkdir()
    tuples = []
    for i in range(count):
        directory = tmp_path / f'input{i}'
        directory.mkdir()
        request = dict(candidate_id=f'candidate-{i}', parent_job_id='remote-parent', parent_workflow_id='structure_prediction',
                       requested_settings=dict(batching_enabled=enabled, structures_per_job=size),
                       requiredness='required', source_artifact={'relative_path': f'producer/{count-i:03d}.pdb'})
        for name, content in zip(remote.FILES, (canonical_json_bytes(request).decode(), 'HEADER MOCK\n', '{}'), strict=True):
            (directory / name).write_text(content)
        tuples.append("tuple(" + ','.join(f"file('/run/input{i}/{name}')" for name in remote.FILES) + ')')
    shim = tmp_path / 'mock-python'
    shim.write_text('''#!/usr/bin/python3
import sys, pathlib, json
args=sys.argv[1:]
def value(flag): return args[args.index(flag)+1]
def emit(root, request):
    root.mkdir(parents=True)
    (root/'workflow_component_result_v3.json').write_text(json.dumps({'candidate_id':request['candidate_id'],'status':'succeeded'}))
    (root/'frustrampnn_result_manifest_v3.json').write_text('{}')
if args[0].endswith('plan_frustrampnn_groups.py'):
    import runpy
    sys.argv = args
    runpy.run_path(args[0], run_name='__main__')
    sys.exit(0)
if args[0].endswith('run_frustrampnn_component.py'):
    requests=[json.loads(pathlib.Path(value('--request')).read_text())]
    emit(pathlib.Path('candidate_bundle'), requests[0])
elif args[0].endswith('remote_frustrampnn_batch.py'):
    dirs=[pathlib.Path(args[i+1]) for i,a in enumerate(args) if a=='--candidate-dir']
    requests=[json.loads((d/'workflow_component_request_v3.json').read_text()) for d in dirs]
    for request in requests: emit(pathlib.Path('grouped_results')/request['candidate_id'],request)
    pathlib.Path('batch_receipts/mock').mkdir(parents=True)
    pathlib.Path('batch_receipts/mock/receipt.json').write_text('{}')
else: raise RuntimeError(args)
pathlib.Path('/run/out/call-'+requests[0]['candidate_id']+'.json').write_text(json.dumps([r['candidate_id'] for r in requests]))
''')
    shim.chmod(0o755)
    (tmp_path / 'main.nf').write_text("nextflow.enable.dsl=2\ninclude { RemoteCanonicalFrustraMPNN } from '/repo/modules/frustrampnn_remote.nf'\nworkflow { RemoteCanonicalFrustraMPNN(Channel.of(" + ','.join(tuples) + "))\nRemoteCanonicalFrustraMPNN.out.result.view { 'RESULT:'+it[0].candidate_id }\n}\n")
    (tmp_path / 'nextflow.config').write_text("""params.code_root='/repo'
params.api_python='/run/mock-python'
params.out_dir='/run/out'
params.container_dir='/mock-containers'
params.frustrampnn_physical_gpu_id=0
process.executor='local'
docker.enabled=false
singularity.enabled=false
""")
    completed = subprocess.run(['docker','run','--rm','--network','none','-e','NXF_OFFLINE=true',
        '-e','NXF_DISABLE_CHECK_LATEST=true','-e','BMS_REMOTE_EXECUTION=1',
        '-e','BMS_REMOTE_ATTEMPT_ID=offline-fixture',
        '-v',f'{ROOT}:/repo:ro','-v',f'{tmp_path}:/run:rw','-w','/run',image,
        'nextflow','run','main.nf','-offline','-w','/run/work'], capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = [json.loads(path.read_text()) for path in (tmp_path / 'out').glob('call-*.json')]
    assert sorted(map(len, calls)) == expected_groups
    from component_runtime import partition_ordered
    expected = partition_ordered(list(reversed([f'candidate-{i}' for i in range(count)])),
        batching_enabled=enabled, structures_per_job=size)
    assert sorted(calls) == sorted([list(group) for group in expected])
    assert sorted(candidate for call in calls for candidate in call) == [f'candidate-{i}' for i in range(count)]
