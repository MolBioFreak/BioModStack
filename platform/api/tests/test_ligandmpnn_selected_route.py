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
from test_ligandmpnn_interface_leaf import fixture, runner


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


def test_selected_route_is_not_publicly_advertised_without_shared_compiler_hooks():
    from main import app
    from model_registry import get_registry
    from services.nextflow import resolve_nextflow_entrypoint
    assert resolve_nextflow_entrypoint(effective_profile='ligandmpnn', model_id='ligandmpnn',
                                       mode='interface_context') == 'workflows/ligandmpnn_interface_context.nf'
    registry = get_registry()
    assert registry.validate_job_params('ligandmpnn', 'interface_context',
                                        {'interface_context_manifest': '/managed/selection.json'})
    assert not any(getattr(inner, 'path', None) == '/api/ligandmpnn/interface-context/selected'
                   for included in app.routes for inner in getattr(getattr(included, 'original_router', None), 'routes', ()))


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
    async def root_resolver(session, identity): return source, source
    async def owners(session, src, root, designs):
        if any(d.job_id != src.id for d in designs):
            raise HTTPException(422, 'foreign Design owner')
    async def create_job(request, tasks, session):
        assert request.model_id == 'ligandmpnn' and request.mode == 'interface_context'
        binding = request.params[publication.KEY]
        publication.verify_binding(binding)
        assert request.params['interface_context_manifest'] == binding['manifest']
        assert binding['settings'] == selection(native['candidate_id']).settings.model_dump()
        return SimpleNamespace(id='child')
    monkeypatch.setattr(jobs, '_resolve_antibody_root_job', root_resolver)
    monkeypatch.setattr(jobs, '_validate_selected_design_owners', owners)
    monkeypatch.setattr(jobs, 'create_job', create_job)
    response = await route.submit_selected(selection(native['candidate_id']), BackgroundTasks(), session)
    assert response['job'].id == 'child'
    assert pdb.read_bytes() == source_file.read_bytes()
    design.job_id = 'foreign'
    with pytest.raises(HTTPException, match='foreign'):
        await route.submit_selected(selection(native['candidate_id']), BackgroundTasks(), session)


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
