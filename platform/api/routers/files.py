"""
File serving and directory browsing API router.
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, StreamingResponse
from pathlib import Path
from datetime import datetime
import mimetypes
import json
import os
import shutil
import stat
from typing import Iterator

from schemas import DirectoryListing, DirectoryEntry
from paths import (
    get_allowed_roots,
    resolve_allowed_path,
    to_allowed_relative,
)

router = APIRouter()
GOVERNED_NGS_MANIFEST_SCHEMAS = frozenset(
    {"sequence_qc.manifest.v1", "biomodstack.construct_verification.v2"}
)


def _read_json_nofollow(path: Path, *, max_bytes: int = 10 * 1024 * 1024) -> dict:
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        absolute = Path(os.path.abspath(path))
        for index, component in enumerate(absolute.parts[1:]):
            final = index == len(absolute.parts[1:]) - 1
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            if not final:
                flags |= os.O_DIRECTORY
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise ValueError("manifest is unsafe")
        raw = b""
        while len(raw) <= max_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - len(raw)))
            if not chunk:
                break
            raw += chunk
        if len(raw) > max_bytes:
            raise ValueError("manifest is too large")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root is invalid")
        return payload
    finally:
        os.close(descriptor)


def _parse_byte_range(range_header: str, file_size: int) -> tuple[int, int]:
    """
    Parse a single HTTP Range header value.
    Supports `bytes=start-end`, `bytes=start-`, and `bytes=-suffix`.
    """
    if not range_header or not range_header.startswith("bytes="):
        raise ValueError("Invalid range header")

    first_range = range_header.replace("bytes=", "", 1).split(",")[0].strip()
    if "-" not in first_range:
        raise ValueError("Invalid range syntax")

    start_raw, end_raw = first_range.split("-", 1)
    if start_raw == "":
        # Suffix range: bytes=-N
        suffix_len = int(end_raw)
        if suffix_len <= 0:
            raise ValueError("Invalid suffix length")
        start = max(file_size - suffix_len, 0)
        end = file_size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1

    if start < 0 or end < start or start >= file_size:
        raise ValueError("Range out of bounds")
    end = min(end, file_size - 1)
    return start, end


def _iter_file_range(path: Path, start: int, end: int, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    remaining = end - start + 1
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            read_len = min(chunk_size, remaining)
            chunk = handle.read(read_len)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def _guess_media_type(path: Path) -> str:
    # Keep BAM/BAM index binary.
    suffix = path.suffix.lower()
    if suffix in {".bam", ".bai", ".csi"}:
        return "application/octet-stream"
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _serve_file_response(full_path: Path, request: Request, as_attachment: bool):
    file_size = full_path.stat().st_size
    media_type = _guess_media_type(full_path)
    range_header = request.headers.get("range")
    cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    if range_header:
        try:
            start, end = _parse_byte_range(range_header, file_size)
        except ValueError:
            raise HTTPException(status_code=416, detail="Invalid or unsatisfiable byte range")

        content_length = end - start + 1
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(content_length),
            **cache_headers,
        }
        if as_attachment:
            headers["Content-Disposition"] = f'attachment; filename="{full_path.name}"'
        return StreamingResponse(
            _iter_file_range(full_path, start, end),
            status_code=206,
            headers=headers,
            media_type=media_type,
        )

    if as_attachment:
        response = FileResponse(
            path=full_path,
            filename=full_path.name,
            media_type=media_type,
        )
    else:
        response = FileResponse(
            path=full_path,
            media_type=media_type,
        )
    response.headers["Accept-Ranges"] = "bytes"
    for key, value in cache_headers.items():
        response.headers[key] = value
    return response


def is_path_allowed(path: Path) -> bool:
    """Check if path is within allowed roots."""
    try:
        resolved = path.resolve()
        for root in get_allowed_roots().values():
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False
    except (ValueError, RuntimeError):
        return False


def _is_governed_ngs_artifact(path: Path) -> bool:
    resolved = path.resolve()
    for parent in resolved.parents:
        manifest = parent / "qc_manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            continue
        try:
            payload = _read_json_nofollow(manifest)
            if payload.get("schema") in GOVERNED_NGS_MANIFEST_SCHEMAS:
                return True
        except (OSError, ValueError, json.JSONDecodeError):
            return True
    return False


def _is_governed_ngs_directory(path: Path) -> bool:
    resolved = path.resolve()
    return any(
        (candidate / "qc_manifest.json").exists()
        for candidate in (resolved, *resolved.parents)
    )


def _reject_governed_ngs_artifact(path: Path) -> None:
    if _is_governed_ngs_artifact(path):
        raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")


@router.get("/browse")
async def browse_directory(path: str = "") -> DirectoryListing:
    """Browse a directory within allowed paths."""
    if not path or path == "/":
        # Return list of allowed root directories
        entries = []
        for dir_name, dir_path in get_allowed_roots().items():
            if dir_path.exists():
                entries.append(DirectoryEntry(
                    name=dir_name,
                    path=dir_name,
                    is_directory=True,
                    size_bytes=None,
                    modified_at=datetime.fromtimestamp(dir_path.stat().st_mtime)
                ))
        return DirectoryListing(path="", entries=entries)
    
    try:
        full_path = resolve_allowed_path(path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to this path")
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    
    if not full_path.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    if _is_governed_ngs_directory(full_path):
        raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    
    entries = []
    for item in sorted(full_path.iterdir()):
        if item.is_dir() and _is_governed_ngs_directory(item):
            continue
        stat = item.stat()
        entries.append(DirectoryEntry(
            name=item.name,
            path=to_allowed_relative(item),
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
    try:
        target_dir = resolve_allowed_path(path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to this path")
    
    if not target_dir.exists():
        # Create directory if it doesn't exist (if inside allowed roots)
        # But we should be careful. 'inputs' usually exists.
        # Let's enforce that the PARENT must be allowed.
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to create directory: {e}")
    if _is_governed_ngs_directory(target_dir):
        raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")

    filename = str(file.filename or "")
    if not filename or filename in {".", ".."} or Path(filename).name != filename:
        raise HTTPException(status_code=400, detail="Upload filename must be one plain basename")
    if filename.casefold() == "qc_manifest.json":
        raise HTTPException(status_code=403, detail="Canonical manifests cannot be written through the generic upload route")
    file_path = target_dir / filename
    if _is_governed_ngs_directory(file_path.parent) or _is_governed_ngs_artifact(file_path):
        raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(file_path, flags, 0o644)
        with os.fdopen(descriptor, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")
        
    return {"filename": filename, "path": to_allowed_relative(file_path), "size": file_path.stat().st_size}


@router.get("/download/{file_path:path}")
async def download_file(file_path: str, request: Request):
    """Download a file from allowed directories."""
    try:
        full_path = resolve_allowed_path(file_path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to this file")
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    _reject_governed_ngs_artifact(full_path)
    
    return _serve_file_response(full_path, request, as_attachment=True)


@router.get("/stream/{file_path:path}")
async def stream_file(file_path: str, request: Request):
    """Stream a file from allowed directories with range support and inline disposition."""
    try:
        full_path = resolve_allowed_path(file_path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to this file")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    _reject_governed_ngs_artifact(full_path)

    return _serve_file_response(full_path, request, as_attachment=False)


@router.get("/pdb/{file_path:path}")
async def serve_pdb(file_path: str):
    """Serve a PDB file with appropriate content type for Mol* viewer."""
    try:
        full_path = resolve_allowed_path(file_path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to this file")
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not full_path.suffix.lower() in ['.pdb', '.cif', '.mmcif']:
        raise HTTPException(status_code=400, detail="Not a structure file")
    _reject_governed_ngs_artifact(full_path)
    
    return FileResponse(
        path=full_path,
        media_type="chemical/x-pdb" if full_path.suffix.lower() == '.pdb' else "chemical/x-mmcif"
    )


@router.post("/extract-chain")
async def extract_chain(
    input_path: str = Form(...),
    chain_id: str = Form(...),
    rename_to: str = Form(None),
    model_number: int | None = Form(None),
):
    """
    Extract a single chain from a multi-chain PDB file.
    
    This is used when a user selects a specific chain from a complex PDB
    (e.g., selecting Chain I from 7TLY which has Ab fragments on chains A,B).
    
    Args:
        input_path: Path to input PDB (relative to project root)
        chain_id: Chain ID to extract (e.g., 'I')
        rename_to: Optional new chain ID (e.g., 'T' for target)
        
    Returns:
        Path to the extracted single-chain PDB file
    """
    import sys
    from paths import get_code_root
    code_root = get_code_root()
    sys.path.insert(0, str(code_root / "scripts"))
    from extract_chain import extract_chains
    
    try:
        full_input = resolve_allowed_path(input_path)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to input file")
    
    if not full_input.exists():
        raise HTTPException(status_code=404, detail="Input file not found")
    _reject_governed_ngs_artifact(full_input)
    
    # Create output filename with chain suffix
    output_name = f"{full_input.stem}_chain{chain_id}{full_input.suffix}"
    output_path = full_input.parent / output_name
    if _is_governed_ngs_directory(output_path.parent) or _is_governed_ngs_artifact(output_path):
        raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    
    try:
        result = extract_chains(
            str(full_input),
            str(output_path),
            [chain_id],
            renumber=False,
            new_chain_id=rename_to,
            model_number=model_number,
        )
        
        return {
            "success": True,
            "input_path": input_path,
            "output_path": to_allowed_relative(output_path),
            "chain_extracted": chain_id,
            "atom_count": result["atom_count"],
            "renamed_to": rename_to
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chain extraction failed: {str(e)}")
