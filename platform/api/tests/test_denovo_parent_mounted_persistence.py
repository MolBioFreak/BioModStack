"""Read-only audit of the actual mounted parent's private HTTP SQLite stores.

Run after deNovoV15ParentHttpLifecycleMounted with the same evidence root.
This does not manufacture a request or substitute a helper round trip.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest


@pytest.mark.skipif(not os.environ.get('BMS_PARENT_LIFECYCLE_ROOT'), reason='requires real mounted parent evidence')
@pytest.mark.parametrize('receipt_name', ['project-prepared.json', 'source-project-prepared.json'])
def test_mounted_parent_immutable_preparation_and_no_execution(receipt_name):
    root = Path(os.environ['BMS_PARENT_LIFECYCLE_ROOT']).resolve()
    assert root.is_relative_to(Path('/home/dalab/.hermes/profiles/fresh/cache/scratch'))
    prepared = json.loads((root / receipt_name).read_text())['data']
    emitted = prepared['draft']['native_job_request']
    with sqlite3.connect(f'file:{root / "project.db"}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM workflow_preparations WHERE resource_id=?', (prepared['preparation_id'],)).fetchone()
        normalized = json.loads(row['normalized_request_json'])
        scheduler = json.loads(row['scheduler_payload_json'])
        receipt = json.loads(row['validation_receipt_json'])
        assert hashlib.sha256(row['normalized_request_json'].encode()).hexdigest() == row['normalized_request_sha256']
        assert receipt['status'] == row['validation_status'] == 'valid'
        assert normalized['workflow']['scheduler'] == scheduler
        assert normalized['workflow']['adapter_id'] == 'bms.core-job.rfd4_proteina.adapter.v1'
        assert scheduler['model_id'] == emitted['model_id'] == 'rfd4_proteina'
        assert scheduler['mode'] == emitted['mode'] == 'generate'
        assert scheduler['execution_target_id'] is emitted['execution_target_id'] is None
        assert scheduler['execution_policy'] == emitted['execution_policy']
        native = emitted['params']['rfd4_request']
        from scripts.lib.rfd4_proteina_contract import normalize_request
        assert scheduler['params']['rfd4_request'] == normalize_request(native, 'generate')
        assert scheduler['params']['rfd4_request']['settings'] == native['settings']
        if 'native_sources' in emitted['params']:
            assert scheduler['params']['native_sources'] == emitted['params']['native_sources']
            selected = scheduler['params']['native_sources']['selected']
            assert selected['model_number'] == 2
            retained = root / selected['path']
            assert hashlib.sha256(retained.read_bytes()).hexdigest() == selected['expected_sha256']
            assert prepared['draft']['native_recipe'] == 'partial'
            assert native['settings']['generation']['args']['partial_t_backbone'] == 0
            closure = Path(scheduler['params']['rfd4_input_closure'])
            assert closure.is_relative_to(root)
            bindings = json.loads(Path(scheduler['params']['rfd4_bindings']).read_text())
            assert (closure / bindings['selected']).is_file()
        assert not {'native_recipe', 'native_task_drafts', 'native_source_drafts', 'native_control_drafts'} & scheduler['params'].keys()
        assert db.execute('SELECT count(*) FROM run_groups').fetchone()[0] == 0
        contexts = db.execute('SELECT state,canonical_job_id FROM launch_contexts').fetchall()
        assert all(row['state'] == 'issued' and row['canonical_job_id'] is None for row in contexts)
    # New connection: persisted immutable authority, not an ORM identity cache.
    with sqlite3.connect(f'file:{root / "project.db"}?mode=ro', uri=True) as cold:
        assert json.loads(cold.execute('SELECT scheduler_payload_json FROM workflow_preparations WHERE resource_id=?', (prepared['preparation_id'],)).fetchone()[0]) == scheduler
    with sqlite3.connect(f'file:{root / "core.db"}?mode=ro', uri=True) as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
    traffic = [json.loads(line) for line in (root / 'http.jsonl').read_text().splitlines()]
    assert not any('/api/jobs' in row['path'] and row['method'] != 'GET' for row in traffic)
