"""Selected native sources and ordinary placement; inert scratch DB fixtures."""
import gzip
import hashlib
from pathlib import Path

import pytest

from database import (Job, Design, RFD3LocalRedesignRequest,
                      RFD3LocalRedesignCandidate, RFD3LocalRedesignArtifact)
from routers import jobs, files
from schemas import JobCreate
from services.binder_source_materialization import StructureSourceRequest
from tests.test_binder_source_materialization import source_api, stores, cif
from tests.test_remote_rectify_admission import admission, request
from test_project_workflow_setups import setup_store
from test_project_generation_bound_jobs import isolated_core
from test_project_setup_job_preparation import isolate_applied_local_policy


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [False, True])
async def test_selected_source_into_different_project_real_insertion(
    setup_store, isolated_core, isolate_applied_local_policy, tmp_path, monkeypatch, native,
):
    """Real two-store owners; no release/resource or scientific-plan doubles.

    The completed source is an inert persisted result fixture, not a native run.
    Missing release binding must fail this qualification, never skip insertion.
    """
    import copy
    import paths
    from fastapi import BackgroundTasks
    from experiment_models import ExperimentLaunchContext, ExperimentRunAttempt
    from test_project_normalized_child_requests import destination
    from services.global_experiments.launch_contexts import (
        prepare_child_launch_contexts, validate_bound_job,
    )

    root = tmp_path / 'owned'
    roots = {'inputs': root / 'inputs', 'bms_results': root / 'results'}
    for module in (paths, files, jobs):
        monkeypatch.setattr(module, 'get_allowed_roots', lambda: roots)
    async with setup_store() as exp, isolated_core() as core:
        source_context = await destination(exp)
        dest = await destination(exp)
        assert source_context['project_id'] != dest['project_id']
        original, selection = await source_rows(core, root, native)
        source = await core.get(Job, 'source')
        source.provenance = {'launch_context_id': source_context['launch_context_id'],
                             'source_evidence': 'inert completed result fixture'}
        await core.commit()
        source_before = copy.deepcopy({key: getattr(source, key) for key in (
            'params', 'provenance', 'parent_job_id', 'lineage_root_job_id',
            'execution_target_id', 'execution_source_revision', 'execution_source_tree')})
        payload = JobCreate(name='Independent destination prediction', model_id='esmfold2',
            mode='predict', params={'sequence': 'MQIFVK', 'pred_method': 'esmfold2'},
            source_structure=selection, execution_target_id=None, parent_job_id=None)
        await jobs._prepare_selected_structure(payload, core)
        retained = payload.source_structure.model_dump(mode='json', exclude_none=True)
        retained_path = paths.resolve_allowed_path(retained['path'])
        retained_bytes = retained_path.read_bytes()
        original.unlink()
        # Reopen the exact retained source after the original producer is offline.
        clone = JobCreate.model_validate(payload.model_dump(mode='json'))
        identity = await jobs._prepare_selected_structure(clone, core)
        assert identity['owner_job_id'] == 'source'
        assert identity['lineage_root_job_id'] == 'scientific-root'
        assert clone.source_structure == payload.source_structure
        prepared = await prepare_child_launch_contexts(exp,
            destination_launch_context_id=dest['launch_context_id'], job_requests=[clone],
            idempotency_key='independent-selected-source', core_session=core)
        await exp.commit()
        replay = await prepare_child_launch_contexts(exp,
            destination_launch_context_id=dest['launch_context_id'], job_requests=[clone],
            idempotency_key='independent-selected-source', core_session=core)
        assert replay == prepared
        child_request = JobCreate.model_validate(prepared['children'][0]['job_request'])
        assert child_request.parent_job_id is None and child_request.execution_target_id is None
        assert child_request.source_structure == clone.source_structure
        context = await exp.get(ExperimentLaunchContext, child_request.launch_context_id)
        assert context.project_id == dest['project_id'] != source_context['project_id']
        assert context.launch_context_id not in (dest['launch_context_id'], source_context['launch_context_id'])
        token = jobs.current_launch_context_id.set(child_request.launch_context_id)
        try:
            response = await jobs.create_job(child_request, BackgroundTasks(), core,
                                             experiment_session=exp)
        finally:
            jobs.current_launch_context_id.reset(token)
        child_id = response.id
    async with setup_store() as exp, isolated_core() as core:
        child = await core.get(Job, child_id)
        source = await core.get(Job, 'source')
        context = await exp.get(ExperimentLaunchContext, response.launch_context_id)
        attempt = await exp.get(ExperimentRunAttempt, context.run_attempt_id)
        await validate_bound_job(exp, context, child)
        assert context.state == 'consumed' and context.canonical_job_id == child.id
        assert attempt.scheduler_job_id == child.id
        assert context.project_id == dest['project_id']
        assert child.parent_job_id is None and child.execution_target_id is None
        assert child.lineage_root_job_id == 'scientific-root'
        assert child.selection_source_job_id == child.source_stage_job_id == 'source'
        assert child.provenance['source_structure'] == retained
        assert retained_path.read_bytes() == retained_bytes
        assert {key: getattr(source, key) for key in source_before} == source_before
        detail = await jobs.get_job(child.id, core)
        assert detail.source_structure.model_dump(mode='json', exclude_none=True) == retained
        reopened = JobCreate(name='Clone retained source', model_id=child.model_id,
            mode=child.mode, params={'sequence': 'MQIFVK', 'pred_method': 'esmfold2'},
            source_structure=detail.source_structure, execution_target_id=None)
        assert (await jobs._prepare_selected_structure(reopened, core))['owner_job_id'] == 'source'


