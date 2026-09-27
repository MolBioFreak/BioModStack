"""General-generation retained rounds through real scratch Jobs; no native sampling."""
from copy import deepcopy
import asyncio
import json
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import Design, Job
from routers import jobs
from schemas import JobCreate, JobResponse
from services import binder_round as rounds
from services.sequence_round_inputs import MODES, REQUEST, normalize_request
from services.nextflow import compile_job_nextflow_invocation
from test_binder_continuation import selected, PDB
from test_project_workflow_setups import setup_store

ENGINES = list(MODES)


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ENGINES)
@pytest.mark.parametrize('remote', [False, True])
async def test_project_destination_uses_existing_bound_job_owner(selected, setup_store, model, remote):
    from test_project_normalized_child_requests import destination
    from experiment_models import ExperimentLaunchContext
    from services.global_experiments.launch_contexts import validate_bound_job
    session, root, _ = await root_request(selected, model, remote=remote)
    async with setup_store() as experiments:
        dest = await destination(experiments)
        await experiments.commit()
        root.provenance = {**root.provenance, 'launch_context_id': dest['launch_context_id']}
        await session.commit()
        result = await rounds.reconcile_round(session, experiments, root.id)
        step = next(iter(result['steps'].values()))
        if not remote and step.get('state') == 'error' and 'resource_source_revision_unavailable' in step.get('error', ''):
            assert not list(await session.scalars(select(Job).where(Job.model_id == model)))
            pytest.skip('Real Project resource admission requires parent release binding; no authority mocked')
        if remote:
            assert step['state'] == 'review_required', result
            context_id = step['request']['launch_context_id']
        else:
            assert step['state'] == 'queued', result
            child = await session.get(Job, step['job_id'])
            context_id = child.provenance['launch_context_id']
        assert context_id != dest['launch_context_id']
        context = await experiments.get(ExperimentLaunchContext, context_id)
        assert context.project_id == dest['project_id'] and context.run_attempt_id
        if not remote:
            await validate_bound_job(experiments, context, child)
            assert child.provenance[rounds.STEP]['source_design_id'] == 'd0'
        again = await rounds.reconcile_round(session, experiments, root.id)
        assert again['steps'] == result['steps']


@pytest.mark.asyncio
@pytest.mark.parametrize('generator', ['rfd3', 'disco', 'laproteina'])
async def test_create_resubmit_retains_envelope_outside_generator_science(selected, generator):
    from fastapi import Request, Response
    session = selected[1]
    params = ({'generator': generator, 'min_length': 90, 'max_length': 90, 'num_designs': 1}
              if generator == 'rfd3' else {'generator': generator, 'backend': generator,
                  'design_task': 'unconditional', 'num_designs': 1, 'target_lengths': '90'})
    request = JobCreate(name='general-sequence', model_id='protein_modification_experimental',
        mode='de_novo_design', params=params, sequence_design=envelope('fampnn', enabled=False))
    plain = request.model_copy(update={'sequence_design': None}, deep=True)
    assert jobs.normalize_job_request(request).params == jobs.normalize_job_request(plain).params
    response = await jobs.create_job(request, BackgroundTasks(), session)
    created = await session.get(Job, response.id)
    assert response.sequence_design.model_dump(mode='json') == envelope('fampnn', enabled=False)
    assert created.provenance[REQUEST] == response.sequence_design.model_dump(mode='json')
    assert 'sequence_design' not in created.params
    created.status = 'failed'
    await session.commit()
    await jobs.resubmit_job(created.id, Request({'type': 'http', 'headers': []}), Response(), session)
    copies = list(await session.scalars(select(Job).where(Job.name == 'general-sequence_resubmit')))
    assert len(copies) == 1
    assert JobResponse.model_validate(copies[0]).sequence_design == response.sequence_design


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ['proteinmpnn', 'fampnn'])
async def test_source_chain_roster_and_native_constraints_not_invented_roles(selected, setup_store, model):
    session, root, _ = await root_request(selected, model)
    design = await session.get(Design, 'd0')
    Path(design.pdb_path).write_text(PDB.replace('ALA A', 'ALA q').replace('END\n', '') +
                                    PDB.replace('ALA A', 'GLY T'))
    root.provenance = {REQUEST: envelope(model, params={'fixed_positions': 'q:1'})}
    await session.commit()
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        child = await session.get(Job, next(iter(result['steps'].values()))['job_id'])
        assert child is not None, result
        assert child.params['design_chain'] == 'q,T' and child.params['target_chain'] == ''
        assert child.mode == 'design'
        if model == 'fampnn':
            from scripts.prep_fampnn_constraints_generic import constraints, pdb_domain
            native = constraints(pdb_domain(child.params['input_pdb']),
                                 {**child.params, 'sequence_design_mode': 'design'})
            assert native is not None
        else:
            from scripts.proteinmpnn_native_binding import role_contract
            native = role_contract(child.params['input_pdb'], child.params)
            assert native['designed_chains'] == ['q', 'T'] and native['target_chains'] == []
            assert native['fixed_positions'] == [['q', 1, '']]


