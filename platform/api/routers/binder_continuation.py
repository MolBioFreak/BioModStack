"""Small selected-operation boundary; no antibody modality or workflow fallback."""
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import Job, get_session
from experiment_database import get_experiment_session
from services.binder_continuation import individual_model_requests, model_request, resolve_selection, resolve_candidate_documents, snapshot_selection
from services.binder_diagnostic_selection import CandidateDocument
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
    candidate_documents: dict[str, CandidateDocument] = Field(default_factory=dict)
    launch_context_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


async def _launch_selected(request: SelectedOperationRequest, background_tasks: BackgroundTasks,
                          session: AsyncSession = Depends(get_session),
                          experiment_session: AsyncSession = Depends(get_experiment_session)):
    from routers.jobs import create_job, get_job
    source, root, designs = await resolve_selection(session, request.source_job_id, request.design_ids)
    try:
        documents = await resolve_candidate_documents(session, designs, request.candidate_documents)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
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
                selected = {design.id: request.candidate_documents[design.id]} if design.id in documents else {}
                selections.extend(await design_selections(session, source_parent=owner,
                    design_ids=[design.id], **({'candidate_documents': selected} if selected else {})))
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
        except (FrustraMPNNChildError, StructureDatasetFanoutError, ValueError, OSError) as exc:
            await session.rollback()
            raise HTTPException(422, str(exc)) from exc
    else:
        if request.frustrampnn_settings is not None:
            raise HTTPException(422, 'FrustraMPNN settings belong to the FrustraMPNN operation')
        try:
            directory = snapshot_selection(source, root, designs, candidate_documents=documents)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        job = model_request(operation=request.operation, params=request.params,
                            source=source, root=root, selection_dir=directory,
                            execution_target_id=request.execution_target_id)
        requests = individual_model_requests(job, request.operation, directory)
        response_context = {'source_job_id': source.id, 'root_job_id': root.id,
                            'operation': request.operation, 'selected_design_count': len(designs)}
        from routers.jobs import submit_selected_child_jobs
        launched = await submit_selected_child_jobs(
            requests, background_tasks, session, experiment_session,
            destination_launch_context_id=request.launch_context_id,
            idempotency_key=request.idempotency_key or str(directory), response_context=response_context)
    return {'source_job_id': source.id, 'root_job_id': root.id,
            'operation': request.operation, 'selected_design_count': len(designs),
            'launched_jobs': launched}


@router.post('/selected', status_code=201)
async def launch_selected(request: SelectedOperationRequest, background_tasks: BackgroundTasks,
                          session: AsyncSession = Depends(get_session),
                          experiment_session: AsyncSession = Depends(get_experiment_session)):
    if not request.launch_context_id or not request.idempotency_key:
        return await _launch_selected(request, background_tasks, session, experiment_session)
    from fastapi.encoders import jsonable_encoder
    from services.global_experiments.workflow_setups import _claim, _replay, _request_digest
    from experiment_services import ExperimentServiceError
    from routers.project_manager import _service_error
    scope = f'binder-selected:{request.launch_context_id}'
    digest = _request_digest(request.model_dump(mode='json'))
    try:
        replay = await _replay(experiment_session, scope=scope, key=request.idempotency_key, request_sha256=digest)
        if replay is not None:
            if replay['status'] == 409:
                raise HTTPException(409, replay['body'])
            return replay['body']
        try:
            body = await _launch_selected(request, background_tasks, session, experiment_session)
            status = 201
        except HTTPException as exc:
            if not (exc.status_code == 409 and isinstance(exc.detail, dict)
                    and exc.detail.get('code') == 'remote_prepared_job_review_required'):
                raise
            status, body = 409, exc.detail
        body = jsonable_encoder(body)
        await _claim(experiment_session, scope=scope, key=request.idempotency_key, request_sha256=digest,
                     result_resource_id=request.launch_context_id, response={'status': status, 'body': body})
        await experiment_session.commit()
        if status == 409:
            raise HTTPException(409, body)
        return body
    except ExperimentServiceError as exc:
        await experiment_session.rollback()
        raise _service_error(exc) from exc
