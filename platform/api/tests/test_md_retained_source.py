"""Real immutable source collection/reopening; no simulation or engine stubs."""
import hashlib
import json
import shutil

import pytest

from database import Job
from services.md import starting_structures as sources
from scripts.bms_md.aggregate_children import retain_source_snapshots, ImmutableCollectionConflict
from test_md_selected_model import ensemble


@pytest.mark.asyncio
@pytest.mark.parametrize('returned', [False, True])
async def test_selected_retained_source_survives_producer_cleanup(tmp_path, returned):
    job_id = '11111111-1111-4111-8111-111111111111'
    source_ref = {'kind': 'candidate', 'id': '22222222-2222-4222-8222-222222222222',
                  'candidate_ref': {'kind': 'native_document', 'job_id': '22222222-2222-4222-8222-222222222222',
                                    'artifact_id': 'nonprimary', 'target_state': 'bound', 'model': 2}}
    root = tmp_path / 'worker'
    inputs = root / 'inputs'; inputs.mkdir(parents=True)
    original = inputs / 'original_structure.pdb'; original.write_bytes(ensemble())
    selected = inputs / 'structure-model-2.pdb'
    selected.write_bytes(sources.select_structure_model(ensemble(), 'pdb', 2))
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    provenance = {'source_ref': source_ref, 'model_number': 2, 'original_format': 'pdb',
                  'original_sha256': digest(original), 'source_sha256': digest(selected),
                  'snapshot': {'structure': str(selected), 'structure_sha256': digest(selected), 'structure_bytes': selected.stat().st_size},
                  'original_snapshot': {'path': str(original), 'sha256': digest(original), 'bytes': original.stat().st_size}}
    expected = selected.read_bytes()
    if returned:
        local = tmp_path / 'local'
        # Transport-only terminal manifest fixture: no mdrun is claimed.
        from scripts.bms_md.aggregate_children import collect_children
        child = tmp_path / 'child'; child.mkdir()
        (child / 'selected.pdb').write_bytes(expected)
        (child / 'manifest.json').write_text(json.dumps({'schema': 'bms.md.run.v1', 'status': 'completed',
            'job_id': job_id, 'replica_index': 0, 'config': {},
            'artifacts': {'source_fixture': {'path': 'selected.pdb'}}}))
        status = tmp_path / 'status.json'
        status.write_text(json.dumps({'child_output_dirs': [str(child)], 'completed': 1, 'child_ids': ['fixture']}))
        aggregate = collect_children(status, local, source_provenance=provenance)
        assert collect_children(status, local, source_provenance=provenance) == aggregate
        retained = retain_source_snapshots(provenance, local)
        assert json.loads((local / 'retained_source.json').read_text()) == retained
        shutil.rmtree(root)  # both worker and original producer are now absent
        root = local
    job = Job(id=job_id, model_id='molecular_dynamics', output_dir=str(root), params={'md_source_provenance': provenance})
    class Session:
        async def get(self, cls, identity):
            assert cls is Job and identity == job_id  # never look up deleted producer
            return job
    ref = sources.StartingStructureSourceRef.model_validate({**source_ref, 'snapshot_job_id': job_id})
    resolved = await sources.resolve_source(ref, Session())
    try:
        assert sources.read_resolved_structure(resolved).data == expected
        assert resolved.source_ref.model_dump(mode='json') == ref.model_dump(mode='json')
        from pathlib import Path
        assert Path(resolved.original_runtime_path).read_bytes() == ensemble()
        assert resolved.source_provenance['consumed_sha256'] == hashlib.sha256(expected).hexdigest()
        from urllib.parse import parse_qs, urlsplit
        inspection = sources.inspect_resolved_structure(resolved)
        selection = json.loads(parse_qs(urlsplit(inspection.viewer.url).query)['selection'][0])
        assert selection['snapshot_job_id'] == job_id
        assert selection['candidate_ref']['model'] == 2
    finally:
        resolved.close()
    wrong = ref.model_copy(deep=True)
    wrong.candidate_ref.model = 1
    with pytest.raises(sources.StartingStructureError, match='different selection'):
        await sources.resolve_source(wrong, Session())
    job.output_dir = str(tmp_path / 'not-returned')
    with pytest.raises(sources.StartingStructureError, match='unavailable'):
        await sources.resolve_source(ref, Session())


def test_optional_original_collection_and_tamper(tmp_path):
    assert retain_source_snapshots({}, tmp_path / 'old') == {}
    assert not (tmp_path / 'old').exists()
    path = tmp_path / 'source.pdb'; path.write_bytes(ensemble())
    provenance = {'original_snapshot': {'path': str(path), 'sha256': '0' * 64, 'bytes': path.stat().st_size}}
    with pytest.raises(ImmutableCollectionConflict, match='digest changed'):
        retain_source_snapshots(provenance, tmp_path / 'bad')
    linked = tmp_path / 'link.pdb'; linked.symlink_to(path)
    provenance['original_snapshot'].update(path=str(linked), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ImmutableCollectionConflict, match='safely'):
        retain_source_snapshots(provenance, tmp_path / 'bad-link')


@pytest.mark.asyncio
async def test_single_model_retained_viewer_keeps_snapshot_owner(tmp_path):
    from urllib.parse import parse_qs, urlsplit
    job_id = '11111111-1111-4111-8111-111111111111'
    source_ref = {'kind': 'upload', 'id': '22222222-2222-4222-8222-222222222222'}
    inputs = tmp_path / 'inputs'; inputs.mkdir()
    path = inputs / 'structure.pdb'
    path.write_bytes(sources.select_structure_model(ensemble(), 'pdb', 1))
    provenance = {'source_ref': source_ref, 'snapshot': {'structure': str(path),
                  'structure_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "core.db"}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Job.__table__.create)
        async with sessions() as session:
            session.add(Job(id=job_id, name='retained source', model_id='molecular_dynamics', mode='simulate',
                            status='completed', output_dir=str(tmp_path), params={'md_source_provenance': provenance}))
            await session.commit()
        ref = sources.StartingStructureSourceRef.model_validate({**source_ref, 'snapshot_job_id': job_id})
        # A fresh SQLAlchemy session reads durable metadata, not browser state.
        async with sessions() as session:
            resolved = await sources.resolve_source(ref, session)
            try:
                selection = json.loads(parse_qs(urlsplit(sources.inspect_resolved_structure(resolved).viewer.url).query)['selection'][0])
                assert selection == ref.model_dump(mode='json', exclude_none=True)
                assert sources.read_resolved_structure(resolved).data == path.read_bytes()
            finally:
                resolved.close()
    finally:
        await engine.dispose()