@pytest.mark.asyncio
async def test_caliby_native_cif_conformer_constraints_and_falsey_settings(selected, setup_store, monkeypatch):
    from Bio.PDB import PDBParser, MMCIFIO
    session, root, tmp = await root_request(selected, 'caliby_experimental')
    design = await session.get(Design, 'd0')
    writer = MMCIFIO()
    writer.set_structure(PDBParser(QUIET=True).get_structure('fixture', design.pdb_path))
    cif = tmp / 'native.cif'
    writer.save(str(cif))
    design.pdb_path = str(cif)
    def no_conversion(*args):
        raise AssertionError('Native Caliby must not require a PDB derivative')
    monkeypatch.setattr(jobs, '_cif_selection_pdb', no_conversion)
    settings = {'fixed_pos_seq': 'A1', 'fixed_pos_scn': '', 'fixed_pos_override_seq': '',
                'pos_restrict_aatype': '', 'symmetry_pos': ''}
    root.provenance = {REQUEST: envelope('caliby_experimental', input_settings=settings,
        params={'verbose': False, 'gaussian_n_conformers': 0, 'omit_aas': []})}
    await session.commit()
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        child = await session.get(Job, next(iter(result['steps'].values()))['job_id'])
        assert child is not None, result
        state = child.params['ensembles'][0]['states'][0]
        assert {key: state[key] for key in settings} == settings
        assert Path(state['path']).suffix == '.cif'
        assert Path(state['path']).read_bytes() == cif.read_bytes()
        assert child.params['verbose'] is False and child.params['gaussian_n_conformers'] == 0
        assert child.params['omit_aas'] == []


def test_caliby_settings_are_native_schema_projections():
    from services.caliby_native import Conformer, EnsembleDesign
    from services.sequence_round_inputs import CalibySettings, ConformerSettings
    assert set(CalibySettings.model_fields) == set(EnsembleDesign.model_fields) - {'ensembles'}
    assert set(ConformerSettings.model_fields) == set(Conformer.model_fields) - {'state_id', 'path'}
    for child, owner in [(CalibySettings, EnsembleDesign), (ConformerSettings, Conformer)]:
        for key, field in child.model_fields.items():
            assert field.annotation == owner.model_fields[key].annotation
            assert field.metadata == owner.model_fields[key].metadata
            assert field.get_default(call_default_factory=True) == owner.model_fields[key].get_default(call_default_factory=True)


@pytest.mark.parametrize('model,values', [
    ('proteinmpnn', {'mpnn_omitAAs': '', 'mpnn_relax_output': False, 'mpnn_backbone_noise': 0}),
    ('fampnn', {'fampnn_seed': 0, 'fampnn_presort_by_length': False, 'fampnn_psce_threshold': None}),
    ('caliby_experimental', {'omit_aas': [], 'verbose': False, 'num_workers': 0}),
])
def test_explicit_native_falsey_values(model, values):
    saved = envelope(model, params=values)
    assert {key: saved['params'][key] for key in values} == values


