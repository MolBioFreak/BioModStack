"""Both authored envelopes use the ordinary retained round and scratch Jobs."""
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Design, Job
from services import binder_round as rounds
from services.sequence_round_inputs import REQUEST as GENERAL
from test_binder_continuation import selected, PDB
from test_project_workflow_setups import setup_store
from test_binder_round_orchestration import fixture_root, envelope as binder_envelope
from test_sequence_round_integration import envelope as general_envelope


async def both(selected, temperature=0.1, *, version=1):
    session, root, tmp = await fixture_root(selected, backbone=True)
    general = general_envelope('proteinmpnn', params={'mpnn_temperature': 0.1, 'mpnn_backbone_noise': 0, 'mpnn_relax_output': False})
    if version == 2:
        general = {'schema_version': 2, 'enabled': True, 'round_plan': {'schema_version': 2,
            'stages': [{'id': 'general', 'kind': 'sequence_design',
                'input': {'selected': True, 'scope': 'whole_composition'}, 'models': [
                    {'invocation_id': 'authored', 'model_id': 'proteinmpnn', 'mode': 'design',
                     'params': {'mpnn_temperature': 0.1, 'design_chain': 'B', 'target_chain': 'A'}}]}]}}
    binder = binder_envelope()
    binder['sequence_design']['params']['mpnn_temperature'] = temperature
    root.provenance = {GENERAL: general, rounds.REQUEST: binder}
    await session.commit()
    return session, root, tmp


@pytest.mark.asyncio
@pytest.mark.parametrize('temperature', [0.1, 0.2])
@pytest.mark.parametrize('version', [1, 2])
async def test_independent_authored_scope_values_and_cold_retry(selected, setup_store, temperature, version):
    session, root, tmp = await both(selected, temperature, version=version)
    async with setup_store() as experiments:
        first = await rounds.reconcile_round(session, experiments, root.id)
        assert len(first['steps']) == 2, first
        steps = {s['metadata']['authored_envelope']: s for s in first['steps'].values()}
        general = await session.get(Job, steps[GENERAL]['job_id'])
        binder = await session.get(Job, steps[rounds.REQUEST]['job_id'])
        assert general is not None and binder is not None, first
        assert general.params['mpnn_temperature'] == 0.1
        assert binder.params['mpnn_temperature'] == temperature
        assert general.params['design_chain'] == ('A,B' if version == 1 else 'B')
        assert general.params['target_chain'] == ('' if version == 1 else 'A')
        assert binder.params['design_chain'] == 'A' and binder.params['target_chain'] == 'B'
        assert general.provenance[rounds.STEP]['source_design_id'] == binder.provenance[rounds.STEP]['source_design_id'] == 'd0'
        if version == 1:
            assert steps[GENERAL]['metadata']['step_id'] == rounds.identity(root.id, 'sequence_design', 'd0', None)
        # General publication must never become a binder predecessor, even when
        # its native model/settings happen to match the binder request.
        general.status = 'completed'
        path = tmp / 'general-output.pdb'
        path.write_text(PDB)
        session.add(Design(id='general-output', name='general', job_id=general.id, pdb_path=str(path)))
        binder.status = 'failed'
        await session.commit()
        failed_id = binder.id
        factory = async_sessionmaker(session.bind, expire_on_commit=False)
        await session.rollback()
        async with factory() as cold:
            partial = await rounds.reconcile_round(cold, experiments, 'root')
            assert len(partial['steps']) == 2 and partial['state'] == 'completed_with_errors', partial
            retried = await rounds.reconcile_round(cold, experiments, 'root', retry=True)
            assert len(retried['steps']) == 3, retried
            replay = await rounds.reconcile_round(cold, experiments, 'root', retry=True)
            assert replay['steps'] == retried['steps']
            replacement = next(s for s in retried['steps'].values() if s['metadata'].get('retry_of_job_id'))
            assert replacement['metadata']['authored_envelope'] == rounds.REQUEST
            assert replacement['metadata']['retry_of_job_id'] == failed_id
            child = await cold.get(Job, replacement['job_id'])
            child.status = 'completed'
            path = tmp / 'binder-output.pdb'
            path.write_text(PDB.replace('ALA', 'SER'))
            cold.add(Design(id='binder-output', name='binder', job_id=child.id, pdb_path=str(path)))
            await cold.commit()
            continued = await rounds.reconcile_round(cold, experiments, 'root')
            predictions = list(await cold.scalars(select(Job).where(Job.model_id == 'protenix')))
            assert len(predictions) == 1, continued
            assert predictions[0].provenance[rounds.STEP]['source_design_id'] == 'binder-output'
            assert predictions[0].params['complex_components'][0]['sequence'] == 'S'
            observed = await rounds.read_round(cold, 'root')
            assert observed['sequence_design'] == first['sequence_design']
            assert observed['binder_round'] == first['binder_round']
            assert observed['steps'] == continued['steps']
            assert (await cold.get(Job, failed_id)).status == 'failed'


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [GENERAL, rounds.REQUEST])
async def test_single_enabled_preserves_legacy_identity(selected, setup_store, enabled):
    session, root, _ = await both(selected)
    provenance = deepcopy(root.provenance)
    provenance[rounds.REQUEST if enabled == GENERAL else GENERAL]['enabled'] = False
    root.provenance = provenance
    await session.commit()
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert list(result['steps']) == [rounds.identity(root.id, 'sequence_design', 'd0', None)]
        assert 'authored_branches' not in result
        assert (await rounds.reconcile_round(session, experiments, root.id))['steps'] == result['steps']


