"""Offline execution proofs: no provider account, public requests, or science."""
import concurrent.futures
import importlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
from biomodstack_msa_controller import prepare, require_controller_submission, PUBLIC_HOST
from biomodstack_msa_handoff import validate_a3m
from services.msa_preparation import export_protenix_inputs, materialize_protenix_inputs


@pytest.fixture
def controller(tmp_path):
    config = tmp_path / 'controller.json'
    config.write_text(json.dumps({'role': 'msa_controller',
        'machine_id': Path('/etc/machine-id').read_text().strip(),
        'qualified_single_egress': True, 'egress_identity': 'offline-fixture-not-network-proof',
        'state_dir': str(tmp_path / 'state')}))
    return config


def test_serial_queue_and_idempotent_replay(controller):
    active = 0
    peak = 0
    calls = []
    mutex = threading.Lock()
    def operation(i):
        nonlocal active, peak
        require_controller_submission()
        with mutex:
            active += 1
            peak = max(peak, active)
        time.sleep(.015)
        with mutex:
            active -= 1
            calls.append(i)
        return {'index': i}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: prepare(controller, {'i': i}, lambda: operation(i)), range(4)))
    assert peak == 1
    assert len(calls) == 4
    assert results == [{'index': i} for i in range(4)]
    assert prepare(controller, {'i': 1}, lambda: pytest.fail('duplicate submit')) == {'index': 1}
    with pytest.raises(RuntimeError, match='controller-only'):
        require_controller_submission()


@pytest.mark.parametrize('error', [TimeoutError('timeout'), RuntimeError('429'), KeyboardInterrupt()])
def test_failed_cancelled_or_interrupted_operation_blocks_replay(controller, error):
    def fail():
        raise error
    with pytest.raises(type(error)):
        prepare(controller, {'sequence_sha256': 'fixture'}, fail)
    with pytest.raises(RuntimeError, match='reconciliation'):
        prepare(controller, {'different': 'job'}, lambda: pytest.fail('resubmission'))
    state = json.loads((controller.parent / 'state' / 'active.json').read_text())
    assert state['status'] == 'blocked_reconciliation'


def _process_probe(config, index):
    def operation():
        require_controller_submission()
        start = time.monotonic()
        time.sleep(.02)
        return [start, time.monotonic()]
    return prepare(Path(config), {'process': index}, operation)


def test_serial_lock_across_processes(controller):
    import multiprocessing
    with concurrent.futures.ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context('fork')) as pool:
        futures = [pool.submit(_process_probe, str(controller), i) for i in range(3)]
        intervals = sorted(f.result() for f in futures)
    assert all(left[1] <= right[0] for left, right in zip(intervals, intervals[1:]))


def test_wrong_computer_denied(controller):
    config = json.loads(controller.read_text())
    config['machine_id'] = 'different-worker'
    controller.write_text(json.dumps(config))
    with pytest.raises(RuntimeError, match='controller-only'):
        prepare(controller, {}, lambda: pytest.fail('worker submit'))


@pytest.mark.parametrize('module', ['run_local_msa', 'lib.local_msa.providers.colabfold_api'])
def test_worker_provider_entry_denied_before_io(module, monkeypatch, tmp_path):
    provider = importlib.import_module(module)
    monkeypatch.setattr(provider, '_http_post_form_json', lambda **kw: pytest.fail('network'))
    with pytest.raises(RuntimeError, match='controller-only'):
        provider._run_colabfold_api_search('AAAA', tmp_path, PUBLIC_HOST, str(tmp_path), True, True, 0, 0)


def test_protenix_worker_denied_before_model_import(tmp_path):
    from prepare_protenix_msa import prepare_with_colabfold_api
    with pytest.raises(RuntimeError, match='controller-only'):
        prepare_with_colabfold_api(tmp_path / 'in', tmp_path / 'out', tmp_path, PUBLIC_HOST)


def test_controller_endpoint_substitution_denied(controller):
    with pytest.raises(ValueError, match='substitution'):
        prepare(controller, {}, lambda: require_controller_submission('https://alternate.invalid'))


def test_controller_service_packages_shared_api_result(controller, tmp_path, monkeypatch):
    import hashlib
    from services import msa_preparation
    source = tmp_path / 'input.json'
    source.write_text(json.dumps([{'name': 'fixture', 'sequences': [
        {'proteinChain': {'sequence': 'AAAA', 'count': 1}}]}]))
    calls = []
    alignment = tmp_path / 'api-fixture.a3m'
    alignment.write_text('>q\nAAAA\n>fixture\nAA-A\n')
    def api_result(*, sequences, params):
        calls.append(sequences)
        return {'provider': 'colabfold_api', 'request_digest': 'fixture',
                'cache_hit': False, 'provenance': {'fixture': True},
                'artifacts': [{'chain_index': 0, 'role': 'unpaired', 'path': str(alignment),
                               'sha256': hashlib.sha256(alignment.read_bytes()).hexdigest()}]}
    monkeypatch.setattr(msa_preparation, 'prepare_model_msa', api_result)
    destination = tmp_path / 'prepared'
    result = msa_preparation.prepare_protenix_inputs(controller, source, destination,
                                                     {'protenix_msa_backend': 'auto'})
    assert result['settings']['protenix_msa_backend'] == 'colabfold_api'
    assert result['artifacts'][0]['role'] == 'unpaired'
    assert calls == [['AAAA']]
    assert result['provenance']['backend'] == 'colabfold_api'


