from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from paths import get_mobile_ui_updates_dir

router = APIRouter(prefix="/mobile-ui", tags=["mobile-ui"])

_ALLOWED_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _validate_segment(value: str, *, label: str) -> str:
    normalized = value.strip()
    if not normalized or not _ALLOWED_SEGMENT.fullmatch(normalized):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return normalized


def _read_json(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Malformed mobile UI metadata: {exc}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _resolve_descendant(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid asset path") from exc
    return candidate


@router.get("/channels/{channel}/manifest")
async def get_channel_manifest(channel: str):
    channel_name = _validate_segment(channel, label="channel")
    manifest_path = get_mobile_ui_updates_dir() / "channels" / channel_name / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="Mobile UI manifest not found")
    return JSONResponse(_read_json(manifest_path))


@router.get("/bundles/{channel}/{version}.zip")
async def download_bundle(channel: str, version: str):
    channel_name = _validate_segment(channel, label="channel")
    bundle_version = _validate_segment(version, label="version")
    bundle_path = get_mobile_ui_updates_dir() / "bundles" / channel_name / f"{bundle_version}.zip"
    if not bundle_path.exists():
        raise HTTPException(status_code=404, detail="Mobile UI bundle not found")
    return FileResponse(bundle_path, media_type="application/zip", filename=bundle_path.name)


@router.get("/files/{channel}/{version}/{asset_path:path}")
async def download_bundle_file(channel: str, version: str, asset_path: str):
    channel_name = _validate_segment(channel, label="channel")
    bundle_version = _validate_segment(version, label="version")
    bundle_root = get_mobile_ui_updates_dir() / "files" / channel_name / bundle_version
    file_path = _resolve_descendant(bundle_root, asset_path)
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Mobile UI asset not found")
    return FileResponse(file_path)
