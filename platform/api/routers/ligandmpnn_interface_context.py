"""Prepared typed selected-only LigandMPNN route; deliberately unmounted.

The shared Nextflow profile binding and portable manifest/input rewrite are not
owned here. Do not mount or advertise this router until those owners land.
"""
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import Design, Job, get_session
from schemas import JobCreate
from paths import get_allowed_roots, resolve_runtime_data_path
from services.ligandmpnn_interface_selection import InterfaceContextSelection
from services.ligandmpnn_interface_publication import KEY, materialize, read_selected, regular_bytes
from services.nextflow import MODEL_MODE_WORKFLOW_ENTRYPOINTS

router = APIRouter(prefix='/api/ligandmpnn/interface-context', tags=['ligandmpnn-interface-context'])


@router.post('/selected', status_code=201)
async def submit_selected(selection: InterfaceContextSelection, background_tasks: BackgroundTasks,
                          session: AsyncSession = Depends(get_session)):
    if MODEL_MODE_WORKFLOW_ENTRYPOINTS.get(('ligandmpnn', 'interface_context')) != 'workflows/ligandmpnn_interface_context.nf':
        raise HTTPException(503, 'Selected interface-context workflow is not registered')
    from routers.jobs import _resolve_antibody_root_job, _validate_selected_design_owners, create_job
    source, root = await _resolve_antibody_root_job(session, selection.source_job_id)
    designs = list((await session.scalars(select(Design).where(Design.id.in_(selection.candidate_ids)))).all())
    by_id = {design.id: design for design in designs}
    if len(by_id) != len(selection.candidate_ids):
        raise HTTPException(404, 'Selected Design ID not found')
    await _validate_selected_design_owners(session, source, root,
                                           [by_id[identity] for identity in selection.candidate_ids])
    if selection.round_id != source.id:
        raise HTTPException(422, 'Round ID must identify the requested source Job')
    sources = {}
    try:
        allowed = tuple(root.resolve() for root in get_allowed_roots().values())
        for candidate in selection.candidate_ids:
            design = by_id[candidate]
            path = resolve_runtime_data_path(Path(design.pdb_path))
            if path.suffix.lower() != '.pdb' or not any(path.is_relative_to(r) for r in allowed):
                raise ValueError('selected structure must be a managed PDB')
            sources[candidate] = regular_bytes(path)
        binding = materialize(selection, sources)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    params = {KEY: binding, 'interface_context_manifest': binding['manifest'],
              'selection_source_job_id': source.id, 'lineage_root_job_id': root.id,
              'result_integrity_requires_designs': False}
    request = JobCreate(name=f'interface-context-{source.id[:8]}', model_id='ligandmpnn',
                        mode='interface_context', params=params)
    response = await create_job(request, background_tasks, session)
    return {'job': response, 'selection': selection.model_dump()}


@router.get('/{job_id}/result')
async def get_selected_result(job_id: str, session: AsyncSession = Depends(get_session)):
    job = await session.get(Job, job_id)
    if job is None or job.model_id != 'ligandmpnn' or job.mode != 'interface_context':
        raise HTTPException(404, 'Selected interface-context Job not found')
    try:
        return await read_selected(job, session)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from exc