@pytest.mark.asyncio
async def test_general_order_is_local_and_failed_binding_does_not_shadow_binder(selected, setup_store, monkeypatch):
    from services import sequence_round_inputs
    session, root, _ = await both(selected)
    original = sequence_round_inputs.design_request
    def unavailable(*args, **kwargs):
        raise ValueError('fixture general binding unavailable')
    monkeypatch.setattr(sequence_round_inputs, 'design_request', unavailable)
    async with setup_store() as experiments:
        partial = await rounds.reconcile_round(session, experiments, root.id)
        assert len(partial['steps']) == 1, partial
        assert next(iter(partial['steps'].values()))['metadata']['authored_envelope'] == rounds.REQUEST
        assert partial['errors'] == {GENERAL + ':d0': 'fixture general binding unavailable'}
        monkeypatch.setattr(sequence_round_inputs, 'design_request', original)
        restored = await rounds.reconcile_round(session, experiments, root.id, retry=True)
        assert len(restored['steps']) == 2 and restored['errors'] == {}, restored
        assert (await rounds.reconcile_round(session, experiments, root.id))['steps'] == restored['steps']


@pytest.mark.asyncio
async def test_general_invocation_successor_stays_inside_authored_branch(selected, setup_store):
    session, root, tmp = await both(selected, version=2)
    provenance = deepcopy(root.provenance)
    provenance[GENERAL]['round_plan']['stages'].append({
        'id': 'next', 'kind': 'sequence_design',
        'input': {'stage_id': 'general', 'scope': 'whole_composition'},
        'models': [{'invocation_id': 'later', 'model_id': 'proteinmpnn', 'mode': 'design',
                    'params': {'mpnn_temperature': 0.3, 'design_chain': 'A', 'target_chain': 'B'}}]})
    root.provenance = provenance
    await session.commit()
    async with setup_store() as experiments:
        first = await rounds.reconcile_round(session, experiments, root.id)
        assert len(first['steps']) == 2, first
        step = next(s for s in first['steps'].values() if s['metadata']['authored_envelope'] == GENERAL)
        general = await session.get(Job, step['job_id'])
        general.status = 'completed'
        path = tmp / 'general-sequential.pdb'
        path.write_text(PDB.replace('END\n', '') + PDB.replace('ALA A', 'GLY B'))
        session.add(Design(id='general-sequential', name='general', job_id=general.id, pdb_path=str(path)))
        await session.commit()
        advanced = await rounds.reconcile_round(session, experiments, root.id)
        assert len(advanced['steps']) == 3, advanced
        later = next(s for s in advanced['steps'].values() if s['metadata'].get('stage_id') == 'next')
        assert later['metadata']['authored_envelope'] == GENERAL
        assert later['metadata']['parent_step_ids'] == [step['metadata']['step_id']]
        assert later['request']['params']['mpnn_temperature'] == 0.3
        assert later['metadata']['source_design_id'] == 'general-sequential'
        assert not list(await session.scalars(select(Job).where(Job.model_id == 'protenix')))
        assert (await rounds.reconcile_round(session, experiments, root.id))['steps'] == advanced['steps']


def test_retired_project_override_does_not_revive_local_redesign():
    from services.protein_project_capabilities import protein_capability_record, protein_capability_inventory
    retired = protein_capability_record('protein.de_novo.local_redesign')
    assert retired['plannable'] is False
    assert retired['availability']['state'] != 'operational'
    ready = protein_capability_inventory(project_ready_only=True)['capabilities']
    assert 'protein.de_novo.local_redesign' not in {r['capability_id'] for r in ready}
    current = protein_capability_record('protein.native.protein_modification_experimental.region_redesign')
    assert current['plannable'] is True
    assert current['allowed_model_modes'] == [{'model_id': 'protein_modification_experimental', 'mode': 'region_redesign'}]
