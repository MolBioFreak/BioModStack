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


def controller_bundle(*, job, target, **kwargs):
    # The actual launch order: one native compile -> exact input materialization
    # -> hosted-MSA artifact binding -> unchanged invocation -> placement.
    from services.model_msa_handoff import prepare_launch_msa
    params = dict(job.params)
    invocation = nextflow.compile_job_nextflow_invocation(job, params, job.output_dir)
    invocation.materialize_inputs(Path(job.output_dir))
    if not params.get('protenix_prepared_msa_dir'):
        params = prepare_launch_msa(str(job.model_id),
            {**params, **invocation.native_parameters}, Path(job.output_dir) / 'prepared-msa')
    invocation = nextflow._bind_protenix_msa_transport(invocation, params)
    return bundle.prepare_remote_bundle(job=job, target=target,
        command=list(invocation.command), native_invocation=invocation, **kwargs)


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
    from component_runtime import SourceIdentity
    monkeypatch.setattr(SourceIdentity, 'from_checkout', lambda *_: SourceIdentity('a'*40, 'b'*40))
    monkeypatch.setattr(bundle, '_runtime_assets', lambda *_, include_support=False, native_invocation=None: [])
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
    # Explicit supplied-input seam for this transport fixture. Query-only cache
    # bytes are NOT presented as provider/settings-qualified shared API evidence.
    native_prepare = msa_preparation.prepare_protenix_inputs
    def supplied_inputs(config, input_json, destination, settings):
        if cached.exists():
            unpaired = cached.parent / 'supplied.a3m'
            paired = cached.parent / 'supplied-paired.a3m'
            unpaired.write_bytes(A3M)
            paired.write_text(f'>query\n{SEQUENCE}\n')
            payload = json.loads(input_json.read_text())
            for task in payload:
                for component in task['sequences']:
                    chain = component.get('proteinChain')
                    if isinstance(chain, dict) and chain.get('sequence') == SEQUENCE:
                        chain.update(unpairedMsaPath=str(unpaired), pairedMsaPath=str(paired))
            input_json.write_text(json.dumps(payload))
        return native_prepare(config, input_json, destination, settings)
    monkeypatch.setattr(msa_preparation, 'prepare_protenix_inputs', supplied_inputs)
    output = roots['results']/'job'
    job = SimpleNamespace(id='msa-offline', model_id='protenix', mode='predict', child_output_dir=None,
                          output_dir=str(output), lineage_root_job_id=None, parent_job_id=None,
                          execution_source_revision='a'*40, execution_source_tree='b'*40,
                          provenance={'remote_execution_assignment': {'lease_id': 'fixture-lease', 'gpu_indices': [0]}},
                          assigned_gpu=0,
                          # This transport fixture selects MSA, not optional Frustra science.
                          params=dict(run_frustrampnn=False, sequence=SEQUENCE, sequence_name='fixture', pred_method='protenix',
                              protenix_msa_backend='colabfold_api', protenix_use_msa=True,
                              msa_cache_dir=str(cache), protenix_seeds='42,71', protenix_n_sample=3))
    target = SimpleNamespace(id='target', remote_root=str(tmp_path/'remote'))
    return roots, job, target, cached