@pytest.mark.parametrize('model', ENGINES)
def test_source_bound_and_unknown_science_not_silently_discarded(model):
    for key in ('input_pdb', 'design_chain', 'target_chain', 'ensembles', 'invented_temperature'):
        with pytest.raises(ValueError):
            envelope(model, params={key: 'caller-supplied'})
    with pytest.raises(ValueError):
        envelope(model, input_settings={'path': '/caller/source.pdb'})


@pytest.mark.asyncio
async def test_general_scheduler_recovers_and_terminal_round_avoids_source_io(selected, setup_store):
    session, root, _ = await root_request(selected, 'caliby_experimental')
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    await session.rollback()
    await rounds.recover_rounds(factory, setup_store)
    async with factory() as core:
        progress = await rounds.read_round(core, 'root')
        child = await core.get(Job, next(iter(progress['steps'].values()))['job_id'])
        assert child is not None, progress
        child.status = 'completed'
        await core.commit()
    await rounds.recover_rounds(factory, setup_store)
    async with factory() as core:
        progress = await rounds.read_round(core, 'root')
        assert progress['state'] == 'completed'
        design = await core.get(Design, 'd0')
        Path(design.pdb_path).unlink()
    await rounds.recover_rounds(factory, setup_store)
    async with factory() as core:
        assert (await rounds.read_round(core, 'root'))['steps'] == progress['steps']


def envelope(model, **changes):
    value = dict(model_id=model, enabled=True, params={})
    value.update(changes)
    return normalize_request(value).model_dump(mode='json')


async def root_request(selected, model, *, remote=False):
    _, session, root, _, tmp = selected
    root.provenance = {REQUEST: envelope(model)}
    root.execution_target_id = 'vast:sequence-fixture' if remote else None
    await session.commit()
    return session, root, tmp


@pytest.mark.asyncio
@pytest.mark.parametrize('model,key,filename', [('proteinmpnn', 'mpnn_bias_AA_jsonl', 'bias.jsonl'),
    ('fampnn', 'fampnn_checkpoint_path', 'custom.pt')])
async def test_model_owned_auxiliary_file_alias_reaches_child(selected, setup_store, monkeypatch, model, key, filename):
    from routers import files
    session, root, tmp = await root_request(selected, model)
    source = tmp / filename
    source.write_text('{"A": 0.0}\n' if model == 'proteinmpnn' else 'inert checkpoint transport fixture')
    monkeypatch.setattr(files, 'get_allowed_roots', lambda: {'downloads': tmp})
    monkeypatch.setattr(jobs, 'resolve_allowed_path', files._allowed_lexical_path)
    root.provenance = {REQUEST: envelope(model, params={key: 'downloads/' + filename})}
    await session.commit()
    async with setup_store() as experiments:
        progress = await rounds.reconcile_round(session, experiments, root.id)
        step = next(iter(progress['steps'].values()))
        assert step['state'] == 'queued', progress
        child = await session.get(Job, step['job_id'])
        assert child.params[key] == str(source)