async def source_rows(session, root, native):
    output = root / 'results/source'
    output.mkdir(parents=True, exist_ok=True)
    raw = gzip.compress(cif(models=(3, 7)).encode())
    path = output / 'exact.cif.gz'
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    job = Job(id='source', name='source', model_id='protein_local_redesign' if native else 'protein_modification_experimental',
              mode='local_redesign' if native else 'de_novo_design', status='completed', params={},
              output_dir=str(output), lineage_root_job_id='scientific-root', execution_target_id='vast:one',
              execution_source_revision='c' * 40, execution_source_tree='d' * 40)
    session.add(job)
    if native:
        session.add(RFD3LocalRedesignRequest(request_id='request', job_id=job.id,
            request_sha256='1' * 64, profile_id='fixture', profile_registry_sha256='2' * 64,
            redesign_mode='partial_diffusion', sequence_policy='preserve', request_json={}))
        session.add(RFD3LocalRedesignCandidate(id='candidate-row', request_id='request',
            candidate_id='candidate', status='generated', artifact_manifest_sha256='3' * 64))
        session.add(RFD3LocalRedesignArtifact(artifact_id='artifact', request_id='request',
            candidate_id='candidate', role='structure', relative_path=path.name, storage_path=str(path),
            content_sha256=digest, size_bytes=len(raw), media_type='chemical/x-mmcif'))
        selection = dict(job_id=job.id, request_id='request', candidate_id='candidate', document={'artifact_id': 'artifact'})
    else:
        session.add(Design(id='design', job_id=job.id, name='candidate', pdb_path=str(path),
            provenance={'artifacts': [{'role': 'candidate_structure', 'relative_path': path.name,
                                      'sha256': digest, 'bytes': len(raw)}]}))
        selection = dict(job_id=job.id, design_id='design')
    if not native:
        job.params = {'rfd3_generation_request': {}, 'rfd3_generation_result_manifest_sha256': '4' * 64,
                      'rfd3_generation_aggregate': {'requested': 1,
                          'length': jobs._rfd3_generation_range([1.0]),
                          'radius_of_gyration': jobs._rfd3_generation_range([0.0])}}
        design = next(row for row in session.new if isinstance(row, Design))
        design.provenance = {**design.provenance, 'metrics': {'residue_count': 1, 'radius_of_gyration': 0.0}}
    await session.commit()
    return path, dict(selection, output_format='pdb', model_number=7)


