"""Selected API materialization -> leaf -> verified attachment on CPU fixtures."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient

from database import Design, Job, JobArtifact, get_session
from routers import binder_blind_pose as route
from services import binder_blind_pose_selected as selected


def pdb(chain, residues, offset=0):
    return ''.join(f'ATOM  {i:5d}  CA  {res:3s} {chain}{i:4d}    {offset+i:8.3f}{offset+i:8.3f}{offset+i:8.3f}  1.00 30.00           C\n'
                   for i, res in enumerate(residues, 1)) + 'END\n'


class Result:
    def __init__(self, rows):
        self.rows = rows
    def all(self):
        return self.rows


class Session:
    def __init__(self):
        self.artifacts = []
    def add(self, row):
        self.artifacts.append(row)
    async def flush(self):
        pass
    async def scalars(self, query):
        return Result(self.artifacts)


@pytest.fixture
def source(tmp_path, monkeypatch):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    monkeypatch.setattr(selected, 'get_inputs_dir', lambda: inputs)
    monkeypatch.setattr(selected, 'get_allowed_roots', lambda: {'inputs': inputs})
    monkeypatch.setattr(selected, 'resolve_runtime_data_path', lambda path: Path(path).resolve())
    candidates = []
    for idx, offset in enumerate((3, 2000)):
        path = inputs / f'design-{idx}.pdb'
        path.write_text(pdb('B', ['ALA', 'GLY'], offset) + pdb('T', ['TRP'], 900))
        candidates.append(SimpleNamespace(id=f'design-{idx}', job_id='source', pdb_path=str(path)))
    target = inputs / 'target.pdb'
    target.write_text(pdb('T', ['TYR'], 10))
    job = SimpleNamespace(id='source', params={'target_pdb': str(target)}, lineage_root_job_id=None)
    return job, candidates, inputs, target


def test_selected_source_geometry_and_candidate_target_cannot_leak(source):
    job, designs, inputs, target = source
    destination = inputs / 'selected'
    binding = selected.prepare_selected(job, designs, target_pdb=str(target),
                                        binder_chains={d.id: ['B'] for d in designs},
                                        target_chains=['T'], directory=destination)
    params = selected.launch_params(destination, variant='fast', model_id_or_path='',
                                    num_loops=1, num_sampling_steps=25, num_diffusion_samples=1, seed=7)
    from run_binder_blind_pose import compile_selected_inputs
    rows = compile_selected_inputs(json.loads(Path(params['blind_pose_selection_manifest']).read_text()),
                                   destination, Path(params['target_pdb']))
    assert [c['sequence'] for c in rows[0]['components']] == ['AG', 'Y']
    assert [row['components'] for row in rows] == [rows[0]['components']] * len(rows)
    assert [c['design_id'] for c in binding['candidates']] == [d.id for d in designs]
    assert binding['target_source_sha256'] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert not {'coordinates', 'template', 'restraints', 'msa'} & set(rows[0]['components'][0])


def test_foreign_owner_is_rejected_before_materializing(source):
    job, designs, inputs, target = source
    designs[1].job_id = 'foreign'
    with pytest.raises(selected.BlindPoseError, match='foreign'):
        selected.prepare_selected(job, designs, target_pdb=str(target),
                                  binder_chains={d.id: ['B'] for d in designs},
                                  target_chains=['T'], directory=inputs / 'selected')
    assert not (inputs / 'selected').exists()


@pytest.mark.asyncio
async def test_request_leaf_cpu_fixture_attachment_and_readback(source, monkeypatch, tmp_path):
    job, designs, inputs, target = source
    directory = inputs / 'selected'
    binding = selected.prepare_selected(job, designs, target_pdb=str(target),
                                        binder_chains={d.id: ['B'] for d in designs},
                                        target_chains=['T'], directory=directory)
    from run_binder_blind_pose import run
    def native(command, check):
        assert check and '--complex-components-file' in command
        assert '--target-pdb' not in command and '--pdb-sequence-path' not in command
        components = json.loads(Path(command[command.index('--complex-components-file') + 1]).read_text())
        assert [row['sequence'] for row in components] == ['AG', 'Y']
        output = Path(command[command.index('--output-dir') + 1])
        key = command[command.index('--sequence-name') + 1]
        sample = key + '_000'
        (output / (sample + '.cif')).write_text('data_sample\n')
        (output / (sample + '.metrics.json')).write_text(json.dumps({'sample_id': sample, 'cif': sample + '.cif', 'iptm': .2}))
        (output / 'manifest.json').write_text(json.dumps({'workflow': 'esmfold2', 'sequence_name': key,
                                                           'sample_count': 1, 'samples': [{
            'sample_id': sample, 'cif': sample + '.cif', 'metrics': sample + '.metrics.json'}]}))
    monkeypatch.setattr('run_binder_blind_pose.subprocess.run', native)
    output = tmp_path / 'results'
    run(directory / 'selection.json', directory, directory / 'target.pdb', output / 'blind_pose_results',
        model_variant='fast', model_id_or_path='', num_loops=1, num_sampling_steps=25,
        num_diffusion_samples=1, seed=7, device='cpu', runner=tmp_path / 'native.py')
    child = SimpleNamespace(id='child', params={selected.KEY: binding,
        **selected.launch_params(directory, variant='fast', model_id_or_path='', num_loops=1,
                                 num_sampling_steps=25, num_diffusion_samples=1, seed=7)}, provenance={},
                            retry_count=0, remote_attempt_id=None, output_dir=str(output))
    session = Session()
    published = await selected.publish_selected(child, output, session)
    assert len(published['records']) == len(designs)
    result = await selected.read_selected(child, session)
    assert [row['design_id'] for row in result['records']] == [d.id for d in designs]
    receipt = output / 'blind_pose_results/blind_pose_receipt.json'
    original_receipt = receipt.read_text()
    changed = json.loads(original_receipt)
    changed['requested_settings']['num_sampling_steps'] = 999
    receipt.write_text(json.dumps(changed))
    with pytest.raises(selected.BlindPoseError, match='native settings'):
        await selected.read_selected(child, session)
    receipt.write_text(original_receipt)
    assert all(row['classification'] == 'unclassified' for row in result['records'])
    assert len(session.artifacts) == len(designs) * 4 + 1
    await selected.publish_selected(child, output, session)
    assert len(session.artifacts) == len(designs) * 4 + 1
    (output / 'blind_pose_results/candidate-0000/candidate-0000_000.cif').write_text('tampered')
    with pytest.raises(selected.BlindPoseError, match='bytes changed'):
        await selected.read_selected(child, session)


def cif(chain, sequence, offset=0):
    rows = []
    for index, residue in enumerate(sequence, 1):
        rows.append(f'ATOM {index} C CA . {residue} {chain} 1 {index} ? {offset+index}.0 0.0 0.0 1.0 20.0 {index} {residue} {chain} CA 1')
    return ('data_native\nloop_\n' + '\n'.join('_atom_site.' + field for field in (
        'group_PDB id type_symbol label_atom_id label_alt_id label_comp_id label_asym_id label_entity_id '
        'label_seq_id pdbx_PDB_ins_code Cartn_x Cartn_y Cartn_z occupancy B_iso_or_equiv '
        'auth_seq_id auth_comp_id auth_asym_id auth_atom_id pdbx_PDB_model_num').split()) + '\n'
        + '\n'.join(rows) + '\n#\n')


def test_bc2_native_cif_binder_and_declared_target_do_not_leak(source):
    job, designs, inputs, _ = source
    job.model_id = 'bindcraft2'
    candidate = inputs / 'native.cif'
    candidate.write_text(cif('B', ['ALA', 'GLY'], 999) + '')
    # Native candidate complex includes an unrelated target-state sequence.
    candidate.write_text(candidate.read_text().replace('\n#\n', '\n') +
                         'ATOM 3 C CA . TRP T 2 1 ? 900.0 0.0 0.0 1.0 20.0 1 TRP T CA 1\n#\n')
    designs[0].pdb_path = str(candidate)
    declared = inputs / 'declared.cif'
    declared.write_text(cif('T', ['TYR']))
    job.params = {'bindcraft2_settings': {'targets': [{'name': 'on', 'target_path': str(declared)}]}}
    binding = selected.prepare_selected(job, designs[:1], target_pdb=str(declared),
        binder_chains={designs[0].id: ['B']}, target_chains=['T'], directory=inputs / 'bc2-selected')
    params = selected.launch_params(inputs / 'bc2-selected', variant='fast', model_id_or_path='',
        num_loops=1, num_sampling_steps=25, num_diffusion_samples=1, seed=7)
    from run_binder_blind_pose import compile_selected_inputs
    compiled = compile_selected_inputs(json.loads(Path(params['blind_pose_selection_manifest']).read_text()),
        inputs / 'bc2-selected', Path(params['target_pdb']))
    assert [component['sequence'] for component in compiled[0]['components']] == ['AG', 'Y']
    assert params['blind_pose_candidate_pdbs'][0].endswith('.cif')
    assert binding['target_source_sha256'] == hashlib.sha256(declared.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_bc2_route_resolves_only_declared_target(source, monkeypatch):
    job, designs, inputs, _ = source
    job.model_id = 'bindcraft2'
    target = inputs / 'declared.cif'
    target.write_text(cif('T', ['TYR']))
    candidate = inputs / 'native.cif'
    candidate.write_text(cif('B', ['ALA'])[:-2] + 'ATOM 2 C CA . TRP T 2 1 ? 999.0 0.0 0.0 1.0 20.0 1 TRP T CA 1\n#\n')
    designs[0].pdb_path = str(candidate)
    job.params = {'bindcraft2_settings': {'targets': [{'name': 'on', 'target_path': str(target)}]}}
    monkeypatch.setattr(route, 'get_inputs_dir', lambda: inputs)
    class SelectionSession:
        async def get(self, model, identifier):
            return job
        async def scalars(self, query):
            return Result(designs[:1])
    from routers import jobs
    async def enqueue(request, background_tasks, session):
        assert request.params['target_pdb'].endswith('target.cif')
        return {'job_id': 'child'}
    monkeypatch.setattr(jobs, 'create_job', enqueue)
    body = route.SelectedBlindPoseRequest(source_job_id='source', target_name='on',
        design_ids=[designs[0].id], binder_chains={designs[0].id: ['B']},
        target_chains=['T'], settings=route.BlindPoseSettings(model_variant='fast',
            model_id_or_path='', num_loops=1, num_sampling_steps=25, num_diffusion_samples=1))
    assert await route.launch_selected(body, BackgroundTasks(), SelectionSession()) == {'job_id': 'child'}


def test_compiler_selected_mode_preserves_manifest_and_all_sampling(source, tmp_path):
    job, designs, inputs, target = source
    destination = inputs / 'compiler-selected'
    binding = selected.prepare_selected(job, designs, target_pdb=str(target),
        binder_chains={d.id: ['B'] for d in designs}, target_chains=['T'], directory=destination)
    params = selected.launch_params(destination, variant='full', model_id_or_path='',
        num_loops=4, num_sampling_steps=83, num_diffusion_samples=2, seed=17)
    from services.nextflow import compile_nextflow_invocation
    params[selected.KEY] = binding
    from routers.jobs import normalize_job_request
    from schemas import JobCreate
    normalized = normalize_job_request(JobCreate(name='blind', model_id='esmfold2', mode='blind_pose', params=params))
    assert normalized.params['blind_pose_candidate_pdbs'] == params['blind_pose_candidate_pdbs']
    assert normalized.params['esmf_seed'] == 17
    invocation = compile_nextflow_invocation('esmfold2', 'blind_pose', normalized.params, str(tmp_path / 'out'), job_id='fixture')
    assert invocation.execution_plan is not None and invocation.execution_plan.complete, (
        None if invocation.execution_plan is None else invocation.execution_plan.blockers)
    assert {'image:esmfold2.sif', 'weights:esmfold2'} <= {
        item.logical_id for item in invocation.execution_plan.metadata.dependencies}
    assert invocation.entrypoint == 'workflows/binder_blind_pose.nf'
    assert 'esmfold2,workstation_ryzen7960x' in invocation.command
    native = invocation.native_parameters
    assert native['blind_pose_candidate_pdbs'] == params['blind_pose_candidate_pdbs']
    assert native['esmfold2_validation_num_sampling_steps'] == 83
    assert native['esmfold2_validation_num_diffusion_samples'] == 2
    assert native['esmfold2_validation_variant'] == 'full'
    assert native['esmf_seed'] == 17
    assert '--blind_pose_selection_manifest' in invocation.command
    assert '--esmf_pdb_sequence_path' not in invocation.command
    assert '--pred_method' not in invocation.command
    from services.remote_execution import bundle
    from paths import get_code_root
    from unittest.mock import patch
    with patch.object(bundle, 'get_inputs_dir', return_value=inputs):
        assets = bundle._input_assets(normalized.params, native_invocation=invocation,
            repo_root=get_code_root(), runtime_paths=set(), output_dir=tmp_path / 'out')
    assert (destination.resolve(), next(relative for path, relative in assets if path == destination.resolve())) in assets
    assert all(path != destination / 'selection.json' for path, _ in assets)
    remote = '/worker/inputs/selected'
    relocated = bundle._rewrite_maturation_pdb_paths(','.join(params['blind_pose_candidate_pdbs']),
        {str(destination.resolve()): remote})
    assert relocated == ','.join(remote + '/' + Path(p).name for p in params['blind_pose_candidate_pdbs'])
    assert bundle._rewrite(params['blind_pose_selection_manifest'], {str(destination.resolve()): remote}) == remote + '/selection.json'


def test_api_contract_rejects_unrouted_mode_before_queue(monkeypatch):
    monkeypatch.setattr(route, 'MODEL_MODE_WORKFLOW_ENTRYPOINTS', {})
    app = FastAPI()
    app.include_router(route.router, prefix='/api/blind-pose')
    response = TestClient(app).post('/api/blind-pose/selected', json={
        'source_job_id': 'source', 'design_ids': ['design-0'], 'binder_chains': {'design-0': ['B']},
        'target_chains': ['T'], 'settings': {'model_variant': 'fast', 'model_id_or_path': '',
                                           'num_loops': 1, 'num_sampling_steps': 25, 'num_diffusion_samples': 1}})
    assert response.status_code == 503
    assert 'not registered' in response.json()['detail']


@pytest.mark.asyncio
async def test_generic_job_submission_cannot_bypass_selected_design_owner():
    from routers.jobs import _create_job
    from schemas import JobCreate
    request = JobCreate(name='unowned-blind-pose', model_id='esmfold2',
                        mode='blind_pose', params={})
    with pytest.raises(HTTPException, match='selected Design route') as exc:
        await _create_job(request, BackgroundTasks(), None)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_selected_api_builds_exact_scheduler_job(source, monkeypatch):
    job, designs, inputs, _ = source
    monkeypatch.setattr(route, 'MODEL_MODE_WORKFLOW_ENTRYPOINTS', {
        ('esmfold2', 'blind_pose'): 'workflows/binder_blind_pose.nf'})
    monkeypatch.setattr(route, 'get_inputs_dir', lambda: inputs)
    class SelectionSession:
        async def get(self, model, identifier):
            return job if identifier == 'source' else None
        async def scalars(self, query):
            return Result(designs)
    from routers import jobs
    async def enqueue(request, background_tasks, session):
        assert request.model_id == 'esmfold2' and request.mode == 'blind_pose'
        assert request.params[selected.KEY]['source_job_id'] == job.id
        assert request.params['target_pdb'].endswith('/target.pdb')
        assert len(request.params['blind_pose_candidate_pdbs']) == len(designs)
        assert request.params['esmfold2_validation_num_sampling_steps'] == 25
        return {'job_id': 'child'}
    monkeypatch.setattr(jobs, 'create_job', enqueue)
    body = route.SelectedBlindPoseRequest(source_job_id='source',
        design_ids=[d.id for d in designs], binder_chains={d.id: ['B'] for d in designs},
        target_chains=['T'], settings=route.BlindPoseSettings(
            model_variant='fast', model_id_or_path='', num_loops=1,
            num_sampling_steps=25, num_diffusion_samples=1))
    assert await route.launch_selected(body, BackgroundTasks(), SelectionSession()) == {'job_id': 'child'}


@pytest.mark.asyncio
async def test_successful_job_finalizes_blind_evidence_without_a_new_design(source, tmp_path, monkeypatch):
    from datetime import datetime
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from database import Base
    from services.result_state_integrity import finalize_successful_job
    from run_binder_blind_pose import run

    parent, designs, inputs, target = source
    directory = inputs / 'finalizer-selected'
    binding = selected.prepare_selected(parent, designs[:1], target_pdb=str(target),
        binder_chains={designs[0].id: ['B']}, target_chains=['T'], directory=directory)
    params = selected.launch_params(directory, variant='fast', model_id_or_path='',
        num_loops=1, num_sampling_steps=25, num_diffusion_samples=1, seed=7)
    params[selected.KEY] = binding

    def native(command, check):
        assert check
        output = Path(command[command.index('--output-dir') + 1])
        key = command[command.index('--sequence-name') + 1]
        sample = key + '_000'
        (output / (sample + '.cif')).write_text('data_sample\n')
        (output / (sample + '.metrics.json')).write_text(json.dumps({
            'sample_id': sample, 'cif': sample + '.cif', 'iptm': .2}))
        (output / 'manifest.json').write_text(json.dumps({
            'workflow': 'esmfold2', 'sequence_name': key, 'sample_count': 1,
            'samples': [{'sample_id': sample, 'cif': sample + '.cif',
                         'metrics': sample + '.metrics.json'}]}))

    monkeypatch.setattr('run_binder_blind_pose.subprocess.run', native)
    output = tmp_path / 'finished'
    run(directory / 'selection.json', directory, directory / 'target.pdb',
        output / 'blind_pose_results', model_variant='fast', model_id_or_path='',
        num_loops=1, num_sampling_steps=25, num_diffusion_samples=1,
        seed=7, device='cpu', runner=tmp_path / 'native.py')
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'blind-pose.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            job = Job(id='blind-child', name='blind-child', model_id='esmfold2',
                mode='blind_pose', params=params, output_dir=str(output), status='running',
                queue_status='running', created_at=datetime.utcnow(), awaiting_input=False,
                awaiting_payload={}, retry_count=0, max_retries=2)
            session.add(job)
            await session.commit()
            completed = await finalize_successful_job(job, str(output), session)
            assert completed.completed, completed
        async with factory() as session:
            job = await session.get(Job, 'blind-child')
            assert job.status == 'completed'
            assert job.provenance['result_integrity']['result_kind'] == 'blind_pose_native_evidence'
            assert await session.scalar(select(func.count(Design.id)).where(Design.job_id == job.id)) == 0
            assert (await selected.read_selected(job, session))['records'][0]['classification'] == 'unclassified'
    finally:
        await engine.dispose()
