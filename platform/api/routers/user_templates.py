"""
User Templates API router - CRUD operations for user-defined run templates.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated, Optional, List, Dict, Any
from datetime import datetime
import json
import uuid

from antibody_pipeline_contract import is_antibody_pipeline_mode
from database import get_session, UserTemplate
from services.user_template_revisions import (
    REVISIONED_SCHEMAS, revisioned, current_revision, append_revision,
)


router = APIRouter()


# --- Schemas ---

class UserTemplateCreate(BaseModel):
    """Request schema for creating a user template."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    icon: str = Field(default="bookmark", max_length=50)
    color: str = Field(default="#6B7280", max_length=20)
    base_template_id: Optional[str] = Field(None, max_length=100)
    model_id: Optional[str] = Field(None, max_length=50)
    mode: Optional[str] = Field(None, max_length=100)
    params: Dict[str, Any] = Field(default_factory=dict)


class UserTemplateUpdate(BaseModel):
    """Request schema for updating a user template."""
    expected_base_revision: Optional[int] = Field(None, ge=0, strict=True)
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=500)
    icon: Optional[str] = Field(None, max_length=50)
    color: Optional[str] = Field(None, max_length=20)
    params: Optional[Dict[str, Any]] = None


class UserTemplateResponse(BaseModel):
    """Response schema for a user template."""
    id: str
    name: str
    description: Optional[str]
    icon: str
    color: str
    base_template_id: Optional[str]
    model_id: Optional[str]
    mode: Optional[str]
    params: Dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


def _validate_bioxp_workflow_draft(
    mode: Optional[str],
    model_id: Optional[str],
    base_template_id: Optional[str],
    params: Any,
) -> None:
    """Validate only the saved representation, never scientific completeness.

    Keep the caller's JSON untouched: draft intents are not executable requests.
    """
    if mode != "bioxp_workflow":
        return
    if model_id is not None or base_template_id is not None:
        raise HTTPException(422, "BioXP workflow drafts have no model or base template")
    schema = params.get("schema") if isinstance(params, dict) else None
    if schema in REVISIONED_SCHEMAS:
        try:
            json.dumps(params, allow_nan=False)
        except (ValueError, TypeError):
            raise HTTPException(422, "BioXP drafts require finite JSON values") from None
        return  # Scientific completeness belongs to compile, not raw persistence.
    keys = {"schema", "steps", "editor_state"}
    if schema == "bms.bioxp-workflow-draft.v2":
        keys.add("deck_plan")
    if (
        not isinstance(params, dict)
        or set(params) != keys
        or schema not in ("bms.bioxp-workflow-draft.v1", "bms.bioxp-workflow-draft.v2")
        or not isinstance(params["steps"], list)
        or not isinstance(params["editor_state"], dict)
    ):
        raise HTTPException(422, "Invalid BioXP workflow draft envelope")
    seen = set()
    for step in params["steps"]:
        if (
            not isinstance(step, dict)
            or not {"step_id", "intent"} <= set(step) <= {"step_id", "intent", "required_capability"}
            or ("required_capability" in step and step["required_capability"] is not None
                and not isinstance(step["required_capability"], str))
            or not isinstance(step["step_id"], str)
            or not step["step_id"]
            or not isinstance(step["intent"], dict)
        ):
            raise HTTPException(422, "Invalid BioXP workflow draft step")
        if step["step_id"] in seen:
            raise HTTPException(422, "BioXP workflow draft step_id values must be distinct")
        seen.add(step["step_id"])
    if schema == "bms.bioxp-workflow-draft.v2":
        from bioxp_workflow_authoring import WorkflowDeckPlan
        from pydantic import ValidationError
        try:
            WorkflowDeckPlan.model_validate(params["deck_plan"])
        except ValidationError as exc:
            raise HTTPException(422, f"Invalid BioXP workflow deck plan: {exc}") from None
    try:
        json.dumps(params, allow_nan=False)
    except (ValueError, TypeError):
        raise HTTPException(422, "BioXP workflow drafts require finite JSON values") from None


def _is_antibody_template(template: UserTemplate) -> bool:
    model_id = (template.model_id or "").strip().lower()
    base_template_id = (template.base_template_id or "").strip().lower()
    mode = (template.mode or "").strip().lower()
    # The display launcher can contain several distinct native generators.
    # A shared card/mode label is not authority to coerce their saved settings
    # into RFantibody/VHH framework and epitope defaults.
    return model_id == "template_antibody_denovo" or (
        not model_id
        and base_template_id == "antibody_denovo"
        and is_antibody_pipeline_mode(mode)
    )


