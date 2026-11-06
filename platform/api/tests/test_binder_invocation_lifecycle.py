"""Shared initial/selected invocation transport through real scratch Job stores."""
import asyncio
from copy import deepcopy

import pytest
from sqlalchemy import select

from database import Job
from schemas import SequenceDesignRequest, SelectedRoundRequest
from services import binder_round as rounds, binder_selected_round as selected_round
from test_binder_continuation import selected
from test_project_workflow_setups import setup_store
from test_binder_selected_round import source
from test_sequence_native_publication import store


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode,write_outputs', [
    ('caliby_experimental', 'ensemble_design', True), ('caliby_experimental', 'sidechain_pack', True),
    ('ligandmpnn', 'ligand_aware', True), ('ligandmpnn', 'ligand_aware', False)])
async def test_actual_native_publication_round_receiving(tmp_path, monkeypatch, store, model, mode, write_outputs):
    from test_sequence_native_publication import emitted_result, seed
    from services.result_state_integrity import finalize_successful_job
    from database import Design
    folder, _, _, _, params = emitted_result(tmp_path, monkeypatch, model, mode, write_outputs=write_outputs)
    await seed(store, folder, model, mode, params, remote=True)
    async with store() as session:
        owner = await session.get(Job, 'native')
        await finalize_successful_job(owner, str(folder), session)
        await session.commit()
        refs = await selected_round.output_refs(session, owner)
        assert refs
        assert list(await session.scalars(select(Design).where(Design.job_id == owner.id))) == []
        for ref in refs:
            row = await selected_round.resolve_ref(session, owner, ref, output_format='native')
            assert row['ref'] == ref
            if not write_outputs:
                assert row['sequence']
                assert 'path' not in row
            else:
                assert row['path']


@pytest.mark.asyncio
async def test_prediction_detour_survives_fresh_project_session(setup_store):
    from test_project_workflow_setups import _project
    from services.global_experiments import workflow_setups as setups
    detour = {'schema_version': 1, 'candidate_ref': {'kind': 'native_sequence',
        'job_id': 'source', 'publication_ref': 'publication', 'native_output_id': 'exact-row'},
        'composition': {'complex_components': [{'id': 'Q', 'type': 'protein', 'sequence': 'HD', 'count': 2}]},
        'round_plan': invocation_plan(), 'md_draft': {'temperature': 298, 'neutralize': False,
        'execution_target_id': None, 'unknown_native_mdp': 'preserved'}, 'return_to': '/designs/source',
        'destination_launch_context_id': 'independent-md-destination'}
    async with setup_store() as session:
        project = await _project(session)
        project_id = project.id
        setup = await setups.create_workflow_setup(session, project_id=project_id, relationship_kind='primary',
            global_experiment_id=None, experiment_name='Prediction detour', experiment_objective='Retain MD authoring',
            domain_kind='protein_in_silico', capability_id='protein.structure_prediction.esmfold2', idempotency_key='detour-create')
        setup_id = setup['setup_context_id']
        await setups.save_workflow_setup_draft(session, project_id=project_id, setup_context_id=setup_id,
            draft={'editor_state': {'prediction_operation_detour': detour}}, expected_generation=setup['generation'],
            idempotency_key='detour-save')
        await session.commit()
    async with setup_store() as fresh:
        restored = await setups.get_workflow_setup(fresh, project_id=project_id, setup_context_id=setup_id)
        assert restored['draft']['prediction_operation_detour'] == detour
        assert restored['state'] == 'open'


def invocation_plan():
    return {'schema_version': 2, 'stages': [{'id': 'design', 'kind': 'sequence_design',
        'input': {'selected': True}, 'models': [
            {'invocation_id': 'first', 'model_id': 'proteinmpnn', 'mode': 'design',
             'execution_target_id': None, 'params': {'mpnn_temperature': 0.71}},
            {'invocation_id': 'second', 'model_id': 'proteinmpnn', 'mode': 'design',
             'params': {'mpnn_temperature': 0.13}}]}]}


def test_initial_envelope_wire_preserves_legacy_and_off():
    legacy = {'schema_version': 1, 'enabled': False, 'model_id': 'proteinmpnn',
              'params': {'mpnn_temperature': 0, 'exclude_cys': False}, 'input_settings': {}}
    assert SequenceDesignRequest.model_validate(legacy).model_dump(mode='json') == legacy
    request = {'schema_version': 2, 'enabled': False, 'round_plan': invocation_plan()}
    parsed = SequenceDesignRequest.model_validate(request)
    assert parsed.round_plan.stages[0].models[0].execution_target_id is None
    assert 'execution_target_id' not in parsed.round_plan.stages[0].models[1].model_fields_set
    assert SequenceDesignRequest.model_validate(parsed.model_dump()).model_dump() == parsed.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [True, False])