@pytest.mark.parametrize('model', ENGINES)
def test_model_owned_defaults_and_clone(model):
    saved = envelope(model)
    assert normalize_request(saved).model_dump(mode='json') == saved
    response = JobResponse.model_validate(Job(id='root', name='root', status='completed',
        model_id='rfdiffusion3', mode='design', params={}, provenance={REQUEST: saved}))
    assert response.sequence_design.model_dump(mode='json') == saved
    assert not response.params
    assert not any(saved['params'].get(key) for key in ('binder_chains', 'target_chains', 'design_chain', 'target_chain'))


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ENGINES)
async def test_real_retained_plan_job_compiler_replay_failure_independence(selected, setup_store, model, monkeypatch):
    from services import nextflow
    session, root, tmp = await root_request(selected, model)
    monkeypatch.setattr(nextflow, 'get_data_root', lambda: tmp)
    import paths
    monkeypatch.setattr(paths, 'get_inputs_dir', lambda: tmp / 'inputs')
    monkeypatch.setattr(paths, 'get_results_dir', lambda: tmp / 'results')
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert result['kind'] == 'general_sequence_design'
        assert result['state'] == 'running', result
        assert len(result['steps']) == 1
        step = next(iter(result['steps'].values()))
        assert step['state'] == 'queued', step
        child = await session.get(Job, step['job_id'])
        assert (child.model_id, child.mode) == (model, MODES[model])
        assert child.lineage_root_job_id == root.id
        assert child.selection_source_job_id == root.id
        assert child.parent_job_id is None
        metadata = child.provenance[rounds.STEP]
        assert metadata['source_design_id'] == 'd0'
        assert metadata['candidate_key'] == 'key-0'
        assert metadata['binder_chains'] == metadata['target_chains'] == []
        assert not child.params.get('target_chain')
        if model != 'caliby_experimental':
            assert child.params['design_chain'] == 'A'
        assert Path(metadata['source_binding']['path']).read_text() == PDB
        retained = JobCreate.model_validate(step['request'])
        assert jobs.normalize_job_request(retained).model_dump() == retained.model_dump()
        invocation = compile_job_nextflow_invocation(child, deepcopy(child.params), str(tmp / 'compiled'))
        assert invocation.entrypoint == ('workflows/caliby_native.nf' if model == 'caliby_experimental'
                                         else 'workflows/protein_sequence_design.nf')
        invocation.materialize_inputs(tmp / 'compiled')
        if model != 'caliby_experimental':
            settings = json.loads(next(item.payload for item in invocation.generated_inputs
                if item.relative_path == '.sequence-design-settings.json'))
            assert settings['iteration_source_design_ids'] == ['d0']
        else:
            assert child.params['ensembles'][0]['ensemble_id'] == 'd0'
            assert len(child.params['ensembles'][0]['states']) == 1
        # Exercise real portable input inventory and relocation from the actual
        # inserted Job/compiler, not a hand-authored NativeInvocation.
        from services.remote_execution import bundle
        from scripts.lib.portable_inputs import resolve_input_path
        import hashlib
        import shutil
        for getter, directory in [('get_data_root', tmp), ('get_inputs_dir', tmp / 'inputs'),
                                   ('get_results_dir', tmp / 'results')]:
            monkeypatch.setattr(bundle, getter, lambda directory=directory: directory)
        refs = []
        assets = bundle._input_assets(invocation.native_parameters, native_invocation=invocation,
            repo_root=Path(__file__).parents[3], runtime_paths=set(), output_dir=tmp / 'compiled', references=refs)
        assert assets and refs
        attempt = tmp / 'worker'
        transfers, records, inventory = [], [], {}
        for source, relative in assets:
            prefix = 'inputs/' + relative
            members = bundle._input_records(source, prefix, native_invocation=invocation, output_dir=tmp / 'compiled')
            records.extend(members)
            remote = attempt / 'bundle' / prefix
            remote.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, remote)
            else:
                shutil.copyfile(source, remote)
            transfers.append(bundle.TransferPlan(source, str(remote)))
            for record in members:
                original = source / record.relative_path[len(prefix):].lstrip('/') if source.is_dir() else source
                inventory[str(original)] = (record.sha256, record.size_bytes)
        for ref in refs:
            assert inventory[ref['source_path']] == (ref['sha256'], ref['size_bytes'])
        staging = tmp / 'bindings'
        staging.mkdir()
        binding, _ = bundle._write_portable_bindings(staging_root=staging, remote_attempt=str(attempt),
            references=refs, input_transfers=transfers, input_records=records,
            remote_runtime=str(attempt / 'runtime'), remote_results=str(attempt / 'results'))
        monkeypatch.setenv('BMS_PORTABLE_INPUT_BINDINGS', str(binding.source))
        for ref in refs:
            relocated = resolve_input_path(ref['source_path'])
            assert attempt in relocated.parents
            assert hashlib.sha256(relocated.read_bytes()).hexdigest() == ref['sha256']
        # Originals disappear, but retained snapshots serve replay/retry and the
        # transfer binding still resolves exact generated source identities.
        Path((await session.get(Design, 'd0')).pdb_path).unlink()
        again = await rounds.reconcile_round(session, experiments, root.id)
        assert again['steps'] == result['steps']
        child.status = 'failed'
        await session.commit()
        failed = await rounds.reconcile_round(session, experiments, root.id)
        assert failed['state'] == 'completed_with_errors'
        assert root.status == 'completed'
        retried = await rounds.reconcile_round(session, experiments, root.id, retry=True)
        assert retried['state'] == 'running', retried
        assert len(list(await session.scalars(select(Job).where(Job.model_id == model)))) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ENGINES)
