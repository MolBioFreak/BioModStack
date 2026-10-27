import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experiment_models import ExperimentBase
from experiment_services import (NotFound, create_project, create_global_experiment,
    create_domain_experiment, create_dataset, save_dataset_revision, derive_dataset_revision)
from services.family_msa.sources import freeze_dataset_resources
from test_project_manager_adapters import _project_payload, _global_payload, _domain_payload


@pytest.mark.asyncio
async def test_derivation_retains_exact_occurrences(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'project.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ExperimentBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        project = await create_project(session, _project_payload())
        experiment = await create_global_experiment(session, project.id, _global_payload())
        domain = await create_domain_experiment(session, project.id, experiment.id, _domain_payload('ngs_molbio'))
        dataset = await create_dataset(session, project.id, 'Source', 'ngs_molbio.sequence_collection.v1', experiment_id=domain.id)
        parent = await save_dataset_revision(session, dataset.aggregate_id,
            {'members': [{'identity': 'same', 'value': {'sequence': 'AC'}} for _ in range(2)]},
            expected_head_generation=dataset.head_generation)
        child = await derive_dataset_revision(session, dataset.aggregate_id, parent.resource_id, [1, 0, 1],
            expected_head_generation=dataset.head_generation)
        await session.commit()
        rows = (await freeze_dataset_resources(session, project_id=project.id, dataset_id=dataset.aggregate_id,
            revision_id=child.resource_id))['members']
        assert [r['value']['origin']['ordinal'] for r in rows] == [1, 0, 1]
        assert all(r['value']['origin']['revision_id'] == parent.resource_id for r in rows)
        assert all(r['value']['source_value'] == {'sequence': 'AC'} for r in rows)
        with pytest.raises(NotFound):
            await derive_dataset_revision(session, dataset.aggregate_id, parent.resource_id, [9],
                expected_head_generation=dataset.head_generation)
    await engine.dispose()


@pytest.mark.asyncio
async def test_ancestry_reorder_duplicates_tampering_and_comparison(tmp_path):
    from services.family_msa.sources import browse_dataset_resources
    from bms_alignment import parse, compare
    import json
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ancestry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(ExperimentBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as s:
        project = await create_project(s, _project_payload())
        exp = await create_global_experiment(s, project.id, _global_payload())
        domain = await create_domain_experiment(s, project.id, exp.id, _domain_payload('ngs_molbio'))
        ds = await create_dataset(s, project.id, 'Source', 'ngs_molbio.sequence_collection.v1', experiment_id=domain.id)
        root = await save_dataset_revision(s, ds.id, {'members': [
            {'identity':'same', 'role':'member', 'value':{'name':'same'}} for _ in range(3)]}, expected_head_generation=ds.head_generation)
        child = await derive_dataset_revision(s, ds.id, root.id, [2,0,1], expected_head_generation=ds.head_generation)
        grandchild = await derive_dataset_revision(s, ds.id, child.id, [1,0], expected_head_generation=ds.head_generation)
        duplicate = await derive_dataset_revision(s, ds.id, grandchild.id, [0,0,1], expected_head_generation=ds.head_generation)
        after_duplicate = await derive_dataset_revision(s, ds.id, duplicate.id, [0,2], expected_head_generation=ds.head_generation)
        good = (await freeze_dataset_resources(s, project_id=project.id, dataset_id=ds.id, revision_id=grandchild.id))['members']
        assert [x['ancestry']['occurrence']['ordinal'] for x in good] == [0,2]
        assert all(x['ancestry']['depth'] == 2 for x in good)
        page = await browse_dataset_resources(s, project_id=project.id, dataset_id=ds.id, revision_id=duplicate.id, offset=0, limit=1)
        assert page['items'][0]['ancestry']['reason'] == 'repeated_origin_ambiguous'
        later = (await freeze_dataset_resources(s, project_id=project.id, dataset_id=ds.id, revision_id=after_duplicate.id))['members']
        assert later[0]['ancestry']['status'] == 'unknown'
        assert later[1]['ancestry']['occurrence']['ordinal'] == 2
        bad_value = dict(good[0]['value'])
        bad_value['source_value'] = {'name':'tampered'}
        bad = await save_dataset_revision(s, ds.id, {'members':[{'identity':'same','value':bad_value}]}, expected_head_generation=ds.head_generation)
        assert (await freeze_dataset_resources(s, project_id=project.id, dataset_id=ds.id, revision_id=bad.id))['members'][0]['ancestry']['status'] == 'unknown'
        a = parse('>x\nA-C\n>y\nA-C\n', 'fasta', 'dna')
        b = parse('>renamed\nAC-\n', 'fasta', 'dna')
        for row, item in zip(a.rows, good):
            row.source_member_id = item['member_identity']
            row.occurrence_id = json.dumps(item['ancestry']['occurrence'], sort_keys=True)
            row.source_positions = [4,None,5]; row.transformation_provenance = 'slice'
        b.rows[0].source_member_id = good[0]['member_identity']
        b.rows[0].occurrence_id = a.rows[0].occurrence_id
        b.rows[0].source_positions = [4,5,None]; b.rows[0].transformation_provenance = 'slice'
        result = compare(a,b)
        assert len(result['rows']) == 1 and result['rows'][0]['positions'][1]['source'] == 5
        a.rows[1].occurrence_id = a.rows[0].occurrence_id
        assert not compare(a,b)['rows'] and compare(a,b)['left_ambiguous_identities']
    await engine.dispose()
