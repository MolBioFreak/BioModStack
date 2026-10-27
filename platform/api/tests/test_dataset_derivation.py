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