def test_real_protenix_handoff_relocated_and_consumed(tmp_path, monkeypatch):
    import prepare_protenix_msa as adapter
    from types import SimpleNamespace
    host = tmp_path / 'controller-root'
    worker = tmp_path / 'different-worker-root'
    a3m = tmp_path / 'original.a3m'
    a3m.write_text('>query\nAAAA\n>hit\nAA-A\n')
    payload = [{'name': 'input', 'sequences': [{'proteinChain': {
        'sequence': 'AAAA', 'count': 2, 'unpairedMsaPath': str(a3m), 'custom_metadata': 'retained'}}]}]
    settings = {'protenix_use_msa': True, 'protenix_msa_backend': 'colabfold_api', 'pairing': 'unpaired'}
    manifest = export_protenix_inputs(payload, host, settings, {'backend': 'supplied'})
    assert 'unpairedMsaPath' not in manifest['model_input'][0]['sequences'][0]['proteinChain']
    shutil.copytree(host, worker)
    shutil.rmtree(host)
    a3m.unlink()
    runtime_input = materialize_protenix_inputs(worker, worker / 'input.json', settings)
    output = worker / 'output.json'
    monkeypatch.setattr(adapter, 'parse_args', lambda: SimpleNamespace(backend='auto', input_json=str(runtime_input),
        output_json=str(output), out_dir=str(worker / 'work'), cache_dir=None, report_json=None))
    monkeypatch.setattr(adapter, 'prepare_with_colabfold_api', lambda **kw: pytest.fail('worker search'))
    adapter.main()
    chain = json.loads(output.read_text())[0]['sequences'][0]['proteinChain']
    assert chain['count'] == 2 and chain['custom_metadata'] == 'retained'
    assert Path(chain['unpairedMsaPath']).is_relative_to(worker)
    assert Path(chain['unpairedMsaPath']).read_text() == '>query\nAAAA\n>hit\nAA-A\n'
    with pytest.raises(ValueError, match='settings'):
        materialize_protenix_inputs(worker, output, {'pairing': 'changed'})
    Path(chain['unpairedMsaPath']).write_text('>query\nAAAA\n>hit\nAAAA\n')
    with pytest.raises(ValueError, match='digest'):
        materialize_protenix_inputs(worker, output, settings)


def test_mismatched_query_and_missing_cache_do_not_become_no_msa(tmp_path):
    import prep_boltz_with_msa as boltz
    from types import SimpleNamespace
    wrong = tmp_path / 'wrong.a3m'
    wrong.write_text('>q\nBBBB\n')
    with pytest.raises(ValueError, match='identity'):
        validate_a3m(wrong, 'AAAA')
    wrong.write_text('>q\nAAAA\n>bad\nAaAA\n')
    with pytest.raises(ValueError, match='alignment row'):
        validate_a3m(wrong, 'AAAA')
    with pytest.raises(RuntimeError, match='controller-only'):
        boltz.get_msa_for_sequence('PEPTIDE', 'test', SimpleNamespace(cache_dir=str(tmp_path), msa_output=str(tmp_path / 'out')))
    with pytest.raises(ValueError, match='no implicit'):
        boltz.generate_yaml_with_msa('test', {'PEPTIDE': ['A']}, {})


@pytest.mark.parametrize('module', ['run_local_msa', 'lib.local_msa.providers.colabfold_api'])
def test_real_provider_control_flow_uses_one_ticket_with_offline_transport(module, controller, tmp_path, monkeypatch):
    provider = importlib.import_module(module)
    submitted = []
    monkeypatch.setattr(provider, '_wait_for_colabfold_submit_slot', lambda **kw: None)
    def submit(**kw):
        submitted.append(kw)
        return {'id': 'offline-fixture-ticket', 'status': 'UNKNOWN'}
    monkeypatch.setattr(provider, '_http_post_form_json', submit)
    monkeypatch.setattr(provider, '_http_get_json', lambda **kw: {'status': 'COMPLETE'})
    monkeypatch.setattr(provider, '_http_download_file', lambda **kw: None)
    def extract(**kw):
        (tmp_path / 'uniref.a3m').write_text('>101\nAAAA\n>fixture\nAA-A\n')
    monkeypatch.setattr(provider, '_extract_tar_archive_safely', extract)
    result = prepare(controller, {'test': module}, lambda: provider._run_colabfold_api_search(
        'AAAA', tmp_path, PUBLIC_HOST, str(tmp_path), False, True, 0, 0))
    assert len(submitted) == 1
    assert submitted[0]['form_data']['q'] == '>101\nAAAA\n'
    assert result['ticket_id'] == 'offline-fixture-ticket'
    receipts = [json.loads(p.read_text()) for p in (controller.parent / 'state').glob('*.json')]
    assert receipts[0]['tickets'][0]['ticket_id'] == 'offline-fixture-ticket'


def test_portable_manifest_traversal_rejected(tmp_path):
    from biomodstack_msa_handoff import resolve_alignments
    with pytest.raises(ValueError, match='portable'):
        resolve_alignments(tmp_path, {'schema': 'bms.msa-inputs.v1', 'settings': {},
            'artifacts': [{'path': '../escape'}]}, sequences={}, settings={})
