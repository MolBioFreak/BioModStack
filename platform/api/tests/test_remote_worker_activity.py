"""Metadata-only worker progress is bounded, attempt-bound and advisory."""
import importlib.util
import json
from pathlib import Path
import uuid

import pytest


def worker():
    path = Path(__file__).parents[1] / 'tools/bms_remote_worker.py'
    spec = importlib.util.spec_from_file_location('worker_activity_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def activity(tmp_path):
    root = tmp_path / 'results' / '.bms-stage-receipts'
    root.mkdir(parents=True)
    identity = dict(job_id='job-one', attempt_id=str(uuid.uuid4()))
    (tmp_path / 'execution-envelope.json').write_text(json.dumps({
        **identity, 'output_directory': str(root.parent)}))
    payload = dict(schema='bms.remote-stage-receipt.v1', **identity,
                   stage='protenix', status='start', outputs=[])
    path = root / 'protenix.start.json'
    path.write_text(json.dumps(payload))
    return tmp_path, identity, path, payload


def test_activity_projects_no_payload_or_scientific_results(activity):
    root, identity, path, payload = activity
    result = worker().workflow_activity(root, identity)
    assert result['stage'] == 'protenix'
    assert result['state'] == 'started'
    assert set(result) == {'stage', 'state', 'updated_at'}
    payload.update(status='complete', outputs=['secret-biological-result.cif'])
    path.unlink()
    path.with_name('protenix.terminal.json').write_text(json.dumps(payload))
    result = worker().workflow_activity(root, identity)
    assert result['state'] == 'completed'
    assert 'secret-biological' not in json.dumps(result)


@pytest.mark.parametrize('key,value', [
    ('job_id', 'wrong'), ('attempt_id', 'wrong'), ('stage', '../secret'),
    ('status', 'invented'), ('extra', True), ('outputs', 'wrong'),
])
def test_activity_rejects_stale_or_invalid_receipts(activity, key, value):
    root, identity, path, payload = activity
    payload[key] = value
    path.write_text(json.dumps(payload))
    assert worker().workflow_activity(root, identity) is None


def test_activity_rejects_symlinks_and_oversized_receipts(activity, tmp_path):
    root, identity, path, payload = activity
    target = tmp_path / 'external.json'
    target.write_text(json.dumps(payload))
    path.unlink()
    path.symlink_to(target)
    assert worker().workflow_activity(root, identity) is None
    path.unlink()
    path.write_bytes(b' ' * 65537)
    assert worker().workflow_activity(root, identity) is None


@pytest.mark.parametrize('fault', ['missing', 'invalid_json', 'not_object', 'missing_root', 'relative_root'])
def test_unknown_activity_authority_is_unavailable_not_execution_failure(activity, fault):
    root, identity, path, payload = activity
    envelope = root / 'execution-envelope.json'
    if fault == 'missing':
        envelope.unlink()
    elif fault == 'invalid_json':
        envelope.write_text('{')
    elif fault == 'not_object':
        envelope.write_text('[]')
    else:
        value = dict(identity)
        if fault == 'relative_root': value['output_directory'] = '.'
        envelope.write_text(json.dumps(value))
    assert worker().workflow_activity(root, identity) is None


@pytest.mark.parametrize('foreign', ['outside', 'relative', 'symlink'])
def test_activity_never_borrows_another_root(activity, foreign):
    root, identity, path, payload = activity
    target = root / 'foreign'
    receipts = target / '.bms-stage-receipts'
    receipts.mkdir(parents=True)
    (receipts / path.name).write_text(json.dumps(payload))
    selected = str(target)
    if foreign == 'relative':
        selected = 'foreign'
    elif foreign == 'symlink':
        alias = root / 'results' / 'aliased'
        alias.symlink_to(target, target_is_directory=True)
        selected = str(alias)
    assert worker().workflow_activity(root, dict(identity, native_output_directory=selected)) is None


def test_current_generation_activity_does_not_reuse_original_stage(activity):
    root, identity, path, payload = activity
    current = root / 'results/generations/2'
    receipts = current / '.bms-stage-receipts'
    receipts.mkdir(parents=True)
    (receipts / 'current.start.json').write_text(json.dumps(dict(payload, stage='current')))
    result = worker().workflow_activity(root, dict(identity, native_output_directory=str(current)))
    assert result['stage'] == 'current'
    assert path.exists()


def test_status_reuses_its_envelope_without_redundant_observation_reads(activity, monkeypatch):
    root, identity, path, payload = activity
    module = worker()
    envelope = module.load_json(root / module.ENVELOPE_FILE)
    module.atomic_json(root / module.STATUS_FILE, module.base_status(envelope, 'running'))
    original = module.load_json
    reads = []
    def read(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(module, 'load_json', read)
    monkeypatch.setattr(module, 'process_matches', lambda *_: True)
    value = module.status(root)
    assert value['state'] == 'running' and value['quiescent'] is False
    assert value['activity']['stage'] == 'protenix'
    assert reads.count(root / module.ENVELOPE_FILE) == 1


def test_unavailable_activity_never_waives_control_identity(activity):
    root, identity, path, payload = activity
    module = worker()
    envelope = module.load_json(root / module.ENVELOPE_FILE)
    module.atomic_json(root / module.STATUS_FILE, module.base_status(envelope, 'running'))
    (root / module.ENVELOPE_FILE).write_text(json.dumps(dict(identity, attempt_id='foreign')))
    with pytest.raises(RuntimeError, match='identity mismatch'):
        module.status(root)


def test_terminal_status_never_carries_stale_activity(activity):
    root, identity, path, payload = activity
    value = {**identity, 'state': 'failed', 'activity': {'stage': 'stale'}}
    (root / 'execution-envelope.json').write_text(json.dumps(identity))
    (root / 'status.json').write_text(json.dumps(value))
    assert 'activity' not in worker().status(root)
