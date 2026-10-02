"""Pure local workflow authoring. Deliberately no robot dependencies."""
from fastapi import APIRouter
from bioxp_workflow_authoring import (
    WorkflowPreviewRequest, WorkflowPreviewResponse, discovery, preview,
)

router = APIRouter()


@router.get("/workflows/schema")
async def workflow_schema():
    return discovery()


@router.post("/workflows/preview", response_model=WorkflowPreviewResponse)
async def workflow_preview(request: WorkflowPreviewRequest):
    return preview(request)
