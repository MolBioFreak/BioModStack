"""Route-free receiving controls; genuine engines, not ONT inference."""
import asyncio
import ast
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest
from test_ngs_clone_runtime_closure import released, native, ROOT


class Session:
    def __init__(self, jobs=()):
        self.jobs = {j.id: j for j in jobs}
    async def commit(self):
        pass
    async def get(self, model, key):
        return self.jobs.get(key)


def test_real_clone_local_selected_image_pinning(released, tmp_path, monkeypatch):
    bundle, installed, lock, params = released
    from services import nextflow
    monkeypatch.setenv('BMS_CONTAINER_DIR', str(installed))
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'image-store'))
    from scripts.lib.shared_runtime_images import publish_image
    import hashlib
    sif = Path(params['dorado_runtime_sif'])
    params['dorado_runtime_sif'] = str(publish_image(sif, tmp_path / 'image-store',
        hashlib.file_digest(sif.open('rb'), 'sha256').hexdigest()))
    invocation = nextflow.compile_nextflow_invocation('nanopore', 'clone_validation', params,
                                                     str(tmp_path / 'output'), job_id='local-pin')
    job = SimpleNamespace(id='local-pin', provenance={})
    # Reproduce the old weights-only classification with the actual native plan.
    text = subprocess.check_output(['git', 'show',
        'c7f22ebadb6ac11f35f33d0a4667158b9e4ae936:platform/api/services/nextflow.py'], cwd=ROOT, text=True)
    function = next(n for n in ast.parse(text).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == '_pin_local_invocation_images')
    scope = dict(vars(nextflow))
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<baseline-pin>', 'exec'), scope)
    with pytest.raises(ValueError, match='no declared weights binding'):
        asyncio.run(scope['_pin_local_invocation_images'](Session(), job, invocation))
    environment = asyncio.run(nextflow._pin_local_invocation_images(Session(), job, invocation))
    receipt = job.provenance['runtime_image_references'][invocation.execution_plan.plan_sha256]
    assert not receipt['legacy_images']
    assert len(receipt['identities']) == 1  # Dorado CAS only; nested .img leaves stay native-lock owned.
    assert environment['BMS_NGS_RUNTIME_SIF'] == params['dorado_runtime_sif']
    assert asyncio.run(nextflow._pin_local_invocation_images(Session(), job, invocation)) == environment
    assert job.provenance['runtime_image_references'][invocation.execution_plan.plan_sha256]['lease_token'] == receipt['lease_token']
    # The selected hashes still refuse changed native leaves, rather than skipping checks.
    plan = invocation.execution_plan
    for kind in ('image', 'runtime_data'):
        selected = next(d for d in plan.dependencies if d.kind == kind and d.semantic_release)
        changed = replace(selected, semantic_release='sha256:' + '0' * 64)
        metadata = replace(plan.metadata, dependencies=tuple(changed if d == selected else d for d in plan.metadata.dependencies))
        bad = replace(invocation, execution_plan=replace(plan, metadata=metadata))
        with pytest.raises(bundle.RemoteBundleError, match='changed or is missing'):
            asyncio.run(nextflow._pin_local_invocation_images(Session(), job, bad))
    (tmp_path / 'pin-proof.json').write_text(json.dumps({'baseline': 'weights-only rejection reproduced',
        'dependencies': [d.logical_id for d in plan.dependencies if d.semantic_release],
        'receipt': receipt, 'environment': environment}, indent=2))


def test_cached_attempt_ancestry_is_not_launch_authority(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'platform/api'))
    from services.remote_execution.executor import _cached_ont_work_attempt
    original = SimpleNamespace(id='original', execution_target_id='worker', remote_attempt_id=str(uuid.uuid4()), params={})
    successor = SimpleNamespace(id='successor', execution_target_id='worker', remote_attempt_id=str(uuid.uuid4()),
                                params={'resume_work_dir': 'work', 'resume_job_id': 'original'})
    job = SimpleNamespace(id='new', model_id='nanopore', execution_target_id='worker',
                          params={'resume_work_dir': 'work', 'resume_job_id': 'successor'})
    session = Session([original, successor])
    assert asyncio.run(_cached_ont_work_attempt(session, job)) == original.remote_attempt_id
    assert not hasattr(job, 'remote_attempt_id')
    job.params = {}
    assert asyncio.run(_cached_ont_work_attempt(session, job)) is None
    job.params = {'resume_work_dir': 'work', 'resume_job_id': 'missing'}
    assert asyncio.run(_cached_ont_work_attempt(session, job)) is None
    job.params['resume_job_id'] = 'original'
    job.execution_target_id = 'independent-worker'
    assert asyncio.run(_cached_ont_work_attempt(session, job)) is None


