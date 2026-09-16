"""Real worker sealing/return owners, isolated files and controlled boot epochs."""
import json
from pathlib import Path

import pytest

from tools import bms_remote_worker as worker


def attempt(tmp_path, state='running', *, reboot=True):
    source = tmp_path / 'bundle/source'
    source.mkdir(parents=True)
    archive = source / '.bms-source.tar'
    archive.write_bytes(b'isolated source archive fixture')
    output = tmp_path / 'results'
    output.mkdir()
    envelope = dict(schema='bms.remote-execution.v1', job_id='job', attempt_id='attempt',
        source_revision='a'*40, source_tree='b'*40,
        source_archive_sha256=worker.sha256_file(archive),
        files=[dict(relative_path='source/.bms-source.tar', size_bytes=archive.stat().st_size,
            sha256=worker.sha256_file(archive), mode=0o644)],
        working_directory=str(Path(__file__).resolve().parents[3]), output_directory=str(output),
        command=['never-run-science'], environment={'API_TOKEN': 'private-fixture-value'})
    worker.atomic_json(worker.envelope_path(tmp_path), envelope)
    value = worker.base_status(envelope, state)
    if reboot:
        value['boot_id'] = 'previous-boot'
    worker.atomic_json(worker.status_path(tmp_path), value)
    (tmp_path / 'nextflow.log').write_text('failure API_TOKEN=private-fixture-value\n')
    (tmp_path / 'unrelated-secret.log').write_text('must not sweep')
    (output / 'native.pdb').write_text('retained native bytes')
    return envelope


@pytest.mark.parametrize('state,reboot', [('running', True), ('prepared', False)])
def test_reboot_and_prelaunch_cancel_seal_immutable_diagnostics(tmp_path, monkeypatch, state, reboot):
    envelope = attempt(tmp_path, state, reboot=reboot)
    if not reboot:
        worker.atomic_json(tmp_path / worker.CANCEL_REQUEST_FILE, {'attempt_id': 'attempt'})
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('science rerun'))
    result = worker.status(tmp_path)
    assert result['state'] == ('lost' if reboot else 'cancelled')
    assert result['quiescent'] is True
    manifest_path = Path(envelope['output_directory']) / worker.RESULT_MANIFEST_FILE
    assert result['result_manifest_sha256'] == worker.sha256_file(manifest_path)
    manifest = worker.load_json(manifest_path)
    assert manifest['execution_envelope_sha256'] == worker.sha256_file(worker.envelope_path(tmp_path))
    assert (manifest['attempt_id'], manifest['source_revision'], manifest['source_tree']) == ('attempt', 'a'*40, 'b'*40)
    assert manifest['exit_code'] != 0
    assert (Path(envelope['output_directory']) / '_remote/recovery/nextflow.log').read_text() == 'failure API_TOKEN=[REDACTED]\n'
    assert not any('unrelated-secret' in row['relative_path'] for row in manifest['artifacts'])
    before = manifest_path.read_bytes()
    assert worker.status(tmp_path) == result
    assert worker.start(tmp_path) == result
    assert manifest_path.read_bytes() == before


def test_recovery_filters_inherited_known_secrets(tmp_path, monkeypatch):
    envelope = attempt(tmp_path)
    monkeypatch.setenv('FIXTURE_API_TOKEN', 'ambient-private-fixture')
    (tmp_path / 'nextflow.log').write_text('ambient-private-fixture\nprivate-fixture-value\n')
    worker.status(tmp_path)
    log = Path(envelope['output_directory']) / '_remote/recovery/nextflow.log'
    assert log.read_text() == '[REDACTED]\n[REDACTED]\n'


def test_same_boot_uncertain_writer_never_seals(tmp_path):
    envelope = attempt(tmp_path, reboot=False)
    result = worker.status(tmp_path)
    assert result['state'] == 'lost' and result['quiescent'] is False
    assert not (Path(envelope['output_directory']) / worker.RESULT_MANIFEST_FILE).exists()


@pytest.mark.parametrize('boundary', ['terminal', 'copy', 'manifest'])
def test_interrupted_sealing_recovers_without_rewriting_terminal(tmp_path, monkeypatch, boundary):
    envelope = attempt(tmp_path)
    original_write = worker._write_atomic_json
    original_link = worker.os.link
    tripped = False
    def fail():
        nonlocal tripped
        if not tripped:
            tripped = True
            raise OSError('isolated interruption')
    def write(path, value):
        original_write(path, value)
        if (boundary == 'terminal' and path.name == worker.STATUS_FILE
                or boundary == 'manifest' and path.name == worker.RESULT_MANIFEST_FILE):
            fail()
    def link(source, destination):
        if boundary == 'copy':
            assert Path(source).read_bytes() == b'failure API_TOKEN=[REDACTED]\n'
            assert not Path(destination).exists()
            fail()
        return original_link(source, destination)
    monkeypatch.setattr(worker, '_write_atomic_json', write)
    monkeypatch.setattr(worker.os, 'link', link)
    with pytest.raises(OSError, match='isolated interruption'):
        worker.status(tmp_path)
    terminal = worker.load_json(worker.status_path(tmp_path))
    path = Path(envelope['output_directory']) / worker.RESULT_MANIFEST_FILE
    sealed = path.read_bytes() if path.exists() else None
    if boundary == 'copy':
        assert not (path.parent / '_remote/recovery/nextflow.log').exists()
    result = worker.status(tmp_path)
    assert result['state'] == terminal['state'] == 'lost'
    assert result['error'] == terminal['error']
    assert result['completed_at'] == terminal['completed_at']
    assert result['result_manifest_sha256'] == worker.sha256_file(path)
    if sealed is not None:
        assert path.read_bytes() == sealed


