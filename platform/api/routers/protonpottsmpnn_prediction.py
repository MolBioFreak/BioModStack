"""Prediction handoff for selected native ProtonPottsMPNN output sequences."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_session
from experiment_database import get_experiment_session
from services.protonpottsmpnn_prediction import PredictionSelection, launch_prediction

router = APIRouter()


@router.post('/{job_id}/protonpottsmpnn/predict', status_code=201)
async def predict_selected(job_id: str, request: PredictionSelection,
                           background_tasks: BackgroundTasks,
                           session: AsyncSession = Depends(get_session),
                           experiment_session: AsyncSession = Depends(get_experiment_session)):
    if not request.launch_context_id or not request.idempotency_key:
        return await launch_prediction(job_id, request, background_tasks, session, experiment_session)
    from services.global_experiments.workflow_setups import _claim, _replay, _request_digest
    from experiment_services import ExperimentServiceError
    from routers.project_manager import _service_error

    scope = f'protonpottsmpnn-predict:{request.launch_context_id}'
    digest = _request_digest({'source_job_id': job_id, **request.model_dump(mode='json')})
    try:
        replay = await _replay(experiment_session, scope=scope, key=request.idempotency_key,
                               request_sha256=digest)
        if replay is not None:
            if replay['status'] == 409:
                raise HTTPException(409, replay['body'])
            return replay['body']
        try:
            body = await launch_prediction(job_id, request, background_tasks, session, experiment_session)
            status = 201
        except HTTPException as exc:
            if not (exc.status_code == 409 and isinstance(exc.detail, dict)
                    and exc.detail.get('code') == 'remote_prepared_job_review_required'):
                raise
            status, body = 409, exc.detail
        body = jsonable_encoder(body)
        await _claim(experiment_session, scope=scope, key=request.idempotency_key,
                     request_sha256=digest, result_resource_id=request.launch_context_id,
                     response={'status': status, 'body': body})
        await experiment_session.commit()
        if status == 409:
            raise HTTPException(409, body)
        return body
    except ExperimentServiceError as exc:
        await experiment_session.rollback()
        raise _service_error(exc) from exc
