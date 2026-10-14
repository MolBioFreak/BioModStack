"""Execution-side ONT replay: real Git archives and historical native compiler.

These tests execute no instrument/provider/scientific process. The companion
native control exercises real Nextflow caching with the installed NGS runtime.
"""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from component_runtime import SourceIdentity
from services import nextflow
from services.remote_execution import bundle


@pytest.fixture
def upgraded_source(tmp_path, monkeypatch):
    original = Path(__file__).resolve().parents[3]
    repo = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', '--shared', '--quiet', str(original), str(repo)], check=True)
    old = SourceIdentity.from_checkout(repo)
    subprocess.run(['git', '-c', 'user.name=Replay test', '-c', 'user.email=replay@example.invalid',
                    'commit', '--quiet', '--allow-empty', '-m', 'fixture installation upgrade'], cwd=repo, check=True)
    current = SourceIdentity.from_checkout(repo)
    assert current != old
    monkeypatch.setenv('BMS_REMOTE_API_BASE_URL', 'https://offline.example.invalid')
    monkeypatch.setenv('BMS_RUNTIME_IMAGE_STORE', str(tmp_path / 'containers/.image-store'))
    monkeypatch.setenv('BMS_HOME', str(repo))
    monkeypatch.setenv('BMS_DATA', str(tmp_path / 'data'))
    monkeypatch.setenv('BMS_INPUTS', str(tmp_path / 'inputs'))
    monkeypatch.setenv('BMS_RESULTS_DIR', str(tmp_path / 'results'))
    monkeypatch.setenv('BMS_WORK', str(tmp_path / 'work'))
    monkeypatch.setenv('BMS_CONTAINER_DIR', str(tmp_path / 'containers'))
    monkeypatch.setenv('BMS_WEIGHTS', str(tmp_path / 'weights'))
    return repo, old, current


def retained_job(tmp_path, source):
    reads = tmp_path / 'inputs' / 'reads.fastq'
    reads.parent.mkdir(exist_ok=True)
    reads.write_text('@read\nACGT\n+\nIIII\n')
    params = dict(ont_workflow_id='ont_basecall_dna', ont_input_mode='pod5',
                  pod5_dir=str(reads.parent), resume_job_id='retained',
                  run_alignment=False, run_assembly=False,
                  run_modifications=False, dorado_runtime_sif=str(tmp_path / 'retained.sif'),
                  resume_work_dir=str(tmp_path / 'work'), resume_source_dir=str(tmp_path / 'results'))
    return SimpleNamespace(id='retained', model_id='nanopore', mode='basecall_dna',
        params=params, provenance={}, execution_source_revision=source.revision,
        execution_source_tree=source.tree, output_dir=str(tmp_path / 'results/retained'),
        child_output_dir=None, parent_job_id=None, lineage_root_job_id=None)


@pytest.mark.parametrize('mode', ['basecall_dna', 'fastq_qc'])
@pytest.mark.parametrize('preview', [False, True])
def test_historical_compiler_and_source_are_selected(upgraded_source, tmp_path, monkeypatch, mode, preview):
    repo, old, current = upgraded_source
    job = retained_job(tmp_path, old)
    if mode == 'fastq_qc':
        job.mode = mode
        job.params.update(ont_workflow_id='ont_fastq_qc', ont_input_mode='fastq',
                          fastq_path=str(tmp_path / 'inputs/reads.fastq'))
        job.params.pop('pod5_dir')
    monkeypatch.setattr(nextflow, 'compile_nextflow_invocation',
        lambda *a, **kw: pytest.fail('current compiler must not label itself historical'))
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir, _preview_only=preview)
    assert invocation.source_identity == old
    assert invocation.execution_plan.source_identity == old
    if not preview:
        assert '-resume' in invocation.command
        assert invocation.command[invocation.command.index('--dorado_runtime_sif') + 1] == job.params['dorado_runtime_sif']
    assert invocation.native_parameters['dorado_runtime_sif'] == job.params['dorado_runtime_sif']
    root = Path(invocation.native_parameters['code_root'])
    assert root != repo
    assert (root / invocation.entrypoint).is_file()
    assert (root / '.bms-source.tar.gz').is_file()
    assert json.loads(invocation.requested_json) == job.params
    assert SourceIdentity.from_checkout(repo) == current


@pytest.mark.parametrize('preview', [False, True])
def test_fresh_work_stays_current(upgraded_source, tmp_path, preview):
    repo, old, current = upgraded_source
    job = retained_job(tmp_path, current)
    job.params.pop('resume_work_dir')
    job.params.pop('resume_source_dir')
    job.params.pop('dorado_runtime_sif')
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir, _preview_only=preview)
    assert invocation.source_identity == current
    assert invocation.native_parameters['code_root'] == str(repo)
    if not preview:
        assert '-resume' not in invocation.command


