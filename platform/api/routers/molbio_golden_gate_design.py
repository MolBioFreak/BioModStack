"""Raw Golden Gate routes, separate from historical prepared semantics."""
from fastapi import APIRouter, Depends, HTTPException
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


@router.get('/design/{operation_id}', response_model=SavedDesign)
async def read(operation_id: str, session: AsyncSession = Depends(get_molbio_session)):
    return await read_workup(session, operation_id)
