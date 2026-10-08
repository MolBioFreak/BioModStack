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


def test_terminal_status_never_carries_stale_activity(activity):
    root, identity, path, payload = activity
    value = {**identity, 'state': 'failed', 'activity': {'stage': 'stale'}}
    (root / 'status.json').write_text(json.dumps(value))
    assert 'activity' not in worker().status(root)
