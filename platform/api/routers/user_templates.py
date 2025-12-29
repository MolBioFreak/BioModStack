"""
User Templates API router - CRUD operations for user-defined run templates.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import uuid

from database import get_session, UserTemplate


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

    class Config:
        from_attributes = True


# --- Endpoints ---

@router.get("", response_model=List[UserTemplateResponse])
async def list_user_templates(
    search: Optional[str] = Query(None, description="Search by name or description"),
    model_id: Optional[str] = Query(None, description="Filter by model ID"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session)
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
    
    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    templates = result.scalars().all()
    
    return templates


@router.post("", response_model=UserTemplateResponse, status_code=201)
async def create_user_template(
    data: UserTemplateCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new user-defined template."""
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
    
    session.add(template)
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
    
    if data.description is not None:
        template.description = data.description
    if data.icon is not None:
        template.icon = data.icon
    if data.color is not None:
        template.color = data.color
    if data.params is not None:
        template.params = data.params
    
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