@pytest.mark.parametrize('boundary', ['copy', 'manifest'])
def test_process_death_during_recovery_leaves_retryable_publication(tmp_path, boundary):
    import multiprocessing
    import os
    envelope = attempt(tmp_path)
    def die():
        original = worker._write_atomic_json
        def write(path, value):
            original(path, value)
            if boundary == 'manifest' and path.name == worker.RESULT_MANIFEST_FILE:
                os._exit(73)
        def link(*args, **kwargs):
            os._exit(72)  # Complete private inode, never a partial final path.
        worker._write_atomic_json = write
        if boundary == 'copy':
            worker.os.link = link
        worker.status(tmp_path)
        os._exit(99)
    child = multiprocessing.get_context('fork').Process(target=die)
    child.start()
    child.join(10)
    if child.is_alive():
        child.kill()
        child.join(5)
        pytest.fail('recovery child did not exit')
    assert child.exitcode == (72 if boundary == 'copy' else 73)
    path = Path(envelope['output_directory']) / worker.RESULT_MANIFEST_FILE
    before = path.read_bytes() if path.exists() else None
    result = worker.status(tmp_path)
    assert result['state'] == 'lost' and result['quiescent'] is True
    assert result['result_manifest_sha256'] == worker.sha256_file(path)
    if before is not None:
        assert path.read_bytes() == before
    assert all('diagnostic-' not in row['relative_path'] for row in worker.load_json(path)['artifacts'])


@pytest.mark.parametrize('change', ['attempt', 'source', 'artifact', 'generation', 'path'])
def test_foreign_or_corrupt_sealed_manifest_is_not_adopted(tmp_path, monkeypatch, change):
    envelope = attempt(tmp_path)
    original = worker._write_atomic_json
    def write(path, value):
        original(path, value)
        if path.name == worker.RESULT_MANIFEST_FILE:
            raise OSError('after manifest')
    monkeypatch.setattr(worker, '_write_atomic_json', write)
    with pytest.raises(OSError):
        worker.status(tmp_path)
    monkeypatch.setattr(worker, '_write_atomic_json', original)
    path = Path(envelope['output_directory']) / worker.RESULT_MANIFEST_FILE
    manifest = worker.load_json(path)
    if change == 'artifact':
        (path.parent / 'native.pdb').write_text('modified science')
    elif change == 'path':
        manifest['artifacts'][0]['relative_path'] = '../foreign'
    else:
        manifest[{'attempt': 'attempt_id', 'source': 'source_tree', 'generation': 'generation'}[change]] = 'foreign'
    worker.atomic_json(path, manifest)
    before = path.read_bytes()
    with pytest.raises((RuntimeError, FileNotFoundError)):
        worker.status(tmp_path)
    assert path.read_bytes() == before
    assert not worker.load_json(worker.status_path(tmp_path))['result_manifest_sha256']


@pytest.mark.parametrize('change', ['attempt', 'schema', 'source', 'generation', 'output', 'boot'])
def test_malformed_attempt_cannot_publish_diagnostics(tmp_path, change):
    envelope = attempt(tmp_path)
    status = worker.load_json(worker.status_path(tmp_path))
    if change == 'attempt':
        status['attempt_id'] = 'foreign'
    elif change == 'generation':
        status['generation'] = True
    elif change == 'boot':
        status['boot_id'] = True
    elif change == 'output':
        status['native_output_directory'] = str(tmp_path / 'outside')
    else:
        envelope.pop('schema' if change == 'schema' else 'source_tree')
    worker.atomic_json(worker.envelope_path(tmp_path), envelope)
    worker._write_atomic_json(worker.status_path(tmp_path), status)
    with pytest.raises(RuntimeError):
        worker.status(tmp_path)
    assert not (tmp_path / 'results' / worker.RESULT_MANIFEST_FILE).exists()


