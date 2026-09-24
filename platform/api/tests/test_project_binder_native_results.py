"""Project references over real publication fixture bytes; no scientific jobs."""
import pytest
from sqlalchemy import select, func

from database import Design, Job
from experiment_services import create_project, create_global_experiment, create_domain_experiment
from services.bindcraft2_publication import publish_native_results
from services.global_experiments.adapters import NativeBinderJobResultAdapter, AdapterError, registry
from services.global_experiments.receipts import attach_verified_entity
from services.global_experiments.result_surfaces import result_surface_for_receipt
from test_bindcraft2_publication import campaign
from test_project_manager_adapters import stores, _project_payload, _global_payload, _domain_payload


@pytest.mark.asyncio
@pytest.mark.parametrize('zero', [True, False])
async def test_native_campaign_attach_replay_reopen_and_corruption(stores, zero):
    root, experiments, core = stores
    output = root / 'results' / 'campaign'
    campaign(output, zero=zero)
    adapter = NativeBinderJobResultAdapter('bindcraft2')
    async with core() as session:
        job = Job(id='campaign', name='Native campaign', model_id='bindcraft2', mode='campaign',
                  params={}, status='completed', output_dir=str(output))
        session.add(job)
        await session.flush()
        await publish_native_results(job, output, session)
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Design)) == (0 if zero else 1)
        found = await adapter.search(session, query='campaign', limit=10)
        assert [row.entity_id for row in found] == ['campaign']
        verified = await adapter.verify(session, 'campaign')
        assert verified['metadata']['artifact_authority'] == 'verified_native_publication'
    async with experiments() as session:
        project = await create_project(session, _project_payload())
        experiment = await create_global_experiment(session, project.id, _global_payload())
        domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload('protein_in_silico'))
        project_id, experiment_id, domain_id = project.id, experiment.id, domain.id
        generation = project.head_generation
        await session.commit()
    async with experiments() as session, core() as core_session:
        kwargs = dict(project_id=project_id, global_experiment_id=experiment_id,
                      domain_experiment_id=domain_id, adapter_id=adapter.adapter_id,
                      entity_id='campaign', operation='link_output', role='produced', note=None,
                      expected_head_generation=generation)
        attached = await attach_verified_entity(session, core_session, **kwargs)
        replay = await attach_verified_entity(session, core_session, **kwargs)
        assert replay['attachment_receipt_id'] == attached['attachment_receipt_id']
        await session.commit()
    async with experiments() as session:
        surface = await result_surface_for_receipt(session, project_id=project_id,
                                                  receipt_id=attached['source_receipt_id'])
        assert surface['readiness'] == 'ready'
        assert surface['route']['path'] == '/designs/campaign'
        assert surface['contract_id'] == 'native_binder_job_result_v1'
    (output / '.campaign_state.json').write_text('{"trajectories": 99}')
    async with core() as session:
        with pytest.raises(AdapterError, match='publication'):
            await adapter.verify(session, 'campaign')
        assert await session.scalar(select(func.count()).select_from(Design)) == (0 if zero else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('model,mode', [('bindcraft2','campaign'), ('ligandmpnn','interface_context'), ('esmfold2','blind_pose')])
async def test_native_adapter_requires_publication_not_only_job_status(stores, model, mode):
    _, _, core = stores
    adapter = NativeBinderJobResultAdapter(model)
    async with core() as session:
        session.add(Job(id='unpublished', name='Unpublished', model_id=model, mode=mode,
                        params={}, status='completed'))
        await session.flush()
        with pytest.raises(AdapterError):
            await adapter.verify(session, 'unpublished')


@pytest.mark.asyncio
async def test_blind_pose_zero_design_publication_attaches_and_reopens(stores, monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from services import binder_blind_pose_selected as selected
    from test_binder_blind_pose_selected import pdb
    from run_binder_blind_pose import run

    root, experiments, core = stores
    inputs = root / 'inputs'
    monkeypatch.setattr(selected, 'get_inputs_dir', lambda: inputs)
    monkeypatch.setattr(selected, 'get_allowed_roots', lambda: {'inputs': inputs})
    monkeypatch.setattr(selected, 'resolve_runtime_data_path', lambda path: Path(path).resolve())
    source = inputs / 'source.pdb'
    source.write_text(pdb('B', ['ALA', 'GLY']))
    target = inputs / 'target.pdb'
    target.write_text(pdb('T', ['TYR']))
    directory = inputs / 'snapshot'
    binding = selected.prepare_selected(
        SimpleNamespace(id='source', params={}, lineage_root_job_id=None),
        [SimpleNamespace(id='source-design', job_id='source', pdb_path=str(source))],
        target_pdb=str(target), binder_chains={'source-design': ['B']},
        target_chains=['T'], directory=directory,
    )

    def fixture_native(command, check):
        output = Path(command[command.index('--output-dir') + 1])
        key = command[command.index('--sequence-name') + 1]
        sample = key + '_000'
        (output / (sample + '.cif')).write_text('data_fixture\n')
        (output / (sample + '.metrics.json')).write_text(json.dumps({'sample_id': sample, 'cif': sample + '.cif', 'iptm': .2}))
        (output / 'manifest.json').write_text(json.dumps({'workflow': 'esmfold2', 'sequence_name': key,
            'sample_count': 1, 'samples': [{'sample_id': sample, 'cif': sample + '.cif', 'metrics': sample + '.metrics.json'}]}))

    monkeypatch.setattr('run_binder_blind_pose.subprocess.run', fixture_native)
    output = root / 'results' / 'diagnostic'
    run(directory / 'selection.json', directory, directory / 'target.pdb', output / 'blind_pose_results',
        model_variant='fast', model_id_or_path='', num_loops=1, num_sampling_steps=25,
        num_diffusion_samples=1, seed=7, device='cpu', runner=root / 'fixture.py')
    params = {selected.KEY: binding, **selected.launch_params(directory, variant='fast', model_id_or_path='',
        num_loops=1, num_sampling_steps=25, num_diffusion_samples=1, seed=7)}
    async with core() as session:
        job = Job(id='diagnostic', name='Diagnostic', model_id='esmfold2', mode='blind_pose',
                  params=params, status='completed', output_dir=str(output))
        session.add(job)
        await session.flush()
        await selected.publish_selected(job, output, session)
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(Design)) == 0
    async with experiments() as session, core() as core_session:
        project = await create_project(session, _project_payload())
        experiment = await create_global_experiment(session, project.id, _global_payload())
        domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload('protein_in_silico'))
        attached = await attach_verified_entity(session, core_session,
            project_id=project.id, global_experiment_id=experiment.id, domain_experiment_id=domain.id,
            adapter_id='bms.native-binder.esmfold2.adapter.v1', entity_id='diagnostic',
            operation='link_output', role='produced', note=None, expected_head_generation=project.head_generation)
        surface = await result_surface_for_receipt(session, project_id=project.id, receipt_id=attached['source_receipt_id'])
        assert surface['route']['path'] == '/designs/diagnostic'
        assert surface['readiness'] == 'ready'
        assert await core_session.scalar(select(func.count()).select_from(Design)) == 0


def test_native_diagnostic_and_campaign_registration_preserves_design_adapters():
    for model in ('bindcraft2', 'ligandmpnn', 'esmfold2'):
        assert registry.get(f'bms.native-binder.{model}.adapter.v1').entity_kind == 'native_binder_job_result'
    assert registry.get('bms.core-job.esmfold2.adapter.v1').entity_kind == 'typed_core_job_result'