def test_bundle_relocated_real_consumer_without_public_outbound(offline_bundle, tmp_path):
    roots, job, target, cached = offline_bundle
    prepared = controller_bundle(job=job, target=target)
    envelope = prepared.envelope
    inputs = [record for record in envelope.files if record.role == 'input']
    bindings = [record for record in inputs if record.relative_path == 'inputs/.bms/portable-input-bindings.json']
    controls = {'inputs/component-resources.config'}
    msa_inputs = [record for record in inputs if record not in bindings and record.relative_path not in controls]
    assert len(bindings) == 1  # integrity-covered access metadata, not another MSA
    assert controls <= {record.relative_path for record in inputs}
    assert len(msa_inputs) == 3  # portable receipt, pairing query and unpaired hits
    assert all('prepared-msa/' in record.relative_path for record in msa_inputs)
    assert any(record.relative_path.endswith('/msa-inputs.json') for record in inputs)
    for transfer in (*prepared.input_transfers, prepared.source_transfer):
        destination = Path(transfer.remote_destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if transfer.source.is_dir():
            shutil.copytree(transfer.source, destination)
        else:
            shutil.copyfile(transfer.source, destination)
    attempt = Path(prepared.remote_attempt_dir)
    shutil.copytree(prepared.local_attempt_dir, attempt, dirs_exist_ok=True)
    (attempt/'bundle/source').symlink_to(prepared.remote_source_dir, target_is_directory=True)
    shutil.rmtree(tmp_path/'controller')
    worker.verify_bundle(attempt)
    # MSA-only prediction has no dynamic children; consume its native argv.
    assert 'BMS_COMPONENT_CONTEXT' not in envelope.environment
    argv = envelope.command
    source = Path(argv[argv.index('--protenix_prepared_msa_dir')+1])
    sha = argv[argv.index('--protenix_prepared_msa_sha256')+1]
    manifest = json.loads((source/'msa-inputs.json').read_text())
    assert str(roots['data']) not in json.dumps(manifest)
    assert manifest['settings']['protenix_n_sample'] == 3
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


def test_provider_failure_blocks_bundle_ready(offline_bundle, monkeypatch):
    roots, job, target, cached = offline_bundle
    cached.unlink()
    def unavailable(**kwargs):
        raise RuntimeError('MSA provider unavailable: offline failure fixture')
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', unavailable)
    with pytest.raises(RuntimeError, match='MSA provider unavailable'):
        controller_bundle(job=job, target=target)
    assert not Path(target.remote_root).exists()
    assert not list((roots['data']/'remote-execution').rglob('execution-envelope.json'))


@pytest.mark.parametrize('mutation', ['mixed', 'caller_transport'])
def test_unsupported_paths_fail_before_transport(offline_bundle, mutation):
    _, job, target, _ = offline_bundle
    if mutation == 'mixed':
        from dataclasses import replace
        from component_runtime import canonical_bytes
        original = nextflow.compile_job_nextflow_invocation(job, dict(job.params), job.output_dir)
        # Explicit negative bundle-boundary fixture. The standalone real compiler
        # fixes pred_method=protenix; do not pretend it emitted a mixed workflow.
        command = list(original.command)
        command[command.index('--pred_method') + 1] = 'boltz_protenix'
        def prepare():
            # The selected-plan binding now rejects this forged alternate
            # science before the later unsupported-workflow transport guard.
            invocation = replace(original, command=tuple(command),
                requested_json=canonical_bytes({**json.loads(original.requested_json), 'pred_method': 'boltz_protenix'}),
                effective_json=canonical_bytes({**json.loads(original.effective_json), 'pred_method': 'boltz_protenix'}),
                native_parameters_json=canonical_bytes({**original.native_parameters, 'pred_method': 'boltz_protenix'}))
            return bundle.prepare_remote_bundle(job=job, target=target,
                command=command, native_invocation=invocation)
    else:
        job.params['protenix_prepared_msa_dir'] = '/foreign'
        job.params['protenix_prepared_msa_sha256'] = 'f' * 64
        def prepare():
            return controller_bundle(job=job, target=target)
    with pytest.raises((bundle.RemoteBundleError, ValueError), match='mixed|unavailable|selected plan science changed'):
        prepare()
    assert not Path(target.remote_root).exists()


def test_no_msa_does_not_prepare(offline_bundle, monkeypatch):
    _, job, target, cached = offline_bundle
    cached.unlink()
    monkeypatch.setattr(msa_preparation, 'prepare_remote_protenix_inputs', lambda *_: pytest.fail('No-MSA must not prepare'))
    job.params['protenix_use_msa'] = False
    result = controller_bundle(job=job, target=target)
    # Static MSA-only prediction does not need a dynamic-child context.
    assert [Path(t.remote_destination).name for t in result.input_transfers] == [
        'portable-input-bindings.json', 'component-resources.config']
    assert not any('prepared-msa/' in r.relative_path for r in result.envelope.files)
    assert '--protenix_prepared_msa_dir' not in result.envelope.command


def test_offline_api_generation_is_packaged_by_bundle(offline_bundle, tmp_path, monkeypatch):
    _, job, target, cached = offline_bundle
    cached.unlink()
    calls = []
    def offline_provider(*, sequences, params):
        calls.append(sequences)
        msa = tmp_path / 'fixture-provider.a3m'
        msa.write_bytes(A3M)
        return {'provider': 'colabfold_api', 'request_digest': 'fixture-not-service',
                'cache_hit': False, 'provenance': {'fixture': True},
                'artifacts': [{'chain_index': 0, 'role': 'unpaired', 'path': str(msa),
                               'sha256': hashlib.sha256(A3M).hexdigest()}]}
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', offline_provider)
    prepared = controller_bundle(job=job, target=target)
    assert calls == [[SEQUENCE]]
    receipt = json.loads((prepared.input_transfers[0].source/'msa-inputs.json').read_text())
    assert receipt['provenance']['backend'] == 'colabfold_api'
    assert receipt['provenance']['fixture'] is True
    assert not Path(target.remote_root).exists()


@pytest.mark.parametrize('fanout', ['single', 'sequence_batch', 'complex'])
def test_raw_protenix_request_uses_precompiled_neurosnap_inputs(offline_bundle, monkeypatch, fanout):
    from services.model_msa_handoff import prepare_launch_msa
    from prepare_protenix_msa import load_native_protenix_input
    _, job, _, cached = offline_bundle
    wrong_job = SimpleNamespace(**{**vars(job), 'model_id': 'boltz2', 'mode': 'predict',
                                   'params': {'sequence': SEQUENCE, 'boltz_use_msa': False}})
    wrong_invocation = nextflow.compile_job_nextflow_invocation(wrong_job, wrong_job.params, job.output_dir)
    cached.unlink()  # Never turn legacy query-only cache into provider evidence.
    sequence = 'MKTLLILAVVAAALAGGGGGG'
    job.params.update(sequence=sequence, msa_provider='neurosnap_api', protenix_msa_backend='neurosnap_api',
                      protenix_seeds='0,42', msa_neurosnap_coverage_percent=40,
                      msa_neurosnap_identity_percent=55, msa_neurosnap_max_sequences=100)
    if fanout == 'sequence_batch':
        job.params['sequence_batch_entries'] = [
            {'name': 'first', 'sequence': sequence}, {'name': 'second', 'sequence': 'ACDEFGHIKLMNPQRSTVWYA'}]
    elif fanout == 'complex':
        job.mode = 'complex'
        job.params['complex_components'] = [
            {'id': 'B', 'type': 'protein', 'sequence': sequence, 'count': 2},
            {'id': 'A', 'type': 'protein', 'sequence': 'ACDEFGHIKLMNPQRSTVWYA', 'count': 1},
            {'id': 'L', 'type': 'ligand', 'smiles': 'CCO', 'count': 1}]
    invocation = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    invocation.materialize_inputs(Path(job.output_dir))
    native = load_native_protenix_input({**job.params, **invocation.native_parameters})
    raw_before = json.dumps(job.params, sort_keys=True)
    calls = []
    def provider(*, sequences, params):
        calls.append((list(sequences), dict(params)))
        artifacts = []
        for index, sequence in enumerate(sequences):
            path = Path(job.output_dir) / f'fixture-{len(calls)}-{index}.a3m'
            path.write_text(f'>query\n{sequence}\n')
            artifacts.append(dict(chain_index=index, role='unpaired', path=str(path),
                                  sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        return dict(provider='neurosnap_api', request_digest='fixture', cache_hit=False,
                    provenance={'fixture': True}, artifacts=artifacts)
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', provider)
    monkeypatch.setattr(nextflow, 'compile_job_nextflow_invocation', lambda *a, **k: pytest.fail('second compilation'))
    destination = Path(job.output_dir) / 'prepared'
    prepared = prepare_launch_msa('protenix', job.params, destination, native_invocation=invocation)
    assert json.dumps(job.params, sort_keys=True) == raw_before
    manifest = json.loads((destination / 'msa-inputs.json').read_text())
    assert [t['name'] for t in manifest['model_input']] == [t['name'] for t in native]
    assert [c[0] for c in calls] == [[c['proteinChain']['sequence'] for c in t['sequences'] if 'proteinChain' in c] for t in native]
    assert all(c[1]['msa_neurosnap_coverage_percent'] == 40 for c in calls)
    assert manifest['settings']['protenix_n_sample'] == 3
    assert all(t['modelSeeds'] == [0, 42] for t in manifest['model_input'])
    worker = Path(job.output_dir) / 'worker'
    destination.rename(worker)
    output = msa_preparation.materialize_protenix_inputs(worker, worker / 'hydrated.json', manifest['settings'])
    tasks = json.loads(output.read_text())
    for expected, actual in zip(native, tasks):
        assert expected['name'] == actual['name']
        for original, component in zip(expected['sequences'], actual['sequences']):
            if 'proteinChain' not in original:
                assert original == component
                continue
            chain = component['proteinChain']
            for field in ('sequence', 'count', 'id'):
                assert chain.get(field) == original['proteinChain'].get(field)
            assert Path(chain['unpairedMsaPath']).is_relative_to(worker)
            assert 'pairedMsaPath' not in chain
    with pytest.raises(ValueError, match='model mismatch'):
        prepare_launch_msa('protenix', job.params, Path(job.output_dir) / 'rejected',
                           native_invocation=wrong_invocation)
    assert not (Path(job.output_dir) / 'rejected').exists()
    assert prepared['protenix_prepared_msa_sha256'] == hashlib.sha256((worker / 'msa-inputs.json').read_bytes()).hexdigest()


def test_native_compile_requires_compiled_batch_and_preserves_seeds(tmp_path):
    from prepare_protenix_msa import load_native_protenix_input
    with pytest.raises(ValueError, match='compiled native complex/batch'):
        load_native_protenix_input({'sequence_batch_entries': [{'name': 'raw', 'sequence': SEQUENCE}]})
    source = tmp_path / 'batch.json'
    source.write_text(json.dumps([{'name': 'variant_001_original', 'sequence': SEQUENCE},
                                  {'name': 'variant_002_original', 'sequence': 'ACDE'}]))
    tasks = load_native_protenix_input({'sequence_batch_json_path': str(source), 'protenix_seeds': '7,19'})
    assert [task['name'] for task in tasks] == ['variant_001_original', 'variant_002_original']
    assert [task['modelSeeds'] for task in tasks] == [[7,19], [7,19]]
    assert nextflow.compile_controller_protenix_input({'sequence_input': SEQUENCE, 'protenix_seeds': '7,19'})[0]['modelSeeds'] == [7,19]


@pytest.mark.parametrize('native_mode,geometry_mode', [
    ('single', 'flexible'), ('complex', 'flexible'),
    ('complex', 'conditioned'), ('complex', 'frozen'),
])
@pytest.mark.parametrize('provider_name', ['colabfold_api', 'neurosnap_api'])
def test_real_nextflow_stages_handoff_with_science_stub(offline_bundle, tmp_path, monkeypatch, native_mode, geometry_mode, provider_name):
    import os
    _, job, target, cached = offline_bundle
    if provider_name == 'neurosnap_api':
        cached.unlink()
        job.params.update(msa_provider=provider_name, protenix_msa_backend=provider_name,
                          msa_neurosnap_coverage_percent=40, msa_neurosnap_identity_percent=55,
                          msa_neurosnap_max_sequences=100)
        def provider(*, sequences, params):
            assert sequences == [SEQUENCE]
            assert params['msa_neurosnap_coverage_percent'] == 40
            path = tmp_path/'neurosnap-fixture.a3m'
            path.write_bytes(A3M)
            return dict(provider=provider_name, request_digest='offline-fixture', cache_hit=False,
                        provenance={'fixture': True}, artifacts=[dict(chain_index=0,
                            role='unpaired', path=str(path), sha256=hashlib.sha256(A3M).hexdigest())])
        monkeypatch.setattr(msa_preparation, 'prepare_model_msa', provider)
    prepared = controller_bundle(job=job, target=target)
    msa = prepared.input_transfers[0].source
    sha = hashlib.sha256((msa/'msa-inputs.json').read_bytes()).hexdigest()
    jar = Path.home()/'.nextflow/framework/25.10.1/nextflow-25.10.1-one.jar'
    if not jar.is_file() or not shutil.which('java'):
        pytest.skip('Installed offline Nextflow 25.10.1/Java required for DSL process proof')
    # Only replace the science execution with a declared Nextflow stub. The
    # production input declaration/staging and complete production DSL compile.
    stub = '''    stub:
    """
    # Declared science placeholders satisfy unchanged mandatory process outputs.
    # They are not valid structures or scientific acceptance evidence.
    mkdir -p producer_publication/fixture predictions/fixture
    printf '{"fixture":"science-stub"}' > producer_publication/fixture/receipt.json
    printf '{"fixture":"science-stub"}' > producer_candidates.json
    printf 'SCIENCE_STUB_RAW_CIF' > predictions/fixture/model.cif
    printf 'SCIENCE_STUB_FINAL_PDB' > predictions/fixture/model.pdb
    cp '${params.fixture_input}' input.json
    PYTHONPATH='${params.deny_dir}' '${params.fixture_python}' '${params.code_root}/scripts/prepare_protenix_msa.py' --input_json input.json --output_json prepared.json --out_dir msa_prepared --report_json msa_prepared/msa_report.json --prepared-inputs '${prepared_msa}' --prepared-sha256 '${params.protenix_prepared_msa_sha256}'
    """

    script:
'''
    module = (ROOT/'modules/protenix.nf').read_text()
    if native_mode == 'single':
        module = module.replace('    script:\n', stub, 1)
    else:
        # Retain the actual CPU native constructor and both declared path
        # tuples; replace only ProtenixFromComplex inference with a CLI stub.
        start = module.index('process ProtenixFromComplex {')
        complex_stub = stub.replace("${params.fixture_input}", "${complex_json}")
        module = module[:start] + module[start:].replace('    script:\n', complex_stub, 1)
    (tmp_path/'protenix.nf').write_text(module)
    (tmp_path/'main.nf').write_text("""nextflow.enable.dsl=2
include { ProtenixPredict } from './protenix.nf'
workflow {
    ProtenixPredict(Channel.of(tuple([producer_stage:'fixture'], params.sequence_input, 'fixture', file(params.protenix_prepared_msa_dir, checkIfExists:true))))
}
""")
    if native_mode == 'complex':
        (tmp_path/'main.nf').write_text("""nextflow.enable.dsl=2
include { PrepProtenixComplex; ProtenixFromComplex } from './protenix.nf'
workflow {
    PrepProtenixComplex(Channel.of(tuple('fixture', file(params.fixture_complex), file(params.fixture_no_msa), file(params.protenix_prepared_msa_dir, checkIfExists:true))))
    ProtenixFromComplex(PrepProtenixComplex.out.protenix_json)
}
""")
        (tmp_path/'complex.json').write_text(json.dumps({'components': [{'type': 'protein', 'sequence': SEQUENCE}]}))
        (tmp_path/'NO_MSA').write_text('')
    input_json = tmp_path/'native-input.json'
    input_json.write_text(json.dumps(nextflow.compile_controller_protenix_input(
        {'sequence_input': SEQUENCE, 'protenix_seeds': job.params['protenix_seeds']})))
    deny = tmp_path/'deny'
    deny.mkdir()
    (deny/'sitecustomize.py').write_text("import socket\ndef denied(*a,**k): raise RuntimeError('OUTBOUND_DENIED')\nsocket.socket.connect=denied\nsocket.socket.connect_ex=denied\nsocket.create_connection=denied\n")
    parameters = {'fixture_input': str(input_json), 'fixture_python': sys.executable,
                  'code_root': str(ROOT), 'out_dir': str(tmp_path/'out'), 'deny_dir': str(deny),
                  'protenix_prepared_msa_dir': str(msa), 'protenix_prepared_msa_sha256': sha,
                  'sequence_input': SEQUENCE, 'msa_cache_dir': str(tmp_path/'unused-cache'),
                  'protenix_seeds': job.params['protenix_seeds'],
                  'protenix_target_geometry_mode': geometry_mode,
                  'fixed_target_source_path': 'fixture-target-not-executed.pdb',
                  'fixed_target_source_chains': 'A', 'target_chains': 'A'}
    if native_mode == 'complex':
        parameters.update(fixture_complex=str(tmp_path/'complex.json'), fixture_no_msa=str(tmp_path/'NO_MSA'))
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
    hydrated = json.loads((reports[0].parent.parent/'prepared.json').read_text())
    assert hydrated[0]['modelSeeds'] == [42, 71]
    chain = hydrated[0]['sequences'][0]['proteinChain']
    assert Path(chain['unpairedMsaPath']).read_bytes() == A3M
    if provider_name == 'neurosnap_api':
        assert 'pairedMsaPath' not in chain
    assert list((tmp_path/'work').rglob('prepared-msa/msa-inputs.json'))
    finalized = native_mode == 'complex' and geometry_mode != 'flexible'
    canonical = tmp_path/'out/pdb_files/predictions'
    assert sorted(p.name for p in canonical.iterdir()) == ['model.pdb' if finalized else 'model.cif']
    raw = tmp_path/'out/run/protenix_complex/raw/predictions/fixture/model.cif'
    assert raw.exists() == finalized
    if finalized:
        assert raw.read_text() == 'SCIENCE_STUB_RAW_CIF'
        assert (canonical/'model.pdb').read_text() == 'SCIENCE_STUB_FINAL_PDB'


def test_nextflow_stages_declared_handoff_as_process_path():
    module = (ROOT/'modules/protenix.nf').read_text()
    workflow = (ROOT/'modules/structure_prediction.nf').read_text()
    assert 'path(prepared_msa)' in module
    assert '--prepared-inputs' in module and '--prepared-sha256' in module
    assert workflow.count('file(params.protenix_prepared_msa_dir, checkIfExists: true)') == 5
    assert 'tuple val(name), path(complex_json), path(msa_file), path(prepared_msa)' in module
    assert 'tuple val(name), path("protenix_input.json"), path(prepared_msa)' in module
    assert 'tuple val(input_sample), path(complex_json), path(prepared_msa)' in module
    assert module.count('--prepared-inputs') == 2
    plr = (ROOT/'workflows/protein_local_redesign.nf').read_text()
    assert 'tuple(producerMeta, protenixJson, params.protenix_prepared_msa_dir ? file(' in plr


def test_controller_does_not_treat_query_only_cache_as_provider_evidence(tmp_path, monkeypatch):
    cache = tmp_path / 'legacy-cache'
    sha = hashlib.sha256(SEQUENCE.encode()).hexdigest()
    cached = cache / sha[:2] / f'{sha}.a3m.gz'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(gzip.compress(A3M))
    calls = []
    def missing(*, sequences, params):
        calls.append(sequences)
        raise RuntimeError('no verified provider/settings/query-bound cached MSA')
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', missing)
    with pytest.raises(RuntimeError, match='no verified provider/settings/query-bound'):
        msa_preparation.prepare_remote_protenix_inputs({'sequence_input': SEQUENCE,
            'msa_cache_dir': str(cache), 'msa_cache_only': True}, tmp_path / 'portable')
    assert calls == [[SEQUENCE]]
    assert not (tmp_path / 'portable/msa-inputs.json').exists()

@pytest.mark.parametrize('fanout', ['sequence_batch', 'complex_batch'])
def test_boltz_compiled_batch_to_relocated_native_consumer(offline_bundle, monkeypatch, fanout):
    from biomodstack_boltz_msa import hydrate_prepared_boltz_components, hydrate_prepared_boltz_task
    from services.model_msa_handoff import prepare_launch_msa
    from biomodstack_msa_handoff import digest
    _, job, _, _ = offline_bundle
    job.model_id = 'boltz2'
    job.mode = 'complex' if fanout == 'complex_batch' else 'predict'
    job.params = {'sequence': SEQUENCE, 'boltz_use_msa': True,
                  'msa_provider': 'colabfold_api', 'colabfold_pairing_mode': 'unpaired',
                  'sequence_batch_entries': [{'name': 'first', 'sequence': SEQUENCE},
                                             {'name': 'second', 'sequence': SEQUENCE}]}
    if fanout == 'complex_batch':
        job.params['complex_components'] = [
            {'id': 'B', 'type': 'protein', 'sequence': SEQUENCE},
            {'id': 'A', 'type': 'protein', 'sequence': SEQUENCE},
            {'id': 'L', 'type': 'ligand', 'ccd': 'ATP'}]
        job.params['sequence_batch_component_id'] = 'A'
    calls = []
    def prepared_provider(*, sequences, params):
        calls.append(list(sequences))
        artifacts = []
        for index, sequence in enumerate(sequences):
            path = Path(job.output_dir) / f'fixture-{len(calls)}-{index}.a3m'
            path.write_text(f'>query\n{sequence}\n')
            artifacts.append({'chain_index': index, 'role': 'unpaired', 'path': str(path),
                              'sha256': digest(path.read_bytes())})
        return {'provider': 'colabfold_api', 'request_digest': f'fixture-{len(calls)}',
                'cache_hit': True, 'provenance': {'fixture': True}, 'artifacts': artifacts}
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', prepared_provider)
    original = nextflow.compile_job_nextflow_invocation(job, job.params, job.output_dir)
    original.materialize_inputs(Path(job.output_dir))
    batch_path = Path(original.native_parameters['sequence_batch_json_path'])
    entries = json.loads(batch_path.read_text())
    snapshot = batch_path.read_bytes()
    prepared = prepare_launch_msa('boltz2', job.params, Path(job.output_dir) / 'prepared-boltz',
                                  native_invocation=original)
    assert batch_path.read_bytes() == snapshot
    host = Path(prepared['boltz_prepared_msa_dir'])
    manifest = json.loads((host / 'msa-inputs.json').read_text())
    assert [t['name'] for t in manifest['tasks']] == [entry['name'] for entry in entries]
    assert len(calls) == len(entries)
    relocated = Path(job.output_dir) / 'worker-boltz'
    shutil.move(str(host), relocated)
    for entry in entries:
        if fanout == 'complex_batch':
            native = json.loads(Path(entry['complex_json']).read_text())
            bound = hydrate_prepared_boltz_components(native, relocated,
                prepared['boltz_prepared_msa_sha256'], task_name=entry['name'])
            for component in bound['components']:
                if component['type'] == 'protein':
                    assert Path(component['msa_path']).is_relative_to(relocated)
        else:
            native = {'sequences': [{'protein': {'id': ['A'], 'sequence': entry['sequence']}}]}
            bound = hydrate_prepared_boltz_task(native, relocated,
                prepared['boltz_prepared_msa_sha256'], task_name=entry['name'])
            assert Path(bound['sequences'][0]['protein']['msa']).is_relative_to(relocated)


@pytest.mark.asyncio
@pytest.mark.parametrize('fanout', ['single', 'sequence_batch', 'complex'])
async def test_precompiled_roster_is_reused_for_msa_and_sealed_launch(offline_bundle, monkeypatch, fanout):
    from unittest.mock import AsyncMock
    from services.model_msa_handoff import prepare_launch_msa
    from scripts.prepare_protenix_msa import load_native_protenix_input
    _, job, _, _ = offline_bundle
    if fanout == 'sequence_batch':
        job.params['sequence_batch_entries'] = [
            {'name': 'first', 'sequence': SEQUENCE}, {'name': 'second', 'sequence': SEQUENCE}]
    elif fanout == 'complex':
        job.mode = 'complex'
        job.params['complex_components'] = [
            {'id': 'A', 'type': 'protein', 'sequence': SEQUENCE, 'count': 2},
            {'id': 'B', 'type': 'ligand', 'smiles': 'CCO', 'count': 1}]
    job.params['protenix_seeds'] = '0,42'
    original = nextflow.compile_job_nextflow_invocation(job=job, params=job.params, output_dir=job.output_dir)
    original.materialize_inputs(Path(job.output_dir))
    msa_params = {**job.params, **original.native_parameters}
    native = load_native_protenix_input(msa_params)
    assert len(native) == (2 if fanout == 'sequence_batch' else 1)
    assert all(task['modelSeeds'] == [0, 42] for task in native)
    prepared = prepare_launch_msa('protenix', msa_params, Path(job.output_dir)/'prepared-msa')
    launch_params = {**job.params, **{key: prepared[key] for key in
        ('protenix_prepared_msa_dir', 'protenix_prepared_msa_sha256')}}
    monkeypatch.setattr(nextflow, 'compile_job_nextflow_invocation',
                        lambda **kw: pytest.fail('second scientific compilation'))
    sealed = await nextflow._compile_launch_nextflow_invocation(
        SimpleNamespace(commit=AsyncMock()), job, launch_params, job.output_dir,
        prepared_invocation=original)
    assert sealed.requested_json == original.requested_json
    assert sealed.effective_json == original.effective_json
    assert sealed.generated_inputs == original.generated_inputs
    assert sealed.native_parameters['protenix_prepared_msa_sha256'] == prepared['protenix_prepared_msa_sha256']