def test_live_publisher_lock_blocks_sealing(tmp_path):
    import fcntl
    attempt(tmp_path)
    with (tmp_path / 'supervisor.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            worker.status(tmp_path)
        assert not (tmp_path / 'results' / worker.RESULT_MANIFEST_FILE).exists()
    assert worker.status(tmp_path)['result_manifest_sha256']


@pytest.mark.parametrize('terminal', ['failed', 'cancelled', 'lost'])
def test_existing_quiescent_terminal_keeps_original_reason_and_time(tmp_path, terminal):
    attempt(tmp_path, terminal, reboot=False)
    value = worker.load_json(worker.status_path(tmp_path))
    value.update(quiescent=True, exit_code=7, error='original native failure',
                 completed_at='2026-01-01T00:00:00Z')
    worker._write_atomic_json(worker.status_path(tmp_path), value)
    result = worker.status(tmp_path)
    assert result['state'] == terminal and result['exit_code'] == 7
    assert result['error'] == value['error'] and result['completed_at'] == value['completed_at']


@pytest.mark.parametrize('proof', ['valid', 'status-missing', 'root-missing'])
def test_paused_cancellation_requires_real_durable_root(tmp_path, monkeypatch, proof):
    from component_runtime import ComponentRuntime
    envelope = attempt(tmp_path, 'awaiting_input', reboot=False)
    output = Path(envelope['output_directory'])
    identity = dict(revision=envelope['source_revision'], tree=envelope['source_tree'])
    runtime = ComponentRuntime(tmp_path / 'ledger.sqlite', artifact_root=output,
        attempt_id='attempt', root_job_id='job', target_id='target', lease_id='lease', source_identity=identity)
    boot = worker.boot_id()
    runtime.claim_root(owner_id='former', boot_id=boot)
    runtime.set_root_state('paused', owner_id='former', boot_id=boot, quiescent=proof != 'root-missing')
    context = dict(attempt_id='attempt', root_job_id='job', target_id='target', lease_id='lease',
        source_identity=identity, artifact_root=str(output), ledger_path=str(tmp_path / 'ledger.sqlite'),
        working_directory=envelope['working_directory'])
    context_path = tmp_path / 'context.json'
    context_path.write_text(json.dumps(context))
    envelope['environment']['BMS_COMPONENT_CONTEXT'] = str(context_path)
    envelope['files'].append(dict(relative_path='inputs/component-context.json',
        size_bytes=context_path.stat().st_size, sha256=worker.sha256_file(context_path)))
    worker.atomic_json(worker.envelope_path(tmp_path), envelope)
    value = worker.load_json(worker.status_path(tmp_path))
    value['quiescent'] = proof != 'status-missing'
    worker._write_atomic_json(worker.status_path(tmp_path), value)
    worker.atomic_json(tmp_path / worker.CANCEL_REQUEST_FILE, {'attempt_id': 'attempt'})
    assert worker._component_checkpoint_runtime(envelope).root_state()['quiescent'] is (proof != 'root-missing')
    result = worker.status(tmp_path)
    if proof != 'valid':
        assert result['state'] == 'awaiting_input' and result['quiescent'] is False
        assert not result['result_manifest_sha256']
        return
    assert result['state'] == 'cancelled' and result['quiescent'] is True
    assert result['result_manifest_sha256']


@pytest.mark.parametrize('interrupted', [False, True])
def test_recovered_continuation_archives_prior_manifest_exactly(tmp_path, monkeypatch, interrupted):
    envelope = attempt(tmp_path, 'failed', reboot=False)
    value = worker.load_json(worker.status_path(tmp_path))
    value.update(quiescent=True, exit_code=9)
    worker._write_atomic_json(worker.status_path(tmp_path), value)
    first = worker.status(tmp_path)
    output = Path(envelope['output_directory'])
    previous = (output / worker.RESULT_MANIFEST_FILE).read_bytes()
    native = output / 'continued'
    native.mkdir()
    (native / 'partial.pdb').write_text('current generation')
    current = dict(first, generation=1, native_output_directory=str(native),
                   result_manifest_sha256=None, state='cancelled', exit_code=-15,
                   error='cancelled before continuation launch')
    worker._write_atomic_json(worker.status_path(tmp_path), current)
    original = worker._write_atomic_json
    def write(path, value):
        original(path, value)
        if interrupted and path == output / worker.RESULT_MANIFEST_FILE:
            raise OSError('after continuation manifest')
    monkeypatch.setattr(worker, '_write_atomic_json', write)
    if interrupted:
        with pytest.raises(OSError):
            worker.status(tmp_path)
        monkeypatch.setattr(worker, '_write_atomic_json', original)
    result = worker.status(tmp_path)
    assert result['generation'] == 1 and result['state'] == 'cancelled'
    assert result['error'] == current['error']
    assert (output / '_remote/result-manifests' / (first['result_manifest_sha256'] + '.json')).read_bytes() == previous
    manifest = worker.load_json(output / worker.RESULT_MANIFEST_FILE)
    assert manifest['generation'] == 1
    assert any(r['relative_path'] == 'continued/_remote/recovery/nextflow.log' for r in manifest['artifacts'])
    assert worker.status(tmp_path) == result
