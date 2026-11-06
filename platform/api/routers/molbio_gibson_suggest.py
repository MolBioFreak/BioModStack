"""Gibson/HiFi overlap-assembly suggestions (advisory; never edits a source)."""
from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from services.assembly.gibson_suggest import SuggestRequest, SuggestResult, suggest_gibson
from services.assembly.types import AssemblyError

router = APIRouter(prefix="/api/molbio/assembly/gibson", tags=["Molecular Biology"])


@router.post("/suggest", response_model=SuggestResult)
async def suggest(request: SuggestRequest):
    try:
        return await run_in_threadpool(suggest_gibson, request)
    except (AssemblyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