@pytest.mark.skipif(not os.environ.get('BMS_TEST_ONT_RESUME_ASSETS'), reason='explicit native assets required')
def test_new_remote_attempt_reuses_native_work_and_cache(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'platform/api'))
    from component_runtime import SourceIdentity
    from services import nextflow
    from services.remote_execution import bundle, executor
    from tools import bms_remote_worker as worker
    assets = Path(os.environ['BMS_TEST_ONT_RESUME_ASSETS'])
    repo = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '--shared', '--quiet', str(ROOT), str(repo)], check=True)
    for key, name in {'HOME':'home', 'BMS_DATA':'data', 'BMS_INPUTS':'inputs', 'BMS_RESULTS_DIR':'results',
            'BMS_RESULTS_ROOT':'results', 'BMS_WORK':'work', 'BMS_WEIGHTS':'weights',
            'BMS_CONTAINER_DIR':'containers', 'BMS_STATE_DIR':'state', 'TMPDIR':'tmp',
            'BMS_RUNTIME_IMAGE_STORE':'image-store'}.items():
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv('BMS_HOME', str(repo))
    monkeypatch.setenv('BMS_NEXTFLOW_BIN', str(assets / 'nextflow'))
    monkeypatch.setenv('NXF_OFFLINE', 'true')
    monkeypatch.setenv('NXF_VER', '25.10.0')
    sif = tmp_path / 'containers/dorado.sif'
    subprocess.run(['cp', '--reflink=auto', str(assets / 'dorado-2.1.2.sif'), str(sif)], check=True)
    monkeypatch.setenv('BMS_NGS_RUNTIME_SIF', str(sif))
    reads = tmp_path / 'inputs/reads.fastq'
    reads.write_text('@read\nACGT\n+\nIIII\n')
    workflow = '''nextflow.enable.dsl=2
process CacheControl {
  cpus 1
  memory '512 MB'
  output:
  path 'version.txt'
  script:
  """
  apptainer exec '${params.dorado_runtime_sif}' dorado --version > version.txt 2>&1
  """
}
workflow { CacheControl() }
'''
    (repo / 'workflows/ngs/ont_fastq_qc.nf').write_text(workflow)
    (repo / 'nextflow.config').write_text('profiles { ont_fastq_qc {} ; workstation_ryzen7960x {} }\ntrace { enabled=true; file="trace.tsv"; overwrite=true }\n')
    subprocess.run(['git', 'add', 'workflows/ngs/ont_fastq_qc.nf', 'nextflow.config'], cwd=repo, check=True)
    subprocess.run(['git', '-c', 'user.name=Native receiving', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'bounded remote native cache control'], cwd=repo, check=True)
    source = SourceIdentity.from_checkout(repo)
    remote = tmp_path / 'worker'
    home = remote / 'cache/nextflow'
    home.mkdir(parents=True)
    shutil.copytree(assets / 'nxf-home/framework', home / 'framework')
    # Attached runtime fixture: actual local interpreter/engine, no SSH/provider.
    target = SimpleNamespace(id='task-worker', remote_root=str(remote), capabilities={
        'critical_runtime_binding': {'paths': {'python': sys.executable, 'nextflow': str(assets / 'nextflow')},
        'environment': {}}})
    params = dict(ont_workflow_id='ont_fastq_qc', ont_input_mode='fastq', fastq_path=str(reads),
                  dorado_runtime_sif=str(sif), run_alignment=False, run_assembly=False)
    jobs, reports = [], []
    for label in ('first', 'cached', 'cached-again', 'fresh'):
        job = SimpleNamespace(id=label, model_id='nanopore', mode='fastq_qc', params=dict(params),
            provenance={}, execution_target_id=target.id, remote_attempt_id=None,
            execution_source_revision=source.revision, execution_source_tree=source.tree,
            output_dir=str(tmp_path / 'results' / label), child_output_dir=None, parent_job_id=None,
            lineage_root_job_id=None, assigned_gpu=None)
        Path(job.output_dir).mkdir()
        if label.startswith('cached'):
            job.params.update(resume_job_id=jobs[-1].id, resume_work_dir=str(tmp_path / 'work'),
                              resume_source_dir=jobs[0].output_dir)
        invocation = asyncio.run(nextflow._compile_launch_nextflow_invocation(Session(), job, job.params, job.output_dir))
        prior = asyncio.run(executor._cached_ont_work_attempt(Session(jobs), job))
        prepared = bundle.prepare_remote_bundle(job=job, target=target, command=list(invocation.command),
                                                 native_invocation=invocation, cached_work_attempt_id=prior)
        attempt = Path(prepared.remote_attempt_dir)
        attempt.mkdir(parents=True)
        shutil.copy2(prepared.local_attempt_dir / 'execution-envelope.json', attempt / 'execution-envelope.json')
        def copy(source, destination):
            destination = Path(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['cp', '-a', '--reflink=auto', str(source), str(destination)], check=True)
        copy(prepared.source_transfer.source, prepared.remote_source_dir)
        (attempt / 'bundle').mkdir()
        (attempt / 'bundle/source').symlink_to(prepared.remote_source_dir, target_is_directory=True)
        for transfer in prepared.runtime_transfers + prepared.input_transfers:
            copy(transfer.source, transfer.remote_destination)
        runtime = Path(prepared.remote_runtime_dir)
        runtime.mkdir(parents=True, exist_ok=True)
        (attempt / 'bundle/runtime').symlink_to(runtime, target_is_directory=True)
        for image in prepared.runtime_images:
            if image.role == 'image':
                destination = Path(image.remote_destination)
                if not destination.exists():
                    from scripts.lib.shared_runtime_images import publish_image
                    assert publish_image(image.source, remote / 'cache/runtime-images', image.sha256) == destination
                for alias in image.aliases:
                    path = Path(alias)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.symlink_to(destination)
        status = worker.prepare(attempt)
        assert status['workflow_pid'] is None and status['attempt_id'] == prepared.attempt_id
        assert not (attempt / 'launch-claim.json').exists()
        command = [sys.executable, str(ROOT / 'platform/api/tools/bms_remote_worker.py'),
                   'supervise', '--attempt-dir', str(attempt)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        (tmp_path / (label + '-worker.log')).write_text(result.stdout + result.stderr)
        status = json.loads((attempt / 'status.json').read_text())
        assert status['exit_code'] == 0, (status, (attempt / 'nextflow.log').read_text())
        rows = (Path(prepared.remote_source_dir) / 'trace.tsv').read_text().splitlines()
        trace = dict(zip(rows[0].split('\t'), rows[1].split('\t')))
        assert trace['status'] == ('CACHED' if prior else 'COMPLETED')
        environment = prepared.envelope.environment
        if prior:
            assert environment['BMS_WORK'] == reports[0]['environment']['BMS_WORK']
            assert environment['NXF_CACHE_DIR'] == reports[0]['environment']['NXF_CACHE_DIR']
            assert trace['hash'] == reports[0]['trace']['hash']
            assert '-resume' in prepared.envelope.command
        else:
            assert environment['BMS_WORK'] == str(attempt / 'work')
            assert '-resume' not in prepared.envelope.command
        reports.append(dict(label=label, attempt=prepared.attempt_id, trace=trace, environment=environment,
                            command=prepared.envelope.command, status=status))
        job.remote_attempt_id = prepared.attempt_id
        jobs.append(job)
    assert len({row['attempt'] for row in reports}) == 4
    (tmp_path / 'remote-native-proof.json').write_text(json.dumps(reports, indent=2))