async def test_remote_preparation_is_retained_without_original_source(selected, setup_store, model):
    session, root, tmp = await root_request(selected, model, remote=True)
    async with setup_store() as experiments:
        first = await rounds.reconcile_round(session, experiments, root.id)
        assert first['state'] == 'review_required', first
        step = next(iter(first['steps'].values()))
        assert step['request']['binder_round_step']['root_job_id'] == root.id
        Path((await session.get(Design, 'd0')).pdb_path).unlink()
        again = await rounds.reconcile_round(session, experiments, root.id)
        assert again['steps'] == first['steps']
        retained = JobCreate.model_validate(step['request'])
        metadata, _, existing = await rounds.bind_step(session, retained)
        assert metadata['source_design_id'] == 'd0' and existing is None
        retained.params['num_parallel_jobs'] = 2
        with pytest.raises(HTTPException, match='retained request changed'):
            await rounds.bind_step(session, retained)
        await session.rollback()
        response = await selected[0].get('/api/binder-continuation/root/round')
        assert response.json()['steps'] == first['steps']


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['absent', 'disabled', 'cancelled', 'empty'])
async def test_generation_only_and_no_source_cases(selected, setup_store, state):
    session, root, _ = await root_request(selected, 'fampnn')
    if state == 'absent':
        root.provenance = {}
    elif state == 'disabled':
        root.provenance = {REQUEST: envelope('fampnn', enabled=False)}
    elif state == 'cancelled':
        root.status = 'cancelled'
    else:
        await session.delete(await session.get(Design, 'd0'))
    await session.commit()
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert not result['steps']
        assert result['state'] == dict(absent='not_requested', disabled='generation_only',
                                      cancelled='cancelled', empty='completed')[state]


