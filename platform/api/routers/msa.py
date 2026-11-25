"""MSA provider setup, credentials, controller toggle, and cache inspection API."""
from __future__ import annotations

from datetime import datetime
import gzip
import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from paths import get_msa_cache_dir


router = APIRouter(prefix="/api/msa", tags=["msa"])


@router.get("/providers")
async def get_msa_provider_readiness():
    """Non-submitting provider/cache configuration for browser and agent setup."""
    from services.msa_provider_setup import provider_hub_items, provider_readiness
    readiness = provider_readiness()
    return {**readiness, "items": provider_hub_items(readiness)}


LOCAL_ADMIN_HOSTS = {None, "127.0.0.1", "::1", "localhost", "testclient"}


def _require_local(request: Request) -> None:
    if request.client and request.client.host not in LOCAL_ADMIN_HOSTS:
        raise HTTPException(status_code=403, detail="MSA provider credentials are limited to local requests")


def _hub_item(provider_id: str) -> dict:
    from services.msa_provider_setup import provider_hub_items
    return next(item for item in provider_hub_items() if item["id"] == provider_id)


def _known_provider(provider_id: str) -> str:
    from services.msa_provider_credentials import PROVIDER_IDS
    if provider_id not in PROVIDER_IDS:
        raise HTTPException(status_code=404, detail="Unknown MSA provider")
    return provider_id


@router.put("/providers/{provider_id}/credential")
async def put_msa_provider_credential(provider_id: str, request: Request):
    """Save a provider API key. The key is written to the private store and never echoed."""
    from services.msa_provider_credentials import CredentialError, save_credential
    _require_local(request)
    _known_provider(provider_id)
    # Parsed by hand so a rejected body is never echoed back in a validation error.
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object with api_key")
    api_key = body.get("api_key")
    endpoint = body.get("endpoint")
    if not isinstance(api_key, str) or not api_key.strip():
        raise HTTPException(status_code=422, detail="API key must not be empty")
    if endpoint is not None and not isinstance(endpoint, str):
        raise HTTPException(status_code=422, detail="Endpoint must be text")
    try:
        save_credential(provider_id, api_key, endpoint)
    except CredentialError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except OSError:
        raise HTTPException(status_code=500, detail="Could not save the provider credential") from None
    return {"success": True, "provider": _hub_item(provider_id)}


@router.delete("/providers/{provider_id}/credential")
async def delete_msa_provider_credential(provider_id: str, request: Request):
    """Remove the key saved from the hub. An environment-provided key is not touched."""
    from services.msa_provider_credentials import clear_credential
    _require_local(request)
    _known_provider(provider_id)
    try:
        removed = clear_credential(provider_id)
    except OSError:
        raise HTTPException(status_code=500, detail="Could not clear the provider credential") from None
    return {"success": True, "removed": removed, "provider": _hub_item(provider_id)}


def _controller_provider(provider_id: str) -> str:
    from services.msa_provider_credentials import get_descriptor
    if not get_descriptor(_known_provider(provider_id)).needs_controller:
        raise HTTPException(status_code=422, detail="This provider is not enabled per computer")
    return provider_id


@router.put("/providers/{provider_id}/controller")
async def enable_msa_provider_controller(provider_id: str, request: Request):
    """Declare this computer the one that submits public searches for the provider. Nothing is submitted."""
    from biomodstack_msa_controller import enable_on_this_computer
    from services.msa_provider_setup import controller_config_path
    _require_local(request)
    _controller_provider(provider_id)
    try:
        enable_on_this_computer(controller_config_path())
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(status_code=500, detail="Could not enable searches on this computer") from None
    return {"success": True, "provider": _hub_item(provider_id)}


@router.delete("/providers/{provider_id}/controller")
async def disable_msa_provider_controller(provider_id: str, request: Request):
    """Stop new public searches from this computer. Cached alignments keep working."""
    from biomodstack_msa_controller import disable_on_this_computer
    from services.msa_provider_setup import controller_config_path
    _require_local(request)
    _controller_provider(provider_id)
    try:
        removed = disable_on_this_computer(controller_config_path())
    except (OSError, ValueError):
        raise HTTPException(status_code=500, detail="Could not turn off searches on this computer") from None
    return {"success": True, "removed": removed, "provider": _hub_item(provider_id)}


class MSACacheInspection(BaseModel):
    model_id: str
    params: dict


@router.post('/provider-cache/inspect')
async def inspect_provider_cache(request: MSACacheInspection):
    """Request-scoped native replay; no provider submission or cache writes."""
    from services.msa_provider_setup import inspect_msa_cache
    try:
        return inspect_msa_cache(request.model_id, request.params)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _normalize_sequence_for_cache(sequence: str) -> str:
    return "".join((sequence or "").split())


def _cached_msa_depth(cache_path: Path) -> int | None:
    try:
        depth = 0
        with gzip.open(cache_path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith(">"):
                    depth += 1
        return depth
    except Exception:
        return None


@router.get("/cache-info")
async def get_msa_cache_info(sequence: str, max_entries: int = 25):
    """
    Inspect local MSA cache entries for a sequence.

    Returns canonical single-cache status and any legacy profile-scoped entries.
    """
    normalized = _normalize_sequence_for_cache(sequence)
    if not normalized:
        raise HTTPException(status_code=400, detail="sequence is required")

    if max_entries < 1:
        max_entries = 1
    if max_entries > 100:
        max_entries = 100

    seq_hash = hashlib.sha256(normalized.encode()).hexdigest()
    cache_root = get_msa_cache_dir()
    subdir = cache_root / seq_hash[:2]
    canonical = subdir / f"{seq_hash}.a3m.gz"

    candidates: list[Path] = []
    if canonical.exists():
        candidates.append(canonical)
    if subdir.exists():
        candidates.extend(
            sorted(
                [p for p in subdir.glob(f"{seq_hash}_*.a3m.gz") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        )

    entries = []
    for cache_path in candidates[:max_entries]:
        name = cache_path.name
        if name == f"{seq_hash}.a3m.gz":
            profile = "single"
        else:
            profile = name.replace(f"{seq_hash}_", "").replace(".a3m.gz", "")
        stat = cache_path.stat()
        entries.append(
            {
                "name": name,
                "profile": profile,
                "path": str(cache_path),
                "size_bytes": int(stat.st_size),
                "modified_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
                "depth": _cached_msa_depth(cache_path),
                "canonical": cache_path == canonical,
            }
        )

    best_depth = None
    for entry in entries:
        depth_val = entry.get("depth")
        if isinstance(depth_val, int):
            best_depth = depth_val if best_depth is None else max(best_depth, depth_val)

    return {
        "sequence_hash": seq_hash,
        "cache_dir": str(cache_root),
        "canonical_exists": canonical.exists(),
        "canonical_path": str(canonical) if canonical.exists() else None,
        "cache_entries": len(candidates),
        "best_depth": best_depth,
        "entries": entries,
    }