@pytest.mark.parametrize('change', ['tree', 'revision', 'not_resume', 'other_model'])
def test_integrity_and_scope_are_not_bypassed(upgraded_source, tmp_path, change):
    _, old, _ = upgraded_source
    job = retained_job(tmp_path, old)
    if change == 'tree':
        job.execution_source_tree = '0' * 40
    elif change == 'revision':
        job.execution_source_revision = '0' * 40
    elif change == 'not_resume':
        job.params.pop('resume_work_dir')
    else:
        job.model_id = 'boltz2'
    with pytest.raises((ValueError, bundle.RemoteBundleError, subprocess.CalledProcessError)):
        nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)


def test_corrupt_cached_archive_cannot_enter_historical_compiler(upgraded_source, tmp_path):
    repo, old, _ = upgraded_source
    data = tmp_path / 'data'
    bundle._staged_source_archive(repo, data, old.revision, tmp_path / 'first', extract=False)
    archive = data / 'remote-execution/source-archives' / (old.revision + '.tar.gz')
    archive.write_bytes(b'corrupt archive')
    job = retained_job(tmp_path, old)
    with pytest.raises(bundle.RemoteBundleError, match='Cached source archive changed'):
        nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)


@pytest.mark.parametrize('tamper', [None, 'source', 'label'])
def test_retained_bundle_uses_verified_historical_source(upgraded_source, tmp_path, monkeypatch, tamper):
    repo, old, _ = upgraded_source
    job = retained_job(tmp_path, old)
    job.mode = 'fastq_qc'
    job.params.update(ont_workflow_id='ont_fastq_qc', ont_input_mode='fastq',
                      fastq_path=str(tmp_path / 'inputs/reads.fastq'))
    job.params.pop('pod5_dir')
    job.params['resume_source_dir'] = job.output_dir
    Path(job.output_dir).mkdir(parents=True)
    work = tmp_path / 'data/work'
    work.mkdir(parents=True)
    (work / '.command.sh').write_text('printf "transport-only retained work fixture\\n"\n')
    job.params['resume_work_dir'] = str(work)
    monkeypatch.setenv('BMS_WORK', str(work))
    job.assigned_gpu = None
    job.provenance = {'remote_execution_assignment': {'lease_id': 'fixture-lease', 'gpu_indices': []}}
    containers = tmp_path / 'containers'
    containers.mkdir()
    (containers / 'dorado.sif').write_bytes(b'transport-only image, never executed')
    job.params['dorado_runtime_sif'] = str(containers / 'dorado.sif')
    from python_runtime_fixture import copy_python_base
    import sys
    runtime = tmp_path / 'runtime/current'
    base = runtime / 'python-runtime'
    copy_python_base(base)
    venv = runtime / 'venv'
    (venv / 'bin').mkdir(parents=True)
    (venv / 'bin/python').symlink_to(base / 'bin' / f'python{sys.version_info.major}.{sys.version_info.minor}')
    (venv / 'pyvenv.cfg').write_text(f'home = {base}/bin\ninclude-system-site-packages = false\n')
    monkeypatch.setenv('BMS_CM_API_RUNTIME_DIR', str(runtime.parent))
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    root = Path(invocation.native_parameters['code_root'])
    if tamper == 'source':
        (root / invocation.entrypoint).write_text('workflow { error "tampered" }')
    elif tamper == 'label':
        from dataclasses import replace
        from component_runtime import canonical_bytes
        # Merely attaching the old source label to the current code root must fail.
        native = dict(invocation.native_parameters, code_root=str(repo))
        invocation = replace(invocation, execution_plan=None, native_parameters_json=canonical_bytes(native))
    target = SimpleNamespace(id='offline-target', remote_root=str(tmp_path / 'remote'))
    if tamper:
        with pytest.raises(bundle.RemoteBundleError, match='Retained ONT'):
            bundle.prepare_remote_bundle(job=job, target=target,
                command=list(invocation.command), native_invocation=invocation)
        return
    prepared = bundle.prepare_remote_bundle(job=job, target=target,
        command=list(invocation.command), native_invocation=invocation)
    assert prepared.envelope.source_revision == old.revision
    assert prepared.envelope.source_tree == old.tree
    assert str(root) not in ' '.join(prepared.envelope.command)
    assert str(repo) not in ' '.join(prepared.envelope.command)
    assert prepared.remote_source_dir in ' '.join(prepared.envelope.command)
    assert (prepared.source_transfer.source / invocation.entrypoint).read_bytes() == (root / invocation.entrypoint).read_bytes()
