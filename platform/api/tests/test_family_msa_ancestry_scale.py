from time import perf_counter

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experiment_models import ExperimentBase, ExperimentDatasetRevisionMember, ExperimentRevision
from experiment_services import (create_project, create_global_experiment, create_domain_experiment,
    create_dataset, save_dataset_revision, derive_dataset_revision)
from services.family_msa.sources import _ancestry, browse_dataset_resources, freeze_dataset_resources
from test_project_manager_adapters import _project_payload, _global_payload, _domain_payload


@pytest.mark.asyncio
async def test_ancestry_batched_large_rosters_and_pages(tmp_path, record_property):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'large.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ExperimentBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as s:
        project = await create_project(s, _project_payload())
        exp = await create_global_experiment(s, project.id, _global_payload())
        domain = await create_domain_experiment(s, project.id, exp.id, _domain_payload('ngs_molbio'))
        ds = await create_dataset(s, project.id, 'Source', 'ngs_molbio.sequence_collection.v1', experiment_id=domain.id)
        root = await save_dataset_revision(s, ds.id, {'members': [
            {'identity': f'protein:{i}', 'value': {'sequence': 'ACDE', 'index': i}}
            for i in range(920)]}, expected_head_generation=ds.head_generation)
        child = await derive_dataset_revision(s, ds.id, root.id, list(reversed(range(920))),
            expected_head_generation=ds.head_generation)
        grandchild = await derive_dataset_revision(s, ds.id, child.id, list(range(920)) + [0],
            expected_head_generation=ds.head_generation)
        await s.commit()
        project_id, dataset_id, revision_id = project.id, ds.id, grandchild.id
    statements = []
    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine.sync_engine, 'before_cursor_execute', count)
    try:
        async with sessions() as s:
            started = perf_counter()
            result = await freeze_dataset_resources(s, project_id=project_id, dataset_id=dataset_id, revision_id=revision_id)
            elapsed = perf_counter() - started
            assert len(statements) == 8
            record_property('large_freeze_seconds', elapsed)
            record_property('large_freeze_sql_queries', len(statements))
            print(f'921 rows, depth 2: {elapsed:.3f}s, {len(statements)} SQL queries')
            items = result['members']
            assert len(items) == 921
            assert items[0]['ancestry'] == items[-1]['ancestry'] == {'status': 'unknown', 'reason': 'repeated_origin_ambiguous'}
            assert [item['ancestry']['occurrence']['ordinal'] for item in items[1:-1]] == list(range(918, -1, -1))
            assert all(item['ancestry']['depth'] == 2 for item in items[1:-1])
        statements.clear()
        async with sessions() as s:
            page = await browse_dataset_resources(s, project_id=project_id, dataset_id=dataset_id,
                revision_id=revision_id, offset=919, limit=2)
            assert len(statements) == 9
            assert page['items'] == items[919:921]
            assert page['total'] == 921 and page['next_offset'] is None
        statements.clear()
        async with sessions() as s:
            selected = await freeze_dataset_resources(s, project_id=project_id, dataset_id=dataset_id,
                revision_id=revision_id, ordinals=[0, 1])
            assert len(statements) == 8
            assert selected['members'] == items[:2]
        async with sessions() as s:
            rev = await s.get(ExperimentRevision, revision_id)
            rows = (await s.execute(select(ExperimentDatasetRevisionMember).where(
                ExperimentDatasetRevisionMember.revision_id == revision_id))).scalars().all()
            # Keep revision objects alive to make the baseline identity-map behavior
            # deterministic and give the old per-row lookup its best case.
            parents = (await s.execute(select(ExperimentRevision).where(
                ExperimentRevision.subject_id == dataset_id))).scalars().all()
            assert len(parents) == 3
            statements.clear()
            started = perf_counter()
            for row in rows:
                original = await _ancestry(s, dataset_id, rev, row)
                if row.ordinal in (0, 920):
                    assert original['status'] == 'resolved'
                else:
                    original.pop('_path')
                    assert original == items[row.ordinal]['ancestry']
            old_elapsed = perf_counter() - started
            assert len(statements) == 1842
            print(f'baseline current roster only: {old_elapsed:.3f}s, {len(statements)} SQL queries')
            record_property('baseline_current_roster_seconds', old_elapsed)
            record_property('baseline_current_roster_queries', len(statements))
        # A missing ancestor must remain a reported mismatch, not a new gate.
        async with sessions() as s:
            source = (await s.execute(select(ExperimentDatasetRevisionMember).where(
                ExperimentDatasetRevisionMember.revision_id == child.id,
                ExperimentDatasetRevisionMember.ordinal == 0))).scalar_one()
            await s.delete(source)
            await s.commit()
        async with sessions() as s:
            missing = await freeze_dataset_resources(s, project_id=project_id, dataset_id=dataset_id,
                revision_id=revision_id, ordinals=[0, 1, 920])
            rev = await s.get(ExperimentRevision, revision_id)
            for item in missing['members']:
                row = (await s.execute(select(ExperimentDatasetRevisionMember).where(
                    ExperimentDatasetRevisionMember.revision_id == revision_id,
                    ExperimentDatasetRevisionMember.ordinal == item['ordinal']))).scalar_one()
                original = await _ancestry(s, dataset_id, rev, row)
                original.pop('_path', None)
                assert item['ancestry'] == original
            assert missing['members'][0]['ancestry'] == {'status': 'unknown', 'reason': 'origin_member_mismatch'}
            assert missing['members'][2]['ancestry'] == missing['members'][0]['ancestry']
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', count)
        await engine.dispose()
