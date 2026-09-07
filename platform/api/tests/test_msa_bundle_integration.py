"""Offline controller -> immutable bundle -> relocated real worker MSA adapter."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest
from services.remote_execution import bundle
from services import msa_preparation, nextflow
from tools import bms_remote_worker as worker

ROOT = Path(__file__).resolve().parents[3]
SEQUENCE = 'MKTLLILAVVAAALA'
A3M = f'>query\n{SEQUENCE}\n>UniRef_fixture TaxID=9606\nMKTLL-LAVVAAALA\n>environment_fixture\nMKTLLILAVVAA-LA\n'.encode()


@pytest.fixture
def offline_bundle(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Public outbound forbidden in offline MSA integration')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    roots = {key: tmp_path / 'controller' / key for key in ('data', 'inputs', 'results', 'weights', 'containers')}
    for root in roots.values():
        root.mkdir(parents=True)
    for name, key in [('get_data_root', 'data'), ('get_inputs_dir', 'inputs'), ('get_results_dir', 'results'),
                      ('get_weights_root', 'weights'), ('get_container_dir', 'containers')]:
        monkeypatch.setattr(bundle, name, lambda key=key: roots[key])
    monkeypatch.setattr(bundle, 'get_code_root', lambda: ROOT)
    monkeypatch.setattr(bundle, 'current_source_identity', lambda *_: ('a'*40, 'b'*40))
    monkeypatch.setattr(bundle, '_git', lambda *_: 'b'*40)
    monkeypatch.setattr(bundle, '_runtime_assets', lambda *_: [])
    monkeypatch.setattr(bundle, 'resolve_job_result_contract', lambda *_: {})
    run = subprocess.run
    def archive(argv, **kwargs):
        if argv[:2] == ['git', 'archive']:
            # Archive seam only: real consumer bytes, never commit or mutate Git.
            with tarfile.open(fileobj=kwargs['stdout'], mode='w') as stream:
                for name in ('biomodstack_msa_handoff.py', 'biomodstack_msa_controller.py',
                             'biomodstack_msa_policy.py', 'scripts/prepare_protenix_msa.py',
                             'scripts/lib/local_msa', 'schemas/msa_search_policy.json',
                             'scripts/local_msa_runtime.py', 'modules/protenix.nf'):
                    stream.add(ROOT/name, arcname=name)
            return SimpleNamespace(returncode=0)
        return run(argv, **kwargs)
    monkeypatch.setattr(bundle.subprocess, 'run', archive)
    cache = roots['data']/'cache'
    sha = hashlib.sha256(SEQUENCE.encode()).hexdigest()
    cached = cache/sha[:2]/f'{sha}.a3m.gz'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(gzip.compress(A3M))
    output = roots['results']/'job'
    job = SimpleNamespace(id='msa-offline', model_id='protenix', mode='predict', child_output_dir=None,
                          output_dir=str(output), lineage_root_job_id=None, parent_job_id=None,
                          execution_source_revision='a'*40, execution_source_tree='b'*40,
                          provenance={}, assigned_gpu=0)
    target = SimpleNamespace(id='target', remote_root=str(tmp_path/'remote'))
    command = ['nextflow', 'run', str(ROOT/'workflows/structure_prediction.nf'),
               '--out_dir', str(output), '--sequence_input', SEQUENCE, '--sequence_name', 'fixture',
               '--pred_method', 'protenix', '--protenix_msa_backend', 'colabfold_api',
               '--msa_cache_dir', str(cache), '--protenix_seeds', '42,71', '--protenix_n_sample', '3']
    return roots, job, target, command, cached


def test_bundle_relocated_real_consumer_without_public_outbound(offline_bundle, tmp_path):
    roots, job, target, command, cached = offline_bundle
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command)
    envelope = prepared.envelope
    inputs = [record for record in envelope.files if record.role == 'input']
    assert len(inputs) == 3  # portable receipt, pairing query and unpaired hits
    assert all('prepared-msa/' in record.relative_path for record in inputs)
    assert any(record.relative_path.endswith('/msa-inputs.json') for record in inputs)
    for transfer in (*prepared.input_transfers, prepared.source_transfer):
        destination = Path(transfer.remote_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(transfer.source, destination)
    attempt = Path(prepared.remote_attempt_dir)
    shutil.copytree(prepared.local_attempt_dir, attempt, dirs_exist_ok=True)
    (attempt/'bundle/source').symlink_to(prepared.remote_source_dir, target_is_directory=True)
    shutil.rmtree(tmp_path/'controller')
    worker.verify_bundle(attempt)
    argv = envelope.command
    source = Path(argv[argv.index('--protenix_prepared_msa_dir')+1])
    sha = argv[argv.index('--protenix_prepared_msa_sha256')+1]
    manifest = json.loads((source/'msa-inputs.json').read_text())
    assert str(roots['data']) not in json.dumps(manifest)
    assert manifest['settings']['protenix_n_sample'] == '3'
    task = tmp_path/'task'
    task.mkdir()
    payload = nextflow.compile_controller_protenix_input({'sequence_input': SEQUENCE, 'protenix_seeds': '42,71'})
    payload[0]['name'] = 'fixture_job1'
    payload[0]['templatesPath'] = '/worker/templates'
    (task/'input.json').write_text(json.dumps(payload))
    # Fresh interpreter, real relocated CLI consumer. Socket creation for either
    # public IPv4/IPv6 family is forbidden, not merely a mocked provider response.
    probe = """import socket,runpy,sys
