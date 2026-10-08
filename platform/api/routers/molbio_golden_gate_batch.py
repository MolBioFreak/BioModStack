"""One request, sequential native calls, delivered partial results; no jobs or stores."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from molbio_database import get_molbio_session
from services.assembly.golden_gate_batch import (
    BatchRequest, BatchEvent, BatchProgress, ScopeEvent, ResultEvent, ErrorEvent, FinishedEvent,
    combination_count, selected_indices, combination,
)
from services.assembly.golden_gate_workflow import run_workflow
from services.assembly.golden_gate_workflow_persistence import resolve_sources, request_sources
from services.assembly.types import AssemblyError

# Included by molbio_golden_gate_design.router, which owns the public prefix.
router = APIRouter(tags=['Molecular Biology'])


async def batch_events(body: BatchRequest, session: AsyncSession, disconnected):
    total = combination_count(body)
    selected = total if body.scope.mode == 'full' else body.scope.count
    evaluated = completed = 0
    def progress():
        return BatchProgress(total=str(total), selected=str(selected), evaluated=str(evaluated),
            completed=str(completed), omitted=str(total-selected), remaining=str(selected-evaluated))
    yield ScopeEvent(mode=body.scope.mode, seed=body.scope.seed if body.scope.mode == 'sampled' else None, progress=progress())
    for index in selected_indices(body):
        # Cooperative boundary only: an already running native calculation is not preempted.
        if await disconnected():
            return
        try:
            native, choices = combination(body, index)
            resolved, _ = await resolve_sources(session, request_sources(native))
            result = await run_in_threadpool(run_workflow, native, resolve_revision=resolved.__getitem__)
        except (AssemblyError, ValueError, HTTPException) as exc:
            evaluated += 1
            yield ErrorEvent(index=str(index), message=str(exc.detail if isinstance(exc, HTTPException) else exc), progress=progress())
        else:
            evaluated += 1
            completed += 1
            yield ResultEvent(index=str(index), alternatives=choices, result=result, progress=progress())
    yield FinishedEvent(progress=progress(), coverage=body.scope.mode if completed == selected else 'incomplete')


class NDJSONResponse(StreamingResponse):
    media_type = 'application/x-ndjson'


@router.post('/design/batch', response_class=NDJSONResponse, responses={200: {
    'description': 'Each newline-delimited JSON record has this BatchEvent schema. EOF without finished means incomplete delivery.',
    'model': BatchEvent,
    'content': {'application/x-ndjson': {'schema': {'type': 'object'}}},
}})
async def batch(body: BatchRequest, request: Request, session: AsyncSession = Depends(get_molbio_session)):
    async def stream():
        async for event in batch_events(body, session, request.is_disconnected):
            yield event.model_dump_json() + '\n'
    return NDJSONResponse(stream(), headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-store'})
