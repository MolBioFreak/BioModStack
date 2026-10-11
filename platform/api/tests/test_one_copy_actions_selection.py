import json
from pathlib import Path

import pytest

from database import Design
from routers import jobs
from services.binder_continuation import snapshot_selection
from test_binder_continuation import selected, PDB
from test_project_workflow_setups import setup_store


@pytest.mark.asyncio
async def test_fresh_jobs_reuse_input_without_reusing_submit_identity(selected):
    client, session, root, source, tmp_path = selected
    payload = {'source_job_id': source.id, 'design_ids': ['d1'], 'operation': 'refine',
               'params': {'binder_chains': 'A', 'target_chains': 'B',
                          'maturation_repack_enabled': True, 'maturation_anchors_enabled': False,
                          'maturation_flow_enabled': False}}
    first = await client.post('/api/binder-continuation/selected', json=payload)
    second = await client.post('/api/binder-continuation/selected', json=payload)
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()['launched_jobs'][0]['id'] != second.json()['launched_jobs'][0]['id']
    directories = list((tmp_path / 'inputs/design_selections/binder').iterdir())
    assert len(directories) == 1
    assert (directories[0] / 'selection_manifest.json').exists()


@pytest.mark.asyncio
async def test_selection_replay_zero_allocation_and_exact_binding(selected, monkeypatch):
    _, session, root, source, tmp_path = selected
    designs = [await session.get(Design, 'd1'), await session.get(Design, 'd0')]
    first = snapshot_selection(source, root, designs)
    originals = {p: (p.stat().st_ino, p.read_bytes()) for p in first.rglob('*') if p.is_file()}
    materialize = jobs._materialize_antibody_selection
    monkeypatch.setattr(jobs, '_materialize_antibody_selection', lambda *a, **k: pytest.fail('replay allocated'))
    second = snapshot_selection(source, root, designs)
    assert second == first
    assert all((p.stat().st_ino, p.read_bytes()) == value for p, value in originals.items())
    rows = json.loads((first / 'source_identity.json').read_text())
    assert [r['source_meta']['parent_design_id'] for r in rows] == ['d1', 'd0']
    assert [r['source_meta']['source_job_id'] for r in rows] == ['round1', 'root']
    monkeypatch.setattr(jobs, '_materialize_antibody_selection', materialize)
    # Same display names/content, but reversed selected identity/order must not alias.
    assert snapshot_selection(source, root, list(reversed(designs))) != first
    # Changed raw mutable source creates a new custody snapshot, no new refusal.
    Path(designs[0].pdb_path).write_text(PDB + 'REMARK changed\n')
    changed = snapshot_selection(source, root, designs)
    assert changed != first
    assert Path(rows[0]['source_path']).read_text() == PDB