@pytest.mark.asyncio
@pytest.mark.parametrize('native', [False, True])
async def test_exact_source_conversion_offline_and_wrong_owner(source_api, native):
    client, session, _, root = source_api
    path, selection = await source_rows(session, root, native)
    readback = await (jobs.get_rfd3_local_redesign_result('source', session) if native
                     else jobs.get_rfd3_generation_result('source', session))
    row = readback['candidates'][0]
    assert row['source_structure'] == {**{k: v for k, v in selection.items() if k != 'model_number'},
                                       'output_format': 'native'}
    if not native:
        assert row['design_id'] == 'design'
    else:
        for field, value in [('request_id', 'foreign'), ('candidate_id', 'foreign'),
                             ('document', {'artifact_id': 'foreign'})]:
            rejected = await client.post('/api/files/materialize-structure', json={**selection, field: value})
            assert rejected.status_code == 422, rejected.text
    response = await client.post('/api/files/materialize-structure', json=selection)
    assert response.status_code == 200, response.text
    prepared = response.json()
    assert prepared['model_numbers'] == [3, 7]
    assert prepared['author_residues'] == [{'model_number': 7, 'auth_asym_id': 'a',
        'auth_seq_id': 42, 'insertion_code': 'B', 'residue_name': 'ALA'}]
    assert prepared['source_structure']['expected_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert prepared['source_structure']['path'].endswith('original.cif.gz')
    path.unlink()
    payload = JobCreate(name='independent', model_id='boltz2', mode='predict',
        params={'sequence': 'ACDE', 'boltz_use_msa': False},
        source_structure=prepared['source_structure'], execution_target_id=None)
    before = payload.params.copy()
    identity = await jobs._prepare_selected_structure(payload, session)
    assert identity['owner_job_id'] == 'source'
    assert identity['lineage_root_job_id'] == 'scientific-root'
    assert payload.params == before and payload.parent_job_id is None
    bad = payload.model_copy(deep=True)
    bad.source_structure.job_id = 'foreign'
    with pytest.raises(Exception, match='requested|belong'):
        await jobs._prepare_selected_structure(bad, session)
    from paths import resolve_allowed_path
    resolve_allowed_path(prepared['source_structure']['path']).write_bytes(b'changed')
    with pytest.raises(Exception, match='digest'):
        await jobs._prepare_selected_structure(payload, session)


@pytest.mark.asyncio
async def test_governed_mmcif_gzip_and_truncated_gzip(source_api):
    client, _, _, root = source_api
    path = root / 'inputs/dotted.source.mmcif.gz'
    path.write_bytes(gzip.compress(cif().encode()))
    response = await client.post('/api/files/materialize-structure', json={'path': 'inputs/' + path.name})
    assert response.status_code == 200, response.text
    assert response.json()['native_format'] == 'cif'
    path.write_bytes(path.read_bytes()[:12])
    rejected = await client.post('/api/files/materialize-structure', json={'path': 'inputs/' + path.name})
    assert rejected.status_code == 422, rejected.text


@pytest.mark.asyncio
@pytest.mark.parametrize('target', [None, 'vast:two'])
@pytest.mark.parametrize('destination', ['boltz2', 'proteinmpnn'])
async def test_preview_approved_insert_offline_independent_lineage(admission, setup_store, tmp_path, monkeypatch, target, destination):
    import paths
    from services import nextflow
    from services.remote_execution import bundle
    client, factory = admission
    from experiment_database import get_experiment_session
    async def experiment_sessions():
        async with setup_store() as exp:
            yield exp
    # Jobs preview/create now depends on both stores, even for standalone input.
    monkeypatch.setitem(client._transport.app.dependency_overrides,
                        get_experiment_session, experiment_sessions)
    roots = {'inputs': tmp_path / 'inputs', 'bms_results': tmp_path / 'results'}
    for directory in roots.values():
        directory.mkdir(exist_ok=True)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: roots)
    monkeypatch.setattr(files, 'get_allowed_roots', lambda: roots)
    for module in (paths, jobs, bundle):
        monkeypatch.setattr(module, 'get_results_dir', lambda: roots['bms_results'])
        monkeypatch.setattr(module, 'get_inputs_dir', lambda: roots['inputs'])
        monkeypatch.setattr(module, 'get_data_root', lambda: tmp_path)
    monkeypatch.setattr(nextflow, 'get_work_dir', lambda: tmp_path / 'work')
    async with factory() as session:
        original, selection = await source_rows(session, tmp_path, True)
    if destination == 'proteinmpnn':
        from tests.test_generic_sequence_launch_closeout import request as sequence_request
        payload = sequence_request('proteinmpnn', 'design', tmp_path).model_dump(mode='json')
        payload['params']['input_pdb'] = str(original)
    else:
        payload = request()
    payload.update(execution_target_id=target, source_structure=selection)
    response = await client.post('/jobs/execution-plan/preview', json=payload)
    assert response.status_code == 200, response.text
    preview = response.json()
    payload = preview['request']
    assert payload['source_structure']['path'].startswith('inputs/structure-')
    assert payload['params'].get('boltz_use_msa', False) is False
    original.unlink()
    again = await client.post('/jobs/execution-plan/preview', json=payload)
    assert again.status_code == 200, again.text
    assert again.json()['approval_digest'] == preview['approval_digest']
    if target:
        payload['execution_plan_approval'] = preview['approval_digest']
    response = await client.post('/jobs', json=payload)
    assert response.status_code == 201, response.text
    async with factory() as session:
        child = await session.get(Job, response.json()['id'])
        parent = await session.get(Job, 'source')
        assert child.parent_job_id is None
        assert child.lineage_root_job_id == 'scientific-root'
        assert child.selection_source_job_id == child.source_stage_job_id == 'source'
        assert child.execution_target_id == target
        if target:
            assert child.execution_source_revision == 'a' * 40
        assert parent.execution_source_revision == 'c' * 40
        assert parent.execution_target_id == 'vast:one'
        assert child.provenance['source_structure_identity']['candidate_id'] == 'candidate'
        assert child.provenance['core_protein_requested_params'].get('boltz_use_msa', False) is False
        if destination == 'proteinmpnn':
            assert child.params['mpnn_omitAAs'] == ''
            assert child.params['mpnn_backbone_noise'] == 0.0
            assert child.params['mpnn_relax_max_cycles'] == 0
        invocation = nextflow.compile_job_nextflow_invocation(child, child.params, child.output_dir)
        assert invocation.execution_plan.complete
        if destination == 'proteinmpnn' and target:
            from types import SimpleNamespace
            invocation.materialize_inputs(Path(child.output_dir))
            monkeypatch.setattr(bundle, '_git', lambda *_: 'b' * 40)
            monkeypatch.setattr(bundle, '_runtime_assets', lambda *_, **__: [])
            actual_run = bundle.subprocess.run
            def archive(command, **kwargs):
                if command[:2] == ['git', 'archive']:
                    command = ['git', 'archive', '--format=tar', 'HEAD']
                return actual_run(command, **kwargs)
            monkeypatch.setattr(bundle.subprocess, 'run', archive)
            child.assigned_gpu = 0
            child.provenance = {**child.provenance, 'remote_execution_assignment':
                               {'lease_id': 'fixture', 'gpu_indices': [0]}}
            prepared = bundle.prepare_remote_bundle(job=child,
                target=SimpleNamespace(id=target, remote_root=str(tmp_path / 'remote')),
                command=list(invocation.command), native_invocation=invocation)
            consumed = Path(child.params['input_pdb'])
            assert any(row.role == 'input' and row.sha256 == hashlib.sha256(consumed.read_bytes()).hexdigest()
                       for row in prepared.envelope.files)
    detail = await client.get('/jobs/' + response.json()['id'])
    assert detail.status_code == 200, detail.text
    assert detail.json()['source_structure'] == response.json()['source_structure']
    # Clone the public request envelope, not effective internal runtime params.
    clone = JobCreate.model_validate({**payload, 'name': 'offline clone',
        'source_structure': detail.json()['source_structure'],
        'execution_plan_approval': None, 'execution_target_id': None})
    async with factory() as session:
        assert (await jobs._prepare_selected_structure(clone, session))['owner_job_id'] == 'source'
    assert clone.parent_job_id is None and clone.execution_target_id is None
