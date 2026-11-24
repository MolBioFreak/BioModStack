"""
File serving and directory browsing API router.
"""

from services.binder_source_materialization import (
    IMMUTABLE_STRUCTURE_UPLOAD_MAX_BYTES,
    _contained_lexically,
    _is_governed_ngs_artifact,
    _lexical_absolute,
    _reject_governed_ngs_artifact,
    _sha256_regular_file_nofollow,
    _under_persisted_ngs_root,
    governed_ngs_result_roots,
    materialize_structure_source,
)

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path
from datetime import datetime
import mimetypes
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from typing import Iterator

from database import Job, get_session
from experiment_database import get_experiment_session
from services.binder_source_materialization import StructureSourceRequest
from schemas import DirectoryListing, DirectoryEntry
from paths import (
    get_allowed_roots,
    resolve_allowed_path,
    to_allowed_relative,
)

router = APIRouter()
IMMUTABLE_STRUCTURE_UPLOAD_PATH = "inputs/protein_local_redesign"
IMMUTABLE_STRUCTURE_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]{1,160}-([0-9a-f]{64})\.pdb$"
)
IMMUTABLE_ANNOTATED_STRUCTURE_UPLOAD_PATH = "inputs/annotated_structures"
IMMUTABLE_ANNOTATED_STRUCTURE_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]{1,160}-([0-9a-f]{64})\.(?:cif|mmcif)$"
)
IMMUTABLE_STRUCTURE_UPLOADS = {
    IMMUTABLE_STRUCTURE_UPLOAD_PATH: IMMUTABLE_STRUCTURE_FILENAME_RE,
    IMMUTABLE_ANNOTATED_STRUCTURE_UPLOAD_PATH: IMMUTABLE_ANNOTATED_STRUCTURE_FILENAME_RE,
}


def _allowed_lexical_path(value: str) -> Path:
    normalized = value.strip().lstrip("/")
    if not normalized:
        raise ValueError("Empty path")
    parts = Path(normalized).parts
    root = get_allowed_roots().get(parts[0])
    if root is None:
        raise ValueError("Root not allowed")
    candidate = _lexical_absolute(root / Path(*parts[1:]))
    if not _contained_lexically(candidate, root):
        raise ValueError("Path escapes allowed root")
    return candidate


async def get_governed_ngs_result_roots(
    session: AsyncSession = Depends(get_session),
) -> tuple[Path, ...]:
    return await governed_ngs_result_roots(session)


def _immutable_structure_response(path: Path, expected_sha256: str, *, existing: bool) -> dict:
    actual_sha256, size = _sha256_regular_file_nofollow(path)
    if actual_sha256 != expected_sha256:
        raise HTTPException(status_code=409, detail="immutable structure path contains different bytes")
    return {
        "filename": path.name,
        "path": to_allowed_relative(path),
        "size": size,
        "sha256": actual_sha256,
        "existing": existing,
    }


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


def _is_governed_ngs_directory(path: Path) -> bool:
    resolved = path.resolve()
    return any(
        (candidate / "qc_manifest.json").exists()
        for candidate in (resolved, *resolved.parents)
    )


