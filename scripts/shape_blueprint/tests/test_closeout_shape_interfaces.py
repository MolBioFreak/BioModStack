"""Real inert Nextflow/process receiving. No inference, providers or live Jobs."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/shape_blueprint'))
sys.path.insert(0, str(ROOT / 'platform/api/tests'))
from test_one_copy_shape_followup import nextflow
from test_one_copy_shape_handoffs import document, descriptor, sha


@pytest.fixture(autouse=True)
def isolated_nextflow_home(tmp_path, monkeypatch):
    monkeypatch.setenv('NXF_HOME', str(tmp_path / 'nxf-home'))


def sealed(value):
    value = dict(value)
    value.pop('request_sha256', None)
    value['request_sha256'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value


@pytest.mark.parametrize('policy', ['auto', 'skip'])
def test_backbone_only_actual_process_to_database(tmp_path, monkeypatch, policy):
    import test_shape_result_ingestion as fixture
    from scripts.shape_blueprint.plan_rfd3_batches import plan_batches
    from scripts.shape_blueprint.build_rfd3_aggregate import build_aggregate
    from services.result_ingester import _ingest_shape_result_manifest
    from database import Design, Job
    from sqlalchemy import select
    request = fixture._request_spec()
    request.update(sequence_policy=policy, sequences_per_backbone=0)
    monkeypatch.setattr(fixture, '_request_spec', lambda: dict(request))
    req = sealed(request)
    document(tmp_path / 'request.json', req)
    plan = plan_batches(req, gpu_memory_gib=32)
    cid = plan['batches'][0]['candidates'][0]['candidate_id']
    aggregate = build_aggregate(plan=plan, admission_records=[dict(candidate_id=cid, status='accepted', reason=None)])
    document(tmp_path / 'aggregate.json', aggregate)
    bundle = tmp_path / 'candidate'
    bundle.mkdir()
    structure = bundle / 'structure.cif'
    structure.write_text('data_EXPLICITLY_INERT\n')
    source = bundle / 'source.pdb'
    source.write_text('REMARK EXPLICITLY_INERT\n')
    metrics = document(bundle / 'metrics.json', dict(schema='bms_shape_candidate_metrics_v1', candidate_id=cid,
        **{k: req[k] for k in ('geometry_sha256', 'point_pool_sha256', 'sdf_sha256')}, source_backbone_sha256=sha(source)))
    document(bundle / 'candidate_bundle.json', dict(schema='bms_shape_candidate_bundle_v1', candidate_id=cid,
        name=cid, status='accepted', structure=descriptor(structure), source_backbone=descriptor(source),
        metrics=descriptor(metrics), provenance={'predictor': 'rfd3'}))
    workflow = f"""include {{ PlanRFD3Batches; BuildShapeResult }} from '{ROOT}/modules/shape_blueprint'
