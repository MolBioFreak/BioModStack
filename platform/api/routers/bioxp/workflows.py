"""Pure local workflow authoring. Deliberately no robot dependencies."""
from fastapi import APIRouter
from bioxp_workflow_authoring import (
    WorkflowPreviewRequest, WorkflowPreviewResponse, discovery, preview,
    WorkflowJobCloneRequest, WorkflowJobClone, clone_job,
)

router = APIRouter()


@router.get("/workflows/schema")
async def workflow_schema():
    return discovery()


@router.post("/workflows/preview", response_model=WorkflowPreviewResponse)
async def workflow_preview(request: WorkflowPreviewRequest):
    return preview(request)


@router.post("/workflows/clone", response_model=WorkflowJobClone)
async def workflow_clone(request: WorkflowJobCloneRequest):
    return clone_job(request)