@pytest.mark.asyncio
@pytest.mark.parametrize('model', ENGINES)
async def test_zero_design_child_success_does_not_spawn_prediction(selected, setup_store, model):
    session, root, _ = await root_request(selected, model)
    async with setup_store() as experiments:
        result = await rounds.reconcile_round(session, experiments, root.id)
        child = await session.get(Job, next(iter(result['steps'].values()))['job_id'])
        assert child is not None, result
        child.status = 'completed'
        await session.commit()
        result = await rounds.reconcile_round(session, experiments, root.id)
        assert result['state'] == 'completed' and len(result['steps']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('empty', [False, True])
async def test_actual_caliby_child_native_publication_and_round_readback(selected, setup_store, monkeypatch, empty):
    import runpy
    import sys
    import types
    from test_sequence_native_publication import CIF
    from services.result_state_integrity import finalize_successful_job
    from routers.sequence_native import get_caliby_native_results
    session, root, tmp = await root_request(selected, 'caliby_experimental')
    async with setup_store() as experiments:
        progress = await rounds.reconcile_round(session, experiments, root.id)
        child = await session.get(Job, next(iter(progress['steps'].values()))['job_id'])
        assert child is not None, progress
        # Only scientific kernels are inert. Real prepared bytes, producer
        # serializer, native publication, shared finalizer and API reader run.
        repo = Path(__file__).parents[3]
        monkeypatch.syspath_prepend(str(repo / 'scripts'))
        runtime = runpy.run_path(str(repo / 'scripts/run_caliby_experimental.py'))
        class InertModel:
            sampling_cfg = {}
            def ensemble_sample(self, mapping, *, out_dir, **kwargs):
                folder = Path(out_dir) / 'packed_samples'
                folder.mkdir(parents=True)
                paths = []
                if not empty:
                    candidate = folder / 'sample.cif'
                    candidate.write_text(CIF)
                    paths.append(str(candidate))
                return {'example_id': [] if empty else ['state_000000'], 'out_pdb': paths,
                        'seq': [] if empty else ['A'], 'U': [] if empty else [-1.25],
                        'input_seq': [] if empty else ['A']}
        api = types.ModuleType('caliby.api')
        api._merge_sampling_cfg = lambda cfg, **kwargs: {**cfg, **kwargs}
        package = types.ModuleType('caliby')
        package.api = api
        monkeypatch.setitem(sys.modules, 'caliby', package)
        monkeypatch.setitem(sys.modules, 'caliby.api', api)
        omega = types.ModuleType('omegaconf')
        omega.OmegaConf = types.SimpleNamespace(to_container=lambda cfg, **kwargs: cfg)
        monkeypatch.setitem(sys.modules, 'omegaconf', omega)
        runtime['run'].__globals__.update(preflight_caliby_runtime=lambda **kwargs: {'task': child.mode, 'model_name': 'inert-transport'},
                                         load_caliby_model=lambda _: InertModel())
        document = runtime['run'](Path(child.params['caliby_request_dir']), Path(child.output_dir) / 'caliby_native')
        child.status = child.queue_status = 'running'
        await session.commit()
        finished = await finalize_successful_job(child, child.output_dir, session)
        assert finished.completed and finished.design_count == 0, child.error_message
        assert not list(await session.scalars(select(Design).where(Design.job_id == child.id)))
        result = await get_caliby_native_results(child.id, session)
        assert result['request'] == document['request']
        assert len(result['records']) == (0 if empty else 1)
        if not empty:
            assert result['records'][0]['source']['ensemble_id'] == 'd0'
        progress = await rounds.reconcile_round(session, experiments, root.id)
        assert progress['state'] == 'completed' and len(progress['steps']) == 1
        assert root.status == 'completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('after_commit', [False, True])
async def test_cancellation_and_concurrent_replay(selected, setup_store, monkeypatch, after_commit):
    session, root, _ = await root_request(selected, 'proteinmpnn')
    name = 'submit_selected_child_jobs' if after_commit else 'create_job'
    original = getattr(jobs, name)
    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise asyncio.CancelledError()
    monkeypatch.setattr(jobs, name, interrupted)
    async with setup_store() as experiments:
        with pytest.raises(asyncio.CancelledError):
            await rounds.reconcile_round(session, experiments, 'root')
        children = list(await session.scalars(select(Job).where(Job.model_id == 'proteinmpnn')))
        assert len(children) == int(after_commit)
        await session.rollback()
    monkeypatch.setattr(jobs, name, original)
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    async def poll():
        async with factory() as core, setup_store() as experiments:
            return await rounds.reconcile_round(core, experiments, 'root')
    await asyncio.gather(poll(), poll())
    async with factory() as core:
        children = list(await core.scalars(select(Job).where(Job.model_id == 'proteinmpnn')))
        assert len(children) == 1
        assert children[0].provenance[rounds.STEP]['source_design_id'] == 'd0'