original = socket.socket
class DeniedSocket(original):
    def __init__(self, family=socket.AF_INET, *args, **kwargs):
        if family in (socket.AF_INET,socket.AF_INET6):
            raise RuntimeError('OUTBOUND_DENIED')
        super().__init__(family,*args,**kwargs)
socket.socket=DeniedSocket
script=sys.argv.pop(1)
sys.path.insert(0,str(__import__('pathlib').Path(script).parents[1]))
sys.path.insert(0,str(__import__('pathlib').Path(script).parent))
runpy.run_path(script,run_name='__main__')
"""
    result = subprocess.run([sys.executable, '-B', '-c', probe,
                             str(Path(prepared.remote_source_dir)/'scripts/prepare_protenix_msa.py'),
                             '--input_json', 'input.json', '--output_json', 'prepared.json', '--out_dir', 'msa',
                             '--prepared-inputs', str(source), '--prepared-sha256', sha,
                             '--report_json', 'report.json'], cwd=task, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    observed = json.loads((task/'prepared.json').read_text())
    assert observed[0]['modelSeeds'] == [42,71]
    assert observed[0]['name'] == 'fixture_job1'
    assert observed[0]['templatesPath'] == '/worker/templates'
    chain = observed[0]['sequences'][0]['proteinChain']
    assert Path(chain['unpairedMsaPath']).read_bytes() == A3M
    assert json.loads((task/'report.json').read_text())['backend'] == 'controller_prepared'
    # Envelope verification and direct consumer both reject tampering.
    Path(chain['unpairedMsaPath']).write_text('>query\nAAAAAAAAAAAAAAA\n')
    with pytest.raises(Exception):
        worker.verify_bundle(attempt)
    from biomodstack_msa_handoff import hydrate_prepared_protenix_task
    with pytest.raises(ValueError, match='query identity'):
        hydrate_prepared_protenix_task(payload, source, sha)


def test_missing_runtime_fails_before_bundle_ready(offline_bundle, monkeypatch):
    roots, job, target, command, cached = offline_bundle
    cached.unlink()
    monkeypatch.delenv('BMS_MSA_CONTROLLER_CONFIG', raising=False)
    with pytest.raises(bundle.RemoteBundleError, match='runtime unavailable or unpinned'):
        bundle.prepare_remote_bundle(job=job, target=target, command=command)
    assert not Path(target.remote_root).exists()
    assert not list((roots['data']/'remote-execution').rglob('execution-envelope.json'))


@pytest.mark.parametrize('mutation', ['complex', 'mixed', 'caller_transport'])
def test_unsupported_paths_fail_before_transport(offline_bundle, mutation):
    _, job, target, command, _ = offline_bundle
    if mutation == 'complex':
        command[2] = str(ROOT/'workflows/complex_prediction.nf')
    elif mutation == 'mixed':
        command[command.index('--pred_method')+1] = 'boltz_protenix'
    else:
        command += ['--protenix_prepared_msa_dir', '/foreign']
    with pytest.raises(bundle.RemoteBundleError, match='controller-owned|supports only'):
        bundle.prepare_remote_bundle(job=job, target=target, command=command)
    assert not Path(target.remote_root).exists()


def test_no_msa_does_not_prepare(offline_bundle, monkeypatch):
    _, job, target, command, cached = offline_bundle
    cached.unlink()
    monkeypatch.setattr(msa_preparation, 'prepare_remote_protenix_inputs', lambda *_: pytest.fail('No-MSA must not prepare'))
    result = bundle.prepare_remote_bundle(job=job, target=target, command=command+['--protenix_use_msa', 'false'])
    assert not result.input_transfers


def test_offline_native_generation_is_packaged_by_bundle(offline_bundle, tmp_path, monkeypatch):
    _, job, target, command, cached = offline_bundle
    cached.unlink()
    config = tmp_path/'controller-config.json'
    config.write_text(json.dumps({'role': 'msa_controller',
        'machine_id': Path('/etc/machine-id').read_text().strip(),
        'qualified_single_egress': True, 'egress_identity': 'offline-fixture-NOT-qualified',
        'state_dir': str(tmp_path/'controller-state')}))
    monkeypatch.setenv('BMS_MSA_CONTROLLER_CONFIG', str(config))
    monkeypatch.setattr(msa_preparation, 'qualify_protenix_controller_runtime',
                        lambda _: {'version': 'offline-fixture-not-installed', 'msa_search_sha256': 'f'*64})
    import prepare_protenix_msa as adapter
    from biomodstack_msa_controller import require_controller_submission
    calls = []
    def offline_native(input_json, output_json, work_dir, host):
        require_controller_submission(host)
        calls.append(host)
        payload = json.loads(input_json.read_text())
        msa = work_dir/'native.a3m'
        msa.write_bytes(A3M)
        payload[0]['sequences'][0]['proteinChain']['unpairedMsaPath'] = str(msa)
        output_json.write_text(json.dumps(payload))
        return output_json
    monkeypatch.setattr(adapter, 'prepare_with_colabfold_api', offline_native)
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command)
    assert calls == ['https://api.colabfold.com']  # fixture call, no network
    receipt = json.loads((prepared.input_transfers[0].source/'msa-inputs.json').read_text())
    assert receipt['provenance']['controller_runtime']['version'] == 'offline-fixture-not-installed'
    assert not (tmp_path/'controller-state/active.json').exists()
    assert not Path(target.remote_root).exists()


def test_runtime_pin_rejects_uninstalled_adapter(tmp_path):
    config = tmp_path/'pin.json'
    config.write_text(json.dumps({'protenix_runtime': {'version': 'not-installed', 'msa_search_sha256': 'f'*64}}))
    with pytest.raises(RuntimeError, match='runtime unavailable or unpinned'):
        msa_preparation.qualify_protenix_controller_runtime(config)


def test_native_compile_rejects_batch_and_preserves_seeds():
    with pytest.raises(ValueError, match='complex/batch'):
        nextflow.compile_controller_protenix_input({'sequence_batch_json_path': '/input/batch.json'})
    assert nextflow.compile_controller_protenix_input({'sequence_input': SEQUENCE, 'protenix_seeds': '7,19'})[0]['modelSeeds'] == [7,19]


def test_real_nextflow_stages_handoff_with_science_stub(offline_bundle, tmp_path):
    import os
    _, job, target, command, _ = offline_bundle
    prepared = bundle.prepare_remote_bundle(job=job, target=target, command=command)
    msa = prepared.input_transfers[0].source
    sha = hashlib.sha256((msa/'msa-inputs.json').read_bytes()).hexdigest()
    jar = Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    if not jar.is_file() or not shutil.which('java'):
        pytest.skip('Installed offline Nextflow 25.10.1/Java required for DSL process proof')
    # Only replace the science execution with a declared Nextflow stub. The
    # production input declaration/staging and complete production DSL compile.
    stub = '''    stub:
    """
    cp '${params.fixture_input}' input.json
    PYTHONPATH='${params.deny_dir}' '${params.fixture_python}' '${params.code_root}/scripts/prepare_protenix_msa.py' --input_json input.json --output_json prepared.json --out_dir msa_prepared --report_json msa_prepared/msa_report.json --prepared-inputs '${prepared_msa}' --prepared-sha256 '${params.protenix_prepared_msa_sha256}'
    """

    script:
'''
    module = (ROOT/'modules/protenix.nf').read_text().replace('    script:\n', stub, 1)
    (tmp_path/'protenix.nf').write_text(module)
    (tmp_path/'main.nf').write_text("""nextflow.enable.dsl=2
