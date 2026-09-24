"""Small selected-operation boundary; no antibody modality or workflow fallback."""
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session
from services.binder_continuation import individual_model_requests, model_request, resolve_selection, snapshot_selection
from services.frustrampnn.settings import FrustraMPNNRequestedSettings, default_settings

router = APIRouter()


class SelectedOperationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_job_id: str
    design_ids: list[str] = Field(min_length=1)
    operation: Literal['refine', 'caliby', 'frustrampnn', 'fampnn', 'proteinmpnn', 'predict_boltz2', 'predict_protenix']
    params: dict[str, Any] = Field(default_factory=dict)
    frustrampnn_settings: FrustraMPNNRequestedSettings | None = None
    execution_target_id: str | None = None


@router.post('/selected', status_code=201)
async def launch_selected(request: SelectedOperationRequest, background_tasks: BackgroundTasks,
                          session: AsyncSession = Depends(get_session)):
    from routers.jobs import create_job, get_job
    source, root, designs = await resolve_selection(session, request.source_job_id, request.design_ids)
    if request.operation == 'frustrampnn':
        from services.frustrampnn.jobs import FrustraMPNNChildError, design_selections
        from services.structure_dataset_fanout import StructureDatasetFanoutError
        from routers.frustrampnn import _fanout_design_selections
        if request.params or request.execution_target_id is not None:
            raise HTTPException(422, 'FrustraMPNN uses its model-owned settings and scheduler placement')
        try:
            # Resolve each exact persisted owner through the full model adapter;
            # the shared same-root check above permits repeated-round siblings.
            selections = []
            for design in designs:
                owner = await session.get(Job, design.job_id)
                selections.extend(await design_selections(session, source_parent=owner,
                                                          design_ids=[design.id]))
            for ordinal, selection in enumerate(selections):
                selection.producer_coordinates.update({
                    'selection_ordinal': ordinal,
                    'lineage_root_job_id': root.id,
                    'parent_design_id': designs[ordinal].id,
                    'source_design_provenance': designs[ordinal].provenance,
                    'source_review_artifact_manifest': designs[ordinal].review_artifact_manifest,
                    'source_review_role_map': designs[ordinal].review_role_map,
                })
            fanout = await _fanout_design_selections(session, parent=source, selections=selections,
                requested_settings=request.frustrampnn_settings or default_settings(),
                trigger='binder_selected')
            launched = [await get_job(child.id, session) for child in fanout.child_jobs]
        except (FrustraMPNNChildError, StructureDatasetFanoutError) as exc:
            await session.rollback()
            raise HTTPException(422, str(exc)) from exc
    else:
        if request.frustrampnn_settings is not None:
            raise HTTPException(422, 'FrustraMPNN settings belong to the FrustraMPNN operation')
        try:
            directory = snapshot_selection(source, root, designs)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        job = model_request(operation=request.operation, params=request.params,
                            source=source, root=root, selection_dir=directory,
                            execution_target_id=request.execution_target_id)
        requests = individual_model_requests(job, request.operation, directory)
        response_context = {'source_job_id': source.id, 'root_job_id': root.id,
                            'operation': request.operation, 'selected_design_count': len(designs)}
        from routers.jobs import _require_prepared_remote_review
        if len(requests) == 1:
            _require_prepared_remote_review(requests[0], response_context)
        elif request.execution_target_id:
            # The same existing placement review receives every retained native
            # request. No endpoint replay or resnapshot during review.
            raise HTTPException(409, {'code': 'remote_prepared_job_review_required',
                                     'job_requests': [item.model_dump(mode='json') for item in requests],
                                     'response_context': response_context})
        try:
            launched = [await create_job(item, background_tasks, session, _commit=False) for item in requests]
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return {'source_job_id': source.id, 'root_job_id': root.id,
            'operation': request.operation, 'selected_design_count': len(designs),
            'launched_jobs': launched}
