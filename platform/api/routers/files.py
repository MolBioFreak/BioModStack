"""
File serving and directory browsing API router.
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pathlib import Path
from datetime import datetime
import os
import shutil

from schemas import DirectoryListing, DirectoryEntry

router = APIRouter()

# Base directories that can be browsed
ALLOWED_DIRECTORIES = [
    "pdj_results",
    "benchmarkdata",
    "lib",
    "rcsb",
    "inputs",  # For antibody and other input file uploads
]

# Project root (parent of platform directory)
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def is_path_allowed(path: Path) -> bool:
    """Check if path is within allowed directories."""
    try:
        resolved = path.resolve()
        project_root = PROJECT_ROOT.resolve()
        
        # Must be within project root
        if not str(resolved).startswith(str(project_root)):
            return False
        
        # Check if it's in an allowed subdirectory
        rel_path = resolved.relative_to(project_root)
        first_part = rel_path.parts[0] if rel_path.parts else ""
        
        return first_part in ALLOWED_DIRECTORIES
    except (ValueError, RuntimeError):
        return False


@router.get("/browse")
async def browse_directory(path: str = "") -> DirectoryListing:
    """Browse a directory within allowed paths."""
    if not path:
        # Return list of allowed root directories
        entries = []
        for dir_name in ALLOWED_DIRECTORIES:
            dir_path = PROJECT_ROOT / dir_name
            if dir_path.exists():
                entries.append(DirectoryEntry(
                    name=dir_name,
                    path=dir_name,
                    is_directory=True,
                    size_bytes=None,
                    modified_at=datetime.fromtimestamp(dir_path.stat().st_mtime)
                ))
        return DirectoryListing(path="", entries=entries)
    
    full_path = PROJECT_ROOT / path
    
    if not is_path_allowed(full_path):
        raise HTTPException(status_code=403, detail="Access denied to this path")
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    
    if not full_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    
    entries = []
    for item in sorted(full_path.iterdir()):
        stat = item.stat()
        entries.append(DirectoryEntry(
            name=item.name,
            path=str(item.relative_to(PROJECT_ROOT)),
            is_directory=item.is_dir(),
            size_bytes=stat.st_size if item.is_file() else None,
            modified_at=datetime.fromtimestamp(stat.st_mtime)
        ))
    
    return DirectoryListing(path=path, entries=entries)


@router.post("/upload")
async def upload_file(
    path: str = Form(...),
    file: UploadFile = File(...)
):
    """Upload a file to a specific directory."""
    # Validate path is allowed
    target_dir = PROJECT_ROOT / path
    
    if not is_path_allowed(target_dir):
        raise HTTPException(status_code=403, detail="Access denied to this path")
    
    if not target_dir.exists():
        # Create directory if it doesn't exist (if inside allowed roots)
        # But we should be careful. 'inputs' usually exists.
        # Let's enforce that the PARENT must be allowed.
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create directory: {e}")

    file_path = target_dir / file.filename
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")
        
    return {"filename": file.filename, "path": str(file_path.relative_to(PROJECT_ROOT)), "size": file_path.stat().st_size}


@router.get("/download/{file_path:path}")
async def download_file(file_path: str):
    """Download a file from allowed directories."""
    full_path = PROJECT_ROOT / file_path
    
    if not is_path_allowed(full_path):
        raise HTTPException(status_code=403, detail="Access denied to this file")
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    
    return FileResponse(
        path=full_path,
        filename=full_path.name,
        media_type="application/octet-stream"
    )


@router.get("/pdb/{file_path:path}")
async def serve_pdb(file_path: str):
    """Serve a PDB file with appropriate content type for Mol* viewer."""
    full_path = PROJECT_ROOT / file_path
    
    if not is_path_allowed(full_path):
        raise HTTPException(status_code=403, detail="Access denied to this file")
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not full_path.suffix.lower() in ['.pdb', '.cif', '.mmcif']:
        raise HTTPException(status_code=400, detail="Not a structure file")
    
    return FileResponse(
        path=full_path,
        media_type="chemical/x-pdb" if full_path.suffix.lower() == '.pdb' else "chemical/x-mmcif"
    )
