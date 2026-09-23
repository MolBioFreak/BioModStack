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
from services.ligandmpnn_interface_selection import InterfaceContextSelection, selected_submission
from services.ligandmpnn_interface_publication import KEY, materialize, read_selected, regular_bytes
from services.nextflow import MODEL_MODE_WORKFLOW_ENTRYPOINTS

router = APIRouter(prefix='/api/ligandmpnn/interface-context', tags=['ligandmpnn-interface-context'])


@router.post('/selected', status_code=201)
async def submit_selected(selection: InterfaceContextSelection, background_tasks: BackgroundTasks,
                          session: AsyncSession = Depends(get_session)):
    if MODEL_MODE_WORKFLOW_ENTRYPOINTS.get(('ligandmpnn', 'interface_context')) != 'workflows/ligandmpnn_interface_context.nf':
        raise HTTPException(503, 'Selected interface-context workflow is not registered')
    from routers.jobs import create_job
    source = await session.get(Job, selection.source_job_id)
    if source is None:
        raise HTTPException(404, 'Source Job not found')
    # This diagnostic consumes exact owned Designs, not the antibody refinement lineage.
    root = source
    designs = list((await session.scalars(select(Design).where(Design.id.in_(selection.candidate_ids)))).all())
    by_id = {design.id: design for design in designs}
    if len(by_id) != len(selection.candidate_ids):
        raise HTTPException(404, 'Selected Design ID not found')
    if any(by_id[identity].job_id != source.id for identity in selection.candidate_ids):
        raise HTTPException(422, 'Selected Design belongs to another Job')
    if source.model_id == 'bindcraft2':
        from services.bindcraft2_publication import read_published_native_results
        await read_published_native_results(source, session)
    if selection.round_id != source.id:
        raise HTTPException(422, 'Round ID must identify the requested source Job')
    sources = {}
    try:
        allowed = tuple(root.resolve() for root in get_allowed_roots().values())
        identities = {}
        for candidate in selection.candidate_ids:
            design = by_id[candidate]
            path = resolve_runtime_data_path(Path(design.pdb_path))
            if path.suffix.lower() not in {'.pdb', '.cif', '.mmcif'} or not any(path.is_relative_to(r) for r in allowed):
                raise ValueError('selected structure must be a managed PDB or CIF')
            original = regular_bytes(path)
            import hashlib
            identities[candidate] = {'path': str(path), 'sha256': hashlib.sha256(original).hexdigest(),
                                     'format': path.suffix.lower(), 'owner_job_id': source.id}
            if source.model_id == 'bindcraft2':
                identities[candidate]['primary_artifact_id'] = (design.provenance or {})['primary_artifact_id']
            if path.suffix.lower() in {'.cif', '.mmcif'}:
                from routers.jobs import _cif_selection_pdb
                import tempfile
                with tempfile.TemporaryDirectory() as scratch:
                    converted = Path(scratch) / 'selected.pdb'
                    _cif_selection_pdb(path, converted)
                    sources[candidate] = regular_bytes(converted)
            else:
                sources[candidate] = original
        binding = materialize(selection, sources, source_identities=identities)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    params = {KEY: binding, 'interface_context_manifest': binding['manifest'],
              'selection_source_job_id': source.id, 'lineage_root_job_id': root.id,
              'result_integrity_requires_designs': False}
    request = JobCreate(name=f'interface-context-{source.id[:8]}', model_id='ligandmpnn',
                        mode='interface_context', params=params)
    token = selected_submission.set(True)
    try:
        response = await create_job(request, background_tasks, session)
    finally:
        selected_submission.reset(token)
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
