"""Ephemeral offline worker processes; toy commands, never science/provider jobs."""
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import textwrap
import uuid

import pytest
from tools import bms_remote_worker as worker


OBSERVER_DRIVER = textwrap.dedent(r'''
    import json, os, sys
    from pathlib import Path
    from tools import bms_remote_worker as worker
    from services import resource_usage_evidence as resources
    attempt, fault = Path(sys.argv[1]), sys.argv[2]
    events = []
    class Monitor:
        @classmethod
        def from_remote_execution(cls, **kwargs):
            events.append('construct')
            if fault == 'construct': raise OSError('fixture monitor unavailable')
            return cls()
        def start(self):
            events.append('start')
            if fault == 'start': raise OSError('fixture start failed')
        def stop_sampling(self):
            events.append('stop')
            if fault == 'stop': raise OSError('fixture stop failed')
        def finish(self, **kwargs):
            events.append('finish')
            self.stop_sampling()
            if fault == 'finish': raise OSError('fixture finish failed')
            value = {'complete': fault != 'incomplete', 'fixture_only': True}
            if fault == 'serialize': value['invalid'] = object()
            return value
    resources.WorkflowResourceMonitor = Monitor
    original_open = Path.open
    def opened(path, *args, **kwargs):
        if path.name == '.bms-resource-usage.json' and args and args[0] == 'xb' and fault == 'open':
            events.append('open-failed')
            raise PermissionError('fixture optional receipt unavailable')
        return original_open(path, *args, **kwargs)
    Path.open = opened
    original_fsync = os.fsync
    def sync(fd):
        if fault == 'fsync' and os.readlink('/proc/self/fd/' + str(fd)).endswith('/.bms-resource-usage.json'):
            events.append('fsync-failed')
            raise OSError('fixture optional receipt fsync unavailable')
        return original_fsync(fd)
    os.fsync = sync
    if fault == 'unquiescent': worker.quiesce_writers = lambda *_: False
    try:
        code = worker.supervise(attempt)
    except Exception:
        if fault != 'unquiescent': raise
        code = 90
    finally:
        (attempt / 'observer-events.json').write_text(json.dumps(events))
    raise SystemExit(code)
''')


def prepared_attempt(tmp_path, *, fault, native_exit):
    attempt = tmp_path / 'attempt'
    source = attempt / 'bundle/source'
    source.mkdir(parents=True)
    archive = source / '.bms-source.tar'
    with tarfile.open(archive, 'w'):
        pass
    output = attempt / 'results'
    envelope = {'schema': 'bms.remote-execution.v1', 'job_id': 'fixture-job',
        'attempt_id': str(uuid.uuid4()), 'source_revision': 'a' * 40, 'source_tree': 'b' * 40,
        'source_archive_sha256': worker.sha256_file(archive),
        'files': [{'relative_path': 'source/.bms-source.tar', 'sha256': worker.sha256_file(archive),
                   'size_bytes': archive.stat().st_size, 'mode': archive.stat().st_mode & 0o777}],
        'command': [sys.executable, '-c', 'from pathlib import Path; import sys; '
                    + 'Path(' + repr(str(output / 'native-fixture.txt')) + ').write_text("offline fixture bytes"); '
                    + 'sys.exit(' + str(native_exit) + ')'],
        'working_directory': str(source), 'output_directory': str(output), 'environment': {}}
    if fault != 'absent':
        envelope['resource_monitor'] = {'params': {'_global_resource_admission': {
            'source_revision': envelope['source_revision'], 'source_tree': envelope['source_tree']}},
            'execution': {'generation': 0, 'attempt': 1,
                          'attempt_id': 'foreign' if fault == 'foreign' else envelope['attempt_id']}}
    worker.atomic_json(attempt / worker.ENVELOPE_FILE, envelope)
    worker.prepare(attempt)
    return attempt, output


@pytest.mark.parametrize('native_exit', [0, 23])
@pytest.mark.parametrize('fault', ['absent', 'foreign', 'construct', 'start', 'stop',
                                  'incomplete', 'finish', 'serialize', 'open', 'fsync', 'valid'])
def test_optional_observer_failures_preserve_native_exit_and_sealed_results(tmp_path, fault, native_exit):
    attempt, output = prepared_attempt(tmp_path, fault=fault, native_exit=native_exit)
    result = subprocess.run([sys.executable, '-c', OBSERVER_DRIVER, str(attempt), fault],
        capture_output=True, text=True, timeout=40, start_new_session=True)
    assert result.returncode == native_exit, (result.stdout, result.stderr)
    assert (output / 'native-fixture.txt').read_text() == 'offline fixture bytes'
    status = worker.load_json(attempt / worker.STATUS_FILE)
    manifest = worker.load_json(output / worker.RESULT_MANIFEST_FILE)
    assert status['state'] == ('succeeded' if native_exit == 0 else 'failed')
    assert status['quiescent'] is True
    assert status['exit_code'] == manifest['exit_code'] == native_exit
    assert status['result_manifest_sha256'] == worker.sha256_file(output / worker.RESULT_MANIFEST_FILE)
    assert any(row['relative_path'] == 'native-fixture.txt' for row in manifest['artifacts'])
    events = json.loads((attempt / 'observer-events.json').read_text())
    if fault in {'absent', 'foreign'}:
        assert events == []
    else:
        assert events[0] == 'construct'
        if fault != 'construct': assert 'start' in events
        if fault not in {'construct', 'start'}: assert 'finish' in events
    if fault == 'open': assert 'open-failed' in events
    if fault == 'fsync': assert 'fsync-failed' in events
    # Stand-in receipt checks only optional transport, not scientific metric schema.
    assert (output / '.bms-resource-usage.json').exists() == (fault == 'valid')


def test_optional_observer_does_not_waive_writer_quiescence(tmp_path):
    attempt, output = prepared_attempt(tmp_path, fault='unquiescent', native_exit=0)
    result = subprocess.run([sys.executable, '-c', OBSERVER_DRIVER, str(attempt), 'unquiescent'],
        capture_output=True, text=True, timeout=40, start_new_session=True)
    assert result.returncode == 90, (result.stdout, result.stderr)
    status = worker.load_json(attempt / worker.STATUS_FILE)
    assert status['state'] == 'running'
    assert not status['quiescent']
    assert not (output / worker.RESULT_MANIFEST_FILE).exists()
