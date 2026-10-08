"""Navigation uses current demanded-page owner, preserving exact legacy selections."""
from unittest.mock import AsyncMock

import pytest

from services.global_experiments import launch_contexts, read_models
from tests.test_project_manager_read_models import _hierarchy, read_model_store


@pytest.mark.asyncio
async def test_return_selection_skips_unused_collection_queries_without_replacing_ownership(read_model_store, monkeypatch):
    async with read_model_store() as session:
        project, experiment, domain = await _hierarchy(session)
        from experiment_services import append_research_record, create_global_experiment
        from tests.test_project_manager_hierarchy import _experiment_payload
        sibling = await create_global_experiment(session, project.id, _experiment_payload())
        record = await append_research_record(session, workspace_id=project.id,
            subject_resource_id=sibling.id, record_kind="note", body="Same Project, sibling experiment")
        # Current direct-record selection is Project-scoped, not focus-scoped.
        # Do not transplant the archived replacement's stricter focus membership.
        for name in ["_record_page", "_activity_page", "_dataset_page", "_result_items"]:
            monkeypatch.setattr(read_models, name, AsyncMock(side_effect=AssertionError("unrequested collection queried")))
        attachment_page = AsyncMock(wraps=read_models._attachment_page)
        monkeypatch.setattr(read_models, "_attachment_page", attachment_page)
        for key in [f"project:{project.id}", f"global_experiment:{experiment.id}", f"domain_experiment:{domain.id}", f"research_record:{record.resource_id}"]:
            await launch_contexts._validate_return_selection(session, project_id=project.id,
                global_experiment_id=experiment.id, selected_node_key=key)
        assert attachment_page.await_count == 4
        assert {call.kwargs["family"] for call in attachment_page.await_args_list} == {"map"}
        with pytest.raises((launch_contexts.LaunchContextError, read_models.ValidationFailure)):
            await launch_contexts._validate_return_selection(session, project_id=project.id,
                global_experiment_id=experiment.id, selected_node_key="workflow:missing")


@pytest.mark.asyncio
async def test_return_to_collection_still_demands_selected_folder(read_model_store, monkeypatch):
    async with read_model_store() as session:
        project, experiment, domain = await _hierarchy(session)
        records = AsyncMock(wraps=read_models._record_page)
        monkeypatch.setattr(read_models, "_record_page", records)
        await launch_contexts._validate_return_selection(session, project_id=project.id,
            global_experiment_id=experiment.id, selected_node_key=f"virtual_folder:{domain.id}:notes")
        records.assert_awaited_once()
