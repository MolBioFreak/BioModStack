"""Independent selected-candidate blind pose API (mount in main.py)."""
from pathlib import Path
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
    target = (source.params or {}).get('target_pdb')
    if not isinstance(target, str) or not target:
        raise HTTPException(422, 'Source job has no independently declared target PDB')
    designs = (await session.scalars(select(Design).where(Design.id.in_(request.design_ids)))).all()
    by_id = {d.id: d for d in designs}
    if len(by_id) != len(request.design_ids):
        raise HTTPException(404, 'Selected Design not found')
    if any(d.job_id != source.id for d in designs):
        raise HTTPException(422, 'Selected Design belongs to a different job')
    directory = get_inputs_dir() / 'blind_pose_selected' / uuid.uuid4().hex
    try:
        binding = prepare_selected(source, [by_id[i] for i in request.design_ids],
                                   target_pdb=target, binder_chains=request.binder_chains,
                                   target_chains=request.target_chains, directory=directory)
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
    params['lineage_root_job_id'] = source.lineage_root_job_id or source.id
    from routers.jobs import create_job
    job = JobCreate(name=f'blind-pose-{source.id[:8]}', model_id='esmfold2', mode='blind_pose', params=params)
    return await create_job(job, background_tasks, session)


@router.get('/{job_id}/result')
async def selected_result(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, 'Job not found')
    try:
        return await read_selected(job, session)
    except (BlindPoseError, ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