async def test_initial_shared_plan_repeated_calls_recover_once(selected, setup_store, monkeypatch, enabled):
    from routers import jobs
    client, session, root, tmp = await source(selected)
    root.status = 'completed'
    root.execution_target_id = None
    root.provenance = {**(root.provenance or {}), 'sequence_design_request': {
        'schema_version': 2, 'enabled': enabled, 'round_plan': invocation_plan()}}
    await session.commit()
    original = jobs.submit_selected_child_jobs
    interrupted = False
    async def lose_response(*args, **kwargs):
        nonlocal interrupted
        response = await original(*args, **kwargs)
        if not interrupted:
            interrupted = True
            raise asyncio.CancelledError('committed child before progress save')
        return response
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', lose_response)
    async with setup_store() as experiments:
        if enabled:
            with pytest.raises(asyncio.CancelledError):
                await rounds.reconcile_round(session, experiments, 'root')
        else:
            result = await rounds.reconcile_round(session, experiments, 'root')
            assert result['state'] == 'generation_only'
    monkeypatch.setattr(jobs, 'submit_selected_child_jobs', original)
    for _ in range(2):
        async with setup_store() as experiments:
            result = await rounds.reconcile_round(session, experiments, 'root')
    children = list(await session.scalars(select(Job).where(Job.model_id == 'proteinmpnn')))
    assert len(children) == (2 if enabled else 0), result
    if enabled:
        assert {child.params['mpnn_temperature'] for child in children} == {0.71, 0.13}
        assert {child.provenance[rounds.STEP]['invocation_id'] for child in children} == {'first', 'second'}
        assert all(child.execution_target_id is None for child in children)
        assert all(step['job_id'] for step in result['steps'].values())


@pytest.mark.asyncio
async def test_selected_repeated_calls_and_explicit_predecessor(selected):
    client, session, root, tmp = await source(selected)
    plan = invocation_plan()
    plan['stages'].append({'id': 'prediction', 'kind': 'prediction',
        'input': {'stage_id': 'design', 'invocation_id': 'second', 'scope': 'whole_composition'},
        'models': [{'invocation_id': 'blind', 'model_id': 'protenix', 'mode': 'complex',
                    'params': {'protenix_use_msa': False}}]})
    payload = {'operation': 'round', 'source_job_id': 'root', 'idempotency_key': 'calls',
        'selection': {'candidate_refs': [{'kind': 'design', 'job_id': 'root', 'design_id': 'd0'}]},
        'round_plan': plan}
    first = await client.post('/api/binder-continuation/selected', json=payload)
    assert first.status_code == 201, first.text
    again = await client.post('/api/binder-continuation/selected', json=payload)
    assert again.status_code == 201, again.text
    assert again.json()['round_id'] == first.json()['round_id']
    steps = again.json()['round']['steps']
    assert len(steps) == 2, again.text
    assert {s['invocation_id'] for s in steps} == {'first', 'second'}
    dependencies = again.json()['round']['dependencies']
    assert len(dependencies) == 1
    assert next(iter(dependencies.values()))['parent_step_id'] == next(s['step_id'] for s in steps if s['invocation_id'] == 'second')


def test_whole_recipe_bonds_and_blind_conditioning():
    from types import SimpleNamespace
    row = {'ref': {'kind': 'native_sequence', 'job_id': 'p', 'publication_ref': 'pub', 'native_output_id': 's'},
        'owner_job_id': 'p', 'components': [
            {'id': 'P', 'type': 'protein', 'sequence': 'AC', 'count': 2, 'templatesPath': '/old/pose'},
            {'id': 'D', 'type': 'dna', 'sequence': 'AC'},
            {'id': 'L', 'type': 'ligand', 'ccd': 'ATP'}],
        'covalent_bonds': [{'left_entity': 1, 'left_copy': 2, 'left_position': 2, 'left_atom': 'SG',
                           'right_entity': 3, 'right_copy': 1, 'right_position': 1, 'right_atom': 'C1'}]}
    child, binding = selected_round.prediction_request(SimpleNamespace(id='root'), deepcopy(row), None,
        'protenix', {'protenix_n_cycle': 17, 'constraint': {'contact': []}}, None,
        scope='whole_composition', selected_chains=['P', 'L'])
    assert [c['id'] for c in child.params['complex_components']] == ['P', 'L']
    assert child.params['complex_components'][0]['count'] == 2
    assert child.params['covalent_bonds'][0]['right_entity'] == 2
    assert child.params['protenix_n_cycle'] == 17
    assert 'templatesPath' not in child.params['complex_components'][0]
    assert 'constraint' not in child.params


