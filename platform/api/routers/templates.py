"""
Templates API router - List available experiment templates.
"""

from fastapi import APIRouter, HTTPException
from typing import List

from template_registry import get_template_registry


router = APIRouter()


@router.get("", response_model=List[dict])
async def list_templates():
    """
    List all available experiment templates.
    """
    registry = get_template_registry()
    templates = registry.list_templates(enabled_only=True)
    
    return [
        {
            "id": t.id,
            "name": t.name,
            "icon": t.icon,
            "color": t.color,
            "description": t.description,
            "goal": t.goal,
            "status": t.status,
            "experimental": t.experimental,
            "stages": [
                {"name": s.name, "tool": s.tool, "description": s.description}
                for s in t.stages
            ],
        }
        for t in templates
    ]


@router.get("/{template_id}")
async def get_template(template_id: str):
    """
    Get full details of a specific template including parameters.
    """
    registry = get_template_registry()
    template = registry.get_template(template_id)
    
    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    
    return {
        "id": template.id,
        "name": template.name,
        "icon": template.icon,
        "color": template.color,
        "description": template.description,
        "goal": template.goal,
        "status": template.status,
        "experimental": template.experimental,
        "stages": [
            {"name": s.name, "tool": s.tool, "description": s.description}
            for s in template.stages
        ],
        "preset_params": template.preset_params,
        "user_params": [
            {
                "name": p.name,
                "label": p.label,
                "type": p.type,
                "required": p.required,
                "default": p.default,
                "description": p.description,
                "enum": p.enum,
                "enum_labels": p.enum_labels,
                "minimum": p.minimum,
                "maximum": p.maximum,
                "step": p.step,
                "ui_control": p.ui_control,
                "ui_group": p.ui_group,
                "ui_order": p.ui_order,
                "ui_placeholder": p.ui_placeholder,
                "placeholder": p.placeholder,
                "preset_type": p.preset_type,
                "file_type": p.file_type,
                "recommended_range": p.recommended_range,
                "default_source": p.default_source,
                "condition": p.condition,  # Include condition for conditional visibility
            }
            for p in template.user_params
        ],
        "ntp_templates": [
            {"id": n.id, "name": n.name, "smiles": n.smiles}
            for n in (template.ntp_templates or [])
        ],
    }
