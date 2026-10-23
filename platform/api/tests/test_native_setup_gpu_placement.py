"""Native setup GPU placement survives preparation, reservation and submission checks."""
import json

import pytest

from experiment_models import ExperimentLaunchContext, ExperimentWorkflowPreparation
from experiment_services import create_run_group, validate_preparation_authority
from routers.project_manager import _launch_context_document
from schemas import JobCreate
from services.global_experiments import launch_contexts, workflow_setups
from services.ngs_molbio_n5 import reserve_run_group
from test_project_workflow_setups import setup_store, _project


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned_gpu", [0, 2, None])
async def test_native_protenix_gpu_survives_real_reservation(setup_store, monkeypatch, pinned_gpu):
    from routers import gpu
    from biomodstack_local_resources import applied_local_policy
    applied_local_policy.cache_clear()
    from services import gpu_config
    monkeypatch.setattr(gpu_config, "read_scheduler_config", lambda: {"global": {"cpu_threads_per_job": 2}})
    monkeypatch.setattr(gpu, "_query_smi_gpu_map", lambda: {
        0: {"uuid": "GPU-fixture-zero"}, 2: {"uuid": "GPU-fixture-two"},
    })
    request = JobCreate(name="Protenix GPU fixture", model_id="protenix", mode="complex",
        params={"sequence": "MQIFVK:MQIFVK", "protenix_use_msa": False},
        pinned_gpu=pinned_gpu, execution_target_id=None).model_dump(mode="json")
    try:
        async with setup_store() as session:
            project = await _project(session)
            setup = await workflow_setups.create_workflow_setup(session, project_id=project.id,
                relationship_kind="primary", global_experiment_id=None, experiment_name="GPU placement",
                experiment_objective="Preserve selected placement", domain_kind="protein_in_silico",
                capability_id="protein.structure_prediction.protenix_v2", idempotency_key="setup")
            await workflow_setups.save_workflow_setup_draft(session, project_id=project.id,
                setup_context_id=setup["setup_context_id"], expected_generation=0,
                draft={"editor_state": {}, "native_job_request": request}, idempotency_key="save")
            prepared = await workflow_setups.prepare_workflow_setup_launch(session, project_id=project.id,
                setup_context_id=setup["setup_context_id"], expected_generation=1, idempotency_key="prepare")
            await session.commit()
        # Reopen the persisted preparation, not an in-memory draft.
        async with setup_store() as session:
            preparation = await session.get(ExperimentWorkflowPreparation, prepared["preparation_id"])
            await validate_preparation_authority(session, preparation)
            scheduler = json.loads(preparation.scheduler_payload_json)
            assert scheduler["resources"]["pinned_gpu"] == pinned_gpu
            assert scheduler["resources"]["cpu_threads"] == min(2, applied_local_policy().cpu_threads)
            assert scheduler["resources"]["dram_gib"] * 1024**3 == applied_local_policy().memory_bytes
            context = await session.get(ExperimentLaunchContext, prepared["launch_context_id"])
            assert await launch_contexts.workflow_pinned_gpu(session, context) == pinned_gpu
            document = await _launch_context_document(session, context)
            submitted = JobCreate.model_validate(document["pinned_scheduler"])
            assert submitted.pinned_gpu == pinned_gpu
            assert submitted.execution_target_id is None
            group = await create_run_group(session, project.id, [preparation.resource_id],
                idempotency_key="run", source_domain_id=setup["domain_experiment_id"],
                launch_context_ids={preparation.resource_id: context.launch_context_id})
            await reserve_run_group(session, group_id=group.resource_id,
                domain_id=setup["domain_experiment_id"], actor="operator")
            await session.commit()
        async with setup_store() as session:
            context = await session.get(ExperimentLaunchContext, prepared["launch_context_id"])
            assert await launch_contexts.validate_prepared_child_job_request(session, context, submitted)
            params = await launch_contexts.validate_bound_job_request(session, context,
                job_name=submitted.name, model_id=submitted.model_id, mode=submitted.mode,
                params=submitted.params, pinned_gpu=submitted.pinned_gpu)
            assert params["sequence"] == submitted.params["sequence"]
            changed = submitted.model_copy(update={"pinned_gpu": 1 if pinned_gpu != 1 else 0})
            with pytest.raises(launch_contexts.LaunchContextError, match="immutable normalized Job authority"):
                await launch_contexts.validate_prepared_child_job_request(session, context, changed)
    finally:
        applied_local_policy.cache_clear()