workflow {{
 PlanRFD3Batches(file('{tmp_path}/request.json'))
 BuildShapeResult(Channel.of(file('{bundle}')).collect(), file('{tmp_path}/request.json'), file('{tmp_path}/aggregate.json'), 'inert-closeout')
}}
"""
    run = nextflow(tmp_path, workflow)
    run('--shape_gpu_memory_gib', '32')
    published = tmp_path / 'published'
    # Cached Nextflow publisher must still produce independent retained names.
    shutil.rmtree(published)
    assert {r['status'] for r in run('--shape_gpu_memory_gib', '32', '-resume')} == {'CACHED'}
    shutil.rmtree(tmp_path / 'work')
    shutil.rmtree(bundle)
    async def ingest():
        factory, engine = await fixture._session_factory(tmp_path)
        try:
            async with factory() as session:
                job = await fixture._register(session, 'inert-closeout', published)
                assert await _ingest_shape_result_manifest(job, published, session, commit=True) == 1
            async with factory() as session:
                design, = (await session.execute(select(Design))).scalars().all()
                assert design.producer_model_id == 'rfd3'
                assert Path(design.pdb_path).read_text() == 'data_EXPLICITLY_INERT\n'
                job = await session.get(Job, 'inert-closeout')
                assert await _ingest_shape_result_manifest(job, published, session, commit=True) == 0
        finally:
            await engine.dispose()
    asyncio.run(ingest())


@pytest.mark.parametrize('capacity', [None, 16, 48])
def test_planner_actual_process_observation_not_fake_capacity(tmp_path, capacity):
    from test_shape_result_ingestion import _request_spec
    document(tmp_path / 'request.json', sealed(_request_spec()))
    run = nextflow(tmp_path, f"include {{ PlanRFD3Batches }} from '{ROOT}/modules/shape_blueprint'\nworkflow {{ PlanRFD3Batches(file('{tmp_path}/request.json')) }}")
    run(*([] if capacity is None else ['--shape_gpu_memory_gib', str(capacity)]))
    receipt = json.loads((tmp_path / 'published/run/shape_batches/rfd3_batch_plan.json').read_text())['resource_admission']
    assert receipt['gpu_memory_gib'] == capacity
    assert receipt['status'] == ('unknown' if capacity is None else 'admitted')


def test_sequence_cli_resolves_relocated_owner_not_task_decoy(tmp_path):
    """Real CLI to native subprocess; only inference is an inert runner."""
    from test_settings_transport import backbone, request
    original = tmp_path / 'original'
    original.mkdir()
    settings = dict(mpnn_temperature=0.2, mpnn_omitAAs='', mpnn_backbone_noise=0,
                    mpnn_checkpoint_type='vanilla', mpnn_checkpoint_model='v_48_020',
                    mpnn_bias_AA_jsonl='native-input-0000.jsonl')
    document(original / 'request.json', request('proteinmpnn', settings))
    (original / 'native-input-0000.jsonl').write_text('{"A": 0.75}\n')
    retained = tmp_path / 'relocated/request'
    shutil.copytree(original, retained)
    shutil.rmtree(original)
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'request.json').symlink_to(retained / 'request.json')
    (task / 'native-input-0000.jsonl').write_text('WRONG TASK-RELATIVE DECOY')
    backbone(task / 'backbone.pdb')
    runner = tmp_path / 'inert_mpnn.py'
    runner.write_text('''import sys, json
from pathlib import Path
args = sys.argv
bias = Path(args[args.index('--bias_AA_jsonl') + 1])
assert json.loads(bias.read_text()) == {'A': 0.75}, bias
out = Path(args[args.index('--out_folder') + 1]); (out / 'seqs').mkdir()
(out / 'seqs/backbone.fa').write_text('>native\\nGGGGG\\n>T=0.2, sample=1, score=1, global_score=1, seq_recovery=1\\nAAAAA\\n')
''')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/shape_blueprint/run_shape_sequence.py'),
        '--engine', 'proteinmpnn', '--candidate-id', 'a' * 64, '--backbone', 'backbone.pdb',
        '--output-dir', 'result', '--receipt', 'result/receipt.json', '--count', '1', '--seed', '19',
        '--request', 'request.json', '--runner', str(runner)], cwd=task, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    rows = json.loads((task / 'result/sequence_records.json').read_text())
    assert rows['records'][0]['sequence'] == 'AAAAA'
    assert (retained / 'request.json').read_bytes() == (task / 'request.json').read_bytes()


def test_esm_actual_nextflow_stages_relocated_request_owner(tmp_path, monkeypatch):
    """Only model invocation is intercepted; native compile and file reader run."""
    original = tmp_path / 'original'
    original.mkdir()
    settings = {'esmf_use_msa': True, 'model_variant': 'full', 'msa_path': 'native-input-0000.a3m',
                'num_diffusion_samples': 1, 'num_loops': 2, 'num_sampling_steps': 7}
    document(original / 'request.json', {'schema': 'bms_shape_design_request_v3', 'validator_settings': {'esmfold2': settings}})
    (original / 'native-input-0000.a3m').write_text('>query\nAAAAA\n>hit\nAAA-A\n')
    retained = tmp_path / 'retained'
    shutil.copytree(original, retained)
    shutil.rmtree(original)
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    (hooks / 'sitecustomize.py').write_text('''import subprocess, json
from pathlib import Path
original_run = subprocess.run
def run(command, *args, **kwargs):
    if any('bms_gpu_run_telemetry.py' in str(v) for v in command):
        # Native compiler has resolved the exact MSA source into CLI args.
        paths = [Path(str(v)) for v in command if str(v).endswith('.a3m')]
        assert len(paths) == 1 and paths[0].read_text().startswith('>query\\nAAAAA'), command
        out = Path('esmfold2_results'); out.mkdir(exist_ok=True)
        (out / 'manifest.json').write_text(json.dumps({'samples': []}))
        (out / 'observed.a3m').write_bytes(paths[0].read_bytes())
        (out / 'inert.cif').write_text('data_INERT')
        (out / 'inert.metrics.json').write_text('{}')
        (out / 'runtime.telemetry.json').write_text('{}')
        (out / 'summary.tsv').write_text('INERT')
        return subprocess.CompletedProcess(command, 0)
    return original_run(command, *args, **kwargs)
subprocess.run = run
''')
    monkeypatch.setenv('PYTHONPATH', str(hooks) + ':' + os.environ.get('PYTHONPATH', ''))
    # Baseline runs its real old caller instead of failing on a missing symbol.
    if 'process ShapeESMFold2Predict' in (ROOT / 'modules/shape_blueprint.nf').read_text():
        workflow = f"include {{ ShapeESMFold2Predict }} from '{ROOT}/modules/shape_blueprint'\nworkflow {{ ShapeESMFold2Predict(Channel.of(tuple([:], 'AAAAA', 'inert')), file('{retained}')) }}"
    else:
        native = json.dumps(settings).replace('"', "'")
        # JSON parser avoids Groovy's map-literal syntax differences.
        workflow = f"include {{ ESMFold2Predict }} from '{ROOT}/modules/esmfold2_experimental'\nworkflow {{ ESMFold2Predict(Channel.of(tuple([shape_settings:new groovy.json.JsonSlurper().parseText('''{json.dumps(settings)}''')], 'AAAAA', 'inert'))) }}"
    run = nextflow(tmp_path, workflow)
    run('--modification_mode', 'shape_blueprint')
    output = tmp_path / 'published/final/esmfold2/inert/esmfold2_results/observed.a3m'
    assert output.read_text() == '>query\nAAAAA\n>hit\nAAA-A\n'
