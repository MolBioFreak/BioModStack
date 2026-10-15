"""Managed target receiving uses file-download ownership, never API cwd."""
import pytest
from sqlalchemy import func, select

from database import Job
from services import binder_selected_round as receiver
from services.binder_round_inputs import BLIND_FIXED_PARAMETERS, BOUND_INPUTS
from test_binder_continuation import selected, PDB
from test_binder_selected_round import source
from test_project_workflow_setups import setup_store


@pytest.mark.asyncio
@pytest.mark.parametrize('path_kind', ['managed', 'absolute', 'relative'])
async def test_independent_target_path_from_api_cwd(selected, monkeypatch, path_kind):
    _, session, root, tmp = await source(selected)
    inputs = tmp / 'separate-input-mount'
    inputs.mkdir()
    target = inputs / 'native.pdb'
    # Two distinguishable chains: explicit B must remain the target.
    target.write_text(PDB.replace('END\n', '') + PDB.replace('ALA A', 'GLY B'))
    api_cwd = tmp / 'unrelated-api'
    api_cwd.mkdir()
    monkeypatch.chdir(api_cwd)
    monkeypatch.setenv('BMS_INPUTS', str(inputs))
    if path_kind == 'managed':
        target_path = 'inputs/native.pdb'
        # A cwd-local impostor must not win over the managed file owner.
        (api_cwd / 'inputs').mkdir()
        (api_cwd / 'inputs/native.pdb').write_text(PDB.replace('ALA A', 'SER B'))
    elif path_kind == 'absolute':
        target_path = str(target)
    else:
        target = api_cwd / 'relative.pdb'
        target.write_text(PDB.replace('ALA A', 'GLY B'))
        target_path = 'relative.pdb'
    root.params = {'bindcraft2_settings': {'targets': [
        {'name': 'independent', 'target_path': target_path, 'chains': 'B'}]}}
    await session.commit()
    before = await session.scalar(select(func.count()).select_from(Job))
    ref = {'kind': 'design', 'job_id': root.id, 'design_id': 'd0'}
    row = await receiver.resolve_ref(session, root, ref, output_format='native')
    resolved = row['targets'][0]
    assert resolved['target_path'] == target_path
    assert 'unavailable_reason' not in resolved
    assert [(c['id'], c['sequence']) for c in resolved['components']] == [('B', 'G')]
    child, binding = receiver.prediction_request(root, row, resolved, 'protenix', {}, None)
    assert [c['sequence'] for c in child.params['complex_components']] == ['A', 'G']
    assert binding['input_components'][1]['source_chain'] == 'B'
    assert not any(child.params.get(key) for key in BLIND_FIXED_PARAMETERS)
    assert not any(child.params.get(key) for key in BOUND_INPUTS - {'sequence', 'sequence_name', 'complex_components'})
    assert await session.scalar(select(func.count()).select_from(Job)) == before