@router.get("/browse")
async def browse_directory(
    path: str = "",
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
) -> DirectoryListing:
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
        lexical_path = _allowed_lexical_path(path)
        if _under_persisted_ngs_root(lexical_path, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
        full_path = resolve_allowed_path(path)
        if _under_persisted_ngs_root(full_path, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
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
        if item.is_dir() and (
            _under_persisted_ngs_root(item, governed_roots)
            or _is_governed_ngs_directory(item)
        ):
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


@router.get("/structure-sources")
async def structure_sources(
    project_id: str | None = None, dataset_id: str | None = None,
    revision_id: str | None = None, receipt_id: str | None = None,
    design_id: str | None = None, collection: str = "resources",
    offset: int = 0, limit: int = 50,
    experiment_session: AsyncSession = Depends(get_experiment_session),
    session: AsyncSession = Depends(get_session),
):
    from services.global_experiments.project_datasets import browse_structure_sources
    from experiment_services import ExperimentServiceError, NotFound
    from services.global_experiments.adapters import AdapterError
    if offset < 0 or not 1 <= limit <= 100 or collection not in {"resources", "datasets"}:
        raise HTTPException(422, "Invalid source page")
    try:
        return await browse_structure_sources(experiment_session, session, project_id=project_id,
            dataset_id=dataset_id, revision_id=revision_id, receipt_id=receipt_id,
            design_id=design_id, collection=collection, offset=offset, limit=limit)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ExperimentServiceError, AdapterError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/materialize-structure")
async def materialize_structure(
    payload: StructureSourceRequest,
    session: AsyncSession = Depends(get_session),
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
):
    return await materialize_structure_source(payload, session, governed_roots)


@router.post("/upload")
async def upload_file(
    path: str = Form(...),
    file: UploadFile = File(...),
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
):
    """Upload a file to a specific directory."""
    # Validate path is allowed
    try:
        lexical_dir = _allowed_lexical_path(path)
        if _under_persisted_ngs_root(lexical_dir, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
        target_dir = resolve_allowed_path(path)
        if _under_persisted_ngs_root(target_dir, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
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
    if any(
        _lexical_absolute(target_dir) == _allowed_lexical_path(directory)
        and pattern.fullmatch(filename)
        for directory, pattern in IMMUTABLE_STRUCTURE_UPLOADS.items()
    ):
        raise HTTPException(status_code=403, detail="Use the immutable structure upload route")
    file_path = target_dir / filename
    if (
        _under_persisted_ngs_root(_lexical_absolute(lexical_dir / filename), governed_roots)
        or _is_governed_ngs_directory(file_path.parent)
        or _is_governed_ngs_artifact(file_path)
    ):
        raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(file_path, flags, 0o644)
        with os.fdopen(descriptor, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Upload destination already exists")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {e}")
        
    return {"filename": filename, "path": to_allowed_relative(file_path), "size": file_path.stat().st_size}


@router.post("/upload-immutable-structure")
async def upload_immutable_structure(
    path: str = Form(...),
    sha256: str = Form(...),
    file: UploadFile = File(...),
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
):
    """Publish exact source bytes; annotated CIF is never renamed to PDB."""
    upload_path = path.strip().strip("/")
    filename_pattern = IMMUTABLE_STRUCTURE_UPLOADS.get(upload_path)
    if filename_pattern is None:
        raise HTTPException(status_code=403, detail="Choose an immutable structure input directory")
    expected_sha256 = sha256.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise HTTPException(status_code=400, detail="sha256 must be 64 lowercase hexadecimal characters")
    filename = str(file.filename or "")
    match = filename_pattern.fullmatch(filename)
    if match is None or match.group(1) != expected_sha256:
        raise HTTPException(status_code=400, detail="Upload filename must end with its SHA-256")

    try:
        lexical_dir = _allowed_lexical_path(upload_path)
        if _under_persisted_ngs_root(lexical_dir, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
        target_dir = resolve_allowed_path(upload_path)
        if _under_persisted_ngs_root(target_dir, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to this path")

    target_dir.mkdir(parents=True, exist_ok=True)
    if _is_governed_ngs_directory(target_dir):
        raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    file_path = target_dir / filename
    if file_path.exists():
        return _immutable_structure_response(file_path, expected_sha256, existing=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".immutable-structure-", suffix=".tmp", dir=target_dir
    )
    temporary_path = Path(temporary_name)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(descriptor, "wb") as buffer:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > IMMUTABLE_STRUCTURE_UPLOAD_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="Structure upload exceeds 64 MiB")
                digest.update(chunk)
                buffer.write(chunk)
            buffer.flush()
            os.fsync(buffer.fileno())
        if digest.hexdigest() != expected_sha256:
            raise HTTPException(status_code=400, detail="Upload content SHA-256 mismatch")
        os.chmod(temporary_path, 0o444)
        try:
            os.link(temporary_path, file_path, follow_symlinks=False)
            directory_descriptor = os.open(target_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            existing = False
        except FileExistsError:
            existing = True
        return _immutable_structure_response(file_path, expected_sha256, existing=existing)
    finally:
        temporary_path.unlink(missing_ok=True)


@router.get("/download/{file_path:path}")
async def download_file(
    file_path: str,
    request: Request,
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
):
    """Download a file from allowed directories."""
    full_path = _download_source(file_path, governed_roots)
    return _serve_file_response(full_path, request, as_attachment=True)


def _download_source(file_path: str, governed_roots: tuple[Path, ...]) -> Path:
    """Shared managed-file read authority, including governed artifact exclusions."""
    try:
        lexical_path = _allowed_lexical_path(file_path)
        if _under_persisted_ngs_root(lexical_path, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
        full_path = resolve_allowed_path(file_path)
        if _under_persisted_ngs_root(full_path, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to this file")
    
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    _reject_governed_ngs_artifact(full_path)
    
    return full_path


@router.get("/disco-jobs/{file_path:path}")
async def read_disco_jobs(
    file_path: str,
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
):
    """Explicit editor import; retain literal FILE_ paths and their checked owner.

    This is not admission or molecular validation. The original document is never
    rewritten; ordinary native closure discovery retains its bytes and ligands.
    """
    from starlette.concurrency import run_in_threadpool
    source = _download_source(file_path, governed_roots)
    try:
        text = await run_in_threadpool(source.read_text, encoding="utf-8")
        return {"text": text, "source_path": str(source)}
    except (OSError, UnicodeError) as exc:
        raise HTTPException(422, "Could not read native DISCO document") from exc


@router.get("/stream/{file_path:path}")
async def stream_file(
    file_path: str,
    request: Request,
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
):
    """Stream a file from allowed directories with range support and inline disposition."""
    try:
        lexical_path = _allowed_lexical_path(file_path)
        if _under_persisted_ngs_root(lexical_path, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
        full_path = resolve_allowed_path(file_path)
        if _under_persisted_ngs_root(full_path, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to this file")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    _reject_governed_ngs_artifact(full_path)

    return _serve_file_response(full_path, request, as_attachment=False)


@router.get("/pdb/{file_path:path}")
async def serve_pdb(
    file_path: str,
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
):
    """Serve a PDB file with appropriate content type for Mol* viewer."""
    try:
        lexical_path = _allowed_lexical_path(file_path)
        if _under_persisted_ngs_root(lexical_path, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
        full_path = resolve_allowed_path(file_path)
        if _under_persisted_ngs_root(full_path, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
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
    governed_roots: tuple[Path, ...] = Depends(get_governed_ngs_result_roots),
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
        lexical_input = _allowed_lexical_path(input_path)
        if _under_persisted_ngs_root(lexical_input, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
        full_input = resolve_allowed_path(input_path)
        if _under_persisted_ngs_root(full_input, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied to input file")
    
    if not full_input.exists():
        raise HTTPException(status_code=404, detail="Input file not found")
    _reject_governed_ngs_artifact(full_input)
    
    # Create output filename with chain suffix
    output_name = f"{full_input.stem}_chain{chain_id}{full_input.suffix}"
    output_path = full_input.parent / output_name
    if (
        _under_persisted_ngs_root(_lexical_absolute(lexical_input.parent / output_name), governed_roots)
        or _is_governed_ngs_directory(output_path.parent)
        or _is_governed_ngs_artifact(output_path)
    ):
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
