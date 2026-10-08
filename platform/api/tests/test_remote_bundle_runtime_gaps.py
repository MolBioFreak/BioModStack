"""Real, import-only runtime relocation with split controller roots."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from services.remote_execution import bundle, executor
from tools import bms_remote_worker as worker
from services import nextflow


@pytest.fixture
def package(tmp_path, monkeypatch):
    roots = {key: tmp_path / 'controller' / key for key in ('data', 'inputs', 'results', 'weights', 'containers', 'repo', 'runtime')}
    for path in roots.values():
        path.mkdir(parents=True)
    for name, key in [('get_data_root','data'), ('get_code_root','repo'), ('get_inputs_dir','inputs'),
                      ('get_results_dir','results'), ('get_weights_root','weights'), ('get_container_dir','containers')]:
        monkeypatch.setattr(bundle, name, lambda key=key: roots[key])
    release = roots['runtime']/'releases/r1'
    base = release/'python-runtime'
    # Real base interpreter and complete base stdlib, not fake executable bytes.
    shutil.copytree(sys.base_prefix, base, symlinks=True,
                    ignore=shutil.ignore_patterns('__pycache__', 'site-packages'))
    venv = release/'venv'
    (venv/'bin').mkdir(parents=True)
    (venv/'bin/python').symlink_to(base/'bin'/f'python{sys.version_info.major}.{sys.version_info.minor}')
    (venv/'bin/python3').symlink_to('python')
    (venv/'pyvenv.cfg').write_text(f'home = {base}/bin\ninclude-system-site-packages = false\n')
    import packaging
    site = venv/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
    site.mkdir(parents=True)
    shutil.copytree(Path(packaging.__file__).parent, site/'packaging', ignore=shutil.ignore_patterns('__pycache__'))
    (venv/'bin/probe').write_text(f'#!{sys.executable}\nimport json,packaging; print(json.dumps({{"dependency": packaging.__version__}}))\n')
    (venv/'bin/probe').chmod(0o755)
    current = roots['runtime']/'current'
    current.symlink_to('releases/r1', target_is_directory=True)
    monkeypatch.setenv('BMS_CM_API_RUNTIME_DIR', str(roots['runtime']))
    monkeypatch.setenv('BMS_API_PYTHON', str(current/'venv/bin/python'))
    monkeypatch.setenv('BMS_REMOTE_API_BASE_URL', 'https://bms.example.invalid')
    (roots['containers']/'protenix.sif').write_bytes(b'image-not-executed')
    (roots['weights']/'protenix').mkdir()
    (roots['weights']/'protenix/model.pt').write_bytes(b'weights-not-executed')
    seq = roots['inputs']/'seq.fasta'
    seq.write_text('>A\nAAAA\n')
    (roots['repo']/'main.nf').write_text('workflow {}\n')
    # Source archiving is an isolated seam: this test cannot mutate Git.
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: ('a'*40, 'b'*40))
    monkeypatch.setattr(bundle, '_git', lambda *_: 'b'*40)
    real_run = subprocess.run
    def archive(argv, **kwargs):
        if argv[:2] == ['git', 'archive']:
            with tarfile.open(fileobj=kwargs['stdout'], mode='w') as tar:
                tar.add(roots['repo']/'main.nf', arcname='main.nf')
            return SimpleNamespace(returncode=0)
        return real_run(argv, **kwargs)
    monkeypatch.setattr(bundle.subprocess, 'run', archive)
    monkeypatch.setattr(bundle, 'resolve_job_result_contract', lambda job: {})
    output = roots['results']/'job'
    command = ['nextflow', 'run', str(roots['repo']/'main.nf'), '-w', str(roots['data']/'work'),
               '--out_dir', str(output), '--input', str(seq), '--protenix_msa_backend', 'colabfold_api',
               '--weights_root', str(roots['weights']), '--container_dir', str(roots['containers']),
               '--data_root', str(roots['data']), '--code_root', str(roots['repo']),
               '--api_python', str(current/'venv/bin/python'),
               '--msa_cache_dir', str(roots['data']/'cache'),
               '--msa_local_db', str(roots['data']/'absent-offline'),
               '--af2_models', '/ignored/default/af2', '--bcp_repo_path', '/ignored/default/bcp']
    job = SimpleNamespace(id='job', model_id='protenix', mode='predict', child_output_dir=None,
                          output_dir=str(output), lineage_root_job_id=None, parent_job_id=None,
                          execution_source_revision='a'*40, execution_source_tree='b'*40,
                          params={'protenix_msa_backend':'local', 'af2_models':'/ignored/original',
                                  'bcp_repo_path':str(roots['data']/'unrelated')}, provenance={}, assigned_gpu=0)
    target = SimpleNamespace(id='target', remote_root=str(tmp_path/'remote'))
    for name, value in {'BMS_HOME': roots['repo'], 'BMS_DATA': roots['data'],
                        'BMS_WEIGHTS': roots['weights'], 'BMS_CONTAINER_DIR': roots['containers'],
                        'BMS_WORK': roots['data']/'work', 'BMS_MSA_CACHE': roots['data']/'cache',
                        'BMS_COLABFOLD_DB': roots['data']/'absent-offline'}.items():
        monkeypatch.setenv(name, str(value))
    from services import gpu_config, msa_server
    monkeypatch.setattr(gpu_config, 'read_scheduler_config', lambda: {})
    monkeypatch.setattr(msa_server, 'read_server_settings', lambda: {})
    return roots, release, job, target, command


def test_normalized_bundle_relocates_real_runtime(package, tmp_path):
    roots, release, job, target, command = package
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command)
    envelope = prepared.envelope
    assert len(prepared.runtime_transfers) == 3
    assert len(prepared.input_transfers) == 1
    assert not any(flag in envelope.command for flag in ('--af2_models','--bcp_repo_path','--msa_local_db'))
    assert str(roots['results']) not in ' '.join(envelope.command)
    assert str(tmp_path/'controller') not in ' '.join(envelope.command + list(envelope.environment.values()))
    for transfer in (*prepared.runtime_transfers, *prepared.input_transfers, prepared.source_transfer):
        destination = Path(transfer.remote_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if transfer.source.is_dir():
            shutil.copytree(transfer.source, destination, symlinks=True)
        else:
            shutil.copy2(transfer.source, destination)
    attempt = Path(prepared.remote_attempt_dir)
    shutil.copytree(prepared.local_attempt_dir, attempt, dirs_exist_ok=True)
    (attempt/'bundle/source').symlink_to(prepared.remote_source_dir, target_is_directory=True)
    (attempt/'bundle/runtime').symlink_to(prepared.remote_runtime_dir, target_is_directory=True)
    # Remove the complete temporary controller, including its original base stdlib.
    shutil.rmtree(tmp_path/'controller')
    assert not release.exists()
    worker.verify_bundle(attempt)
    interpreter = envelope.environment['BMS_API_PYTHON']
    assert interpreter == envelope.command[envelope.command.index('--api_python')+1]
    probe = 'import json,ssl,sqlite3,packaging,sys; print(json.dumps({"base":sys.base_prefix,"prefix":sys.prefix,"dependency":packaging.__version__}))'
    result = subprocess.run([interpreter, '-I', '-B', '-c', probe], text=True, capture_output=True, check=True, timeout=20)
    observed = json.loads(result.stdout)
    assert observed['base'].startswith(prepared.remote_runtime_dir)
    assert observed['prefix'].startswith(prepared.remote_runtime_dir)
    assert observed['dependency']
    entrypoint = subprocess.run([str(Path(interpreter).with_name('probe'))], text=True, capture_output=True, check=True, timeout=20)
    assert json.loads(entrypoint.stdout)['dependency'] == observed['dependency']
    print('REAL_RELOCATION', json.dumps(observed), 'controller_removed=True')


def test_runtime_config_accepts_lexical_current_alias(package, tmp_path):
    roots, release, job, target, command = package
    config = release/'venv/pyvenv.cfg'
    config.write_text(f"home = {roots['runtime']}/current/python-runtime/bin\ninclude-system-site-packages = false\n")
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command)
    transfer = next(t for t in prepared.runtime_transfers if t.origin == release)
    assert str(roots['runtime']) not in (transfer.source/'venv/pyvenv.cfg').read_text()


def test_effective_dependency_inventory_omits_other_model_runtime_defaults():
    command = ['nextflow', '--protenix_msa_backend', 'colabfold_api',
               '--rf3_container_path', '/unrelated/rf3.sif', '--run_frustrampnn', 'true']
    compiled, params = bundle.compile_remote_dependencies('protenix', 'predict', command)
    assert '--rf3_container_path' not in compiled
    assert params['run_frustrampnn'] is True


def test_actual_normalized_nextflow_command_omits_unrelated_original_params(package):
    roots, release, job, target, command = package
    normalized = dict(job.params, protenix_msa_backend='colabfold_api', sequence='AAAA',
                      api_python=str(roots['runtime']/'current/venv/bin/python'))
    argv = nextflow.build_nextflow_command('protenix', 'predict', normalized, job.output_dir, job_id=job.id)
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=argv)
    assert len(prepared.runtime_transfers) == 3
    assert prepared.input_transfers == ()
    assert '--msa_local_db' not in prepared.envelope.command
    assert '--af2_models' not in prepared.envelope.command
    assert str(roots['data']) not in ' '.join(prepared.envelope.command)


@pytest.mark.parametrize('native,expected', [(None, 'colabfold_api'), ('auto', 'colabfold_api'), ('local', 'local')])
def test_remote_provider_resolution_preserves_explicit_backend_and_caller_argv(native, expected):
    command = ['nextflow', '--msa_provider', 'colabfold_api', '--msa_local_db', '/offline']
    if native is not None:
        command.extend(['--protenix_msa_backend', native])
    original = list(command)
    compiled, effective = bundle.compile_remote_dependencies('protenix', 'predict', command)
    assert command == original
    assert effective['protenix_msa_backend'] == expected
    assert compiled[compiled.index('--protenix_msa_backend') + 1] == expected
    assert ('--msa_local_db' in compiled) is (expected == 'local')


def test_cancelled_smaller_request_shape_keeps_enabled_stage_assets(package):
    roots, release, job, target, command = package
    (roots['containers']/'frustrampnn.sif').write_bytes(b'fixture-image-not-executed')
    normalized = dict(
        msa_provider='colabfold_api', allow_retries=False, sequence='AAAA', gpu_id=0,
        protenix_n_sample=5, protenix_n_cycle=10, protenix_n_step=200,
        protenix_seeds='42', protenix_use_msa=True, protenix_use_template=False,
        run_frustrampnn=True, pred_method='protenix', structure_validator='protenix',
        api_python=str(roots['runtime']/'current/venv/bin/python'),
    )
    argv = nextflow.build_nextflow_command('protenix', 'predict', normalized, job.output_dir, job_id=job.id)
    _, effective = bundle.compile_remote_dependencies('protenix', 'predict', argv)
    assert effective.get('protenix_msa_backend') == 'colabfold_api', {
        key: value for key, value in effective.items() if 'msa' in key
    }
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=argv)
    sources = {transfer.origin.name for transfer in prepared.runtime_transfers}
    assert sources == {'protenix.sif', 'frustrampnn.sif', 'protenix', 'r1'}
    assert '--msa_local_db' not in prepared.envelope.command
    assert prepared.envelope.command[prepared.envelope.command.index('--protenix_msa_backend') + 1] == 'colabfold_api'
    assert prepared.input_transfers == ()


@pytest.mark.asyncio
async def test_staging_has_no_controller_aliases(package, monkeypatch):
    roots, release, job, target, command = package
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command)
    calls = []
    async def remote(connection, argv, **kwargs):
        calls.append(argv)
    async def transfer(*args, **kwargs):
        pass
    monkeypatch.setattr(executor, 'run_remote', remote)
    monkeypatch.setattr(executor, '_transfer_plan', transfer)
    monkeypatch.setattr(executor, 'rsync_to_remote', transfer)
    await executor._stage_bundle(None, prepared)
    assert str(roots['results']) not in str(calls)
    assert str(roots['data']) not in str(calls)
    assert any(prepared.envelope.environment['BMS_MSA_CACHE'] in argv for argv in calls)
