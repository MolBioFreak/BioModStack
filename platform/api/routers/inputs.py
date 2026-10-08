"""
Inputs API router - Serve input presets and standard paths.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from input_registry import get_input_registry


router = APIRouter()


@router.get("/presets")
async def list_presets(type: str = Query(..., description="Preset type: pdb, sequence, yaml, contig, ntp")):
    """
    List input presets by type.
    """
    registry = get_input_registry()
    presets = registry.list_presets(type)
    
    if type == 'pdb':
        return [
            {"id": p.id, "name": p.name, "path": p.path, "description": p.description, "category": p.category}
            for p in presets
        ]
    elif type == 'sequence':
        return [
            {"id": p.id, "name": p.name, "sequence": p.sequence, "description": p.description, "length": p.length}
            for p in presets
        ]
    elif type == 'yaml':
        return [
            {"id": p.id, "name": p.name, "description": p.description, "content": p.content}
            for p in presets
        ]
    elif type == 'contig':
        return [
            {"id": p.id, "name": p.name, "value": p.value, "description": p.description}
            for p in presets
        ]
    elif type == 'ntp':
        return [
            {"id": p.id, "name": p.name, "smiles": p.smiles, "description": p.description}
            for p in presets
        ]
    else:
        raise HTTPException(status_code=400, detail=f"Invalid preset type: {type}")


@router.get("/presets/{type}/{preset_id}")
async def get_preset(type: str, preset_id: str):
    """
    Get a specific preset by type and ID.
    """
    registry = get_input_registry()
    preset = registry.get_preset(type, preset_id)
    
    if not preset:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")
    
    return preset.model_dump()


@router.get("/paths")
async def get_standard_paths():
    """
    Get standard directory paths.
    """
    registry = get_input_registry()
    paths = registry.get_standard_paths()
    
    return {
        path_id: {
            "path": sp.path,
            "absolute_path": sp.absolute_path,
            "description": sp.description
        }
        for path_id, sp in paths.items()
    }


@router.get("/preset-directories")
async def list_preset_directories():
    """
    List directory presets for batch file processing.
    """
    registry = get_input_registry()
    directories = registry.list_presets('directory')
    
    return [
        {
            "id": d.id, 
            "name": d.name, 
            "path": d.absolute_path,  # Return absolute path for direct use
            "description": d.description, 
            "count": d.count,
            "filter_ids": d.filter_ids
        }
        for d in directories
    ]
