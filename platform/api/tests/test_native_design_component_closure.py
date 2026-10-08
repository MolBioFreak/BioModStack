"""Offline native-fixture DAG evidence, never GPU/model acceptance."""
from __future__ import annotations
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from native_design_runtime_fixture import offline_worker_env
from services.frustrampnn.contracts import canonical_json_bytes
from test_remote_frustrampnn_self_contained import prepared, remote, component
from test_frustrampnn_component_phase3 import _mock_v2_runtime

ROOT = Path(__file__).resolve().parents[3]
native = importlib.import_module('scripts.native_frustrampnn_parent')


def fixture_candidates(tmp_path, monkeypatch, count=3, workflow='protein_design', enabled=True):
    _mock_v2_runtime(component, monkeypatch, tmp_path)
    candidates, bundles = [], []
    for i in range(count):
        candidate = tmp_path/f'candidate-{i}'
        request = prepared(candidate, i, enabled=enabled, size=2, workflow=workflow)
        bundle = tmp_path/'fixtures'/request['candidate_id']
        component.run_component(request=request, request_payload=canonical_json_bytes(request),
            source_structure=candidate/remote.FILES[1], structure_map=candidate/remote.FILES[2],
            output_dir=bundle, container=tmp_path/'mock.sif', physical_gpu_id=3)
        candidates.append(candidate)
        bundles.append(bundle)
    return candidates, bundles


@pytest.mark.parametrize('workflow', ['protein_design', 'structure_prediction', 'conformational_mapping'])
def test_native_exact_join_retains_science_and_reopens(tmp_path, monkeypatch, workflow):
    candidates, bundles = fixture_candidates(tmp_path, monkeypatch, workflow=workflow)
    output, publication = tmp_path/'joined', tmp_path/'published'
    publication.mkdir()
    receipt = native.seal(list(reversed(candidates)), list(reversed(bundles)), output, publication, 'attempt')
    from component_runtime import ordered_candidates
    expected = ordered_candidates([remote.read_candidate(p)[0] for p in candidates], lambda r: r)
    assert receipt['candidate_ids'] == [r['candidate_id'] for r in expected]
    assert [len(g) for g in receipt['grouping_plan']['groups']] == [2, 1]
    # Publication replay is idempotent and request, native bytes stay unchanged.
    assert native.seal(candidates, bundles, output, publication, 'attempt') == receipt
    from services.frustrampnn.persistence import load_and_validate_result_bundle
    for bundle in bundles:
        published = publication/'frustrampnn/results'/bundle.name
        terminal = json.loads((published/'workflow_component_result_v3.json').read_bytes())
        accepted = load_and_validate_result_bundle(published, expected_parent_job_id='remote-parent', terminal_envelope=terminal)
        assert accepted.contract_version == 3
    with pytest.raises(ValueError, match='exact join'):
        native.seal(candidates, bundles[:-1], tmp_path/'missing', publication, 'attempt')
    with pytest.raises(ValueError, match='duplicate'):
        native.seal(candidates, bundles+bundles[:1], tmp_path/'duplicate', publication, 'attempt')


@pytest.mark.runtime_integration
@pytest.mark.parametrize('placement', ['local', 'worker'])
@pytest.mark.parametrize('enabled', [False, True])
def test_real_offline_nextflow_native_parent_dag(tmp_path, monkeypatch, placement, enabled):
    """Run actual production DAG; only scientific inference is a native fixture.

    Preparation is separately tested above. Planner, task staging, grouped and
    singleton dispatch, exact join, manifest validation and publication run real
    source, using file fixtures with an unavailable host callback endpoint.
    """
    with monkeypatch.context() as model_patch:
        candidates, bundles = fixture_candidates(tmp_path, model_patch, enabled=enabled)
    python = Path(sys.executable)
    shim = tmp_path/'fixture-python'
    shim.write_text(f'''#!{python}
import sys, pathlib, json, shutil, runpy
args=sys.argv[1:]
if args[0].endswith('native_frustrampnn_parent.py') and args[1]=='run':
    dirs=[pathlib.Path(args[i+1]) for i,a in enumerate(args) if a=='--candidate']
    ids=[json.loads((d/'workflow_component_request_v3.json').read_text())['candidate_id'] for d in dirs]
    for cid in ids:
        shutil.copytree(pathlib.Path({str(tmp_path/'fixtures')!r})/cid, pathlib.Path('grouped_results')/cid)
    pathlib.Path('group_receipts').mkdir()
    pathlib.Path('group_receipts/native-fixture.json').write_text(json.dumps(ids))
    pathlib.Path({str(tmp_path)!r}, 'observed-'+ids[0]+'.json').write_text(json.dumps(ids))
else:
    sys.argv=args
    runpy.run_path(args[0], run_name='__main__')
''')
    shim.chmod(0o755)
    (tmp_path/'out').mkdir()
    workflow = tmp_path/'main.nf'
    paths = ','.join(f"file('{p}')" for p in reversed(candidates))
    workflow.write_text(f"""nextflow.enable.dsl=2
include {{ NativePreparedFrustraMPNNParent }} from '{ROOT}/modules/frustrampnn_native_parent.nf'
workflow {{ NativePreparedFrustraMPNNParent(Channel.of({paths})) }}
""")
    config = tmp_path/'nextflow.config'
    config.write_text(f"""params.code_root='{ROOT}'
params.job_id='remote-parent'
params.component_attempt_id='fixture-attempt'
params.api_python='{shim}'
params.out_dir='{tmp_path}/out'
params.container_dir='{tmp_path}'
params.frustrampnn_physical_gpu_id=3
process.executor='local'
process.shell=['/bin/bash','-euo','pipefail']
singularity.enabled=false
apptainer.enabled=false
docker.enabled=false
""")
    framework = Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    if not framework.is_file():
        pytest.skip('pinned offline Nextflow 25.10.1 runtime unavailable')
    target = tmp_path/'nxf/framework/25.10.1'/framework.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(framework, target)
    env = {**os.environ, 'NXF_HOME': str(tmp_path/'nxf'), 'NXF_VER': '25.10.1', 'NXF_OFFLINE': 'true', 'NXF_DISABLE_CHECK_LATEST': 'true',
        'API_BASE_URL': 'http://127.0.0.1:1', 'PYTHONDONTWRITEBYTECODE': '1'}
    for k in ('BMS_REMOTE_EXECUTION', 'BMS_STAGE_REPORT_TOKEN', 'DATABASE_URL'):
        env.pop(k, None)
    if placement == 'worker':
        env['BMS_REMOTE_EXECUTION'] = '1'
    env = offline_worker_env(tmp_path, env)
    completed = subprocess.run(['java', '--add-opens=java.base/java.util=ALL-UNNAMED', '-jar', str(target), '-C', str(config), 'run', str(workflow), '-offline', '-w', str(tmp_path/'work')], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stdout+completed.stderr
    receipt_path = tmp_path/'out/frustrampnn/component_runtime/terminal.json'
    assert receipt_path.is_file(), completed.stdout+completed.stderr
    receipt = json.loads(receipt_path.read_bytes())
    calls = [json.loads(p.read_bytes()) for p in tmp_path.glob('observed-*.json')]
    from component_runtime import partition_ordered
    ids = [remote.read_candidate(p)[0]['candidate_id'] for p in candidates]
    assert sorted(calls) == sorted(map(list, partition_ordered(ids, batching_enabled=enabled, structures_per_job=2)))
    assert receipt['candidate_ids'] == ids
    assert len(list((tmp_path/'out/frustrampnn/results').glob('*/workflow_component_result_v3.json'))) == 3
