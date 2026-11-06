"""Authenticated private Studio assets and both upstream API route forms."""
from fastapi import APIRouter, HTTPException, Request

from routers.experiment_workspaces import _authenticated_principal
from routers.molbio_share import browser_mutation_origin
from services.atomworks_studio import PREFIX, forward

router = APIRouter(prefix=PREFIX, tags=["atomworks-studio"])


@router.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"])
async def studio(request: Request, path: str):
    try:
        actor, _roles = _authenticated_principal(request)
        if request.method not in {"GET", "HEAD"}:
            browser_mutation_origin(request)
        return await forward(request, path, actor)
    except HTTPException as exc:
        exc.headers = {**(exc.headers or {}), "Cache-Control": "private, no-store"}
        raise
