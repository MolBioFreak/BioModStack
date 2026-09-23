"""Selected route, private transport, native artifact replay/readback."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from routers import ligandmpnn_interface_context as route
from services import ligandmpnn_interface_publication as publication
from services.ligandmpnn_interface_selection import InterfaceContextSelection
from services.ligandmpnn_interface_selection import selected_submission
from test_ligandmpnn_interface_leaf import fixture, runner
from test_core_protein_scientific_admission import admission


class Rows:
    def __init__(self, rows): self.rows = rows
    def all(self): return self.rows


class Session:
    def __init__(self, source=None, designs=()):
        self.source = source
        self.designs = designs
        self.artifacts = []
    async def get(self, model, identity):
        return self.source if identity == getattr(self.source, 'id', None) else None
    async def scalars(self, query):
        return Rows(self.designs if not self.artifacts else self.artifacts)
    def add(self, row): self.artifacts.append(row)
    async def flush(self): pass


def selection(candidate, source='source'):
    return InterfaceContextSelection.model_validate({
        'action': 'ligandmpnn_interface_context', 'source_job_id': source, 'round_id': source,
        'candidate_ids': [candidate], 'settings': {'binder_chain': 'B', 'target_chain': 'A',
        'target_patch': ['A3', 'A4'], 'seed': 7, 'samples': 1, 'temperature': 0.1}})


def test_selected_mode_and_mounted_route_share_server_owned_selection_contract():
    from main import app
    from model_registry import get_registry
    from services.nextflow import resolve_nextflow_entrypoint
    assert resolve_nextflow_entrypoint(effective_profile='ligandmpnn', model_id='ligandmpnn',
                                       mode='interface_context') == 'workflows/ligandmpnn_interface_context.nf'
    registry = get_registry()
    model = registry.get_model('ligandmpnn')
    assert model is not None
    selected_mode = next(mode for mode in model.modes if mode.id == 'interface_context')
    assert selected_mode.selected_only
    assert registry.validate_job_params('ligandmpnn', 'interface_context',
                                        {'interface_context_manifest': '/managed/selection.json'})
    assert any(inner.path == '/api/ligandmpnn/interface-context/selected'
               for included in app.routes
               for inner in getattr(getattr(included, 'original_router', None), 'routes', ()))


@pytest.mark.asyncio
async def test_selected_route_is_reachable_through_actual_asgi_dispatch():
    import httpx
    from main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url='http://testserver') as client:
        response = await client.post('/api/ligandmpnn/interface-context/selected', json={})
    assert response.status_code == 422
    assert {entry['loc'][-1] for entry in response.json()['detail']} == {
        'action', 'source_job_id', 'round_id', 'candidate_ids', 'settings'}


@pytest.mark.asyncio
async def test_generic_job_submission_cannot_inject_selected_manifest():
    from routers import jobs
    from schemas import JobCreate
    with pytest.raises(HTTPException) as denied:
        await jobs._create_job(JobCreate(name='unowned-selection', model_id='ligandmpnn',
            mode='interface_context', params={'interface_context_manifest': '/tmp/forged.json'}),
            BackgroundTasks(), None)
    assert denied.value.status_code == 403
    assert 'selected interface-context route' in str(denied.value.detail)


@pytest.mark.asyncio
async def test_selected_route_creates_real_queued_job_and_reopens_owned_settings(admission, tmp_path, monkeypatch):
    from database import Design, Job
    from services.nextflow import compile_job_nextflow_invocation
    from routers import jobs
    source_pdb, _, native = fixture(tmp_path)
    monkeypatch.setattr(route, 'get_allowed_roots', lambda: {'selected': tmp_path})
    monkeypatch.setattr(route, 'resolve_runtime_data_path', lambda path: Path(path).resolve())
    monkeypatch.setattr(publication, 'get_inputs_dir', lambda: tmp_path / 'managed-inputs')
    admission.add(Job(id='source', name='native-source', model_id='antibody_denovo',
                      mode='design', status='completed', params={}))
    admission.add(Design(id=native['candidate_id'], job_id='source', name='selected',
                         pdb_path=str(source_pdb)))
    await admission.commit()
    request = selection(native['candidate_id'])
    response = await route.submit_selected(request, BackgroundTasks(), admission)
    job = await admission.get(Job, response['job'].id)
    assert (job.model_id, job.mode) == ('ligandmpnn', 'interface_context')
    assert job.params['selection_source_job_id'] == 'source'
    assert job.params['lineage_root_job_id'] == 'source'
    publication.verify_binding(job.params[publication.KEY])
    invocation = compile_job_nextflow_invocation(job, job.params, str(tmp_path / 'output'))
    assert invocation.execution_plan is not None and invocation.execution_plan.complete, (
        None if invocation.execution_plan is None else invocation.execution_plan.blockers)
    assert {'image:foundry.sif',
            'support_tool:scripts/stage_ligandmpnn_interface_context.py',
            'support_tool:scripts/run_ligandmpnn_interface_context.py'} <= {
                item.logical_id for item in invocation.execution_plan.metadata.dependencies}
    assert invocation.entrypoint == 'workflows/ligandmpnn_interface_context.nf'
    assert invocation.native_parameters['interface_context_manifest'] == job.params['interface_context_manifest']
    assert request.settings.model_dump() == job.params[publication.KEY]['settings']
    from services.remote_execution.bundle import compile_remote_dependencies
    remote_command, remote_params = compile_remote_dependencies('ligandmpnn', 'interface_context',
        list(invocation.command), native_invocation=invocation)
    assert remote_command[remote_command.index('--interface_context_manifest') + 1] == job.params['interface_context_manifest']
    assert '--rfd_models' not in remote_command and '--af2_models' not in remote_command
    assert remote_params['interface_context_manifest'] == job.params['interface_context_manifest']
    # The queued background task is deliberately not run: native GPU execution
    # requires a real worker, but admission and its persisted plan are exercised.


@pytest.mark.asyncio
async def test_route_resolves_design_owner_and_binds_typed_request_before_queue(tmp_path, monkeypatch):
    source_file, _, native = fixture(tmp_path)
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    pdb = inputs / 'selected.pdb'
    pdb.write_bytes(source_file.read_bytes())
    monkeypatch.setattr(route, 'get_allowed_roots', lambda: {'inputs': inputs})
    monkeypatch.setattr(route, 'resolve_runtime_data_path', lambda path: Path(path).resolve())
    monkeypatch.setattr(publication, 'get_inputs_dir', lambda: inputs)
    source = SimpleNamespace(id='source', params={}, model_id='antibody_denovo')
    design = SimpleNamespace(id=native['candidate_id'], job_id='source', pdb_path=str(pdb))
    session = Session(source, [design])
    from routers import jobs
    async def create_job(request, tasks, session):
        assert selected_submission.get()
        assert request.model_id == 'ligandmpnn' and request.mode == 'interface_context'
        from model_registry import get_registry
        assert not get_registry().validate_job_params(request.model_id, request.mode, request.params)
        binding = request.params[publication.KEY]
        publication.verify_binding(binding)
        assert request.params['interface_context_manifest'] == binding['manifest']
        assert binding['settings'] == selection(native['candidate_id']).settings.model_dump()
        assert all(request.params[key] == value for key, value in binding['settings'].items())
        from services.nextflow import compile_nextflow_invocation
        invocation = compile_nextflow_invocation(request.model_id, request.mode, request.params,
                                                 str(tmp_path / 'output'), job_id='child')
        assert invocation.entrypoint == 'workflows/ligandmpnn_interface_context.nf'
        assert invocation.native_parameters['interface_context_manifest'] == binding['manifest']
        assert '--binder_chain' not in invocation.command
        assert '--temperature' not in invocation.command
        return SimpleNamespace(id='child')
    monkeypatch.setattr(jobs, 'create_job', create_job)
    response = await route.submit_selected(selection(native['candidate_id']), BackgroundTasks(), session)
    assert not selected_submission.get()
    assert response['job'].id == 'child'
    assert pdb.read_bytes() == source_file.read_bytes()
    design.job_id = 'foreign'
    with pytest.raises(HTTPException, match='another Job'):
        await route.submit_selected(selection(native['candidate_id']), BackgroundTasks(), session)

@pytest.mark.asyncio
async def test_generic_bc2_cif_derivative_preserves_original_identity(tmp_path, monkeypatch):
    from test_binder_blind_pose_selected import cif
    from routers import jobs
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    native = inputs / 'native.cif'
    native.write_text(cif('B', ['ALA', 'GLY']).replace('\n#\n', '\n') +
                      'ATOM 3 C CA . TYR A 2 3 ? 10.0 0.0 0.0 1.0 20.0 3 TYR A CA 1\n#\n')
    original = native.read_bytes()
    source = SimpleNamespace(id='bc2', model_id='bindcraft2', params={})
    design = SimpleNamespace(id='chosen', job_id='bc2', pdb_path=str(native),
                             provenance={'primary_artifact_id': 'native-artifact'})
    session = Session(source, [design])
    monkeypatch.setattr(route, 'get_allowed_roots', lambda: {'inputs': inputs})
    monkeypatch.setattr(route, 'resolve_runtime_data_path', lambda path: Path(path).resolve())
    monkeypatch.setattr(publication, 'get_inputs_dir', lambda: inputs)
    from services import bindcraft2_publication
    async def readback(job, db): return {}
    monkeypatch.setattr(bindcraft2_publication, 'read_published_native_results', readback)
    async def create_job(request, tasks, db):
        assert selected_submission.get()
        binding = request.params[publication.KEY]
        publication.verify_binding(binding)
        identity = binding['sources']['chosen']['original']
        assert identity == {'path': str(native), 'sha256': hashlib.sha256(original).hexdigest(),
                            'format': '.cif', 'owner_job_id': 'bc2',
                            'primary_artifact_id': 'native-artifact'}
        assert binding['sources']['chosen']['sha256'] != identity['sha256']
        assert ' B ' in Path(binding['sources']['chosen']['path']).read_text()
        return SimpleNamespace(id='child')
    monkeypatch.setattr(jobs, 'create_job', create_job)
    chosen = selection('chosen', 'bc2').model_copy(update={'settings':
        selection('chosen', 'bc2').settings.model_copy(update={'target_patch': ['A3']})})
    await route.submit_selected(chosen, BackgroundTasks(), session)
    assert native.read_bytes() == original
    assert not selected_submission.get()


@pytest.mark.asyncio
async def test_native_publication_reopens_exact_hashed_files_and_rejects_tamper(tmp_path, monkeypatch):
    source, _, native = fixture(tmp_path)
    inputs = tmp_path / 'inputs'
    monkeypatch.setattr(publication, 'get_inputs_dir', lambda: inputs)
    chosen = selection(native['candidate_id'])
    chosen = chosen.model_copy(update={'settings': chosen.settings.model_copy(update={'target_patch': native['target_patch']})})
    binding = publication.materialize(chosen, {native['candidate_id']: source.read_bytes()})
    root = tmp_path / 'output'
    result_dir = root / 'ligandmpnn_interface_context/000/context_result'
    result_dir.mkdir(parents=True)
    reference, complex_pdb, ablated_pdb = runner.prepare(source, native['target_patch'], 'B')
    conditions = {}
    for label, name, content in [('supplied_complex', 'masked_complex.pdb', complex_pdb),
                                 ('without_binder', 'masked_without_binder.pdb', ablated_pdb)]:
        (result_dir / name).write_text(content)
        conditions[label] = {'masked_input_sha256': hashlib.sha256((result_dir / name).read_bytes()).hexdigest(),
                             'samples': [{'batch_idx': 0, 'design_idx': 0, 'sampled_patch': reference,
                                          'patch_residue_count': len(reference), 'patch_exact_matches': len(reference)}]}
    payload = {'schema': 'bms.ligandmpnn.interface-context.experimental.v1',
        'status': 'completed_unclassified', 'qualification': 'unqualified', 'model_type': 'ligand_mpnn',
        'method': 'masked_whole_patch_sampling_with_binder_ablation',
        'checkpoint_sha256': runner.CHECKPOINT_SHA256, 'foundry_version': runner.VERSION,
        'candidate_id': native['candidate_id'], 'round_id': 'source',
        'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'target_patch': native['target_patch'], 'reference_patch': reference,
        'fixed_binder_chain': 'B', 'target_chain': 'A', 'seed': 7, 'samples': 1,
        'temperature': 0.1, 'conditions': conditions}
    (result_dir / 'result.json').write_text(json.dumps(payload))
    job = SimpleNamespace(id='child', output_dir=str(root), params={publication.KEY: binding}, provenance={})
    session = Session()
    receipt = await publication.publish_selected(job, root, session)
    assert len(session.artifacts) == 3
    reopened = await publication.read_selected(job, session)
    assert [dict((key, value) for key, value in row.items() if key != 'conditions')
            for row in reopened['records']] == receipt['records']
    assert receipt['records'][0]['status'] == 'completed_unclassified'
    assert 'verdict' not in receipt
    assert receipt == await publication.publish_selected(job, root, session)
    (result_dir / 'result.json').write_text('{}')
    with pytest.raises(ValueError):
        await publication.read_selected(job, session)
    (result_dir / 'result.json').write_text(json.dumps(payload))

    # Production caller, not only an ingester helper: it must complete with zero
    # Designs while registering and reopening native artifacts transactionally.
    from test_result_state_integrity import _session_factory, _job
    from services.result_state_integrity import finalize_successful_job
    from database import Job, JobArtifact, Design
    from sqlalchemy import select
    factory, engine = await _session_factory(tmp_path)
    try:
        async with factory() as db:
            child = _job('child', model_id='ligandmpnn', mode='interface_context',
                         params={publication.KEY: binding}, output_dir=str(root))
            db.add(child)
            await db.commit()
            completed = await finalize_successful_job(child, str(root), db)
            assert completed.completed and completed.design_count == 0
        async with factory() as db:
            reopened_job = await db.get(Job, 'child')
            assert reopened_job.status == 'completed'
            assert len((await db.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == 'child'))).all()) == 3
            assert not (await db.scalars(select(Design).where(Design.job_id == 'child'))).all()
            assert (await publication.read_selected(reopened_job, db))['records'][0]['conditions'] == conditions
    finally:
        await engine.dispose()
