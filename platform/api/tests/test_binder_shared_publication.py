"""SQLite publication tests for shared binder document identity and finalization."""
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import Base, Design, Job, FrustraMPNNResult, ExecutionTarget
from services import result_ingester as ingester
from services import stage_review
from services.result_state_integrity import finalize_successful_job
from test_frustrampnn_result_ingestion import (
    _parent_bundle, _seed_parent_job, _publish_primary_pdb,
    _bind_primary_csv_to_bundles, MANIFEST_PATH,
)


PDB = 'ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 90.00           C\nEND\n'


@pytest_asyncio.fixture
async def store(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "shared.db"}')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def job(identity, **kw):
    return Job(id=identity, name=identity, model_id='rfantibody', mode='design',
               status='running', queue_status='running', awaiting_input=False,
               params={}, **kw)


@pytest.mark.asyncio
async def test_review_same_stems_states_formats_survive_sqlite_replay(tmp_path, store, monkeypatch):
    root = tmp_path / 'review'
    root.mkdir()
    paths = [root / name for name in ('state-a/same.pdb', 'state-a/same.CIF',
                                      'state-b/same.pdb', 'state-b/same.MmCiF')]
    for path in paths:
        path.parent.mkdir(exist_ok=True)
        if path.suffix.lower() == '.pdb':
            path.write_text(PDB)
        else:
            from Bio.PDB import PDBParser, MMCIFIO
            source = path.with_suffix('.pdb')
            writer = MMCIFIO()
            writer.set_structure(PDBParser(QUIET=True).get_structure('candidate', source))
            writer.save(str(path))
    # Paths are already resolved in this unit of publication; no deployment roots.
    monkeypatch.setattr(stage_review, 'refresh_gate_payload', lambda payload, _: payload)
    monkeypatch.setattr(stage_review, 'resolve_review_path', lambda value, _: Path(value) if value else None)
    async with store() as session:
        owner = job('review', output_dir=str(root), awaiting_stage='post_fampnn',
                    awaiting_payload={'candidate_dir': str(root)})
        session.add(owner)
        await session.commit()
        session.add(Design(id='historical-review', job_id=owner.id, name='same',
                           pdb_path=str(paths[0]), source_stage='post_fampnn', artifact_group='candidate'))
        await session.commit()
        assert await stage_review.ensure_stage_review_rows(session, owner) == len(paths)
        rows = list((await session.scalars(select(Design))).all())
        assert all(row.plddt_overall is None and row.residue_plddt is None for row in rows)
        before = {row.pdb_path: row.id for row in rows}
        assert set(before) == {str(path) for path in paths}
        assert before[str(paths[0])] == 'historical-review'
        assert len(set(before.values())) == len(paths)
        assert await stage_review.ensure_stage_review_rows(session, owner, force=True) == len(paths)
    async with store() as session:
        assert {row.pdb_path: row.id for row in (await session.scalars(select(Design))).all()} == before


@pytest.mark.asyncio
async def test_explicit_lineage_beats_ambiguous_alias_and_origin_round_drift(tmp_path, store):
    source_path = tmp_path / 'same.pdb'
    source_path.write_text(PDB)
    manifest = tmp_path / 'selection_manifest.json'
    items = [{'design_id': identity, 'design_job_id': 'round-1', 'design_name': 'same',
              'selection_pdb_path': str(tmp_path / identity / 'same.pdb')}
             for identity in ('parent-a', 'parent-b')]
    manifest.write_text(json.dumps({'designs': items}))
    async with store() as session:
        session.add_all([job('root'), job('round-1'), job('round-2')])
        session.add(Design(id='origin', job_id='root', name='same', pdb_path=str(source_path)))
        for identity in ('parent-a', 'parent-b'):
            session.add(Design(id=identity, job_id='round-1', name='same', pdb_path=str(source_path),
                               origin_design_id='origin', origin_job_id='round-1',
                               origin_backbone_design_id='origin'))
        await session.commit()
    async with store() as session:
        owner = await session.get(Job, 'round-2')
        owner.params = {'selected_input_manifest': str(manifest), 'source_stage_job_id': 'round-1'}
        context = ingester._job_stage_context(owner)
        assert context['selection_index']['same'] is None
        assert (await ingester._resolve_parent_design_lineage(session, context, 'same'))['parent_design_id'] is None
        explicit = await ingester._resolve_parent_design_lineage(
            session, context, 'same', source_identity={'source_design_id': 'parent-b', 'source_job_id': 'round-1'})
        exact_path = await ingester._resolve_parent_design_lineage(
            session, context, 'same', structure_path=Path(items[1]['selection_pdb_path']))
        for lineage in (explicit, exact_path):
            assert lineage['parent_design_id'] == 'parent-b'
            assert (lineage['origin_design_id'], lineage['origin_job_id']) == ('origin', 'root')
            assert lineage['source_stage_job_id'] == 'round-1'
        child = Design(id='descendant', job_id=owner.id, name='same', pdb_path=str(source_path),
                       **ingester._design_lineage_fields(context, explicit))
        session.add(child)
        await session.commit()
    async with store() as session:
        descendant = await session.get(Design, 'descendant')
        assert descendant.parent_design_id == 'parent-b'
        assert descendant.origin_job_id == 'root'