include { ProtenixPredict } from './protenix.nf'
workflow {
    ProtenixPredict(Channel.of(tuple([producer_stage:'fixture'], params.sequence_input, 'fixture', file(params.protenix_prepared_msa_dir, checkIfExists:true))))
}
""")
    input_json = tmp_path/'native-input.json'
    input_json.write_text(json.dumps(nextflow.compile_controller_protenix_input({'sequence_input': SEQUENCE})))
    deny = tmp_path/'deny'
    deny.mkdir()
    (deny/'sitecustomize.py').write_text("import socket\ndef denied(*a,**k): raise RuntimeError('OUTBOUND_DENIED')\nsocket.socket.connect=denied\nsocket.socket.connect_ex=denied\nsocket.create_connection=denied\n")
    parameters = {'fixture_input': str(input_json), 'fixture_python': sys.executable,
                  'code_root': str(ROOT), 'out_dir': str(tmp_path/'out'), 'deny_dir': str(deny),
                  'protenix_prepared_msa_dir': str(msa), 'protenix_prepared_msa_sha256': sha,
                  'sequence_input': SEQUENCE, 'msa_cache_dir': str(tmp_path/'unused-cache')}
    (tmp_path/'params.json').write_text(json.dumps(parameters))
    result = subprocess.run(['java', '-jar', str(jar), 'run', str(tmp_path/'main.nf'),
                             '-stub-run', '-params-file', str(tmp_path/'params.json'), '-ansi-log', 'false'],
                            cwd=tmp_path, env={**os.environ, 'NXF_HOME':str(tmp_path/'nxf-home'),
                                              'NXF_OFFLINE':'true', 'NXF_DISABLE_CHECK_LATEST':'true'},
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    reports = list((tmp_path/'work').rglob('msa_report.json'))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())['backend'] == 'controller_prepared'
    assert list((tmp_path/'work').rglob('prepared-msa/msa-inputs.json'))


def test_nextflow_stages_declared_handoff_as_process_path():
    module = (ROOT/'modules/protenix.nf').read_text()
    workflow = (ROOT/'modules/structure_prediction.nf').read_text()
    assert 'path(prepared_msa)' in module
    assert '--prepared-inputs' in module and '--prepared-sha256' in module
    assert workflow.count('file(params.protenix_prepared_msa_dir, checkIfExists: true)') == 3
