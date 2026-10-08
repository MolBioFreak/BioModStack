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
    assert [r['candidate_id'] for r in batch['records']] == sorted(r['candidate_id'] for r in requests)
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
    with pytest.raises(ValueError, match='cardinality'):
        remote.materialize_batch(dirs, tmp_path / 'authority')


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
                       requested_settings=dict(batching_enabled=enabled, structures_per_job=size))
        for name, content in zip(remote.FILES, (json.dumps(request), 'HEADER MOCK\n', '{}'), strict=True):
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
        '-v',f'{ROOT}:/repo:ro','-v',f'{tmp_path}:/run:rw','-w','/run',image,
        'nextflow','run','main.nf','-offline','-w','/run/work'], capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    calls = [json.loads(path.read_text()) for path in (tmp_path / 'out').glob('call-*.json')]
    assert sorted(map(len, calls)) == expected_groups
    assert sorted(candidate for call in calls for candidate in call) == [f'candidate-{i}' for i in range(count)]
