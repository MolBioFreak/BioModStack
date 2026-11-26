"""Return URI selection validation preserves Project ownership without read_models."""
import pytest

from services.global_experiments import launch_contexts
from tests._project_store_helpers import _hierarchy, read_model_store


@pytest.mark.asyncio
async def test_return_selection_validates_project_ownership_directly(read_model_store):
    async with read_model_store() as session:
        project, experiment, domain = await _hierarchy(session)
        from experiment_services import append_research_record, create_global_experiment
        from tests.test_project_manager_hierarchy import _experiment_payload

        sibling = await create_global_experiment(session, project.id, _experiment_payload())
        record = await append_research_record(
            session,
            workspace_id=project.id,
            subject_resource_id=sibling.id,
            record_kind="note",
            body="Same Project, sibling experiment",
        )
        for key in [
            f"project:{project.id}",
            f"global_experiment:{experiment.id}",
            f"domain_experiment:{domain.id}",
            f"research_record:{record.resource_id}",
            f"virtual_folder:{domain.id}:notes",
        ]:
            await launch_contexts._validate_return_selection(
                session,
                project_id=project.id,
                global_experiment_id=experiment.id,
                selected_node_key=key,
            )
        for bad_key in [
            "workflow:missing",
            "project:other-project",
            f"virtual_folder:{domain.id}:unknown",
            "malformed",
        ]:
            with pytest.raises(launch_contexts.LaunchContextError):
                await launch_contexts._validate_return_selection(
                    session,
                    project_id=project.id,
                    global_experiment_id=experiment.id,
                    selected_node_key=bad_key,
                )