@pytest.mark.asyncio
async def test_collected_documents_replay_and_manifest_parent_identity(tmp_path, store):
    parents = ['parent-a', 'parent-b']
    paths = []
    for state, parent in zip(('a', 'b'), parents):
        path = tmp_path / 'collected' / 'maturation' / state / 'same_ppiflow_sample0.pdb'
        path.parent.mkdir(parents=True)
        path.write_text(PDB)
        path.with_name(path.stem + '_sample_identity.json').write_text(json.dumps({
            'pdb_name': path.name, 'sample_meta': {'sample_index': 0},
            'source': {'source_meta': {'design_id': parent, 'design_job_id': 'source'}}}))
        paths.append(path)
    async with store() as session:
        session.add_all([job('source'), job('child')])
        session.add_all([Design(id=identity, job_id='source', name='same', pdb_path=str(path))
                         for identity, path in zip(parents, paths)])
        await session.commit()
        owner = await session.get(Job, 'child')
        assert await ingester.ingest_collected_ppiflow_structures('child', tmp_path, session, owner) == 2
        assert await ingester.ingest_collected_ppiflow_structures('child', tmp_path, session, owner) == 0
    async with store() as session:
        rows = list((await session.scalars(select(Design).where(Design.job_id == 'child'))).all())
        assert {row.pdb_path: row.parent_design_id for row in rows} == dict(zip(map(str, paths), parents))
        assert all(row.plddt_overall is None and row.passed_screen is None for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('same_bytes', [True, False])
async def test_descendant_geometry_and_acceptance_are_not_ancestral_evidence(tmp_path, store, same_bytes):
    source = tmp_path / 'source.pdb'
    output = tmp_path / 'descendant.pdb'
    source.write_text(PDB)
    output.write_text(PDB if same_bytes else PDB.replace('0.000', '1.000', 1))
    async with store() as session:
        session.add_all([job('source'), job('child')])
        parent = Design(id='parent', job_id='source', name='parent', pdb_path=str(source),
                        passed_screen=True, target_contact_count=8, plddt_overall=95, iptm=.9,
                        artifact_class='validated_complex')
        child = Design(id='child', job_id='child', name='child', pdb_path=str(output), parent_design_id='parent')
        ingester._inherit_source_design_metrics(child, parent, structure_path=output)
        session.add_all([parent, child])
        await session.commit()
    async with store() as session:
        parent = await session.get(Design, 'parent')
        child = await session.get(Design, 'child')
        assert parent.passed_screen is True and parent.plddt_overall == 95
        assert child.passed_screen is (True if same_bytes else None)
        assert child.target_contact_count == (8 if same_bytes else None)
        assert child.plddt_overall is None and child.iptm is None
        assert child.artifact_class is None


@pytest.mark.asyncio
async def test_raw_same_basename_documents_survive_publication_replay(tmp_path, store):
    paths = [tmp_path / state / 'same.pdb' for state in ('a', 'b')]
    for path in paths:
        path.parent.mkdir()
        path.write_text(PDB)
    async with store() as session:
        owner = job('raw', output_dir=str(tmp_path))
        session.add(owner)
        await session.commit()
        assert await ingester.ingest_loose_files(owner.id, tmp_path, session, owner) == 2
        before = {row.pdb_path: row.id for row in (await session.scalars(select(Design))).all()}
        assert set(before) == set(map(str, paths))
        assert await ingester.ingest_loose_files(owner.id, tmp_path, session, owner) == 0
    async with store() as session:
        assert {row.pdb_path: row.id for row in (await session.scalars(select(Design))).all()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize('manual', [False, True])
@pytest.mark.parametrize('preexisting', [False, True])
async def test_actual_finalizer_optional_failure_retry_and_component_ingestion(tmp_path, store, manual, preexisting):
    root = tmp_path / 'publication'
    bundle = root / 'frustrampnn' / 'results' / 'candidate'
    candidate_id, invocation_id, _ = _parent_bundle(
        bundle, job_root=root, job_id='publication', parent_workflow_id='protein_design',
        producer_stage='protein_design:af2_terminal',
        producer_candidate_key='results/best_designs/canonical.pdb')
    _publish_primary_pdb(root, 'canonical')
    csv_path = root / 'results' / 'all_designs.csv'
    csv_path.write_text(f'candidate_id,description,pr_plddt\n{candidate_id},candidate,87\n')
    _bind_primary_csv_to_bundles(csv_path, [bundle])
    await _seed_parent_job(store, job_id='publication', job_root=root, manifests=[bundle / MANIFEST_PATH])
    async with store() as session:
        owner = await session.get(Job, 'publication')
        if manual:
            session.add(ExecutionTarget(id='target', name='target', provider='vast',
                                        provider_instance_id='fixture', leased_job_id='other-job'))
            owner.execution_target_id = 'target'
            owner.remote_state = 'returning'
            owner.remote_attempt_id = 'attempt'
            owner.nextflow_run_id = 'remote:attempt'
            owner.provenance = {'remote_result_generation': {'retained': 'generation'}}
        if preexisting:
            await ingester._ingest_job_results(owner.id, str(root), session)
        owner.stage_outputs = {'frustrampnn': [str(root / 'missing-manifest.json')]}
        await session.commit()
        first = await finalize_successful_job(owner, str(root), session)
        assert not first.completed, owner.error_message
        assert first.integrity_state == 'ingestion_failed'
    async with store() as session:
        owner = await session.get(Job, 'publication')
        assert owner.provenance['result_integrity']['primary_validated'] is True
        assert [row.id for row in (await session.scalars(select(Design))).all()] == [candidate_id]
        assert not (await session.scalars(select(FrustraMPNNResult))).all()
        if manual:
            assert owner.remote_state == 'returned_ingestion_failed'
            assert owner.provenance['remote_result_generation'] == {'retained': 'generation'}
            assert (await session.get(ExecutionTarget, 'target')).leased_job_id == 'other-job'
            owner.remote_state = 'returning'
        owner.status = owner.queue_status = 'running'
        owner.stage_outputs = {'frustrampnn': [str(bundle / MANIFEST_PATH),
                                                 str(bundle / 'workflow_component_result_v1.json')]}
        await session.commit()
        retry = await finalize_successful_job(owner, str(root), session)
        assert retry.completed, owner.error_message
    async with store() as session:
        assert [row.id for row in (await session.scalars(select(Design))).all()] == [candidate_id]
        result = await session.get(FrustraMPNNResult, ('publication', invocation_id))
        assert result is not None and result.design_id == candidate_id
        owner = await session.get(Job, 'publication')
        assert owner.provenance['result_integrity']['idempotent_prior_results'] is True
        owner.status = owner.queue_status = 'running'
        if manual:
            owner.remote_state = 'returning'
        await session.commit()
        assert (await finalize_successful_job(owner, str(root), session)).completed
    async with store() as session:
        assert len((await session.scalars(select(FrustraMPNNResult))).all()) == 1
        assert [row.id for row in (await session.scalars(select(Design))).all()] == [candidate_id]
