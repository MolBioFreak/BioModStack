"""Real stage CLI -> shared runtime submission -> native child preparation.

Only the asynchronous science wait is stopped; no inference or hosted service.
"""
from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_structure_prediction_frustrampnn_v2_transport import _selected_settings, _two_model_pdb

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('workflow,batching,count', [
    ('protein_design', True, 3), ('antibody_denovo', False, 2),
    ('conformational_mapping', True, 1),
])
def test_task_work_sources_enter_retained_custody(tmp_path, monkeypatch, workflow, batching, count):
    from scripts import run_frustrampnn_parent_fanout as client
    from lib.component_adapter import runtime_from_environment
    import wait_for_children

    artifacts = tmp_path / 'results'
    artifacts.mkdir()
    context = tmp_path / 'context.json'
    context.write_text(json.dumps(dict(ledger_path=str(tmp_path / 'runtime.sqlite'),
        artifact_root=str(artifacts), attempt_id='attempt', root_job_id='parent',
        target_id='target', lease_id='lease')))
    monkeypatch.setenv('BMS_COMPONENT_CONTEXT', str(context))
    candidates = []
    raw = _two_model_pdb()
    for index in range(count):
        work = tmp_path / 'work' / str(index)
        work.mkdir(parents=True)
        source = work / 'terminal.pdb'
        source.write_bytes(raw)
        metadata = dict(candidate_id=f'candidate-{index}', parent_job_id='parent',
            parent_workflow_id=workflow, producer_stage=f'{workflow}:terminal',
            producer_candidate_key=f'terminal/{index}.pdb', requiredness='required')
        subprocess.run([sys.executable, str(ROOT / 'scripts/stage_frustrampnn_parent_candidate.py'),
            '--source', str(source), '--metadata-base64',
            base64.b64encode(client._canonical_bytes(metadata)).decode()], cwd=work, check=True)
        # Match SpawnWait's stageInMode copy into a distinct task work directory.
        staged = tmp_path / 'fanout-work' / f'candidate_candidate-{index}'
        shutil.copytree(work / f'candidate_candidate-{index}', staged)
        candidates.append(staged)
    settings = _selected_settings().model_dump(mode='json', exclude_none=False)
    origin = settings.pop('settings_value_origin')
    settings.update(batching_enabled=batching, structures_per_job=2)
    child_ids = []

    class ScienceWait(Exception):
        pass

    def stop_wait(*args, expected_child_ids, **kwargs):
        child_ids[:] = expected_child_ids
        raise ScienceWait

    monkeypatch.setattr(wait_for_children, 'wait_for_children', stop_wait)
    arguments = dict(parent_job_id='parent', parent_workflow_id=workflow,
        settings_json=client._canonical_bytes(settings).decode(), settings_value_origin=origin,
        candidate_dirs=list(reversed(candidates)), output_receipt=tmp_path / 'receipt.json',
        output_bundles=tmp_path / 'bundles')
    with pytest.raises(ScienceWait):
        client.execute_parent_fanout(**arguments)
    first_ids = list(child_ids)
    with pytest.raises(ScienceWait):
        client.execute_parent_fanout(**arguments)
    assert child_ids == first_ids
    runtime = runtime_from_environment()
    payloads = [runtime.request(identity).payload for identity in child_ids]
    members = [member for payload in payloads
        for member in payload['params']['frustrampnn_component_group']['candidates']]
    assert [m['metadata']['candidate_id'] for m in members] == [f'candidate-{i}' for i in range(count)]
    assert len(child_ids) == ((count + 1) // 2 if batching else count)
    for member in members:
        retained = artifacts / member['source_relative_path']
        assert retained.read_bytes() == raw
        assert member['source_sha256'] == hashlib.sha256(raw).hexdigest()
        assert member['source_size_bytes'] == len(raw)
        assert json.loads((retained.parent / 'metadata.json').read_bytes()) == member['metadata']

    # Retry must not overwrite retained bytes or accept changed source authority.
    source = candidates[0] / 'source.pdb'
    source.write_bytes(raw + b'REMARK changed\n')
    with pytest.raises(ValueError, match='conflict'):
        client.execute_parent_fanout(**arguments)
    source.write_bytes(raw)
    retained = artifacts / members[0]['source_relative_path']
    retained.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='conflict'):
        client.execute_parent_fanout(**arguments)
    retained.write_bytes(raw)
    retained.unlink()
    retained.symlink_to(source)
    with pytest.raises(ValueError, match='subpath|conflict'):
        client.execute_parent_fanout(**arguments)
    retained.unlink()
    retained.write_bytes(raw)

    # The native child now survives actual removal of both producer task roots.
    shutil.rmtree(tmp_path / 'work')
    shutil.rmtree(tmp_path / 'fanout-work')
    for child_id, payload in zip(child_ids, payloads, strict=True):
        output = artifacts / 'children' / child_id
        prepared = client.prepare_runtime_child(payload, child_id=child_id, output_root=output)
        assert client.prepare_runtime_child(payload, child_id=child_id, output_root=output) == prepared
        envelope = prepared['_frustrampnn_child_v1']
        assert envelope['selection'] == payload['params']['frustrampnn_component_group']['candidates']
        assert envelope['normalized_requested_settings'] == {**settings, 'settings_value_origin': origin}
        assert Path(prepared['frustrampnn_batch_manifest_path']).is_file()
    retained.write_bytes(b'tampered after submission')
    with pytest.raises(ValueError, match='source binding changed'):
        client.prepare_runtime_child(payloads[0], child_id=child_ids[0],
            output_root=artifacts / 'children' / child_ids[0])
