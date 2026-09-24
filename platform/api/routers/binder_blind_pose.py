"""Independent selected-candidate blind pose API (mount in main.py)."""
from types import SimpleNamespace
from services.binder_diagnostic_selection import CandidateDocument, declared_targets, documents, root_id, selected_document
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job, get_session
from paths import get_inputs_dir
from schemas import JobCreate
from services.binder_blind_pose_selected import (
    BlindPoseError, KEY, launch_params, prepare_selected, read_selected,
)
from services.nextflow import MODEL_MODE_WORKFLOW_ENTRYPOINTS

router = APIRouter()


class BlindPoseSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_variant: str = Field(pattern='^(fast|full)$')
    model_id_or_path: str
    num_loops: int = Field(ge=1, le=12)
    num_sampling_steps: int = Field(ge=1, le=1000)
    num_diffusion_samples: int = Field(ge=1, le=8)
    seed: int | None = Field(default=None, ge=0)


class SelectedBlindPoseRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_job_id: str
    target_name: str | None = None
    candidate_documents: dict[str, CandidateDocument] = Field(default_factory=dict)
    design_ids: list[str] = Field(min_length=1)
    binder_chains: dict[str, list[str]]
    target_chains: list[str] = Field(min_length=1)
    settings: BlindPoseSettings


@router.post('/selected', status_code=201)
async def launch_selected(request: SelectedBlindPoseRequest, background_tasks: BackgroundTasks,
                          session: AsyncSession = Depends(get_session)):
    # Check executable registration before materializing inputs. A generic model
    # fallback would silently invoke protein_design.nf instead of this leaf.
    if MODEL_MODE_WORKFLOW_ENTRYPOINTS.get(('esmfold2', 'blind_pose')) != 'workflows/binder_blind_pose.nf':
        raise HTTPException(503, 'Selected blind pose workflow is not registered for job execution')
    if len(set(request.design_ids)) != len(request.design_ids):
        raise HTTPException(422, 'Duplicate Design IDs')
    source = await session.get(Job, request.source_job_id)
    if source is None:
        raise HTTPException(404, 'Source job not found')
    targets = await declared_targets(source, session)
    matches = [t for t in targets if request.target_name is None or t.get('name') == request.target_name]
    if len(matches) != 1:
        raise HTTPException(422, 'Select one independently declared target_name')
    target_context = matches[0]
    target = target_context.get('target_path')
    if not isinstance(target, str) or not target:
        raise HTTPException(422, 'Source job has no independently declared target structure')
    designs = (await session.scalars(select(Design).where(Design.id.in_(request.design_ids)))).all()
    by_id = {d.id: d for d in designs}
    if len(by_id) != len(request.design_ids):
        raise HTTPException(404, 'Selected Design not found')
    if any(d.job_id != source.id for d in designs):
        raise HTTPException(422, 'Selected Design belongs to a different job')
    if set(request.candidate_documents) - set(request.design_ids):
        raise HTTPException(422, 'Document selectors must belong to selected Designs')
    directory = get_inputs_dir() / 'blind_pose_selected' / uuid.uuid4().hex
    try:
        resolved, identities = [], {}
        for identity in request.design_ids:
            path, metadata = await selected_document(source, by_id[identity], request.candidate_documents.get(identity), session)
            resolved.append(SimpleNamespace(id=identity, job_id=source.id, pdb_path=str(path)))
            identities[identity] = metadata
        binding = prepare_selected(source, resolved,
                                   target_pdb=target, binder_chains=request.binder_chains,
                                   target_chains=request.target_chains, directory=directory,
                                   source_identities=identities, target_context=target_context)
        params = launch_params(directory, variant=request.settings.model_variant,
                               model_id_or_path=request.settings.model_id_or_path,
                               num_loops=request.settings.num_loops,
                               num_sampling_steps=request.settings.num_sampling_steps,
                               num_diffusion_samples=request.settings.num_diffusion_samples,
                               seed=request.settings.seed)
    except (BlindPoseError, ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    params[KEY] = binding
    params['selection_source_job_id'] = source.id
    params['lineage_root_job_id'] = root_id(source)
    from routers.jobs import create_job
    job = JobCreate(name=f'blind-pose-{source.id[:8]}', model_id='esmfold2', mode='blind_pose', params=params)
    from services.binder_blind_pose_trust import selected_submission
    with selected_submission():
        return await create_job(job, background_tasks, session)


@router.get('/{job_id}/selection-context')
async def selection_context(job_id: str, session: AsyncSession = Depends(get_session)):
    source = await session.get(Job, job_id)
    if source is None:
        raise HTTPException(404, 'Source job not found')
    targets = await declared_targets(source, session)
    designs = (await session.scalars(select(Design).where(Design.job_id == source.id))).all()
    return {'source_job_id': source.id, 'lineage_root_job_id': root_id(source),
            'targets': [{key: row[key] for key in ('name', 'owner_job_id')} for row in targets],
            'candidate_documents': {design.id: documents(source, design) for design in designs}}


@router.get('/{job_id}/result')
async def selected_result(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, 'Job not found')
    try:
        return await read_selected(job, session)
    except (BlindPoseError, ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
