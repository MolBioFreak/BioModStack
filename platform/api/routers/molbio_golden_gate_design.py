"""Raw Golden Gate routes, separate from historical prepared semantics."""
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import TypeAdapter
from services.assembly.golden_gate_workflow_exports import PortableWorkflow, workflow_zip
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from molbio_database import get_molbio_session
from services.assembly.golden_gate_workflow_types import WorkflowRequest, WorkflowResult, SaveDesignRequest, SavedDesign
from services.assembly.golden_gate_workflow_wire import WorkflowWire, expand_workflow, project_workflow
from services.assembly.golden_gate_workflow import run_workflow
from services.assembly.golden_gate_workflow_persistence import resolve_sources, request_sources, save_workup, read_workup
from services.assembly.types import AssemblyError
from routers.molbio_golden_gate_batch import router as batch_router

router = APIRouter(prefix='/api/molbio/assembly/golden-gate', tags=['Molecular Biology'])
router.include_router(batch_router)
WireView = Literal['full', 'normalized']


def _native(value, kind, native_type):
    if not isinstance(value, WorkflowWire):
        return value
    try:
        return TypeAdapter(native_type).validate_python(expand_workflow(value, kind))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _view(value, kind, view):
    return project_workflow(value.model_dump(mode='json'), kind) if view == 'normalized' else value


@router.post('/design', response_model=WorkflowResult | WorkflowWire)
async def design(request: WorkflowRequest | WorkflowWire, session: AsyncSession = Depends(get_molbio_session), view: WireView = 'full'):
    request = _native(request, 'request', WorkflowRequest)
    resolved, _ = await resolve_sources(session, request_sources(request))
    try:
        result = await run_in_threadpool(run_workflow, request, resolve_revision=resolved.__getitem__)
        return _view(result, 'result', view)
    except (AssemblyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/design/save', response_model=SavedDesign | WorkflowWire)
async def save(request: SaveDesignRequest | WorkflowWire, session: AsyncSession = Depends(get_molbio_session), view: WireView = 'full'):
    request = _native(request, 'save', SaveDesignRequest)
    try:
        return _view(await save_workup(session, request), 'saved', view)
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
async def export_preview(result: WorkflowResult | WorkflowWire):
    """Download caller-owned displayed evidence, without persistence or reruns."""
    return await _export_response(_native(result, 'result', WorkflowResult))


@router.post('/design/import', response_model=WorkflowResult | WorkflowWire)
async def import_workflow(document: PortableWorkflow | WorkflowWire, view: WireView = 'full'):
    """Typed offline read/display, not recomputation or revision admission."""
    return _view(_native(document, 'portable', PortableWorkflow).result, 'result', view)


@router.get('/design/{operation_id}/export', response_class=Response,
    responses={200: {'content': {'application/zip': {}}}})
async def export_saved(operation_id: str, session: AsyncSession = Depends(get_molbio_session)):
    saved = await read_workup(session, operation_id)
    return await _export_response(saved.result)


@router.get('/design/{operation_id}', response_model=SavedDesign | WorkflowWire)
async def read(operation_id: str, session: AsyncSession = Depends(get_molbio_session), view: WireView = 'full'):
    return _view(await read_workup(session, operation_id), 'saved', view)
