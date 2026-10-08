"""
File serving and directory browsing API router.
"""

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
from schemas import DirectoryListing, DirectoryEntry
from paths import (
    get_allowed_roots,
    resolve_allowed_path,
    to_allowed_relative,
)

router = APIRouter()
IMMUTABLE_STRUCTURE_UPLOAD_PATH = "inputs/protein_local_redesign"
IMMUTABLE_STRUCTURE_UPLOAD_MAX_BYTES = 64 * 1024 * 1024
IMMUTABLE_STRUCTURE_FILENAME_RE = re.compile(
    r"^[A-Za-z0-9_.-]{1,160}-([0-9a-f]{64})\.pdb$"
)
GOVERNED_NGS_MANIFEST_SCHEMAS = frozenset(
    {"sequence_qc.manifest.v1", "biomodstack.construct_verification.v2"}
)
GOVERNED_NGS_MODEL_IDS = frozenset(
    {"nanopore", "ont_fastq_qc", "ont_plasmid_qc", "ont_construct_screening", "wf_clone_validation"}
)


def _lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def _contained_lexically(path: Path, root: Path) -> bool:
    try:
        _lexical_absolute(path).relative_to(_lexical_absolute(root))
        return True
    except ValueError:
        return False


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
    """Return stable lexical authority from persisted canonical NGS job rows."""
    configured_root = _lexical_absolute(get_allowed_roots()["bms_results"])
    result = await session.execute(
        select(Job.output_dir, Job.child_output_dir).where(Job.model_id.in_(GOVERNED_NGS_MODEL_IDS))
    )
    roots: set[Path] = set()
    for output_dir, child_output_dir in result.all():
        for raw in (output_dir, child_output_dir):
            if not isinstance(raw, str) or not raw.strip():
                continue
            declared = Path(raw.strip()).expanduser()
            candidate = _lexical_absolute(declared if declared.is_absolute() else configured_root / declared)
            if _contained_lexically(candidate, configured_root):
                roots.add(candidate)
    return tuple(sorted(roots, key=os.fspath))


def _under_persisted_ngs_root(path: Path, governed_roots: tuple[Path, ...]) -> bool:
    return any(_contained_lexically(path, root) for root in governed_roots)


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


def _sha256_regular_file_nofollow(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("immutable structure path is not a regular file")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest(), metadata.st_size
    finally:
        os.close(descriptor)


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
    immutable_dir = _allowed_lexical_path(IMMUTABLE_STRUCTURE_UPLOAD_PATH)
    if (
        _lexical_absolute(target_dir) == immutable_dir
        and IMMUTABLE_STRUCTURE_FILENAME_RE.fullmatch(filename)
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
    """Atomically publish one SHA-256-addressed RFD3 structure input."""
    if path.strip().strip("/") != IMMUTABLE_STRUCTURE_UPLOAD_PATH:
        raise HTTPException(status_code=403, detail="Immutable structure uploads use the RFD3 input directory")
    expected_sha256 = sha256.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise HTTPException(status_code=400, detail="sha256 must be 64 lowercase hexadecimal characters")
    filename = str(file.filename or "")
    match = IMMUTABLE_STRUCTURE_FILENAME_RE.fullmatch(filename)
    if match is None or match.group(1) != expected_sha256:
        raise HTTPException(status_code=400, detail="Upload filename must end with its SHA-256")

    try:
        lexical_dir = _allowed_lexical_path(IMMUTABLE_STRUCTURE_UPLOAD_PATH)
        if _under_persisted_ngs_root(lexical_dir, governed_roots):
            raise HTTPException(status_code=403, detail="Use the job-scoped governed artifact route")
        target_dir = resolve_allowed_path(IMMUTABLE_STRUCTURE_UPLOAD_PATH)
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
        prefix=".rfd3-structure-", suffix=".tmp", dir=target_dir
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
    
    return _serve_file_response(full_path, request, as_attachment=True)


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
