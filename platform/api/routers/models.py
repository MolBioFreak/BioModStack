"""
Models API router - List available models and their configurations.
"""

from fastapi import APIRouter, HTTPException
from typing import Optional, List

from model_registry import get_registry, ModelDefinition


router = APIRouter()


@router.get("", response_model=List[dict])
async def list_models(
    category: Optional[str] = None,
    include_experimental: bool = False
):
    """
    List all available models.
    
    - **category**: Filter by category (backbone_generation, sequence_design, etc.)
    - **include_experimental**: Include models marked as experimental/disabled
    """
    registry = get_registry()
    models = registry.list_models(
        category=category, 
        enabled_only=not include_experimental
    )
    
    return [
        {
            "id": m.id,
            "name": m.name,
            "version": m.version,
            "category": m.category,
            "description": m.description,
            "modes": [
                {
                    "id": mode.id, 
                    "name": mode.name,
                    "description": mode.description,
                    "params": mode.params
                }
                for mode in m.modes
            ],
            "params": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default,
                    "enum": p.enum,
                    "minimum": p.minimum,
                    "maximum": p.maximum,
                    "hidden": p.hidden,
                    "preset_type": getattr(p, 'preset_type', None),
                    "file_type": getattr(p, 'file_type', None),
                }
                for p in m.params
            ],
            "enabled": m.enabled,
            "experimental": m.experimental,
            "ui_icon": m.ui_icon,
            "ui_color": m.ui_color,
        }
        for m in models
    ]


@router.get("/categories")
async def list_categories():
    """Get list of model categories."""
    registry = get_registry()
    return {"categories": registry.get_categories()}


@router.get("/{model_id}")
async def get_model(model_id: str):
    """
    Get full details of a specific model including parameter schema.
    """
    registry = get_registry()
    model = registry.get_model(model_id)
    
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    
    return {
        "id": model.id,
        "name": model.name,
        "version": model.version,
        "category": model.category,
        "description": model.description,
        "container": model.container,
        "inputs": model.inputs,
        "outputs": model.outputs,
        "modes": [
            {
                "id": mode.id,
                "name": mode.name,
                "description": mode.description,
                "params": mode.params,
            }
            for mode in model.modes
        ],
        "params": [
            {
                "name": p.name,
                "type": p.type,
                "description": p.description,
                "required": p.required,
                "default": p.default,
                "enum": p.enum,
                "minimum": p.minimum,
                "maximum": p.maximum,
                "hidden": p.hidden,
                "preset_type": getattr(p, 'preset_type', None),
                "file_type": getattr(p, 'file_type', None),
            }
            for p in model.params
        ],
        "ntp_templates": [
            {
                "id": t.id,
                "name": t.name,
                "smiles": t.smiles,
                "description": t.description,
            }
            for t in model.ntp_templates
        ],
        "enabled": model.enabled,
        "experimental": model.experimental,
        "ui_icon": model.ui_icon,
        "ui_color": model.ui_color,
    }


@router.get("/{model_id}/integration")
async def get_model_integration(model_id: str):
    """Return the shared, non-launchable workflow-integration contract for a model."""
    registry = get_registry()
    model = registry.get_internal_model_definition(model_id)
    if not model or not model.enabled or model.integration is None:
        raise HTTPException(status_code=404, detail=f"Model integration '{model_id}' not found")
    return {
        "model_id": model.id,
        "model_name": model.name,
        "model_version": model.version,
        **model.integration.model_dump(),
    }


@router.get("/{model_id}/modes")
async def get_model_modes(model_id: str):
    """Get available modes for a model."""
    registry = get_registry()
    model = registry.get_model(model_id)
    
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    
    return {
        "model_id": model_id,
        "modes": [
            {
                "id": mode.id,
                "name": mode.name,
                "description": mode.description,
                "params": mode.params,
            }
            for mode in model.modes
        ]
    }


@router.get("/{model_id}/ntp-templates")
async def get_ntp_templates(model_id: str):
    """Get NTP templates for nucleotide-aware models."""
    registry = get_registry()
    model = registry.get_model(model_id)
    
    if not model:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    
    if not model.ntp_templates:
        return {"model_id": model_id, "templates": [], "message": "No NTP templates for this model"}
    
    return {
        "model_id": model_id,
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "smiles": t.smiles,
                "description": t.description,
            }
            for t in model.ntp_templates
        ]
    }