@pytest.mark.asyncio
async def test_paid_route_retains_local_custody_and_recovers_without_provider_submit(selected, monkeypatch):
    from services import boltz_api_jobs as paid
    client, session, root, tmp = await source(selected)
    estimates = []
    async def estimate(**kwargs):
        estimates.append(deepcopy(kwargs))
        return {'cost': 1}, 'approved-offline-estimate'
    monkeypatch.setattr(paid, 'estimate_boltz_api_cost', estimate)
    payload = {'operation': 'round', 'source_job_id': 'root', 'idempotency_key': 'paid',
        'selection': {'candidate_refs': [{'kind': 'design', 'job_id': 'root', 'design_id': 'd0'}]},
        'round_plan': {'schema_version': 2, 'stages': [{'id': 'paid', 'kind': 'prediction',
            'input': {'selected': True}, 'models': [{'invocation_id': 'paid-once', 'model_id': 'boltz2',
                'mode': 'complex', 'route': 'boltz_api', 'params': {'boltz_use_msa': False}}]}]}}
    response = await client.post('/api/binder-continuation/selected', json=payload)
    assert response.status_code == 201, response.text
    retained = response.json()['round']
    assert retained['state'] == 'review_required', retained
    assert estimates == []  # Merely composing a paid call cannot contact a provider.
    step = retained['steps'][0]
    endpoint = f"/api/binder-continuation/root/round/{retained['round_id']}/steps/{step['step_id']}/paid-approval"
    for _ in range(2):
        approval = await client.post(endpoint, json={'approved_estimate_fingerprint': 'approved-offline-estimate'})
        assert approval.status_code == 200, approval.text
    children = list(await session.scalars(select(Job).where(Job.model_id == 'boltz_api')))
    assert len(children) == 1
    child = children[0]
    assert child.lineage_root_job_id == 'root'
    assert child.provenance[rounds.STEP]['source_ref']['design_id'] == 'd0'
    assert child.provenance[rounds.STEP]['step_id'] == step['step_id']
    assert len(estimates) == 1
    assert child.params['provider_input'] == estimates[0]['provider_input']
    assert set(child.params['provider_input']) == {'entities', 'num_samples'}
    assert 'root' not in str(child.params['provider_input'])

@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode,params', [
    ('caliby_experimental', 'ensemble_design', {'omit_aas': [], 'num_workers': 0, 'scn_step_scale': 0}),
    ('caliby_experimental', 'sidechain_pack', {'packer_model_name': 'caliby_packer_030'}),
    ('ligandmpnn', 'ligand_aware', {'write_structures': False, 'write_fasta': False}),
    ('fampnn', 'design', {'fampnn_exclude_cys': False}),
    ('boltz_cp_experimental', 'design', {}),
])
async def test_actual_native_call_bindings(selected, monkeypatch, model, mode, params):
    if model == 'boltz_cp_experimental':
        # Queue/compiler transport only; external provider readiness is inert.
        from services import msa_provider_setup
        monkeypatch.setattr(msa_provider_setup, 'preflight_msa_provider', lambda *args: None)
    client, session, root, tmp = await source(selected)
    call = {'invocation_id': 'native', 'model_id': model, 'mode': mode,
            'execution_target_id': None, 'params': params}
    if mode == 'ensemble_design':
        call['input_groups'] = [{'ensemble_id': group, 'states': [
            {'state_id': group + '-primary', 'candidate_ref': {'kind': 'design', 'job_id': 'round1', 'design_id': 'd1'},
             'settings': {'fixed_pos_seq': 'B1', 'fixed_pos_scn': ''}},
            {'state_id': group + '-other', 'candidate_ref': {'kind': 'design', 'job_id': 'root', 'design_id': 'd0'}}]}
            for group in ['one', 'two']]
    response = await client.post('/api/binder-continuation/selected', json={
        'operation': 'round', 'source_job_id': 'root', 'idempotency_key': mode,
        'selection': {'candidate_refs': [{'kind': 'design', 'job_id': 'root', 'design_id': 'd0'}]},
        'round_plan': {'schema_version': 2, 'stages': [{'id': 'native',
            'kind': 'prediction' if model == 'boltz_cp_experimental' else 'sequence_design',
            'input': {'selected': True, 'scope': 'whole_composition'}, 'models': [call]}]}})
    assert response.status_code == 201, response.text
    result = response.json()['round']
    assert not result['errors'], result
    assert len(result['steps']) == 1, result
    step = result['steps'][0]
    assert step['job_id'], step
    child = await session.get(Job, step['job_id'])
    assert child.mode == mode and child.execution_target_id is None
    if mode == 'ensemble_design':
        assert [group['ensemble_id'] for group in child.params['ensembles']] == ['one', 'two']
        assert [state['state_id'] for state in child.params['ensembles'][0]['states']] == ['one-primary', 'one-other']
        assert child.params['ensembles'][0]['states'][0]['fixed_pos_seq'] == 'B1'
        assert child.params['omit_aas'] == []
    if mode == 'sidechain_pack':
        assert child.params['packer_model_name'] == 'caliby_packer_030'
        assert 'structures' in child.params and 'ensembles' not in child.params
    if model == 'ligandmpnn':
        assert child.params['write_structures'] is False and child.params['write_fasta'] is False
        assert child.params['target_pdb'] and 'input_pdb' not in child.params

