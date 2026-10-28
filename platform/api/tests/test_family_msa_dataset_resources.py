import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from experiment_models import (ExperimentBase, ExperimentResource, ExperimentAggregateHead,
                               ExperimentRevision, ExperimentDatasetRevisionMember,
                               ExperimentExternalEntityReceipt)
from experiment_services import NotFound
from services.family_msa.sources import browse_dataset_resources, freeze_dataset_resources


@pytest.mark.asyncio
async def test_pinned_inventory_and_freeze_without_designs(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'project.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(ExperimentBase.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([ExperimentResource(id=x, kind="test") for x in ("p", "d", "r", "q")])
        session.add(ExperimentAggregateHead(aggregate_id="d", aggregate_kind="dataset", workspace_id="p", display_name="D"))
        session.add(ExperimentRevision(resource_id="r", subject_id="d", revision_number=1, schema_name="test", schema_version="1", canonical_payload="{}", payload_sha256="a" * 64, dependency_graph_sha256="b" * 64))
        session.add(ExperimentExternalEntityReceipt(id="q", workspace_id="p", resource_id="q", store_id="core", entity_kind="alignment_document", entity_id="native", generation_or_revision="1", content_digest="c" * 64, availability="available", verification_authority="producer"))
        for i in range(205):
            session.add(ExperimentDatasetRevisionMember(revision_id="r", ordinal=i, role="resource", semantic_identity="q" if i != 204 else "missing", value_json=json.dumps({"metadata": {"display_label": "duplicate"}}), content_sha256="d" * 64))
        await session.commit()
    async with factory() as session:
        pages = [await browse_dataset_resources(session, project_id="p", dataset_id="d", revision_id="r", offset=i, limit=50) for i in range(0, 205, 50)]
        assert [p["total"] for p in pages] == [205] * 5
        assert [x["ordinal"] for p in pages for x in p["items"]] == list(range(205))
        assert pages[-1]["next_offset"] is None
        assert pages[0]["items"][0]["receipt"]["entity_kind"] == "alignment_document"
        assert pages[-1]["items"][-1]["receipt"] is None
        frozen = await freeze_dataset_resources(session, project_id="p", dataset_id="d", revision_id="r")
        assert len(frozen["members"]) == 205
        assert [x["ordinal"] for x in (await freeze_dataset_resources(session, project_id="p", dataset_id="d", revision_id="r", ordinals=[204, 0]))["members"]] == [0, 204]
        with pytest.raises(NotFound):
            await freeze_dataset_resources(session, project_id="other", dataset_id="d", revision_id="r")
        with pytest.raises(NotFound):
            await freeze_dataset_resources(session, project_id="p", dataset_id="d", revision_id="r", ordinals=[205])
    await engine.dispose()
