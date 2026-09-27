"""Real SQLite/ASGI diagnostic document and descendant-context contracts."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from database import Base, Design, Job, JobArtifact, get_session
from routers import binder_blind_pose as route
from routers import ligandmpnn_interface_context as ligand_route
from services import ligandmpnn_interface_publication as ligand_publication
from services import binder_blind_pose_selected as selected
from services.binder_diagnostic_selection import CandidateDocument, selected_document
from test_binder_blind_pose_selected import pdb


@pytest.mark.asyncio
async def test_sqlite_asgi_exact_document_and_descendant_declared_targets(tmp_path, monkeypatch):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    for module in (route, selected):
        monkeypatch.setattr(module, 'get_inputs_dir', lambda: inputs)
    monkeypatch.setattr(selected, 'get_allowed_roots', lambda: {'inputs': inputs})
    monkeypatch.setattr(selected, 'resolve_runtime_data_path', lambda path: Path(path).resolve())
    monkeypatch.setattr(ligand_route, 'get_allowed_roots', lambda: {'inputs': inputs})
    monkeypatch.setattr(ligand_route, 'resolve_runtime_data_path', lambda path: Path(path).resolve())
    monkeypatch.setattr(ligand_publication, 'get_inputs_dir', lambda: inputs)
    from services import bindcraft2_publication
    async def published(job, session): pass
    monkeypatch.setattr(bindcraft2_publication, 'read_published_native_results', published)
    target = inputs / 'independent.pdb'
    target.write_text(pdb('T', ['TYR']))
    primary = inputs / 'primary.pdb'
    primary.write_text(pdb('B', ['ALA']))
    alternate = inputs / 'alternate.pdb'
    alternate.write_text(pdb('B', ['GLY']) + pdb('T', ['TRP']))
    digest = hashlib.sha256(alternate.read_bytes()).hexdigest()
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "contexts.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = FastAPI()
    app.include_router(route.router, prefix='/api/blind-pose')
    app.include_router(ligand_route.router)
    async def session_dep():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_session] = session_dep
    from routers import jobs
    # Preserve the actual route and DB; do not schedule a native/GPU job.
    async def enqueue(request, tasks, session):
        child = Job(id=request.model_id + '-' + request.params['selection_source_job_id'], name=request.name, model_id=request.model_id,
                    mode=request.mode, params=request.params, status='queued')
        session.add(child)
        await session.commit()
        return {'id': child.id}
    monkeypatch.setattr(jobs, 'create_job', enqueue)
    try:
        async with factory() as session:
            root = Job(id='root', name='root', model_id='bindcraft2', mode='campaign', params={
                'bindcraft2_settings': {'targets': [{'name': 'declared', 'target_path': str(target)}]}},
                provenance={'bindcraft2_native_publication': {'candidates': [{'design_id': 'candidate', 'structures': [
                    {'artifact_id': 'alternate-artifact', 'target_state': 'other', 'sha256': digest}]}]}})
            session.add_all([root, Job(id='round1', name='round1', model_id='proteinmpnn', mode='design',
                params={'selection_source_job_id': 'root', 'lineage_root_job_id': 'root'}),
                Job(id='round2', name='round2', model_id='proteinmpnn', mode='design',
                    parent_job_id='round1', params={'lineage_root_job_id': 'root'}),
                Design(id='candidate', job_id='root', name='candidate', pdb_path=str(primary)),
                Design(id='descendant', job_id='round2', name='descendant', pdb_path=str(alternate),
                       origin_job_id='root', parent_design_id='candidate', provenance={'selected_state': 'other'}),
                JobArtifact(id='alternate-artifact', owner_job_id='root', attempt=0,
                    logical_path='native/alternate.pdb', storage_path=str(alternate), sha256=digest,
                    bytes=len(alternate.read_bytes()), media_type='chemical/x-pdb')])
            await session.commit()
            design = await session.get(Design, 'candidate')
            path, identity = await selected_document(root, design, CandidateDocument(target_state='other'), session)
            assert path == alternate and identity['artifact_id'] == 'alternate-artifact'
            with pytest.raises(ValueError, match='producer-bound'):
                await selected_document(root, design, CandidateDocument(artifact_id='unrelated'), session)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            context = await client.get('/api/blind-pose/round2/selection-context')
            assert context.status_code == 200
            assert context.json()['targets'] == [{'name': 'declared', 'owner_job_id': 'root'}]
            exact = await client.post('/api/blind-pose/selected', json={
                'source_job_id': 'root', 'target_name': 'declared', 'design_ids': ['candidate'],
                'candidate_documents': {'candidate': {'artifact_id': 'alternate-artifact', 'target_state': 'other'}},
                'binder_chains': {'candidate': ['B']}, 'target_chains': ['T'],
                'settings': {'model_variant': 'fast', 'model_id_or_path': '', 'num_loops': 1,
                             'num_sampling_steps': 25, 'num_diffusion_samples': 1}})
            assert exact.status_code == 201, exact.text
            ligand = await client.post('/api/ligandmpnn/interface-context/selected', json={
                'action': 'ligandmpnn_interface_context', 'source_job_id': 'root', 'round_id': 'root',
                'candidate_ids': ['candidate'],
                'candidate_documents': {'candidate': {'artifact_id': 'alternate-artifact', 'target_state': 'other'}},
                'settings': {'binder_chain': 'B', 'target_chain': 'T', 'target_patch': ['T1'],
                             'seed': 7, 'samples': 1, 'temperature': 0.1}})
            assert ligand.status_code == 201, ligand.text
            response = await client.post('/api/blind-pose/selected', json={
                'source_job_id': 'round2', 'target_name': 'declared', 'design_ids': ['descendant'],
                'binder_chains': {'descendant': ['B']}, 'target_chains': ['T'],
                'settings': {'model_variant': 'fast', 'model_id_or_path': '', 'num_loops': 1,
                             'num_sampling_steps': 25, 'num_diffusion_samples': 1}})
            assert response.status_code == 201, response.text
        async with factory() as session:
            ligand = await session.get(Job, 'ligandmpnn-root')
            original = ligand.params[ligand_publication.KEY]['sources']['candidate']['original']
            assert original['artifact_id'] == 'alternate-artifact' and original['target_state'] == 'other'
            assert Path(original['snapshot_path']).read_bytes() == alternate.read_bytes()
            exact = await session.get(Job, 'esmfold2-root')
            candidate = exact.params[selected.KEY]['candidates'][0]
            assert candidate['source_sha256'] == digest
            assert candidate['source_identity']['artifact_id'] == 'alternate-artifact'
            assert candidate['source_identity']['target_state'] == 'other'
            child = await session.get(Job, 'esmfold2-round2')
            binding = child.params[selected.KEY]
            assert child.params['lineage_root_job_id'] == 'root'
            assert binding['target_context']['owner_job_id'] == 'root'
            assert binding['target_context']['name'] == 'declared'
            identity = binding['candidates'][0]['source_identity']
            assert identity['owner_job_id'] == 'round2'
            assert identity['origin_job_id'] == 'root'
            assert identity['parent_design_id'] == 'candidate'
            assert identity['design_provenance'] == {'selected_state': 'other'}
            from run_binder_blind_pose import compile_selected_inputs
            manifest = Path(child.params['blind_pose_selection_manifest'])
            compiled = compile_selected_inputs(json.loads(manifest.read_text()), manifest.parent, Path(child.params['target_pdb']))
            assert [row['sequence'] for row in compiled[0]['components']] == ['G', 'Y']
            # Source disappears after snapshot: selection still has its exact bytes.
            alternate.unlink()
            selected._verify_request_snapshots(child, binding)
            ligand_publication.verify_binding(ligand.params[ligand_publication.KEY])
            # Fixture-native output exercises real publication + a fresh ASGI GET.
            from run_binder_blind_pose import run
            def native(command, check):
                output = Path(command[command.index('--output-dir') + 1])
                key = command[command.index('--sequence-name') + 1]
                sample = key + '_000'
                (output / (sample + '.cif')).write_text('data_fixture\n')
                (output / (sample + '.metrics.json')).write_text(json.dumps({
                    'sample_id': sample, 'cif': sample + '.cif', 'iptm': 0.2}))
                (output / 'manifest.json').write_text(json.dumps({'workflow': 'esmfold2',
                    'sequence_name': key, 'sample_count': 1, 'samples': [{'sample_id': sample,
                        'cif': sample + '.cif', 'metrics': sample + '.metrics.json'}]}))
            monkeypatch.setattr('run_binder_blind_pose.subprocess.run', native)
            output = tmp_path / 'result'
            run(manifest, manifest.parent, Path(child.params['target_pdb']), output / 'blind_pose_results',
                model_variant='fast', model_id_or_path='', num_loops=1, num_sampling_steps=25,
                num_diffusion_samples=1, seed=None, device='cpu', runner=tmp_path / 'fixture.py')
            child.output_dir = str(output)
            await selected.publish_selected(child, output, session)
            await session.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            reopened = await client.get('/api/blind-pose/esmfold2-round2/result')
            assert reopened.status_code == 200, reopened.text
            assert reopened.json()['target_context']['owner_job_id'] == 'root'
            assert reopened.json()['records'][0]['source_identity']['parent_design_id'] == 'candidate'
            assert reopened.json()['records'][0]['raw_metrics']['iptm'] == 0.2
    finally:
        await engine.dispose()