def _normalize_antibody_template_params(params: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    if not isinstance(params, dict):
        return params, False

    normalized = dict(params)
    changed = False

    framework_type = str(normalized.get("framework_type") or "").strip().lower()
    sabdab_framework = normalized.get("sabdab_framework")
    if framework_type == "sabdab" and isinstance(sabdab_framework, dict):
        sabdab_framework = dict(sabdab_framework)
        sabdab_path = str(sabdab_framework.get("filePath") or "").strip()
        custom_framework_path = str(normalized.get("custom_framework_path") or "").strip()
        framework_pdb = str(normalized.get("framework_pdb") or "").strip()

        if sabdab_path:
            if custom_framework_path != sabdab_path:
                normalized["custom_framework_path"] = sabdab_path
                changed = True
            if framework_pdb != sabdab_path:
                normalized["framework_pdb"] = sabdab_path
                changed = True
        elif framework_pdb.endswith("_hlt.pdb"):
            sabdab_framework["filePath"] = framework_pdb
            normalized["sabdab_framework"] = sabdab_framework
            changed = True
        elif custom_framework_path.endswith("_hlt.pdb"):
            sabdab_framework["filePath"] = custom_framework_path
            normalized["sabdab_framework"] = sabdab_framework
            changed = True

    selected_residues = normalized.get("selected_residues")
    if not isinstance(selected_residues, list):
        epitope_residues = str(normalized.get("epitope_residues") or "").strip()
        if epitope_residues:
            normalized["selected_residues"] = [
                residue.strip() for residue in epitope_residues.split(",") if residue.strip()
            ]
            changed = True

    selected_chain = str(normalized.get("selected_chain") or "").strip()
    antigen_chains = str(normalized.get("antigen_chains") or "").strip()
    if not selected_chain and antigen_chains:
        normalized["selected_chain"] = antigen_chains.split(",")[0].strip()
        changed = True

    return normalized, changed


async def _normalize_template_records(
    templates: List[UserTemplate],
    session: AsyncSession,
) -> List[UserTemplate]:
    changed_any = False
    for template in templates:
        if not _is_antibody_template(template):
            continue
        normalized_params, changed = _normalize_antibody_template_params(template.params or {})
        if changed:
            template.params = normalized_params
            changed_any = True

    if changed_any:
        await session.commit()
        for template in templates:
            await session.refresh(template)

    return templates


# --- Endpoints ---

@router.get("", response_model=List[UserTemplateResponse])
async def list_user_templates(
    search: Optional[str] = Query(None, description="Search by name or description"),
    model_id: Optional[str] = Query(None, description="Filter by model ID"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    mode: Annotated[Optional[str], Query(description="Include this exact mode")] = None,
    exclude_mode: Annotated[Optional[str], Query(description="Exclude this exact mode")] = None,
):
    """List all user-defined templates."""
    query = select(UserTemplate).order_by(desc(UserTemplate.created_at))
    
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            UserTemplate.name.ilike(search_pattern) | 
            UserTemplate.description.ilike(search_pattern)
        )
    
    if model_id:
        query = query.where(UserTemplate.model_id == model_id)
    if mode is not None:
        query = query.where(UserTemplate.mode == mode)
    if exclude_mode is not None:
        query = query.where(
            UserTemplate.mode.is_(None) | (UserTemplate.mode != exclude_mode)
        )
    
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    templates = result.scalars().all()

    templates = await _normalize_template_records(templates, session)
    return templates


@router.post("", response_model=UserTemplateResponse, status_code=201)
async def create_user_template(
    data: UserTemplateCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new user-defined template."""
    _validate_bioxp_workflow_draft(data.mode, data.model_id, data.base_template_id, data.params)
    # Check for duplicate name
    existing = await session.execute(
        select(UserTemplate).where(UserTemplate.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Template with name '{data.name}' already exists")
    
    template = UserTemplate(
        id=str(uuid.uuid4()),
        name=data.name,
        description=data.description,
        icon=data.icon,
        color=data.color,
        base_template_id=data.base_template_id,
        model_id=data.model_id,
        mode=data.mode,
        params=data.params,
    )
    if _is_antibody_template(template):
        template.params, _ = _normalize_antibody_template_params(data.params)
    
    session.add(template)
    if revisioned(template):
        await append_revision(session, template, 1)
    await session.commit()
    await session.refresh(template)
    
    return template


@router.get("/{template_id}", response_model=UserTemplateResponse)
async def get_user_template(
    template_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific user template by ID."""
    result = await session.execute(
        select(UserTemplate).where(UserTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    templates = await _normalize_template_records([template], session)
    template = templates[0]
    return template


@router.put("/{template_id}", response_model=UserTemplateResponse)
async def update_user_template(
    template_id: str,
    data: UserTemplateUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update a user template."""
    result = await session.execute(
        select(UserTemplate).where(UserTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Validate the effective saved row, including name-only edits. Explicit null
    # params is malformed for this mode, not an instruction to erase the draft.
    _validate_bioxp_workflow_draft(
        template.mode, template.model_id, template.base_template_id,
        data.params if "params" in data.model_fields_set else template.params,
    )

    track_revision = revisioned(template) or (
        template.mode == "bioxp_workflow" and data.params is not None
        and data.params.get("schema") in REVISIONED_SCHEMAS)
    if revisioned(template) and data.params is not None and data.params.get("schema") != template.params.get("schema"):
        raise HTTPException(422, "A revisioned template cannot change collection discriminator")
    base_revision = 0
    if track_revision:
        base_revision = await current_revision(session, template.id)
        if data.expected_base_revision != base_revision:
            raise HTTPException(409, {"code": "revision_conflict", "category": "persistence",
                "message": "Expected base revision is required and must match", "path": "/expected_base_revision",
                "current_revision": base_revision})
        if base_revision == 0:
            await append_revision(session, template, 0)  # Retain original legacy draft.

    # Update fields if provided
    if data.name is not None:
        # Check for duplicate name
        existing = await session.execute(
            select(UserTemplate).where(
                UserTemplate.name == data.name,
                UserTemplate.id != template_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Template with name '{data.name}' already exists")
        template.name = data.name
    
    if "description" in data.model_fields_set:
        template.description = data.description
    if data.icon is not None:
        template.icon = data.icon
    if data.color is not None:
        template.color = data.color
    if data.params is not None:
        params = data.params
        if _is_antibody_template(template):
            params, _ = _normalize_antibody_template_params(params)
        template.params = params
    
    if track_revision:
        await append_revision(session, template, base_revision + 1)
    await session.commit()
    await session.refresh(template)
    
    return template


@router.delete("/{template_id}", status_code=204)
async def delete_user_template(
    template_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Delete a user template."""
    result = await session.execute(
        select(UserTemplate).where(UserTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    await session.delete(template)
    await session.commit()
