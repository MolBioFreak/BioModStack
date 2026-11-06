"""Tailnet-only creation, listing and revocation of temporary MolBio guest shares.

This router lives in the main (tailnet-only) BMS API. It freezes a read-only
bundle and a grant-index entry into the configured publish directory; the
separate public share server (``share_server.app``) only reads them. The link
key and passcode are returned exactly once and are never stored in clear.

Callers are either the tailnet operator (no credential, as for every other
tailnet BMS route) or an AI agent presenting ``Authorization: Bearer`` with a
token from the configured agent-token file. That token is recognised only by
this router, so it can create/list/revoke shares and nothing else.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Annotated, Any, Literal, Union
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from experiment_database import get_experiment_session
from molbio_database import get_molbio_session
from molbio_ngs_database import get_molbio_ngs_session
from services.molbio_share_export import ShareScopeError, build_share_bundle, encode_bundle
from share_server import grants as G

router = APIRouter(prefix="/api/molbio/shares", tags=["molbio-shares"])

AGENT_TOKEN_FILE_ENV = "BMS_SHARE_AGENT_TOKEN_FILE"
MAX_AGENT_TOKEN_FILE_BYTES = 64 * 1024
RETAIN_ENDED_GRANT_SECONDS = 7 * 24 * 60 * 60

Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectScope(_Strict):
    kind: Literal["project"]
    project_id: Identifier


class ProjectDomainScope(_Strict):
    kind: Literal["project_domain"]
    project_id: Identifier
    global_experiment_id: Identifier
    domain_experiment_id: Identifier
    state_revision_id: Identifier | None = None


class ItemsScope(_Strict):
    kind: Literal["items"]
    sequence_ids: list[Identifier] = Field(default_factory=list, max_length=200)
    operation_ids: list[Identifier] = Field(default_factory=list, max_length=200)


ShareScope = Annotated[Union[ProjectScope, ProjectDomainScope, ItemsScope], Field(discriminator="kind")]


class CreateShareRequest(_Strict):
    scope: ShareScope
    expires_in_seconds: int = Field(ge=G.MIN_EXPIRY_SECONDS, le=G.MAX_EXPIRY_SECONDS)
    label: str = Field(default="", max_length=G.MAX_LABEL_LENGTH)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _clean_label(value: str) -> str:
    return "".join(ch for ch in value if ch.isprintable()).strip()[: G.MAX_LABEL_LENGTH]


# ---------------------------------------------------------------------------
# Caller identity
# ---------------------------------------------------------------------------

def _load_agent_tokens() -> dict[str, str]:
    """Return ``{token_sha256: label}`` from the 0600 agent-token file.

    File format: one ``label:token`` per line; ``#`` comments allowed. The file
    must be a regular file owned by this process user and not group/world
    accessible. Any defect refuses agent access (operator access is unaffected).
    """
    raw_path = os.environ.get(AGENT_TOKEN_FILE_ENV, "").strip()
    if not raw_path:
        return {}
    path = Path(raw_path).expanduser()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError:
        return {}
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid()
            or info.st_size > MAX_AGENT_TOKEN_FILE_BYTES
        ):
            return {}
        data = os.read(fd, MAX_AGENT_TOKEN_FILE_BYTES + 1).decode("utf-8", "replace")
    finally:
        os.close(fd)
    tokens: dict[str, str] = {}
    for line in data.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        label, token = (part.strip() for part in line.split(":", 1))
        if len(token) < 32 or not label:
            continue
        tokens[hashlib.sha256(token.encode("utf-8")).hexdigest()] = _clean_label(label)[:64] or "agent"
    return tokens


def share_creator(authorization: Annotated[str | None, Header()] = None) -> str:
    """``operator`` for an uncredentialed tailnet caller, ``agent:<label>`` for a token."""
    if authorization is None:
        return "operator"
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _error(401, "share_agent_token_invalid", "agent share token is invalid")
    presented = hashlib.sha256(token.strip().encode("utf-8")).hexdigest().encode("ascii")
    label = None
    for digest, candidate in _load_agent_tokens().items():
        if hmac.compare_digest(presented, digest.encode("ascii")):
            label = candidate
    if label is None:
        raise _error(401, "share_agent_token_invalid", "agent share token is invalid")
    return f"agent:{label}"


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.scheme, parsed.hostname.casefold(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def browser_mutation_origin(request: Request) -> None:
    """Reject browser CSRF, without adding credentials to tailnet/agent use.

    Respect the existing CORS origin owner and configured frontend. The private
    HTTPS node origin is distinct from the public guest origin on port 8443.
    Non-browser callers without Origin/Fetch Metadata retain their API path.
    """
    supplied = request.headers.get("origin")
    fetch_site = request.headers.get("sec-fetch-site", "").casefold()
    if supplied is None:
        if fetch_site in {"cross-site", "same-site"}:
            raise _error(403, "share_origin_denied", "cross-site share changes are not allowed")
        return
    trusted = {_origin(str(request.base_url)), _origin(os.environ.get("BMS_FRONTEND_HEALTH_URL", ""))}
    for middleware in request.app.user_middleware:
        if middleware.cls is CORSMiddleware:
            trusted.update(_origin(value) for value in middleware.kwargs.get("allow_origins", []))
    try:
        trusted.add(_origin(f"https://{G.public_host()}"))
    except G.ShareConfigurationError:
        pass
    trusted.discard(None)
    if _origin(supplied) not in trusted:
        raise _error(403, "share_origin_denied", "untrusted browser origin cannot change shares")


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _publish_root() -> Path:
    try:
        root = G.publish_dir()
        G.prepare_publish_dir(root)
        G.public_host()
    except G.ShareConfigurationError as exc:
        raise _error(503, "share_publishing_unconfigured", str(exc)) from exc
    return root


def _remove_bundle(root: Path, grant_id: str) -> None:
    try:
        os.unlink(G.bundle_path(root, grant_id))
    except (FileNotFoundError, ValueError):
        pass


def _prune(root: Path, grants: list[dict[str, Any]], now: int) -> list[dict[str, Any]]:
    """Drop bundles of ended grants and forget index entries a week after they end."""
    kept = []
    for grant in grants:
        state = G.grant_state(grant, now=now)
        if not state.active:
            _remove_bundle(root, str(grant.get("grant_id")))
            ended = grant.get("revoked_at") or grant.get("expires_at") or 0
            try:
                if now - int(ended) > RETAIN_ENDED_GRANT_SECONDS:
                    continue
            except (TypeError, ValueError):
                continue
        kept.append(grant)
    return kept


def _public_view(grant: dict[str, Any], access: dict[str, list[dict[str, Any]]], now: int) -> dict[str, Any]:
    state = G.grant_state(grant, now=now)
    grant_id = str(grant.get("grant_id"))
    return {
        "grant_id": grant_id,
        "label": grant.get("label") or "",
        "creator": grant.get("creator") or "operator",
        "scope": grant.get("scope") or {},
        "created_at": G.iso(grant.get("created_at")),
        "expires_at": G.iso(grant.get("expires_at")),
        "revoked_at": G.iso(grant.get("revoked_at")),
        "revoked_by": grant.get("revoked_by"),
        "state": state.reason,
        "active": state.active,
        "bundle_bytes": grant.get("bundle_bytes"),
        "access_log": access.get(grant_id, []),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/config")
async def share_config() -> dict[str, Any]:
    try:
        root = G.publish_dir()
        G.public_host()
        configured = root.is_dir()
    except G.ShareConfigurationError:
        configured = False
    return {
        "configured": configured,
        "max_expiry_seconds": G.MAX_EXPIRY_SECONDS,
        "min_expiry_seconds": G.MIN_EXPIRY_SECONDS,
        "public_port": G.PUBLIC_FUNNEL_PORT,
    }


@router.post("", dependencies=[Depends(browser_mutation_origin)])
async def create_share(
    payload: CreateShareRequest,
    creator: str = Depends(share_creator),
    molbio: AsyncSession = Depends(get_molbio_session),
    experiment: AsyncSession = Depends(get_experiment_session),
    native: AsyncSession = Depends(get_molbio_ngs_session),
) -> dict[str, Any]:
    root = _publish_root()
    lifetime = G.clamp_check_expiry(payload.expires_in_seconds)
    try:
        bundle, summary = await build_share_bundle(
            payload.scope.model_dump(), molbio=molbio, experiment=experiment, native=native,
        )
        data = encode_bundle(bundle)
    except ShareScopeError as exc:
        raise _error(exc.status, exc.code, exc.message) from exc

    grant_id = G.new_grant_id()
    key = G.new_link_key()
    passcode = G.new_passcode()
    now = G.utc_now()
    grant = {
        "grant_id": grant_id,
        "key_sha256": G.key_sha256(key),
        "passcode": G.hash_passcode(passcode),
        "label": _clean_label(payload.label),
        "creator": creator,
        "created_at": now,
        "expires_at": now + lifetime,
        "revoked_at": None,
        "revoked_by": None,
        "scope": summary,
        "bundle_sha256": hashlib.sha256(data).hexdigest(),
        "bundle_bytes": len(data),
    }
    with G.index_lock(root):
        G.atomic_write(G.bundle_path(root, grant_id), data)
        try:
            document = G.load_index(root)
            grants = _prune(root, list(document["grants"]), now)
            grants.append(grant)
            G.save_index(root, {"grants": grants})
        except BaseException:
            _remove_bundle(root, grant_id)
            raise
    return {
        "grant": _public_view(grant, {}, now),
        "link": G.share_link(key),
        "passcode": G.format_passcode(passcode),
        "notice": "The link and passcode are shown only once and are not stored.",
    }


@router.get("")
async def list_shares(creator: str = Depends(share_creator)) -> dict[str, Any]:
    del creator  # any authorised caller may list
    root = _publish_root()
    now = G.utc_now()
    try:
        document = G.load_index(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise _error(500, "share_index_unreadable", "share grant index is unreadable") from exc
    access = G.read_access_log(G.access_log_path())
    grants = sorted(document["grants"], key=lambda g: int(g.get("created_at") or 0), reverse=True)
    views = [_public_view(grant, access, now) for grant in grants if isinstance(grant, dict)]
    return {
        "schema": "bms.molbio.share-list.v1",
        "active_count": sum(1 for view in views if view["active"]),
        "items": views,
        "security": G.read_security_summary(G.access_log_path(), now=now),
    }


@router.post("/{grant_id}/revoke", dependencies=[Depends(browser_mutation_origin)])
async def revoke_share(grant_id: str, creator: str = Depends(share_creator)) -> dict[str, Any]:
    if not G.well_formed_grant_id(grant_id):
        raise _error(404, "share_not_found", "share was not found")
    root = _publish_root()
    now = G.utc_now()
    with G.index_lock(root):
        document = G.load_index(root)
        grants = list(document["grants"])
        target = next((g for g in grants if isinstance(g, dict) and g.get("grant_id") == grant_id), None)
        if target is None:
            raise _error(404, "share_not_found", "share was not found")
        if target.get("revoked_at") is None:
            target["revoked_at"] = now
            target["revoked_by"] = creator
        G.save_index(root, {"grants": grants})
        _remove_bundle(root, grant_id)
    return {"grant": _public_view(target, {}, now)}
