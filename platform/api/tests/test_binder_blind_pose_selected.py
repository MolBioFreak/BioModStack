"""Selected API materialization -> leaf -> verified attachment on CPU fixtures."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, FastAPI
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
    assert all(row['classification'] == 'unclassified' for row in result['records'])
    assert len(session.artifacts) == len(designs) * 4 + 1
    await selected.publish_selected(child, output, session)
    assert len(session.artifacts) == len(designs) * 4 + 1
    (output / 'blind_pose_results/candidate-0000/candidate-0000_000.cif').write_text('tampered')
    with pytest.raises(selected.BlindPoseError, match='bytes changed'):
        await selected.read_selected(child, session)


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
