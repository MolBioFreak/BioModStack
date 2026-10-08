"""Raw Golden Gate routes, separate from historical prepared semantics."""
from fastapi import APIRouter, Depends, HTTPException, Response
from services.assembly.golden_gate_workflow_exports import PortableWorkflow, workflow_zip
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from molbio_database import get_molbio_session
from services.assembly.golden_gate_workflow_types import WorkflowRequest, WorkflowResult, SaveDesignRequest, SavedDesign
from services.assembly.golden_gate_workflow import run_workflow
from services.assembly.golden_gate_workflow_persistence import resolve_sources, request_sources, save_workup, read_workup
from services.assembly.types import AssemblyError

router = APIRouter(prefix='/api/molbio/assembly/golden-gate', tags=['Molecular Biology'])


@router.post('/design', response_model=WorkflowResult)
async def design(request: WorkflowRequest, session: AsyncSession = Depends(get_molbio_session)):
    resolved, _ = await resolve_sources(session, request_sources(request))
    try:
        return await run_in_threadpool(run_workflow, request, resolve_revision=resolved.__getitem__)
    except (AssemblyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/design/save', response_model=SavedDesign)
async def save(request: SaveDesignRequest, session: AsyncSession = Depends(get_molbio_session)):
    try:
        return await save_workup(session, request)
    except (AssemblyError, ValueError) as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc


async def _export_response(result: WorkflowResult) -> Response:
    try:
        data = await run_in_threadpool(workflow_zip, result)
    except (AssemblyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(data, media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="golden-gate-workflow.zip"'})


@router.post('/design/export', response_class=Response,
    responses={200: {'content': {'application/zip': {}}}})
async def export_preview(result: WorkflowResult):
    """Download caller-owned displayed evidence, without persistence or reruns."""
    return await _export_response(result)


@router.post('/design/import', response_model=WorkflowResult)
async def import_workflow(document: PortableWorkflow):
    """Typed offline read/display, not recomputation or revision admission."""
    return document.result


@router.get('/design/{operation_id}/export', response_class=Response,
    responses={200: {'content': {'application/zip': {}}}})
async def export_saved(operation_id: str, session: AsyncSession = Depends(get_molbio_session)):
    saved = await read_workup(session, operation_id)
    return await _export_response(saved.result)


@router.get('/design/{operation_id}', response_model=SavedDesign)
async def read(operation_id: str, session: AsyncSession = Depends(get_molbio_session)):
    return await read_workup(session, operation_id)
